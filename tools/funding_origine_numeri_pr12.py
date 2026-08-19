"""Analisi una-tantum per la PR #12: da quale finestra vengono i numeri di
funding della prima consegna (BTC 0,042 %, ETH 0,051 %, SOL 0,067 %,
HYPE 0,089 %), che erano 3-4 volte piu' bassi di quelli attuali.

Sola lettura. Non fa parte del modulo `costs/`.

Il codice che li ha prodotti non e' in git (il primo commit di `costs/` gia'
contiene il modello attuale), quindi l'unica ricostruzione possibile e' sui
DATI: si cerca la finestra [H_end - k + 1, H_end] che, sommata sui rate
registrati, riproduce quei quattro numeri contemporaneamente. Se una finestra
del genere esiste ed e' molto piu' corta di 240 ore, la causa era la finestra,
non il modello. Se non esiste, la causa non e' ricostruibile dai dati e va
dichiarata tale.
"""

from __future__ import annotations

import argparse
import os
import tempfile

from costs import sources
from costs.funding import hour_utc

TARGET_LONG = {"BTC": 0.042, "ETH": 0.051, "SOL": 0.067, "HYPE": 0.089}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", default="/home/ubuntu/hl-data/mainnet")
    p.add_argument("--memory-limit", default="1GB")
    a = p.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        con = sources.connect(os.path.join(tmp, "duck"),
                              memory_limit=a.memory_limit, threads=1)
        series = {c: sources.funding_series(con, a.data_dir, c)
                  for c in TARGET_LONG}
        con.close()

    for coin, s in series.items():
        lo, hi = s.span
        print(f"{coin}: {len(s)} regolamenti, da {hour_utc(lo)} a {hour_utc(hi)}")

    hi = min(max(s.rates) for s in series.values())
    lo = max(min(s.rates) for s in series.values())

    # Somme prefisse: la ricerca prova ~160.000 finestre, e rifare la somma
    # dentro il ciclo costerebbe minuti di CPU su una macchina che deve
    # soprattutto non disturbare il collector.
    prefix: dict[str, dict[int, float]] = {}
    for coin, s in series.items():
        acc = 0.0
        pref = {lo - 1: 0.0}
        for h in range(lo, hi + 1):
            acc += s.rates.get(h, 0.0)
            pref[h] = acc
        prefix[coin] = pref

    def cum(coin: str, first: int, last: int) -> float:
        pref = prefix[coin]
        return 100.0 * (pref[last] - pref[max(first, lo) - 1])

    best = []
    for h_end in range(lo + 1, hi + 1):
        for k in range(1, min(400, h_end - lo + 1) + 1):
            first = h_end - k + 1
            err = max(abs(cum(c, first, h_end) - t) / t
                      for c, t in TARGET_LONG.items())
            best.append((err, h_end, k))
    best.sort()

    print(f"\nle 10 finestre che meglio riproducono i quattro numeri "
          f"(errore = massimo scarto relativo fra le 4 coin):\n")
    print(f"  {'errore':>8}  {'ore':>4}  {'fine (UTC)':>16}   "
          + "   ".join(f"{c:>7}" for c in TARGET_LONG))
    print(f"  {'atteso':>8}  {'':>4}  {'':>16}   "
          + "   ".join(f"{t:>7.3f}" for t in TARGET_LONG.values()))
    for err, h_end, k in best[:10]:
        first = h_end - k + 1
        print(f"  {err:>8.1%}  {k:>4}  {hour_utc(h_end):>16}   "
              + "   ".join(f"{cum(c, first, h_end):>7.3f}" for c in TARGET_LONG))

    print("\nper riferimento, la finestra di 240 ore che finisce all'ultimo "
          "regolamento disponibile:")
    print("  " + "   ".join(f"{c} {cum(c, hi - 239, hi):.4f} %"
                            for c in TARGET_LONG))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
