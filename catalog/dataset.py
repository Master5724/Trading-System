"""Scoperta delle partizioni e connessione DuckDB in sola lettura.

Nessun percorso e' cablato: il catalogo scopre canali e coin dal layout del
writer (`<data_dir>/<channel>/<coin>/date=.../hour=.../part-*.parquet`), quindi
gira su qualunque data_dir — testnet, mainnet, una copia, una directory di test.

Il vincolo di sola lettura non e' un commento gentile: il collector sta
scrivendo in quella directory mentre il catalogo legge. Qui viene reso
esplicito in tre punti — nessuna query di scrittura, temp_directory di DuckDB
forzata fuori da data_dir, e un guard sulla out_dir.
"""

from __future__ import annotations

import os

import duckdb

# Canali che NON sono stream continui: il collector li scrive una volta per
# riconnessione (dump REST). Contarne le righe per ora e' legittimo, ma la
# mediana oraria non significa niente, quindi il report li tiene separati.
BACKFILL_CHANNELS = frozenset({"backfill_candle", "backfill_funding"})

GAPS_FILENAME = "_gaps.jsonl"


class DataDirError(Exception):
    pass


def assert_read_only_layout(data_dir: str, out_dir: str) -> None:
    """Rifiuta di partire se gli output finirebbero dentro la directory dati.

    E' l'unico modo in cui questo programma potrebbe scrivere in `data_dir`,
    quindi vale la pena spendere quattro righe per renderlo impossibile invece
    che improbabile.
    """
    d = os.path.realpath(data_dir)
    o = os.path.realpath(out_dir)
    if o == d or o.startswith(d + os.sep):
        raise DataDirError(
            f"out_dir ({o}) e' dentro data_dir ({d}): il catalogo e' in sola "
            f"lettura sui dati, scegli una directory di output separata"
        )


def discover(data_dir: str) -> list[tuple[str, str]]:
    """Ritorna le coppie (channel, coin) presenti su disco, ordinate.

    `coin` e' il nome della directory: `_global` per i canali senza coin.
    Una partizione conta come esistente solo se contiene almeno una directory
    `date=`; una cartella vuota lasciata da un run precedente non deve
    comparire nel report come canale a zero righe.
    """
    if not os.path.isdir(data_dir):
        raise DataDirError(f"data_dir non esiste: {data_dir}")
    out: list[tuple[str, str]] = []
    for channel in sorted(os.listdir(data_dir)):
        ch_dir = os.path.join(data_dir, channel)
        if not os.path.isdir(ch_dir):
            continue
        for coin in sorted(os.listdir(ch_dir)):
            coin_dir = os.path.join(ch_dir, coin)
            if not os.path.isdir(coin_dir):
                continue
            if any(e.startswith("date=") for e in os.listdir(coin_dir)):
                out.append((channel, coin))
    if not out:
        raise DataDirError(f"nessuna partizione parquet trovata sotto {data_dir}")
    return out


def glob_all(data_dir: str) -> str:
    return os.path.join(data_dir, "*", "*", "date=*", "hour=*", "*.parquet")


def glob_channel(data_dir: str, channel: str) -> str:
    return os.path.join(data_dir, channel, "*", "date=*", "hour=*", "*.parquet")


def glob_partition(data_dir: str, channel: str, coin: str) -> str:
    return os.path.join(data_dir, channel, coin, "date=*", "hour=*", "*.parquet")


def sql_str(value: str) -> str:
    """Letterale SQL. I percorsi arrivano dalla CLI, non da input non fidato,
    ma un apice in un nome di directory non deve produrre SQL rotto."""
    return "'" + value.replace("'", "''") + "'"


def connect(temp_dir: str, memory_limit: str | None = None) -> duckdb.DuckDBPyConnection:
    """Connessione in-memory. `temp_dir` sta fuori da data_dir per costruzione:
    se DuckDB deve fare spill su disco non deve toccare i dati di produzione."""
    os.makedirs(temp_dir, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"SET temp_directory = {sql_str(temp_dir)}")
    if memory_limit:
        con.execute(f"SET memory_limit = {sql_str(memory_limit)}")
    return con


def read(glob: str, **opts) -> str:
    """Espressione `read_parquet` con le opzioni fissate una volta sola.

    `hive_partitioning=false`: le directory `date=`/`hour=` sono l'ora in cui il
    writer ha aperto il batch, non l'ora della riga. Il catalogo deriva l'ora
    da `ts_local_ns`; lasciar comparire le colonne hive inviterebbe a usare
    quella sbagliata.
    """
    parts = [sql_str(glob), "hive_partitioning=false"]
    for k, v in opts.items():
        parts.append(f"{k}={'true' if v is True else 'false' if v is False else v}")
    return f"read_parquet({', '.join(parts)})"
