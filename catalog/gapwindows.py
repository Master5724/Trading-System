"""Adattatore fra il registro dei buchi del collector e le query del catalogo.

Il parser del JSONL NON viene riscritto qui: `collector.gaps.load_windows` e'
gia' l'unico posto in cui si decide cosa significa una riga di quel file, e
averne due copie e' esattamente il tipo di duplicazione che produce due
verita' diverse sullo stesso dato.

Qui si aggiunge solo cio' che serve al catalogo: risolvere le finestre ancora
aperte contro un istante di riferimento, materializzarle in una tabella
DuckDB, e — vedi sotto — garantire che nessun record che dichiara una durata
esca dal conto.

**Il contratto di lettura di `_gaps.jsonl`.** Si considerano TUTTI i record
che dichiarano una durata, qualunque sia il valore di `event`. Il file e'
append-only e additivo: oltre a `open`/`close` contiene `resume`, `reconnect`,
`reopen` e almeno una riga `manual` scritta a mano (il blackout da 4959 s del
14 agosto 2026, l'interruzione piu' lunga dell'intera raccolta). Un lettore che
filtra su `event == "close"` salta quella riga in silenzio e sottostima il
tempo scollegato di oltre un'ora — e' successo davvero in un'analisi.

Il parser condiviso gestisce gia' `manual`, ma "gestito oggi" non e' un
contratto: un evento nuovo introdotto domani sparirebbe di nuovo senza che
nessuno se ne accorga. Per questo `load()` fa una seconda passata di sola
verifica sul file: ogni record con una durata dev'essere coperto da una
finestra: quelli che non lo sono vengono recuperati come finestre a se' stanti
e contati nell'audit, invece di essere ignorati.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from collector.gaps import load_windows

# Origine di una finestra. `ORIGIN_REGISTRY` = ricostruita dal parser condiviso
# (open + close/resume/...). `ORIGIN_RECOVERED` = record autoconclusivo che il
# parser non ha trasformato in finestra, recuperato qui.
ORIGIN_REGISTRY = "registro"
ORIGIN_RECOVERED = "recuperato"


@dataclass(frozen=True)
class Window:
    start_ms: int
    end_ms: int
    reason: str
    channels: tuple[str, ...]
    still_open: bool
    origin: str = ORIGIN_REGISTRY
    event: str = "open"

    @property
    def duration_s(self) -> float:
        return (self.end_ms - self.start_ms) / 1000.0


def _declared_duration_records(path: str) -> list[dict]:
    """I record del registro che dichiarano una durata, con gli estremi risolti.

    "Dichiara una durata" significa: ha `start_ms` e ha `end_ms` oppure
    `duration_s`. Nessun filtro sul tipo di evento — e' il punto. Le righe
    illeggibili si saltano (un JSONL troncato da un kill non deve rendere
    inutilizzabile il resto), quelle senza durata pure: un `open` spaiato o un
    `reconnect` non dicono quanto e' durato il buco, e la loro semantica sta
    nel parser condiviso, non qui.
    """
    out: list[dict] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    start = int(rec["start_ms"])
                except (ValueError, KeyError, TypeError):
                    continue
                end = _resolve_end(rec, start)
                if end is None:
                    continue
                channels = rec.get("channels") or (
                    [rec["channel"]] if rec.get("channel") else []
                )
                out.append(
                    {
                        "event": str(rec.get("event") or "?"),
                        "start_ms": start,
                        "end_ms": max(end, start),
                        "reason": str(rec.get("reason") or ""),
                        "channels": tuple(str(c) for c in channels),
                    }
                )
    except FileNotFoundError:
        return []
    return out


def _resolve_end(rec: dict, start: int) -> int | None:
    """`end_ms` se c'e', altrimenti ricostruito da `duration_s`. I due campi
    coesistono su quasi tutte le righe; `end_ms` vince perche' e' l'istante
    misurato, mentre `duration_s` e' un derivato arrotondato."""
    for key, to_end in (("end_ms", lambda v: int(v)),
                        ("duration_s", lambda v: start + int(round(float(v) * 1000)))):
        value = rec.get(key)
        if value is None:
            continue
        try:
            return to_end(value)
        except (TypeError, ValueError):
            continue
    return None


