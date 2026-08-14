"""Top of book e distribuzione dello spread, da l2Book.

Serve al modello di costo (Task 2): lo slippage minimo pagabile da un ordine
market e' mezzo spread, e senza una distribuzione misurata quella meta' sarebbe
una costante inventata.

`book_top` viene materializzato una volta e riusato anche dal controllo di
coerenza dei prezzi: leggere due volte 550 MB di JSON per estrarre gli stessi
due numeri sarebbe solo tempo buttato.
"""

from __future__ import annotations

from .dataset import glob_channel, read
from .metrics import NS_PER_HOUR
from .sanity import QUANTILES


def build_book_top(con, data_dir: str) -> int:
    """Estrae bid/ask migliori da ogni snapshot.

    `levels[0]` sono i bid, `levels[1]` gli ask (verificato sui payload reali:
    levels[0][0].px < levels[1][0].px). Un lato vuoto produce NULL invece di
    far fallire la query: un book senza un lato e' un dato, e va contato.
    """
    src = read(glob_channel(data_dir, "l2Book"))
    con.execute(
        f"""
        CREATE OR REPLACE TABLE book_top AS
        SELECT coin, ts_local_ns, ts_exch_ms,
               ts_local_ns // {NS_PER_HOUR}       AS hour_idx,
               bid, ask,
               (bid + ask) / 2                    AS mid,
               ask - bid                          AS spread_abs,
               CASE WHEN bid > 0 AND ask > 0
                    THEN 1e4 * (ask - bid) / ((ask + bid) / 2) END AS spread_bps,
               n_bid_levels, n_ask_levels
        FROM (
            SELECT coin, ts_local_ns, ts_exch_ms,
                   TRY_CAST(json_extract_string(raw, '$.levels[0][0].px') AS DOUBLE) AS bid,
                   TRY_CAST(json_extract_string(raw, '$.levels[1][0].px') AS DOUBLE) AS ask,
                   json_array_length(raw, '$.levels[0]') AS n_bid_levels,
                   json_array_length(raw, '$.levels[1]') AS n_ask_levels
            FROM {src}
        )
        """
    )
    return con.execute("SELECT count(*) FROM book_top").fetchone()[0]


def stats(con) -> list[dict]:
    cols = ["coin", "n", "n_no_bid", "n_no_ask", "n_crossed", "n_locked",
            "spread_bps_q", "spread_bps_mean", "spread_bps_max",
            "spread_abs_q", "min_levels_bid", "min_levels_ask", "px_min", "px_max"]
    rows = con.execute(
        f"""
        SELECT coin,
               count(*)                                        AS n,
               count(*) FILTER (WHERE bid IS NULL)              AS n_no_bid,
               count(*) FILTER (WHERE ask IS NULL)              AS n_no_ask,
               count(*) FILTER (WHERE ask < bid)                AS n_crossed,
               count(*) FILTER (WHERE ask = bid)                AS n_locked,
               quantile_cont(spread_bps, {QUANTILES})           AS spread_bps_q,
               avg(spread_bps)                                  AS spread_bps_mean,
               max(spread_bps)                                  AS spread_bps_max,
               quantile_cont(spread_abs, {QUANTILES})           AS spread_abs_q,
               min(n_bid_levels)                                AS min_levels_bid,
               min(n_ask_levels)                                AS min_levels_ask,
               min(mid)                                         AS px_min,
               max(mid)                                         AS px_max
        FROM book_top GROUP BY 1 ORDER BY 1
        """
    ).fetchall()
    return [dict(zip(cols, r)) for r in rows]


def build_hourly_spread(con) -> None:
    con.execute(
        """
        CREATE OR REPLACE TABLE hourly_spread AS
        SELECT 'l2Book' AS channel, coin, hour_idx,
               median(spread_bps) AS spread_bps_p50,
               median(mid)        AS mid_p50
        FROM book_top GROUP BY 1, 2, 3
        """
    )
