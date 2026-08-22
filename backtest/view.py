"""La finestra sul passato che la strategia riceve, e perche' non c'e' altro.

**Il look-ahead qui non e' una regola di disciplina: e' un fatto strutturale.**
La strategia non riceve mai il feed, ne' un indice sui dati, ne' un puntatore
al motore. Riceve una `MarketView` costruita all'istante di decisione `t`, che
contiene SOLO eventi gia' consumati dal motore, e il motore consuma solo
eventi con `ts_local_ns < t`. Gli eventi successivi a `t`, nel momento in cui
`decide()` gira, **non sono in memoria**: stanno ancora nell'iteratore del
feed. Non c'e' niente da cui una feature possa pescarli.

Sopra questo fatto ci sono due difese esplicite, che esistono perche' la prima
riga di difesa non e' verificabile da un test:

1. `at()` — l'unico accesso per timestamp — solleva `LookAheadError` se le si
   chiede un istante `>= t`. E' il caso realistico: una feature che indicizza
   per tempo e sbaglia il confine di un'unita'.
2. la view ricorda il massimo `ts_local_ns` restituito, e il motore verifica
   dopo ogni decisione che sia `< t`. Se un giorno qualcuno allentasse il
   taglio del feed da `<` a `<=`, la verifica scatterebbe subito invece di
   produrre un backtest bellissimo.

Il limite di tutto questo, dichiarato perche' e' reale: la barriera protegge
cio' che passa DALLA VIEW. Una strategia che riceva nel costruttore un
dizionario di rendimenti futuri sta barando, e nessun controllo qui puo'
accorgersene — infatti il test delle etichette mescolate fa esattamente cosi',
di proposito.
"""

from __future__ import annotations

from costs import L2Book

from .events import BookEvent, TradeEvent


class LookAheadError(RuntimeError):
    """Una feature ha chiesto dati a un istante non ancora accaduto.

    Non e' un errore recuperabile: se scatta, il risultato del backtest
    prodotto fino a quel punto va buttato, non corretto.
    """


class MarketView:
    """Cio' che si sapeva a `as_of_ns`, e nient'altro."""

    __slots__ = ("as_of_ns", "bar_idx", "coins", "_books", "_trades",
                 "_closes", "_positions", "_equity", "max_ts_seen")

    def __init__(self, as_of_ns: int, bar_idx: int, coins: tuple[str, ...],
                 books: dict[str, BookEvent],
                 trades: dict[str, tuple[TradeEvent, ...]],
                 closes: dict[str, tuple[float, ...]],
                 positions: dict[str, float],
                 equity: float) -> None:
        self.as_of_ns = as_of_ns
        self.bar_idx = bar_idx
        self.coins = coins
        self._books = books
        self._trades = trades
        self._closes = closes
        self._positions = positions
        self._equity = equity
        self.max_ts_seen = -1

    # -- il guardiano ---------------------------------------------------------

    def _seen(self, ts_local_ns: int) -> int:
        """Ogni timestamp che esce da qui passa di qui. Vedi la docstring."""
        if ts_local_ns >= self.as_of_ns:
            raise LookAheadError(
                f"richiesto un dato a ts={ts_local_ns} con decisione a "
                f"ts={self.as_of_ns}: mancano {ts_local_ns - self.as_of_ns} ns "
                f"al futuro. La decisione usa solo dati STRETTAMENTE precedenti."
            )
        if ts_local_ns > self.max_ts_seen:
            self.max_ts_seen = ts_local_ns
        return ts_local_ns

    def at(self, coin: str, ts_local_ns: int) -> BookEvent | None:
        """Lo snapshot piu' recente non successivo a `ts_local_ns`.

        Esiste per dare una forma legittima alla domanda "com'era il book a
        quest'ora": senza, chi scrive una feature se la costruirebbe da solo
        tenendosi un riferimento, e nessun controllo la vedrebbe passare.
        """
        self._seen(ts_local_ns)
        ev = self._books.get(coin)
        if ev is None or ev.ts_local_ns > ts_local_ns:
            return None
        return ev

    # -- book ------------------------------------------------------------------

    def book_event(self, coin: str) -> BookEvent | None:
        ev = self._books.get(coin)
        if ev is None:
            return None
        self._seen(ev.ts_local_ns)
        return ev

    def book(self, coin: str) -> L2Book | None:
        ev = self.book_event(coin)
        return None if ev is None else ev.book

    def book_age_ns(self, coin: str) -> int | None:
        """Quanti nanosecondi ha lo snapshot piu' recente. Su Hyperliquid gli
        `l2Book` arrivano ogni ~5,38 s: qualche secondo e' la norma, non
        un'anomalia."""
        ev = self._books.get(coin)
        if ev is None:
            return None
        return self.as_of_ns - self._seen(ev.ts_local_ns)

    def mid(self, coin: str) -> float | None:
        b = self.book(coin)
        return None if b is None else b.mid

    # -- trade -----------------------------------------------------------------

    def trades(self, coin: str, n: int | None = None) -> tuple[TradeEvent, ...]:
        """Gli ultimi trade registrati, dal piu' vecchio al piu' recente.

        La finestra e' limitata (vedi `EngineConfig.trade_window`): il motore
        non tiene tutta la storia in memoria, e una strategia che ne avesse
        bisogno dovrebbe dichiararlo alzando quel numero, non scoprirlo per
        caso perche' il motore gliela regalava.
        """
        got = self._trades.get(coin, ())
        if got:
            self._seen(got[-1].ts_local_ns)
        return got if n is None else got[-n:]

    def last_price(self, coin: str) -> float | None:
        got = self._trades.get(coin, ())
        if not got:
            return None
        self._seen(got[-1].ts_local_ns)
        return got[-1].px

    # -- barre chiuse ----------------------------------------------------------

    def closes(self, coin: str, n: int | None = None) -> tuple[float, ...]:
        """Il mid alla chiusura di ogni barra gia' conclusa, in ordine.

        L'ultimo elemento e' la chiusura della barra appena finita: e' il dato
        piu' recente che una decisione possa usare, e per costruzione e' stato
        misurato a un istante `< as_of_ns`.
        """
        got = self._closes.get(coin, ())
        return got if n is None else got[-n:]

    # -- stato del conto -------------------------------------------------------

    def position(self, coin: str) -> float:
        """Posizione firmata in unita' di base: positiva long, negativa short."""
        return self._positions.get(coin, 0.0)

    @property
    def equity(self) -> float:
        return self._equity
