"""Gli eventi che il motore consuma, e da quale riga di dati vengono.

Un evento e' una riga registrata dal collector, gia' interpretata: uno snapshot
`l2Book` oppure un trade. Porta con se' la propria provenienza — il file
parquet, il timestamp locale, e per i trade il `tid` — perche' l'invariante 5
di CLAUDE.md vieta i fill inventati, e l'unico modo di dimostrare che un fill
non lo e' consiste nel ricondurlo alla riga che lo giustifica.

**Il clock e' `ts_local_ns`, non il `time` dell'exchange.** Un trade avvenuto
sull'exchange alle 12:00:00,000 e arrivato alle 12:00:00,180 e' utilizzabile
solo dalle 12:00:00,180 in poi: ordinare per il timestamp dell'exchange
darebbe al motore informazione prima che esistesse, che e' esattamente il
look-ahead vietato dall'invariante del prompt. `ts_exch_ms` e `time_ms`
restano nel giornale come dato di controllo e non entrano in nessuna
decisione.

**L'ordine e' totale.** A parita' di nanosecondo si ordina per tipo (prima il
book, poi il trade), poi per coin, poi per `tid`. Non cambia nessun fill — un
fill maker guarda i trade, uno taker guarda il book — ma rende la sequenza
riproducibile bit per bit, che e' un requisito esplicito.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Union

from costs import L2Book

BOOK_RANK = 0
TRADE_RANK = 1


@dataclass(frozen=True, slots=True)
class BookEvent:
    """Uno snapshot `l2Book` a 10 livelli per lato, gia' validato da `costs`."""

    ts_local_ns: int
    coin: str
    book: L2Book
    ts_exch_ms: int = 0
    src_file: str = ""

    @property
    def rank(self) -> int:
        return BOOK_RANK

    @property
    def tie(self) -> int:
        return 0

    @property
    def ref(self) -> str:
        """Riferimento alla riga di dati, nel formato che il giornale scrive.

        `file#ts_local_ns`: dentro una partizione il collector scrive una riga
        per messaggio, quindi la coppia identifica la riga senza ambiguita'.
        """
        return f"{self.src_file}#{self.ts_local_ns}"


@dataclass(frozen=True, slots=True)
class TradeEvent:
    """Un trade eseguito sull'exchange, deduplicato per `tid` dal catalogo.

    `side` e' il lato dell'AGGRESSORE come lo manda Hyperliquid (`B` = ha
    comprato prendendo dagli ask, `A` = ha venduto prendendo dai bid). Il
    motore non lo usa per decidere un fill maker — conta il prezzo, non chi ha
    aggredito — ma sta nel giornale perche' senza di esso una riga non e'
    confrontabile col dato grezzo.
    """

    ts_local_ns: int
    coin: str
    px: float
    sz: float
    side: str
    tid: int
    time_ms: int = 0
    src_file: str = ""

    @property
    def rank(self) -> int:
        return TRADE_RANK

    @property
    def tie(self) -> int:
        return self.tid

    @property
    def ref(self) -> str:
        """Il `tid` e' l'identificatore dell'exchange: piu' forte di un percorso
        di file, e stabile anche se i parquet vengono riorganizzati."""
        return f"tid={self.tid}@{self.ts_local_ns}"


Event = Union[BookEvent, TradeEvent]


def sort_key(e: Event) -> tuple[int, int, str, int]:
    """Chiave d'ordine totale del feed. Vedi la docstring del modulo."""
    return (e.ts_local_ns, e.rank, e.coin, e.tie)
