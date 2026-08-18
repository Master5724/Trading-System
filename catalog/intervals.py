"""Distribuzione degli intervalli fra messaggi consecutivi, e verifica della
classificazione dichiarata dei canali.

Due domande a cui il catalogo non sapeva rispondere, e che hanno la stessa
risposta:

1. **La classificazione "a cadenza fissa" e' dichiarata, non misurata.**
   `dataset.DEFAULT_FIXED_RATE_CHANNELS` elenca i canali che si suppone
   l'exchange pubblichi a intervalli regolari, e su quell'elenco si basa il
   flag `low_volume`. Ma nessuno verifica l'ipotesi: se domani Hyperliquid
   passasse `allMids` da "ogni 5 secondi" a "quando cambia qualcosa", il
   catalogo continuerebbe a marcare ore come sospette usando una soglia che
   non ha piu' senso, e il report non lo direbbe.

2. **La soglia di staleness del kill switch (Task 5) e' un multiplo del p99
   degli intervalli**, e il catalogo pubblicava solo righe per ora. Righe per
   ora e p99 degli intervalli non sono la stessa informazione: un canale puo'
   consegnare lo stesso numero di righe stando zitto meta' ora e poi
   raffichando, e il conteggio orario non lo distingue da un metronomo.

L'indice di regolarita' e' `p99 / mediana`. Vicino a 1 significa che il
99esimo percentile degli intervalli e' lungo quanto quello tipico: metronomo.
Molto sopra 1 significa che gli arrivi si concentrano e poi tacciono. E' un
rapporto e non una deviazione standard di proposito: e' adimensionale, quindi
confronta `allMids` (un messaggio ogni 5 s) con `trades` (a raffiche) senza
che la scala dei due canali entri nel numero.

**Tre scelte di metodo.**

*Gli intervalli che SONO un buco non contano.* Un'interruzione di 4959 secondi
produce un intervallo da 4959 secondi che non dice niente sulla cadenza del
canale: dice che la raccolta era ferma. Lasciarlo dentro sposterebbe massimo e
p99 verso una misura della qualita' della connessione invece che della cadenza
dell'exchange.

*Quali intervalli siano buchi lo decidono i dati, non il registro.* Fino al
2026-08-14 l'esclusione si basava sulle finestre di `_gaps.jsonl`, ed era buona
solo quanto quel registro: le finestre venivano chiuse alla riconnessione della
socket invece che alla ripresa dei dati, quindi un buco reale dell'8 agosto
sopravviveva dentro le statistiche. Ora la sorgente e' `derivedgaps`: un
intervallo e' escluso se supera la soglia della sua partizione, e il registro
serve a spiegare la causa (`derivedgaps.reconcile`), non a stabilire
l'esistenza. Conseguenza da tenere a mente leggendo la tabella: `max_s` non puo'
superare la soglia per costruzione, ed e' il motivo per cui il report la stampa
accanto.

*Gli intervalli nulli restano dentro.* Due messaggi consegnati nello stesso
nanosecondo locale sono un fatto: e' il modo in cui un canale a raffiche si
comporta, ed e' proprio cio' che l'indice deve vedere. Conseguenza: su un
canale abbastanza a raffiche la mediana puo' essere 0 e il rapporto non
esistere. Non si sostituisce con un numero comodo: il rapporto resta NULL e il
verdetto e' `smentito` se quel canale era dichiarato a cadenza fissa — una
mediana di 0 e' la prova piu' forte possibile che non lo e'.

Il catalogo NON corregge la classificazione. La segnala e basta: quali canali
siano a cadenza fissa dipende da cosa il collector ha sottoscritto e da come si
comporta l'exchange, e va deciso guardando i numeri, non da un `if`.
"""

from __future__ import annotations

from .dataset import BACKFILL_CHANNELS, sql_str

# Soglie della verifica di classificazione. Scelte a priori e asimmetriche, non
# tarate sui dati: CLAUDE.md invariante 7. Il rapporto grezzo viene pubblicato
# comunque, quindi chi legge puo' dissentire dal verdetto senza rifare il conto.
#
# La banda morta fra 2 e 3 non e' indecisione: un canale dichiarato a cadenza
# fissa viene smentito solo se il p99 vale piu' di TRE mediane (margine ampio,
# perche' smentire una dichiarazione e' un'affermazione forte), mentre un canale
# non dichiarato viene segnalato come sospettosamente regolare solo se sta sotto
# DUE mediane. Fra 2 e 3 nessuno dei due verdetti viene emesso, e un canale al
# confine non cambia etichetta a ogni run.
RATIO_FIXED_MAX = 3.0
RATIO_REGULAR_MAX = 2.0

