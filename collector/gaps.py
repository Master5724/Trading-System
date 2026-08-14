"""
Registro delle finestre di disconnessione (CLAUDE.md, invariante 6).

Il WebSocket di Hyperliquid non espone numeri di sequenza sui dati di mercato:
non esiste modo, guardando i Parquet, di distinguere "in quel minuto non e'
successo niente" da "in quel minuto eravamo scollegati". L'unico posto in cui
quell'informazione esiste e' il processo che si e' scollegato, ed e' per questo
che va scritta qui invece che dedotta dopo.

Formato: JSONL append-only, un evento per riga.

    {"event":"open",     "start_ms":..,"start_iso":..,"reason":..,"channels":[..],"cause":".."}
    {"event":"reconnect","start_ms":..,"reconnect_ms":..,"reconnect_iso":..}
    {"event":"resume",   "start_ms":..,"channel":"trades","end_ms":..,"duration_s":..}
    {"event":"reopen",   "start_ms":..,"at_ms":..,"reason":..}
    {"event":"close",    "start_ms":..,"end_ms":..,"end_iso":..,"duration_s":..}
    {"event":"manual",   "start_ms":..,"end_ms":..,..}   (riga scritta a mano)

Perche' `open` e `close` sono due righe e non una a finestra chiusa: se il
processo viene ucciso mentre e' scollegato, una riga scritta solo alla chiusura
non esisterebbe, e quel buco — il piu' lungo, quello che conta di piu' —
sparirebbe dal registro. Con due righe, un `open` spaiato e' esso stesso il
dato: finestra mai chiusa.

**Quando si chiude una finestra.** Alla ripresa dei dati, per canale, non alla
riconnessione della socket. Una socket che si riapre non e' un dato che arriva:
il 2026-08-08 il canale `trades` e' rimasto muto 92s dopo una riconnessione
mentre gli altri canali erano gia' ripartiti, e il registro — che chiudeva alla
riconnessione — dichiarava un buco di 1.5s. Una finestra dichiarata chiusa
troppo presto e' peggio di una finestra non registrata: il consumatore a valle
legge dati mancanti come dati validi.

**Congelamenti.** Un processo fermo (swap, OOM, macchina sospesa) con la socket
TCP ancora aperta non produce nessun errore da nessuna parte: e' invisibile per
costruzione. L'unico segnale disponibile e' il salto dell'orologio monotono fra
due risvegli del watchdog — vedi `FreezeDetector`.

Ogni riga viene fsync-ata. Un registro dei buchi che si perde in un buffer del
filesystem quando la macchina si riavvia non serve a niente.

I campi sono additivi: le righe scritte dalle versioni precedenti restano
leggibili, e i campi nuovi sono tutti opzionali in lettura.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

# Valori del campo "cause". Le righe scritte prima della sua introduzione non
# ce l'hanno e vengono lette come CAUSE_DISCONNECT.
CAUSE_DISCONNECT = "disconnect"
CAUSE_FREEZE = "process_freeze"
CAUSE_MANUAL = "manual"

# Soglia di default del rilevatore di congelamento: un ritardo maggiore di
# questo fra due risvegli del watchdog non e' jitter dello scheduler.
DEFAULT_FREEZE_JUMP_SECONDS = 60.0


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


@dataclass
class Gap:
    """Una finestra in cui il collector non stava ricevendo dati.

    `end_ms is None` significa finestra ancora aperta: o il collector e'
    scollegato adesso, o un canale non ha ancora ripreso a produrre, o il
    processo e' morto senza riconnettersi. In tutti i casi il consumatore a
    valle deve trattarla come aperta fino a ora.

    `channel_ends` tiene l'istante in cui ogni canale ha ripreso a produrre.
    La finestra aggregata si chiude quando ha ripreso l'ultimo: `end_ms` e'
    quindi il massimo dei ritorni, non l'istante della riconnessione.
    """

    start_ms: int
    reason: str
    channels: list[str] = field(default_factory=list)
    end_ms: int | None = None
    cause: str = CAUSE_DISCONNECT
    # Istante in cui la socket e' tornata su. Serve a misurare quanto del buco
    # e' avvenuto a socket viva: e' il numero che il registro precedente
    # nascondeva.
    reconnect_ms: int | None = None
    channel_ends: dict[str, int] = field(default_factory=dict)

    @property
    def duration_s(self) -> float | None:
        if self.end_ms is None:
            return None
        return (self.end_ms - self.start_ms) / 1000.0

    @property
    def pending_channels(self) -> list[str]:
        """Canali dichiarati nella finestra che non hanno ancora ripreso."""
        return sorted(c for c in self.channels if c not in self.channel_ends)

    def end_for(self, channel: str) -> int | None:
        """Fine della finestra per un singolo canale, `None` se quel canale non
        ha ancora ripreso.

        Un canale non dichiarato nella finestra ricade sul valore aggregato:
        non sappiamo se fosse coinvolto, e il default e' conservativo."""
        if not self.channels or channel not in self.channels:
            return self.end_ms
        return self.channel_ends.get(channel)

    def duration_for(self, channel: str) -> float | None:
        end = self.end_for(channel)
        if end is None:
            return None
        return (end - self.start_ms) / 1000.0

    def covers(self, ts_ms: int, now_ms: int | None = None,
               channel: str | None = None) -> bool:
        """True se `ts_ms` cade dentro la finestra. Una finestra aperta copre
        tutto fino a `now_ms`: e' il default conservativo, i dati di un periodo
        che non sappiamo classificare vanno scartati, non tenuti.

        Con `channel` la domanda diventa per canale: dopo una riconnessione i
        canali ripartono in momenti diversi, e tenere il dato di un canale gia'
        vivo mentre se ne scarta un altro ancora muto e' l'unica risposta
        onesta."""
        end = self.end_for(channel) if channel is not None else self.end_ms
        if end is None:
            end = now_ms if now_ms is not None else int(time.time() * 1000)
        return self.start_ms <= ts_ms <= end


