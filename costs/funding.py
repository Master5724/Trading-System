"""Funding: quanto costa TENERE una posizione, dai dati registrati.

**Convenzione di segno.** Su Hyperliquid un funding rate positivo significa
che i long pagano i short. Quindi qui un costo positivo e' denaro uscito, per
entrambi i lati: un long con rate positivo paga, uno short con lo stesso rate
incassa (costo negativo). Il segno si ottiene da `Side.sign`, mai da un `if`
duplicato in giro per il codice.

**Un valore per ora, e quale.** `ctx.funding` dentro `activeAssetCtx` viene
ripubblicato circa una volta al secondo; sommare i campioni conterebbe lo
stesso funding migliaia di volte. Il funding si regola una volta all'ora,
quindi la serie giusta e' oraria e il campione scelto e' l'ultimo osservato
dentro l'ora.

**L'allineamento non e' un'assunzione: e' misurato.** L'ultimo campione di
`ctx.funding` osservato durante l'ora H coincide con il rate che il dump REST
`fundingHistory` attribuisce al regolamento all'inizio dell'ora H+1. Misurato
su 120 ore consecutive di BTC e HYPE (10-14 agosto 2026): differenza media e
massima **esattamente 0**. Non "circa": zero. Da qui la costruzione
`rates[H + 1] = ultimo_campione(H)` in `from_hourly_samples`, e il fatto che
`costs.sources` verifica lo stesso allineamento sui dati che carica.

**Le ore che non ci sono non valgono zero.** Zero e' un rate di funding
legittimo e frequente. Un'ora senza campioni, o un'ora la cui raccolta e'
attraversata da un buco derivato dai dati, non viene sostituita con zero:
viene contata a parte e `FundingCost.complete` diventa falso. Chi calcola un
PnL su una finestra incompleta deve saperlo (CLAUDE.md invariante 6); chi non
vuole nemmeno vedere il numero usa `strict=True` e riceve un'eccezione.

**L'ultimo regolamento derivato dai campioni non e' un fatto: e' provvisorio.**
`rates[H + 1]` e' l'ULTIMO campione dell'ora H. Finche' l'ora H e' in corso,
quel campione non e' ancora l'ultimo, e il valore cambia sotto chi legge: due
letture della stessa serie a un minuto di distanza danno due numeri diversi
(misurato su BTC il 2026-08-18: -7,72e-07 contro -5,04e-07 nella stessa
esecuzione). Un rate provvisorio ha lo stesso difetto di uno inaffidabile — il
numero c'e' ma non e' quello giusto — e quindi non entra in nessuna somma: e'
contato a parte in `n_provisional`. Senza questo, il report non e'
riproducibile e diverge dal catalogo di un'ora di funding senza che nulla lo
segnali. La serie REST non ha il problema: li' il timestamp e' il regolamento
gia' avvenuto.

**Cosa non e' modellato.** Il notional e' costante per tutta la detenzione: il
funding di ogni ora si applica alla stessa dimensione. E' l'approssimazione
standard, e sottostima leggermente il costo di una posizione in guadagno (il
cui notional cresce col prezzo) e lo sovrastima per una in perdita. Chi vuole
il calcolo esatto passa il notional ora per ora a `cost_by_settlement`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .leverage import on_equity
from .side import Side

NS_PER_HOUR = 3_600_000_000_000
HOURS_PER_DAY = 24


class IncompleteFunding(Exception):
    """La finestra richiesta contiene ore senza rate affidabile."""


def settlement_hours(start_ns: int, end_ns: int) -> range:
    """Indici orari dei regolamenti che ricadono in [start_ns, end_ns).

    Un regolamento avviene all'istante `h * NS_PER_HOUR`. La convenzione e'
    che si paga il funding di un regolamento se la posizione era aperta a
    quell'istante, estremo iniziale incluso ed estremo finale escluso: chi apre
    esattamente sull'ora paga quel regolamento, chi chiude esattamente
    sull'ora non lo paga.

    Nessuna delle due scelte e' "quella giusta" — sull'exchange l'esito di un
    ordine che arriva nello stesso istante del regolamento dipende dall'ordine
    di elaborazione, che non e' osservabile. E' una convenzione, e' dichiarata,
    ed e' l'unica usata da backtest, paper e live.
    """
    if end_ns < start_ns:
        raise ValueError(f"intervallo invertito: {start_ns} > {end_ns}")
    first = -(-start_ns // NS_PER_HOUR)          # ceil
    stop = -(-end_ns // NS_PER_HOUR)             # ceil, escluso
    return range(first, stop)


def hour_utc(hour_idx: int) -> str:
    return datetime.fromtimestamp(hour_idx * 3600, tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M"
    )


@dataclass(frozen=True)
class FundingCost:
    """Esito del calcolo su una finestra di detenzione.

    `cost` e' in valuta di quote e ha il segno del flusso: positivo = pagato.
    `cost_frac` e' la stessa cosa in frazione del notional, ed e' il numero da
    confrontare fra coin e fra periodi.

    I tre conteggi non sono ridondanti:
    - `n_missing`: regolamenti per cui non esiste nessun campione. Il collector
      era fermo o non aveva ancora iniziato.
    - `n_unreliable`: regolamenti la cui ora e' attraversata da un buco
      derivato dai dati. Un campione c'e', ma potrebbe essere vecchio di
      minuti rispetto all'istante di regolamento.
    - `n_provisional`: regolamenti il cui rate deriva da un'ora di
      campionamento non ancora conclusa. Il valore esiste ma cambiera'.
    - `n_known`: quelli effettivamente sommati.
    """

    coin: str
    side: Side
    notional: float
    start_ns: int
    end_ns: int
    n_settlements: int
    n_known: int
    n_missing: int
    n_unreliable: int
    rate_sum: float
    first_hour: int | None
    last_hour: int | None
    n_provisional: int = 0

    @property
    def cost(self) -> float:
        return self.side.sign * self.rate_sum * self.notional

    @property
    def cost_frac(self) -> float:
        """Frazione del notional. Positivo = costo."""
        return self.side.sign * self.rate_sum

    @property
    def cost_pct(self) -> float:
        return 100.0 * self.cost_frac

    @property
    def complete(self) -> bool:
        return (self.n_missing == 0 and self.n_unreliable == 0
                and self.n_provisional == 0)

    @property
    def coverage(self) -> float:
        if self.n_settlements == 0:
            return 1.0
        return self.n_known / self.n_settlements

    def on_equity(self, leverage: float = 1.0) -> float:
        """Costo in frazione del CAPITALE, non del notional. Vedi
        `costs.leverage`: a leva 5 lo stesso funding pesa cinque volte."""
        return on_equity(self.cost_frac, leverage)

    @property
    def annualized_pct(self) -> float:
        """Il costo esteso a un anno allo stesso ritmo. Serve a rendere
        confrontabile una finestra di 10 giorni con una di 3 — non e' una
        previsione, il funding cambia segno."""
        hours = (self.end_ns - self.start_ns) / NS_PER_HOUR
        if hours <= 0:
            return 0.0
        return self.cost_pct * (365.0 * 24.0 / hours)


@dataclass(frozen=True)
class FundingSeries:
    """Serie oraria dei rate, indicizzata per ora di REGOLAMENTO.

    `rates[h]` e' il rate applicato all'istante `h * NS_PER_HOUR`. Non e' il
    rate "osservato durante l'ora h": la traslazione avviene una volta sola, in
    `from_hourly_samples`, e da li' in poi l'indice significa una cosa sola.

    `unreliable` contiene le ore di regolamento il cui rate esiste ma proviene
    da una finestra di raccolta con un buco derivato dai dati.

    `provisional` contiene le ore di regolamento il cui rate deriva da un'ora
    di campionamento non ancora conclusa (vedi la docstring del modulo). E'
    separato da `unreliable` perche' le due cose si curano in modo diverso: un
    buco e' passato e restera' un buco, un rate provvisorio diventa definitivo
    da solo appena l'ora finisce.
    """

    coin: str
    rates: dict[int, float]
    unreliable: frozenset[int] = frozenset()
    provisional: frozenset[int] = frozenset()

    @classmethod
    def from_hourly_samples(cls, coin: str, samples: dict[int, float],
                            unreliable_sample_hours: frozenset[int] | set[int] = frozenset(),
                            ) -> FundingSeries:
        """Da `activeAssetCtx`: `samples[h]` = ultimo `ctx.funding` visto
        durante l'ora h. Il regolamento a cui si applica e' quello dell'ora h+1
        (misurato, vedi docstring del modulo).

        L'ora di campionamento piu' recente e' l'unica che puo' essere ancora
        in corso mentre si legge — il collector sta scrivendo dentro quella
        stessa ora — quindi il regolamento che ne deriva nasce provvisorio.
        """
        return cls(
            coin=coin,
            rates={h + 1: r for h, r in samples.items()},
            unreliable=frozenset(h + 1 for h in unreliable_sample_hours),
            provisional=frozenset([max(samples) + 1]) if samples else frozenset(),
        )

    @classmethod
    def from_settlements(cls, coin: str, rates: dict[int, float],
                         unreliable: frozenset[int] | set[int] = frozenset(),
                         ) -> FundingSeries:
        """Da `fundingHistory` (REST): il timestamp E' gia' l'istante del
        regolamento. E' la fonte di controllo, non la primaria: il backfill
        gira solo alla riconnessione e copre 48 h di lookback.

        Nessun rate provvisorio: un regolamento che il REST riporta e' un
        regolamento gia' avvenuto.
        """
        return cls(coin=coin, rates=dict(rates), unreliable=frozenset(unreliable))

    def __len__(self) -> int:
        return len(self.rates)

    @property
    def span(self) -> tuple[int, int] | None:
        if not self.rates:
            return None
        return min(self.rates), max(self.rates)

    @property
    def last_final(self) -> int | None:
        """L'ora di regolamento piu' recente il cui rate e' un fatto acquisito.

        E' l'ancora giusta per una finestra: `span[1]` puo' essere un rate che
        cambiera' fra un minuto, e ancorarcisi rende il numero irriproducibile.
        """
        final = [h for h in self.rates if h not in self.provisional]
        return max(final) if final else None

    def rate(self, hour_idx: int) -> float | None:
        """None significa "non lo so", e non deve mai diventare 0.0 per strada."""
        if hour_idx in self.unreliable or hour_idx in self.provisional:
            return None
        return self.rates.get(hour_idx)

    def cost(self, side: Side, notional: float, start_ns: int, end_ns: int,
             strict: bool = False) -> FundingCost:
        """Costo di funding di una posizione tenuta in [start_ns, end_ns)."""
        if notional < 0:
            raise ValueError(f"notional negativo: {notional}")
        hours = settlement_hours(start_ns, end_ns)
        rate_sum = 0.0
        n_known = n_missing = n_unreliable = n_provisional = 0
        first = last = None
        for h in hours:
            if h in self.unreliable:
                n_unreliable += 1
                continue
            if h in self.provisional:
                n_provisional += 1
                continue
            r = self.rates.get(h)
            if r is None:
                n_missing += 1
                continue
            rate_sum += r
            n_known += 1
            first = h if first is None else first
            last = h
        cost = FundingCost(
            coin=self.coin, side=side, notional=notional,
            start_ns=start_ns, end_ns=end_ns,
            n_settlements=len(hours), n_known=n_known,
            n_missing=n_missing, n_unreliable=n_unreliable,
            rate_sum=rate_sum, first_hour=first, last_hour=last,
            n_provisional=n_provisional,
        )
        if strict and not cost.complete:
            raise IncompleteFunding(
                f"{self.coin}: {cost.n_missing} regolamenti senza campione, "
                f"{cost.n_unreliable} in ore inaffidabili e "
                f"{cost.n_provisional} ancora provvisori su "
                f"{cost.n_settlements} fra {hour_utc(hours.start)} e "
                f"{hour_utc(hours.stop)} UTC"
            )
        return cost

    def cost_by_settlement(self, side: Side, notional_at: dict[int, float],
                           start_ns: int, end_ns: int) -> FundingCost:
        """Variante a notional variabile: `notional_at[h]` e' il notional della
        posizione all'istante del regolamento h. Esiste perche' l'ipotesi di
        notional costante e' un'approssimazione, e il backtester — che il
        notional ora per ora ce l'ha — non deve essere costretto a usarla.
        """
        hours = settlement_hours(start_ns, end_ns)
        total = 0.0
        weighted_notional = 0.0
        n_known = n_missing = n_unreliable = n_provisional = 0
        first = last = None
        for h in hours:
            n = notional_at.get(h)
            if h in self.unreliable:
                n_unreliable += 1
                continue
            if h in self.provisional:
                n_provisional += 1
                continue
            r = self.rates.get(h)
            if r is None or n is None:
                n_missing += 1
                continue
            total += r * n
            weighted_notional += n
            n_known += 1
            first = h if first is None else first
            last = h
        avg_notional = weighted_notional / n_known if n_known else 0.0
        return FundingCost(
            coin=self.coin, side=side, notional=avg_notional,
            start_ns=start_ns, end_ns=end_ns,
            n_settlements=len(hours), n_known=n_known,
            n_missing=n_missing, n_unreliable=n_unreliable,
            # rate_sum equivalente: quello che, moltiplicato per il notional
            # medio, riproduce il costo esatto. Cosi' `cost` resta una sola
            # formula per entrambe le varianti.
            rate_sum=(total / avg_notional) if avg_notional else 0.0,
            first_hour=first, last_hour=last,
            n_provisional=n_provisional,
        )

    def disagreement(self, other: FundingSeries) -> dict:
        """Confronto con una seconda fonte sulle ore in comune.

        Non e' un test decorativo: se la serie da `activeAssetCtx` e quella da
        `fundingHistory` divergono, il funding di ogni backtest e' sbagliato di
        quella quantita' e nessun'altra verifica se ne accorgerebbe.
        """
        # I rate provvisori sono esclusi da entrambi i lati: confrontare un
        # valore che cambiera' fra un minuto con un regolamento gia' avvenuto
        # non misura un disaccordo fra le fonti, misura l'ora corrente.
        common = sorted((set(self.rates) & set(other.rates))
                        - self.provisional - other.provisional)
        diffs = [abs(self.rates[h] - other.rates[h]) for h in common]
        return {
            "n_common": len(common),
            "n_only_self": len(set(self.rates) - set(other.rates)),
            "n_only_other": len(set(other.rates) - set(self.rates)),
            "max_abs_diff": max(diffs) if diffs else 0.0,
            "mean_abs_diff": (sum(diffs) / len(diffs)) if diffs else 0.0,
            "sum_self": sum(self.rates[h] for h in common),
            "sum_other": sum(other.rates[h] for h in common),
        }
