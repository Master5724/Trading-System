"""Estrae dai dati di produzione il campione reale usato dai test di `costs/`.

**Perche' un campione registrato e non dati sintetici.** Il funding cumulato e
lo slippage devono essere verificati sui dati veri: un test su rate inventati
verifica l'aritmetica e non il fatto che la serie sia stata letta e allineata
nel modo giusto. Ma un test che leggesse direttamente `/home/ubuntu/hl-data`
sarebbe non deterministico (il collector scrive mentre il test gira) e
inutilizzabile su un'altra macchina. Quindi: una fetta fissa, estratta una
volta, committata nel repo.

**Perche' e' riproducibile.** La finestra e' fissata a date assolute su un
periodo di raccolta gia' concluso, non a "le ultime N ore": rilanciare questo
script fra un mese deve produrre gli stessi byte. Se non li producesse, i dati
di produzione sarebbero cambiati sotto — e sarebbe una notizia.

**Sola lettura sulla produzione**, scrive solo dentro `tests/fixtures/`.

    .venv/bin/python tests/fixtures/make_costs_sample.py \\
        --data-dir /home/ubuntu/hl-data/mainnet
"""

from __future__ import annotations

import argparse
import os
from datetime import date as _date, timedelta

import duckdb

# Finestra fissa su un periodo gia' chiuso. Undici giorni: dieci servono al
# test del funding a 10 giorni, l'undicesimo copre il bordo.
FROM_DATE = "2026-08-03"
N_DAYS = 11
FUNDING_COINS = ("BTC", "HYPE")
BOOK_COINS = ("BTC", "ETH", "SOL", "HYPE")
BOOK_DAYS = 10
BOOK_HOURS = ("00", "06", "12", "18")
TRADE_COIN = "BTC"
TRADE_DATE = "2026-08-14"
TRADE_HOUR = "12"
N_TRADE_ROWS = 400

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_ROOT = os.path.join(HERE, "costs_sample")


def days(n: int) -> list[str]:
    d0 = _date.fromisoformat(FROM_DATE)
    return [(d0 + timedelta(days=i)).isoformat() for i in range(n)]


def dest(channel: str, coin: str) -> str:
    """Tutte le righe di una partizione in un solo file.

    La directory `date=`/`hour=` NON corrisponde ai timestamp delle righe: come
    nei dati veri e' l'ora in cui il writer ha aperto il batch. Tenerle diverse
    impedisce che un test torni per sbaglio a fidarsi del nome della directory
    invece che di `ts_local_ns`.
    """
    d = os.path.join(OUT_ROOT, channel, coin, "date=2026-08-01", "hour=00")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "part-0.parquet")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", default="/home/ubuntu/hl-data/mainnet")
    a = p.parse_args()
    con = duckdb.connect()
    con.execute("SET enable_external_file_cache = false")
    con.execute("SET memory_limit = '1GB'")
    con.execute("SET threads = 1")

    def glob(channel: str, coin: str, date: str = "*", hour: str = "*") -> str:
        return os.path.join(a.data_dir, channel, coin, f"date={date}",
                            f"hour={hour}", "*.parquet")

    def accumulate(sql: str, first: bool) -> None:
        con.execute(f"{'CREATE OR REPLACE TABLE _s AS' if first else 'INSERT INTO _s'} {sql}")

    def write(channel: str, coin: str) -> None:
        path = dest(channel, coin)
        con.execute(f"COPY (SELECT * FROM _s ORDER BY ts_local_ns) TO '{path}' "
                    f"(FORMAT PARQUET, COMPRESSION zstd)")
        n = con.execute(f"SELECT count(*) FROM read_parquet('{path}')").fetchone()[0]
        print(f"{channel}/{coin}: {n} righe -> {os.path.getsize(path) / 1024:.0f} KiB")

    # -- funding orario: un campione per ora, l'ULTIMO, che e' quello che il
    #    modello usa. Tenerli tutti moltiplicherebbe per mille la dimensione
    #    del fixture senza cambiare un solo numero atteso.
    for coin in FUNDING_COINS:
        for i, day in enumerate(days(N_DAYS)):
            accumulate(
                f"""
                SELECT * FROM read_parquet('{glob("activeAssetCtx", coin, day)}',
                                           hive_partitioning=false)
                QUALIFY row_number() OVER (
                    PARTITION BY ts_local_ns // 3600000000000
                    ORDER BY ts_local_ns DESC) = 1
                """,
                first=(i == 0),
            )
        write("activeAssetCtx", coin)

        # Serie REST di controllo: un dump ogni 40 ore. Ogni dump contiene 48 h
        # di storia, quindi la copertura resta continua con margine.
        accumulate(
            f"""
            SELECT * FROM read_parquet('{glob("backfill_funding", coin)}',
                                       hive_partitioning=false)
            QUALIFY row_number() OVER (
                PARTITION BY ts_local_ns // (40 * 3600000000000)
                ORDER BY ts_local_ns) = 1
            """,
            first=True,
        )
        write("backfill_funding", coin)

    # -- book: quattro snapshot al giorno per dieci giorni. Distribuiti sulle
    #    ore, non consecutivi: uno slippage misurato su cinque snapshot dello
    #    stesso minuto sarebbe cinque volte la stessa osservazione.
    for coin in BOOK_COINS:
        first = True
        for day in days(BOOK_DAYS):
            for hour in BOOK_HOURS:
                accumulate(
                    f"""
                    SELECT * FROM read_parquet(
                        '{glob("l2Book", coin, day, hour)}',
                        hive_partitioning=false)
                    ORDER BY ts_local_ns LIMIT 1
                    """,
                    first=first,
                )
                first = False
        write("l2Book", coin)

    # -- trade: righe consegnate cosi' come sono, ritrasmissioni comprese.
    #    Servono a verificare che `costs` legga dalla dedup del catalogo e non
    #    dal grezzo.
    accumulate(
        f"""SELECT * FROM read_parquet(
                '{glob("trades", TRADE_COIN, TRADE_DATE, TRADE_HOUR)}',
                hive_partitioning=false)
            ORDER BY ts_local_ns LIMIT {N_TRADE_ROWS}""",
        first=True,
    )
    write("trades", TRADE_COIN)

    total = sum(
        os.path.getsize(os.path.join(root, f))
        for root, _, files in os.walk(OUT_ROOT) for f in files
    )
    print(f"totale campione: {total / 1024:.0f} KiB in {OUT_ROOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
