"""Esecuzione del motore sui dati registrati.

    .venv/bin/python -m backtest --data-dir /home/ubuntu/hl-data/mainnet \\
        --coins BTC --from 2026-08-16 --to 2026-08-17 --strategy random

Sola lettura su `data_dir`. Il default della strategia e' `flat`, che non manda
ordini: per far comparire un fill bisogna chiederlo esplicitamente.

Il riepilogo stampa numeri grezzi. In particolare stampa il **residuo di
conservazione** ricalcolato dal giornale: se non e' zero al centesimo, il resto
del riepilogo non va letto.
"""

from __future__ import annotations

import argparse
import os
import resource
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from costs import Side, sources                                    # noqa: E402
from costs.funding import NS_PER_HOUR                              # noqa: E402

from backtest import Engine, EngineConfig                          # noqa: E402
from backtest.feed import ParquetFeed, context, dates_between      # noqa: E402
from backtest import strategies as strat                           # noqa: E402

DEFAULT_DATA_DIR = "/home/ubuntu/hl-data/mainnet"


def ts_of(text: str) -> int:
    """`2026-08-16` o `2026-08-16T12:30` -> nanosecondi UTC."""
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        return int(dt.timestamp() * 1_000_000_000)
    raise argparse.ArgumentTypeError(f"data non riconosciuta: {text}")


def pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    i = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return s[i]