# Sotto questo numero di intervalli utilizzabili il p99 e' una singola
# osservazione travestita da percentile. Il verdetto viene sospeso.
MIN_INTERVALS = 100

VERDICT_OK = "coerente"
VERDICT_DENIED = "smentito"
VERDICT_UNDECLARED = "regolare_non_dichiarato"
VERDICT_SKIPPED = "non_valutato"

# Colonne innestate su `hourly` e quindi presenti nel parquet riassuntivo.
HOURLY_COLUMNS = [
    "iv_n", "iv_median_s", "iv_p90_s", "iv_p99_s", "iv_max_s", "iv_mean_s",
    "iv_ratio_p99_p50", "iv_n_excluded_gap", "iv_gap_threshold_s", "iv_verdict",
]


def build(con) -> int:
    """Costruisce la tabella `intervals`, una riga per (canale, coin).

    Legge `ts_ordered` (gia' materializzata da `sanity.build_ordered`): quella
    tabella tiene le righe nell'ordine di scrittura, che e' l'ordine di arrivo,
    e ha gia' il `delta_ns` dal messaggio precedente. Ricalcolarlo qui
    significherebbe rileggere 2.6 GB di parquet per ottenere le stesse colonne.

    Legge anche `gap_thresholds` (`derivedgaps.build_thresholds`): un intervallo
    e' un buco se supera la soglia della sua partizione. Un LEFT JOIN che non
    trova soglia — i canali di backfill, che soglia non hanno — non esclude
    niente: su un dump REST per riconnessione la distanza fra due righe e' il
    funzionamento previsto, non un'interruzione.

    L'ordine e' single-pass di proposito: la soglia si calcola sugli intervalli
    grezzi, poi si escludono i buchi, poi si ricalcolano i percentili. Nessuna
    iterazione fino a convergenza — sarebbe una taratura, e il p99 e' gia'
    insensibile a un pugno di interruzioni.

    I `delta_ns` negativi (passi indietro dell'orologio) non entrano nella
    distribuzione: sarebbero durate negative. Vengono contati a parte, e
    `sanity.monotonicity` li elenca gia' per esteso — qui interessa solo che non
    inquinino i percentili.
    """
    backfill = ", ".join(repr(c) for c in sorted(BACKFILL_CHANNELS))
    con.execute(
        f"""
        CREATE OR REPLACE TABLE intervals AS
        SELECT *,
               CASE WHEN median_s > 0 THEN p99_s / median_s END AS ratio_p99_p50
        FROM (
            WITH d AS (
                SELECT o.channel, o.coin, o.delta_ns,
                       t.threshold_ns, t.threshold_s
                FROM ts_ordered o
                LEFT JOIN gap_thresholds t USING (channel, coin)
                WHERE o.delta_ns IS NOT NULL
            ),
            usable AS (
                SELECT channel, coin, delta_ns, threshold_s,
                       coalesce(delta_ns >= threshold_ns, false) AS in_gap,
                       coalesce(delta_ns < threshold_ns, true) AND delta_ns >= 0
                                                                 AS ok
                FROM d
            )
            SELECT channel, coin,
                   channel NOT IN ({backfill})                     AS is_stream,
                   max(threshold_s)                                AS gap_threshold_s,
                   count(*)                                        AS n_pairs,
                   count(*) FILTER (WHERE in_gap)                  AS n_excluded_gap,
                   count(*) FILTER (WHERE NOT in_gap AND delta_ns < 0)
                                                                   AS n_excluded_negative,
                   count(*) FILTER (WHERE ok)                      AS n,
                   min(delta_ns) FILTER (WHERE ok) / 1e9           AS min_s,
                   quantile_cont(delta_ns, 0.50) FILTER (WHERE ok) / 1e9 AS median_s,
                   quantile_cont(delta_ns, 0.90) FILTER (WHERE ok) / 1e9 AS p90_s,
                   quantile_cont(delta_ns, 0.99) FILTER (WHERE ok) / 1e9 AS p99_s,
                   max(delta_ns) FILTER (WHERE ok) / 1e9           AS max_s,
                   avg(delta_ns) FILTER (WHERE ok) / 1e9           AS mean_s
            FROM usable
            GROUP BY 1, 2
        )
        """
    )
    return con.execute(
        "SELECT coalesce(sum(n_excluded_gap), 0) FROM intervals"
    ).fetchone()[0]


STAT_COLUMNS = [
    "channel", "coin", "is_stream", "n", "median_s", "p90_s", "p99_s", "max_s",
    "mean_s", "min_s", "ratio_p99_p50", "n_pairs", "n_excluded_gap",
    "n_excluded_negative", "gap_threshold_s",
]


