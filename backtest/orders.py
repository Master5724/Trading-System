"""Ordini e motivi di rifiuto.

Un ordine e' un'intenzione: non e' detto che diventi un fill. Il motore lo
rifiuta quando i dati registrati non bastano a determinare l'esecuzione, e in
quel caso il motivo finisce nel giornale. E' la forma operativa
dell'invariante 5 di CLAUDE.md: **saltare e dichiarare, mai stimare.**
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from costs import Side


class OrderKind(Enum):
    MARKET = "market"
    LIMIT = "limit"


class Reject(Enum):
    """Perche' un ordine non e' diventato un fill.

    Sono tutte condizioni sui DATI, non errori di programmazione: un motore
    onesto ne produce a decine e le conta. Un motore che non ne produce mai su
    dati veri sta inventando qualcosa.
    """

    NO_BOOK = "nessun_book_disponibile"
    BOOK_STALE = "book_troppo_vecchio"
    INSUFFICIENT_DEPTH = "profondita_insufficiente"
    UNRELIABLE_HOUR = "ora_inaffidabile"
    EXPIRED = "scaduto_senza_attraversamento"
    INVALID = "ordine_non_valido"


@dataclass(frozen=True, slots=True)
class Order:
    """Ordine emesso da una strategia a un istante di decisione.

    `size` e' in unita' di base ed e' sempre positiva: la direzione sta in
    `side`. Una size negativa sarebbe un secondo modo di dire "vendi", e due
    modi di dire la stessa cosa sono un modo di sbagliarla.
    """

    coin: str
    side: Side
    size: float
    kind: OrderKind = OrderKind.MARKET
    limit_px: float | None = None
    ttl_bars: int = 1
    tag: str = ""

    def __post_init__(self) -> None:
        if self.size <= 0:
            raise ValueError(f"size non positiva: {self.size}")
        if self.ttl_bars <= 0:
            raise ValueError(
                f"ttl_bars = {self.ttl_bars}: un ordine limit senza scadenza "
                f"resterebbe sul book per sempre, e un fill a distanza di ore "
                f"da una decisione non e' piu' quella decisione"
            )
        if self.kind is OrderKind.LIMIT:
            if self.limit_px is None or self.limit_px <= 0:
                raise ValueError(f"limit senza prezzo valido: {self.limit_px}")
        elif self.limit_px is not None:
            raise ValueError("un market order non ha un limit_px")

    @property
    def signed_size(self) -> float:
        return self.side.sign * self.size


@dataclass(slots=True)
class Resting:
    """Un ordine limit in attesa sul book, col suo stato di riempimento.

    Vive nel motore, non nella strategia: la strategia non puo' toccarlo, cosi'
    come non puo' toccare un ordine gia' inviato all'exchange.
    """

    order: Order
    placed_ns: int
    bar_idx: int
    expires_ns: int
    ref_book_ts_ns: int
    ref_book_age_ns: int
    ref_book_src: str
    ref_mid: float
    filled: float = 0.0

    @property
    def remaining(self) -> float:
        return self.order.size - self.filled
