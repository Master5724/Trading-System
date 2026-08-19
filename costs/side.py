"""Il lato di un ordine e il lato di una posizione, che sono la stessa cosa.

Esiste come modulo separato per non far dipendere il funding dal book: sono
due calcoli senza niente in comune tranne questo enum, e un import fra loro
suggerirebbe un legame che non c'e'.

Una posizione **long** si apre comprando, quindi `LONG is Side.BUY`. Gli alias
esistono perche' scrivere `Side.BUY` nel calcolo del funding si legge male: il
funding non lo paga un ordine, lo paga una posizione.
"""

from __future__ import annotations

from enum import Enum


class Side(Enum):
    BUY = 1
    SELL = -1

    @property
    def sign(self) -> int:
        """+1 per un long/acquisto, -1 per uno short/vendita.

        E' il fattore che porta un prezzo o un rate a un costo: per un
        acquisto pagare piu' del mid e' un costo, per una vendita lo e'
        incassare meno.
        """
        return self.value

    @property
    def opposite(self) -> Side:
        return Side.SELL if self is Side.BUY else Side.BUY


LONG = Side.BUY
SHORT = Side.SELL
