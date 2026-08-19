"""La leva moltiplica il costo sul capitale, e nient'altro.

Fee, funding e slippage si applicano al notional. Il capitale impegnato e'
`notional / leva`. Quindi la stessa posizione, aperta a 1x, 2x e 5x, ha lo
stesso costo in dollari e un costo sul capitale in rapporto 1 : 2 : 5.

Sembra banale. L'errore non e' sbagliare la formula, e' non applicarla: un
round-trip che costa lo 0,19% del notional e' lo 0,95% del capitale a leva 5, e
una strategia che gira dieci volte al mese ne consuma il 9,5%. Questo file
esiste perche' quel fattore resti visibile in un test invece che implicito in
una moltiplicazione sparsa.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from costs import (
    CostModel,
    FundingSeries,
    Liquidity,
    LONG,
    equity_for,
    on_equity,
)
from costs.funding import NS_PER_HOUR
from tests.costs_fixture import simple_book

LEVE = (1.0, 2.0, 5.0)


class TestConversione(unittest.TestCase):
    def test_rapporto_uno_due_cinque(self):
        base = 0.0019          # 19 bps sul notional
        valori = [on_equity(base, lev) for lev in LEVE]
        self.assertAlmostEqual(valori[0], 0.0019, places=15)
        self.assertAlmostEqual(valori[1], 0.0038, places=15)
        self.assertAlmostEqual(valori[2], 0.0095, places=15)
        self.assertAlmostEqual(valori[1] / valori[0], 2.0, places=12)
        self.assertAlmostEqual(valori[2] / valori[0], 5.0, places=12)

    def test_capitale_impegnato(self):
        self.assertAlmostEqual(equity_for(1000.0, 1.0), 1000.0, places=12)
        self.assertAlmostEqual(equity_for(1000.0, 5.0), 200.0, places=12)

    def test_leva_non_positiva(self):
        for lev in (0.0, -1.0):
            with self.assertRaises(ValueError):
                on_equity(0.001, lev)


class TestRoundTripAtreLeve(unittest.TestCase):
    """Stessa posizione da 1.000 $ di notional, tre leve."""

    def setUp(self):
        self.book = simple_book(mid=100.0, half_spread=0.05, size=100.0)
        self.rt = CostModel().round_trip(1000.0, self.book,
                                         liquidity=Liquidity.TAKER)

    def test_il_costo_in_dollari_non_cambia(self):
        """La leva non rende l'operazione piu' cara: rende piu' piccolo il
        capitale che la sostiene."""
        self.assertAlmostEqual(self.rt.total, 1.90, places=10)   # 0,19% di 1000

    def test_costo_sul_capitale_in_rapporto_uno_due_cinque(self):
        costi = [self.rt.on_equity(lev) for lev in LEVE]
        self.assertAlmostEqual(costi[0], 0.0019, places=12)
        self.assertAlmostEqual(costi[1] / costi[0], 2.0, places=12)
        self.assertAlmostEqual(costi[2] / costi[0], 5.0, places=12)

    def test_costo_come_frazione_del_capitale_effettivo(self):
        """Controprova per divisione: costo in dollari / capitale in dollari,
        senza passare dalla funzione che si sta verificando."""
        for lev in LEVE:
            with self.subTest(leva=lev):
                capitale = self.rt.equity(lev)
                self.assertAlmostEqual(self.rt.on_equity(lev),
                                       self.rt.total / capitale, places=15)


class TestFundingAtreLeve(unittest.TestCase):
    """Il funding e' il costo che la leva amplifica di piu', perche' si accumula
    ora dopo ora mentre la posizione resta aperta."""

    def setUp(self):
        # 240 regolamenti da 0,001% = 0,24% sul notional in dieci giorni.
        self.series = FundingSeries.from_settlements(
            "X", {h: 0.00001 for h in range(240)}
        )
        self.cost = self.series.cost(LONG, 1000.0, 0, 240 * NS_PER_HOUR)

    def test_costo_sul_notional(self):
        self.assertEqual(self.cost.n_settlements, 240)
        self.assertAlmostEqual(self.cost.cost_frac, 0.0024, places=12)
        self.assertAlmostEqual(self.cost.cost, 2.40, places=10)

    def test_rapporto_uno_due_cinque(self):
        costi = [self.cost.on_equity(lev) for lev in LEVE]
        self.assertAlmostEqual(costi[0], 0.0024, places=12)
        self.assertAlmostEqual(costi[1] / costi[0], 2.0, places=12)
        self.assertAlmostEqual(costi[2] / costi[0], 5.0, places=12)


class TestCostoCompletoAtreLeve(unittest.TestCase):
    """Round-trip piu' funding insieme: e' il numero che una strategia swing
    paga davvero, ed e' quello che deve scalare 1 : 2 : 5."""

    def setUp(self):
        book = simple_book(mid=100.0, half_spread=0.05, size=100.0)
        series = FundingSeries.from_settlements(
            "X", {h: 0.00001 for h in range(240)}
        )
        funding = series.cost(LONG, 1000.0, 0, 240 * NS_PER_HOUR)
        self.rt = CostModel().round_trip(1000.0, book,
                                         liquidity=Liquidity.TAKER,
                                         funding=funding)

    def test_totale(self):
        # 1,90 $ di esecuzione + 2,40 $ di funding su 1.000 $ = 0,43%.
        self.assertAlmostEqual(self.rt.total, 4.30, places=10)
        self.assertAlmostEqual(self.rt.total_pct, 0.43, places=10)
        self.assertTrue(self.rt.complete)

    def test_rapporto_uno_due_cinque(self):
        costi = [self.rt.on_equity(lev) for lev in LEVE]
        self.assertAlmostEqual(costi[0], 0.0043, places=12)
        self.assertAlmostEqual(costi[1], 0.0086, places=12)
        self.assertAlmostEqual(costi[2], 0.0215, places=12)

    def test_a_leva_cinque_il_capitale_e_duecento_dollari(self):
        """Il conto per intero: 200 $ di capitale sostengono 1.000 $ di
        notional, e i 4,30 $ di costo sono il 2,15% di quel capitale."""
        self.assertAlmostEqual(self.rt.equity(5.0), 200.0, places=10)
        self.assertAlmostEqual(self.rt.total / 200.0, 0.0215, places=12)


