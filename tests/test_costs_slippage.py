"""Cammino sul book: mezzo spread, impatto, e il rifiuto di estrapolare.

Il regime che questo modulo deve azzeccare non e' quello degli ordini grandi:
e' quello degli ordini da poche centinaia di dollari, che stanno interamente
dentro il primo livello. Li' l'impatto e' **esattamente zero** e tutto il costo
e' mezzo spread. Un modello con una costante di slippage, o con una radice del
notional, in quel regime restituisce un numero inventato — e i test qui sotto
sono scritti per non passare se qualcuno lo introducesse.
"""

from __future__ import annotations

import glob as _glob
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from costs import InsufficientDepth, InvalidBook, L2Book, Side
from tests.costs_fixture import (
    SAMPLE_BOOK_COINS,
    SAMPLE_DIR,
    book,
    simple_book,
)


def _sample_books(coin: str) -> list[L2Book]:
    """Gli snapshot reali del campione. Letti con `json`, senza DuckDB: qui
    interessa il modello, non la lettura dei parquet."""
    import duckdb

    pattern = os.path.join(SAMPLE_DIR, "l2Book", coin, "date=*", "hour=*",
                           "*.parquet")
    con = duckdb.connect()
    rows = con.execute(
        f"SELECT ts_local_ns, ts_exch_ms, raw FROM read_parquet("
        f"'{pattern}', hive_partitioning=false) ORDER BY ts_local_ns"
    ).fetchall()
    con.close()
    return [L2Book.from_payload(json.loads(raw), ts, ms) for ts, ms, raw in rows]


class TestSizeSottoIlPrimoLivello(unittest.TestCase):
    """Il caso normale per un conto da poche centinaia di dollari."""

    def setUp(self):
        # mid 100, mezzo spread 0.05 = 5 bps, primo livello da 10 unita'.
        self.book = simple_book(mid=100.0, half_spread=0.05, size=10.0)

    def test_impatto_esattamente_zero(self):
        """Non "circa zero": zero. Il confronto e' con `assertEqual`, non con
        `assertAlmostEqual`, perche' un modello che restituisse 1e-9 al posto
        di 0 starebbe stimando qualcosa che non ha misurato."""
        for size in (1e-6, 0.001, 1.0, 9.999):
            with self.subTest(size=size):
                f = self.book.walk(Side.BUY, size)
                self.assertEqual(f.impact_frac, 0.0)
                self.assertEqual(f.levels_used, 1)

    def test_tutto_il_costo_e_mezzo_spread(self):
        f = self.book.walk(Side.BUY, 1.0)
        self.assertAlmostEqual(f.half_spread_bps, 5.0, places=10)
        self.assertAlmostEqual(f.slippage_bps, 5.0, places=10)
        self.assertAlmostEqual(f.avg_px, 100.05, places=12)

    def test_lo_stesso_in_vendita(self):
        f = self.book.walk(Side.SELL, 1.0)
        self.assertEqual(f.impact_frac, 0.0)
        self.assertAlmostEqual(f.slippage_bps, 5.0, places=10)
        self.assertAlmostEqual(f.avg_px, 99.95, places=12)

    def test_lo_slippage_e_sempre_un_costo(self):
        """Segno positivo su entrambi i lati: un modello che restituisse
        slippage negativo in vendita regalerebbe denaro al backtest."""
        for side in (Side.BUY, Side.SELL):
            self.assertGreater(self.book.walk(side, 1.0).slippage_frac, 0.0)

    def test_size_minuscola_non_degenera(self):
        """Un ordine da un milionesimo di unita' paga la stessa percentuale di
        uno da una unita': il costo e' proporzionale, non forfettario."""
        a = self.book.walk(Side.BUY, 1e-6)
        b = self.book.walk(Side.BUY, 1.0)
        self.assertAlmostEqual(a.slippage_frac, b.slippage_frac, places=15)


