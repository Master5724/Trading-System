"""Controlli di sanita' sul contenuto dei parquet.

Questi sono i controlli che dicono se i dati hanno senso, non solo se
esistono. La domanda a cui rispondono e': se domani un backtest gira su questi
file e produce un numero, quel numero e' credibile?

Convenzione: ogni funzione ritorna dati grezzi (liste di dict), non testo.
L'interpretazione sta nel report, cosi' gli stessi numeri sono riusabili da
altro codice senza doverli riestrarre da una stringa formattata.
"""

from __future__ import annotations

from .dataset import accumulate, drop, glob_channel, glob_partition, read

# Percentili usati ovunque nel modulo. Estremi inclusi: e' la coda che dice se
# un dato e' inutilizzabile, la mediana da sola non lo direbbe mai.
QUANTILES = [0.001, 0.01, 0.05, 0.25, 0.50, 0.75, 0.95, 0.99, 0.999]

# Soglia oltre la quale uno scostamento fra il mid del book e allMids viene
# elencato come anomalo. Non e' un parametro tarato: il report pubblica
# comunque la distribuzione completa, questa soglia serve solo a decidere cosa
# stampare per esteso. 10 bps sono diversi multipli dello spread tipico dei
# major, quindi non e' spiegabile con un tick di disallineamento.
COHERENCE_ANOMALY_BPS = 10.0

# Distanza massima ammessa fra una riga l2Book e la riga allMids con cui viene
# confrontata. Oltre, il confronto misurerebbe il movimento del prezzo nel
# frattempo invece della coerenza delle due fonti.
COHERENCE_MAX_LAG_NS = 2_000_000_000


def _rows(con, sql: str, cols: list[str]) -> list[dict]:
    return [dict(zip(cols, r)) for r in con.execute(sql).fetchall()]


def per_partizione(con, data_dir: str, partitions, tmp: str, cols: list[str],
                   sql_for) -> list[dict]:
    """Esegue `sql_for(src)` una partizione per volta e concatena i risultati.

    Tutte le metriche di questo modulo sono gia' raggruppate per (canale,
    coin), quindi lo scan globale non aggiungeva niente al risultato: teneva
    solo in memoria, contemporaneamente, gli hash set di tutti i COUNT(DISTINCT)
    di tutte le partizioni. Su questi dati (317k file, 2.4 GB) quel picco
    arrivava a superare il tetto di 2 GB del cgroup e il processo veniva ucciso
    a meta' scansione — misurato, non temuto. Il collector gira sulla stessa
    macchina: e' gia' successo che un esaurimento di memoria lo congelasse per
    82 minuti, ed e' il buco piu' lungo dell'intera raccolta.
    """
    drop(con, tmp)
    for channel, coin in partitions:
        accumulate(con, tmp, sql_for(read(glob_partition(data_dir, channel, coin))))
    rows = _rows(con, f"SELECT * FROM {tmp} ORDER BY channel, coin", cols)
    drop(con, tmp)
    return rows


def duplicates(con, data_dir: str, partitions) -> list[dict]:
    """Duplicati esatti, payload ripetuti, timestamp ripetuti.

    Tre livelli distinti perche' significano cose diverse:
    - `n_exact_dup`: stessa riga identica (ts locale compreso). Non dovrebbe
      esistere: il writer scrive ogni messaggio una volta sola.
    - `n_payload_dup`: stesso payload a timestamp locali diversi. Legittimo se
      l'exchange ripubblica uno stato invariato (activeAssetCtx, candle in
      aggiornamento), sospetto altrove.
    - `n_dup_ts_exch`: stesso timestamp exchange su piu' righe. Atteso per
      `candle` (la stessa barra viene ripubblicata a ogni tick, con `t` fisso),
      da guardare per gli altri.

    Il confronto usa un hash a 64 bit invece del testo integrale: su ~10^7
    righe la probabilita' di collisione e' dell'ordine di 10^-6, quindi un
    duplicato riportato e' vero e un conteggio puo' al piu' sovrastimare di
    uno-due su decine di milioni. Confrontare le stringhe intere costerebbe
    l'ordine dei GB di memoria per il DISTINCT.

    Una query per partizione, non una su tutti i file: vedi `per_partizione`.
    """
    cols = ["channel", "coin", "n_rows", "n_exact_dup", "n_payload_dup",
            "n_dup_ts_local", "n_dup_ts_exch"]
    return per_partizione(
        con, data_dir, partitions, "_dup_raw", cols,
        lambda src: f"""
        SELECT channel,
               CASE WHEN coin = '' THEN '_global' ELSE coin END AS coin,
               count(*)                                                  AS n_rows,
               count(*) - count(DISTINCT hash(ts_local_ns, ts_exch_ms, raw))
                                                                         AS n_exact_dup,
               count(*) - count(DISTINCT hash(raw))                      AS n_payload_dup,
               count(*) - count(DISTINCT ts_local_ns)                    AS n_dup_ts_local,
               count(*) FILTER (WHERE ts_exch_ms <> 0)
                 - count(DISTINCT ts_exch_ms) FILTER (WHERE ts_exch_ms <> 0)
                                                                         AS n_dup_ts_exch
        FROM {src}
        GROUP BY 1, 2
        """,
    )


