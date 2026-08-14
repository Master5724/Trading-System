"""Adattatore fra il registro dei buchi del collector e le query del catalogo.

Il parser del JSONL NON viene riscritto qui: `collector.gaps.load_windows` e'
gia' l'unico posto in cui si decide cosa significa una riga di quel file, e
averne due copie e' esattamente il tipo di duplicazione che produce due
verita' diverse sullo stesso dato.

Qui si aggiunge solo cio' che serve al catalogo: risolvere le finestre ancora
aperte contro un istante di riferimento, e materializzarle in una tabella
DuckDB.
"""

from __future__ import annotations

from dataclasses import dataclass

from collector.gaps import load_windows


@dataclass(frozen=True)
class Window:
    start_ms: int
    end_ms: int
    reason: str
    channels: tuple[str, ...]
    still_open: bool

    @property
    def duration_s(self) -> float:
        return (self.end_ms - self.start_ms) / 1000.0


def load(path: str, now_ms: int) -> list[Window]:
    """Finestre di disconnessione con estremi risolti.

    Una finestra senza `close` (processo ucciso mentre era scollegato, o
    scollegato adesso) viene estesa fino a `now_ms`. E' il default
    conservativo di CLAUDE.md invariante 6: un periodo che non sappiamo
    classificare va marcato, non ignorato.
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
    return out


def materialize(con, windows: list[Window], table: str = "gap_windows") -> None:
    """Crea la tabella DuckDB delle finestre. Vuota se il registro non esiste:
    l'assenza del file non e' un errore fatale, ma il report deve dirlo."""
    con.execute(
        f"CREATE OR REPLACE TABLE {table} ("
        "  start_ms BIGINT, end_ms BIGINT, reason VARCHAR,"
        "  channels VARCHAR, still_open BOOLEAN)"
    )
    if not windows:
        return
    con.executemany(
        f"INSERT INTO {table} VALUES (?, ?, ?, ?, ?)",
        [
            (w.start_ms, w.end_ms, w.reason, ",".join(w.channels), w.still_open)
            for w in windows
        ],
    )
