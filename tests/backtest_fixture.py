"""Mercato sintetico deterministico per i test del motore.

**Perche' sintetico.** I test avversariali chiedono di sapere la risposta
giusta prima di girare: "il PnL di una strategia casuale deve valere meno il
conto delle commissioni, entro l'errore statistico" ha senso solo se la
passeggiata dei prezzi e' una martingala per costruzione e la sua varianza e'
un numero scritto nel test, non stimato sui dati. Su dati veri quella stessa
asserzione misurerebbe il mercato invece del motore.

Il campione reale non sparisce: il motore ci gira sopra nell'esecuzione finale
del task, e `tests/test_backtest_feed.py` verifica su parquet veri la lettura,
la dedup e l'ordinamento. Qui si verifica l'aritmetica.

**Determinismo.** Ogni funzione prende un seme e usa un `random.Random`
proprio. Nessuna dipende dal modulo `random` globale, che sarebbe condiviso
con qualunque altro test giri nello stesso processo.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from costs import FundingSeries, L2Book, Level
from costs.funding import NS_PER_HOUR

from backtest.events import BookEvent, Event, TradeEvent

NS = 1_000_000_000
COIN = "TEST"

# Un'ora tonda: cosi' le frontiere di barra e i regolamenti di funding cadono
# su numeri interi e un'asserzione sbagliata si riconosce a occhio.
HOUR0 = 496_111
START_NS = HOUR0 * NS_PER_HOUR


@dataclass(frozen=True)
class Market:
    """Un mercato finito: eventi, finestra e i prezzi che li hanno generati."""

    events: list[Event]
    start_ns: int
    end_ns: int
    mids: list[float]
    step_sigma: float
    dt_s: int
    coin: str = COIN

    def bar_sigma(self, bar_s: int, size: float) -> float:
        """Deviazione standard del PnL di UNA barra tenuta con `size` unita'.

        La passeggiata ha passi indipendenti di deviazione `step_sigma`; in una
        barra ce ne stanno `bar_s / dt_s`, quindi la varianza si somma.
        """
        steps = bar_s / self.dt_s
        return abs(size) * self.step_sigma * (steps ** 0.5)


def book(coin: str, ts_ns: int, mid: float, half_spread: float,
         depth: float, levels: int = 3, src: str = "sintetico.parquet"
         ) -> BookEvent:
    """Book simmetrico attorno al mid, `levels` livelli distanti `half_spread`."""
    bids = tuple(Level(mid - half_spread * (1 + i), depth) for i in range(levels))
    asks = tuple(Level(mid + half_spread * (1 + i), depth) for i in range(levels))
    return BookEvent(
        ts_local_ns=ts_ns, coin=coin,
        book=L2Book(coin=coin, ts_local_ns=ts_ns, ts_exch_ms=ts_ns // 1_000_000,
                    bids=bids, asks=asks),
        ts_exch_ms=ts_ns // 1_000_000, src_file=src,
    )


def random_walk(n: int, seed: int, p0: float = 100.0,
                sigma: float = 0.02) -> list[float]:
    """Passeggiata aleatoria a incrementi gaussiani indipendenti.

    Media degli incrementi esattamente zero: nessun drift da confondere con un
    edge. `sigma` e' in unita' di prezzo, non in percentuale, cosi' la varianza
    del PnL si scrive senza passare per il livello del prezzo.
    """
    rng = random.Random(seed)
    out, p = [p0], p0
    for _ in range(n - 1):
        p += rng.gauss(0.0, sigma)
        out.append(p)
    return out


def market(n_bars: int = 120, bar_s: int = 60, dt_s: int = 5, seed: int = 1,
           p0: float = 100.0, sigma: float = 0.02, half_spread: float = 0.01,
           depth: float = 1_000.0, with_trades: bool = True,
           coin: str = COIN, warmup: int = 6) -> Market:
    """Un mercato di `n_bars` barre, con book ogni `dt_s` secondi.

    I primi `warmup` snapshot stanno PRIMA dell'inizio della finestra: alla
    prima frontiera di barra il motore deve gia' avere un book, altrimenti il
    primo ordine verrebbe rifiutato per assenza di dati e il test misurerebbe
    quello.

    I trade sono uno per snapshot, al mid e un secondo dopo: servono a tenere
    viva la finestra dei trade e a dare qualcosa da attraversare ai maker. Non
    generano fill da soli.
    """
    per_bar = bar_s // dt_s
    n = warmup + n_bars * per_bar + 1
    mids = random_walk(n, seed=seed, p0=p0, sigma=sigma)
    t0 = START_NS - warmup * dt_s * NS
    events: list[Event] = []
    for i, mid in enumerate(mids):
        ts = t0 + i * dt_s * NS
        events.append(book(coin, ts, mid, half_spread, depth))
        if with_trades:
            events.append(TradeEvent(ts_local_ns=ts + NS, coin=coin, px=mid,
                                     sz=depth / 10.0, side="B",
                                     tid=1_000_000 + i,
                                     time_ms=(ts + NS) // 1_000_000))
    events.sort(key=lambda e: (e.ts_local_ns, 0 if isinstance(e, BookEvent) else 1))
    return Market(events=events, start_ns=START_NS,
                  end_ns=START_NS + n_bars * bar_s * NS,
                  mids=mids[warmup:], step_sigma=sigma, dt_s=dt_s, coin=coin)


def funding(coin: str = COIN, hours: int = 24, rate: float = 1.25e-05,
            first_hour: int = HOUR0) -> FundingSeries:
    """Serie di funding piatta e completa sulla finestra.

    Il valore di default e' quello che Hyperliquid produce quando il premio
    resta dentro la banda del clamp: 0,00125% l'ora. Serve un rate diverso da
    zero, altrimenti il test di conservazione non verificherebbe il ramo del
    funding.
    """
    return FundingSeries.from_settlements(
        coin, {first_hour + i: rate for i in range(-1, hours + 2)}
    )


def bar_boundaries(m: Market, bar_s: int = 60) -> list[int]:
    step = bar_s * NS
    return list(range(m.start_ns, m.end_ns + 1, step))


def mid_at(m: Market, ts_ns: int) -> float:
    """Il mid dello snapshot piu' recente strettamente precedente a `ts_ns`."""
    best = None
    for ev in m.events:
        if isinstance(ev, BookEvent) and ev.ts_local_ns < ts_ns:
            best = ev.book.mid
        elif ev.ts_local_ns >= ts_ns:
            break
    if best is None:
        raise AssertionError(f"nessun book prima di {ts_ns}")
    return best
