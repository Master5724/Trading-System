"""Test della distribuzione degli intervalli fra messaggi e della verifica
di classificazione dei canali.

Due difetti da coprire, e per ognuno la prova che il test sa fallire:

1. **Gli intervalli che sono un buco inquinano la distribuzione.**
   `TestBuchiEsclusi` misura una serie regolare interrotta da un'interruzione
   di 600 s; `TestLaVerificaSaFallire.test_senza_esclusione_*` rifa' lo stesso
   conto con l'esclusione disattivata (soglia irraggiungibile) e pretende che
   le asserzioni esplodano.

2. **La classificazione "a cadenza fissa" e' dichiarata, non misurata.**
   `TestVerdetto` costruisce un canale dichiarato fisso che si comporta a
   raffiche e pretende `smentito`.

I buchi che alimentano l'esclusione si derivano dai dati (`derivedgaps`), non
dal registro: la riconciliazione fra le due fonti e i suoi test stanno in
`test_catalog_derived_gaps.py`.

Tutto su directory temporanee: i dati di produzione sono scritti da un
collector vivo, cambiano sotto i piedi e sono in sola lettura.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catalog import dataset, derivedgaps, gapwindows, intervals, metrics, sanity
from tests.catalog_fixture import BASE_HOUR_IDX, NS_PER_HOUR, write_partition

NS = 1_000_000_000
BASE_NS = BASE_HOUR_IDX * NS_PER_HOUR
BASE_MS = BASE_NS // 1_000_000


def regolari(n: int, step_ns: int = NS, start_ns: int = BASE_NS) -> list[tuple]:
    """Serie perfettamente regolare: un messaggio ogni `step_ns`."""
    return [(start_ns + i * step_ns, 0, "{}") for i in range(n)]


def raffiche(cicli: int, per_raffica: int = 20, dentro_ns: int = 1_000_000,
             pausa_ns: int = 60 * NS, start_ns: int = BASE_NS) -> list[tuple]:
    """Serie a raffiche: `per_raffica` messaggi ravvicinati, poi una pausa."""
    rows: list[tuple] = []
    t = start_ns
    for _ in range(cicli):
        for _ in range(per_raffica):
            rows.append((t, 0, "{}"))
            t += dentro_ns
        t += pausa_ns - dentro_ns
    return rows


def window(start_ms: int, end_ms: int, reason: str = "test") -> gapwindows.Window:
    return gapwindows.Window(start_ms=start_ms, end_ms=end_ms, reason=reason,
                             channels=(), still_open=False)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = os.path.join(self.tmp.name, "dati")
        self.addCleanup(self.tmp.cleanup)

    def build(self, fixed_rate=None, p99_multiple=None, min_gap_s=None):
        """Costruisce `ts_ordered`, le soglie di buco e `intervals`.

        Le soglie vanno costruite prima: sono la sorgente dell'esclusione.
        `p99_multiple`/`min_gap_s` servono ai test che disattivano la logica."""
        con = dataset.connect(os.path.join(self.tmp.name, "tmpdb"))
        self.addCleanup(con.close)
        partitions = dataset.discover(self.data_dir)
        sanity.build_ordered(con, self.data_dir, partitions)
        derivedgaps.build_thresholds(
            con,
            derivedgaps.DEFAULT_P99_MULTIPLE if p99_multiple is None else p99_multiple,
            derivedgaps.DEFAULT_MIN_GAP_S if min_gap_s is None else min_gap_s,
        )
        derivedgaps.build(con)
        intervals.build(con)
        self.rows = {(r["channel"], r["coin"]): r for r in intervals.stats(con)}
        if fixed_rate is not None:
            self.classi = {
                (c["channel"], c["coin"]): c
                for c in intervals.classify(intervals.stats(con), fixed_rate)
            }
        return con

    def riga(self, channel: str, coin: str = "BTC") -> dict:
        return self.rows[(channel, coin)]


# ---------------------------------------------------------------------------
# 1. La forma della distribuzione
# ---------------------------------------------------------------------------


class TestSerieRegolare(Base):
    """Un metronomo deve dare indice 1, non "circa 1": gli intervalli sono
    tutti identici per costruzione, quindi mediana e p99 coincidono."""

    def setUp(self):
        super().setUp()
        write_partition(self.data_dir, "allMids", "_global", regolari(500))

    def test_indice_esattamente_uno(self):
        self.build()
        r = self.riga("allMids", "_global")
        self.assertEqual(r["n"], 499)
        self.assertAlmostEqual(r["median_s"], 1.0)
        self.assertAlmostEqual(r["p90_s"], 1.0)
        self.assertAlmostEqual(r["p99_s"], 1.0)
        self.assertAlmostEqual(r["max_s"], 1.0)
        self.assertAlmostEqual(r["mean_s"], 1.0)
        self.assertAlmostEqual(r["ratio_p99_p50"], 1.0)

    def test_niente_di_escluso(self):
        self.build()
        r = self.riga("allMids", "_global")
        self.assertEqual(r["n_pairs"], 499)
        self.assertEqual(r["n_excluded_gap"], 0)
        self.assertEqual(r["n_excluded_negative"], 0)


class TestSerieARaffiche(Base):
    """20 messaggi a 1 ms e poi 60 s di silenzio: stesso numero di righe per
    ora di un metronomo, distribuzione completamente diversa. E' il caso che il
    conteggio orario da solo non sa distinguere."""

    def setUp(self):
        super().setUp()
        write_partition(self.data_dir, "trades", "BTC", raffiche(30))

    def test_indice_alto(self):
        self.build()
        r = self.riga("trades")
        self.assertEqual(r["n"], 30 * 20 - 1)
        self.assertAlmostEqual(r["median_s"], 0.001, places=6)
        self.assertAlmostEqual(r["p99_s"], 60.0, places=3)
        self.assertGreater(r["ratio_p99_p50"], 1000.0)
        self.assertGreater(r["ratio_p99_p50"], intervals.RATIO_FIXED_MAX)

    def test_la_media_da_sola_non_direbbe_niente(self):
        """Media ~3 s con mediana 1 ms: la media e' compatibile con un canale
        tranquillo, ed e' il motivo per cui l'indice usa i percentili."""
        self.build()
        r = self.riga("trades")
        self.assertGreater(r["mean_s"], 2.0)
        self.assertLess(r["median_s"], 0.01)


# ---------------------------------------------------------------------------
# 2. L'esclusione dei buchi
# ---------------------------------------------------------------------------

# 400 messaggi a 1/s, un'interruzione di 600 s, altri 400 messaggi a 1/s.
PRIMA = 400
DOPO = 400
BUCO_S = 600


def serie_con_buco() -> list[tuple]:
    rows = regolari(PRIMA)
    ripresa = BASE_NS + (PRIMA - 1) * NS + BUCO_S * NS
    rows += regolari(DOPO, start_ns=ripresa)
    return rows


class TestBuchiEsclusi(Base):
    """L'esclusione non ha bisogno di nessun registro: il buco da 600 s supera
    la soglia della partizione (max(5 x p99, 30 s) = 30 s) e per questo esce."""

    def setUp(self):
        super().setUp()
        write_partition(self.data_dir, "l2Book", "BTC", serie_con_buco())

    def test_l_intervallo_del_buco_non_entra(self):
        self.build()
        r = self.riga("l2Book")
        self.assertEqual(r["n_pairs"], PRIMA + DOPO - 1)
        self.assertEqual(r["n_excluded_gap"], 1)
        self.assertEqual(r["n"], PRIMA + DOPO - 2)
        # Il massimo torna a essere la cadenza normale: il buco non c'e' piu'.
        self.assertAlmostEqual(r["max_s"], 1.0)
        self.assertAlmostEqual(r["median_s"], 1.0)
        self.assertAlmostEqual(r["ratio_p99_p50"], 1.0)

    def test_gli_intervalli_adiacenti_restano(self):
        """Escludere il buco non deve diventare escludere il vicinato: esce un
        intervallo solo, e gli altri 798 restano tutti."""
        con = self.build()
        n_uno_secondo = con.execute(
            "SELECT n FROM intervals WHERE channel = 'l2Book'"
        ).fetchone()[0]
        self.assertEqual(n_uno_secondo, PRIMA + DOPO - 2)

    def test_la_soglia_usata_e_pubblicata(self):
        """Con max_s limitato dalla soglia per costruzione, chi legge la tabella
        deve poter vedere quale soglia era in vigore."""
        self.build()
        self.assertAlmostEqual(self.riga("l2Book")["gap_threshold_s"],
                               derivedgaps.DEFAULT_MIN_GAP_S)

    def test_soglia_bassissima_esclude_quasi_tutto(self):
        """Il caso opposto: con una soglia sotto la cadenza normale ogni
        intervallo diventa un buco. Serve a provare che l'esclusione segue
        davvero la soglia e non un valore cablato."""
        self.build(min_gap_s=0.5, p99_multiple=0.1)
        r = self.riga("l2Book")
        self.assertEqual(r["n"], 0)
        self.assertEqual(r["n_excluded_gap"], PRIMA + DOPO - 1)
        self.assertIsNone(r["median_s"])
        self.assertIsNone(r["ratio_p99_p50"])


# ---------------------------------------------------------------------------
# 3. Il verdetto sulla classificazione
# ---------------------------------------------------------------------------


class TestVerdetto(Base):
    def setUp(self):
        super().setUp()
        # allMids: dichiarato a cadenza fissa E regolare -> coerente.
        write_partition(self.data_dir, "allMids", "_global", regolari(500))
        # l2Book: dichiarato a cadenza fissa ma a raffiche -> smentito.
        write_partition(self.data_dir, "l2Book", "BTC", raffiche(30))
        # trades: non dichiarato e a raffiche -> coerente.
        write_partition(self.data_dir, "trades", "BTC", raffiche(30))
        # candle: non dichiarato ma regolare -> segnalato.
        write_partition(self.data_dir, "candle", "BTC", regolari(500))
        # backfill_candle: canale REST, non valutabile.
        write_partition(self.data_dir, "backfill_candle", "BTC", raffiche(30))
        self.build(fixed_rate=frozenset({"allMids", "l2Book"}))

    def verdetto(self, channel: str, coin: str = "BTC") -> dict:
        return self.classi[(channel, coin)]

    def test_dichiarato_fisso_e_regolare(self):
        v = self.verdetto("allMids", "_global")
        self.assertTrue(v["declared_fixed_rate"])
        self.assertEqual(v["verdict"], intervals.VERDICT_OK)

    def test_dichiarato_fisso_ma_a_raffiche_viene_smentito(self):
        v = self.verdetto("l2Book")
        self.assertTrue(v["declared_fixed_rate"])
        self.assertEqual(v["verdict"], intervals.VERDICT_DENIED)
        self.assertIn("p99/mediana", v["nota"])

    def test_non_dichiarato_e_a_raffiche(self):
        self.assertEqual(self.verdetto("trades")["verdict"], intervals.VERDICT_OK)

    def test_non_dichiarato_ma_regolare_viene_segnalato(self):
        v = self.verdetto("candle")
        self.assertFalse(v["declared_fixed_rate"])
        self.assertEqual(v["verdict"], intervals.VERDICT_UNDECLARED)

    def test_canale_rest_non_valutato(self):
        v = self.verdetto("backfill_candle")
        self.assertEqual(v["verdict"], intervals.VERDICT_SKIPPED)

    def test_il_verdetto_non_riscrive_la_classificazione(self):
        """Segnalare, non correggere: dopo il verdetto l'elenco dichiarato e'
        quello di prima. Se un giorno qualcuno lo "aggiustasse" in automatico,
        il flag low_volume cambierebbe di significato senza che nessuno lo
        abbia deciso."""
        dichiarati = {c for c, v in self.classi.items() if v["declared_fixed_rate"]}
        self.assertEqual({c[0] for c in dichiarati}, {"allMids", "l2Book"})


class TestVerdettoSospeso(Base):
    def test_pochi_intervalli(self):
        write_partition(self.data_dir, "allMids", "_global",
                        regolari(intervals.MIN_INTERVALS - 10))
        self.build(fixed_rate=frozenset({"allMids"}))
        v = self.classi[("allMids", "_global")]
        self.assertEqual(v["verdict"], intervals.VERDICT_SKIPPED)
        self.assertIn("intervalli utilizzabili", v["nota"])

    def test_mediana_zero_smentisce_la_cadenza_fissa(self):
        """Piu' della meta' dei messaggi consegnati nello stesso nanosecondo:
        il rapporto non esiste, e non viene inventato. Ma un canale simile non
        e' a cadenza fissa, e il verdetto lo dice."""
        rows = []
        t = BASE_NS
        for _ in range(300):
            rows += [(t, 0, "{}"), (t, 0, "{}"), (t, 0, "{}")]
            t += NS
        write_partition(self.data_dir, "l2Book", "BTC", rows)
        self.build(fixed_rate=frozenset({"l2Book"}))
        r = self.riga("l2Book")
        self.assertEqual(r["median_s"], 0.0)
        self.assertIsNone(r["ratio_p99_p50"])
        self.assertEqual(self.classi[("l2Book", "BTC")]["verdict"],
                         intervals.VERDICT_DENIED)


