"""Da cosa nasce un fill, e da cosa il motore si rifiuta di farlo nascere.

Due sole regole, quelle del prompt e dell'invariante 5 di CLAUDE.md.

**Taker.** Si cammina il book REGISTRATO, livello per livello, con
`costs.CostModel`. Nessun modello di impatto, nessuna costante di slippage,
nessuna interpolazione dentro una candela. Se la profondita' registrata non
basta, `costs` solleva `InsufficientDepth` e qui l'ordine viene rifiutato: il
prezzo che avrebbe avuto un ordine piu' grande del book non e' nei dati, e
inventarlo e' il modo piu' economico di rendere profittevole una strategia che
non lo e'.

**Maker.** Un ordine in attesa a prezzo `L` viene eseguito solo se un trade
successivo passa ATTRAVERSO `L`: strettamente sotto per un acquisto,
strettamente sopra per una vendita. Un trade esattamente a `L` non basta, ed
e' la differenza fra un backtest e una favola: il prezzo che tocca il tuo
livello dice che il mercato ci e' arrivato, non che sia arrivato fino a TE.
Davanti a te c'e' una coda di cui i dati registrati non dicono nulla — su
Hyperliquid l'`l2Book` da' la size aggregata per livello, non la posizione in
coda. L'unica prova osservabile che la coda al tuo livello sia stata esaurita
e' una stampa oltre il livello.

La size eseguibile e' limitata dalla size del trade che ha attraversato: piu'
contratti di quanti ne siano davvero passati non erano disponibili, e prenderli
sarebbe un altro modo di inventare.

**Freschezza.** Gli snapshot arrivano ogni ~5,38 s, quindi al momento di
eseguire il book piu' recente ha quasi sempre qualche secondo. L'eta' viene
misurata e scritta su ogni fill; oltre una soglia configurabile l'ordine e'
rifiutato invece di essere eseguito su un book che potrebbe non esistere piu'.
"""

from __future__ import annotations

from dataclasses import dataclass

from costs import CostModel, ExecutionCost, InsufficientDepth, Liquidity, Side

from .events import BookEvent, TradeEvent
from .orders import Order, OrderKind, Reject


@dataclass(frozen=True, slots=True)
class Attempt:
    """Esito di un tentativo di esecuzione: o un costo, o un motivo."""

    cost: ExecutionCost | None
    reject: Reject | None
    book_ev: BookEvent | None
    age_ns: int | None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.cost is not None


def taker(model: CostModel, order: Order, book_ev: BookEvent | None,
          as_of_ns: int, max_age_ns: int) -> Attempt:
    """Esecuzione a mercato sul book registrato piu' recente."""
    if book_ev is None:
        return Attempt(None, Reject.NO_BOOK, None, None,
                       "nessuno snapshot l2Book precedente alla decisione")
    age = as_of_ns - book_ev.ts_local_ns
    if age > max_age_ns:
        return Attempt(None, Reject.BOOK_STALE, book_ev, age,
                       f"eta' {age / 1e9:.3f}s oltre la soglia "
                       f"{max_age_ns / 1e9:.3f}s")
    try:
        cost = model.execution_from_size(book_ev.book, order.side, order.size,
                                         Liquidity.TAKER)
    except InsufficientDepth as e:
        return Attempt(None, Reject.INSUFFICIENT_DEPTH, book_ev, age, str(e))
    return Attempt(cost, None, book_ev, age)


def crosses(side: Side, limit_px: float, trade_px: float) -> bool:
    """Il trade e' passato ATTRAVERSO il livello? Confronto stretto, sempre.

    E' la riga che separa un fill maker legittimo da uno regalato. Chi la
    rilassasse in `<=` renderebbe eseguibile ogni ordine appoggiato al best,
    che e' precisamente l'ipotesi che il paper trading smentisce per primo.
    """
    if side is Side.BUY:
        return trade_px < limit_px
    return trade_px > limit_px


def maker_execution(model: CostModel, coin: str, side: Side, size: float,
                    px: float, mid: float) -> ExecutionCost:
    """Costo di un fill maker eseguito al proprio limite.

    La commissione viene da `costs.FeeSchedule` — non esiste un secondo posto
    in cui si calcolino le fee. Spread e impatto sono zero per definizione di
    maker: non si e' attraversato niente. Il `mid` di riferimento e' quello
    dello snapshot su cui l'ordine e' stato appoggiato, e sta nel giornale
    perche' senza di esso non si puo' misurare il miglioramento di prezzo
    ottenuto rispetto a un taker.
    """
    notional_executed = size * px
    return ExecutionCost(
        coin=coin, side=side, liquidity=Liquidity.MAKER, size=size,
        notional_nominal=size * mid,
        notional_executed=notional_executed,
        fee=model.fees.fee(notional_executed, Liquidity.MAKER),
        spread=0.0, impact=0.0, avg_px=px, mid=mid, fill=None,
    )


def maker_fillable(order: Order, remaining: float,
                   trade: TradeEvent) -> float:
    """Quanta size di un ordine in attesa questo trade puo' riempire.

    Zero se non ha attraversato il livello. Altrimenti il minimo fra cio' che
    resta dell'ordine e cio' che e' davvero passato: la size del trade e' il
    tetto di quanto poteva essere eseguito in quel momento.
    """
    if order.kind is not OrderKind.LIMIT or order.limit_px is None:
        return 0.0
    if trade.coin != order.coin:
        return 0.0
    if not crosses(order.side, order.limit_px, trade.px):
        return 0.0
    return min(remaining, trade.sz)
