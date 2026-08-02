"""
Scrittura append-only su Parquet, partizionata per canale/coin/giorno/ora.

Principio: si salva il messaggio GREZZO piu' i due timestamp. Nessuna
normalizzazione in ingest. Se fra tre mesi scopri un bug nel parser, rifai
il parsing sullo storico; se avessi normalizzato in ingest, quel dato e'
perso per sempre.

Layout:
  data/<channel>/<coin>/date=YYYY-MM-DD/hour=HH/part-<ns>.parquet

Schema:
  ts_local_ns  int64   ricezione locale, monotona sul tuo host
  ts_exch_ms   int64   timestamp dichiarato dall'exchange (0 se assente)
  channel      string
  coin         string  ("" per canali globali/utente)
  raw          string  JSON originale del payload
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone

import pyarrow as pa
import pyarrow.parquet as pq

SCHEMA = pa.schema(
    [
        ("ts_local_ns", pa.int64()),
        ("ts_exch_ms", pa.int64()),
        ("channel", pa.string()),
        ("coin", pa.string()),
        ("raw", pa.string()),
    ]
)


class WriterPool:
    """Un buffer per (channel, coin). Flush per righe o per tempo. Thread-safe."""

    def __init__(self, data_dir: str, flush_rows: int = 5000,
                 flush_seconds: int = 60, compression: str = "zstd"):
        self.data_dir = data_dir
        self.flush_rows = flush_rows
        self.flush_seconds = flush_seconds
        self.compression = compression
        self._buf: dict[tuple[str, str], list[dict]] = defaultdict(list)
        self._last_flush: dict[tuple[str, str], float] = defaultdict(time.monotonic)
        self._lock = threading.Lock()
        self.rows_written = 0

    def add(self, channel: str, coin: str, ts_exch_ms: int, payload) -> None:
        key = (channel, coin or "")
        row = {
            "ts_local_ns": time.time_ns(),
            "ts_exch_ms": int(ts_exch_ms or 0),
            "channel": channel,
            "coin": coin or "",
            "raw": json.dumps(payload, separators=(",", ":")),
        }
        with self._lock:
            self._buf[key].append(row)
            due = (
                len(self._buf[key]) >= self.flush_rows
                or time.monotonic() - self._last_flush[key] >= self.flush_seconds
            )
            rows = self._buf.pop(key) if due else None
            if due:
                self._last_flush[key] = time.monotonic()
        if rows:
            self._write(key, rows)

    def flush_all(self) -> None:
        with self._lock:
            snapshot = {k: v for k, v in self._buf.items() if v}
            self._buf.clear()
            now = time.monotonic()
            for k in snapshot:
                self._last_flush[k] = now
        for key, rows in snapshot.items():
            self._write(key, rows)

    def _write(self, key: tuple[str, str], rows: list[dict]) -> None:
        channel, coin = key
        # Partiziona sull'ora del PRIMO record del batch: un batch non
        # attraversa mai piu' di flush_seconds, quindi lo sfasamento massimo
        # al confine dell'ora e' irrilevante per le query.
        dt = datetime.fromtimestamp(rows[0]["ts_local_ns"] / 1e9, tz=timezone.utc)
        d = os.path.join(
            self.data_dir, channel, coin or "_global", f"date={dt:%Y-%m-%d}"
        )
        os.makedirs(d, exist_ok=True)
        hour_dir = os.path.join(d, f"hour={dt:%H}")
        os.makedirs(hour_dir, exist_ok=True)

        # Parquet non e' appendable. Scrivere part-file immutabili invece di
        # riscrivere il file dell'ora evita I/O quadratico (con l2Book il
        # rewrite sarebbe centinaia di MB al minuto). DuckDB legge la cartella
        # con una glob senza accorgersi della differenza.
        path = os.path.join(hour_dir, f"part-{rows[0]['ts_local_ns']}.parquet")
        table = pa.Table.from_pylist(rows, schema=SCHEMA)
        pq.write_table(table, path, compression=self.compression)
        self.rows_written += len(rows)