class TestImpattoAMano(unittest.TestCase):
    """Tre livelli, numeri scelti perche' il conto si faccia a mente.

    Ask: 100.05 x 1, 100.15 x 2, 100.35 x 10. Bid: 99.95 x 1 (mid = 100).

    Comprando 3 unita' si consumano il primo livello intero e due terzi... no:
    il primo (1) e due del secondo (2), esattamente:
        spesa = 1 x 100.05 + 2 x 100.15 = 100.05 + 200.30 = 300.35
        avg   = 300.35 / 3 = 100.1166666...
        impatto = (100.1166666 - 100.05) / 100 = 0.000666666  = 6,6667 bps
        mezzo spread                            = 0.0005      = 5 bps
        slippage totale = (100.1166666 - 100) / 100 = 0.0011666 = 11,6667 bps
    """

    def setUp(self):
        self.book = book(
            bids=[(99.95, 1.0), (99.85, 2.0), (99.65, 10.0)],
            asks=[(100.05, 1.0), (100.15, 2.0), (100.35, 10.0)],
        )

    def test_prezzo_medio(self):
        f = self.book.walk(Side.BUY, 3.0)
        self.assertAlmostEqual(f.avg_px, 300.35 / 3.0, places=12)
        self.assertEqual(f.levels_used, 2)

    def test_scomposizione(self):
        f = self.book.walk(Side.BUY, 3.0)
        self.assertAlmostEqual(f.impact_bps, 6.666666666, places=6)
        self.assertAlmostEqual(f.half_spread_bps, 5.0, places=10)
        self.assertAlmostEqual(f.slippage_bps, 11.666666666, places=6)

    def test_identita_slippage_uguale_spread_piu_impatto(self):
        """Algebrica, non approssimata: (avg-mid) = (avg-best) + (best-mid)."""
        for size in (0.5, 1.0, 1.5, 3.0, 5.0, 12.9):
            for side in (Side.BUY, Side.SELL):
                with self.subTest(size=size, side=side):
                    f = self.book.walk(side, size)
                    self.assertAlmostEqual(
                        f.slippage_frac, f.half_spread_frac + f.impact_frac,
                        places=15,
                    )

    def test_impatto_cresce_con_la_size(self):
        sizes = [0.5, 1.0, 1.01, 2.0, 3.0, 5.0, 10.0, 13.0]
        impacts = [self.book.walk(Side.BUY, s).impact_bps for s in sizes]
        for prima, dopo in zip(impacts, impacts[1:]):
            self.assertGreaterEqual(dopo, prima)
        # E cresce davvero: non e' una successione di zeri.
        self.assertEqual(impacts[0], 0.0)
        self.assertGreater(impacts[-1], impacts[0])


class TestProfonditaInsufficiente(unittest.TestCase):
    """Oltre i dieci livelli registrati il modello non risponde."""

    def setUp(self):
        self.book = book(bids=[(99.95, 1.0)], asks=[(100.05, 1.0), (100.15, 2.0)])

    def test_dichiara_invece_di_estrapolare(self):
        f = self.book.walk(Side.BUY, 10.0)
        self.assertFalse(f.sufficient)
        self.assertIsNone(f.avg_px)
        self.assertAlmostEqual(f.filled_size, 3.0, places=12)   # quanto c'era
        self.assertAlmostEqual(f.depth_size, 3.0, places=12)

    def test_le_proprieta_di_prezzo_sollevano(self):
        """Il punto: un chiamante distratto riceve un'eccezione, non uno zero
        o un prezzo inventato dall'ultimo livello disponibile."""
        f = self.book.walk(Side.BUY, 10.0)
        for prop in ("slippage_frac", "impact_frac", "slippage_bps",
                     "notional_executed"):
            with self.subTest(prop=prop):
                with self.assertRaises(InsufficientDepth):
                    getattr(f, prop)

    def test_walk_or_raise(self):
        with self.assertRaises(InsufficientDepth):
            self.book.walk_or_raise(Side.BUY, 10.0)

    def test_al_limite_esatto_e_sufficiente(self):
        """Consumare tutto il book fino all'ultima unita' e' sufficiente: la
        tolleranza serve solo all'errore di rappresentazione, non a coprire un
        livello mancante."""
        f = self.book.walk(Side.BUY, 3.0)
        self.assertTrue(f.sufficient)
        self.assertAlmostEqual(f.avg_px, (100.05 + 2 * 100.15) / 3.0, places=12)