def load(path: str, now_ms: int) -> list[Window]:
    """Finestre di disconnessione con estremi risolti."""
    return load_with_audit(path, now_ms)[0]


def load_with_audit(path: str, now_ms: int) -> tuple[list[Window], dict]:
    """Come `load`, piu' il conto di cosa e' stato letto dal registro.

    Una finestra senza `close` (processo ucciso mentre era scollegato, o
    scollegato adesso) viene estesa fino a `now_ms`. E' il default
    conservativo di CLAUDE.md invariante 6: un periodo che non sappiamo
    classificare va marcato, non ignorato.

    L'audit non e' decorazione: `record_con_durata` e `recuperati` sono i due
    numeri che permettono a chi legge il report di verificare che il contratto
    di lettura sia stato rispettato, senza riaprire il JSONL.
    """
    out: list[Window] = []
    for g in load_windows(path):
        still_open = g.end_ms is None
        end = g.end_ms if g.end_ms is not None else now_ms
        # Una finestra aperta prima di `now_ms` ma chiusa dopo non esiste; una
        # con end < start sarebbe un registro corrotto: la si tiene puntiforme
        # invece di generare intervalli negativi che spariscono dalle somme.
        out.append(
            Window(
                start_ms=g.start_ms,
                end_ms=max(end, g.start_ms),
                reason=g.reason,
                channels=tuple(g.channels),
                still_open=still_open,
            )
        )

    records = _declared_duration_records(path)
    recovered: list[Window] = []
    for rec in records:
        if _covered_by(out, rec):
            continue
        # Un record scoperto viene applicato a tutti i canali (`channels`
        # vuoto): se non sappiamo ricostruirne la semantica, marcare piu' del
        # necessario e' l'errore giusto da fare.
        recovered.append(
            Window(
                start_ms=rec["start_ms"],
                end_ms=rec["end_ms"],
                reason=rec["reason"] or f"evento:{rec['event']}",
                channels=rec["channels"],
                still_open=False,
                origin=ORIGIN_RECOVERED,
                event=rec["event"],
            )
        )
    out.extend(recovered)
    out.sort(key=lambda w: (w.start_ms, w.end_ms))

    audit = {
        "record_con_durata": len(records),
        "recuperati": len(recovered),
        "eventi_con_durata": _count_events(records),
        "eventi_recuperati": _count_events(
            [{"event": w.event} for w in recovered]
        ),
    }
    return out, audit


def _covered_by(windows: list[Window], rec: dict) -> bool:
    """Il record e' gia' dentro una finestra ricostruita dal parser?

    Copertura per contenimento, non per sovrapposizione: un `resume` per canale
    sta dentro la finestra aggregata che lo ha generato ed e' quindi gia'
    contato, mentre un record che sfora — o che sta altrove nel tempo — porta
    informazione che nessuna finestra esprime e va recuperato.
    """
    return any(
        w.start_ms <= rec["start_ms"] and w.end_ms >= rec["end_ms"] for w in windows
    )


def _count_events(records: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for rec in records:
        counts[rec["event"]] = counts.get(rec["event"], 0) + 1
    return dict(sorted(counts.items()))


def materialize(con, windows: list[Window], table: str = "gap_windows") -> None:
    """Crea la tabella DuckDB delle finestre. Vuota se il registro non esiste:
    l'assenza del file non e' un errore fatale, ma il report deve dirlo."""
    con.execute(
        f"CREATE OR REPLACE TABLE {table} ("
        "  start_ms BIGINT, end_ms BIGINT, reason VARCHAR,"
        "  channels VARCHAR, still_open BOOLEAN, origin VARCHAR, event VARCHAR)"
    )
    if not windows:
        return
    con.executemany(
        f"INSERT INTO {table} VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (w.start_ms, w.end_ms, w.reason, ",".join(w.channels), w.still_open,
             w.origin, w.event)
            for w in windows
        ],
    )
