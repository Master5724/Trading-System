"""Il modello di costo del sistema. Uno solo (CLAUDE.md, invariante 4).

Backtest, paper e live importano da qui:

    from costs import CostModel, Liquidity, Side, L2Book

    model = CostModel()                       # tier base Hyperliquid
    book  = L2Book.from_payload(payload, ts)  # snapshot registrato
    cost  = model.round_trip(100.0, book, liquidity=Liquidity.TAKER)
    print(cost.total_pct, cost.breakeven_move_frac)

Nessun altro modulo del sistema calcola fee, funding o slippage. Se un giorno
serve un costo che questo modulo non sa dare, si estende questo modulo — non
si scrive il calcolo altrove "solo per il backtest". Quella e' la scorciatoia
che rende un backtest non confrontabile col live senza che niente fallisca.

I sottomoduli sono divisi per cosa costa:

    fees        commissioni maker/taker, tier configurabile
    slippage    cammino sul book L2 registrato, con dichiarazione di
                insufficienza invece di estrapolazione
    funding     costo di detenzione, orario, dai dati di activeAssetCtx
    leverage    conversione costo-su-notional -> costo-su-capitale
    model       la facciata: CostModel, ExecutionCost, RoundTripCost
    sources     lettura dai parquet (trade deduplicati, ore inaffidabili
                escluse dai buchi derivati dai dati)
    validate    confronto del modello con le esecuzioni realmente avvenute
    report      aggregazione per il report a schermo
"""

from __future__ import annotations

from .fees import HYPERLIQUID_PERP_BASE, FeeSchedule, Liquidity
from .funding import (
    FundingCost,
    FundingSeries,
    IncompleteFunding,
    settlement_hours,
)
from .leverage import equity_for, on_equity
from .model import DEFAULT, CostModel, ExecutionCost, RoundTripCost
from .side import LONG, SHORT, Side
from .slippage import (
    Fill,
    InsufficientDepth,
    InvalidBook,
    L2Book,
    Level,
)

__all__ = [
    "CostModel",
    "DEFAULT",
    "ExecutionCost",
    "RoundTripCost",
    "FeeSchedule",
    "Liquidity",
    "HYPERLIQUID_PERP_BASE",
    "FundingSeries",
    "FundingCost",
    "IncompleteFunding",
    "settlement_hours",
    "L2Book",
    "Level",
    "Fill",
    "InsufficientDepth",
    "InvalidBook",
    "Side",
    "LONG",
    "SHORT",
    "on_equity",
    "equity_for",
]