def build_strategy(name: str, coin: str, notional: float, seed: int):
    if name == "flat":
        return strat.Flat()
    if name == "random":
        return strat.RandomTaker(coin, notional=notional, seed=seed)
    if name == "always_long":
        return strat.AlwaysLong(coin, notional=notional)
    if name == "maker":
        return strat.MakerQuote(coin, side=Side.BUY, notional=notional)
    raise SystemExit(f"strategia sconosciuta: {name}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="backtest", description=__doc__)
    p.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    p.add_argument("--coins", default="BTC")
    p.add_argument("--from", dest="start", type=ts_of, required=True)
    p.add_argument("--to", dest="end", type=ts_of, required=True)
    p.add_argument("--bar-s", type=int, default=60)
    p.add_argument("--strategy", default="flat")
    p.add_argument("--notional", type=float, default=1_000.0)
    p.add_argument("--equity", type=float, default=10_000.0)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--max-book-age-s", type=float, default=30.0)
    p.add_argument("--out", default="")
    p.add_argument("--temp-dir", default="/tmp/backtest-duckdb")
    # 512MB e non 1GB: misurato, il picco del processo insegue il tetto dato a
    # DuckDB (1331 MB con 1GB, 706 MB con 512MB) e il risultato non cambia di
    # un bit — stesso digest nelle due esecuzioni. Su questa macchina il tetto
    # lo condivide col collector, quindi il default e' il piu' basso dei due.
    p.add_argument("--memory-limit", default="512MB")
    args = p.parse_args(argv)

    coins = tuple(sorted(c.strip() for c in args.coins.split(",") if c.strip()))
    if len(coins) != 1 and args.strategy != "flat":
        raise SystemExit("le strategie fittizie operano su una coin sola")
    cfg = EngineConfig(bar_s=args.bar_s, initial_equity=args.equity,
                       max_book_age_s=args.max_book_age_s)

    t0 = time.monotonic()
    con = sources.connect(args.temp_dir, memory_limit=args.memory_limit,
                          threads=1)
    # La barra di avanzamento di DuckDB scrive sul terminale mentre il report
    # scrive sullo stesso terminale: l'output integrale che finisce nel
    # riepilogo del task non deve contenere sequenze di controllo.
    con.execute("SET enable_progress_bar = false")
    giorni = dates_between(args.start, args.end)
    funding, blocked, fstats = context(con, args.data_dir, coins, giorni)
    t_ctx = time.monotonic() - t0
    # `ru_maxrss` e' un massimo storico, non l'occupazione istantanea: leggerlo
    # a fine di ogni fase dice QUALE fase ha fatto il picco, che e' l'unica
    # domanda utile quando il tetto di memoria e' condiviso col collector.
    peak_ctx_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0

    feed = ParquetFeed(con, args.data_dir, coins, args.start, args.end)
    strategy = build_strategy(args.strategy, coins[0], args.notional, args.seed)
    engine = Engine(cfg, strategy, coins, funding=funding, blocked=blocked)
    t1 = time.monotonic()
    r = engine.run(feed, args.start, args.end)
    t_run = time.monotonic() - t1
    peak_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0

    out = args.out
    if out:
        os.makedirs(out, exist_ok=True)
        r.journal.write(os.path.join(out, "journal.csv"),
                        os.path.join(out, "equity.csv"))

    w = sys.stdout.write
    w("=== CONFIGURAZIONE ===\n")
    w(f"data_dir              {args.data_dir}\n")
    w(f"coin                  {','.join(coins)}\n")
    w(f"finestra              {args.start} -> {args.end} "
      f"({(args.end - args.start) / 3600e9:.2f} ore)\n")
    w(f"giorni letti          {','.join(giorni)} (finestra + 1 per lato)\n")
    w(f"barra                 {cfg.bar_s}s\n")
    w(f"strategia             {args.strategy} (seed={args.seed}, "
      f"notional={args.notional!r})\n")
    w(f"equity iniziale       {cfg.initial_equity!r}\n")
    w(f"eta' max del book     {cfg.max_book_age_s!r}s\n")
    w(f"fee                   maker {cfg.fees.maker_rate!r} / "
      f"taker {cfg.fees.taker_rate!r} (tier {cfg.fees.tier})\n")

    w("\n=== DATI ===\n")
    w(f"righe book lette      {feed.n_book_rows}\n")
    w(f"book inutilizzabili   {feed.n_book_invalid}\n")
    w(f"trade deduplicati     {feed.n_trade_rows}\n")
    for coin in coins:
        s = fstats[coin]
        w(f"{coin}: ore bloccate {s['ore_bloccate']}, regolamenti di funding "
          f"noti {s['regolamenti_noti']}, primo {s['primo_regolamento']}, "
          f"ultimo definitivo {s['ultimo_definitivo']}\n")
        blocked_in_window = sorted(
            h for h in blocked.per_coin.get(coin, ())
            if args.start // NS_PER_HOUR <= h <= args.end // NS_PER_HOUR
        )
        w(f"{coin}: ore bloccate DENTRO la finestra {len(blocked_in_window)} "
          f"{blocked_in_window}\n")
        # La soglia decide cosa e' un buco e cosa no, e cambia col p99 degli
        # intervalli osservati: leggendo pochi giorni puo' salire. Il buco piu'
        # corto mai osservato su questi dati dura 41,4 s, quindi una soglia che
        # si avvicina a quel numero va vista, non dedotta.
        w(f"{coin}: soglie di buco (s) {s['soglie']}\n")

    w("\n=== BARRE E ORDINI ===\n")
    w(f"barre                 {r.n_bars}\n")
    w(f"barre con decisione   {r.n_bars_decided}\n")
    w(f"barre tutte bloccate  {r.n_bars_all_blocked}\n")
    w(f"ordini                {r.n_orders}\n")
    w(f"fill taker            {r.n_fills_taker}\n")
    w(f"fill maker            {r.n_fills_maker}\n")
    w(f"rifiuti               {r.journal.count('reject')} {r.rejects}\n")
    w(f"chiusure d'ufficio    {r.n_forced_close} "
      f"(fallite {r.n_forced_close_failed})\n")
    ages = r.book_age_ms
    w(f"eta' del book sui fill (ms)  n={len(ages)} min={pct(ages, 0.0)} "
      f"p50={pct(ages, 0.5)} p90={pct(ages, 0.9)} max={pct(ages, 1.0)}\n")

    w("\n=== CONTO ===\n")
    w(f"equity iniziale       {r.initial_equity!r}\n")
    w(f"equity finale         {r.final_equity!r}\n")
    w(f"PnL                   {r.pnl!r}\n")
    w(f"realizzato            {r.realized_pnl!r}\n")
    w(f"fee                   {r.fees_paid!r}\n")
    w(f"funding               {r.funding_paid!r} "
      f"({r.n_settlements} regolamenti, {r.n_funding_unknown} non noti)\n")
    w(f"residuo conservazione {r.conservation_residual!r}\n")
    w(f"posizione finale      "
      f"{ {c: r.journal.equity_rows[-1].get('pos_' + c) for c in coins} }\n")

    w("\n=== RIPRODUCIBILITA' ===\n")
    w(f"digest sha256         {r.digest()}\n")
    w(f"righe di giornale     {len(r.journal.rows)}\n")
    w(f"righe di equity       {len(r.journal.equity_rows)}\n")
    if out:
        w(f"scritti               {out}/journal.csv, {out}/equity.csv\n")

    w("\n=== TEMPI E MEMORIA ===\n")
    w(f"contesto (gap+funding) {t_ctx:.2f}s\n")
    w(f"simulazione            {t_run:.2f}s\n")
    w(f"picco RSS dopo contesto {peak_ctx_mb:.1f} MB\n")
    w(f"picco RSS a fine run    {peak_mb:.1f} MB\n")
    w(f"memory_limit DuckDB     {args.memory_limit}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
