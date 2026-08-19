"""Serie del funding da activeAssetCtx, con verifica incrociata su REST.

Convenzione di segno, usata ovunque in questo modulo: su Hyperliquid un
funding **positivo** significa che i long pagano i short. Quindi la somma dei
rate su una finestra e' il **costo** sostenuto da una posizione long, in
frazione del notional. Un numero positivo qui e' denaro uscito.

Il punto delicato non e' la somma, e' cosa si somma. `ctx.funding` viene
ripubblicato circa una volta al secondo: sommare tutti i campioni conterebbe
lo stesso funding migliaia di volte. Il funding su Hyperliquid si regola una
volta all'ora, quindi la serie giusta e' **un valore per ora**, e il valore
scelto e' l'ultimo campione osservato dentro l'ora — quello piu' vicino
all'istante di regolamento. E' un'assunzione, ed e' il motivo per cui lo
stesso numero viene ricalcolato da `backfill_funding` (la serie storica REST,
che e' gia' oraria per costruzione) e i due risultati vengono confrontati.
"""

from __future__ import annotations

from .dataset import glob_channel, read
from .metrics import MS_PER_HOUR, NS_PER_HOUR
from .sanity import QUANTILES

# Lag testati nel confronto fra la serie da activeAssetCtx e quella REST.
# Non e' una taratura: serve a determinare empiricamente come si allineano due
# serie di cui non conosciamo a priori la convenzione di etichettatura oraria.
CROSS_CHECK_LAGS = (-1, 0, 1)


def build_samples(con, data_dir: str) -> int:
    src = read(glob_channel(data_dir, "activeAssetCtx"))
    con.execute(
        f"""
        CREATE OR REPLACE TABLE funding_samples AS
        SELECT coin, ts_local_ns,
               ts_local_ns // {NS_PER_HOUR} AS hour_idx,
               TRY_CAST(json_extract_string(raw, '$.ctx.funding')      AS DOUBLE) AS funding,
               TRY_CAST(json_extract_string(raw, '$.ctx.premium')      AS DOUBLE) AS premium,
               TRY_CAST(json_extract_string(raw, '$.ctx.markPx')       AS DOUBLE) AS mark_px,
               TRY_CAST(json_extract_string(raw, '$.ctx.oraclePx')     AS DOUBLE) AS oracle_px,
               TRY_CAST(json_extract_string(raw, '$.ctx.openInterest') AS DOUBLE) AS open_interest
        FROM {src}
        """
    )
    return con.execute("SELECT count(*) FROM funding_samples").fetchone()[0]


def build_hourly_series(con) -> None:
    """Un valore per ora: l'ultimo campione dell'ora. Vengono tenuti anche min,
    max e numero di valori distinti dentro l'ora, perche' se il rate oscillasse
    molto entro l'ora la scelta dell'ultimo campione smetterebbe di essere
    innocua e il report deve poterlo mostrare."""
    con.execute(
        """
        CREATE OR REPLACE TABLE funding_hourly AS
        SELECT coin, hour_idx,
               epoch_ms(hour_idx * 3600000)          AS hour_utc,
               arg_max(funding, ts_local_ns)         AS funding_last,
               min(funding)                          AS funding_min,
               max(funding)                          AS funding_max,
               avg(funding)                          AS funding_mean,
               count(*)                              AS n_samples,
               count(DISTINCT funding)               AS n_distinct,
               arg_max(mark_px, ts_local_ns)         AS mark_px_last,
               arg_max(open_interest, ts_local_ns)   AS open_interest_last
        FROM funding_samples WHERE funding IS NOT NULL
        GROUP BY 1, 2
        """
    )


