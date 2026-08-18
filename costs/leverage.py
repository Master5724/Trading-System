"""La leva non cambia il costo: cambia il capitale su cui lo si misura.

Fee, funding e slippage si applicano tutti al **notional** della posizione.
Il capitale impegnato e' `notional / leva`. Quindi lo stesso costo, espresso
in frazione del capitale, e' `leva` volte piu' grande:

    costo_sul_capitale = costo_sul_notional * leva

Sembra ovvio scritto cosi', e infatti l'errore non e' sbagliare la formula: e'
dimenticarsi di applicarla. Un costo di round-trip dello 0,09% del notional
non spaventa nessuno; lo stesso costo a leva 5 e' lo 0,45% del capitale a ogni
giro, cioe' il 4,5% dopo dieci giri. Questa funzione esiste per avere un posto
in cui quella conversione ha un nome, invece di comparire come una
moltiplicazione sparsa dentro un report.
"""

from __future__ import annotations


def on_equity(frac_of_notional: float, leverage: float = 1.0) -> float:
    """Converte una frazione del notional in frazione del capitale impegnato."""
    if leverage <= 0:
        raise ValueError(f"leva non positiva: {leverage}")
    return frac_of_notional * leverage


def equity_for(notional: float, leverage: float = 1.0) -> float:
    """Capitale (margine) necessario a sostenere `notional` a questa leva."""
    if leverage <= 0:
        raise ValueError(f"leva non positiva: {leverage}")
    if notional < 0:
        raise ValueError(f"notional negativo: {notional}")
    return notional / leverage
