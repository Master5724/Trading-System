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

# Canali a cadenza fissa: l'exchange li pubblica a intervalli regolari
# indipendentemente da cosa fa il mercato, quindi il conteggio orario delle
# righe e' pressoche' costante e uno scostamento del 10% dalla mediana e'
# davvero un'anomalia di raccolta. Sugli altri canali (`trades`, `candle`) il
# volume e' guidato dall'attivita': fra l'ora di un rilascio macro e le 4 del
# mattino di domenica il numero di trade cambia di multipli, e la stessa soglia
# produce centinaia di ore marcate che non hanno niente di anomalo.
#
# L'elenco e' un default, non una verita': `--fixed-rate-channels` lo
# sovrascrive, perche' quali canali siano a cadenza fissa dipende da cosa il
# collector ha sottoscritto, non da questo file.
DEFAULT_FIXED_RATE_CHANNELS = frozenset({"activeAssetCtx", "allMids", "l2Book"})

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


def globs_for_dates(data_dir: str, channel: str, coin: str,
                    dates: list[str]) -> list[str]:
    """Un glob per ogni directory `date=` fra quelle chieste che esiste davvero.

    Le directory inesistenti si scartano qui: `read_parquet` su un glob senza
    file solleva, e un giorno mancante ai bordi di una finestra e' normale — il
    primo giorno di raccolta, o domani. Chi legge una finestra deve ottenere
    zero righe da un giorno che non c'e', non un errore.
    """
    return [
        os.path.join(data_dir, channel, coin, f"date={d}", "hour=*", "*.parquet")
        for d in dates
        if os.path.isdir(os.path.join(data_dir, channel, coin, f"date={d}"))
    ]


def read_many(globs: list[str], columns: str, **opts) -> str:
    """`UNION ALL` di `read_parquet` sui glob dati, proiettato su `columns`.

    Serve a restringere una lettura ad alcuni giorni tenendo UNA sola
    sotto-query: le funzioni che ci lavorano sopra (l'ordinamento di
    `sanity`, la dedup di `trades`) usano finestre analitiche che devono
    vedere l'intera partizione insieme. Unire dopo la finestra darebbe deltas
    nulli a ogni mezzanotte, cioe' esattamente i buchi che si stanno cercando.
    """
    if not globs:
        raise ValueError(
            "read_many senza glob: il caso 'nessun giorno utile' ha bisogno di "
            "una sotto-query TIPIZZATA, e i tipi li conosce chi chiama"
        )
    return "(" + " UNION ALL ".join(
        f"SELECT {columns} FROM {read(g, **opts)}" for g in globs
    ) + ")"


def sql_str(value: str) -> str:
    """Letterale SQL. I percorsi arrivano dalla CLI, non da input non fidato,
    ma un apice in un nome di directory non deve produrre SQL rotto."""
    return "'" + value.replace("'", "''") + "'"


def connect(
    temp_dir: str,
    memory_limit: str | None = None,
    threads: int | None = None,
) -> duckdb.DuckDBPyConnection:
    """Connessione in-memory. `temp_dir` sta fuori da data_dir per costruzione:
    se DuckDB deve fare spill su disco non deve toccare i dati di produzione.

    `threads` non e' una manopola di prestazioni, e' una manopola di memoria:
    ogni thread tiene i propri buffer di scan e le proprie tabelle hash, quindi
    il picco cresce col parallelismo mentre `memory_limit` copre solo la parte
    che DuckDB sa contabilizzare. Su questa macchina (2 core, e il collector
    che gira sopra) il default e' 1: il catalogo puo' permettersi di essere
    lento, non di far uccidere il collector dall'OOM killer.
    """
    os.makedirs(temp_dir, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"SET temp_directory = {sql_str(temp_dir)}")
    # DuckDB tiene in RAM i file esterni gia' letti. Su ~317k parquet per 2.4 GB
    # quella cache cresce fino a superare il tetto del cgroup e il processo
    # viene ucciso a meta' scansione: misurato, non temuto — con la cache
    # attiva il catalogo muore per OOM a 2 GB, senza sta in 300 MB. La cache
    # del kernel copre gia' le riletture, e a costo zero per il collector.
    con.execute("SET enable_external_file_cache = false")
    if memory_limit:
        con.execute(f"SET memory_limit = {sql_str(memory_limit)}")
    if threads:
        con.execute(f"SET threads = {int(threads)}")
    return con


def accumulate(con, table: str, select_sql: str) -> None:
    """Appende il risultato di `select_sql` a `table`, creandola alla prima
    chiamata.

    E' il mattone del passaggio da "una query su tutti i file" a "una query per
    partizione". Serve perche' ogni metrica del catalogo e' gia' calcolata per
    (canale, coin): lo scan globale non aggiungeva niente al risultato, ma
    teneva in memoria contemporaneamente i metadati di tutti i parquet e tutti
    gli hash set dei COUNT(DISTINCT). Su 300k file quel picco arriva a diversi
    GB, e su questa macchina la RAM la sta usando il collector.
    """
    exists = con.execute(
        "SELECT count(*) FROM duckdb_tables() WHERE table_name = ?", [table]
    ).fetchone()[0]
    if exists:
        con.execute(f"INSERT INTO {table} {select_sql}")
    else:
        con.execute(f"CREATE TABLE {table} AS {select_sql}")


def drop(con, *tables: str) -> None:
    for t in tables:
        con.execute(f"DROP TABLE IF EXISTS {t}")


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
