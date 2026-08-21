"""Lettura del canale `trades`: esplosione del payload e dedup per `tid`.

**Perche' esiste.** Il collector scrive i messaggi del WebSocket cosi' come
arrivano: una riga di parquet contiene un array JSON di trade, non un trade.
Dopo una riconnessione l'exchange rimanda gli ultimi scambi, quindi lo stesso
`tid` — l'identificatore dell'esecuzione — arriva due volte. Sui dati mainnet
raccolti finora e' circa lo 0,2% dei trade. Non e' un guasto del collector: e'
il comportamento del server, e i dati su disco sono la registrazione onesta di
cio' che e' arrivato. Riscriverli sarebbe perdere l'informazione su cosa il
processo ha davvero visto.

**Quindi la dedup avviene in lettura.** I file non si toccano (CLAUDE.md: sola
lettura sui dati raccolti). Questo modulo e' il posto unico da cui il
backtester leggera' i trade, cosi' come `costs/` sara' il posto unico del
modello di costo: un giorno in cui il backtest deduplica in un modo e il
catalogo ne conta un altro numero e' il tipo di divergenza che rende
inspiegabile un risultato.

**Quale copia si tiene.** La prima arrivata (`ts_local_ns` minimo). E' quella
che un sistema live avrebbe visto per primo, ed e' l'unica scelta che non
introduce un ritardo fittizio: tenere la ritrasmissione significherebbe
datare il trade al momento della riconnessione, cioe' fino a decine di secondi
dopo il fatto. Il tie-break su `hash` rende l'esito deterministico anche se
due consegne condividono lo stesso `ts_local_ns` (stesso messaggio, stesso
batch).

**Assunzione dichiarata.** `px` e `sz` arrivano come stringhe decimali e qui
diventano DOUBLE. Per i tick di Hyperliquid (5 cifre significative sul prezzo)
la conversione e' esatta, ma resta un'assunzione: se un giorno servisse
aritmetica esatta, `px_raw`/`sz_raw` conservano il testo originale.

**Su quanti giorni si deduplica.** `dates=None` legge l'intera partizione della
coin, ed e' il default perche' e' la risposta certa. Ma il costo cresce con lo
storico raccolto e non con cio' che serve a chi legge: su questa macchina un
esaurimento di memoria ha gia' congelato il collector per 82 minuti, quindi chi
lavora su una finestra passa l'elenco dei giorni che gli interessano piu' un
margine.

Quanto margine e' un fatto misurato, non una scelta: sull'intero mainnet
raccolto la distanza massima fra due consegne dello stesso `tid` e' **264 s**
(SOL; BTC 145 s, ETH 224 s, HYPE 195 s, su 12,1 milioni di consegne e 17.017
`tid` consegnati piu' di una volta). Un margine di un giorno per lato e' 327
volte quel massimo. Se un giorno la distanza superasse il margine, l'effetto
sarebbe di tenere la ritrasmissione invece della consegna originale per i
`tid` a cavallo del bordo — non un duplicato in piu', ma un trade datato al
momento della riconnessione.
"""

from __future__ import annotations

from .dataset import (
    accumulate,
    drop,
    glob_partition,
    globs_for_dates,
    read,
    read_many,
)

CHANNEL = "trades"

# Campi del payload di un trade Hyperliquid. `users` (le due controparti) non
# viene estratto: non serve a nessun calcolo del backtester e raddoppierebbe
# la larghezza della tabella.
TRADE_SELECT = """
    CAST(json_extract_string(j, '$.tid')  AS BIGINT)  AS tid,
    json_extract_string(j, '$.coin')                  AS coin,
    json_extract_string(j, '$.side')                  AS side,
    CAST(json_extract_string(j, '$.px')   AS DOUBLE)  AS px,
    CAST(json_extract_string(j, '$.sz')   AS DOUBLE)  AS sz,
    json_extract_string(j, '$.px')                    AS px_raw,
    json_extract_string(j, '$.sz')                    AS sz_raw,
    CAST(json_extract_string(j, '$.time') AS BIGINT)  AS time_ms,
    json_extract_string(j, '$.hash')                  AS tx_hash,
    ts_local_ns
"""

# Identita' del contenuto di un trade, `tid` escluso. Serve a distinguere una
# ritrasmissione benigna (stesso tid, stesso contenuto) da una contraddizione
# (stesso tid, contenuto diverso), che sarebbe un dato da non fidarsi.
_CONTENT_HASH = "hash(side, px_raw, sz_raw, time_ms, tx_hash)"


