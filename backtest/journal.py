"""Il giornale operazioni e la curva di equity, scritti in modo riproducibile.

Due file CSV. Il giornale ha una riga per **ogni** decisione che ha toccato il
conto o che avrebbe potuto: fill, rifiuti, regolamenti di funding. I rifiuti
non sono rumore da nascondere — sono la prova che il motore ha saltato quello
che i dati non determinavano, e un giornale senza rifiuti su dati veri e' un
giornale che sta inventando.

**Provenienza.** Ogni riga di fill porta `ref`: `file#ts_local_ns` per un fill
taker (lo snapshot camminato), `tid=...@ts_local_ns` per un fill maker (il
trade che ha attraversato il livello). E' il requisito di tracciabilita': da
una riga del giornale si torna alla riga di dati che l'ha prodotta.

**Riproducibilita'.** I float si scrivono con `repr`, che fa round-trip esatto
in Python: rileggendo il file si riottiene lo stesso bit. Nessuna
formattazione a numero fisso di decimali — quella perde informazione proprio
dove serve, sui residui di conservazione. Il separatore di riga e' `\\n`
esplicito, cosi' il file e' identico su ogni piattaforma.
"""

from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass, field
from datetime import datetime, timezone

FILL = "fill"
REJECT = "reject"
FUNDING = "funding"

JOURNAL_COLUMNS = (
    "bar_idx", "decision_ts_ns", "decision_utc", "event", "coin", "side",
    "kind", "liquidity", "tag", "size_ordered", "size_filled", "px",
    "notional_nominal", "notional_executed", "fee", "spread_cost",
    "impact_cost", "realized_pnl", "funding", "position_after",
    "book_ts_ns", "book_age_ms", "book_src", "trade_tid", "trade_ts_ns",
    "trade_time_ms", "reason", "ref",
)


def utc(ts_ns: int) -> str:
    return datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S.%f"
    )[:-3]


def num(x: float | int | None) -> str:
    """Un numero come stringa che rilegge identico. Vuoto se non esiste: uno
    zero al posto di un dato mancante e' una bugia che nessuno nota."""
    if x is None:
        return ""
    if isinstance(x, int):
        return str(x)
    return repr(float(x))


@dataclass
class Journal:
    """Accumula le righe e le scrive. Non decide niente."""

    rows: list[dict] = field(default_factory=list)
    equity_rows: list[dict] = field(default_factory=list)
    equity_columns: tuple[str, ...] = ()

    def add(self, **row) -> None:
        unknown = set(row) - set(JOURNAL_COLUMNS)
        if unknown:
            raise KeyError(f"colonne non previste nel giornale: {sorted(unknown)}")
        self.rows.append(row)

    def add_equity(self, row: dict) -> None:
        if not self.equity_columns:
            self.equity_columns = tuple(row)
        elif tuple(row) != self.equity_columns:
            raise KeyError("la curva di equity ha cambiato colonne a meta' run")
        self.equity_rows.append(row)

    # -- serializzazione -------------------------------------------------------

    def journal_csv(self) -> str:
        return _csv(JOURNAL_COLUMNS, self.rows)

    def equity_csv(self) -> str:
        return _csv(self.equity_columns, self.equity_rows)

    def write(self, journal_path: str, equity_path: str) -> None:
        for path, text in ((journal_path, self.journal_csv()),
                           (equity_path, self.equity_csv())):
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(text)

    def digest(self) -> str:
        """Impronta dei due file. Due run che producono lo stesso digest hanno
        prodotto gli stessi byte: e' cosi' che il test di determinismo si
        riduce a un confronto di stringhe."""
        h = hashlib.sha256()
        h.update(self.journal_csv().encode("utf-8"))
        h.update(self.equity_csv().encode("utf-8"))
        return h.hexdigest()

    # -- letture usate dai test e dal report ------------------------------------

    def total(self, column: str, event: str | None = None) -> float:
        """Somma di una colonna sulle righe di un tipo. E' il modo in cui il
        test di conservazione ricalcola l'identita' dal giornale invece che dai
        contatori del portafoglio."""
        tot = 0.0
        for r in self.rows:
            if event is not None and r.get("event") != event:
                continue
            v = r.get(column)
            if v is not None:
                tot += float(v)
        return tot

    def count(self, event: str) -> int:
        return sum(1 for r in self.rows if r.get("event") == event)


def _csv(columns: tuple[str, ...], rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(columns)
    for r in rows:
        w.writerow([_cell(r.get(c)) for c in columns])
    return buf.getvalue()


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return repr(v)
    return str(v)
