"""Analisi una-tantum per la PR #12: da dove viene lo spostamento dello
slippage mediano di BTC fra due esecuzioni del report.

Sola lettura sulla directory dati. Non fa parte del modulo `costs/`: e' uno
strumento di indagine, e sta fuori apposta.

Per ogni coin e per ogni giorno: mediana del mezzo spread in bps, mediana del
mid, mediana dello spread in valuta, e tick minimo osservato. Se il mezzo
spread in bps si muove mentre lo spread in valuta resta fermo, la causa e' il
prezzo, non il book.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile

from costs import sources
from costs.slippage import L2Book


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", default="/home/ubuntu/hl-data/mainnet")
    p.add_argument("--every-s", type=int, default=300)
    p.add_argument("--memory-limit", default="1GB")
    a = p.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        con = sources.connect(os.path.join(tmp, "duck"),
                              memory_limit=a.memory_limit, threads=1)
        coins = sources.discover_coins(a.data_dir)
        out: dict[str, list] = {}
        for coin in coins:
            rows = []
            for date in sources.dates_of(a.data_dir, sources.BOOK_CHANNEL, coin):
                books = list(sources.sample_books(con, a.data_dir, coin,
                                                  every_s=a.every_s,
                                                  dates=[date]))
                if not books:
                    continue
                half_bps = sorted(1e4 * b.half_spread_frac for b in books)
                spread = sorted(b.spread for b in books)
                mid = sorted(b.mid for b in books)
                n = len(books)
                rows.append({
                    "date": date,
                    "n": n,
                    "half_spread_bps_p50": half_bps[n // 2],
                    "spread_p50": spread[n // 2],
                    "mid_p50": mid[n // 2],
                    "spread_min": spread[0],
                })
            out[coin] = rows
            print(f"\n=== {coin} ===")
            print(f"  {'giorno':>12}  {'n':>5}  {'mezzo spread bps':>17}  "
                  f"{'spread $':>12}  {'mid $':>12}  {'spread min $':>13}")
            for r in rows:
                print(f"  {r['date']:>12}  {r['n']:>5}  "
                      f"{r['half_spread_bps_p50']:>17.4f}  "
                      f"{r['spread_p50']:>12.6g}  {r['mid_p50']:>12.6g}  "
                      f"{r['spread_min']:>13.6g}")
        con.close()
    with open("/tmp/btc_spread_by_day.json", "w") as f:
        json.dump(out, f, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