def _source(data_dir: str, coin: str, dates: list[str] | None) -> str:
    """L'espressione di lettura: tutta la partizione, o i soli giorni chiesti.

    I giorni che non esistono su disco vengono scartati qui: `read_parquet` su
    un glob senza file solleva, e un giorno mancante ai bordi della finestra e'
    normale (il primo giorno di raccolta, o domani).
    """
    if dates is None:
        return read(glob_partition(data_dir, CHANNEL, coin))
    globs = globs_for_dates(data_dir, CHANNEL, coin, dates)
    if not globs:
        # Nessun giorno utile: una tabella vuota con lo stesso schema, cosi'
        # che chi legge riceva zero righe invece di un errore di glob.
        return ("(SELECT NULL::BIGINT AS ts_local_ns, NULL::VARCHAR AS raw "
                "WHERE false)")
    return read_many(globs, "ts_local_ns, raw")


def exploded_sql(data_dir: str, coin: str = "*",
                 dates: list[str] | None = None) -> str:
    """Sotto-query con una riga per trade CONSEGNATO (duplicati compresi).

    E' la vista grezza: serve a contare le ritrasmissioni. Chi vuole i trade
    per calcolarci sopra qualcosa usa `dedup_sql`. `dates` limita la lettura ai
    giorni indicati: vedi la docstring del modulo per quanto margine serve.
    """
    src = _source(data_dir, coin, dates)
    return f"""(
        SELECT {TRADE_SELECT}
        FROM (SELECT ts_local_ns, unnest(json_extract(raw, '$[*]')) AS j FROM {src})
    )"""


def dedup_sql(data_dir: str, coin: str = "*",
              dates: list[str] | None = None) -> str:
    """Sotto-query con una riga per `tid`: la prima consegna osservata.

    Usabile direttamente in un FROM:

        con.execute(f"SELECT count(*) FROM {dedup_sql(data_dir, 'BTC')}")

    Ordinata per `ts_local_ns` in nessun punto: l'ordinamento costa e chi
    consuma sa quale gli serve. Il backtester ordinera' per `time_ms` (ordine
    dell'exchange) o per `ts_local_ns` (ordine in cui il dato era
    disponibile) a seconda della domanda, e la differenza fra i due e'
    esattamente il look-ahead che CLAUDE.md vieta di ignorare.
    """
    return f"""(
        SELECT * FROM {exploded_sql(data_dir, coin, dates)}
        QUALIFY row_number() OVER (
            PARTITION BY tid ORDER BY ts_local_ns, tx_hash
        ) = 1
    )"""


def materialize_dedup(
    con, data_dir: str, coins: list[str], table: str = "trades_dedup"
) -> int:
    """Materializza i trade deduplicati in una tabella DuckDB.

    Una coin per volta: lo scan globale terrebbe in memoria l'hash set di
    tutti i `tid` di tutte le coin contemporaneamente, e su questa macchina la
    RAM la sta usando il collector.
    """
    drop(con, table)
    for coin in coins:
        accumulate(con, table, f"SELECT * FROM {dedup_sql(data_dir, coin)}")
    if not coins:
        return 0
    return con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def duplicate_stats(con, data_dir: str, coins: list[str]) -> list[dict]:
    """Quanti `tid` duplicati esistono, per coin.

    Tre numeri distinti perche' significano cose diverse:
    - `n_dup_tid`: consegne in eccesso. Attese dopo una riconnessione.
    - `n_tid_ripetuti`: quanti `tid` distinti sono stati consegnati piu' di
      una volta. Con `n_dup_tid` dice se le ritrasmissioni sono concentrate
      su pochi trade o sparse.
    - `n_tid_contraddittori`: stesso `tid`, contenuto diverso. Deve essere 0.
      Se non lo e', la dedup sta scegliendo fra due verita' e il numero che il
      backtester leggera' dipende da quale copia e' arrivata prima.
    """
    if not coins:
        return []
    tmp = "_trade_tid_stats"
    drop(con, tmp)
    for coin in coins:
        accumulate(
            con,
            tmp,
            f"""
            WITH per_tid AS (
                SELECT coin, tid,
                       count(*)                        AS n_deliveries,
                       count(DISTINCT {_CONTENT_HASH}) AS n_versions
                FROM {exploded_sql(data_dir, coin)}
                GROUP BY 1, 2
            )
            SELECT coin,
                   sum(n_deliveries)                              AS n_trades,
                   sum(n_deliveries) - count(*)                   AS n_dup_tid,
                   count(*)                                       AS n_distinct_tid,
                   count(*) FILTER (WHERE n_deliveries > 1)       AS n_tid_ripetuti,
                   count(*) FILTER (WHERE n_versions > 1)         AS n_tid_contraddittori
            FROM per_tid
            GROUP BY 1
            """,
        )
    cols = ["coin", "n_trades", "n_distinct_tid", "n_dup_tid", "n_tid_ripetuti",
            "n_tid_contraddittori"]
    rows = con.execute(
        f"""
        SELECT coin, sum(n_trades), sum(n_distinct_tid), sum(n_dup_tid),
               sum(n_tid_ripetuti), sum(n_tid_contraddittori)
        FROM {tmp} GROUP BY 1 ORDER BY 1
        """
    ).fetchall()
    drop(con, tmp)
    return [dict(zip(cols, r)) for r in rows]