# ---------------------------------------------------------------------------
# 4. Le colonne finiscono nel parquet, non solo a schermo
# ---------------------------------------------------------------------------


class TestColonneNelParquet(Base):
    def setUp(self):
        super().setUp()
        write_partition(self.data_dir, "l2Book", "BTC", regolari(500))
        write_partition(self.data_dir, "trades", "BTC", raffiche(30))

    def test_hourly_metrics_contiene_le_colonne(self):
        import duckdb

        con = dataset.connect(os.path.join(self.tmp.name, "tmpdb"))
        self.addCleanup(con.close)
        partitions = dataset.discover(self.data_dir)
        gapwindows.materialize(con, [])
        metrics.build_hourly(con, self.data_dir, partitions)
        metrics.apply_gap_overlap(con)
        metrics.apply_low_volume(con, frozenset({"l2Book"}))
        sanity.build_ordered(con, self.data_dir, partitions)
        derivedgaps.build_thresholds(con)
        derivedgaps.build(con)
        intervals.build(con)
        classi = intervals.classify(intervals.stats(con), frozenset({"l2Book"}))
        intervals.attach_to_hourly(con, classi)

        path = os.path.join(self.tmp.name, "hourly_metrics.parquet")
        metrics.write_parquet(con, path)
        iv_path = os.path.join(self.tmp.name, "intervals.parquet")
        intervals.write_parquet(con, iv_path)

        letto = duckdb.connect()
        self.addCleanup(letto.close)
        cols = {
            r[0] for r in letto.execute(
                f"DESCRIBE SELECT * FROM read_parquet('{path}')"
            ).fetchall()
        }
        for c in intervals.HOURLY_COLUMNS:
            self.assertIn(c, cols)

        riga = letto.execute(
            f"SELECT iv_median_s, iv_p99_s, iv_ratio_p99_p50, iv_verdict "
            f"FROM read_parquet('{path}') WHERE channel = 'l2Book' LIMIT 1"
        ).fetchone()
        self.assertAlmostEqual(riga[0], 1.0)
        self.assertAlmostEqual(riga[1], 1.0)
        self.assertAlmostEqual(riga[2], 1.0)
        self.assertEqual(riga[3], intervals.VERDICT_OK)

        n = letto.execute(
            f"SELECT count(*) FROM read_parquet('{iv_path}')"
        ).fetchone()[0]
        self.assertEqual(n, 2)


