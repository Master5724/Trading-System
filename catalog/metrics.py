"""Copertura oraria e marcatura delle ore inaffidabili.

Tre scelte di metodo, tutte deliberate:

1. **L'ora della riga viene da `ts_local_ns`, non dalla directory.** Il writer
   partiziona sull'ora del primo record del batch, quindi un batch a cavallo
   dell'ora finisce tutto nella directory precedente. Contare i file per ora,
   o fidarsi del nome della directory, misura il comportamento del flush, non
   il volume dei dati.

2. **Le ore mancanti esistono.** La griglia va da min a max ora di ogni
   partizione: un'ora senza nemmeno una riga compare con `n_rows = 0`. Un
   GROUP BY da solo la farebbe sparire, ed e' esattamente l'ora che non deve
   sparire.

3. **I due flag non si combinano mai.** `gap_overlap` e' un fatto registrato
   dal processo che si e' scollegato; `low_volume` e' una deduzione statistica
   dai dati. Fonderli in un unico "ora buona/cattiva" butterebbe via la
   distinzione fra "so che mancava" e "sospetto che manchi". Il catalogo marca,
   non esclude.

4. **`low_volume` vale solo dove la cadenza e' fissa.** La statistica oraria
   (`n_rows`, `median_rows`, `rows_ratio`, `below_median`) viene calcolata e
   pubblicata per tutti i canali; il flag di sospetto viene alzato solo sui
   canali a cadenza fissa. Su `trades` e `candle` il volume orario varia con
   l'attivita' del mercato di molto piu' del 10%, e marcare quelle ore
   significa produrre centinaia di allarmi che dicono "di domenica notte si
   scambia meno" — rumore che nasconde le poche ore davvero rotte. Vedi
   `dataset.DEFAULT_FIXED_RATE_CHANNELS`.
"""

from __future__ import annotations

from .dataset import (
    BACKFILL_CHANNELS,
    DEFAULT_FIXED_RATE_CHANNELS,
    accumulate,
    drop,
    glob_partition,
    read,
)

# Soglia del flag statistico: sotto il 90% della mediana oraria della stessa
# coppia canale/coin. Valore fissato dalla specifica del task, non tarato sui
# dati: tararlo significherebbe scegliere la soglia che produce il numero di
# ore sospette che si preferisce vedere.
LOW_VOLUME_FRACTION = 0.90

NS_PER_HOUR = 3_600_000_000_000
MS_PER_HOUR = 3_600_000