class TestBookNonValido(unittest.TestCase):
    """Un book rotto e' un dato, e va rifiutato rumorosamente."""

    def test_incrociato(self):
        with self.assertRaises(InvalidBook):
            book(bids=[(100.10, 1.0)], asks=[(100.00, 1.0)])

    def test_lato_vuoto(self):
        with self.assertRaises(InvalidBook):
            book(bids=[], asks=[(100.0, 1.0)])

    def test_livelli_non_ordinati(self):
        """Ask in ordine decrescente: il cammino produrrebbe uno slippage
        negativo, cioe' un guadagno, senza nessun errore."""
        with self.assertRaises(InvalidBook):
            book(bids=[(99.0, 1.0)], asks=[(100.2, 1.0), (100.1, 1.0)])

    def test_size_non_positiva(self):
        with self.assertRaises(InvalidBook):
            book(bids=[(99.0, 1.0)], asks=[(100.0, 0.0)])

    def test_try_from_payload_non_solleva(self):
        self.assertIsNone(L2Book.try_from_payload(
            {"coin": "X", "levels": [[{"px": "100.1", "sz": "1"}],
                                     [{"px": "100.0", "sz": "1"}]]}, 0))

    def test_ordine_richiesto_zero(self):
        with self.assertRaises(ValueError):
            simple_book().walk(Side.BUY, 0.0)


class TestSuiBookReali(unittest.TestCase):
    """Gli stessi conti sugli snapshot registrati dal collector.

    Il campione (`tests/fixtures/costs_sample/`) e' una fetta fissa dei dati
    mainnet: 40 snapshot per coin, distribuiti su dieci giorni. Serve a
    verificare che il modello regga la forma reale del book — dieci livelli,
    prezzi con tick diversi per coin, size in unita' di base — e non solo i
    numeri tondi dei test sopra.
    """

    @classmethod
    def setUpClass(cls):
        cls.books = {c: _sample_books(c) for c in SAMPLE_BOOK_COINS}

    def test_il_campione_c_e(self):
        for coin, bs in self.books.items():
            with self.subTest(coin=coin):
                self.assertEqual(len(bs), 40)
                self.assertTrue(all(len(b.asks) == 10 and len(b.bids) == 10
                                    for b in bs))

    def test_cento_dollari_non_hanno_impatto(self):
        """Su tutte e quattro le coin, su tutti gli snapshot: un ordine da
        100 $ resta dentro il primo livello. Se un giorno questo test
        fallisse, sarebbe una notizia sul mercato, non sul codice."""
        for coin, bs in self.books.items():
            for b in bs:
                with self.subTest(coin=coin, ts=b.ts_local_ns):
                    for side in (Side.BUY, Side.SELL):
                        f = b.walk(side, b.size_for_notional(100.0))
                        self.assertEqual(f.impact_frac, 0.0)
                        self.assertAlmostEqual(f.slippage_frac,
                                               b.half_spread_frac, places=15)

    def test_slippage_non_decresce_con_la_size(self):
        """Su ogni snapshot reale: 100, 500, 2.000, 20.000, 200.000 $.
        Monotonia non stretta — sulle coin liquide anche 200.000 $ possono
        stare dentro il primo livello, e in quel caso il costo NON deve
        salire."""
        notionals = [100.0, 500.0, 2000.0, 20_000.0, 200_000.0]
        for coin, bs in self.books.items():
            for b in bs:
                prev = -1.0
                for n in notionals:
                    f = b.walk(Side.BUY, b.size_for_notional(n))
                    if not f.sufficient:
                        break
                    with self.subTest(coin=coin, notional=n):
                        # Tolleranza relativa e non confronto esatto: quando
                        # l'ordine attraversa piu' livelli, il prezzo medio e'
                        # una media pesata e due size vicine possono differire
                        # nell'ultimo bit. Un test che pretendesse la monotonia
                        # esatta in virgola mobile fallirebbe per un fatto di
                        # rappresentazione, non di modello.
                        self.assertGreaterEqual(f.slippage_frac,
                                                prev - 1e-12 * abs(prev))
                    prev = f.slippage_frac

    def test_size_enorme_dichiarata_insufficiente(self):
        """Cento milioni di dollari eccedono dieci livelli su qualunque coin di
        questo mercato: il modello deve dirlo, non produrre un prezzo."""
        for coin, bs in self.books.items():
            with self.subTest(coin=coin):
                b = bs[0]
                f = b.walk(Side.BUY, b.size_for_notional(1e8))
                self.assertFalse(f.sufficient)
                with self.assertRaises(InsufficientDepth):
                    f.slippage_bps

    def test_identita_sui_dati_reali(self):
        for coin, bs in self.books.items():
            for b in bs:
                size = b.size_for_notional(50_000.0)
                for side in (Side.BUY, Side.SELL):
                    f = b.walk(side, size)
                    if not f.sufficient:
                        continue
                    with self.subTest(coin=coin, side=side):
                        self.assertAlmostEqual(
                            f.slippage_frac,
                            f.half_spread_frac + f.impact_frac, places=14)


