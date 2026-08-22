"""Il motore: una griglia di barre, un feed di eventi, e nessuna scorciatoia.

**Come scorre il tempo.** Il tempo e' una griglia regolare di frontiere di
barra allineate all'epoch (`bar_s` deve dividere 3600, cosi' ogni regolamento
di funding cade esattamente su una frontiera e non a meta' di una barra). A
ogni frontiera `t` il motore, nell'ordine:

1. consuma dal feed tutti gli eventi con `ts_local_ns < t` — aggiorna il book
   corrente, la finestra di trade recenti, e prova a riempire gli ordini limit
   in attesa coi trade appena arrivati;
2. fa scadere gli ordini limit oltre il loro TTL;
3. chiude d'ufficio le posizioni sulle coin la cui ora e' inaffidabile;
4. costruisce la `MarketView`, chiede una decisione alla strategia ed esegue
   gli ordini che ne sono usciti;
5. regola il funding, se `t` e' una frontiera d'ora;
6. scrive una riga di equity.

**Perche' la decisione non puo' guardare avanti.** Il taglio al punto 1 e'
STRETTO: al punto 4 gli eventi `>= t` non sono ancora stati letti. La
strategia non riceve il feed, riceve la view. Vedi `backtest/view.py`.

**Perche' il funding si regola dopo le esecuzioni.** La convenzione di
`costs.settlement_hours` e' che chi apre esattamente sull'ora paga quel
regolamento e chi chiude esattamente sull'ora non lo paga. Regolare prima
delle esecuzioni la invertirebbe, e la differenza — un'ora di funding a ogni
entrata e a ogni uscita che cadono sull'ora — sarebbe un errore piccolo,
sistematico e invisibile. Il motore ha una convenzione sola, ed e' quella di
`costs`.

**Decisione e esecuzione.** La decisione a `t` nasce da dati della barra
precedente e di prima; il fill NON usa un prezzo interno alla barra su cui la
decisione e' stata presa, ma lo stato del mondo a `t`: lo snapshot piu' recente
con timestamp precedente a `t`, la cui eta' finisce su ogni riga di fill. Su
Hyperliquid gli `l2Book` arrivano ogni ~5,38 s, quindi quello snapshot ha
tipicamente qualche secondo. **Questa e' l'ipotesi piu' ottimistica del
motore** ed e' dichiarata qui: il prezzo a cui si esegue e' un prezzo che la
strategia aveva gia' potuto vedere, mentre in produzione l'ordine arriva
sull'exchange qualche centinaio di millisecondi dopo la decisione, contro un
book che nel frattempo si e' mosso — e si e' mosso, in media, contro di te,
perche' il momento in cui decidi di attraversare lo spread non e' un momento a
caso. Il motore misura l'eta' del book per rendere quantificabile questo
scarto quando il paper trading lo mettera' alla prova.

**Estremi.** Uno snapshot con `ts_local_ns` esattamente uguale a `t` e'
trattato come non ancora disponibile: fra "strettamente precedente alla
decisione" (invariante 4 del prompt) e "non successivo all'esecuzione"
(invariante 6) vince il primo, sempre. A risoluzione di nanosecondo il caso e'
teorico; la regola no.

**Attraversamento dei buchi.** Un'ora e' inaffidabile se un buco derivato dai
dati la attraversa o se il rate di funding di quel regolamento non e' noto. Il
motore non finge continuita': chiude d'ufficio la posizione alla prima
frontiera dell'ora inaffidabile — usando l'ultimo book affidabile, che e'
quello dell'ora precedente — rifiuta ogni ordine su quella coin finche' dura,
e riparte piatto dopo. Ogni chiusura d'ufficio e' una riga del giornale, e il
riepilogo conta le finestre attraversate. Se la chiusura non e' eseguibile (il
book piu' recente e' gia' troppo vecchio) la posizione resta aperta, il
tentativo viene ripetuto a ogni barra successiva e il fatto viene contato in
`n_forced_close_failed`: un risultato con quel contatore diverso da zero e' un
risultato che attraversa un buco, e va letto sapendolo.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Iterable, Iterator, Protocol, Sequence

from costs import (
    HYPERLIQUID_PERP_BASE,
    CostModel,
    FeeSchedule,
    FundingSeries,
    IncompleteFunding,
    Liquidity,
    Side,
)
from costs.funding import NS_PER_HOUR

from . import fills, journal as jn
from .events import BookEvent, Event, TradeEvent
from .journal import Journal
from .orders import Order, OrderKind, Reject, Resting
from .view import LookAheadError, MarketView

# Sotto questa frazione di size residua un ordine limit si considera riempito.
# Serve solo a chiudere gli ordini quando la somma dei riempimenti parziali
# torna alla size richiesta a meno dell'ultimo bit.
_EPS = 1e-12


class Strategy(Protocol):
    """Riceve cio' che si sapeva, restituisce ordini. Nient'altro.

    Non ha accesso al feed, al portafoglio o al motore: se un giorno servisse
    darglielo, sarebbe il momento di fermarsi e chiedersi cosa si sta per
    rendere possibile.
    """

    def decide(self, view: MarketView) -> Sequence[Order]:
        ...


@dataclass(frozen=True)
class EngineConfig:
    """I parametri del motore. Nessuno di questi e' una soglia di strategia.

    `max_book_age_s` e' un limite operativo, non un parametro da tarare: gli
    snapshot arrivano ogni ~5,38 s e il default (30 s) e' poco piu' di cinque
    intervalli, cioe' "il collector ha saltato qualche messaggio ma non e'
    andato via". Alzarlo rende eseguibili fill su book che potrebbero non
    esistere piu'; abbassarlo sotto ~6 s rifiuta l'esecuzione normale.
    """

    bar_s: int = 60
    initial_equity: float = 10_000.0
    max_book_age_s: float = 30.0
    trade_window: int = 200
    close_window: int = 512
    fees: FeeSchedule = HYPERLIQUID_PERP_BASE
    close_at_end: bool = True
    require_funding: bool = True

    def __post_init__(self) -> None:
        if self.bar_s <= 0 or 3600 % self.bar_s != 0:
            raise ValueError(
                f"bar_s = {self.bar_s}: deve dividere 3600, altrimenti un "
                f"regolamento di funding cadrebbe dentro una barra e il "
                f"notional su cui si paga dipenderebbe da dove cade"
            )
        if self.max_book_age_s <= 0:
            raise ValueError(f"max_book_age_s non positiva: {self.max_book_age_s}")
        if self.initial_equity <= 0:
            raise ValueError(f"equity iniziale non positiva: {self.initial_equity}")

    @property
    def bar_ns(self) -> int:
        return int(self.bar_s) * 1_000_000_000

    @property
    def max_book_age_ns(self) -> int:
        return int(self.max_book_age_s * 1_000_000_000)


@dataclass(frozen=True)
class BlockedHours:
    """Ore (indice orario UTC) in cui una coin non e' negoziabile.

    Vengono dai buchi DERIVATI DAI DATI di `catalog.derivedgaps`, non dal
    registro `_gaps.jsonl`: il registro e' una dichiarazione del collector, i
    buchi derivati sono l'assenza misurata di righe.
    """

    per_coin: dict[str, frozenset[int]] = field(default_factory=dict)

    def blocked(self, coin: str, hour_idx: int) -> bool:
        return hour_idx in self.per_coin.get(coin, frozenset())

    @property
    def n_hours(self) -> int:
        return sum(len(v) for v in self.per_coin.values())


@dataclass
class Result:
    """Cosa e' successo. I contatori sono parte del risultato quanto il PnL."""

    config: EngineConfig
    coins: tuple[str, ...]
    start_ns: int
    end_ns: int
    journal: Journal
    initial_equity: float
    final_equity: float
    realized_pnl: float
    fees_paid: float
    funding_paid: float
    n_bars: int = 0
    n_bars_decided: int = 0
    n_bars_all_blocked: int = 0
    n_events_book: int = 0
    n_events_trade: int = 0
    n_book_invalid: int = 0
    n_orders: int = 0
    n_fills_taker: int = 0
    n_fills_maker: int = 0
    n_settlements: int = 0
    n_forced_close: int = 0
    n_forced_close_failed: int = 0
    n_funding_unknown: int = 0
    rejects: dict[str, int] = field(default_factory=dict)
    book_age_ms: list[float] = field(default_factory=list)

    @property
    def n_fills(self) -> int:
        return self.n_fills_taker + self.n_fills_maker

    @property
    def pnl(self) -> float:
        return self.final_equity - self.initial_equity

    @property
    def conservation_residual(self) -> float:
        """Quanto manca all'identita', ricalcolata dal GIORNALE.

        Se non e' zero al centesimo, il risultato non e' un risultato.
        """
        expected = (self.initial_equity
                    + self.journal.total("realized_pnl", jn.FILL)
                    - self.journal.total("fee", jn.FILL)
                    - self.journal.total("funding", jn.FUNDING))
        return self.final_equity - expected

    def digest(self) -> str:
        return self.journal.digest()