def build_hourly(con, data_dir: str, partitions: list[tuple[str, str]]) -> None:
    """Popola la tabella `hourly` con una riga per (canale, coin, ora UTC).

    Una query per partizione, non una su tutti i file: il GROUP BY e' gia' per
    (canale, coin, ora), quindi partizionare lo scan da' lo stesso risultato
    tenendo in memoria un solo canale/coin per volta.
    """
    drop(con, "hourly_raw")
    for channel, coin in partitions:
        src = read(glob_partition(data_dir, channel, coin))
        accumulate(
            con,
            "hourly_raw",
            f"""
        SELECT
            channel,
            CASE WHEN coin = '' THEN '_global' ELSE coin END AS coin,
            ts_local_ns // {NS_PER_HOUR} AS hour_idx,
            count(*)                                              AS n_rows,
            min(ts_local_ns)                                      AS first_ts_ns,
            max(ts_local_ns)                                      AS last_ts_ns,
            count(*) FILTER (WHERE ts_exch_ms = 0)                AS n_exch_ts_zero,
            count(*) FILTER (WHERE ts_exch_ms <> 0)               AS n_exch_ts_set,
            count(DISTINCT ts_exch_ms) FILTER (WHERE ts_exch_ms <> 0)
                                                                  AS n_exch_ts_distinct,
            median(ts_local_ns / 1e6 - ts_exch_ms)
                FILTER (WHERE ts_exch_ms <> 0)                    AS latency_ms_p50
        FROM {src}
        GROUP BY 1, 2, 3
        """,
        )

    # Griglia densa: ogni ora fra la prima e l'ultima osservata, per partizione.
    con.execute(
        """
        CREATE OR REPLACE TABLE hourly_grid AS
        SELECT r.channel, r.coin, g.hour_idx
        FROM (
            SELECT channel, coin, min(hour_idx) AS h0, max(hour_idx) AS h1
            FROM hourly_raw GROUP BY 1, 2
        ) r, generate_series(r.h0, r.h1) g(hour_idx)
        """
    )

    con.execute(
        f"""
        CREATE OR REPLACE TABLE hourly_base AS
        SELECT
            g.channel,
            g.coin,
            g.hour_idx,
            g.hour_idx * {MS_PER_HOUR}                    AS hour_start_ms,
            epoch_ms(g.hour_idx * {MS_PER_HOUR})          AS hour_utc,
            coalesce(h.n_rows, 0)                         AS n_rows,
            h.first_ts_ns,
            h.last_ts_ns,
            coalesce(h.n_exch_ts_zero, 0)                 AS n_exch_ts_zero,
            coalesce(h.n_exch_ts_set, 0)                  AS n_exch_ts_set,
            coalesce(h.n_exch_ts_distinct, 0)             AS n_exch_ts_distinct,
            h.latency_ms_p50,
            g.channel NOT IN ({", ".join(repr(c) for c in sorted(BACKFILL_CHANNELS))})
                                                          AS is_stream,
            g.hour_idx = min(g.hour_idx) OVER (PARTITION BY g.channel, g.coin)
              OR g.hour_idx = max(g.hour_idx) OVER (PARTITION BY g.channel, g.coin)
                                                          AS is_edge_hour
        FROM hourly_grid g
        LEFT JOIN hourly_raw h USING (channel, coin, hour_idx)
        """
    )


def apply_gap_overlap(con) -> None:
    """Interseca ogni ora con le finestre di disconnessione registrate.

    Le finestre vengono applicate a TUTTI i canali, non solo a quelli elencati
    nel record. Il campo `channels` dice quali stream erano sottoscritti quando
    la connessione e' caduta, ma a cadere e' la connessione del processo: se
    quel processo non riceveva niente, non riceveva niente per nessuno.
    Applicare la finestra solo ai canali elencati marcherebbe meno ore di
    quante siano davvero sospette.
    """
    con.execute(
        f"""
        CREATE OR REPLACE TABLE hourly_gaps AS
        SELECT h.channel, h.coin, h.hour_idx,
               sum(
                   greatest(0,
                       least(w.end_ms, h.hour_start_ms + {MS_PER_HOUR})
                     - greatest(w.start_ms, h.hour_start_ms))
               ) / 1000.0 AS gap_seconds,
               count(*)    AS n_gap_windows,
               bool_or(w.still_open) AS gap_still_open
        FROM hourly_base h
        JOIN gap_windows w
          ON w.start_ms < h.hour_start_ms + {MS_PER_HOUR}
         AND w.end_ms   > h.hour_start_ms
        GROUP BY 1, 2, 3
        """
    )


def _in_list(column: str, values: frozenset[str] | set[str]) -> str:
    """Predicato SQL di appartenenza. Con un insieme vuoto ritorna `false`:
    `IN ()` non e' SQL valido, e la risposta giusta a "nessun canale e' a
    cadenza fissa" e' "nessuna ora viene marcata", non un errore."""
    if not values:
        return "false"
    return f"{column} IN ({', '.join(repr(v) for v in sorted(values))})"


