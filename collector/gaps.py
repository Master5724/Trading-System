"""
Registro delle finestre di disconnessione (CLAUDE.md, invariante 6).

Il WebSocket di Hyperliquid non espone numeri di sequenza sui dati di mercato:
non esiste modo, guardando i Parquet, di distinguere "in quel minuto non e'
successo niente" da "in quel minuto eravamo scollegati". L'unico posto in cui
quell'informazione esiste e' il processo che si e' scollegato, ed e' per questo
che va scritta qui invece che dedotta dopo.

Formato: JSONL append-only, un evento per riga, due eventi per finestra.

    {"event":"open","start_ms":..,"start_iso":..,"reason":"..","channels":[..]}
    {"event":"close","start_ms":..,"end_ms":..,"end_iso":..,"duration_s":..}

Perche' due righe e non una a finestra chiusa: se il processo viene ucciso
mentre e' scollegato, una riga scritta solo alla chiusura non esisterebbe, e
quel buco — il piu' lungo, quello che conta di piu' — sparirebbe dal registro.
Con due righe, un `open` spaiato e' esso stesso il dato: finestra mai chiusa.

Ogni riga viene fsync-ata. Un registro dei buchi che si perde in un buffer del
filesystem quando la macchina si riavvia non serve a niente.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


@dataclass
class Gap:
    """Una finestra in cui il collector non stava ricevendo dati.

    `end_ms is None` significa finestra ancora aperta: o il collector e'
    scollegato adesso, o il processo e' morto senza riconnettersi. In entrambi
    i casi il consumatore a valle deve trattarla come aperta fino a ora.
    """

    start_ms: int
    reason: str
    channels: list[str] = field(default_factory=list)
    end_ms: int | None = None

    @property
    def duration_s(self) -> float | None:
        if self.end_ms is None:
            return None
        return (self.end_ms - self.start_ms) / 1000.0

    def covers(self, ts_ms: int, now_ms: int | None = None) -> bool:
        """True se `ts_ms` cade dentro la finestra. Una finestra aperta copre
        tutto fino a `now_ms`: e' il default conservativo, i dati di un periodo
        che non sappiamo classificare vanno scartati, non tenuti."""
        end = self.end_ms if self.end_ms is not None else (
            now_ms if now_ms is not None else int(time.time() * 1000)
        )
        return self.start_ms <= ts_ms <= end


class GapRecorder:
    """Scrive le finestre di disconnessione su un JSONL append-only.

    All'avvio rilegge il file: se il processo precedente e' morto con una
    finestra aperta, quella finestra viene adottata e chiusa alla prima
    riconnessione, invece di restare aperta per sempre.
    """

    def __init__(self, path: str, clock=time.time):
        self.path = path
        self._clock = clock
        self._lock = threading.Lock()
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._open: Gap | None = _last_unclosed(path)

    def _now_ms(self) -> int:
        return int(self._clock() * 1000)

    def _append(self, record: dict) -> None:
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, separators=(",", ":")) + "\n")
            f.flush()
            os.fsync(f.fileno())

    @property
    def current(self) -> Gap | None:
        """La finestra aperta in questo momento, se c'e'."""
        return self._open

    def mark_disconnected(self, reason: str, channels: list[str] | None = None) -> Gap:
        """Apre una finestra. Chiamata piu' volte di fila (riconnessioni che
        falliscono a ripetizione) non la riapre: il buco e' uno solo, dal primo
        distacco fino alla prima riconnessione riuscita."""
        with self._lock:
            if self._open is not None:
                return self._open
            gap = Gap(
                start_ms=self._now_ms(),
                reason=reason,
                channels=sorted(channels or []),
            )
            self._open = gap
            self._append(
                {
                    "event": "open",
                    "start_ms": gap.start_ms,
                    "start_iso": _iso(gap.start_ms),
                    "reason": gap.reason,
                    "channels": gap.channels,
                }
            )
            return gap

    def mark_connected(self) -> Gap | None:
        """Chiude la finestra aperta, se c'e'. Ritorna la finestra chiusa."""
        with self._lock:
            gap = self._open
            if gap is None:
                return None
            gap.end_ms = self._now_ms()
            self._open = None
            self._append(
                {
                    "event": "close",
                    "start_ms": gap.start_ms,
                    "end_ms": gap.end_ms,
                    "end_iso": _iso(gap.end_ms),
                    "duration_s": round(gap.duration_s or 0.0, 3),
                    "reason": gap.reason,
                    "channels": gap.channels,
                }
            )
            return gap


def load_windows(path: str) -> list[Gap]:
    """Rilegge il registro. Righe illeggibili vengono saltate: un JSONL
    troncato da un kill a meta' riga non deve rendere inutilizzabile lo
    storico dei buchi precedenti."""
    gaps: dict[int, Gap] = {}
    order: list[int] = []
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
                if rec.get("event") == "open":
                    if start not in gaps:
                        gaps[start] = Gap(
                            start_ms=start,
                            reason=rec.get("reason", ""),
                            channels=list(rec.get("channels") or []),
                        )
                        order.append(start)
                elif rec.get("event") == "close" and start in gaps:
                    gaps[start].end_ms = int(rec["end_ms"])
    except FileNotFoundError:
        return []
    return [gaps[s] for s in order]


def _last_unclosed(path: str) -> Gap | None:
    windows = load_windows(path)
    for gap in reversed(windows):
        if gap.end_ms is None:
            return gap
    return None