def build_ordered(con, data_dir: str, partitions) -> None:
    """Materializza le righe nell'ordine in cui sono state scritte.

    L'ordine di scrittura e' (part-file, riga dentro il file): il writer
    appende in ordine di arrivo e nomina il file con il ts della prima riga
    del batch, quindi ordinare per `part_ns` ricostruisce l'ordine di arrivo
    senza assumere che i timestamp siano gia' ordinati — che e' proprio la
    cosa che stiamo verificando.
    """
    drop(con, "ts_ordered")
    for channel, coin in partitions:
        src = read(glob_partition(data_dir, channel, coin),
                   filename=True, file_row_number=True)
        accumulate(
            con,
            "ts_ordered",
            rf"""
        WITH r AS (
            SELECT channel,
                   CASE WHEN coin = '' THEN '_global' ELSE coin END AS coin,
                   ts_local_ns,
                   filename,
                   file_row_number AS rn,
                   CAST(regexp_extract(filename, 'part-(\d+)\.parquet$', 1) AS BIGINT)
                       AS part_ns
            FROM {src}
        )
        SELECT channel, coin, ts_local_ns, filename, rn, part_ns,
               ts_local_ns - lag(ts_local_ns)
                   OVER (PARTITION BY channel, coin ORDER BY part_ns, rn) AS delta_ns
        FROM r
        """,
        )


def monotonicity(con) -> list[dict]:
    return _rows(
        con,
        """
        SELECT channel, coin,
               count(*) FILTER (WHERE delta_ns IS NOT NULL)  AS n_pairs,
               count(*) FILTER (WHERE delta_ns < 0)          AS n_decreasing,
               count(*) FILTER (WHERE delta_ns = 0)          AS n_equal,
               min(delta_ns) FILTER (WHERE delta_ns < 0)     AS worst_backstep_ns
        FROM ts_ordered GROUP BY 1, 2 ORDER BY 1, 2
        """,
        ["channel", "coin", "n_pairs", "n_decreasing", "n_equal", "worst_backstep_ns"],
    )


def monotonicity_breaks(con, limit: int) -> list[dict]:
    return _rows(
        con,
        f"""
        SELECT channel, coin,
               epoch_ms(ts_local_ns // 1000000) AS at_utc,
               ts_local_ns, delta_ns, rn, filename
        FROM ts_ordered WHERE delta_ns < 0
        ORDER BY delta_ns LIMIT {int(limit)}
        """,
        ["channel", "coin", "at_utc", "ts_local_ns", "delta_ns", "rn", "filename"],
    )


def interarrival(con) -> list[dict]:
    """Distanza fra messaggi consecutivi. Dice se un canale sta arrivando alla
    frequenza che ci si aspetta, e quanto sono lunghe le pause peggiori — che
    e' l'unica misura di "buco" disponibile per i canali che l'exchange non
    ripubblica mai."""
    return _rows(
        con,
        f"""
        SELECT channel, coin,
               count(*) FILTER (WHERE delta_ns > 0)              AS n,
               min(delta_ns) FILTER (WHERE delta_ns > 0) / 1e9   AS min_s,
               quantile_cont(delta_ns / 1e9, {QUANTILES}) FILTER (WHERE delta_ns > 0)
                                                                 AS q,
               max(delta_ns) / 1e9                               AS max_s,
               avg(delta_ns) / 1e9                               AS mean_s
        FROM ts_ordered GROUP BY 1, 2 ORDER BY 1, 2
        """,
        ["channel", "coin", "n", "min_s", "q", "max_s", "mean_s"],
    )


