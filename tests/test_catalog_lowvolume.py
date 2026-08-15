"""Test della soglia `low_volume`: si applica solo ai canali a cadenza fissa.

Il difetto che questi test coprono: marcare come sospetta un'ora di `trades`
perche' ha il 20% di righe in meno della mediana. Su un canale guidato
dall'attivita' del mercato quella differenza e' il mercato, non la raccolta, e
il flag produce centinaia di ore marcate che seppelliscono le poche ore
davvero rotte.

`TestLaVerificaSaFallire` in fondo rimette la soglia su tutti i canali e
pretende che le asserzioni esplodano.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catalog import dataset, gapwindows, metrics
from tests.catalog_fixture import hourly_rows, write_partition

# 8 ore piene e una a meta'. L'ora bassa e' la 3: non e' un bordo, quindi non
# viene esclusa dal calcolo della mediana ne' scusata come ora parziale.
ORE_PIENE = {h: 100 for h in range(8)}
ORE_CON_BUCO = {**ORE_PIENE, 3: 50}


class CatalogoTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = os.path.join(self.tmp.name, "dati")
        # Stesso identico profilo orario sui due canali: se il risultato
        # differisce, e' per il canale e non per i dati.
        write_partition(self.data_dir, "l2Book", "BTC", hourly_rows(ORE_CON_BUCO))
        write_partition(self.data_dir, "trades", "BTC", hourly_rows(ORE_CON_BUCO))
        self.addCleanup(self.tmp.cleanup)

    def build(self, fixed_rate=None):
        con = dataset.connect(os.path.join(self.tmp.name, "tmpdb"))
        self.addCleanup(con.close)
        partitions = dataset.discover(self.data_dir)
        gapwindows.materialize(con, [])
        metrics.build_hourly(con, self.data_dir, partitions)
        metrics.apply_gap_overlap(con)
        metrics.apply_low_volume(con, fixed_rate)
        return con

    def ora(self, con, channel: str, h: int) -> dict:
        cols = ["n_rows", "median_rows", "rows_ratio", "low_volume",
                "below_median", "is_fixed_rate"]
        row = con.execute(
            f"SELECT {', '.join(cols)} FROM hourly "
            f"WHERE channel = ? AND hour_idx = ?",
            [channel, _hour_idx(h)],
        ).fetchone()
        self.assertIsNotNone(row, f"ora {h} assente per {channel}")
        return dict(zip(cols, row))


def _hour_idx(h: int) -> int:
    from tests.catalog_fixture import BASE_HOUR_IDX

    return BASE_HOUR_IDX + h


class TestSogliaSoloSuCadenzaFissa(CatalogoTestCase):
    def test_l2book_viene_marcato(self):
        con = self.build()
        ora = self.ora(con, "l2Book", 3)
        self.assertEqual(ora["n_rows"], 50)
        self.assertEqual(ora["median_rows"], 100)
        self.assertTrue(ora["is_fixed_rate"])
        self.assertTrue(ora["low_volume"])

    def test_trades_non_viene_marcato(self):
        con = self.build()
        ora = self.ora(con, "trades", 3)
        self.assertFalse(ora["is_fixed_rate"])
        self.assertFalse(ora["low_volume"])

    def test_la_statistica_oraria_resta_pubblicata(self):
        """Non marcare non vuol dire non misurare: rapporto e mediana devono
        esserci comunque, se no il canale diventa cieco invece che silenzioso."""
        con = self.build()
        ora = self.ora(con, "trades", 3)
        self.assertEqual(ora["n_rows"], 50)
        self.assertEqual(ora["median_rows"], 100)
        self.assertAlmostEqual(ora["rows_ratio"], 0.5)
        self.assertTrue(ora["below_median"])

    def test_conteggi_del_report(self):
        con = self.build()
        fc = metrics.flagged_counts(con)
        # Una sola ora marcata in tutto: la 3 di l2Book.
        self.assertEqual(fc["low_volume"], 1)
        # E una sotto la mediana ma non valutata: la 3 di trades.
        self.assertEqual(fc["sotto_mediana_non_valutate"], 1)
        self.assertEqual(fc["ore_cadenza_fissa"], 8)

    def test_le_ore_marcate_elencate_escludono_trades(self):
        con = self.build()
        marcate = metrics.flagged_hours(con, 100)
        self.assertEqual([(r["channel"], r["n_rows"]) for r in marcate],
                         [("l2Book", 50)])

    def test_copertura_riporta_entrambe_le_colonne(self):
        con = self.build()
        cov = {r["channel"]: r for r in metrics.coverage(con)}
        self.assertEqual(cov["l2Book"]["n_low_volume_hours"], 1)
        self.assertEqual(cov["trades"]["n_low_volume_hours"], 0)
        self.assertEqual(cov["trades"]["n_hours_below_median"], 1)
        self.assertTrue(cov["l2Book"]["is_fixed_rate"])
        self.assertFalse(cov["trades"]["is_fixed_rate"])


class TestElencoConfigurabile(CatalogoTestCase):
    """Quali canali abbiano cadenza fissa dipende da cosa il collector ha
    sottoscritto, non da una costante nel codice: l'elenco si sovrascrive."""

    def test_elenco_invertito(self):
        con = self.build(fixed_rate=frozenset({"trades"}))
        self.assertTrue(self.ora(con, "trades", 3)["low_volume"])
        self.assertFalse(self.ora(con, "l2Book", 3)["low_volume"])

    def test_elenco_vuoto_non_marca_niente_e_non_esplode(self):
        con = self.build(fixed_rate=frozenset())
        self.assertFalse(self.ora(con, "trades", 3)["low_volume"])
        self.assertFalse(self.ora(con, "l2Book", 3)["low_volume"])
        self.assertEqual(metrics.flagged_counts(con)["low_volume"], 0)

    def test_default_dal_modulo_dataset(self):
        """Il default non e' cablato in `metrics`: viene da un unico posto."""
        self.assertIn("l2Book", dataset.DEFAULT_FIXED_RATE_CHANNELS)
        self.assertIn("allMids", dataset.DEFAULT_FIXED_RATE_CHANNELS)
        self.assertIn("activeAssetCtx", dataset.DEFAULT_FIXED_RATE_CHANNELS)
        self.assertNotIn("trades", dataset.DEFAULT_FIXED_RATE_CHANNELS)
        self.assertNotIn("candle", dataset.DEFAULT_FIXED_RATE_CHANNELS)

    def test_la_cli_espone_l_elenco(self):
        from catalog.__main__ import parse_args

        args = parse_args(["--data-dir", "/x", "--fixed-rate-channels", "a,b"])
        self.assertEqual(args.fixed_rate_channels, "a,b")
        default = parse_args(["--data-dir", "/x"]).fixed_rate_channels
        self.assertEqual(sorted(default.split(",")),
                         sorted(dataset.DEFAULT_FIXED_RATE_CHANNELS))


class TestLaVerificaSaFallire(CatalogoTestCase):
    """Con la soglia rimessa su tutti i canali — cioe' col difetto dentro — le
    asserzioni sopra devono esplodere. Se passassero lo stesso, non starebbero
    verificando la correzione."""

    def test_trades_marcato_col_difetto(self):
        con = self.build(fixed_rate=frozenset({"l2Book", "trades"}))
        with self.assertRaises(AssertionError):
            self.assertFalse(self.ora(con, "trades", 3)["low_volume"])

    def test_conteggi_col_difetto(self):
        con = self.build(fixed_rate=frozenset({"l2Book", "trades"}))
        fc = metrics.flagged_counts(con)
        with self.assertRaises(AssertionError):
            self.assertEqual(fc["low_volume"], 1)
        self.assertEqual(fc["low_volume"], 2)
        self.assertEqual(fc["sotto_mediana_non_valutate"], 0)


if __name__ == "__main__":
    unittest.main()
