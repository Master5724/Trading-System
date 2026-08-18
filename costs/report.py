"""Il modello di costo eseguito sui dati registrati, coin per coin.

Non aggiunge modello: chiama `CostModel` esattamente come lo chiamera' il
backtester, e ne aggrega gli esiti. Se un numero di questo report fosse
calcolato qui invece che dal modello, il report smetterebbe di essere una
verifica del modello e diventerebbe una seconda implementazione — cioe' la
cosa che l'invariante 4 vieta.

**Perche' mediane e non medie.** Lo spread ha una coda destra lunga (i momenti
di stress) che sposta la media di parecchio e che nessun ordine e' costretto a
subire: si puo' scegliere di non operare in quei momenti. La mediana descrive
il costo dell'operativita' normale. Le code restano visibili nel p90 e nel p99,
stampati accanto.
"""

from __future__ import annotations

from dataclasses import dataclass

from .fees import Liquidity
from .funding import NS_PER_HOUR, FundingSeries
from .model import CostModel
from .side import LONG, SHORT, Side
from .slippage import InsufficientDepth, L2Book

HOURS_PER_DAY = 24


def quantile(values: list[float], q: float) -> float | None:
    """Percentile per interpolazione lineare. Nessuna dipendenza esterna per
    una funzione di sei righe che verrebbe importata da numpy con tutto il
    resto dietro."""
    if not values:
        return None
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    pos = q * (len(s) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


@dataclass
class _Acc:
    """Accumulatore delle osservazioni per una size."""

    notional: float
    half_spread_bps: list[float]
    impact_bps: list[float]
    slippage_bps: list[float]
    n_insufficient: int = 0
    n_observations: int = 0

    def summary(self) -> dict:
        return {
            "notional_usd": self.notional,
            "n_osservazioni": self.n_observations,
            "n_size_oltre_book": self.n_insufficient,
            "half_spread_bps_p50": quantile(self.half_spread_bps, 0.50),
            "impact_bps_p50": quantile(self.impact_bps, 0.50),
            "impact_bps_p90": quantile(self.impact_bps, 0.90),
            "impact_bps_max": max(self.impact_bps) if self.impact_bps else None,
            "slippage_bps_p50": quantile(self.slippage_bps, 0.50),
            "slippage_bps_p90": quantile(self.slippage_bps, 0.90),
            "slippage_bps_p99": quantile(self.slippage_bps, 0.99),
        }


def slippage_over_books(model: CostModel, books, notionals: list[float],
                        sides: tuple[Side, ...] = (Side.BUY, Side.SELL),
                        ) -> tuple[dict[float, dict], dict]:
    """Cammina il book per ogni size e ogni lato, su ogni snapshot campionato.

    Restituisce anche le statistiche del round-trip taker e maker sul primo
    notional della lista: sono calcolate nella stessa passata perche' rileggere
    gli snapshot una seconda volta costerebbe quanto tutto il resto.

    Entrambi i lati e non solo l'acquisto: il book non e' simmetrico, e una
    strategia che entra long ed esce vendendo paga i due lati in momenti
    diversi. Aggregarli insieme e' la scelta giusta per una statistica di
    costo, e le due distribuzioni restano separabili dai dati grezzi.
    """
    accs = {n: _Acc(n, [], [], []) for n in notionals}
    rt_taker_pct: list[float] = []
    rt_maker_pct: list[float] = []
    spread_bps: list[float] = []
    n_books = 0
    rt_notional = notionals[0]

    for book in books:
        n_books += 1
        spread_bps.append(book.spread_bps)
        for notional in notionals:
            acc = accs[notional]
            size = book.size_for_notional(notional)
            for side in sides:
                fill = book.walk(side, size)
                acc.n_observations += 1
                if not fill.sufficient:
                    acc.n_insufficient += 1
                    continue
                acc.half_spread_bps.append(fill.half_spread_bps)
                acc.impact_bps.append(fill.impact_bps)
                acc.slippage_bps.append(fill.slippage_bps)
        try:
            rt_taker_pct.append(
                model.round_trip(rt_notional, book,
                                 liquidity=Liquidity.TAKER).total_pct
            )
        except InsufficientDepth:
            # Solo questa: il round-trip su uno snapshot troppo sottile viene
            # saltato e resta contato in `n_size_oltre_book`. Qualunque altra
            # eccezione e' un errore del modello e deve arrivare fino in cima,
            # non finire in una mediana.
            pass
        rt_maker_pct.append(
            model.round_trip(rt_notional, book,
                             liquidity=Liquidity.MAKER).total_pct
        )

    summary = {
        "n_snapshot": n_books,
        "spread_bps_p50": quantile(spread_bps, 0.50),
        "spread_bps_p90": quantile(spread_bps, 0.90),
        "round_trip_notional_usd": rt_notional,
        "round_trip_taker_pct_p50": quantile(rt_taker_pct, 0.50),
        "round_trip_taker_pct_p90": quantile(rt_taker_pct, 0.90),
        "round_trip_maker_pct_p50": quantile(rt_maker_pct, 0.50),
        "round_trip_taker_n": len(rt_taker_pct),
    }
    return {n: a.summary() for n, a in accs.items()}, summary


def funding_window(series: FundingSeries, days: int) -> tuple[int, int] | None:
    """Gli ultimi `days` giorni di regolamenti DEFINITIVI, in nanosecondi.

    Ancorata all'ultimo regolamento presente nella serie e non all'orologio: un
    report che gira mentre il collector e' fermo da un'ora deve descrivere i
    dati che ha, non una finestra che finisce nel vuoto.

    "Definitivo" e non "ultimo": il regolamento derivato dall'ora di
    campionamento in corso e' provvisorio (vedi `costs.funding`), e ancorarci
    la finestra faceva due danni insieme — il report cambiava numero a ogni
    esecuzione, e la finestra scivolava di un'ora rispetto a quella del
    catalogo, che l'ora in corso la scarta gia'. Era l'intera causa della
    divergenza residua fra i due moduli.
    """
    last = series.last_final
    if last is None:
        return None
    first = last - days * HOURS_PER_DAY + 1
    return first * NS_PER_HOUR, (last + 1) * NS_PER_HOUR


def funding_report(series: FundingSeries, rest: FundingSeries | None,
                   notional: float, days: int,
                   leverages: tuple[float, ...] = (1.0, 2.0, 5.0)) -> dict:
    """Funding di un long e di uno short tenuti `days` giorni, piu' il
    controllo incrociato con la serie REST sulla stessa finestra.

    Il long e lo short non sono l'uno l'opposto dell'altro per definizione:
    lo sono per questo modello, e stamparli entrambi rende visibile se un
    giorno smettessero di esserlo (per esempio se qualcuno introducesse un
    costo asimmetrico).
    """
    window = funding_window(series, days)
    if window is None:
        return {"disponibile": False}
    start_ns, end_ns = window
    long_cost = series.cost(LONG, notional, start_ns, end_ns)
    short_cost = series.cost(SHORT, notional, start_ns, end_ns)
    out = {
        "disponibile": True,
        "giorni": days,
        "notional_usd": notional,
        "n_regolamenti": long_cost.n_settlements,
        "n_noti": long_cost.n_known,
        "n_mancanti": long_cost.n_missing,
        "n_inaffidabili": long_cost.n_unreliable,
        "n_provvisori": long_cost.n_provisional,
        "completa": long_cost.complete,
        "long_pct": long_cost.cost_pct,
        "short_pct": short_cost.cost_pct,
        "long_usd": long_cost.cost,
        "short_usd": short_cost.cost,
        "long_annualizzato_pct": long_cost.annualized_pct,
        "long_su_capitale_pct": {
            f"{lev:g}x": 100.0 * long_cost.on_equity(lev) for lev in leverages
        },
    }
    if rest is not None:
        rest_cost = rest.cost(LONG, notional, start_ns, end_ns)
        out["controllo_rest"] = {
            **series.disagreement(rest),
            "long_pct_rest": rest_cost.cost_pct,
            "n_noti_rest": rest_cost.n_known,
            "differenza_pct": long_cost.cost_pct - rest_cost.cost_pct,
        }
    return out


def format_coin(coin: str, sizes: dict[float, dict], execution: dict,
                funding: dict, trades: dict | None) -> list[str]:
    """Il report a schermo. Numeri in bps dove servono tre decimali di
    risoluzione, in percentuale dove il lettore ragiona in percentuale."""
    L = [f"", f"=== {coin} ===",
         f"  snapshot usati: {execution['n_snapshot']:,}   "
         f"spread mediano: {execution['spread_bps_p50']:.3f} bps "
         f"(p90 {execution['spread_bps_p90']:.3f})"]
    if trades:
        L.append(
            f"  trade (dedup per tid): {trades['n_trades']:,}   "
            f"notional mediano {trades['notional_p50']:,.0f} $   "
            f"p99 {trades['notional_p99']:,.0f} $"
        )
    rt_n = execution["round_trip_notional_usd"]
    L += [
        f"  round-trip su {rt_n:,.0f} $ (mediana sugli snapshot):",
        f"      taker  {execution['round_trip_taker_pct_p50']:.4f} %   "
        f"(p90 {execution['round_trip_taker_pct_p90']:.4f} %)",
        f"      maker  {execution['round_trip_maker_pct_p50']:.4f} %   "
        f"(limite inferiore: niente selezione avversa, niente mancata esecuzione)",
        f"  slippage per size (mediana su acquisti e vendite):",
        f"      {'size $':>8}  {'mezzo spread':>13}  {'impatto p50':>12}  "
        f"{'impatto p90':>12}  {'totale p50':>11}  {'totale p99':>11}  oltre book",
    ]
    for notional in sorted(sizes):
        s = sizes[notional]
        def f(key, fmt="{:.4f}"):
            v = s[key]
            return fmt.format(v) if v is not None else "n/d"
        L.append(
            f"      {notional:>8,.0f}  {f('half_spread_bps_p50'):>13}  "
            f"{f('impact_bps_p50'):>12}  {f('impact_bps_p90'):>12}  "
            f"{f('slippage_bps_p50'):>11}  {f('slippage_bps_p99'):>11}  "
            f"{s['n_size_oltre_book']:>10,}"
        )
    L.append("      (bps sul mid; 'oltre book' = size che eccede i 10 livelli)")

    if funding.get("disponibile"):
        L += [
            f"  funding su {funding['giorni']} giorni "
            f"({funding['n_noti']}/{funding['n_regolamenti']} regolamenti noti, "
            f"{funding['n_mancanti']} mancanti, {funding['n_inaffidabili']} "
            f"in ore inaffidabili, {funding['n_provvisori']} provvisori):",
            f"      long   {funding['long_pct']:+.4f} %   "
            f"short  {funding['short_pct']:+.4f} %   "
            f"(annualizzato long {funding['long_annualizzato_pct']:+.1f} %)",
            "      long sul capitale: " + "   ".join(
                f"{k} {v:+.4f} %" for k, v in funding["long_su_capitale_pct"].items()
            ),
        ]
        cr = funding.get("controllo_rest")
        if cr:
            L.append(
                f"      controllo REST: {cr['n_common']} ore in comune, "
                f"differenza max {cr['max_abs_diff']:.3e}, "
                f"cumulato REST {cr['long_pct_rest']:+.4f} % "
                f"(scarto {cr['differenza_pct']:+.6f} punti)"
            )
    else:
        L.append("  funding: nessun dato")
    return L
