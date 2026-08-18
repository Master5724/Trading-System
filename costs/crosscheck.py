"""Il funding di `costs/` contro quello di `catalog/`, sugli stessi dati.

**Perche' esiste.** Due moduli leggono lo stesso canale `activeAssetCtx` e
calcolano la stessa grandezza per due scopi diversi: `catalog/` per descrivere
i dati raccolti, `costs/` perche' e' il modello che il backtester usera'. Se
divergono, uno dei due e' sbagliato e il numero che si legge dipende da quale
comando si e' lanciato. L'invariante 4 di CLAUDE.md vieta due modelli di costo;
questo modulo e' cio' che rende la proibizione verificabile invece che
dichiarata.

**Cosa confronta, e in che ordine.** Un confronto fra due totali non serve a
niente: se differiscono non dice dove. Quindi si procede per strati, dal piu'
fine al piu' grosso, e ogni strato ha un esito separato.

1. *Ora per ora.* Le due serie orarie, riportate allo stesso sistema di indici
   (l'ora di REGOLAMENTO: `catalog` indicizza per ora di campionamento, e la
   traslazione di un'ora avviene qui in un punto solo). Le ore in comune devono
   avere rate identici — non "vicini": identici, e' lo stesso `arg_max` sugli
   stessi byte. Una differenza qui e' un errore di lettura di uno dei due.
2. *La finestra.* I due moduli scelgono la finestra di 10 giorni per conto
   loro. Se le due scelte non coincidono, i totali non sono confrontabili
   nemmeno quando entrambi i moduli sono corretti — ed e' esattamente il modo
   in cui una divergenza puo' sopravvivere a lungo sembrando un errore di
   calcolo. La coincidenza delle finestre e' quindi un esito a se'.
3. *Il totale sulla finestra identica.* Deve venire uguale al bit. La
   tolleranza e' `1e-15` di frazione, cioe' l'errore di somma in virgola
   mobile su 240 addendi, non un margine di modello.

**L'unica differenza legittima, e perche' e' misurata a parte.** `costs/`
esclude i regolamenti che cadono in un'ora attraversata da un buco derivato dai
dati (`sources.unreliable_hours`): il rate c'e' ma potrebbe essere vecchio di
minuti rispetto all'istante di regolamento. `catalog/` non lo fa, perche'
descrive cio' che e' stato raccolto. Non e' una discordanza fra i due calcoli:
e' un filtro in piu' da una parte sola. Viene quantificato in `mask_delta_pct`
— quanto funding quelle ore valgono — invece di essere assorbito allargando una
tolleranza, che e' il modo in cui un cross-check smette di verificare qualcosa.

    .venv/bin/python -m costs.crosscheck --data-dir /home/ubuntu/hl-data/mainnet
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile

from catalog import funding as catalog_funding

from .funding import NS_PER_HOUR, FundingSeries
from .report import funding_window
from .side import LONG

# Somma di 240 addendi in doppia precisione: l'errore relativo resta di qualche
# unita' nell'ultimo bit. Qualunque cosa di piu' grande e' un fatto, non rumore.
EXACT_TOL_FRAC = 1e-15

FUNDING_CHANNEL = "activeAssetCtx"


def catalog_settlement_series(con, data_dir: str) -> dict[str, dict[int, float]]:
    """La serie oraria di `catalog/`, riportata a ore di regolamento.

    `catalog.funding_hourly` indicizza per ora di campionamento; il rate
    osservato durante l'ora H e' quello regolato all'inizio dell'ora H+1.
    """
    catalog_funding.build_samples(con, data_dir)
    catalog_funding.build_hourly_series(con)
    out: dict[str, dict[int, float]] = {}
    for coin, hour_idx, rate in con.execute(
        "SELECT coin, hour_idx, funding_last FROM funding_hourly"
    ).fetchall():
        out.setdefault(coin, {})[hour_idx + 1] = rate
    return out


def compare_hourly(costs_series: FundingSeries,
                   catalog_rates: dict[int, float]) -> dict:
    """Confronto ora per ora fra le due serie, sullo stesso indice.

    Le ore provvisorie sono escluse da entrambi i lati: il rate dell'ora di
    campionamento in corso cambia mentre lo si legge, e i due moduli lo leggono
    in due momenti diversi. Confrontarle misurerebbe la latenza fra due query,
    non un disaccordo.
    """
    prov = costs_series.provisional
    mine = {h: r for h, r in costs_series.rates.items() if h not in prov}
    theirs = {h: r for h, r in catalog_rates.items() if h not in prov}
    common = sorted(set(mine) & set(theirs))
    diverging = [(h, mine[h], theirs[h]) for h in common if mine[h] != theirs[h]]
    return {
        "n_common": len(common),
        "n_only_costs": len(set(mine) - set(theirs)),
        "n_only_catalog": len(set(theirs) - set(mine)),
        "n_diverging": len(diverging),
        "max_abs_diff": max((abs(a - b) for _, a, b in diverging), default=0.0),
        "worst_hours": [
            {"hour_idx": h, "costs": a, "catalog": b, "diff": a - b}
            for h, a, b in sorted(diverging, key=lambda t: -abs(t[1] - t[2]))[:5]
        ],
        "n_provisional_escluse": len(prov & set(costs_series.rates)),
    }


def compare_window(costs_series: FundingSeries, catalog_row: dict,
                   days: int, notional: float = 100.0) -> dict:
    """Finestre e totali dei due moduli, piu' il totale sulla finestra comune.

    `costs_no_mask` e' la somma di `costs/` sulla finestra del catalogo senza il
    filtro delle ore inaffidabili: e' il numero che deve coincidere al bit con
    quello del catalogo, perche' a quel punto i due moduli stanno sommando
    esattamente gli stessi addendi.
    """
    window = funding_window(costs_series, days)
    cat_first = catalog_row["first_settlement_hour_idx"]
    cat_last = catalog_row["last_settlement_hour_idx"]
    costs_cost = costs_series.cost(LONG, notional, *window)
    costs_first = window[0] // NS_PER_HOUR
    costs_last = window[1] // NS_PER_HOUR - 1

    # Sulla finestra del catalogo, senza maschera: stessi addendi da entrambe
    # le parti. Sommati qui a mano e non da `cost()` proprio perche' `cost()`
    # applica le esclusioni che qui vanno tolte di mezzo.
    hours = range(cat_first, cat_last + 1)
    costs_no_mask = sum(costs_series.rates[h] for h in hours
                        if h in costs_series.rates)
    catalog_frac = catalog_row["cum_funding_frac"]
    masked = sorted(h for h in hours if h in costs_series.unreliable
                    and h in costs_series.rates)
    return {
        "giorni": days,
        "costs_first_hour": costs_first,
        "costs_last_hour": costs_last,
        "catalog_first_hour": cat_first,
        "catalog_last_hour": cat_last,
        "finestre_coincidono": (costs_first == cat_first
                                and costs_last == cat_last),
        "costs_pct": costs_cost.cost_pct,
        "catalog_pct": catalog_row["cum_funding_pct"],
        "costs_pct_senza_maschera": costs_no_mask * 100.0,
        "diff_pct_finestra_comune": (costs_no_mask - catalog_frac) * 100.0,
        "coincidono": abs(costs_no_mask - catalog_frac) <= EXACT_TOL_FRAC,
        "n_ore_mascherate": len(masked),
        "mask_delta_pct": sum(costs_series.rates[h] for h in masked) * 100.0,
        "n_noti": costs_cost.n_known,
        "n_mancanti": costs_cost.n_missing,
        "n_inaffidabili": costs_cost.n_unreliable,
        "n_provvisori": costs_cost.n_provisional,
        "catalog_hours_observed": catalog_row["hours_observed"],
        "catalog_hours_missing": catalog_row["hours_missing"],
    }


def run(con, data_dir: str, days: int = 10,
        coins: list[str] | None = None,
        apply_gap_mask: bool = True) -> list[dict]:
    """Il confronto completo, coin per coin.

    `apply_gap_mask` esiste per i test sul campione registrato, dove i buchi
    derivati non sono quelli della produzione e la maschera non aggiungerebbe
    informazione. Sui dati veri va lasciato acceso: e' cio' che `costs/` fa
    davvero quando il report gira.
    """
    from . import sources

    if coins is None:
        coins = sources.discover_coins(data_dir, FUNDING_CHANNEL)
    catalog_rates = catalog_settlement_series(con, data_dir)
    catalog_rows = {r["coin"]: r for r in catalog_funding.cumulative_long(con, days)}

    unreliable: dict[tuple[str, str], frozenset[int]] = {}
    if apply_gap_mask:
        unreliable = sources.unreliable_hours(
            con, data_dir, [(FUNDING_CHANNEL, c) for c in coins])

    out = []
    for coin in coins:
        if coin not in catalog_rows:
            out.append({"coin": coin, "disponibile": False})
            continue
        mask = unreliable.get((FUNDING_CHANNEL, coin), frozenset())
        series = sources.funding_series(con, data_dir, coin, unreliable=mask)
        if funding_window(series, days) is None:
            out.append({"coin": coin, "disponibile": False})
            continue
        out.append({
            "coin": coin,
            "disponibile": True,
            "ore": compare_hourly(series, catalog_rates.get(coin, {})),
            "finestra": compare_window(series, catalog_rows[coin], days),
        })
    return out


def all_agree(rows: list[dict]) -> bool:
    """Vero se ogni coin disponibile concorda ora per ora e sul totale."""
    got = [r for r in rows if r.get("disponibile")]
    return bool(got) and all(
        r["ore"]["n_diverging"] == 0
        and r["finestra"]["finestre_coincidono"]
        and r["finestra"]["coincidono"]
        for r in got
    )


def format_report(rows: list[dict]) -> list[str]:
    L = ["", "=== funding: costs/ contro catalog/ ===", ""]
    for r in rows:
        if not r.get("disponibile"):
            L.append(f"  {r['coin']}: dati insufficienti")
            continue
        o, w = r["ore"], r["finestra"]
        L += [
            f"  {r['coin']}:",
            f"      ore confrontate {o['n_common']}   divergenti "
            f"{o['n_diverging']}   differenza max {o['max_abs_diff']:.3e}"
            f"   (solo costs {o['n_only_costs']}, solo catalog "
            f"{o['n_only_catalog']}, provvisorie escluse "
            f"{o['n_provisional_escluse']})",
            f"      finestra costs {w['costs_first_hour']}..{w['costs_last_hour']}"
            f"   catalog {w['catalog_first_hour']}..{w['catalog_last_hour']}"
            f"   {'coincidono' if w['finestre_coincidono'] else 'DIVERSE'}",
            f"      cumulato {w['giorni']}g: costs {w['costs_pct']:+.6f} %"
            f"   catalog {w['catalog_pct']:+.6f} %",
            f"      sulla stessa finestra e senza maschera dei buchi: costs "
            f"{w['costs_pct_senza_maschera']:+.6f} %   scarto "
            f"{w['diff_pct_finestra_comune']:+.3e} punti   "
            f"{'IDENTICI' if w['coincidono'] else 'DIVERSI'}",
            f"      maschera dei buchi: {w['n_ore_mascherate']} ore escluse da "
            f"costs, valgono {w['mask_delta_pct']:+.6f} punti",
        ]
        for h in o["worst_hours"]:
            L.append(f"      ora {h['hour_idx']}: costs {h['costs']:.10g} "
                     f"catalog {h['catalog']:.10g} diff {h['diff']:+.3e}")
    L += ["", f"esito: {'CONCORDI' if all_agree(rows) else 'DISCORDI'}", ""]
    return L


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Confronta il funding calcolato da costs/ e da catalog/.")
    p.add_argument("--data-dir", default="/home/ubuntu/hl-data/mainnet")
    p.add_argument("--days", type=int, default=10)
    p.add_argument("--coin", action="append", dest="coins")
    p.add_argument("--memory-limit", default="1GB")
    p.add_argument("--no-gap-mask", action="store_true",
                   help="non escludere le ore attraversate da buchi derivati")
    a = p.parse_args(argv)

    from . import sources

    with tempfile.TemporaryDirectory() as tmp:
        con = sources.connect(os.path.join(tmp, "duck"),
                              memory_limit=a.memory_limit)
        try:
            rows = run(con, a.data_dir, days=a.days, coins=a.coins,
                       apply_gap_mask=not a.no_gap_mask)
        finally:
            con.close()
    print("\n".join(format_report(rows)))
    # Codice di uscita diverso da zero se i due moduli non concordano: cosi' il
    # confronto puo' stare in uno script senza che nessuno debba leggerlo.
    return 0 if all_agree(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
