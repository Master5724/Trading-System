"""La facciata unica del modello di costo (CLAUDE.md, invariante 4).

Backtest, paper e live importano `CostModel` da qui e non calcolano fee,
funding o slippage per conto proprio. Non e' una preferenza di stile: e' la
sola difesa contro il bug piu' costoso possibile in questo progetto, cioe' un
backtest che usa un modello di costo leggermente diverso da quello che si paga
davvero, e che quindi produce un risultato falso e plausibile.

**La scomposizione.** Ogni esecuzione ha tre componenti tenute separate, non
un totale opaco:

    fee            commissione sul notional eseguito, maker o taker
    spread         mezzo spread, il costo di attraversare il book (solo taker)
    impact         il consumo oltre il primo livello (spesso esattamente 0)

Restano separate fino al report perche' rispondono a domande diverse: la fee
si riduce cambiando tier o passando maker, lo spread si riduce solo scegliendo
quando operare, l'impatto solo riducendo la size. Un unico numero "costo"
nasconderebbe quale dei tre domina — e sui dati reali ne domina uno solo.

**Il costo maker e' il punto piu' fragile di questo modulo.** Modellarlo come
"solo la fee maker" contiene due errori di segno opposto che non si annullano:

- e' PESSIMISTA sul prezzo: un ordine passivo eseguito al proprio limite compra
  al bid, cioe' mezzo spread meglio del mid. Qui quel vantaggio non viene
  contato.
- e' OTTIMISTA sull'esecuzione: assume che l'ordine venga eseguito. Un ordine
  passivo viene riempito soprattutto quando il mercato gli sta venendo addosso
  (selezione avversa), e quando non viene riempito il costo non e' zero — e' il
  trade mancato.

Nessuna delle due cose si puo' stimare da uno snapshot del book: servono la
coda del livello e l'esito degli ordini, cioe' il motore di fill del Task 3.
Finche' quello non esiste, i numeri maker prodotti qui vanno letti come un
LIMITE INFERIORE al costo maker, mai come una previsione.
"""

from __future__ import annotations

from dataclasses import dataclass

from .fees import HYPERLIQUID_PERP_BASE, FeeSchedule, Liquidity
from .funding import FundingCost, FundingSeries
from .leverage import equity_for, on_equity
from .side import Side
from .slippage import Fill, L2Book


@dataclass(frozen=True)
class ExecutionCost:
    """Costo di una singola esecuzione, in valuta di quote.

    Tutte le componenti sono positive quando sono un costo. `notional_nominal`
    (size valutata al mid del book usato) e' la base di ogni frazione: usare il
    notional eseguito renderebbe le percentuali di acquisto e vendita non
    confrontabili fra loro.
    """

    coin: str
    side: Side
    liquidity: Liquidity
    size: float
    notional_nominal: float
    notional_executed: float
    fee: float
    spread: float
    impact: float
    avg_px: float
    mid: float
    fill: Fill | None

    @property
    def slippage(self) -> float:
        return self.spread + self.impact

    @property
    def total(self) -> float:
        return self.fee + self.spread + self.impact

    @property
    def total_frac(self) -> float:
        return self.total / self.notional_nominal

    @property
    def total_bps(self) -> float:
        return 1e4 * self.total_frac

    def on_equity(self, leverage: float = 1.0) -> float:
        return on_equity(self.total_frac, leverage)


@dataclass(frozen=True)
class RoundTripCost:
    """Entrata + uscita + funding della detenzione.

    `funding` puo' essere None (round-trip istantaneo, o detenzione non
    valutata). Se c'e' ed e' incompleto, `complete` e' falso: un totale
    calcolato su una finestra di funding bucata e' proprio il numero plausibile
    e sbagliato che CLAUDE.md vieta di produrre in silenzio.
    """

    entry: ExecutionCost
    exit: ExecutionCost
    funding: FundingCost | None = None

    @property
    def fee(self) -> float:
        return self.entry.fee + self.exit.fee

    @property
    def slippage(self) -> float:
        return self.entry.slippage + self.exit.slippage

    @property
    def funding_cost(self) -> float:
        return self.funding.cost if self.funding else 0.0

    @property
    def total(self) -> float:
        return self.entry.total + self.exit.total + self.funding_cost

    @property
    def notional_nominal(self) -> float:
        """Base delle frazioni: il notional dell'ENTRATA. E' la dimensione che
        la decisione di trading ha scelto; quella dell'uscita dipende da come
        e' andato il prezzo, e riferirvi il costo mescolerebbe costo e risultato.
        """
        return self.entry.notional_nominal

    @property
    def total_frac(self) -> float:
        return self.total / self.notional_nominal

    @property
    def total_bps(self) -> float:
        return 1e4 * self.total_frac

    @property
    def total_pct(self) -> float:
        return 100.0 * self.total_frac

    def on_equity(self, leverage: float = 1.0) -> float:
        """Costo in frazione del capitale impegnato. A leva 5 lo stesso
        round-trip pesa cinque volte: vedi `costs.leverage`."""
        return on_equity(self.total_frac, leverage)

    def equity(self, leverage: float = 1.0) -> float:
        return equity_for(self.notional_nominal, leverage)

    @property
    def complete(self) -> bool:
        return self.funding is None or self.funding.complete

    @property
    def breakeven_move_frac(self) -> float:
        """Di quanto deve muoversi il prezzo perche' il trade vada in pari.

        E' il modo in cui questo modulo diventa una decisione: una strategia che
        cerca movimenti piu' piccoli di questo numero perde denaro anche
        indovinando la direzione ogni volta.
        """
        return self.total_frac


