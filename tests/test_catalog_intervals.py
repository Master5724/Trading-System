"""Test della distribuzione degli intervalli fra messaggi e della verifica
di classificazione dei canali.

Tre difetti da coprire, e per ognuno la prova che il test sa fallire:

1. **Gli intervalli dentro un buco registrato inquinano la distribuzione.**
   `TestBuchiEsclusi` misura una serie regolare interrotta da un'interruzione
   di 600 s; `TestLaVerificaSaFallire.test_senza_esclusione_*` rifa' lo stesso
   conto con l'esclusione disattivata e pretende che le asserzioni esplodano.

2. **Il contratto di lettura di `_gaps.jsonl` e' "tutti i record con una
   durata", non "event == close".** `TestContrattoDelRegistro` scrive un
   evento che il parser condiviso non conosce e verifica che l'intervallo
   venga escluso lo stesso; la variante col difetto usa solo
   `collector.gaps.load_windows` e mostra che il buco tornerebbe dentro.

3. **La classificazione "a cadenza fissa" e' dichiarata, non misurata.**
   `TestVerdetto` costruisce un canale dichiarato fisso che si comporta a
   raffiche e pretende `smentito`.

Tutto su directory temporanee: i dati di produzione sono scritti da un
collector vivo, cambiano sotto i piedi e sono in sola lettura.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catalog import dataset, gapwindows, intervals, metrics, sanity
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

    def build(self, windows=(), fixed_rate=None):
        """Costruisce `ts_ordered` e `intervals` sulla data_dir corrente."""
        con = dataset.connect(os.path.join(self.tmp.name, "tmpdb"))
        self.addCleanup(con.close)
        partitions = dataset.discover(self.data_dir)
        sanity.build_ordered(con, self.data_dir, partitions)
        intervals.build(con, list(windows))
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


# La finestra registrata sta DENTRO il buco: inizia un secondo dopo l'ultimo
# messaggio e finisce un secondo prima del primo della ripresa. Cosi' l'unico
# intervallo che la interseca e' quello che scavalca l'interruzione, e i due
# intervalli adiacenti restano dentro la statistica: se il codice usasse una
# regola di sovrapposizione piu' larga, il test se ne accorgerebbe.
BUCO_START_MS = BASE_MS + (PRIMA - 1) * 1000 + 1000
BUCO_END_MS = BUCO_START_MS + (BUCO_S - 2) * 1000


class TestBuchiEsclusi(Base):
    def setUp(self):
        super().setUp()
        write_partition(self.data_dir, "l2Book", "BTC", serie_con_buco())

    def test_l_intervallo_del_buco_non_entra(self):
        self.build([window(BUCO_START_MS, BUCO_END_MS)])
        r = self.riga("l2Book")
        self.assertEqual(r["n_pairs"], PRIMA + DOPO - 1)
        self.assertEqual(r["n_excluded_gap"], 1)
        self.assertEqual(r["n"], PRIMA + DOPO - 2)
        # Il massimo torna a essere la cadenza normale: il buco non c'e' piu'.
        self.assertAlmostEqual(r["max_s"], 1.0)
        self.assertAlmostEqual(r["median_s"], 1.0)
        self.assertAlmostEqual(r["ratio_p99_p50"], 1.0)

    def test_gli_intervalli_adiacenti_restano(self):
        """Escludere per intersezione non deve diventare escludere il vicinato:
        n_excluded_gap == 1 lo dice, e il conteggio finale lo conferma."""
        con = self.build([window(BUCO_START_MS, BUCO_END_MS)])
        n_uno_secondo = con.execute(
            "SELECT n FROM intervals WHERE channel = 'l2Book'"
        ).fetchone()[0]
        self.assertEqual(n_uno_secondo, PRIMA + DOPO - 2)

    def test_finestra_che_tocca_il_bordo_non_esclude(self):
        """Una finestra che finisce esattamente dove comincia un intervallo non
        lo interseca. Con `>=` invece di `>` il conto salterebbe."""
        fine_ms = BASE_MS + 10 * 1000
        self.build([window(fine_ms - 5000, fine_ms)])
        r = self.riga("l2Book")
        self.assertEqual(r["n_excluded_gap"], 5)

    def test_finestra_che_copre_tutto(self):
        self.build([window(BASE_MS - 1000, BASE_MS + 10_000_000)])
        r = self.riga("l2Book")
        self.assertEqual(r["n"], 0)
        self.assertEqual(r["n_excluded_gap"], PRIMA + DOPO - 1)
        self.assertIsNone(r["median_s"])
        self.assertIsNone(r["ratio_p99_p50"])


class TestFinestreFuse(unittest.TestCase):
    """Il join che esclude gli intervalli cerca l'ULTIMA finestra che inizia
    prima della fine dell'intervallo. Quel ragionamento vale solo se le
    finestre sono disgiunte, e nel registro vero non lo sono."""

    def test_sovrapposte_e_annidate(self):
        w = [window(1000, 2000), window(1500, 1800), window(1900, 3000)]
        self.assertEqual(intervals.merge_windows(w),
                         [(1000 * 10**6, 3000 * 10**6)])

    def test_disgiunte_restano_separate(self):
        w = [window(1000, 2000), window(5000, 6000)]
        self.assertEqual(len(intervals.merge_windows(w)), 2)

    def test_adiacenti_vengono_fuse(self):
        w = [window(1000, 2000), window(2000, 3000)]
        self.assertEqual(intervals.merge_windows(w),
                         [(1000 * 10**6, 3000 * 10**6)])

    def test_registro_vuoto(self):
        self.assertEqual(intervals.merge_windows([]), [])


class TestContrattoDelRegistro(Base):
    """`_gaps.jsonl` non contiene solo open/close: contiene `manual`, `resume`,
    e domani conterra' qualcosa che oggi non esiste. Il contratto e' "tutti i
    record che dichiarano una durata"."""

    def setUp(self):
        super().setUp()
        write_partition(self.data_dir, "l2Book", "BTC", serie_con_buco())
        self.gaps = os.path.join(self.tmp.name, "_gaps.jsonl")
        with open(self.gaps, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "event": "blackout_ricostruito_a_mano",
                "start_ms": BUCO_START_MS,
                "duration_s": (BUCO_END_MS - BUCO_START_MS) / 1000.0,
                "reason": "evento che il parser condiviso non conosce",
            }) + "\n")

    def test_evento_sconosciuto_esclude_lo_stesso(self):
        windows = gapwindows.load(self.gaps, now_ms=BUCO_END_MS + 10**7)
        self.assertEqual(len(windows), 1)
        self.build(windows)
        self.assertEqual(self.riga("l2Book")["n_excluded_gap"], 1)
        self.assertAlmostEqual(self.riga("l2Book")["max_s"], 1.0)


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
        intervals.build(con, [])
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
        self.build([])  # nessuna finestra: esclusione disattivata
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
        self.build([])
        r_rotto = self.riga("l2Book")
        self.build([window(BUCO_START_MS, BUCO_END_MS)])
        r_pulito = self.riga("l2Book")
        self.assertGreater(r_rotto["max_s"], 100 * r_pulito["max_s"])
        self.assertGreater(r_rotto["mean_s"], r_pulito["mean_s"])
        with self.assertRaises(AssertionError):
            self.assertAlmostEqual(r_rotto["mean_s"], r_pulito["mean_s"], places=3)

    def test_col_filtro_su_event_close_il_buco_sparisce(self):
        """La mutazione del contratto di lettura: leggere il registro con il
        solo parser condiviso (che ignora gli eventi sconosciuti) invece che
        con `gapwindows.load`. La finestra non esiste piu' e l'intervallo del
        buco rientra nella statistica."""
        from collector.gaps import load_windows

        gaps = os.path.join(self.tmp.name, "_gaps.jsonl")
        with open(gaps, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "event": "blackout_ricostruito_a_mano",
                "start_ms": BUCO_START_MS,
                "duration_s": (BUCO_END_MS - BUCO_START_MS) / 1000.0,
            }) + "\n")

        col_difetto = [
            g for g in load_windows(gaps) if g.end_ms is not None
        ]
        self.assertEqual(col_difetto, [], "il parser condiviso ignora l'evento")
        self.build(col_difetto)
        with self.assertRaises(AssertionError):
            self.assertEqual(self.riga("l2Book")["n_excluded_gap"], 1)

        corretto = gapwindows.load(gaps, now_ms=BUCO_END_MS + 10**7)
        self.build(corretto)
        self.assertEqual(self.riga("l2Book")["n_excluded_gap"], 1)

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
