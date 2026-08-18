"""Helper per i test di `costs/`: book costruiti a mano e campione reale.

Due tipi di dato, per due domande diverse:

- **book sintetici** con numeri tondi, per verificare l'aritmetica contro un
  calcolo fatto a mano e scritto nel test. Su prezzi come 99.95/100.05 il
  risultato atteso si scrive su un foglio, e un'asserzione che sbaglia si
  riconosce a occhio.
- **campione reale** (`tests/fixtures/costs_sample/`), estratto una volta dai
  dati di produzione da `make_costs_sample.py`, per verificare che il modello
  legga e allinei davvero i dati registrati. Nessun test tocca
  `/home/ubuntu/hl-data`: quella directory e' scritta da un collector vivo e i
  test devono essere deterministici.
"""

from __future__ import annotations

import os

from costs.slippage import L2Book, Level

SAMPLE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "fixtures", "costs_sample")

# Il campione copre queste coin. Fisse, perche' sono fisse nel fixture.
SAMPLE_FUNDING_COINS = ("BTC", "HYPE")
SAMPLE_BOOK_COINS = ("BTC", "ETH", "SOL", "HYPE")


def book(bids: list[tuple[float, float]], asks: list[tuple[float, float]],
         coin: str = "TEST", ts_local_ns: int = 1_785_808_800_000_000_000,
         ts_exch_ms: int = 1_785_808_800_000) -> L2Book:
    """Book da liste `(px, sz)`, gia' ordinate come le manda l'exchange."""
    return L2Book(
        coin=coin, ts_local_ns=ts_local_ns, ts_exch_ms=ts_exch_ms,
        bids=tuple(Level(px, sz) for px, sz in bids),
        asks=tuple(Level(px, sz) for px, sz in asks),
    )


def simple_book(mid: float = 100.0, half_spread: float = 0.05,
                size: float = 10.0) -> L2Book:
    """Un solo livello per lato, simmetrico attorno al mid.

    Con mid=100 e half_spread=0.05 il mezzo spread e' esattamente 5 bps e ogni
    numero atteso resta scrivibile a mente.
    """
    return book([(mid - half_spread, size)], [(mid + half_spread, size)])


def sample_available() -> bool:
    return os.path.isdir(SAMPLE_DIR)
