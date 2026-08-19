"""CLI: il modello di costo eseguito sui dati registrati.

    python -m costs --data-dir /home/ubuntu/hl-data/mainnet --out-dir ~/hl-reports

Nessun percorso e' cablato e nessuna coin e' privilegiata: le coin si scoprono
dal disco. Gli output vanno sempre fuori dalla directory dati — il controllo e'
lo stesso del catalogo (`dataset.assert_read_only_layout`), e non e' una
gentilezza: il collector sta scrivendo li' dentro mentre questo comando legge.

Sulla memoria: DuckDB parte con un tetto (`--memory-limit`) e un solo thread,
perche' questa macchina ha 2 core e sopra ci gira la raccolta dati. Il tetto di
DuckDB non copre tutto il processo, quindi il comando va comunque lanciato
dentro un cgroup:

    systemd-run --user --scope -p MemoryMax=2G nice -n 19 ionice -c 3 \\
        .venv/bin/python -m costs --data-dir ... --out-dir ...

**Un solo comando, non due.** Il cross-check del funding con `catalog/` gira
dentro questa esecuzione e con la stessa maschera dei buchi. Quando erano due
comandi separati, i due numeri di funding venivano da due istanti diversi: la
finestra scivola di un'ora ogni ora, quindi differivano legittimamente di
~0,001 punti, e quella differenza legittima rendeva indistinguibile una
differenza illegittima. `python -m costs.crosscheck` resta per il confronto da
solo, ma il report non ne dipende.

**Codice di uscita.** 0 se il report e' internamente coerente, 2 se non lo e':
un report che si contraddice non deve poter passare inosservato a uno script
che guarda solo l'uscita. I controlli sono in `costs.coherence`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

from catalog import dataset

from . import coherence, crosscheck
from . import report as costs_report
from . import sources, validate
from .fees import HYPERLIQUID_PERP_BASE, FeeSchedule, Liquidity
from .model import CostModel


def _log(msg: str) -> None:
    print(f"[costs] {msg}", file=sys.stderr, flush=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m costs",
        description="Modello di costo (fee, funding, slippage) eseguito sui "
                    "parquet registrati. Sola lettura sulla directory dati.",
    )
    p.add_argument("--data-dir", required=True)
    p.add_argument("--out-dir", default="hl-reports")
    p.add_argument("--coins", default="",
                   help="coin da analizzare, separate da virgola "
                        "(default: tutte quelle presenti su disco)")
    p.add_argument("--notionals", default="100,500,2000",
                   help="size in dollari da valutare. La prima e' quella usata "
                        "per il round-trip e per il funding")
    p.add_argument("--funding-days", type=int, default=10)
    p.add_argument("--leverages", default="1,2,5",
                   help="leve per cui riportare il costo sul capitale")
    p.add_argument("--sample-every-s", type=int, default=60,
                   help="un solo snapshot l2Book ogni N secondi (default 60): "
                        "camminare tutti gli snapshot di 17 giorni costerebbe "
                        "ore di CPU senza spostare una mediana")
    p.add_argument("--maker-rate", type=float,
                   default=HYPERLIQUID_PERP_BASE.maker_rate,
                   help="frazione del notional (0.00015 = 1,5 bps)")
    p.add_argument("--taker-rate", type=float,
                   default=HYPERLIQUID_PERP_BASE.taker_rate)
    p.add_argument("--tier", default=HYPERLIQUID_PERP_BASE.tier)
    p.add_argument("--validate-date", default="",
                   help="giorno (YYYY-MM-DD) su cui confrontare il modello con "
                        "i trade realmente eseguiti; vuoto = salta")
    p.add_argument("--validate-hour", default="*",
                   help="ora del giorno di validazione (default: tutte)")
    p.add_argument("--no-crosscheck", action="store_true",
                   help="salta il confronto del funding con catalog/. Il "
                        "controllo di coerenza corrispondente diventa NON "
                        "VERIFICATO e il comando esce comunque diverso da zero")
    p.add_argument("--memory-limit", default="1GB")
    p.add_argument("--threads", type=int, default=1)
    p.add_argument("--gap-p99-multiple", type=float, default=5.0)
    p.add_argument("--gap-min-s", type=float, default=30.0)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    a = parse_args(argv)
    dataset.assert_read_only_layout(a.data_dir, a.out_dir)
    os.makedirs(a.out_dir, exist_ok=True)

    notionals = [float(x) for x in a.notionals.split(",") if x.strip()]
    leverages = tuple(float(x) for x in a.leverages.split(",") if x.strip())
    fees = FeeSchedule(maker_rate=a.maker_rate, taker_rate=a.taker_rate,
                       tier=a.tier)
    model = CostModel(fees=fees)

    con = sources.connect(os.path.join(a.out_dir, "_duckdb_tmp"),
                          memory_limit=a.memory_limit, threads=a.threads)
    coins = ([c.strip() for c in a.coins.split(",") if c.strip()]
             or sources.discover_coins(a.data_dir))
    _log(f"coin: {', '.join(coins)}")
    _log(f"tier {fees.tier}: maker {fees.as_bps(Liquidity.MAKER):.2f} bps, "
         f"taker {fees.as_bps(Liquidity.TAKER):.2f} bps")

    t0 = time.time()
    partitions = [(sources.BOOK_CHANNEL, c) for c in coins]
    partitions += [(sources.FUNDING_CHANNEL, c) for c in coins]
    _log("derivo i buchi dai dati (l2Book e activeAssetCtx)...")
    unreliable = sources.unreliable_hours(
        con, a.data_dir, partitions,
        p99_multiple=a.gap_p99_multiple, min_gap_s=a.gap_min_s,
    )
    for key, hours in sorted(unreliable.items()):
        if hours:
            _log(f"  {key[0]}/{key[1]}: {len(hours)} ore inaffidabili")
    _log(f"buchi derivati in {time.time() - t0:.0f}s")

    out: dict = {
        "generato_utc": datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "data_dir": os.path.realpath(a.data_dir),
        "tier": fees.tier,
        "maker_bps": fees.as_bps(Liquidity.MAKER),
        "taker_bps": fees.as_bps(Liquidity.TAKER),
        "campionamento_book_s": a.sample_every_s,
        "notionals_usd": notionals,
        "coin": {},
    }
    lines: list[str] = []
    memo: dict[str, tuple[dict, dict, dict]] = {}

    for coin in coins:
        t1 = time.time()
        _log(f"{coin}: cammino il book sugli snapshot campionati...")
        books = sources.sample_books(
            con, a.data_dir, coin, every_s=a.sample_every_s,
            unreliable=unreliable.get((sources.BOOK_CHANNEL, coin), frozenset()),
        )
        sizes, execution = costs_report.slippage_over_books(model, books, notionals)
        book_stats = sources.sample_book_stats(
            con, a.data_dir, coin, every_s=a.sample_every_s,
            unreliable=unreliable.get((sources.BOOK_CHANNEL, coin), frozenset()),
        )

        _log(f"{coin}: funding...")
        series = sources.funding_series(
            con, a.data_dir, coin,
            unreliable=unreliable.get((sources.FUNDING_CHANNEL, coin), frozenset()),
        )
        rest = sources.rest_funding_series(con, a.data_dir, coin)
        funding = costs_report.funding_report(
            series, rest, notionals[0], a.funding_days, leverages
        )

        _log(f"{coin}: trade (dedup per tid)...")
        trade_stats = sources.trade_notional_stats(con, a.data_dir, coin)

        out["coin"][coin] = {
            "esecuzione": execution,
            "campionamento": book_stats,
            "slippage_per_size": {str(k): v for k, v in sizes.items()},
            "funding": funding,
            "trade": trade_stats,
        }
        memo[coin] = (sizes, execution, funding)
        lines += costs_report.format_coin(coin, sizes, execution, funding,
                                          trade_stats)
        _log(f"{coin}: fatto in {time.time() - t1:.0f}s")

    if a.validate_date:
        for coin in coins:
            _log(f"{coin}: confronto col mercato su {a.validate_date}...")
            v = validate.compare_sample(con, a.data_dir, coin, a.validate_date,
                                        a.validate_hour)
            out["coin"][coin]["validazione"] = v
            lines += [
                f"",
                f"  {coin} — modello contro trade reali del {v['date']} "
                f"({v['n_trades']:,} trade):",
                f"      lato coerente col book precedente: "
                f"{100 * (v['frac_side_coerente'] or 0):.1f} %",
                f"      slippage mediano reale {v['slippage_reale_bps_p50']:.3f} bps "
                f"contro modello {v['slippage_modello_bps_p50']:.3f} bps "
                f"(scarto mediano {v['errore_bps_p50']:+.3f})",
                f"      eta' del book usato: mediana {v['eta_book_ms_p50']:.0f} ms, "
                f"p99 {v['eta_book_ms_p99']:.0f} ms",
                f"      size oltre i 10 livelli: {v['n_size_oltre_book']:,}",
            ]

    # Il cross-check col catalogo gira DENTRO questa esecuzione e con la
    # stessa maschera dei buchi. Lanciarlo come comando separato produceva due
    # numeri di funding calcolati in due momenti diversi — e siccome la
    # finestra scivola di un'ora ogni ora, i due numeri differivano
    # legittimamente, il che rendeva impossibile distinguere quella differenza
    # da un errore.
    cross_rows: list[dict] = []
    if not a.no_crosscheck:
        _log("cross-check del funding con catalog/...")
        cross_rows = crosscheck.run(con, a.data_dir, days=a.funding_days,
                                    coins=coins, unreliable=unreliable)
        out["crosscheck_funding"] = cross_rows
        lines += crosscheck.format_report(cross_rows)
    cross_by_coin = {r["coin"]: r for r in cross_rows}

    checks: list[coherence.Check] = []
    for coin in coins:
        sizes, execution, funding = memo[coin]
        checks += coherence.check_coin(coin, execution, sizes, funding,
                                       cross_by_coin.get(coin))
    out["coerenza"] = [c.to_dict() for c in checks]
    out["coerente"] = coherence.all_ok(checks)

    lines += costs_report.format_summary(memo, notionals)
    lines += coherence.format_checks(checks)

    print("\n".join(lines))
    path = os.path.join(a.out_dir, "costs_report.json")
    with open(path, "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    _log(f"riepilogo JSON in {path} ({time.time() - t0:.0f}s totali)")
    if not out["coerente"]:
        # Diverso da zero: un report che si contraddice non deve poter essere
        # consumato da uno script che guarda solo il codice di uscita.
        _log("COERENZA INTERNA FALLITA: i numeri del report si contraddicono")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