@dataclass(frozen=True)
class CostModel:
    """Punto unico da cui backtest, paper e live ottengono un costo."""

    fees: FeeSchedule = HYPERLIQUID_PERP_BASE

    # -- esecuzione ----------------------------------------------------------

    def execution_from_size(self, book: L2Book, side: Side, size: float,
                            liquidity: Liquidity) -> ExecutionCost:
        """Costo di eseguire `size` unita' di base contro `book`.

        Per il taker cammina il book: se la profondita' registrata non basta,
        `Fill.slippage_frac` solleva `InsufficientDepth` e questo metodo la
        propaga. Non esiste un percorso che restituisca un costo estrapolato.
        """
        if liquidity is Liquidity.TAKER:
            fill = book.walk(side, size)
            avg_px = fill.require_avg_px()  # solleva InsufficientDepth se il book non basta
            notional_nominal = fill.notional_nominal
            notional_executed = fill.notional_executed
            spread = fill.half_spread_frac * notional_nominal
            impact = fill.impact_frac * notional_nominal
        else:
            # Maker: nessun attraversamento del book. Il prezzo di riferimento
            # e' il mid — non il best del proprio lato — per non contare come
            # guadagno mezzo spread che la selezione avversa si riprende in
            # parte. Vedi la docstring del modulo: e' un limite inferiore.
            fill = None
            avg_px = book.mid
            notional_nominal = size * book.mid
            notional_executed = notional_nominal
            spread = 0.0
            impact = 0.0
        return ExecutionCost(
            coin=book.coin, side=side, liquidity=liquidity, size=size,
            notional_nominal=notional_nominal,
            notional_executed=notional_executed,
            fee=self.fees.fee(notional_executed, liquidity),
            spread=spread, impact=impact,
            avg_px=avg_px, mid=book.mid, fill=fill,
        )

    def execution(self, book: L2Book, side: Side, notional: float,
                  liquidity: Liquidity) -> ExecutionCost:
        """Come sopra, ma la size si esprime in valuta di quote.

        La conversione usa il mid del book (`L2Book.size_for_notional`): la
        size si decide prima di sapere a che prezzo si eseguira'.
        """
        return self.execution_from_size(
            book, side, book.size_for_notional(notional), liquidity
        )

    # -- round-trip ----------------------------------------------------------

    def round_trip(self, notional: float, entry_book: L2Book,
                   exit_book: L2Book | None = None,
                   liquidity: Liquidity = Liquidity.TAKER,
                   side: Side = Side.BUY,
                   funding: FundingCost | None = None) -> RoundTripCost:
        """Apertura e chiusura della stessa posizione.

        Si chiude la stessa SIZE, non lo stesso notional: e' cio' che succede
        davvero, e il notional di uscita cambia col prezzo. `exit_book=None`
        significa uscita contro lo stesso book — la misura del costo puro, a
        prezzo invariato, che e' il numero da confrontare fra coin.
        """
        size = entry_book.size_for_notional(notional)
        entry = self.execution_from_size(entry_book, side, size, liquidity)
        exit_ = self.execution_from_size(
            exit_book or entry_book, side.opposite, size, liquidity
        )
        return RoundTripCost(entry=entry, exit=exit_, funding=funding)

    def hold(self, series: FundingSeries, side: Side, notional: float,
             start_ns: int, end_ns: int, strict: bool = False) -> FundingCost:
        """Funding di una detenzione. Delega a `FundingSeries.cost`: esiste qui
        perche' chi ha in mano il `CostModel` non debba importare un secondo
        modulo per completare il conto."""
        return series.cost(side, notional, start_ns, end_ns, strict=strict)


# Il modello di default. Tier base (il piu' caro), che e' il default sicuro:
# un backtest che sovrastima i costi scarta strategie buone, uno che li
# sottostima ne approva di perdenti.
DEFAULT = CostModel(fees=HYPERLIQUID_PERP_BASE)
