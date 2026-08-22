"""Il motore event-driven. Uno solo, per backtest e per paper trading.

    from backtest import Engine, EngineConfig, Order, OrderKind
    from backtest.strategies import Flat

    engine = Engine(EngineConfig(bar_s=60), Flat(), coins=["BTC"],
                    funding={"BTC": serie}, blocked=ore_bloccate)
    result = engine.run(eventi, start_ns, end_ns)
    print(result.final_equity, result.conservation_residual)

Cosa c'e' dentro, e perche' sta in file separati:

    events      gli eventi registrati, con la loro provenienza
    feed        parquet -> eventi, in ordine, senza reimplementare catalog
    view        la finestra sul passato che la strategia riceve (anti look-ahead)
    orders      ordini e motivi di rifiuto
    fills       taker sul book camminato, maker solo per attraversamento
    portfolio   posizione, cassa, PnL
    journal     giornale operazioni e curva di equity, riproducibili
    engine      il ciclo: barre, buchi, decisione, esecuzione
    strategies  strategie FITTIZIE, per misurare il motore

Il modello di costo non e' qui: e' `costs/`, importato com'e' (invariante 4 di
CLAUDE.md). In questo pacchetto non esiste una riga che calcoli una fee, un
funding o uno slippage.
"""

from __future__ import annotations

from .engine import BlockedHours, Engine, EngineConfig, Result, Strategy
from .events import BookEvent, Event, TradeEvent
from .journal import Journal
from .orders import Order, OrderKind, Reject
from .portfolio import Portfolio, Position
from .view import LookAheadError, MarketView

__all__ = [
    "Engine",
    "EngineConfig",
    "BlockedHours",
    "Result",
    "Strategy",
    "BookEvent",
    "TradeEvent",
    "Event",
    "Journal",
    "Order",
    "OrderKind",
    "Reject",
    "Portfolio",
    "Position",
    "MarketView",
    "LookAheadError",
]