# ---------------------------------------------------------------------------
# 5. La verifica sa fallire
# ---------------------------------------------------------------------------


class TestLaVerificaSaFallire(Base):
    """Con la logica disattivata — cioe' col difetto dentro — le asserzioni
    sopra devono esplodere. Se passassero lo stesso non starebbero verificando
    niente."""

    def setUp(self):
        super().setUp()
        write_partition(self.data_dir, "l2Book", "BTC", serie_con_buco())

    def test_senza_esclusione_il_buco_rientra(self):
        # Soglia irraggiungibile: rilevamento disattivato, il difetto e' dentro.
        self.build(min_gap_s=10_000.0)
        r = self.riga("l2Book")
        with self.assertRaises(AssertionError):
            self.assertAlmostEqual(r["max_s"], 1.0)
        with self.assertRaises(AssertionError):
            self.assertEqual(r["n_excluded_gap"], 1)
        # E il buco e' proprio li' dove ce lo aspettiamo.
        self.assertAlmostEqual(r["max_s"], float(BUCO_S))
        self.assertEqual(r["n"], PRIMA + DOPO - 1)

    def test_senza_esclusione_l_indice_smentirebbe_un_canale_sano(self):
        """Il caso che conta davvero: il buco da 600 s spinge il p99 e
        l'esclusione e' cio' che impedisce a un canale regolare di essere
        dichiarato a raffiche. Con 799 intervalli, uno solo da 600 s finisce
        oltre il p99 — quindi qui il difetto si vede sul massimo e sulla media,
        non sul verdetto: e' il motivo per cui il report pubblica anche
        `max_s` e `esclusi_gap` e non solo l'indice."""
        self.build(min_gap_s=10_000.0)
        r_rotto = self.riga("l2Book")
        self.build()
        r_pulito = self.riga("l2Book")
        self.assertGreater(r_rotto["max_s"], 100 * r_pulito["max_s"])
        self.assertGreater(r_rotto["mean_s"], r_pulito["mean_s"])
        with self.assertRaises(AssertionError):
            self.assertAlmostEqual(r_rotto["mean_s"], r_pulito["mean_s"], places=3)

    def test_col_registro_al_posto_dei_dati_il_buco_sopravvive(self):
        """La mutazione che questa modifica rimuove: escludere in base al
        registro invece che ai dati. Il registro qui dichiara una finestra da
        due secondi mentre l'assenza vera dura 600 s — e' il difetto misurato
        sui dati veri prima del 14 agosto 2026. Escludendo per finestra
        l'intervallo del buco resterebbe dentro la statistica; escludendo per
        soglia esce, e la riconciliazione misura la differenza."""
        buco_ms = BASE_MS + (PRIMA - 1) * 1000
        corta = window(buco_ms, buco_ms + 2000)

        self.build()
        pulito = self.riga("l2Book")
        self.assertEqual(pulito["n_excluded_gap"], 1)
        self.assertAlmostEqual(pulito["max_s"], 1.0)

        rec = derivedgaps.reconcile(
            [derivedgaps.DerivedGap("l2Book", "BTC", buco_ms * 10**6,
                                    (buco_ms + BUCO_S * 1000) * 10**6)],
            [corta],
        )
        self.assertEqual(rec["totali"]["n_spiegati"], 1)
        self.assertAlmostEqual(rec["spiegati"][0]["sottostima_s"], BUCO_S - 2)

    def test_senza_verifica_di_classificazione_nessuno_se_ne_accorge(self):
        """Un l2Book a raffiche resta dichiarato a cadenza fissa: e' proprio la
        cecita' che questa aggiunta rimuove. Il verdetto la rende visibile, ma
        `is_fixed_rate` in `hourly` non cambia."""
        write_partition(self.data_dir, "allMids", "_global", raffiche(30))
        self.build(fixed_rate=frozenset({"allMids"}))
        v = self.classi[("allMids", "_global")]
        self.assertEqual(v["verdict"], intervals.VERDICT_DENIED)
        with self.assertRaises(AssertionError):
            self.assertEqual(v["verdict"], intervals.VERDICT_OK)


if __name__ == "__main__":
    unittest.main()