class TestLaVerificaSaFallire(unittest.TestCase):
    """Le tre scorciatoie che questo modulo esiste per impedire."""

    def test_una_costante_di_slippage_non_passerebbe(self):
        """Un modello "0,5 bps costanti" sbaglierebbe di un fattore dieci sul
        book reale piu' stretto, e non crescerebbe con la size."""
        b = book(bids=[(99.95, 1.0), (99.85, 2.0)],
                 asks=[(100.05, 1.0), (100.15, 2.0)])
        costante_bps = 0.5
        vero_piccolo = b.walk(Side.BUY, 0.5).slippage_bps
        vero_grande = b.walk(Side.BUY, 3.0).slippage_bps
        with self.assertRaises(AssertionError):
            self.assertAlmostEqual(costante_bps, vero_piccolo, places=1)
        with self.assertRaises(AssertionError):
            self.assertAlmostEqual(vero_piccolo, vero_grande, places=1)

    def test_estrapolare_oltre_il_book_darebbe_un_numero_plausibile(self):
        """Se al posto dell'insufficienza si prendesse l'ultimo livello
        disponibile, si otterrebbe un prezzo credibile e sbagliato: il test
        dimostra che quel numero esiste ed e' vicino al vero, cioe' che il
        rifiuto non e' pedanteria."""
        b = book(bids=[(99.95, 1.0)], asks=[(100.05, 1.0), (100.15, 1.0)])
        f = b.walk(Side.BUY, 5.0)
        self.assertFalse(f.sufficient)
        finto = (1 * 100.05 + 1 * 100.15 + 3 * 100.15) / 5.0   # estrapolando
        self.assertAlmostEqual(finto, 100.13, places=2)        # plausibile...
        with self.assertRaises(InsufficientDepth):             # ...e rifiutato
            f.require_avg_px()

    def test_leggere_i_lati_al_contrario_darebbe_uno_sconto(self):
        """Se `levels()` scambiasse bid e ask, comprare al bid produrrebbe uno
        slippage negativo. Qui si mostra il numero che ne uscirebbe."""
        b = simple_book(mid=100.0, half_spread=0.05, size=10.0)
        giusto = b.walk(Side.BUY, 1.0).avg_px
        scambiato = b.bids[0].px
        self.assertGreater(giusto, scambiato)
        with self.assertRaises(AssertionError):
            self.assertAlmostEqual(giusto, scambiato, places=6)


if __name__ == "__main__":
    unittest.main()
