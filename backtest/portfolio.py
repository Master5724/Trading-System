"""Posizione, cassa e PnL. La contabilita', tenuta in un punto solo.

**Tre contatori indipendenti, una sola identita' che li lega.** `cash` e' un
saldo che si muove a ogni evento (un fill, un regolamento di funding);
`realized_pnl`, `fees_paid` e `funding_paid` sono somme accumulate a parte.
Nessuno dei due lati e' definito in funzione dell'altro, quindi

    equity_finale == equity_iniziale + realizzato - fee - funding

e' una verifica vera e non una tautologia: passa solo se i due percorsi di
aggiornamento sono d'accordo. E' il test di conservazione preteso da CLAUDE.md,
e il test lo ricalcola dal GIORNALE scritto su disco, non da questi campi.

**Lo slippage non compare nell'identita', ed e' corretto cosi'.** Un fill taker
avviene al prezzo medio ottenuto camminando il book: mezzo spread e impatto
sono gia' dentro quel prezzo, quindi dentro il PnL. Sottrarli una seconda volta
come voce di costo li conterebbe due volte. Restano nel giornale come
scomposizione diagnostica — quanto del prezzo era mid, quanto spread, quanto
impatto — e quella scomposizione viene da `costs`, non da qui.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class Position:
    """Posizione netta su una coin. `size` firmata, `avg_px` prezzo medio di
    carico della parte aperta."""

    size: float = 0.0
    avg_px: float = 0.0

    def unrealized(self, mark: float) -> float:
        return self.size * (mark - self.avg_px)


@dataclass(slots=True)
class Portfolio:
    """Il conto. Non conosce ne' book ne' strategia: riceve fill gia' decisi."""

    initial_equity: float
    cash: float = 0.0
    realized_pnl: float = 0.0
    fees_paid: float = 0.0
    funding_paid: float = 0.0
    positions: dict[str, Position] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.cash == 0.0:
            self.cash = self.initial_equity

    def position(self, coin: str) -> Position:
        p = self.positions.get(coin)
        if p is None:
            p = Position()
            self.positions[coin] = p
        return p

    def size(self, coin: str) -> float:
        p = self.positions.get(coin)
        return 0.0 if p is None else p.size

    def apply_fill(self, coin: str, signed_size: float, px: float,
                   fee: float) -> float:
        """Applica un fill e restituisce il PnL realizzato che ne e' uscito.

        La parte che chiude paga il conto al prezzo medio di carico; la parte
        che apre (o che ribalta il segno) riparte dal prezzo del fill. Un
        ribaltamento in un colpo solo — da +2 a -1 con una vendita di 3 — non
        e' un caso di scuola: e' cio' che fa una strategia che inverte, e
        trattarlo male sposterebbe PnL fra realizzato e non realizzato senza
        che nessun totale se ne accorga.
        """
        if signed_size == 0.0:
            raise ValueError("fill di size nulla")
        if px <= 0:
            raise ValueError(f"prezzo non positivo: {px}")
        p = self.position(coin)
        realized = 0.0
        if p.size != 0.0 and (p.size > 0) != (signed_size > 0):
            closing = min(abs(p.size), abs(signed_size))
            realized = closing * (px - p.avg_px) * (1.0 if p.size > 0 else -1.0)
            new_size = p.size + signed_size
            if (new_size > 0) == (p.size > 0) and new_size != 0.0:
                pass                      # riduzione: il carico non cambia
            elif new_size == 0.0:
                p.avg_px = 0.0
            else:
                p.avg_px = px             # ribaltata: la parte nuova nasce qui
            p.size = new_size
        else:
            total = abs(p.size) + abs(signed_size)
            p.avg_px = (p.avg_px * abs(p.size) + px * abs(signed_size)) / total
            p.size = p.size + signed_size
        self.realized_pnl += realized
        self.fees_paid += fee
        self.cash += realized - fee
        return realized

    def apply_funding(self, amount: float) -> None:
        """`amount` col segno del flusso: positivo = pagato, negativo = incassato."""
        self.funding_paid += amount
        self.cash -= amount

    def unrealized(self, marks: dict[str, float]) -> float:
        total = 0.0
        for coin in sorted(self.positions):
            p = self.positions[coin]
            if p.size == 0.0:
                continue
            m = marks.get(coin)
            if m is None:
                raise KeyError(f"{coin}: posizione aperta senza prezzo di mark")
            total += p.unrealized(m)
        return total

    def equity(self, marks: dict[str, float]) -> float:
        return self.cash + self.unrealized(marks)

    @property
    def flat(self) -> bool:
        return all(p.size == 0.0 for p in self.positions.values())