def apply_low_volume(con, fixed_rate_channels: frozenset[str] | None = None) -> None:
    """Confronta il conteggio orario con la mediana della stessa partizione.

    La mediana si calcola sulle ore "pulite": non la prima e non l'ultima ora
    della serie (sono parziali per costruzione, il collector e' partito o il
    catalogo gira a meta' ora) e non le ore che intersecano un buco registrato.
    Includerle abbasserebbe la mediana e renderebbe il flag cieco proprio dove
    serve. Se non resta nessuna ora pulita si ripiega su tutte le ore e lo si
    dichiara nella colonna `median_basis`.

    Il confronto con la mediana viene calcolato per tutti i canali
    (`below_median`), ma diventa il flag `low_volume` solo per i canali a
    cadenza fissa. Le due colonne separate servono a non confondere "non e'
    sotto la soglia" con "non e' stato valutato": `is_fixed_rate` dice quale
    dei due casi e'.
    """
    fixed = (
        DEFAULT_FIXED_RATE_CHANNELS if fixed_rate_channels is None
        else frozenset(fixed_rate_channels)
    )
    con.execute(
        """
        CREATE OR REPLACE TABLE hourly_median AS
        WITH j AS (
            SELECT b.*, coalesce(g.gap_seconds, 0) AS gap_seconds
            FROM hourly_base b LEFT JOIN hourly_gaps g USING (channel, coin, hour_idx)
        ),
        clean AS (
            SELECT channel, coin, median(n_rows) AS m, count(*) AS n
            FROM j WHERE NOT is_edge_hour AND gap_seconds = 0
            GROUP BY 1, 2
        ),
        allh AS (
            SELECT channel, coin, median(n_rows) AS m, count(*) AS n
            FROM j GROUP BY 1, 2
        )
        SELECT a.channel, a.coin,
               coalesce(c.m, a.m)                                   AS median_rows,
               coalesce(c.n, a.n)                                   AS median_basis_hours,
               CASE WHEN c.m IS NULL THEN 'tutte_le_ore' ELSE 'ore_pulite' END
                                                                    AS median_basis
        FROM allh a LEFT JOIN clean c USING (channel, coin)
        """
    )

    con.execute(
        f"""
        CREATE OR REPLACE TABLE hourly AS
        SELECT
            b.channel, b.coin, b.hour_utc, b.hour_idx, b.hour_start_ms,
            b.n_rows, b.first_ts_ns, b.last_ts_ns,
            b.n_exch_ts_zero, b.n_exch_ts_set, b.n_exch_ts_distinct,
            b.latency_ms_p50,
            b.is_stream, b.is_edge_hour,
            coalesce(g.gap_seconds, 0.0)      AS gap_seconds,
            coalesce(g.n_gap_windows, 0)      AS n_gap_windows,
            coalesce(g.gap_still_open, false) AS gap_still_open,
            coalesce(g.gap_seconds, 0.0) > 0  AS gap_overlap,
            m.median_rows, m.median_basis, m.median_basis_hours,
            CASE WHEN m.median_rows > 0 THEN b.n_rows / m.median_rows END AS rows_ratio,
            {_in_list("b.channel", fixed)}    AS is_fixed_rate,
            m.median_rows > 0 AND b.n_rows < {LOW_VOLUME_FRACTION} * m.median_rows
                                              AS below_median,
            {_in_list("b.channel", fixed)}
              AND m.median_rows > 0
              AND b.n_rows < {LOW_VOLUME_FRACTION} * m.median_rows
                                              AS low_volume
        FROM hourly_base b
        LEFT JOIN hourly_gaps g   USING (channel, coin, hour_idx)
        LEFT JOIN hourly_median m USING (channel, coin)
        """
    )


def attach_hourly_extra(con, table: str, columns: list[str]) -> None:
    """Innesta colonne per-ora prodotte da altri moduli (spread, per esempio).

    Tenuto separato perche' quelle metriche costano una lettura di `raw`,
    mentre tutto il resto di questo modulo legge solo le colonne dei timestamp.
    """
    sel = ", ".join(f"x.{c}" for c in columns)
    con.execute(
        f"""
        CREATE OR REPLACE TABLE hourly AS
        SELECT h.*, {sel}
        FROM hourly h LEFT JOIN {table} x USING (channel, coin, hour_idx)
        """
    )