def exch_ts_zero(con) -> list[dict]:
    """Quante righe hanno `ts_exch_ms = 0`, per canale. Non viene corretto:
    per allMids e activeAssetCtx il payload non contiene nessun timestamp,
    quindi 0 e' il valore giusto e l'unico riferimento temporale e' locale."""
    return _rows(
        con,
        """
        SELECT channel, coin, sum(n_rows) AS n_rows,
               sum(n_exch_ts_zero) AS n_zero,
               sum(n_exch_ts_zero) * 100.0 / nullif(sum(n_rows), 0) AS pct_zero,
               sum(n_exch_ts_set) AS n_set,
               sum(n_exch_ts_distinct) AS n_distinct_set
        FROM hourly GROUP BY 1, 2 ORDER BY 1, 2
        """,
        ["channel", "coin", "n_rows", "n_zero", "pct_zero", "n_set", "n_distinct_set"],
    )


def latency(con, data_dir: str, partitions) -> list[dict]:
    """Distribuzione di (ts_local_ms - ts_exch_ms) per i canali che espongono
    il timestamp dell'exchange.

    Attenzione a leggerla: per `candle` il timestamp e' l'APERTURA della barra,
    non l'istante di invio, quindi la "latenza" cresce fino a 60 s dentro il
    minuto ed e' una misura del tempo trascorso nella barra, non della rete.
    Per `backfill_*` e' il tempo della richiesta REST. Solo `l2Book` e `trades`
    misurano davvero un ritardo exchange -> host.
    """
    cols = ["channel", "coin", "n", "min_ms", "q", "max_ms", "mean_ms", "n_negative"]
    return per_partizione(
        con, data_dir, partitions, "_lat_raw", cols,
        lambda src: f"""
        SELECT channel,
               CASE WHEN coin = '' THEN '_global' ELSE coin END AS coin,
               count(*)                                  AS n,
               min(lat)                                  AS min_ms,
               quantile_cont(lat, {QUANTILES})           AS q,
               max(lat)                                  AS max_ms,
               avg(lat)                                  AS mean_ms,
               count(*) FILTER (WHERE lat < 0)           AS n_negative
        FROM (
            SELECT channel, coin, ts_local_ns / 1e6 - ts_exch_ms AS lat
            FROM {src} WHERE ts_exch_ms <> 0
        )
        GROUP BY 1, 2
        """,
    )


def build_all_mids(con, data_dir: str, coins: list[str]) -> None:
    """Estrae da allMids il mid delle sole coin sottoscritte.

    Una riga di allMids contiene qualche centinaio di asset (perp per nome,
    spot come `@N`): estrarre in un colpo solo la lista dei percorsi che
    servono evita di rileggere ~200 MB di JSON una volta per coin.
    """
    paths = "[" + ", ".join("'$.mids." + c + "'" for c in coins) + "]"
    names = "[" + ", ".join("'" + c + "'" for c in coins) + "]"
    src = read(glob_channel(data_dir, "allMids"))
    con.execute(
        f"""
        CREATE OR REPLACE TABLE all_mids AS
        SELECT ts_local_ns, {names}[i] AS coin, TRY_CAST(pxs[i] AS DOUBLE) AS mid
        FROM (
            SELECT ts_local_ns, json_extract_string(raw, {paths}) AS pxs
            FROM {src}
        ), generate_series(1, {len(coins)}) g(i)
        WHERE pxs[i] IS NOT NULL
        """
    )


def all_mids_missing(con, coins: list[str], data_dir: str) -> list[dict]:
    """Righe di allMids in cui una coin sottoscritta non compare. Un mid che
    sparisce dal dizionario e' un dato mancante silenzioso: nessun errore,
    nessuna eccezione, solo una chiave in meno."""
    src = read(glob_channel(data_dir, "allMids"))
    total = con.execute(f"SELECT count(*) FROM {src}").fetchone()[0]
    rows = con.execute(
        "SELECT coin, count(*) FROM all_mids GROUP BY 1 ORDER BY 1"
    ).fetchall()
    present = {c: n for c, n in rows}
    return [
        {"coin": c, "n_allmids_rows": total, "n_present": present.get(c, 0),
         "n_missing": total - present.get(c, 0)}
        for c in coins
    ]


