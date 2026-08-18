"""Buchi derivati dai dati, e riconciliazione col registro del collector.

**Il principio.** Un buco nella raccolta si misura dall'assenza di righe, non
dalla dichiarazione di chi raccoglie. `_gaps.jsonl` e' una dichiarazione: dice
che il collector *sapeva* di essere scollegato, e ne da' la causa. Ma
l'affidabilita' di quella dichiarazione dipende dal codice che l'ha scritta, e
quel codice e' cambiato: fino al 2026-08-14 20:33 la finestra veniva chiusa
alla riconnessione della socket invece che alla ripresa effettiva dei dati,
quindi la durata registrata era sistematicamente piu' corta di quella vera —
in un caso noto di quasi cento secondi. Un'esclusione statistica alimentata da
quel registro e' buona solo quanto il registro: un buco reale dell'8 agosto
2026, presente su tutte e quattro le coin del canale `trades`, entrava ancora
nelle statistiche come intervallo valido.

Quindi l'ordine si inverte. I buchi si DERIVANO dai dati; il registro serve a
spiegarne la causa e a misurare quanto se ne accorgeva.

**Perche' e' compatibile con l'invariante 8** ("il silenzio di uno stream non
significa mai assenza di eventi"). L'invariante vieta di concludere *non e'
successo niente* dal silenzio, e vieta di prendere decisioni operative su quella
base. Qui il silenzio produce la conclusione opposta e conservativa: *non
sappiamo cosa e' successo*, quindi quell'intervallo viene escluso dalle
statistiche e stampato. Marcare piu' del necessario e' l'errore giusto da fare;
il costo di un falso positivo e' un intervallo perso, quello di un falso
negativo e' una statistica che sembra sana.

**La soglia.** Per ogni partizione (canale, coin) si prende il p99 osservato
degli intervalli fra righe consecutive e lo si moltiplica per un fattore
configurabile, con un minimo assoluto. Tre scelte dietro questa forma:

*Per partizione e non per canale.* Su un giorno reale il p99 di `trades/BTC` e'
2,8 s e quello di `trades/ETH` 17,0 s: sei volte tanto, stesso canale. Una
soglia unica di canale sarebbe dettata dalla coin piu' lenta e renderebbe cieco
il rilevamento sulle altre — cioe' proprio dove i buchi si vedono meglio.

*Un multiplo del p99 e non del massimo o della media.* Il massimo E' il buco che
si sta cercando (circolarita'), la media di un canale a raffiche non descrive
niente. Il p99 e' insensibile a un pugno di interruzioni su centinaia di
migliaia di intervalli, quindi la soglia non si sposta per colpa di cio' che
deve trovare.

*Il valore del fattore, e perche' non e' tarato.* Sui dati reali il rapporto
p999/p99 sta intorno a 1,4 su tutti i canali: oltre il p99 la coda naturale
muore in fretta. Il default 5 sta quindi circa 3,5 volte oltre il p999
osservato — un margine ampio scelto guardando la FORMA della distribuzione, non
il numero di buchi che produce (CLAUDE.md invariante 7). Il minimo assoluto
esiste perche' su un canale velocissimo (`activeAssetCtx`, p99 1,4 s) cinque
volte il p99 sono sette secondi, e sette secondi di silenzio non sono
un'interruzione della raccolta: sono un canale tranquillo.

Entrambi i numeri sono argomenti della CLI e il report stampa soglia, p99 e
conteggio per ogni partizione: chi legge puo' dissentire senza rifare i conti.

**Cosa NON fa.** Non usa la simultaneita' fra canali, che sarebbe il segnale
piu' forte di tutti — un'interruzione della raccolta ferma tutti i canali nello
stesso istante, una pausa di mercato no. Il rilevamento qui e' per partizione e
indipendente, quindi un silenzio isolato su una coin illiquida puo' comparire
fra i non spiegati. La riconciliazione lo rende visibile (stessi estremi su
quattro coin = interruzione; una sola coin = probabile mercato fermo), ma non
lo decide al posto di chi legge.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import datetime, timezone

from .dataset import BACKFILL_CHANNELS, sql_str
from .gapwindows import Window, merge_spans, merge_windows

# Fattore applicato al p99 della partizione, e pavimento assoluto in secondi.
# Scelti a priori dalla forma della distribuzione (vedi docstring), sovrascritti
# da --gap-p99-multiple e --gap-min-s.
DEFAULT_P99_MULTIPLE = 5.0
DEFAULT_MIN_GAP_S = 30.0

# Sotto questo numero di intervalli il p99 e' una singola osservazione
# travestita da percentile: resta solo il minimo assoluto. Stessa soglia di
# `intervals.MIN_INTERVALS`, stessa ragione.
MIN_INTERVALS_FOR_P99 = 100

BASIS_P99 = "p99"
BASIS_FLOOR = "minimo_assoluto"
BASIS_TOO_FEW = "pochi_intervalli"

NS_PER_S = 1_000_000_000


@dataclass(frozen=True)
class DerivedGap:
    """Un'assenza di righe fra due messaggi consecutivi della stessa partizione.

    Gli estremi sono i due timestamp locali che la delimitano: `start_ns` e'
    l'ultima riga prima del silenzio, `end_ns` la prima dopo. La durata vera
    dell'interruzione e' quindi al massimo questa — il collector puo' essersi
    scollegato un istante dopo `start_ns` — ed e' l'unica misura che i dati
    consentono senza inventare nulla.
    """

    channel: str
    coin: str
    start_ns: int
    end_ns: int

    @property
    def duration_s(self) -> float:
        return (self.end_ns - self.start_ns) / 1e9


def utc(ns: int) -> str:
    return datetime.fromtimestamp(ns / 1e9, tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def _day(ns: int) -> str:
    return datetime.fromtimestamp(ns / 1e9, tz=timezone.utc).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# 1. Soglie e rilevamento
# ---------------------------------------------------------------------------

THRESHOLD_COLUMNS = [
    "channel", "coin", "n_intervals", "p99_s", "threshold_s", "basis",
]


def build_thresholds(
    con,
    p99_multiple: float = DEFAULT_P99_MULTIPLE,
    min_gap_s: float = DEFAULT_MIN_GAP_S,
) -> list[dict]:
    """Materializza `gap_thresholds`, una riga per partizione di stream.

    Legge `ts_ordered` (gia' costruita da `sanity.build_ordered`): le righe
    nell'ordine di scrittura, con il `delta_ns` dal messaggio precedente.

    I canali di backfill non compaiono: sono dump REST scritti una volta per
    riconnessione, non hanno cadenza, e la distanza fra due dump non e' un buco
    di raccolta ma il funzionamento previsto. Restando fuori da questa tabella
    restano fuori anche dal rilevamento e dall'esclusione — un LEFT JOIN che
    non trova soglia non esclude niente.

    I `delta_ns` negativi (passi indietro dell'orologio) non entrano nel p99:
    sarebbero durate negative. `sanity.monotonicity` li elenca gia' per esteso.
    """
    backfill = ", ".join(repr(c) for c in sorted(BACKFILL_CHANNELS))
    con.execute(
        f"""
        CREATE OR REPLACE TABLE gap_thresholds AS
        SELECT *,
               CAST(threshold_s * {NS_PER_S} AS BIGINT) AS threshold_ns
        FROM (
            SELECT channel, coin, n_intervals, p99_s,
                   CASE WHEN n_intervals < {int(MIN_INTERVALS_FOR_P99)}
                        THEN {float(min_gap_s)}
                        ELSE greatest(p99_s * {float(p99_multiple)},
                                      {float(min_gap_s)}) END AS threshold_s,
                   CASE WHEN n_intervals < {int(MIN_INTERVALS_FOR_P99)}
                            THEN {sql_str(BASIS_TOO_FEW)}
                        WHEN p99_s * {float(p99_multiple)} >= {float(min_gap_s)}
                            THEN {sql_str(BASIS_P99)}
                        ELSE {sql_str(BASIS_FLOOR)} END AS basis
            FROM (
                SELECT channel, coin,
                       count(*)                              AS n_intervals,
                       quantile_cont(delta_ns, 0.99) / 1e9   AS p99_s
                FROM ts_ordered
                WHERE delta_ns IS NOT NULL AND delta_ns >= 0
                  AND channel NOT IN ({backfill})
                GROUP BY 1, 2
            )
        )
        """
    )
    rows = con.execute(
        f"SELECT {', '.join(THRESHOLD_COLUMNS)} FROM gap_thresholds "
        f"ORDER BY channel, coin"
    ).fetchall()
    return [dict(zip(THRESHOLD_COLUMNS, r)) for r in rows]


def build(con) -> int:
    """Materializza `derived_gaps` e ne ritorna il conteggio.

    Un buco e' un intervallo che supera la soglia della sua partizione: nessun
    join temporale, nessuna interpolazione. Questa e' anche la definizione che
    `intervals` usa per escludere, quindi le due tabelle non possono divergere:
    ogni riga di `derived_gaps` e' esattamente un intervallo escluso.
    """
    con.execute(
        """
        CREATE OR REPLACE TABLE derived_gaps AS
        SELECT o.channel, o.coin,
               o.ts_local_ns - o.delta_ns AS start_ns,
               o.ts_local_ns              AS end_ns,
               o.delta_ns / 1e9           AS duration_s,
               t.threshold_s
        FROM ts_ordered o
        JOIN gap_thresholds t USING (channel, coin)
        WHERE o.delta_ns >= t.threshold_ns
        ORDER BY o.ts_local_ns - o.delta_ns
        """
    )
    return con.execute("SELECT count(*) FROM derived_gaps").fetchone()[0]


STAT_COLUMNS = [
    "channel", "coin", "n_intervals", "p99_s", "threshold_s", "basis",
    "n_gaps", "total_s", "max_s",
]


def stats(con) -> list[dict]:
    """Una riga per partizione: soglia usata, quanti buchi, quanto tempo."""
    rows = con.execute(
        f"""
        SELECT t.channel, t.coin, t.n_intervals, t.p99_s, t.threshold_s, t.basis,
               coalesce(g.n_gaps, 0)   AS n_gaps,
               coalesce(g.total_s, 0)  AS total_s,
               g.max_s
        FROM gap_thresholds t
        LEFT JOIN (
            SELECT channel, coin, count(*) AS n_gaps,
                   sum(duration_s) AS total_s, max(duration_s) AS max_s
            FROM derived_gaps GROUP BY 1, 2
        ) g USING (channel, coin)
        ORDER BY t.channel, t.coin
        """
    ).fetchall()
    return [dict(zip(STAT_COLUMNS, r)) for r in rows]


def fetch(con) -> list[DerivedGap]:
    rows = con.execute(
        "SELECT channel, coin, start_ns, end_ns FROM derived_gaps "
        "ORDER BY start_ns, channel, coin"
    ).fetchall()
    return [DerivedGap(*r) for r in rows]


def write_parquet(con, path: str) -> int:
    """Elenco completo su disco. Il report ne stampa una parte: il resto deve
    restare interrogabile senza rifare la scansione."""
    con.execute(
        f"COPY (SELECT * FROM derived_gaps ORDER BY start_ns, channel, coin) "
        f"TO {sql_str(path)} (FORMAT PARQUET, COMPRESSION zstd)"
    )
    return con.execute("SELECT count(*) FROM derived_gaps").fetchone()[0]


# ---------------------------------------------------------------------------
# 2. Riconciliazione fra i due mondi
# ---------------------------------------------------------------------------


def _overlap_ns(spans: list[tuple[int, int]], starts: list[int],
                a: int, b: int) -> int:
    """Nanosecondi di [a, b) coperti da `spans`, che devono essere disgiunti e
    ordinati (`merge_spans` lo garantisce). Bisect e non scan: il registro reale
    ha centinaia di finestre e i buchi derivati possono essere migliaia."""
    total = 0
    i = bisect_right(starts, b) - 1
    while i >= 0 and spans[i][1] > a:
        total += max(0, min(spans[i][1], b) - max(spans[i][0], a))
        i -= 1
    return total


def _intersects(spans: list[tuple[int, int]], starts: list[int],
                a: int, b: int) -> bool:
    i = bisect_left(starts, b) - 1
    return i >= 0 and spans[i][1] > a


def reconcile(gaps: list[DerivedGap], windows: list[Window],
              limit: int = 40) -> dict:
    """Confronta cio' che i dati mostrano con cio' che il registro dichiara.

    Tre insiemi, che rispondono a tre domande diverse:

    - **spiegati**: assenza nei dati che una finestra registrata copre almeno in
      parte. Per ognuno, di quanto la finestra sottostima la durata osservata —
      la misura diretta della qualita' del registro, ed e' il numero che deve
      scendere dopo la correzione del 14 agosto 2026.
    - **non spiegati**: assenza nei dati che nessuna finestra tocca. Sono i buchi
      che l'esclusione basata sul registro lasciava dentro le statistiche.
    - **finestre senza assenza**: il registro dichiara un'interruzione che nei
      dati non si vede. Non e' necessariamente un errore: una disconnessione piu'
      corta della soglia di rilevamento e' invisibile per costruzione, e la
      colonna `sotto_soglia` lo distingue da una finestra lunga che avrebbe
      dovuto lasciare traccia.

    La copertura si calcola sulle finestre FUSE: nel registro un `resume` per
    canale sta dentro la finestra aggregata che lo ha generato, e sommare le
    intersezioni una per finestra conterebbe due volte lo stesso tempo,
    producendo sottostime negative.

    Le liste dettagliate sono ordinate per durata decrescente e troncate a
    `limit`; i conteggi in `totali` sono sempre completi.
    """
    span_list = merge_windows(windows)
    span_starts = [s for s, _ in span_list]

    gap_spans = merge_spans([(g.start_ns, g.end_ns) for g in gaps])
    gap_starts = [s for s, _ in gap_spans]

    spiegati: list[dict] = []
    non_spiegati: list[dict] = []
    for g in gaps:
        covered = _overlap_ns(span_list, span_starts, g.start_ns, g.end_ns)
        riga = {
            "channel": g.channel,
            "coin": g.coin,
            "start_utc": utc(g.start_ns),
            "end_utc": utc(g.end_ns),
            "start_ns": g.start_ns,
            "end_ns": g.end_ns,
            "duration_s": g.duration_s,
        }
        if covered <= 0:
            non_spiegati.append(riga)
            continue
        w = _principale(windows, g)
        riga.update(
            {
                "coperto_s": covered / 1e9,
                "sottostima_s": (g.end_ns - g.start_ns - covered) / 1e9,
                "copertura_pct": 100.0 * covered / (g.end_ns - g.start_ns),
                "reason": (w.reason if w else "")[:60],
                "event": w.event if w else "",
                "origin": w.origin if w else "",
            }
        )
        spiegati.append(riga)

    senza_assenza: list[dict] = []
    for w in windows:
        a, b = w.start_ms * 1_000_000, w.end_ms * 1_000_000
        if b > a and _intersects(gap_spans, gap_starts, a, b):
            continue
        senza_assenza.append(
            {
                "start_utc": utc(a),
                "duration_s": w.duration_s,
                "event": w.event,
                "origin": w.origin,
                "still_open": w.still_open,
                "reason": w.reason[:60],
            }
        )

    tot_sotto = sum(r["sottostima_s"] for r in spiegati)
    return {
        "spiegati": sorted(spiegati, key=lambda r: -r["duration_s"])[:limit],
        "non_spiegati": sorted(non_spiegati, key=lambda r: -r["duration_s"])[:limit],
        "finestre_senza_assenza": sorted(
            senza_assenza, key=lambda r: -r["duration_s"]
        )[:limit],
        "sottostima_per_giorno": _per_giorno(spiegati),
        "totali": {
            "n_derivati": len(gaps),
            "n_spiegati": len(spiegati),
            "n_non_spiegati": len(non_spiegati),
            "durata_non_spiegata_s": sum(r["duration_s"] for r in non_spiegati),
            "n_finestre": len(windows),
            "n_finestre_senza_assenza": len(senza_assenza),
            "sottostima_totale_s": tot_sotto,
            "sottostima_media_s": tot_sotto / len(spiegati) if spiegati else 0.0,
        },
    }


def _principale(windows: list[Window], g: DerivedGap) -> Window | None:
    """La finestra che copre la fetta piu' grande del buco: e' quella che ne
    dichiara la causa. Scan lineare, ma solo sui buchi gia' risultati spiegati:
    quelli sono pochi per definizione, e la chiarezza vale piu' del bisect."""
    best, best_ov = None, 0
    for w in windows:
        ov = min(w.end_ms * 1_000_000, g.end_ns) - max(
            w.start_ms * 1_000_000, g.start_ns
        )
        if ov > best_ov:
            best, best_ov = w, ov
    return best


def _per_giorno(spiegati: list[dict]) -> list[dict]:
    """Sottostima aggregata per giorno UTC.

    Aggregata per giorno e non "prima/dopo una data": la data della correzione
    e' un fatto che chi legge conosce, e un taglio cablato nel codice
    trasformerebbe una serie temporale in un verdetto. Cosi' si vede anche se il
    miglioramento e' arrivato per gradi o di colpo.
    """
    per_day: dict[str, list[float]] = {}
    for r in spiegati:
        per_day.setdefault(_day(r["start_ns"]), []).append(r["sottostima_s"])
    out = []
    for day in sorted(per_day):
        vals = sorted(per_day[day])
        out.append(
            {
                "giorno": day,
                "n": len(vals),
                "sottostima_mediana_s": vals[len(vals) // 2],
                "sottostima_max_s": vals[-1],
                "sottostima_totale_s": sum(vals),
            }
        )
    return out