class GapRecorder:
    """Scrive le finestre di disconnessione su un JSONL append-only.

    All'avvio rilegge il file: se il processo precedente e' morto con una
    finestra aperta, quella finestra viene adottata e chiusa quando i dati
    riprendono, invece di restare aperta per sempre.
    """

    def __init__(self, path: str, clock=time.time):
        self.path = path
        self._clock = clock
        self._lock = threading.RLock()
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
        distacco fino a quando i dati riprendono davvero.

        Se pero' dei canali erano gia' ripartiti dentro questa finestra, un
        nuovo distacco li rimette muti: i loro ritorni vengono annullati, se no
        la finestra si chiuderebbe contando una ripresa che non vale piu'."""
        with self._lock:
            if self._open is not None:
                self._reset_resumed_locked(reason)
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
                    "cause": gap.cause,
                }
            )
            return gap

    def _reset_resumed_locked(self, reason: str) -> None:
        gap = self._open
        if gap is None or not gap.channel_ends:
            return
        at = self._now_ms()
        gap.channel_ends.clear()
        gap.reconnect_ms = None
        self._append(
            {
                "event": "reopen",
                "start_ms": gap.start_ms,
                "at_ms": at,
                "at_iso": _iso(at),
                "reason": reason,
            }
        )

    def mark_reconnected(self) -> Gap | None:
        """La socket e' tornata su. **Non chiude la finestra**: un canale puo'
        restare muto ancora a lungo, e chiudere qui e' esattamente il difetto
        che questo modulo esiste per non avere. Ritorna la finestra ancora
        aperta, se c'e'."""
        with self._lock:
            gap = self._open
            if gap is None:
                return None
            gap.reconnect_ms = self._now_ms()
            self._append(
                {
                    "event": "reconnect",
                    "start_ms": gap.start_ms,
                    "reconnect_ms": gap.reconnect_ms,
                    "reconnect_iso": _iso(gap.reconnect_ms),
                }
            )
            return gap

    def mark_data(self, channel: str) -> Gap | None:
        """Un messaggio di dati e' arrivato su `channel`: e' questo l'evento
        che chiude un buco. Ritorna la finestra se questo messaggio l'ha
        chiusa del tutto, altrimenti `None`.

        Percorso caldo — viene chiamata per ogni messaggio ricevuto, migliaia
        al minuto — quindi il caso "nessuna finestra aperta" esce prima del
        lock."""
        if self._open is None:
            return None
        with self._lock:
            gap = self._open
            if gap is None:
                return None
            now = self._now_ms()
            if not gap.channels:
                # Finestra senza elenco di canali (registro vecchio, o apertura
                # senza elenco): non c'e' modo di ragionare per canale, il primo
                # dato che arriva la chiude.
                gap.channel_ends[channel] = now
                return self._close_locked(now)
            if channel not in gap.channels or channel in gap.channel_ends:
                return None
            gap.channel_ends[channel] = now
            self._append(
                {
                    "event": "resume",
                    "start_ms": gap.start_ms,
                    "channel": channel,
                    "end_ms": now,
                    "end_iso": _iso(now),
                    "duration_s": round((now - gap.start_ms) / 1000.0, 3),
                }
            )
            if gap.pending_channels:
                return None
            return self._close_locked(now)

    def mark_connected(self) -> Gap | None:
        """Chiude la finestra su tutti i canali all'istante corrente.

        Non e' piu' il collector a chiamarla — la riconnessione della socket
        non e' una ripresa dei dati, vedi `mark_reconnected` — ma resta per chi
        deve chiudere una finestra a mano, e perche' toglierla romperebbe i
        chiamanti esistenti."""
        with self._lock:
            gap = self._open
            if gap is None:
                return None
            now = self._now_ms()
            for ch in gap.channels:
                gap.channel_ends.setdefault(ch, now)
            return self._close_locked(now)

    def _close_locked(self, end_ms: int) -> Gap:
        gap = self._open
        assert gap is not None
        gap.end_ms = end_ms
        self._open = None
        record = {
            "event": "close",
            "start_ms": gap.start_ms,
            "end_ms": end_ms,
            "end_iso": _iso(end_ms),
            "duration_s": round(gap.duration_s or 0.0, 3),
            "reason": gap.reason,
            "channels": gap.channels,
            "cause": gap.cause,
        }
        if gap.reconnect_ms is not None:
            record["reconnect_ms"] = gap.reconnect_ms
            record["reconnect_iso"] = _iso(gap.reconnect_ms)
            # Quanto del buco e' avvenuto a socket gia' viva. Se questo numero
            # e' grande, la socket stava mentendo.
            record["silence_after_reconnect_s"] = round(
                (end_ms - gap.reconnect_ms) / 1000.0, 3
            )
        if gap.channel_ends:
            record["per_channel_duration_s"] = {
                ch: round((end - gap.start_ms) / 1000.0, 3)
                for ch, end in sorted(gap.channel_ends.items())
            }
        self._append(record)
        return gap

    def record_freeze(self, elapsed_s: float, jump_s: float,
                      channels: list[str] | None = None) -> Gap:
        """Registra un congelamento del processo: finestra chiusa, scritta
        tutta in una volta a posteriori.

        Un congelamento si scopre solo quando e' gia' finito, quindi qui non
        c'e' il rischio di perdere una finestra mai chiusa e le due righe si
        possono scrivere insieme.

        La finestra parte dal risveglio precedente del watchdog (`elapsed_s`
        fa), non dal momento in cui il processo si e' fermato davvero: quello
        non e' osservabile. Sovrastimare di un intervallo di watchdog e' la
        direzione giusta in cui sbagliare.

        Non tocca l'eventuale finestra aperta: un congelamento e' un fatto
        indipendente dalla disconnessione che potrebbe averlo seguito, e due
        finestre sovrapposte nel registro sono l'unione dei periodi da
        scartare."""
        with self._lock:
            end_ms = self._now_ms()
            start_ms = end_ms - int(round(elapsed_s * 1000))
            gap = Gap(
                start_ms=start_ms,
                reason=(f"process freeze: watchdog fermo per {elapsed_s:.1f}s "
                        f"(salto di {jump_s:.1f}s sull'orologio monotono)"),
                channels=sorted(channels or []),
                end_ms=end_ms,
                cause=CAUSE_FREEZE,
            )
            common = {
                "start_ms": gap.start_ms,
                "reason": gap.reason,
                "channels": gap.channels,
                "cause": CAUSE_FREEZE,
                "monotonic_jump_s": round(jump_s, 3),
            }
            self._append({"event": "open", "start_iso": _iso(gap.start_ms), **common})
            self._append(
                {
                    "event": "close",
                    "end_ms": gap.end_ms,
                    "end_iso": _iso(gap.end_ms),
                    "duration_s": round(gap.duration_s or 0.0, 3),
                    **common,
                }
            )
            return gap


