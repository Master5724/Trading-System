"""CLI del catalogo.

    python -m catalog --data-dir /path/ai/dati --out-dir ~/hl-reports

Nessun percorso e' cablato e nessuna directory dati e' privilegiata: il
comando gira identico su testnet, su mainnet o su una copia. Gli output vanno
sempre altrove — vedi `dataset.assert_read_only_layout`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

from . import dataset, funding, gapwindows, metrics, report, sanity, spread, trades


def _log(msg: str) -> None:
    print(f"[catalog] {msg}", file=sys.stderr, flush=True)


def _iso(ns: int | None) -> str | None:
    if ns is None:
        return None
    return datetime.fromtimestamp(ns / 1e9, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _json_default(o):
    if isinstance(o, datetime):
        return o.isoformat()
    raise TypeError(f"non serializzabile: {type(o)}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m catalog",
        description="Report di integrita' sui parquet raccolti dal collector "
                    "(sola lettura sulla directory dati).",
    )
    p.add_argument("--data-dir", required=True,
                   help="directory dati da scansionare (mai modificata)")
    p.add_argument("--out-dir", default="hl-reports",
                   help="directory di output; deve stare fuori da --data-dir")
    p.add_argument("--gaps-file", default=None,
                   help="registro dei buchi (default: <data-dir>/_gaps.jsonl)")
    p.add_argument("--funding-days", type=int, default=10,
                   help="orizzonte del funding cumulato di un long (default 10)")
    p.add_argument("--max-rows", type=int, default=40,
                   help="quante ore marcate stampare per esteso (default 40)")
    p.add_argument("--memory-limit", default="1GB",
                   help="tetto di memoria per DuckDB (default 1GB): il collector "
                        "gira sulla stessa macchina e non va affamato")
    p.add_argument("--threads", type=int, default=1,
                   help="thread DuckDB (default 1). Piu' thread = piu' picco di "
                        "memoria: il catalogo puo' essere lento, non puo' far "
                        "uccidere il collector dall'OOM killer")
    p.add_argument("--fixed-rate-channels",
                   default=",".join(sorted(dataset.DEFAULT_FIXED_RATE_CHANNELS)),
                   help="canali a cadenza fissa, separati da virgola: solo su "
                        "questi il conteggio orario sotto la mediana viene "
                        "marcato low_volume. Stringa vuota = nessun canale.")
    p.add_argument("--now-ms", type=int, default=None,
                   help="istante di riferimento per chiudere le finestre di gap "
                        "ancora aperte (default: adesso). Serve ai test.")
    return p.parse_args(argv)


def run(args: argparse.Namespace) -> dict:
    t_start = time.time()
    data_dir = os.path.realpath(args.data_dir)
    out_dir = os.path.realpath(args.out_dir)
    dataset.assert_read_only_layout(data_dir, out_dir)
    os.makedirs(out_dir, exist_ok=True)

    gaps_path = args.gaps_file or os.path.join(data_dir, dataset.GAPS_FILENAME)
    now_ms = args.now_ms if args.now_ms is not None else int(time.time() * 1000)

    partitions = dataset.discover(data_dir)
    channels = sorted({c for c, _ in partitions})
    l2_coins = sorted({coin for ch, coin in partitions if ch == "l2Book"})
    trade_coins = sorted({coin for ch, coin in partitions if ch == trades.CHANNEL})
    fixed_rate = frozenset(
        c.strip() for c in args.fixed_rate_channels.split(",") if c.strip()
    )
    _log(f"{len(partitions)} partizioni, canali: {', '.join(channels)}")

    con = dataset.connect(os.path.join(out_dir, ".duckdb-tmp"), args.memory_limit,
                          args.threads)
    res: dict = {"funding_days": args.funding_days}

    def step(label, fn):
        t0 = time.time()
        out = fn()
        _log(f"{label}: {time.time() - t0:.1f}s")
        return out

    # --- copertura e marcatura oraria ---------------------------------------
    windows, gaps_audit = gapwindows.load_with_audit(gaps_path, now_ms)
    gapwindows.materialize(con, windows)
    step("copertura oraria", lambda: metrics.build_hourly(con, data_dir, partitions))
    metrics.apply_gap_overlap(con)
    metrics.apply_low_volume(con, fixed_rate)

    # --- book: serve sia allo spread sia alla coerenza dei prezzi ------------
    step("estrazione top of book", lambda: spread.build_book_top(con, data_dir))
    spread.build_hourly_spread(con)
    metrics.attach_hourly_extra(con, "hourly_spread", ["spread_bps_p50", "mid_p50"])

    res["coverage"] = metrics.coverage(con)
    for r in res["coverage"]:
        r["first_utc"] = _iso(r["first_ts_ns"])
        r["last_utc"] = _iso(r["last_ts_ns"])
    res["flag_counts"] = metrics.flagged_counts(con)
    res["flagged"] = metrics.flagged_hours(con, args.max_rows)
    n_flagged_total = con.execute(
        "SELECT count(*) FROM hourly WHERE is_stream AND (gap_overlap OR low_volume)"
    ).fetchone()[0]
    res["flagged_truncated"] = max(0, n_flagged_total - len(res["flagged"]))

    res["fixed_rate_channels"] = sorted(fixed_rate)
    res["gaps"] = {
        "path": gaps_path,
        "exists": os.path.exists(gaps_path),
        "n": len(windows),
        "n_open": sum(1 for w in windows if w.still_open),
        "total_s": sum(w.duration_s for w in windows),
        "max_s": max((w.duration_s for w in windows), default=0.0),
        "audit": gaps_audit,
        "longest": [
            {
                "start_ms": w.start_ms,
                "start_utc": _iso(w.start_ms * 1_000_000),
                "duration_s": w.duration_s,
                "event": w.event,
                "origin": w.origin,
                "reason": w.reason[:80],
            }
            for w in sorted(windows, key=lambda w: -w.duration_s)[:5]
        ],
        "by_reason": [
            dict(zip(["reason", "n", "total_s", "max_s"], r))
            for r in con.execute(
                "SELECT reason, count(*), sum(end_ms - start_ms) / 1000.0,"
                " max(end_ms - start_ms) / 1000.0"
                " FROM gap_windows GROUP BY 1 ORDER BY 3 DESC"
            ).fetchall()
        ],
    }

    # --- sanita' -------------------------------------------------------------
    res["duplicates"] = step("duplicati", lambda: sanity.duplicates(con, data_dir, partitions))
    step("ordinamento righe", lambda: sanity.build_ordered(con, data_dir, partitions))
    res["monotonicity"] = sanity.monotonicity(con)
    res["monotonicity_breaks"] = sanity.monotonicity_breaks(con, args.max_rows)
    res["interarrival"] = sanity.interarrival(con)
    res["exch_ts_zero"] = sanity.exch_ts_zero(con)
    res["latency"] = step("latenza", lambda: sanity.latency(con, data_dir, partitions))
    res["partition_drift"] = step(
        "partizionamento", lambda: sanity.partition_drift(con, data_dir, partitions))
    res["trade_dup"] = (
        step("duplicati trade",
             lambda: trades.duplicate_stats(con, data_dir, trade_coins))
        if trade_coins else []
    )

    if "allMids" in channels and l2_coins:
        step("allMids", lambda: sanity.build_all_mids(con, data_dir, l2_coins))
        res["allmids_missing"] = sanity.all_mids_missing(con, l2_coins, data_dir)
        res["coherence"] = step("coerenza prezzi", lambda: sanity.price_coherence(con))
        res["coherence_worst"] = sanity.coherence_worst(con, args.max_rows)
    else:
        res["allmids_missing"], res["coherence"], res["coherence_worst"] = [], [], []

    # --- spread e funding ----------------------------------------------------
    res["spread"] = spread.stats(con)

    if "activeAssetCtx" in channels:
        step("funding", lambda: funding.build_samples(con, data_dir))
        funding.build_hourly_series(con)
        res["funding_stats"] = funding.stats(con)
        res["funding_cum"] = funding.cumulative_long(con, args.funding_days)
    else:
        res["funding_stats"], res["funding_cum"] = [], []

    if "backfill_funding" in channels and res["funding_stats"]:
        res["rest_funding_meta"] = step(
            "funding REST", lambda: funding.build_rest_series(con, data_dir))
        res["funding_cross"] = funding.cross_check(con)
        res["funding_cum_rest"] = funding.cumulative_long_rest(con, args.funding_days)
    else:
        res["rest_funding_meta"] = {
            k: 0 for k in ["n_settlements", "n_rows_before_dedup", "n_contradictory",
                           "n_coins", "first_time_ms", "last_time_ms", "n_off_hour"]
        }
        res["funding_cross"], res["funding_cum_rest"] = [], []

    # --- output --------------------------------------------------------------
    parquet_path = os.path.join(out_dir, "hourly_metrics.parquet")
    n_hourly = metrics.write_parquet(con, parquet_path)

    total_rows = sum(r["n_rows"] for r in res["coverage"])
    first_ns = min((r["first_ts_ns"] for r in res["coverage"] if r["first_ts_ns"]),
                   default=None)
    last_ns = max((r["last_ts_ns"] for r in res["coverage"] if r["last_ts_ns"]),
                  default=None)
    res["meta"] = {
        "data_dir": data_dir,
        "out_dir": out_dir,
        "run_utc": report.now_utc_str(),
        "n_partitions": len(partitions),
        "n_rows": total_rows,
        "first_utc": _iso(first_ns),
        "last_utc": _iso(last_ns),
        "span_days": (last_ns - first_ns) / 86_400e9 if first_ns and last_ns else 0.0,
        "elapsed_s": time.time() - t_start,
        "hourly_rows": n_hourly,
        "parquet": parquet_path,
    }

    lines = report.build(res)
    text = "\n".join(lines)
    with open(os.path.join(out_dir, "report.txt"), "w", encoding="utf-8") as f:
        f.write(text + "\n")
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2, default=_json_default, ensure_ascii=False)

    print(text)
    _log(f"parquet: {parquet_path} ({n_hourly} righe)")
    con.close()
    return res


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        run(args)
    except dataset.DataDirError as e:
        print(f"errore: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
