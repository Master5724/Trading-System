"""Calcola le soglie di buco sullo storico e le congela in un file versionato.

E' l'UNICO punto del sistema in cui una soglia viene calcolata dai dati. Tutti
i consumatori — `costs`, `backtest`, `crosscheck` — leggono il file prodotto qui
e non ricalcolano niente.

**Perche' congelarle.** La soglia di una partizione e' un multiplo del p99 dei
suoi intervalli. Finche' la si ricalcola a ogni esecuzione, quel numero si
muove da solo insieme allo storico: fra due report a nove giorni di distanza la
soglia di `trades/SOL` e' passata da 82,48 s a 80,80 s perche' lo storico e'
cresciuto da 17 a 20 giorni. Due esecuzioni identiche sulla stessa finestra
davano quindi conteggi di buchi diversi, e la differenza non era nei dati della
finestra ma in dati che stanno FUORI da essa. Un backtest riprodotto a marzo
non deve dipendere da quanti dati sono arrivati a febbraio.

C'e' anche il caso peggiore, gia' misurato: se la soglia viene dal p99 della
finestra stessa, sale proprio quando la raccolta in quella finestra e' andata
male e maschera la degradazione che dovrebbe rilevare.

**Cosa NON risolve.** Congelare non rende la soglia giusta, la rende stabile e
riproducibile. Se il regime di una coin cambia davvero — piu' scambiata, quindi
intervalli piu' corti — la soglia congelata resta larga e smette di vedere
buchi che ora sarebbero anomali. Per questo il comando senza `--scrivi`
ricalcola e stampa lo scostamento: e' il controllo di scadenza, da fare a mano.

    python -m catalog.soglie --data-dir /home/ubuntu/hl-data/mainnet
    python -m catalog.soglie --data-dir /home/ubuntu/hl-data/mainnet --scrivi

Lettura completa dello storico: e' la fase che fa il picco di memoria del
sistema. Va lanciato sotto `systemd-run --user --scope -p MemoryMax=2G`, come
ogni job pesante su questa macchina.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import sys
import time
from datetime import datetime, timezone

from costs import sources

from . import dataset, derivedgaps, sanity

DEFAULT_DATA_DIR = "/home/ubuntu/hl-data/mainnet"


def _giorni(data_dir: str, partitions: list[tuple[str, str]]) -> list[str]:
    giorni: set[str] = set()
    for channel, coin in partitions:
        giorni.update(sources.dates_of(data_dir, channel, coin))
    return sorted(giorni)


def calcola(data_dir: str, tmp: str, p99_multiple: float, min_gap_s: float
            ) -> dict:
    """Il documento completo: soglie piu' lo storico da cui escono."""
    partitions = [p for p in dataset.discover(data_dir)
                  if p[0] not in dataset.BACKFILL_CHANNELS]
    giorni = _giorni(data_dir, partitions)
    t0 = time.monotonic()
    con = sources.connect(tmp)
    try:
        sanity.build_ordered(con, data_dir, partitions)
        soglie = derivedgaps.build_thresholds(
            con, p99_multiple=p99_multiple, min_gap_s=min_gap_s
        )
        n_righe = con.execute("SELECT count(*) FROM ts_ordered").fetchone()[0]
    finally:
        con.close()
    return {
        "versione": derivedgaps.FROZEN_VERSION,
        "calcolate_il": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "storico": {
            "data_dir": data_dir,
            "primo_giorno": giorni[0] if giorni else None,
            "ultimo_giorno": giorni[-1] if giorni else None,
            "n_giorni": len(giorni),
            "n_righe_ts_ordered": int(n_righe),
            "p99_multiple": p99_multiple,
            "min_gap_s": min_gap_s,
            "min_intervals_for_p99": derivedgaps.MIN_INTERVALS_FOR_P99,
            "secondi_di_calcolo": round(time.monotonic() - t0, 1),
            "picco_rss_mb": round(
                resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
        },
        "comando": (f"python -m catalog.soglie --data-dir {data_dir} "
                    f"--gap-p99-multiple {p99_multiple:g} "
                    f"--gap-min-s {min_gap_s:g} --scrivi"),
        "soglie": [
            {"channel": r["channel"], "coin": r["coin"],
             "n_intervals": int(r["n_intervals"]),
             "p99_s": round(float(r["p99_s"]), 6),
             "threshold_s": round(float(r["threshold_s"]), 6),
             "basis": r["basis"]}
            for r in soglie
        ],
    }


def scostamento(vecchio: dict, nuovo: dict) -> list[str]:
    """Riga per riga: soglia nel file, soglia appena misurata, variazione."""
    per_key = {(r["channel"], r["coin"]): r for r in vecchio["soglie"]}
    out = []
    for r in nuovo["soglie"]:
        k = (r["channel"], r["coin"])
        v = per_key.get(k)
        if v is None:
            out.append(f"{k[0]:16s} {k[1]:5s}  assente nel file  ->  "
                       f"{r['threshold_s']:8.3f} s  NUOVA")
            continue
        d = r["threshold_s"] - v["threshold_s"]
        out.append(f"{k[0]:16s} {k[1]:5s}  file {v['threshold_s']:8.3f} s  "
                   f"misurata {r['threshold_s']:8.3f} s  "
                   f"{d:+7.3f} s ({d / v['threshold_s'] * 100:+6.2f}%)")
    for k in sorted(set(per_key) - {(r["channel"], r["coin"])
                                    for r in nuovo["soglie"]}):
        out.append(f"{k[0]:16s} {k[1]:5s}  nel file ma non piu' nei dati")
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="catalog.soglie", description=__doc__)
    p.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    p.add_argument("--out", default=derivedgaps.FROZEN_PATH,
                   help="file delle soglie congelate")
    p.add_argument("--tmp", default="/tmp/soglie-duckdb")
    p.add_argument("--gap-p99-multiple", type=float,
                   default=derivedgaps.DEFAULT_P99_MULTIPLE)
    p.add_argument("--gap-min-s", type=float,
                   default=derivedgaps.DEFAULT_MIN_GAP_S)
    p.add_argument("--scrivi", action="store_true",
                   help="sovrascrive il file. Senza, stampa solo lo scostamento")
    args = p.parse_args(argv)

    os.makedirs(args.tmp, exist_ok=True)
    dataset.assert_read_only_layout(args.data_dir, args.tmp)
    nuovo = calcola(args.data_dir, args.tmp, args.gap_p99_multiple,
                    args.gap_min_s)
    s = nuovo["storico"]
    print(f"storico {s['primo_giorno']} -> {s['ultimo_giorno']} "
          f"({s['n_giorni']} giorni, {s['n_righe_ts_ordered']} righe), "
          f"{s['secondi_di_calcolo']} s, picco {s['picco_rss_mb']} MB")
    if os.path.exists(args.out):
        vecchio = derivedgaps.load_frozen(args.out)
        print(f"file esistente calcolato il {vecchio['calcolate_il']} su "
              f"{vecchio['storico']['n_giorni']} giorni")
        for riga in scostamento(vecchio, nuovo):
            print("  " + riga)
    else:
        for r in nuovo["soglie"]:
            print(f"  {r['channel']:16s} {r['coin']:5s} "
                  f"{r['threshold_s']:8.3f} s  ({r['basis']}, "
                  f"p99 {r['p99_s']:.3f} s su {r['n_intervals']} intervalli)")
    if not args.scrivi:
        print("nessuna scrittura (--scrivi per sovrascrivere)")
        return 0
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(nuovo, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print(f"scritto {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