class FreezeDetector:
    """Rileva i congelamenti del processo dal salto dell'orologio monotono.

    Il watchdog si sveglia ogni `tick_s`. Se fra due risvegli e' passato molto
    piu' tempo del previsto, il processo non stava girando: swap, OOM killer
    che ha fatto arrancare la macchina, sospensione, GC patologico. Durante
    quel tempo non e' stato scritto niente, ma la socket TCP puo' essere
    rimasta perfettamente aperta — nessun errore, nessuna riconnessione,
    nessuna riga nel registro. Il salto dell'orologio e' l'unico rilevatore
    che funziona anche quando la socket mente.

    Due orologi, due domande diverse: il monotono misura il salto (non lo
    disturbano NTP ne' i cambi d'ora), quello di sistema fornisce i timestamp
    scritti nel registro, che devono essere confrontabili coi dati.
    """

    def __init__(self, recorder: GapRecorder, tick_s: float,
                 threshold_s: float = DEFAULT_FREEZE_JUMP_SECONDS,
                 monotonic=time.monotonic):
        self._recorder = recorder
        self._tick_s = float(tick_s)
        self._threshold_s = float(threshold_s)
        self._monotonic = monotonic
        self._last = monotonic()

    def tick(self, channels: list[str] | None = None) -> Gap | None:
        """Da chiamare a ogni risveglio del watchdog. Ritorna la finestra di
        congelamento registrata, o `None` se il risveglio era puntuale."""
        now = self._monotonic()
        elapsed = now - self._last
        self._last = now
        jump = elapsed - self._tick_s
        if jump <= self._threshold_s:
            return None
        return self._recorder.record_freeze(
            elapsed_s=elapsed, jump_s=jump, channels=channels
        )


