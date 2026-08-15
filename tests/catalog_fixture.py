"""Fabbrica di directory dati finte per i test del catalogo.

I test del catalogo NON girano sui dati di produzione: quelli sono scritti da
un collector vivo, cambiano sotto i piedi e sono in sola lettura. Qui si
costruisce a mano una data_dir con lo stesso layout del writer
(`<channel>/<coin>/date=.../hour=.../part-*.parquet`), riga per riga, cosi'
ogni numero atteso in un'asserzione e' esatto e non "circa".
"""

from __future__ import annotations

import json
import os

import duckdb

NS_PER_HOUR = 3_600_000_000_000

# Ora di riferimento arbitraria ma fissa (2026-08-01T00:00Z circa). Fissa e non
# "adesso": un test che dipende dall'orologio fallisce a mezzanotte.
BASE_HOUR_IDX = 1785_664_000_000 // 3_600_000


def write_partition(
    data_dir: str,
    channel: str,
    coin: str,
    rows: list[tuple[int, int, str]],
    date: str = "2026-08-01",
    hour: str = "00",
    part: str = "part-0.parquet",
) -> str:
    """Scrive un parquet con lo schema del writer. `rows` = (ts_local_ns,
    ts_exch_ms, raw). La directory `date=`/`hour=` e' l'ora del flush e non
    deve corrispondere ai timestamp: il catalogo legge l'ora da `ts_local_ns`,
    e tenerle diverse nei test impedisce che qualcuno torni a fidarsi del
    nome della directory."""
    out_dir = os.path.join(data_dir, channel, coin, f"date={date}", f"hour={hour}")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, part)
    con = duckdb.connect()
    con.execute(
        "CREATE TABLE t (ts_local_ns BIGINT, ts_exch_ms BIGINT, "
        "channel VARCHAR, coin VARCHAR, raw VARCHAR)"
    )
    con.executemany(
        "INSERT INTO t VALUES (?, ?, ?, ?, ?)",
        [(ts, ex, channel, coin, raw) for ts, ex, raw in rows],
    )
    con.execute(f"COPY t TO '{path}' (FORMAT PARQUET)")
    con.close()
    return path


def hourly_rows(counts: dict[int, int], payload: str = "{}") -> list[tuple[int, int, str]]:
    """Righe distribuite su piu' ore: `{offset_ora: n_righe}`.

    I timestamp restano dentro l'ora a cui appartengono (un messaggio al
    secondo), perche' e' la loro ora — non il file in cui finiscono — a
    decidere in che riga di `hourly` vengono contati.
    """
    rows: list[tuple[int, int, str]] = []
    for h, n in sorted(counts.items()):
        base = (BASE_HOUR_IDX + h) * NS_PER_HOUR
        for i in range(n):
            ts = base + i * 1_000_000_000
            rows.append((ts, ts // 1_000_000, payload))
    return rows


def trade_payload(trades: list[dict]) -> str:
    """Un messaggio del canale trades: array JSON di esecuzioni."""
    return json.dumps(trades, separators=(",", ":"))


def trade(tid: int, coin: str = "BTC", px: str = "100.0", sz: str = "1.0",
          side: str = "B", time_ms: int = 1_785_664_000_000,
          tx_hash: str | None = None) -> dict:
    return {
        "coin": coin,
        "side": side,
        "px": px,
        "sz": sz,
        "time": time_ms,
        "hash": tx_hash or f"0x{tid:064x}",
        "tid": tid,
        "users": ["0xaaa", "0xbbb"],
    }
