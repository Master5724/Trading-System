"""Commissioni e costo di un round-trip, contro un calcolo fatto a mano.

Il test centrale (`TestRoundTripTakerAMano`) non usa nessuna costante presa dal
codice: i numeri sono scritti nel test, derivati passo per passo nel commento,
e verificabili senza eseguire niente. E' l'unico modo per cui questo file puo'
accorgersi che il modello e' cambiato — un test che ricalcola il valore atteso
con le stesse funzioni che verifica non fallisce mai.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from costs import CostModel, FeeSchedule, Liquidity, Side
from costs.fees import HYPERLIQUID_PERP_BASE
from tests.costs_fixture import simple_book


class TestSchedule(unittest.TestCase):
    def test_tier_base_hyperliquid(self):
        """0,0150% maker e 0,0450% taker sui perp: il tier di partenza."""
        self.assertEqual(HYPERLIQUID_PERP_BASE.maker_rate, 0.000150)
        self.assertEqual(HYPERLIQUID_PERP_BASE.taker_rate, 0.000450)
        self.assertAlmostEqual(HYPERLIQUID_PERP_BASE.as_bps(Liquidity.MAKER), 1.5)
        self.assertAlmostEqual(HYPERLIQUID_PERP_BASE.as_bps(Liquidity.TAKER), 4.5)

    def test_tier_configurabile(self):
        """Il tier non e' cablato: si passa, e il modello lo usa."""
        s = FeeSchedule(maker_rate=0.00010, taker_rate=0.00030, tier="vip1")
        self.assertEqual(s.fee(1000.0, Liquidity.TAKER), 0.30)
        self.assertEqual(s.fee(1000.0, Liquidity.MAKER), 0.10)
        self.assertEqual(CostModel(fees=s).fees.tier, "vip1")

    def test_rebate_maker_ammesso(self):
        """Ai tier alti il maker viene pagato: un rate negativo deve passare e
        produrre un costo negativo, non un errore."""
        s = FeeSchedule(maker_rate=-0.00002, taker_rate=0.00025, tier="vip4")
        self.assertLess(s.fee(1000.0, Liquidity.MAKER), 0.0)

    def test_maker_e_taker_non_si_scambiano(self):
        with self.assertRaises(ValueError):
            FeeSchedule(maker_rate=0.00045, taker_rate=0.00015)

    def test_percentuale_al_posto_della_frazione(self):
        """`taker_rate: 0.045` in un config significherebbe 4,5% e nessuna
        eccezione: e' l'errore da cento volte che questo controllo intercetta."""
        with self.assertRaises(ValueError):
            FeeSchedule.from_mapping({"maker_rate": 0.015, "taker_rate": 0.045})

    def test_liquidity_senza_default(self):
        """Chi calcola una fee deve dichiarare se e' maker o taker: la
        differenza e' un fattore tre."""
        with self.assertRaises(TypeError):
            HYPERLIQUID_PERP_BASE.fee(1000.0)  # type: ignore[call-arg]


