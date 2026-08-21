"""Da cosa il modello di costo prende i dati, e cosa si rifiuta di prendere.

Questo modulo non calcola costi: carica book e funding dai parquet registrati e
li consegna gia' ripuliti alle strutture di `costs.slippage` e `costs.funding`.
Sta separato apposta — il modello deve poter girare su un book costruito a mano
in un test senza trascinarsi dietro DuckDB.

**Tre vincoli sull'uso dei dati, che qui diventano codice.**

1. *Sola lettura.* Il collector sta scrivendo in `data_dir` mentre questo
   modulo legge. Nessuna query di scrittura, e `temp_directory` di DuckDB
   forzata fuori dalla directory dati (riuso di `catalog.dataset.connect`).

2. *Trade deduplicati per `tid`.* Non c'e' una seconda implementazione della
   dedup: si importa `catalog.trades.dedup_sql`. Le ritrasmissioni dopo una
   riconnessione sono ~0,17% dei trade su mainnet, e due moduli che le contano
   in modo diverso sono il tipo di divergenza che rende inspiegabile un
   risultato.

3. *Finestre inaffidabili escluse dai BUCHI DERIVATI DAI DATI*, non dal
   registro `_gaps.jsonl`. Il registro e' una dichiarazione del collector, e
   fino al 2026-08-14 chiudeva la finestra alla riconnessione della socket
   invece che alla ripresa dei dati: per il periodo precedente sottostima le
   durate, in un caso noto di quasi cento secondi. Qui si riusa
   `catalog.derivedgaps`, che misura l'assenza di righe.

**Cosa significa "ora inaffidabile" per un costo.** Sono due cose diverse a
seconda del canale:

- su `activeAssetCtx`, un buco che attraversa l'ora H significa che l'ultimo
  campione di funding dell'ora potrebbe essere vecchio di minuti rispetto
  all'istante di regolamento. Il rate c'e', ma non e' quello giusto. L'ora
  viene marcata e il funding la conta come non nota (mai come zero).
- su `l2Book`, un buco significa che in quella finestra il campione di
  snapshot manca. Gli snapshot rimasti sono validi in se', ma una statistica
  calcolata su un'ora bucata e' calcolata su un pezzo di ora scelto dalla
  disconnessione, non dal campionamento. Vengono esclusi e contati.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator

from catalog import dataset, derivedgaps, sanity, trades as catalog_trades

from .funding import NS_PER_HOUR, FundingSeries
from .slippage import L2Book

BOOK_CHANNEL = "l2Book"
FUNDING_CHANNEL = "activeAssetCtx"
REST_FUNDING_CHANNEL = "backfill_funding"


def connect(temp_dir: str, memory_limit: str = "1GB", threads: int = 1):
    """Connessione DuckDB con gli stessi vincoli del catalogo.

    Il default di `threads` e' 1 e non e' pigrizia: ogni thread tiene i propri
    buffer di scan, e su questa macchina (2 core) la RAM la sta usando il
    collector. Un esaurimento di memoria qui ha gia' congelato la raccolta per
    82 minuti una volta.
    """
    return dataset.connect(temp_dir, memory_limit=memory_limit, threads=threads)


def discover_coins(data_dir: str, channel: str = BOOK_CHANNEL) -> list[str]:
    """Le coin presenti su disco per un canale. Nessuna lista cablata: se
    domani il collector sottoscrive una quinta coin, il report la trova."""
    return sorted(
        coin for ch, coin in dataset.discover(data_dir)
        if ch == channel and coin != "_global"
    )


def dates_of(data_dir: str, channel: str, coin: str) -> list[str]:
    """Le directory `date=` di una partizione, in ordine.

    Serve a scandire un giorno per volta: un solo glob su 17 giorni tiene in
    memoria i metadati di tutti i parquet della coin contemporaneamente, e il
    picco cresce con la storia raccolta.
    """
    d = os.path.join(data_dir, channel, coin)
    if not os.path.isdir(d):
        return []
    return sorted(e.split("=", 1)[1] for e in os.listdir(d)
                  if e.startswith("date="))


def _day_glob(data_dir: str, channel: str, coin: str, date: str) -> str:
    return os.path.join(data_dir, channel, coin, f"date={date}", "hour=*",
                        "*.parquet")


# ---------------------------------------------------------------------------
# Finestre inaffidabili, derivate dai dati
# ---------------------------------------------------------------------------


def unreliable_hours(con, data_dir: str, partitions: list[tuple[str, str]],
                     p99_multiple: float = derivedgaps.DEFAULT_P99_MULTIPLE,
                     min_gap_s: float = derivedgaps.DEFAULT_MIN_GAP_S,
                     dates: list[str] | None = None,
                     thresholds_out: list | None = None,
                     ) -> dict[tuple[str, str], frozenset[int]]:
    """Ore (indice orario UTC) attraversate da un buco derivato dai dati.

    Riuso integrale di `catalog`: `sanity.build_ordered` ricostruisce l'ordine
    di scrittura, `derivedgaps` calcola la soglia per partizione (multiplo del
    p99 osservato, con pavimento assoluto) e i buchi che la superano. Qui si
    aggiunge solo la conversione da intervallo a insieme di ore, perche' e' la
    granularita' a cui funding e statistiche di book sono aggregati.

    Un buco fra le 10:59 e le 11:02 rende inaffidabili SIA l'ora 10 SIA l'ora
    11: l'estremo finale e' incluso. Marcare in eccesso e' l'errore giusto —
    costa un'ora di dati, mentre l'errore opposto costa una statistica che
    sembra sana.

    `dates` viene passato a `sanity.build_ordered` e limita la lettura a quei
    giorni. Il default resta l'intero storico, e con esso la soglia derivata dal
    p99. Quando invece si legge una finestra, la soglia diventa il PAVIMENTO
    FISSO `min_gap_s`: il p99 di una finestra si calcola sulle stesse righe di
    cui deve giudicare la completezza, quindi sale proprio quando la raccolta e'
    andata peggio e maschera la degradazione che dovrebbe rilevare (misurato:
    `trades/SOL` 82,5 s sullo storico, 109,7 s su tre giorni). Le soglie usate
    escono da `thresholds_out` con `basis = pavimento_fisso`.

    `thresholds_out`, se passata, riceve le soglie effettivamente usate. Esiste
    perche' una soglia e' un numero che cambia il risultato senza comparire da
    nessuna parte: chi restringe i giorni deve poterla leggere, non dedurla.
    """
    sanity.build_ordered(con, data_dir, partitions, dates)
    soglie = derivedgaps.build_thresholds(
        con, p99_multiple=p99_multiple, min_gap_s=min_gap_s,
        fixed_s=None if dates is None else min_gap_s,
    )
    if thresholds_out is not None:
        thresholds_out.extend(soglie)
    derivedgaps.build(con)
    out: dict[tuple[str, str], set[int]] = {p: set() for p in partitions}
    rows = con.execute(
        "SELECT channel, coin, start_ns, end_ns FROM derived_gaps"
    ).fetchall()
    for channel, coin, start_ns, end_ns in rows:
        key = (channel, coin)
        if key not in out:
            continue
        out[key].update(range(start_ns // NS_PER_HOUR,
                              end_ns // NS_PER_HOUR + 1))
    dataset.drop(con, "ts_ordered", "gap_thresholds", "derived_gaps")
    return {k: frozenset(v) for k, v in out.items()}


# ---------------------------------------------------------------------------
# Funding
# ---------------------------------------------------------------------------


def funding_series(con, data_dir: str, coin: str,
                   unreliable: frozenset[int] = frozenset()) -> FundingSeries:
    """Serie oraria da `activeAssetCtx`: ultimo `ctx.funding` di ogni ora.

    L'ora e' derivata da `ts_local_ns` (l'istante in cui il dato e' arrivato),
    non dalla directory `date=`/`hour=`, che e' l'ora in cui il writer ha
    aperto il batch. La traslazione all'ora di regolamento avviene dentro
    `FundingSeries.from_hourly_samples`, in un punto solo.
    """
    samples: dict[int, float] = {}
    for date in dates_of(data_dir, FUNDING_CHANNEL, coin):
        src = dataset.read(_day_glob(data_dir, FUNDING_CHANNEL, coin, date))
        rows = con.execute(
            f"""
            SELECT ts_local_ns // {NS_PER_HOUR} AS hour_idx,
                   arg_max(TRY_CAST(json_extract_string(raw, '$.ctx.funding')
                                    AS DOUBLE), ts_local_ns) AS funding
            FROM {src}
            GROUP BY 1
            HAVING funding IS NOT NULL
            """
        ).fetchall()
        # Un'ora puo' comparire in due giorni diversi solo al confine, e li' il
        # campione piu' recente vince: stessa regola dentro e fra i file.
        for hour_idx, funding in rows:
            samples[hour_idx] = funding
    return FundingSeries.from_hourly_samples(coin, samples, unreliable)


def rest_funding_series(con, data_dir: str, coin: str) -> FundingSeries:
    """Serie di controllo dal dump REST `fundingHistory`.

    Il timestamp REST e' gia' l'istante di regolamento, quindi non serve
    nessuna traslazione: e' proprio questo che rende il confronto con la serie
    derivata da `activeAssetCtx` una verifica e non una tautologia.

    Il backfill rigira a ogni riconnessione con 48 h di lookback, quindi lo
    stesso regolamento compare in decine di dump. Si tiene un valore per
    (coin, ora) e si conta se due dump si contraddicono.
    """
    src = dataset.read(dataset.glob_channel(data_dir, REST_FUNDING_CHANNEL))
    rows = con.execute(
        f"""
        SELECT time_ms // 3600000 AS hour_idx, any_value(rate) AS rate
        FROM (
            SELECT json_extract_string(e, '$.coin') AS coin,
                   TRY_CAST(json_extract_string(e, '$.time') AS BIGINT) AS time_ms,
                   TRY_CAST(json_extract_string(e, '$.fundingRate') AS DOUBLE) AS rate
            FROM (SELECT unnest(json_extract(raw, '$[*]')) AS e FROM {src})
        )
        WHERE coin = ? AND time_ms IS NOT NULL AND rate IS NOT NULL
        GROUP BY 1
        """,
        [coin],
    ).fetchall()
    return FundingSeries.from_settlements(coin, dict(rows))


# ---------------------------------------------------------------------------
# Book
# ---------------------------------------------------------------------------


def sample_books(con, data_dir: str, coin: str, every_s: int = 60,
                 unreliable: frozenset[int] = frozenset(),
                 dates: list[str] | None = None) -> Iterator[L2Book]:
    """Uno snapshot ogni `every_s` secondi, il primo di ogni intervallo.

    **Perche' campionare.** Su 17 giorni gli snapshot l2Book sono milioni per
    coin: camminare il book su tutti richiederebbe ore di CPU su una macchina
    che deve soprattutto non disturbare il collector. Un campione regolare a
    un minuto da' ~24.000 osservazioni per coin, e su un campione cosi' la
    mediana di una distribuzione di spread e' stabile alla terza cifra. Il
    campionamento e' REGOLARE nel tempo e non casuale: pesa allo stesso modo
    l'ora di punta e la notte, che e' quello che serve a una statistica di
    costo.

    Il primo snapshot di ogni intervallo e non uno a caso: e' deterministico,
    quindi due esecuzioni del report producono lo stesso numero.

    Gli snapshot che cadono in un'ora inaffidabile non vengono restituiti. Chi
    vuole contarli usa `sample_book_stats`.
    """
    bucket_ns = every_s * 1_000_000_000
    for date in (dates if dates is not None
                 else dates_of(data_dir, BOOK_CHANNEL, coin)):
        src = dataset.read(_day_glob(data_dir, BOOK_CHANNEL, coin, date))
        rows = con.execute(
            f"""
            SELECT min(ts_local_ns)                       AS ts_local_ns,
                   arg_min(ts_exch_ms, ts_local_ns)       AS ts_exch_ms,
                   arg_min(raw, ts_local_ns)              AS raw
            FROM {src}
            GROUP BY ts_local_ns // {bucket_ns}
            ORDER BY 1
            """
        ).fetchall()
        for ts_local_ns, ts_exch_ms, raw in rows:
            if ts_local_ns // NS_PER_HOUR in unreliable:
                continue
            book = L2Book.try_from_payload(json.loads(raw), ts_local_ns,
                                           ts_exch_ms or 0)
            if book is not None:
                yield book


def sample_book_stats(con, data_dir: str, coin: str, every_s: int = 60,
                      unreliable: frozenset[int] = frozenset()) -> dict:
    """Quanti snapshot il campionamento produce, quanti ne scarta e perche'.

    Esiste per la stessa ragione per cui `catalog` stampa le ore marcate: un
    numero calcolato su meta' del campione non deve poter essere letto come se
    fosse calcolato su tutto.
    """
    bucket_ns = every_s * 1_000_000_000
    n_sampled = n_unreliable = n_invalid = 0
    for date in dates_of(data_dir, BOOK_CHANNEL, coin):
        src = dataset.read(_day_glob(data_dir, BOOK_CHANNEL, coin, date))
        rows = con.execute(
            f"""
            SELECT min(ts_local_ns) AS ts, arg_min(raw, ts_local_ns) AS raw
            FROM {src} GROUP BY ts_local_ns // {bucket_ns}
            """
        ).fetchall()
        for ts, raw in rows:
            n_sampled += 1
            if ts // NS_PER_HOUR in unreliable:
                n_unreliable += 1
            elif L2Book.try_from_payload(json.loads(raw), ts) is None:
                n_invalid += 1
    return {
        "coin": coin,
        "n_sampled": n_sampled,
        "n_unreliable": n_unreliable,
        "n_invalid": n_invalid,
        "n_used": n_sampled - n_unreliable - n_invalid,
    }


# ---------------------------------------------------------------------------
# Trade (deduplicati per tid, dalla funzione del catalogo)
# ---------------------------------------------------------------------------


def trade_notional_stats(con, data_dir: str, coin: str,
                         dates: list[str] | None = None) -> dict:
    """Distribuzione del notional dei trade reali, per contestualizzare le size.

    Non entra nel calcolo del costo: serve a rispondere alla domanda "100 $ e'
    tanto o poco su questa coin". La risposta cambia il senso di tutto il
    report — se il trade mediano vale 200 $, un ordine da 2.000 $ non e'
    piccolo come sembra guardando solo la profondita' del primo livello.

    Legge dalla dedup del catalogo (`catalog.trades.dedup_sql`): le
    ritrasmissioni dopo una riconnessione conterebbero due volte lo stesso
    scambio.
    """
    sub = catalog_trades.dedup_sql(data_dir, coin)
    where = ""
    if dates:
        days = ", ".join(dataset.sql_str(d) for d in dates)
        where = f"WHERE strftime(epoch_ms(time_ms), '%Y-%m-%d') IN ({days})"
    cols = ["coin", "n_trades", "notional_p50", "notional_p90", "notional_p99",
            "notional_max", "notional_sum", "n_buy", "n_sell"]
    row = con.execute(
        f"""
        SELECT '{coin}' AS coin,
               count(*)                          AS n,
               median(px * sz)                   AS p50,
               quantile_cont(px * sz, 0.90)      AS p90,
               quantile_cont(px * sz, 0.99)      AS p99,
               max(px * sz)                      AS mx,
               sum(px * sz)                      AS tot,
               count(*) FILTER (WHERE side = 'B') AS n_buy,
               count(*) FILTER (WHERE side = 'A') AS n_sell
        FROM {sub} {where}
        """
    ).fetchone()
    return dict(zip(cols, row))
