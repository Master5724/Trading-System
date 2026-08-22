"""Controllo di deriva delle soglie congelate, a costo di un giorno di dati.

**Il problema che risolve.** Le soglie di buco sono congelate in
`gap_thresholds.json` e nessun comando le ricalcola da solo: e' quello che le
rende riproducibili. Il prezzo e' che una soglia congelata non si accorge se il
regime della partizione cambia. Se `trades/SOL` diventa piu' scambiata, i suoi
intervalli si accorciano, e la soglia di 79,37 s calcolata a agosto resta larga:
smette di vedere silenzi che ora sarebbero anomali, e non lo dice.

Il controllo che serve non e' un ricalcolo — quello riporterebbe la deriva
dentro il sistema. E' un CONFRONTO: il p99 del solo ultimo giorno accanto al p99
congelato, e quanti intervalli di quel giorno superano la soglia. Due numeri per
partizione, ogni giorno, dentro il report. Se il rapporto fra i due p99 scende o
sale in modo persistente, e' un umano a decidere se rigenerare il file con
`python -m catalog.soglie --scrivi`.

**Perche' legge due giorni e non uno.** Il `delta_ns` della prima riga di un
giorno si misura rispetto all'ultima riga del giorno prima. Senza il giorno
precedente quel delta e' NULL e un'interruzione a cavallo della mezzanotte —
l'ora in cui e' piu' probabile che nessuno stia guardando — non comparirebbe.
Si legge il margine, si conta solo il giorno richiesto.

**Perche' l'ultimo giorno COMPLETO.** Il p99 del giorno in corso e' calcolato
sulle ore trascorse: alle 06:10 UTC sarebbero le sei ore di notte, cioe' le piu'
lente, e il confronto direbbe "deriva" ogni mattina. Il default e' quindi
l'ultimo giorno intero presente nei dati.

    python -m catalog.deriva --data-dir /home/ubuntu/hl-data/mainnet

Costo misurato sui dati veri (17 partizioni, due giorni): vedi README.
"""

from __future__ import annotations

import argparse
import os
import resource
import sys
import time
from datetime import date, datetime, timedelta, timezone

from costs import sources

from . import dataset, derivedgaps, sanity

DEFAULT_DATA_DIR = "/home/ubuntu/hl-data/mainnet"
NS_PER_S = 1_000_000_000
NS_PER_HOUR = 3_600 * NS_PER_S

COLONNE = ["channel", "coin", "threshold_s", "p99_congelato_s", "p99_giorno_s",
           "rapporto", "n_intervalli", "n_oltre", "n_ore", "basis"]


