"""Commissioni Hyperliquid: schedule con tier configurabile.

**Perche' il tier non e' cablato.** La fee dipende dal volume rolling a 14
giorni e dallo staking, cioe' da uno stato del conto che cambia nel tempo e
che questo modulo non puo' conoscere. Cablare "0,045%" dentro il backtester
significherebbe che il giorno in cui il conto cambia tier il backtest e il
live smettono silenziosamente di parlare della stessa cosa. Qui il tier e' un
oggetto che si passa, e il default e' il tier base — il peggiore, quindi il
piu' sicuro come default.

**Solo il tier base e' cablato, e questo e' voluto.** La tabella completa dei
tier non e' stata verificata contro l'exchange, e una tabella plausibile ma
sbagliata dentro il modulo di costo e' esattamente il tipo di errore che
CLAUDE.md descrive: non produce un'eccezione, produce un backtest che sembra
profittevole. Chi opera a un tier diverso costruisce la propria `FeeSchedule`
con i numeri che ha verificato sul proprio conto.

**Rebate maker.** `maker_rate` puo' essere negativa: ai tier alti Hyperliquid
paga il maker. Il codice non lo vieta e non lo assume — `fee()` restituisce un
numero negativo se il rate lo e', e chi somma i costi vedra' un accredito.

**Base di calcolo.** La fee si applica al notional ESEGUITO (prezzo medio di
esecuzione x size), non al notional nominale deciso al mid. Su size piccole la
differenza e' di pochi centesimi di punto base, ma e' la definizione giusta e
non costa niente rispettarla.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Liquidity(Enum):
    """Come l'ordine ha interagito col book.

    Non ha un default in nessun punto del modulo: la differenza fra maker e
    taker e' un fattore 3 sul costo, e un default silenzioso sarebbe il modo
    piu' economico di rendere ottimistico un backtest.
    """

    MAKER = "maker"
    TAKER = "taker"


@dataclass(frozen=True)
class FeeSchedule:
    """Tariffe in frazione del notional (0.00045 = 0,045% = 4,5 bps)."""

    maker_rate: float
    taker_rate: float
    tier: str = "base"

    def __post_init__(self) -> None:
        # Il taker che paga meno del maker esiste in teoria ma non su
        # Hyperliquid: se compare, e' quasi sempre uno scambio di argomenti
        # nel costruttore, e nessun test lo intercetterebbe.
        if self.taker_rate < self.maker_rate:
            raise ValueError(
                f"taker_rate ({self.taker_rate}) < maker_rate ({self.maker_rate}): "
                f"argomenti invertiti?"
            )
        if self.taker_rate < 0:
            raise ValueError(f"taker_rate negativa ({self.taker_rate})")

    def rate(self, liquidity: Liquidity) -> float:
        return self.maker_rate if liquidity is Liquidity.MAKER else self.taker_rate

    def fee(self, notional: float, liquidity: Liquidity) -> float:
        """Commissione in valuta di quote. `notional` e' sempre positivo:
        il lato (compra o vende) non cambia la fee."""
        if notional < 0:
            raise ValueError(f"notional negativo: {notional}")
        return notional * self.rate(liquidity)

    def round_trip_rate(self, liquidity: Liquidity) -> float:
        """Due esecuzioni dello stesso tipo. Esiste come metodo perche' il
        round-trip misto (entrata maker, uscita taker) e' il caso realistico e
        va scritto esplicitamente, non ottenuto raddoppiando per distrazione."""
        return 2.0 * self.rate(liquidity)

    def as_bps(self, liquidity: Liquidity) -> float:
        return 1e4 * self.rate(liquidity)

    @classmethod
    def from_mapping(cls, m: dict) -> FeeSchedule:
        """Costruzione da config. Le chiavi sono i rate in FRAZIONE, non in
        percentuale e non in bps: un config che dice `taker_rate: 0.045` invece
        di `0.00045` produrrebbe un costo 100 volte troppo alto senza errori,
        quindi il controllo di plausibilita' qui sotto rifiuta il caso."""
        s = cls(
            maker_rate=float(m["maker_rate"]),
            taker_rate=float(m["taker_rate"]),
            tier=str(m.get("tier", "custom")),
        )
        if s.taker_rate > 0.01:
            raise ValueError(
                f"taker_rate = {s.taker_rate}: oltre l'1% del notional. I rate "
                f"si esprimono in frazione (0.00045 = 4,5 bps), non in percentuale"
            )
        return s


# Tier base perp di Hyperliquid: 0,0150% maker, 0,0450% taker.
# E' il default di ogni entrypoint perche' e' il piu' caro: sbagliare per
# eccesso di costo produce un backtest pessimista, sbagliare per difetto
# produce una strategia che perde denaro vero.
HYPERLIQUID_PERP_BASE = FeeSchedule(
    maker_rate=0.000150,
    taker_rate=0.000450,
    tier="base",
)