def price_coherence(con) -> list[dict]:
    """Confronta il mid del book con il mid pubblicato su allMids.

    Ogni riga di l2Book viene confrontata con l'ultima riga di allMids
    ricevuta prima di essa (ASOF), entro 2 s. Non e' un confronto simmetrico
    di proposito: allMids arriva piu' di rado, e prendere il valore
    *successivo* significherebbe confrontare il book con un prezzo che al
    momento dello snapshot non era ancora noto.
    """
    con.execute(
        f"""
        CREATE OR REPLACE TABLE coherence AS
        SELECT b.coin, b.ts_local_ns, b.mid AS book_mid, m.mid AS allmids_mid,
               b.ts_local_ns - m.ts_local_ns AS lag_ns,
               1e4 * (b.mid - m.mid) / m.mid  AS dev_bps
        FROM book_top b ASOF JOIN all_mids m
          ON b.coin = m.coin AND b.ts_local_ns >= m.ts_local_ns
        WHERE m.mid > 0 AND b.mid > 0
          AND b.ts_local_ns - m.ts_local_ns <= {COHERENCE_MAX_LAG_NS}
        """
    )
    return _rows(
        con,
        f"""
        SELECT coin, count(*) AS n,
               avg(dev_bps) AS mean_bps,
               quantile_cont(dev_bps, {QUANTILES}) AS q,
               max(abs(dev_bps)) AS max_abs_bps,
               count(*) FILTER (WHERE abs(dev_bps) > 5)   AS n_gt_5bps,
               count(*) FILTER (WHERE abs(dev_bps) > {COHERENCE_ANOMALY_BPS})
                                                          AS n_gt_10bps,
               count(*) FILTER (WHERE abs(dev_bps) > 50)  AS n_gt_50bps,
               median(lag_ns) / 1e9 AS median_lag_s
        FROM coherence GROUP BY 1 ORDER BY 1
        """,
        ["coin", "n", "mean_bps", "q", "max_abs_bps", "n_gt_5bps", "n_gt_10bps",
         "n_gt_50bps", "median_lag_s"],
    )


def coherence_worst(con, limit: int) -> list[dict]:
    return _rows(
        con,
        f"""
        SELECT coin, epoch_ms(ts_local_ns // 1000000) AS at_utc,
               book_mid, allmids_mid, dev_bps, lag_ns / 1e9 AS lag_s
        FROM coherence WHERE abs(dev_bps) > {COHERENCE_ANOMALY_BPS}
        ORDER BY abs(dev_bps) DESC LIMIT {int(limit)}
        """,
        ["coin", "at_utc", "book_mid", "allmids_mid", "dev_bps", "lag_s"],
    )


def partition_drift(con, data_dir: str, partitions) -> list[dict]:
    """Righe finite in una directory `date=`/`hour=` diversa dall'ora del loro
    `ts_local_ns`, e righe la cui colonna `coin` non coincide con la directory.

    Il primo caso e' atteso e documentato nel writer (il batch eredita l'ora
    del primo record) e serve solo a quantificare quanto: e' la prova che
    contare i file per ora sarebbe sbagliato. Il secondo caso non e' atteso da
    niente: significherebbe dati archiviati sotto la coin sbagliata.
    """
    cols = ["channel", "coin", "n_rows", "n_hour_drift", "n_coin_mismatch",
            "n_channel_mismatch"]
    return per_partizione(
        con, data_dir, partitions, "_drift_raw", cols,
        lambda src: rf"""
        WITH r AS (
            SELECT channel,
                   CASE WHEN coin = '' THEN '_global' ELSE coin END AS row_coin,
                   regexp_extract(filename, '([^/]+)/[^/]+/date=', 1) AS dir_channel,
                   regexp_extract(filename, '/([^/]+)/date=', 1)      AS dir_coin,
                   regexp_extract(filename, 'date=([0-9-]+)/hour=([0-9]+)', ['d', 'h'])
                       AS dh,
                   ts_local_ns
            FROM {src}
        )
        SELECT channel, row_coin AS coin, count(*) AS n_rows,
               count(*) FILTER (
                   WHERE strftime(epoch_ms(ts_local_ns // 1000000), '%Y-%m-%d %H')
                      <> dh['d'] || ' ' || dh['h']
               ) AS n_hour_drift,
               count(*) FILTER (WHERE dir_coin <> row_coin) AS n_coin_mismatch,
               count(*) FILTER (WHERE dir_channel <> channel) AS n_channel_mismatch
        FROM r GROUP BY 1, 2
        """,
    )