def stats(con) -> list[dict]:
    cols = ["coin", "n_hours", "first_hour", "last_hour", "min", "max", "mean",
            "median", "stddev", "q", "n_pos", "n_neg", "n_zero", "n_sign_changes",
            "mean_annual_pct", "intrahour_spread_max"]
    rows = con.execute(
        f"""
        WITH nz AS (
            SELECT coin, hour_idx, funding_last,
                   lag(funding_last) OVER (PARTITION BY coin ORDER BY hour_idx) AS prev
            FROM funding_hourly WHERE funding_last <> 0
        ),
        sc AS (
            SELECT coin, count(*) AS n_sign_changes FROM nz
            WHERE prev IS NOT NULL AND sign(funding_last) <> sign(prev)
            GROUP BY 1
        )
        SELECT h.coin,
               count(*)                                   AS n_hours,
               min(h.hour_utc)                            AS first_hour,
               max(h.hour_utc)                            AS last_hour,
               min(h.funding_last)                        AS mn,
               max(h.funding_last)                        AS mx,
               avg(h.funding_last)                        AS mean,
               median(h.funding_last)                     AS med,
               stddev_samp(h.funding_last)                AS sd,
               quantile_cont(h.funding_last, {QUANTILES}) AS q,
               count(*) FILTER (WHERE h.funding_last > 0)  AS n_pos,
               count(*) FILTER (WHERE h.funding_last < 0)  AS n_neg,
               count(*) FILTER (WHERE h.funding_last = 0)  AS n_zero,
               coalesce(any_value(sc.n_sign_changes), 0)   AS n_sign_changes,
               avg(h.funding_last) * 24 * 365 * 100        AS mean_annual_pct,
               max(h.funding_max - h.funding_min)          AS intrahour_spread_max
        FROM funding_hourly h LEFT JOIN sc ON sc.coin = h.coin
        GROUP BY 1 ORDER BY 1
        """
    ).fetchall()
    return [dict(zip(cols, r)) for r in rows]


def cumulative_long(con, days: int) -> list[dict]:
    """Funding cumulato pagato da un long tenuto `days` giorni, in % del
    notional, sulle ultime `days*24` ore complete disponibili.

    "Ore complete": si scarta l'ultima ora osservata, che e' in corso mentre il
    catalogo gira. Le ore della finestra in cui non esiste nessun campione
    vengono contate come mancanti e riportate: non vengono sostituite con zero,
    perche' zero e' un valore di funding legittimo e confonderlo con "non lo
    so" e' esattamente il modo in cui un backtest produce un numero plausibile
    e falso.

    Assunzione sul notional: posizione a notional costante, cioe' il funding
    di ogni ora si applica alla stessa dimensione. E' l'approssimazione
    standard e sottostima leggermente il costo di una posizione in guadagno
    (il cui notional cresce). Il report la dichiara.

    **`from_hour` e `to_hour` sono istanti di REGOLAMENTO, non di
    campionamento.** L'ultimo campione osservato durante l'ora H e' il rate che
    viene regolato all'inizio dell'ora H+1 (l'allineamento e' misurato, vedi
    `costs.funding`), quindi una finestra che somma i campioni delle ore
    [A, B] descrive i regolamenti delle ore [A+1, B+1]. Etichettarla con A e B
    la faceva sembrare sfasata di un'ora rispetto alla stessa finestra
    calcolata da `costs/`, che ragiona in ore di regolamento. Le ore sommate
    sono esattamente le stesse di prima: cambia solo come vengono dichiarate.
    """
    hours = days * 24
    cols = ["coin", "hours_requested", "hours_observed", "hours_missing",
            "from_hour", "to_hour", "cum_funding_pct", "cum_funding_frac",
            "worst_hour_pct", "best_hour_pct", "mark_px_first", "mark_px_last",
            "first_settlement_hour_idx", "last_settlement_hour_idx"]
    rows = con.execute(
        f"""
        WITH last_complete AS (
            SELECT coin, max(hour_idx) - 1 AS h_end FROM funding_hourly GROUP BY 1
        ),
        win AS (
            SELECT f.*, l.h_end
            FROM funding_hourly f JOIN last_complete l USING (coin)
            WHERE f.hour_idx <= l.h_end AND f.hour_idx > l.h_end - {hours}
        )
        SELECT coin,
               {hours}                                  AS hours_requested,
               count(*)                                 AS hours_observed,
               {hours} - count(*)                       AS hours_missing,
               epoch_ms((min(hour_idx) + 1) * {MS_PER_HOUR}) AS from_hour,
               epoch_ms((max(hour_idx) + 1) * {MS_PER_HOUR}) AS to_hour,
               sum(funding_last) * 100                  AS cum_pct,
               sum(funding_last)                        AS cum_frac,
               max(funding_last) * 100                  AS worst_hour_pct,
               min(funding_last) * 100                  AS best_hour_pct,
               arg_min(mark_px_last, hour_idx)          AS mark_first,
               arg_max(mark_px_last, hour_idx)          AS mark_last,
               -- Estremi NOMINALI della finestra, in ore di regolamento: non
               -- dipendono da quali ore siano state osservate, e sono quelli
               -- che il cross-check con `costs/` deve poter confrontare.
               any_value(h_end) - {hours} + 2           AS first_settlement,
               any_value(h_end) + 1                     AS last_settlement
        FROM win GROUP BY 1 ORDER BY 1
        """
    ).fetchall()
    return [dict(zip(cols, r)) for r in rows]