class TestLaVerificaSaFallire(unittest.TestCase):
    def test_dimenticare_la_leva_appiattisce_tutto(self):
        """Se il costo sul capitale fosse restituito senza moltiplicare per la
        leva, i tre numeri sarebbero identici — e nessuno se ne accorgerebbe
        guardando un solo caso."""
        rt = CostModel().round_trip(
            1000.0, simple_book(mid=100.0, half_spread=0.05, size=100.0),
            liquidity=Liquidity.TAKER,
        )
        senza_leva = [rt.total_frac for _ in LEVE]     # l'errore
        con_leva = [rt.on_equity(lev) for lev in LEVE]
        self.assertEqual(len(set(senza_leva)), 1)
        with self.assertRaises(AssertionError):
            self.assertAlmostEqual(senza_leva[2], con_leva[2], places=6)

    def test_applicare_la_leva_al_notional_invece_che_al_capitale(self):
        """L'altro errore possibile: moltiplicare il notional per la leva
        invece di dividere il capitale. Il costo in dollari diventerebbe cinque
        volte tanto, cioe' il backtest pagherebbe commissioni che non esistono."""
        book = simple_book(mid=100.0, half_spread=0.05, size=100.0)
        giusto = CostModel().round_trip(1000.0, book, liquidity=Liquidity.TAKER)
        sbagliato = CostModel().round_trip(5000.0, book, liquidity=Liquidity.TAKER)
        self.assertAlmostEqual(sbagliato.total, 5 * giusto.total, places=8)
        with self.assertRaises(AssertionError):
            self.assertAlmostEqual(sbagliato.total, giusto.total, places=6)


if __name__ == "__main__":
    unittest.main()