def coverage(con) -> list[dict]:
    """Riga di copertura per partizione. Le righe per ora sono la mediana,
    non la media: una singola ora vuota o doppia non deve spostare il numero
    che si legge nel report."""
    cols = [
        "channel", "coin", "n_rows", "n_hours", "n_hours_with_data",
        "first_ts_ns", "last_ts_ns", "span_hours", "rows_per_hour_median",
        "rows_per_hour_mean", "n_hours_zero", "n_gap_hours", "n_low_volume_hours",
        "n_hours_below_median", "is_stream", "is_fixed_rate",
    ]
    rows = con.execute(
        """
        SELECT channel, coin,
               sum(n_rows)                                  AS n_rows,
               count(*)                                     AS n_hours,
               count(*) FILTER (WHERE n_rows > 0)           AS n_hours_with_data,
               min(first_ts_ns)                             AS first_ts_ns,
               max(last_ts_ns)                              AS last_ts_ns,
               (max(last_ts_ns) - min(first_ts_ns)) / 3.6e12 AS span_hours,
               median(n_rows)                               AS rows_per_hour_median,
               avg(n_rows)                                  AS rows_per_hour_mean,
               count(*) FILTER (WHERE n_rows = 0)           AS n_hours_zero,
               count(*) FILTER (WHERE gap_overlap)          AS n_gap_hours,
               count(*) FILTER (WHERE low_volume)           AS n_low_volume_hours,
               count(*) FILTER (WHERE below_median)         AS n_hours_below_median,
               any_value(is_stream)                         AS is_stream,
               any_value(is_fixed_rate)                     AS is_fixed_rate
        FROM hourly GROUP BY 1, 2 ORDER BY 1, 2
        """
    ).fetchall()
    return [dict(zip(cols, r)) for r in rows]


def flagged_hours(con, limit: int) -> list[dict]:
    cols = [
        "channel", "coin", "hour_utc", "n_rows", "median_rows", "rows_ratio",
        "gap_seconds", "gap_overlap", "low_volume", "is_edge_hour", "gap_still_open",
        "is_fixed_rate", "below_median",
    ]
    rows = con.execute(
        f"""
        SELECT channel, coin, hour_utc, n_rows, median_rows, rows_ratio,
               gap_seconds, gap_overlap, low_volume, is_edge_hour, gap_still_open,
               is_fixed_rate, below_median
        FROM hourly
        WHERE is_stream AND (gap_overlap OR low_volume)
        ORDER BY hour_utc, channel, coin
        LIMIT {int(limit)}
        """
    ).fetchall()
    return [dict(zip(cols, r)) for r in rows]


def flagged_counts(con) -> dict:
    """Conteggi delle ore marcate.

    `sotto_mediana_non_valutate` e' il numero di ore che sarebbero state
    marcate `low_volume` dalla versione precedente e che oggi non lo sono
    perche' il canale non ha cadenza fissa. Va pubblicato: una soglia che
    smette di essere applicata deve lasciare traccia del suo effetto, se no
    la differenza fra due report diventa inspiegabile.
    """
    row = con.execute(
        """
        SELECT count(*) FILTER (WHERE is_stream AND gap_overlap),
               count(*) FILTER (WHERE is_stream AND low_volume),
               count(*) FILTER (WHERE is_stream AND gap_overlap AND low_volume),
               count(*) FILTER (WHERE is_stream AND low_volume AND NOT gap_overlap),
               count(*) FILTER (WHERE is_stream AND low_volume AND NOT gap_overlap
                                  AND NOT is_edge_hour),
               count(*) FILTER (WHERE is_stream AND n_rows = 0),
               count(*) FILTER (WHERE is_stream),
               count(*) FILTER (WHERE is_stream AND below_median
                                  AND NOT is_fixed_rate),
               count(*) FILTER (WHERE is_stream AND is_fixed_rate)
        FROM hourly
        """
    ).fetchone()
    return dict(
        zip(
            ["gap_overlap", "low_volume", "entrambi", "solo_low_volume",
             "solo_low_volume_non_edge", "ore_vuote", "ore_totali_stream",
             "sotto_mediana_non_valutate", "ore_cadenza_fissa"],
            row,
        )
    )


def write_parquet(con, path: str) -> int:
    from .dataset import sql_str

    con.execute(
        f"COPY (SELECT * FROM hourly ORDER BY channel, coin, hour_idx) "
        f"TO {sql_str(path)} (FORMAT PARQUET, COMPRESSION zstd)"
    )
    return con.execute("SELECT count(*) FROM hourly").fetchone()[0]