def stats(con) -> list[dict]:
    rows = con.execute(
        f"SELECT {', '.join(STAT_COLUMNS)} FROM intervals ORDER BY channel, coin"
    ).fetchall()
    return [dict(zip(STAT_COLUMNS, r)) for r in rows]


def classify(rows: list[dict], fixed_rate: frozenset[str] | set[str]) -> list[dict]:
    """Confronta la classificazione dichiarata con il rapporto misurato.

    Ritorna una riga per partizione con `declared`, `ratio_p99_p50`, `verdict` e
    una `nota` in italiano che dice cosa contesta il numero. Nessun elenco viene
    riscritto: `verdict` e' un'osservazione, non una correzione.
    """
    declared = frozenset(fixed_rate)
    out: list[dict] = []
    for r in rows:
        ratio = r.get("ratio_p99_p50")
        is_declared = r["channel"] in declared
        verdict, nota = _verdict(r, ratio, is_declared)
        out.append(
            {
                "channel": r["channel"],
                "coin": r["coin"],
                "declared_fixed_rate": is_declared,
                "n": r["n"],
                "median_s": r["median_s"],
                "p99_s": r["p99_s"],
                "ratio_p99_p50": ratio,
                "verdict": verdict,
                "nota": nota,
            }
        )
    return out


def _verdict(r: dict, ratio: float | None, is_declared: bool) -> tuple[str, str]:
    if not r["is_stream"]:
        return VERDICT_SKIPPED, "canale REST: un dump per riconnessione, nessuna cadenza"
    if (r["n"] or 0) < MIN_INTERVALS:
        return VERDICT_SKIPPED, f"meno di {MIN_INTERVALS} intervalli utilizzabili"
    if ratio is None:
        # Mediana 0: piu' della meta' dei messaggi arriva insieme al precedente.
        if is_declared:
            return VERDICT_DENIED, "mediana degli intervalli = 0: consegne a blocchi"
        return VERDICT_OK, "mediana degli intervalli = 0: consegne a blocchi, atteso"
    if is_declared and ratio > RATIO_FIXED_MAX:
        return (
            VERDICT_DENIED,
            f"dichiarato a cadenza fissa ma p99/mediana = {ratio:.1f} "
            f"(> {RATIO_FIXED_MAX:g})",
        )
    if not is_declared and ratio < RATIO_REGULAR_MAX:
        return (
            VERDICT_UNDECLARED,
            f"non dichiarato ma p99/mediana = {ratio:.2f} "
            f"(< {RATIO_REGULAR_MAX:g}): sembra un metronomo",
        )
    return VERDICT_OK, ""


def attach_to_hourly(con, classes: list[dict]) -> None:
    """Innesta le colonne `iv_*` su `hourly`, e quindi nel parquet riassuntivo.

    Sono costanti per partizione ripetute su ogni riga oraria. E' la stessa
    forma che `hourly` ha gia' per `median_rows`, `median_basis` e
    `is_fixed_rate`: chi legge il parquet trova la soglia di staleness del suo
    canale senza dover aprire un secondo file e senza doverla ricalcolare.
    """
    con.execute(
        "CREATE OR REPLACE TABLE interval_class "
        "(channel VARCHAR, coin VARCHAR, verdict VARCHAR)"
    )
    if classes:
        con.executemany(
            "INSERT INTO interval_class VALUES (?, ?, ?)",
            [(c["channel"], c["coin"], c["verdict"]) for c in classes],
        )
    con.execute(
        """
        CREATE OR REPLACE TABLE hourly AS
        SELECT h.*,
               i.n                 AS iv_n,
               i.median_s          AS iv_median_s,
               i.p90_s             AS iv_p90_s,
               i.p99_s             AS iv_p99_s,
               i.max_s             AS iv_max_s,
               i.mean_s            AS iv_mean_s,
               i.ratio_p99_p50     AS iv_ratio_p99_p50,
               i.n_excluded_gap    AS iv_n_excluded_gap,
               i.gap_threshold_s   AS iv_gap_threshold_s,
               c.verdict           AS iv_verdict
        FROM hourly h
        LEFT JOIN intervals i       USING (channel, coin)
        LEFT JOIN interval_class c  USING (channel, coin)
        """
    )


def write_parquet(con, path: str) -> int:
    """Copia autonoma della tabella per partizione. Il parquet orario le
    contiene gia' come costanti ripetute; questo file esiste per chi vuole le
    quattordici righe e non le decine di migliaia."""
    con.execute(
        f"COPY (SELECT * FROM intervals ORDER BY channel, coin) "
        f"TO {sql_str(path)} (FORMAT PARQUET, COMPRESSION zstd)"
    )
    return con.execute("SELECT count(*) FROM intervals").fetchone()[0]