def _rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def _ns_del_giorno(giorno: str) -> tuple[int, int]:
    d = datetime.strptime(giorno, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return (int(d.timestamp()) * NS_PER_S,
            int((d + timedelta(days=1)).timestamp()) * NS_PER_S)


def giorno_da_controllare(data_dir: str, partitions: list[tuple[str, str]],
                          oggi: str | None = None) -> str | None:
    """L'ultimo giorno intero presente nei dati, cioe' il piu' recente che non
    sia quello in corso."""
    oggi = oggi or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    giorni = {g for ch, coin in partitions
              for g in sources.dates_of(data_dir, ch, coin) if g < oggi}
    return max(giorni) if giorni else None


def controlla(con, data_dir: str, partitions: list[tuple[str, str]],
              giorno: str, frozen: list[dict] | None,
              p99_multiple: float = derivedgaps.DEFAULT_P99_MULTIPLE,
              min_gap_s: float = derivedgaps.DEFAULT_MIN_GAP_S,
              ) -> list[dict]:
    """Una riga per partizione: soglia in uso, i due p99, quanti intervalli la
    superano nel giorno richiesto e quante ore toccano.

    `frozen=None` significa soglie misurate sui due giorni letti: e' la
    modalita' che NON va usata per giudicare (il p99 della finestra si calcola
    sulle stesse righe di cui deve giudicare la completezza), ed esiste per
    poter mostrare quanto le due origini divergono.
    """
    giorno_prima = (date.fromisoformat(giorno) - timedelta(days=1)).isoformat()
    sanity.build_ordered(con, data_dir, partitions, [giorno_prima, giorno])
    usate = derivedgaps.build_thresholds(
        con, p99_multiple=p99_multiple, min_gap_s=min_gap_s, frozen=frozen)
    per_key = {(r["channel"], r["coin"]): r for r in usate}

    a, b = _ns_del_giorno(giorno)
    righe = con.execute(
        """
        SELECT o.channel, o.coin,
               count(*)                                   AS n_intervalli,
               count(*) FILTER (WHERE o.delta_ns >= t.threshold_ns) AS n_oltre,
               quantile_cont(o.delta_ns, 0.99) / 1e9      AS p99_giorno_s
        FROM ts_ordered o
        JOIN gap_thresholds t USING (channel, coin)
        WHERE o.delta_ns IS NOT NULL
          AND o.ts_local_ns >= ? AND o.ts_local_ns < ?
        GROUP BY 1, 2
        """,
        [a, b],
    ).fetchall()

    # Le ore toccate: gli intervalli oltre soglia sono pochi per definizione,
    # quindi si portano in Python invece di aggregare in SQL.
    ore: dict[tuple[str, str], set[int]] = {}
    for ch, coin, fine, delta in con.execute(
        """
        SELECT o.channel, o.coin, o.ts_local_ns, o.delta_ns
        FROM ts_ordered o JOIN gap_thresholds t USING (channel, coin)
        WHERE o.delta_ns >= t.threshold_ns
          AND o.ts_local_ns >= ? AND o.ts_local_ns < ?
        """,
        [a, b],
    ).fetchall():
        ore.setdefault((ch, coin), set()).update(
            range((fine - delta) // NS_PER_HOUR, fine // NS_PER_HOUR + 1))

    out = []
    for ch, coin, n_int, n_oltre, p99_g in righe:
        s = per_key[(ch, coin)]
        p99_c = float(s["p99_s"])
        out.append(dict(zip(COLONNE, [
            ch, coin, float(s["threshold_s"]), p99_c, float(p99_g),
            (float(p99_g) / p99_c) if p99_c else float("nan"),
            int(n_int), int(n_oltre), len(ore.get((ch, coin), ())),
            s["basis"],
        ])))
    out.sort(key=lambda r: (-r["rapporto"], r["channel"], r["coin"]))
    dataset.drop(con, "ts_ordered", "gap_thresholds")
    return out


def intestazione(doc: dict | None, partitions: list[tuple[str, str]],
                 nei_dati: int) -> list[str]:
    """Provenienza delle soglie e copertura.

    Esiste perche' il numero di partizioni coperte e' cambiato da 12 a 17 senza
    che nessun report lo dicesse: un conteggio di buchi che raddoppia perche' e'
    cambiato l'insieme delle partizioni, e non i dati, e' esattamente il genere
    di cosa che un report deve rendere impossibile da non notare.
    """
    if doc is None:
        return [f"soglie: misurate sui giorni letti (nessun file), "
                f"{len(partitions)} partizioni chieste, {nei_dati} nei dati"]
    s = doc["storico"]
    n_righe = f"{s['n_righe_ts_ordered']:,}".replace(",", ".")
    return [
        f"soglie: congelate v{doc['versione']}, calcolate il "
        f"{doc['calcolate_il']}",
        f"  storico di calcolo: {s['primo_giorno']} -> {s['ultimo_giorno']} "
        f"({s['n_giorni']} giorni, {n_righe} righe)",
        f"  copertura: {len(doc['soglie'])} partizioni nel file, "
        f"{len(partitions)} chieste, {nei_dati} presenti nei dati",
    ]


def tabella(righe: list[dict]) -> list[str]:
    out = [f"{'partizione':<22} {'soglia':>7} {'p99cong':>8} {'p99gg':>7} "
           f"{'rapp':>5} {'oltre':>6} {'ore':>4}"]
    for r in righe:
        out.append(
            f"{r['channel'] + '/' + r['coin']:<22} {r['threshold_s']:>7.2f} "
            f"{r['p99_congelato_s']:>8.2f} {r['p99_giorno_s']:>7.2f} "
            f"{r['rapporto']:>5.2f} {r['n_oltre']:>6} {r['n_ore']:>4}")
    return out


def sommario(righe: list[dict]) -> list[str]:
    """Le due righe che qualcuno leggera' davvero.

    Nessuna soglia di allarme: quale scostamento sia troppo dipende da cosa si
    sta facendo con i dati, e inventare qui un numero significherebbe tararlo su
    niente. Si stampa il caso peggiore e lo si lascia leggere.
    """
    if not righe:
        return ["nessuna partizione con intervalli nel giorno richiesto"]
    peggio = righe[0]
    tot_oltre = sum(r["n_oltre"] for r in righe)
    tot_int = sum(r["n_intervalli"] for r in righe)
    tot_ore = sum(r["n_ore"] for r in righe)
    return [
        f"scostamento massimo: {peggio['channel']}/{peggio['coin']} "
        f"p99 {peggio['p99_congelato_s']:.2f} s congelato -> "
        f"{peggio['p99_giorno_s']:.2f} s nel giorno "
        f"({(peggio['rapporto'] - 1) * 100:+.1f}%)",
        f"intervalli oltre soglia: {tot_oltre} su "
        + f"{tot_int:,}".replace(",", ".") + f"; ore marcate: {tot_ore}",
    ]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="catalog.deriva", description=__doc__)
    p.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    p.add_argument("--tmp", default="/tmp/deriva-duckdb")
    p.add_argument("--giorno", default=None,
                   help="default: l'ultimo giorno intero presente nei dati")
    p.add_argument("--soglie", choices=list(sources.SOGLIE_MODI),
                   default=sources.SOGLIE_CONGELATE)
    p.add_argument("--soglie-path", default=derivedgaps.FROZEN_PATH)
    p.add_argument("--memory-limit", default="128MB",
                   help="tetto di DuckDB. Misurato: e' QUESTO numero a fissare "
                        "il picco RSS, non la mole dei dati letti — 512MB dava "
                        "569 MB di picco, 128MB ne da' 226, e i risultati sono "
                        "identici perche' cambia solo quanto si versa su disco")
    args = p.parse_args(argv)

    if args.soglie == sources.SOGLIE_FISSE:
        p.error("modo 'fisse' non previsto qui: il controllo confronta il p99 "
                "del giorno con quello congelato, e una soglia fissa non ha p99")

    t0 = time.monotonic()
    os.makedirs(args.tmp, exist_ok=True)
    dataset.assert_read_only_layout(args.data_dir, args.tmp)
    tutte = [q for q in dataset.discover(args.data_dir)
             if q[0] not in dataset.BACKFILL_CHANNELS]
    doc = (derivedgaps.load_frozen(args.soglie_path)
           if args.soglie == sources.SOGLIE_CONGELATE else None)
    # Si controllano le partizioni per cui esiste una soglia: se il file ne
    # copre meno di quante ce ne sono nei dati, l'intestazione lo dice invece
    # di far fallire il report giornaliero.
    partitions = ([q for q in tutte
                   if (q[0], q[1]) in {(r["channel"], r["coin"])
                                       for r in doc["soglie"]}]
                  if doc else tutte)
    giorno = args.giorno or giorno_da_controllare(args.data_dir, partitions)
    if giorno is None:
        print("nessun giorno intero nei dati")
        return 1

    print(f"=== SOGLIE DI BUCO E DERIVA ({giorno}) ===")
    for r in intestazione(doc, partitions, len(tutte)):
        print(r)

    con = sources.connect(args.tmp, memory_limit=args.memory_limit)
    try:
        frozen = (derivedgaps.frozen_rows(doc, partitions) if doc else None)
        righe = controlla(con, args.data_dir, partitions, giorno, frozen)
    finally:
        con.close()
    for r in tabella(righe) + sommario(righe):
        print(r)
    print(f"costo del controllo: 2 giorni letti, "
          f"{time.monotonic() - t0:.1f} s, picco RSS {_rss_mb():.0f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