def build_rest_series(con, data_dir: str) -> dict:
    """Serie oraria dal dump REST `fundingHistory`, deduplicata.

    Il backfill rigira a ogni riconnessione con 48 h di lookback, quindi lo
    stesso (coin, time) compare in decine di dump. Se due dump dichiarano rate
    diversi per lo stesso istante, non e' una duplicazione innocua ma una
    contraddizione fra due letture della stessa storia: viene contata.
    """
    src = read(glob_channel(data_dir, "backfill_funding"))
    con.execute(
        f"""
        CREATE OR REPLACE TABLE rest_funding_raw AS
        SELECT json_extract_string(e, '$.coin')                    AS coin,
               TRY_CAST(json_extract_string(e, '$.time') AS BIGINT) AS time_ms,
               TRY_CAST(json_extract_string(e, '$.fundingRate') AS DOUBLE) AS rate
        FROM (SELECT unnest(json_extract(raw, '$[*]')) AS e FROM {src})
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE TABLE rest_funding AS
        SELECT coin, time_ms, time_ms // {MS_PER_HOUR} AS hour_idx, any_value(rate) AS rate,
               count(DISTINCT rate) AS n_distinct_rates, count(*) AS n_copies
        FROM rest_funding_raw WHERE coin IS NOT NULL AND time_ms IS NOT NULL
        GROUP BY 1, 2
        """
    )
    row = con.execute(
        """
        SELECT count(*), sum(n_copies), sum(n_distinct_rates > 1),
               count(DISTINCT coin), min(time_ms), max(time_ms),
               count(*) FILTER (WHERE time_ms % 3600000 > 60000)
        FROM rest_funding
        """
    ).fetchone()
    return dict(
        zip(
            ["n_settlements", "n_rows_before_dedup", "n_contradictory", "n_coins",
             "first_time_ms", "last_time_ms", "n_off_hour"],
            row,
        )
    )


def cross_check(con) -> list[dict]:
    """Confronta la serie oraria derivata da activeAssetCtx con quella REST a
    piu' sfasamenti. Lo sfasamento che minimizza l'errore dice come le due
    fonti si allineano; l'errore residuo dice quanto ci si puo' fidare della
    serie derivata."""
    cols = ["coin", "lag_h", "n", "mean_abs_diff", "max_abs_diff", "corr"]
    out: list[dict] = []
    for lag in CROSS_CHECK_LAGS:
        rows = con.execute(
            f"""
            SELECT r.coin, {lag} AS lag_h, count(*) AS n,
                   avg(abs(f.funding_last - r.rate))  AS mad,
                   max(abs(f.funding_last - r.rate))  AS maxd,
                   corr(f.funding_last, r.rate)       AS c
            FROM rest_funding r
            JOIN funding_hourly f
              ON f.coin = r.coin AND f.hour_idx = r.hour_idx + {lag}
            GROUP BY 1 ORDER BY 1
            """
        ).fetchall()
        out.extend(dict(zip(cols, r)) for r in rows)
    return out


def cumulative_long_rest(con, days: int) -> list[dict]:
    """Stesso calcolo di `cumulative_long`, ma sulla serie REST: e' il numero
    di controllo. Se i due divergono in modo apprezzabile, il numero da
    activeAssetCtx non e' affidabile e il report deve dirlo."""
    hours = days * 24
    cols = ["coin", "hours_observed", "from_hour", "to_hour", "cum_funding_pct"]
    rows = con.execute(
        f"""
        WITH last_complete AS (
            SELECT coin, max(hour_idx) AS h_end FROM rest_funding GROUP BY 1
        ),
        win AS (
            SELECT r.* FROM rest_funding r JOIN last_complete l USING (coin)
            WHERE r.hour_idx <= l.h_end AND r.hour_idx > l.h_end - {hours}
        )
        SELECT coin, count(*) AS n,
               epoch_ms(min(hour_idx) * {MS_PER_HOUR}) AS from_hour,
               epoch_ms(max(hour_idx) * {MS_PER_HOUR}) AS to_hour,
               sum(rate) * 100 AS cum_pct
        FROM win GROUP BY 1 ORDER BY 1
        """
    ).fetchall()
    return [dict(zip(cols, r)) for r in rows]
