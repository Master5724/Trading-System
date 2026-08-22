"""Strategie FITTIZIE. Nessuna di queste vuole guadagnare.

CLAUDE.md dice che nessuna strategia esiste ancora e non va inventata. Quelle
qui dentro servono al motore, non al mercato: una non fa niente, una tira i
dadi, una appoggia un limite lontano dal mid, una bara di proposito. Sono gli
strumenti con cui si misura il motore — se una di queste guadagna, il bug e'
nel motore.

**Il dimensionamento e' fisso in unita' di base**, deciso al primo book utile.
Ricalcolarlo a ogni barra dal notional produrrebbe un rivolo di ordini di
aggiustamento che sporcano il conto delle commissioni senza aggiungere niente
a cio' che i test devono verificare.
"""

from __future__ import annotations

import random
from typing import Sequence

from costs import Side

from .orders import Order, OrderKind
from .view import MarketView


class _Sized:
    """Base comune: converte un notional in una size di base, una volta sola."""

    def __init__(self, coin: str, notional: float) -> None:
        self.coin = coin
        self.notional = float(notional)
        self._size: float | None = None

    def size(self, view: MarketView) -> float | None:
        if self._size is None:
            mid = view.mid(self.coin)
            if mid is None:
                return None
            self._size = self.notional / mid
        return self._size

    def _to_target(self, view: MarketView, target: float) -> list[Order]:
        """Ordini per portare la posizione da dov'e' a `target`."""
        delta = target - view.position(self.coin)
        if abs(delta) < 1e-15:
            return []
        return [Order(coin=self.coin, side=Side.BUY if delta > 0 else Side.SELL,
                      size=abs(delta), kind=OrderKind.MARKET, tag=self.tag)]

    tag = ""


class Flat:
    """Segnale sempre piatto. Deve produrre PnL esattamente 0 e zero fee."""

    tag = "flat"

    def decide(self, view: MarketView) -> Sequence[Order]:
        return ()


class RandomTaker(_Sized):
    """Target casuale in {-1, 0, +1}, eseguito a mercato.

    Il generatore e' un `random.Random` col proprio seme: il modulo `random`
    globale renderebbe il risultato dipendente da chiunque altro abbia tirato
    un dado nello stesso processo, e il determinismo e' un requisito.
    """

    tag = "random"

    def __init__(self, coin: str, notional: float = 1_000.0, seed: int = 0,
                 p_change: float = 0.1) -> None:
        super().__init__(coin, notional)
        self.rng = random.Random(seed)
        self.p_change = float(p_change)
        self._target = 0.0

    def decide(self, view: MarketView) -> Sequence[Order]:
        unit = self.size(view)
        if unit is None:
            return ()
        if self.rng.random() < self.p_change:
            self._target = self.rng.choice((-1.0, 0.0, 1.0)) * unit
        return self._to_target(view, self._target)


class AlwaysLong(_Sized):
    """Compra alla prima barra utile e non molla. Serve al funding: e' l'unica
    strategia che tiene una posizione aperta per ore."""

    tag = "always_long"

    def decide(self, view: MarketView) -> Sequence[Order]:
        unit = self.size(view)
        if unit is None:
            return ()
        return self._to_target(view, unit)


class LabelFollower(_Sized):
    """Segue un'etichetta fornita DA FUORI: `labels[bar_idx]`.

    Bara di proposito, ed e' il punto. Con le etichette vere (il rendimento
    della barra successiva) deve guadagnare, altrimenti il motore non
    riuscirebbe a rappresentare nemmeno un edge che esiste. Con le stesse
    etichette mescolate deve tornare a zero: se resta un vantaggio, viene dal
    motore e non dal segnale.

    La barriera di `MarketView` non la ferma — non passa da li' — ed e' la
    ragione per cui quella barriera va dichiarata per quello che copre.
    """

    tag = "label"

    def __init__(self, coin: str, labels: dict[int, float],
                 notional: float = 1_000.0) -> None:
        super().__init__(coin, notional)
        self.labels = dict(labels)

    def decide(self, view: MarketView) -> Sequence[Order]:
        unit = self.size(view)
        if unit is None:
            return ()
        label = self.labels.get(view.bar_idx, 0.0)
        target = unit if label > 0 else (-unit if label < 0 else 0.0)
        return self._to_target(view, target)


class MakerQuote(_Sized):
    """Appoggia un limite a `offset_bps` dal mid, dal lato scelto.

    Esiste per esercitare la regola dell'attraversamento: un limite lontano dal
    mid viene eseguito solo se il prezzo ci passa attraverso davvero.
    """

    tag = "maker"

    def __init__(self, coin: str, side: Side = Side.BUY,
                 notional: float = 1_000.0, offset_bps: float = 5.0,
                 ttl_bars: int = 3) -> None:
        super().__init__(coin, notional)
        self.side = side
        self.offset_bps = float(offset_bps)
        self.ttl_bars = int(ttl_bars)

    def decide(self, view: MarketView) -> Sequence[Order]:
        unit = self.size(view)
        mid = view.mid(self.coin)
        if unit is None or mid is None:
            return ()
        off = mid * self.offset_bps / 1e4
        px = mid - off if self.side is Side.BUY else mid + off
        return [Order(coin=self.coin, side=self.side, size=unit,
                      kind=OrderKind.LIMIT, limit_px=px,
                      ttl_bars=self.ttl_bars, tag=self.tag)]


class PeeksAhead:
    """Chiede alla view un dato all'istante stesso della decisione.

    Non e' una strategia: e' il reagente del test anti look-ahead. Deve
    sollevare `LookAheadError` — se un giorno non lo facesse, il test
    diventerebbe rosso, che e' esattamente il comportamento voluto.
    """

    tag = "peek"

    def __init__(self, coin: str, offset_ns: int = 0) -> None:
        self.coin = coin
        self.offset_ns = int(offset_ns)

    def decide(self, view: MarketView) -> Sequence[Order]:
        view.at(self.coin, view.as_of_ns + self.offset_ns)
        return ()


BY_NAME = {
    "flat": Flat,
    "random": RandomTaker,
    "always_long": AlwaysLong,
    "maker": MakerQuote,
}