class TestRoundTripTakerAMano(unittest.TestCase):
    """Il conto, per esteso.

    Book: bid 99.95 x 10, ask 100.05 x 10. Quindi mid = 100.00 esatto,
    spread = 0.10, mezzo spread = 0.05 = 5 bps del mid.

    Ordine: 100 $ di notional nominale. La size si decide al mid:
        size = 100 / 100 = 1.0 unita'.

    ENTRATA (compra, taker). La size sta dentro il primo livello (1.0 < 10),
    quindi esegue tutta a 100.05:
        notional eseguito = 1.0 x 100.05         = 100.05 $
        fee               = 100.05 x 0.00045     =   0.0450225 $
        slippage vs mid   = (100.05 - 100) x 1.0 =   0.05 $      (= 5 bps)
        impatto                                   =   0 $  (un solo livello)

    USCITA (vende la stessa size, taker), a 99.95:
        notional eseguito = 1.0 x 99.95          =  99.95 $
        fee               =  99.95 x 0.00045     =   0.0449775 $
        slippage vs mid   = (100 - 99.95) x 1.0  =   0.05 $

    TOTALE:
        fee      = 0.0450225 + 0.0449775 = 0.09 $ esatti
                   (perche' 100.05 + 99.95 = 200.00, e 200 x 0.00045 = 0.09)
        slippage = 0.05 + 0.05           = 0.10 $
        costo    = 0.19 $ su 100 $ di notional = 0.19 % = 19 bps

    E 19 bps e' anche 2 x 4,5 (fee) + 2 x 5 (mezzo spread), che e' il modo
    veloce di controllare il risultato a mente.
    """

    def setUp(self):
        self.model = CostModel()          # tier base
        self.book = simple_book(mid=100.0, half_spread=0.05, size=10.0)

    def test_componenti_dell_entrata(self):
        e = self.model.execution(self.book, Side.BUY, 100.0, Liquidity.TAKER)
        self.assertAlmostEqual(e.size, 1.0, places=12)
        self.assertAlmostEqual(e.notional_executed, 100.05, places=10)
        self.assertAlmostEqual(e.fee, 0.0450225, places=10)
        self.assertAlmostEqual(e.spread, 0.05, places=10)
        self.assertEqual(e.impact, 0.0)
        self.assertAlmostEqual(e.total, 0.0950225, places=10)

    def test_componenti_dell_uscita(self):
        e = self.model.execution(self.book, Side.SELL, 100.0, Liquidity.TAKER)
        self.assertAlmostEqual(e.notional_executed, 99.95, places=10)
        self.assertAlmostEqual(e.fee, 0.0449775, places=10)
        self.assertAlmostEqual(e.spread, 0.05, places=10)

    def test_round_trip_totale(self):
        rt = self.model.round_trip(100.0, self.book, liquidity=Liquidity.TAKER)
        self.assertAlmostEqual(rt.fee, 0.09, places=10)
        self.assertAlmostEqual(rt.slippage, 0.10, places=10)
        self.assertAlmostEqual(rt.total, 0.19, places=10)
        self.assertAlmostEqual(rt.total_pct, 0.19, places=10)
        self.assertAlmostEqual(rt.total_bps, 19.0, places=8)

    def test_breakeven(self):
        """Il prezzo deve muoversi di 19 bps perche' il trade vada in pari."""
        rt = self.model.round_trip(100.0, self.book, liquidity=Liquidity.TAKER)
        self.assertAlmostEqual(rt.breakeven_move_frac, 0.0019, places=10)

    def test_round_trip_maker(self):
        """Maker: nessun attraversamento dello spread, solo due fee da 1,5 bps.
        E' un limite inferiore dichiarato, non una previsione (vedi
        `costs.model`): qui si verifica che sia esattamente 2 x 1,5 bps."""
        rt = self.model.round_trip(100.0, self.book, liquidity=Liquidity.MAKER)
        self.assertEqual(rt.slippage, 0.0)
        self.assertAlmostEqual(rt.total, 0.03, places=10)
        self.assertAlmostEqual(rt.total_pct, 0.03, places=10)

    def test_short_costa_come_il_long(self):
        """Un round-trip aperto in vendita paga lo stesso: il book e'
        simmetrico e nessuna asimmetria deve comparire dal nulla."""
        long_rt = self.model.round_trip(100.0, self.book, side=Side.BUY,
                                        liquidity=Liquidity.TAKER)
        short_rt = self.model.round_trip(100.0, self.book, side=Side.SELL,
                                         liquidity=Liquidity.TAKER)
        self.assertAlmostEqual(long_rt.total, short_rt.total, places=12)

    def test_costo_proporzionale_al_notional(self):
        """Finche' la size resta dentro il primo livello il costo e' lineare:
        dieci volte il notional, dieci volte il costo, stessa percentuale."""
        a = self.model.round_trip(100.0, self.book, liquidity=Liquidity.TAKER)
        b = self.model.round_trip(1000.0, self.book, liquidity=Liquidity.TAKER)
        self.assertAlmostEqual(b.total, 10 * a.total, places=10)
        self.assertAlmostEqual(b.total_pct, a.total_pct, places=12)


class TestLaVerificaSaFallire(unittest.TestCase):
    """I test sopra devono poter diventare rossi.

    Ognuno di questi disabilita la logica che il test corrispondente difende e
    pretende che l'asserzione esploda. Un test che non puo' fallire non e' un
    test — e su un modulo che nessuna eccezione protegge, e' l'unica difesa che
    resta.
    """

    def test_fee_taker_al_posto_della_maker_cambia_il_totale(self):
        """Se il modello usasse la fee maker per un'esecuzione taker, il
        round-trip costerebbe 0,03% invece di 0,19%: la differenza e' sei
        volte, e il test del round-trip la vedrebbe."""
        book = simple_book(mid=100.0, half_spread=0.05, size=10.0)
        taker = CostModel().round_trip(100.0, book, liquidity=Liquidity.TAKER)
        maker = CostModel().round_trip(100.0, book, liquidity=Liquidity.MAKER)
        with self.assertRaises(AssertionError):
            self.assertAlmostEqual(taker.total, maker.total, places=6)

    def test_rate_in_percentuale_produrrebbe_un_costo_cento_volte_maggiore(self):
        """La verifica di plausibilita' di `from_mapping` serve a questo: senza,
        un config sbagliato passa e il backtest paga l'1,9% invece dello 0,19%."""
        sbagliato = FeeSchedule(maker_rate=0.015, taker_rate=0.045, tier="rotto")
        book = simple_book()
        rt = CostModel(fees=sbagliato).round_trip(100.0, book,
                                                  liquidity=Liquidity.TAKER)
        self.assertGreater(rt.total_pct, 9.0)
        with self.assertRaises(AssertionError):
            self.assertAlmostEqual(rt.total_pct, 0.19, places=4)

    def test_ignorare_lo_spread_renderebbe_il_taker_uguale_al_maker(self):
        """Contro-prova diretta del fatto che il mezzo spread e' contato: se lo
        si togliesse dal taker, resterebbero solo le fee."""
        book = simple_book(mid=100.0, half_spread=0.05, size=10.0)
        rt = CostModel().round_trip(100.0, book, liquidity=Liquidity.TAKER)
        solo_fee = rt.fee
        self.assertAlmostEqual(solo_fee, 0.09, places=10)
        with self.assertRaises(AssertionError):
            self.assertAlmostEqual(solo_fee, rt.total, places=6)


if __name__ == "__main__":
    unittest.main()
