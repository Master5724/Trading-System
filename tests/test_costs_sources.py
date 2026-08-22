"""Come `costs/` legge i dati: dedup dei trade, buchi derivati, sola lettura.

Tre vincoli del prompt di Task 2 diventano asserzioni qui:

1. i trade si leggono DEDUPLICATI per `tid`, e con la funzione del catalogo —
   non con una seconda implementazione;
2. le finestre inaffidabili si escludono con i buchi DERIVATI DAI DATI, non con
   il registro `_gaps.jsonl` (che per il periodo precedente al 14 agosto 2026
   sottostima le durate);
3. la directory dati non viene mai scritta.

I primi due sono verificati su una data_dir costruita a mano, con un buco
piazzato dove il test sa che sta e duplicati costruiti apposta: sui dati veri
il numero atteso non sarebbe noto in anticipo, e un test che ricalcola il
proprio valore atteso non verifica niente.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catalog import trades as catalog_trades
from costs import sources
from costs.funding import NS_PER_HOUR
from tests.catalog_fixture import BASE_HOUR_IDX, trade_payload, write_partition
from tests.costs_fixture import SAMPLE_BOOK_COINS, SAMPLE_DIR

H0 = BASE_HOUR_IDX          # indice della prima ora usata dai fixture


def ctx_payload(coin: str, funding: float, mark: float = 100.0) -> str:
    return json.dumps({"coin": coin,
                       "ctx": {"funding": f"{funding:.10f}",
                               "markPx": str(mark), "oraclePx": str(mark)}})


def book_payload(coin: str, mid: float = 100.0) -> str:
    return json.dumps({
        "coin": coin,
        "levels": [
            [{"px": f"{mid - 0.05:.4f}", "sz": "10", "n": 1}],
            [{"px": f"{mid + 0.05:.4f}", "sz": "10", "n": 1}],
        ],
    })


def _snapshot(path: str) -> dict[str, str]:
    """Impronta di ogni file sotto `path`: serve a dimostrare che leggere non
    scrive."""
    out = {}
    for root, _, files in os.walk(path):
        for f in sorted(files):
            p = os.path.join(root, f)
            with open(p, "rb") as fh:
                out[p] = hashlib.sha256(fh.read()).hexdigest()
    return out


class TestBuchiDerivatiDaiDati(unittest.TestCase):
    """Un buco costruito apposta, e le ore che ne conseguono.

    Tre ore di `activeAssetCtx` con un campione al minuto. Nella seconda ora i
    campioni fra il minuto 10 e il minuto 50 non ci sono: quaranta minuti di
    silenzio. La soglia derivata per questa partizione e' `max(5 x p99, 30 s)`
    = 300 s (il p99 degli intervalli e' 60 s), quindi il buco viene rilevato e
    l'ora 1 finisce fra quelle inaffidabili.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = os.path.join(self.tmp.name, "data")
        rows = []
        for h in range(3):
            for m in range(60):
                if h == 1 and 10 <= m < 50:
                    continue                       # <- il buco
                ts = (H0 + h) * NS_PER_HOUR + m * 60 * 1_000_000_000
                rows.append((ts, ts // 1_000_000,
                             ctx_payload("TEST", 0.0001 * (h + 1))))
        write_partition(self.data_dir, "activeAssetCtx", "TEST", rows)
        self.con = sources.connect(os.path.join(self.tmp.name, "duck"),
                                   memory_limit="512MB")

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def test_l_ora_col_buco_e_marcata(self):
        u = sources.unreliable_hours(self.con, self.data_dir,
                                     [("activeAssetCtx", "TEST")],
                                     soglie=sources.SOGLIE_MISURATE)
        ore = u[("activeAssetCtx", "TEST")]
        self.assertIn(H0 + 1, ore)
        self.assertNotIn(H0, ore)
        self.assertNotIn(H0 + 2, ore)

    def test_il_funding_esclude_l_ora_marcata(self):
        """L'ora esclusa non vale zero e non vale il rate osservato: vale
        "non lo so", e il costo la conta separatamente."""
        u = sources.unreliable_hours(self.con, self.data_dir,
                                     [("activeAssetCtx", "TEST")],
                                     soglie=sources.SOGLIE_MISURATE)
        serie = sources.funding_series(self.con, self.data_dir, "TEST",
                                       unreliable=u[("activeAssetCtx", "TEST")])
        # I campioni delle ore 0,1,2 regolano alle ore 1,2,3.
        from costs import LONG
        c = serie.cost(LONG, 1000.0, (H0 + 1) * NS_PER_HOUR,
                       (H0 + 4) * NS_PER_HOUR)
        self.assertEqual(c.n_settlements, 3)
        self.assertEqual(c.n_unreliable, 1)      # il regolamento H0+2
        self.assertFalse(c.complete)

    def test_senza_buco_nessuna_ora_marcata(self):
        """Controprova: la stessa serie senza interruzione non produce
        esclusioni. Una soglia che marcasse anche i dati sani renderebbe
        inutilizzabile qualunque finestra."""
        data_dir = os.path.join(self.tmp.name, "sano")
        rows = [((H0 + h) * NS_PER_HOUR + m * 60 * 1_000_000_000,
                 0, ctx_payload("TEST", 0.0001))
                for h in range(3) for m in range(60)]
        write_partition(data_dir, "activeAssetCtx", "TEST", rows)
        u = sources.unreliable_hours(self.con, data_dir,
                                     [("activeAssetCtx", "TEST")],
                                     soglie=sources.SOGLIE_MISURATE)
        self.assertEqual(u[("activeAssetCtx", "TEST")], frozenset())

    def test_gli_snapshot_del_book_in_ore_marcate_non_vengono_usati(self):
        data_dir = os.path.join(self.tmp.name, "book")
        rows = []
        for h in range(3):
            for m in range(0, 60, 5):
                ts = (H0 + h) * NS_PER_HOUR + m * 60 * 1_000_000_000
                rows.append((ts, ts // 1_000_000, book_payload("TEST")))
        write_partition(data_dir, "l2Book", "TEST", rows)
        tutti = list(sources.sample_books(self.con, data_dir, "TEST",
                                          every_s=60))
        senza = list(sources.sample_books(self.con, data_dir, "TEST",
                                          every_s=60, unreliable={H0 + 1}))
        self.assertEqual(len(tutti), 36)
        self.assertEqual(len(senza), 24)
        self.assertTrue(all(b.ts_local_ns // NS_PER_HOUR != H0 + 1
                            for b in senza))


class TestDedupDeiTrade(unittest.TestCase):
    """Le ritrasmissioni dopo una riconnessione non devono contare due volte."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = os.path.join(self.tmp.name, "data")
        base = H0 * NS_PER_HOUR
        t = [{"coin": "TEST", "side": "B", "px": "100.0", "sz": "1.0",
              "time": 1, "hash": "0xaa", "tid": 1},
             {"coin": "TEST", "side": "A", "px": "100.0", "sz": "3.0",
              "time": 2, "hash": "0xbb", "tid": 2}]
        rows = [
            (base, base // 10**6, trade_payload(t)),
            # Riconnessione: il server rimanda gli stessi due trade, piu' uno nuovo.
            (base + 10**9, (base + 10**9) // 10**6, trade_payload(
                t + [{"coin": "TEST", "side": "B", "px": "100.0", "sz": "6.0",
                      "time": 3, "hash": "0xcc", "tid": 3}])),
        ]
        write_partition(self.data_dir, "trades", "TEST", rows)
        self.con = sources.connect(os.path.join(self.tmp.name, "duck"),
                                   memory_limit="512MB")

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def test_conta_i_tid_distinti(self):
        s = sources.trade_notional_stats(self.con, self.data_dir, "TEST")
        self.assertEqual(s["n_trades"], 3)          # non 5
        self.assertEqual(s["n_buy"], 2)
        self.assertEqual(s["n_sell"], 1)
        # Notional: 100, 300, 600 -> mediana 300, somma 1000.
        self.assertAlmostEqual(s["notional_p50"], 300.0, places=9)
        self.assertAlmostEqual(s["notional_sum"], 1000.0, places=9)

    def test_usa_la_funzione_del_catalogo(self):
        """Non una seconda implementazione: lo stesso SQL, quindi lo stesso
        numero. Se un giorno divergessero, il backtester e il catalogo
        conterebbero due volumi diversi sugli stessi file."""
        n_catalogo = self.con.execute(
            f"SELECT count(*) FROM {catalog_trades.dedup_sql(self.data_dir, 'TEST')}"
        ).fetchone()[0]
        n_costs = sources.trade_notional_stats(self.con, self.data_dir,
                                               "TEST")["n_trades"]
        self.assertEqual(n_costs, n_catalogo)


class TestSulCampioneReale(unittest.TestCase):
    """Gli stessi percorsi sul campione registrato."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.con = sources.connect(os.path.join(cls.tmp.name, "duck"),
                                  memory_limit="512MB")

    @classmethod
    def tearDownClass(cls):
        cls.con.close()
        cls.tmp.cleanup()

    def test_scopre_le_coin_dal_disco(self):
        self.assertEqual(sorted(sources.discover_coins(SAMPLE_DIR)),
                         sorted(SAMPLE_BOOK_COINS))

    def test_legge_gli_snapshot(self):
        books = list(sources.sample_books(self.con, SAMPLE_DIR, "BTC",
                                          every_s=1))
        self.assertEqual(len(books), 40)
        self.assertTrue(all(b.coin == "BTC" for b in books))
        self.assertTrue(all(b.spread > 0 for b in books))

    def test_il_campionamento_riduce(self):
        """A un'ora di distanza fra i bucket restano meno snapshot: il campione
        ne contiene quattro al giorno, distanziati sei ore."""
        uno_al_secondo = list(sources.sample_books(self.con, SAMPLE_DIR, "BTC",
                                                   every_s=1))
        uno_al_giorno = list(sources.sample_books(self.con, SAMPLE_DIR, "BTC",
                                                  every_s=86400))
        self.assertEqual(len(uno_al_secondo), 40)
        self.assertEqual(len(uno_al_giorno), 10)

    def test_statistiche_dei_trade(self):
        s = sources.trade_notional_stats(self.con, SAMPLE_DIR, "BTC")
        self.assertGreater(s["n_trades"], 0)
        self.assertEqual(s["n_trades"], s["n_buy"] + s["n_sell"])
        self.assertGreater(s["notional_p50"], 0.0)

    def test_leggere_non_scrive(self):
        """CLAUDE.md: sola lettura sui dati raccolti. Qui e' verificato per
        impronta, file per file — il collector sta scrivendo nella directory
        vera mentre questo modulo la legge."""
        prima = _snapshot(SAMPLE_DIR)
        list(sources.sample_books(self.con, SAMPLE_DIR, "BTC", every_s=60))
        sources.funding_series(self.con, SAMPLE_DIR, "BTC")
        sources.rest_funding_series(self.con, SAMPLE_DIR, "BTC")
        sources.trade_notional_stats(self.con, SAMPLE_DIR, "BTC")
        self.assertEqual(prima, _snapshot(SAMPLE_DIR))


class TestLaVerificaSaFallire(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.con = sources.connect(os.path.join(self.tmp.name, "duck"),
                                   memory_limit="512MB")

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def test_senza_dedup_il_volume_e_gonfiato(self):
        """Lo stesso fixture letto dal grezzo da 5 trade invece di 3: il 66% in
        piu' di volume, e due esecuzioni datate al momento della
        ritrasmissione invece che a quello del fatto."""
        data_dir = os.path.join(self.tmp.name, "data")
        base = H0 * NS_PER_HOUR
        t = [{"coin": "TEST", "side": "B", "px": "100.0", "sz": "1.0",
              "time": 1, "hash": "0xaa", "tid": 1},
             {"coin": "TEST", "side": "A", "px": "100.0", "sz": "3.0",
              "time": 2, "hash": "0xbb", "tid": 2}]
        write_partition(data_dir, "trades", "TEST", [
            (base, 0, trade_payload(t)),
            (base + 10**9, 0, trade_payload(t + [
                {"coin": "TEST", "side": "B", "px": "100.0", "sz": "6.0",
                 "time": 3, "hash": "0xcc", "tid": 3}])),
        ])
        grezzo = self.con.execute(
            f"SELECT count(*) FROM {catalog_trades.exploded_sql(data_dir, 'TEST')}"
        ).fetchone()[0]
        dedup = sources.trade_notional_stats(self.con, data_dir, "TEST")["n_trades"]
        self.assertEqual(grezzo, 5)
        self.assertEqual(dedup, 3)
        with self.assertRaises(AssertionError):
            self.assertEqual(grezzo, dedup)

    def test_senza_esclusione_l_ora_bucata_entrerebbe_nel_conto(self):
        """Se le ore inaffidabili non fossero escluse, il funding risulterebbe
        completo e il numero sarebbe indistinguibile da uno calcolato su dati
        sani.

        Tre ore di campioni (H0, H0+1, H0+2) danno tre regolamenti (H0+1, H0+2,
        H0+3), di cui l'ultimo e' provvisorio perche' deriva dall'ora piu'
        recente. La finestra si ferma quindi a H0+3 escluso: il provvisorio
        renderebbe `complete` falso da solo, e maschererebbe proprio la cosa
        che questo test deve mostrare — che senza l'esclusione delle ore
        bucate il risultato sembra sano.
        """
        from costs import LONG
        data_dir = os.path.join(self.tmp.name, "d2")
        rows = []
        for h in range(3):
            for m in range(60):
                if h == 1 and 10 <= m < 50:
                    continue
                ts = (H0 + h) * NS_PER_HOUR + m * 60 * 1_000_000_000
                rows.append((ts, 0, ctx_payload("TEST", 0.0001)))
        write_partition(data_dir, "activeAssetCtx", "TEST", rows)
        u = sources.unreliable_hours(self.con, data_dir,
                                     [("activeAssetCtx", "TEST")],
                                     soglie=sources.SOGLIE_MISURATE)
        # H0+1 e H0+2: due regolamenti definitivi, il secondo dei quali deriva
        # dall'ora bucata H0+1.
        start, end = (H0 + 1) * NS_PER_HOUR, (H0 + 3) * NS_PER_HOUR
        con_esclusione = sources.funding_series(
            self.con, data_dir, "TEST",
            unreliable=u[("activeAssetCtx", "TEST")]).cost(LONG, 1000.0, start, end)
        senza = sources.funding_series(self.con, data_dir, "TEST").cost(
            LONG, 1000.0, start, end)
        self.assertEqual(con_esclusione.n_provisional, 0)
        self.assertEqual(senza.n_provisional, 0)
        self.assertEqual(con_esclusione.n_unreliable, 1)
        self.assertFalse(con_esclusione.complete)
        self.assertTrue(senza.complete)          # <- il numero pulito e falso
        with self.assertRaises(AssertionError):
            self.assertEqual(con_esclusione.n_known, senza.n_known)


if __name__ == "__main__":
    unittest.main()