class Engine:
    """Un motore, usato identico da backtest e paper: cambia il feed, non questo.

    In paper trading gli eventi arrivano dal WebSocket invece che dai parquet e
    gli ordini vanno all'exchange invece che a `fills`; la griglia delle barre,
    la barriera anti look-ahead, la contabilita' e il giornale sono gli stessi
    oggetti. Non esiste un ramo `if backtest:` in questo file, ed e' deliberato.
    """

    def __init__(self, config: EngineConfig, strategy: Strategy,
                 coins: Sequence[str],
                 funding: dict[str, FundingSeries] | None = None,
                 blocked: BlockedHours | None = None,
                 model: CostModel | None = None) -> None:
        self.config = config
        self.strategy = strategy
        self.coins = tuple(sorted(coins))
        self.funding = dict(funding or {})
        self.blocked = blocked or BlockedHours()
        self.model = model or CostModel(fees=config.fees)
        if self.model.fees != config.fees:
            raise ValueError("il CostModel passato non usa le fee della config")

    # -- ostacoli sui dati ------------------------------------------------------

    def _block_reason(self, coin: str, hour_idx: int) -> str | None:
        if self.blocked.blocked(coin, hour_idx):
            return "buco_derivato_dai_dati"
        series = self.funding.get(coin)
        if series is None:
            if self.config.require_funding:
                return "nessuna_serie_di_funding"
            return None
        if series.rate(hour_idx) is None:
            return "funding_non_noto"
        return None

    # -- il ciclo ---------------------------------------------------------------

    def run(self, events: Iterable[Event], start_ns: int, end_ns: int) -> Result:
        from .portfolio import Portfolio

        cfg = self.config
        bar_ns = cfg.bar_ns
        first = -(-int(start_ns) // bar_ns) * bar_ns
        last = (int(end_ns) // bar_ns) * bar_ns
        if last <= first:
            raise ValueError(
                f"finestra troppo corta: fra {start_ns} e {end_ns} non ci sono "
                f"due frontiere di barra da {cfg.bar_s}s"
            )

        self._it = _ordered(events)
        self._peeked: Event | None = None
        self._books: dict[str, BookEvent] = {}
        self._trades: dict[str, deque[TradeEvent]] = {
            c: deque(maxlen=cfg.trade_window) for c in self.coins
        }
        # Finestre limitate: la memoria del motore non deve crescere con la
        # lunghezza del backtest. Chi ha bisogno di piu' storia alza il numero
        # nella config e lo dichiara, invece di trovarsela regalata.
        self._closes: dict[str, deque[float]] = {
            c: deque(maxlen=cfg.close_window) for c in self.coins
        }
        self._resting: list[Resting] = []
        self.portfolio = Portfolio(initial_equity=cfg.initial_equity)
        self.journal = Journal()
        self.result = Result(
            config=cfg, coins=self.coins, start_ns=first, end_ns=last,
            journal=self.journal, initial_equity=cfg.initial_equity,
            final_equity=cfg.initial_equity, realized_pnl=0.0,
            fees_paid=0.0, funding_paid=0.0,
        )

        t = first
        bar_idx = 0
        while t <= last:
            self._drain(t)
            self._expire(t, bar_idx)
            blocked = {c: self._block_reason(c, t // NS_PER_HOUR)
                       for c in self.coins}
            self._force_close_blocked(t, bar_idx, blocked)
            if t == last:
                if cfg.close_at_end:
                    self._close_all(t, bar_idx, "chiusura_fine_run")
            else:
                self._decide(t, bar_idx, blocked)
            if t % NS_PER_HOUR == 0:
                self._settle_funding(t, bar_idx)
            self._record_equity(t, bar_idx)
            for coin in self.coins:
                m = self._mark(coin)
                if m is not None:
                    self._closes[coin].append(m)
            self.result.n_bars += 1
            t += bar_ns
            bar_idx += 1

        marks = self._marks_for_open()
        r = self.result
        r.final_equity = self.portfolio.equity(marks)
        r.realized_pnl = self.portfolio.realized_pnl
        r.fees_paid = self.portfolio.fees_paid
        r.funding_paid = self.portfolio.funding_paid
        return r

    # -- feed -------------------------------------------------------------------

    def _peek(self) -> Event | None:
        if self._peeked is None:
            self._peeked = next(self._it, None)
        return self._peeked

    def _drain(self, until_ns: int) -> None:
        """Consuma gli eventi con `ts_local_ns` STRETTAMENTE minore di `until_ns`."""
        while True:
            ev = self._peek()
            if ev is None or ev.ts_local_ns >= until_ns:
                return
            self._peeked = None
            if isinstance(ev, BookEvent):
                self.result.n_events_book += 1
                if ev.coin in self._trades:
                    self._books[ev.coin] = ev
            else:
                self.result.n_events_trade += 1
                buf = self._trades.get(ev.coin)
                if buf is not None:
                    buf.append(ev)
                    self._match(ev)

    # -- ordini limit in attesa --------------------------------------------------

    def _match(self, trade: TradeEvent) -> None:
        """Prova a riempire gli ordini in attesa con un trade appena arrivato.

        La size del trade e' un budget: due nostri ordini appoggiati allo stesso
        livello non possono essere riempiti entrambi per intero dallo stesso
        scambio, perche' quei contratti sono passati una volta sola.
        """
        budget = trade.sz
        for r in list(self._resting):
            if budget <= 0.0:
                return
            if r.order.coin != trade.coin:
                continue
            if trade.ts_local_ns <= r.placed_ns or trade.ts_local_ns >= r.expires_ns:
                continue
            qty = fills.maker_fillable(r.order, min(r.remaining, budget), trade)
            if qty <= 0.0:
                continue
            budget -= qty
            self._fill_maker(r, trade, qty)

    def _fill_maker(self, r: Resting, trade: TradeEvent, qty: float) -> None:
        o = r.order
        cost = fills.maker_execution(self.model, o.coin, o.side, qty,
                                     float(o.limit_px), r.ref_mid)
        realized = self.portfolio.apply_fill(o.coin, o.side.sign * qty,
                                             float(o.limit_px), cost.fee)
        r.filled += qty
        if r.remaining <= o.size * _EPS:
            self._resting.remove(r)
        self.result.n_fills_maker += 1
        self.result.book_age_ms.append(r.ref_book_age_ns / 1e6)
        self.journal.add(
            bar_idx=r.bar_idx, decision_ts_ns=r.placed_ns,
            decision_utc=jn.utc(r.placed_ns), event=jn.FILL, coin=o.coin,
            side=o.side.name, kind=o.kind.value, liquidity=Liquidity.MAKER.value,
            tag=o.tag, size_ordered=o.size, size_filled=qty,
            px=float(o.limit_px), notional_nominal=cost.notional_nominal,
            notional_executed=cost.notional_executed, fee=cost.fee,
            spread_cost=cost.spread, impact_cost=cost.impact,
            realized_pnl=realized, position_after=self.portfolio.size(o.coin),
            book_ts_ns=r.ref_book_ts_ns, book_age_ms=r.ref_book_age_ns / 1e6,
            book_src=r.ref_book_src, trade_tid=trade.tid,
            trade_ts_ns=trade.ts_local_ns, trade_time_ms=trade.time_ms,
            reason="attraversamento", ref=trade.ref,
        )

    def _expire(self, t: int, bar_idx: int) -> None:
        for r in list(self._resting):
            if r.expires_ns <= t:
                self._resting.remove(r)
                self._reject(t, bar_idx, r.order, Reject.EXPIRED,
                             f"residuo {r.remaining:.8g} non attraversato entro "
                             f"{r.order.ttl_bars} barre")

    # -- buchi ------------------------------------------------------------------

    def _force_close_blocked(self, t: int, bar_idx: int,
                             blocked: dict[str, str | None]) -> None:
        for coin in self.coins:
            reason = blocked[coin]
            if reason is None:
                continue
            for r in [x for x in self._resting if x.order.coin == coin]:
                self._resting.remove(r)
                self._reject(t, bar_idx, r.order, Reject.UNRELIABLE_HOUR, reason)
            if self.portfolio.size(coin) == 0.0:
                continue
            if self._close(coin, t, bar_idx, f"chiusura_forzata:{reason}"):
                self.result.n_forced_close += 1
            else:
                self.result.n_forced_close_failed += 1

    def _close_all(self, t: int, bar_idx: int, tag: str) -> None:
        for coin in self.coins:
            if self.portfolio.size(coin) != 0.0:
                self._close(coin, t, bar_idx, tag)

    def _close(self, coin: str, t: int, bar_idx: int, tag: str) -> bool:
        size = self.portfolio.size(coin)
        side = Side.SELL if size > 0 else Side.BUY
        order = Order(coin=coin, side=side, size=abs(size),
                      kind=OrderKind.MARKET, tag=tag)
        return self._execute_market(order, t, bar_idx)

    # -- funding ----------------------------------------------------------------

    def _settle_funding(self, t: int, bar_idx: int) -> None:
        """Un regolamento all'inizio di ogni ora, sul notional del momento.

        Il conto lo fa `costs.FundingSeries` in modalita' stretta: se il rate
        di quel regolamento non e' noto solleva invece di sommare zero. Non
        dovrebbe mai succedere — un'ora senza rate e' un'ora bloccata, e la
        posizione viene chiusa prima — e se succede e' un errore di questo
        motore, non un dato da arrotondare.
        """
        hour = t // NS_PER_HOUR
        for coin in self.coins:
            size = self.portfolio.size(coin)
            if size == 0.0:
                continue
            series = self.funding.get(coin)
            if series is None:
                continue
            mark = self._mark(coin)
            if mark is None:
                continue
            side = Side.BUY if size > 0 else Side.SELL
            try:
                fc = series.cost(side, abs(size) * mark, t, t + 1, strict=True)
            except IncompleteFunding as e:
                # Ci si arriva solo se una chiusura d'ufficio non e' stata
                # eseguibile e la posizione e' rimasta aperta dentro un'ora
                # bloccata. Il costo NON viene stimato: si scrive la riga con
                # l'importo vuoto e si conta. Un'equity a cui manca un
                # regolamento e' sbagliata, ma sbagliata in modo visibile.
                self.result.n_funding_unknown += 1
                self.journal.add(
                    bar_idx=bar_idx, decision_ts_ns=t, decision_utc=jn.utc(t),
                    event=jn.FUNDING, coin=coin, side=side.name,
                    size_filled=abs(size), px=mark,
                    notional_nominal=abs(size) * mark, position_after=size,
                    reason=f"funding_non_noto: {e}", ref=f"hour={hour}",
                )
                continue
            self.portfolio.apply_funding(fc.cost)
            self.result.n_settlements += 1
            self.journal.add(
                bar_idx=bar_idx, decision_ts_ns=t, decision_utc=jn.utc(t),
                event=jn.FUNDING, coin=coin, side=side.name,
                size_filled=abs(size), px=mark,
                notional_nominal=abs(size) * mark, funding=fc.cost,
                position_after=size, reason=f"rate={fc.rate_sum!r}",
                ref=f"hour={hour}",
            )

    # -- decisione ed esecuzione -------------------------------------------------

    def _decide(self, t: int, bar_idx: int,
                blocked: dict[str, str | None]) -> None:
        if all(blocked[c] is not None for c in self.coins):
            self.result.n_bars_all_blocked += 1
            return
        marks = self._marks_for_open()
        view = MarketView(
            as_of_ns=t, bar_idx=bar_idx, coins=self.coins,
            books=dict(self._books),
            trades={c: tuple(b) for c, b in self._trades.items()},
            closes={c: tuple(v) for c, v in self._closes.items()},
            positions={c: self.portfolio.size(c) for c in self.coins},
            equity=self.portfolio.equity(marks),
        )
        orders = self.strategy.decide(view) or ()
        if view.max_ts_seen >= t:
            raise LookAheadError(
                f"la strategia ha ricevuto un dato a ts={view.max_ts_seen} "
                f"decidendo a ts={t}: il taglio del feed non e' piu' stretto"
            )
        self.result.n_bars_decided += 1
        for order in orders:
            self.result.n_orders += 1
            if order.coin not in self._trades:
                self._reject(t, bar_idx, order, Reject.INVALID,
                             f"coin non simulata: {order.coin}")
                continue
            reason = blocked[order.coin]
            if reason is not None:
                self._reject(t, bar_idx, order, Reject.UNRELIABLE_HOUR, reason)
                continue
            if order.kind is OrderKind.MARKET:
                self._execute_market(order, t, bar_idx)
            else:
                self._place_limit(order, t, bar_idx)

    def _execute_market(self, order: Order, t: int, bar_idx: int) -> bool:
        att = fills.taker(self.model, order, self._books.get(order.coin), t,
                          self.config.max_book_age_ns)
        if not att.ok:
            self._reject(t, bar_idx, order, att.reject, att.detail, att)
            return False
        cost = att.cost
        assert cost is not None and att.book_ev is not None
        realized = self.portfolio.apply_fill(
            order.coin, order.signed_size, cost.avg_px, cost.fee
        )
        self.result.n_fills_taker += 1
        self.result.book_age_ms.append((att.age_ns or 0) / 1e6)
        self.journal.add(
            bar_idx=bar_idx, decision_ts_ns=t, decision_utc=jn.utc(t),
            event=jn.FILL, coin=order.coin, side=order.side.name,
            kind=order.kind.value, liquidity=Liquidity.TAKER.value,
            tag=order.tag, size_ordered=order.size, size_filled=order.size,
            px=cost.avg_px, notional_nominal=cost.notional_nominal,
            notional_executed=cost.notional_executed, fee=cost.fee,
            spread_cost=cost.spread, impact_cost=cost.impact,
            realized_pnl=realized, position_after=self.portfolio.size(order.coin),
            book_ts_ns=att.book_ev.ts_local_ns,
            book_age_ms=(att.age_ns or 0) / 1e6,
            book_src=att.book_ev.src_file,
            reason="cammino_sul_book", ref=att.book_ev.ref,
        )
        return True

    def _place_limit(self, order: Order, t: int, bar_idx: int) -> None:
        ev = self._books.get(order.coin)
        if ev is None:
            self._reject(t, bar_idx, order, Reject.NO_BOOK,
                         "nessuno snapshot su cui appoggiare il limite")
            return
        age = t - ev.ts_local_ns
        if age > self.config.max_book_age_ns:
            self._reject(t, bar_idx, order, Reject.BOOK_STALE,
                         f"eta' {age / 1e9:.3f}s oltre la soglia", None, ev, age)
            return
        px = float(order.limit_px)
        marketable = (px >= ev.book.best_ask if order.side is Side.BUY
                      else px <= ev.book.best_bid)
        if marketable:
            self._reject(t, bar_idx, order, Reject.INVALID,
                         f"limite {px!r} attraversa il book "
                         f"({ev.book.best_bid!r}/{ev.book.best_ask!r}): sarebbe "
                         f"un taker, e come maker sarebbe un fill regalato",
                         None, ev, age)
            return
        self._resting.append(Resting(
            order=order, placed_ns=t, bar_idx=bar_idx,
            expires_ns=t + order.ttl_bars * self.config.bar_ns,
            ref_book_ts_ns=ev.ts_local_ns, ref_book_age_ns=age,
            ref_book_src=ev.src_file, ref_mid=ev.book.mid,
        ))

    def _reject(self, t: int, bar_idx: int, order: Order,
                reject: Reject | None, detail: str,
                att: fills.Attempt | None = None,
                ev: BookEvent | None = None, age: int | None = None) -> None:
        code = (reject or Reject.INVALID).value
        self.result.rejects[code] = self.result.rejects.get(code, 0) + 1
        book_ev = ev or (att.book_ev if att else None)
        book_age = age if age is not None else (att.age_ns if att else None)
        self.journal.add(
            bar_idx=bar_idx, decision_ts_ns=t, decision_utc=jn.utc(t),
            event=jn.REJECT, coin=order.coin, side=order.side.name,
            kind=order.kind.value, tag=order.tag, size_ordered=order.size,
            px=order.limit_px,
            position_after=self.portfolio.size(order.coin),
            book_ts_ns=book_ev.ts_local_ns if book_ev else None,
            book_age_ms=(book_age / 1e6) if book_age is not None else None,
            book_src=book_ev.src_file if book_ev else "",
            reason=f"{code}: {detail}",
            ref=book_ev.ref if book_ev else "",
        )

    # -- stato -------------------------------------------------------------------

    def _mark(self, coin: str) -> float | None:
        ev = self._books.get(coin)
        return None if ev is None else ev.book.mid

    def _marks_for_open(self) -> dict[str, float]:
        out = {}
        for coin in self.coins:
            m = self._mark(coin)
            if m is not None:
                out[coin] = m
        return out

    def _record_equity(self, t: int, bar_idx: int) -> None:
        marks = self._marks_for_open()
        row = {
            "bar_idx": bar_idx,
            "ts_ns": t,
            "utc": jn.utc(t),
            "cash": self.portfolio.cash,
            "realized_pnl": self.portfolio.realized_pnl,
            "fees_cum": self.portfolio.fees_paid,
            "funding_cum": self.portfolio.funding_paid,
            "unrealized": self.portfolio.unrealized(marks),
            "equity": self.portfolio.equity(marks),
        }
        for coin in self.coins:
            row[f"pos_{coin}"] = self.portfolio.size(coin)
            row[f"mark_{coin}"] = marks.get(coin)
        self.journal.add_equity(row)


def _ordered(events: Iterable[Event]) -> Iterator[Event]:
    """Verifica che il feed sia non decrescente in `ts_local_ns`.

    Un'inversione significa che il feed ha consegnato un dato dopo un dato piu'
    recente: da li' in poi la barriera anti look-ahead non garantisce piu'
    niente. Si ferma invece di continuare — un motore che riordina in silenzio
    nasconde un difetto della lettura dei parquet.
    """
    prev = -1
    for ev in events:
        if ev.ts_local_ns < prev:
            raise ValueError(
                f"feed fuori ordine: {ev.coin} a ts={ev.ts_local_ns} dopo "
                f"ts={prev} (scarto {prev - ev.ts_local_ns} ns)"
            )
        prev = ev.ts_local_ns
        yield ev