def load_windows(path: str) -> list[Gap]:
    """Rilegge il registro. Righe illeggibili vengono saltate: un JSONL
    troncato da un kill a meta' riga non deve rendere inutilizzabile lo
    storico dei buchi precedenti. Eventi sconosciuti vengono ignorati, cosi'
    come i campi in piu': il formato cresce per aggiunta."""
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
                    event = rec.get("event")
                    if event in ("open", "manual"):
                        if start not in gaps:
                            gaps[start] = Gap(
                                start_ms=start,
                                reason=rec.get("reason", ""),
                                channels=list(rec.get("channels") or []),
                                cause=rec.get("cause", CAUSE_DISCONNECT),
                            )
                            order.append(start)
                            # La riga `manual` e' autoconclusiva: una correzione
                            # scritta a mano non ha una seconda riga di chiusura.
                            if event == "manual" and rec.get("end_ms") is not None:
                                gaps[start].end_ms = int(rec["end_ms"])
                    elif start in gaps:
                        _apply_event(gaps[start], event, rec)
                except (ValueError, KeyError, TypeError):
                    continue
    except FileNotFoundError:
        return []
    return [gaps[s] for s in order]


def _apply_event(gap: Gap, event: str | None, rec: dict) -> None:
    if event == "close":
        gap.end_ms = int(rec["end_ms"])
        per_channel = rec.get("per_channel_duration_s")
        if isinstance(per_channel, dict) and not gap.channel_ends:
            # Registro scritto da un processo che non ha lasciato righe
            # `resume` (chiusura via mark_connected): i ritorni per canale si
            # ricostruiscono dalle durate.
            for ch, dur in per_channel.items():
                gap.channel_ends[ch] = gap.start_ms + int(round(float(dur) * 1000))
        if rec.get("reconnect_ms") is not None:
            gap.reconnect_ms = int(rec["reconnect_ms"])
    elif event == "resume":
        gap.channel_ends[str(rec["channel"])] = int(rec["end_ms"])
    elif event == "reconnect":
        gap.reconnect_ms = int(rec["reconnect_ms"])
    elif event == "reopen":
        # I canali erano ripartiti e sono tornati muti: i ritorni registrati
        # prima non valgono piu'.
        gap.channel_ends.clear()
        gap.reconnect_ms = None


def _last_unclosed(path: str) -> Gap | None:
    windows = load_windows(path)
    for gap in reversed(windows):
        if gap.end_ms is None:
            return gap
    return None
