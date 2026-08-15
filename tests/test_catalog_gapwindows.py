"""Test del contratto di lettura di `_gaps.jsonl`.

Il contratto: si considerano TUTTI i record che dichiarano una durata,
qualunque sia il valore di `event`. Il file contiene `open`, `close`,
`reconnect`, `resume`, e almeno una riga `manual` — la correzione scritta a
mano per il blackout da 4959 s del 14 agosto 2026, che e' l'interruzione piu'
lunga dell'intera raccolta.

Un lettore che filtra su `event == "close"` salta quella riga in silenzio.
Non e' un'ipotesi: e' successo in un'analisi, e il tempo scollegato risultava
inferiore di oltre un'ora. `TestLaVerificaSaFallire` in fondo rimette quel
filtro al suo posto e pretende che le asserzioni esplodano.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catalog import gapwindows

# Riga reale del registro di produzione, accorciata nel motivo. Il blackout da
# 4959 s del 14 agosto 2026: processo congelato per esaurimento di memoria
# della macchina, socket TCP ancora aperta, nessun errore da nessuna parte.
MANUAL_START = 1786722929000
MANUAL_END = 1786727888000
MANUAL_DURATA_S = 4959.0

NOW = 1786800000000


def riga(**kw) -> str:
    return json.dumps(kw, separators=(",", ":"))


class GapWindowsTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "_gaps.jsonl")
        self.addCleanup(self.tmp.cleanup)

    def scrivi(self, righe: list[str]) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            for r in righe:
                f.write(r + "\n")

    def scrivi_registro_realistico(self) -> None:
        """Un registro con la stessa varieta' di eventi di quello vero."""
        self.scrivi([
            # finestra ordinaria: apertura + chiusura
            riga(event="open", start_ms=1_000_000, reason="shutdown",
                 channels=["l2Book", "trades"]),
            riga(event="close", start_ms=1_000_000, end_ms=1_060_000,
                 duration_s=60.0),
            # riconnessione con riprese per canale
            riga(event="open", start_ms=2_000_000, reason="disconnect",
                 channels=["l2Book", "trades"]),
            riga(event="reconnect", start_ms=2_000_000, reconnect_ms=2_001_000),
            riga(event="resume", start_ms=2_000_000, channel="trades",
                 end_ms=2_002_000, duration_s=2.0),
            riga(event="resume", start_ms=2_000_000, channel="l2Book",
                 end_ms=2_005_000, duration_s=5.0),
            riga(event="close", start_ms=2_000_000, end_ms=2_005_000,
                 duration_s=5.0),
            # la correzione a mano: ne' open ne' close
            riga(event="manual", start_ms=MANUAL_START, end_ms=MANUAL_END,
                 duration_s=MANUAL_DURATA_S,
                 reason="congelamento del processo per esaurimento di memoria"),
        ])

    def durate(self, windows) -> list[float]:
        return sorted(w.duration_s for w in windows)


class TestTuttiIRecordConDurata(GapWindowsTestCase):
    def test_il_record_manual_entra_nel_conto(self):
        self.scrivi_registro_realistico()
        windows = gapwindows.load(self.path, NOW)
        durate = self.durate(windows)
        self.assertIn(MANUAL_DURATA_S, durate)
        self.assertEqual(max(durate), MANUAL_DURATA_S)
        self.assertAlmostEqual(sum(durate), 60.0 + 5.0 + MANUAL_DURATA_S)

    def test_e_la_finestra_piu_lunga(self):
        self.scrivi_registro_realistico()
        windows = gapwindows.load(self.path, NOW)
        piu_lunga = max(windows, key=lambda w: w.duration_s)
        self.assertEqual(piu_lunga.start_ms, MANUAL_START)
        self.assertEqual(piu_lunga.end_ms, MANUAL_END)

    def test_evento_sconosciuto_con_durata_viene_recuperato(self):
        """Il punto del contratto non e' `manual`: e' che nessun record con una
        durata sparisca. Un evento introdotto domani non deve svanire perche'
        il parser non lo conosce ancora."""
        self.scrivi_registro_realistico()
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(riga(event="blackout_da_analisi_esterna", start_ms=9_000_000,
                         duration_s=120.0, reason="verificato a mano") + "\n")
        windows, audit = gapwindows.load_with_audit(self.path, NOW)
        recuperate = [w for w in windows if w.origin == gapwindows.ORIGIN_RECOVERED]
        self.assertEqual(len(recuperate), 1)
        self.assertEqual(recuperate[0].duration_s, 120.0)
        self.assertEqual(recuperate[0].event, "blackout_da_analisi_esterna")
        self.assertEqual(audit["recuperati"], 1)
        self.assertIn(120.0, self.durate(windows))

    def test_durata_ricostruita_da_duration_s_senza_end_ms(self):
        self.scrivi([riga(event="patch", start_ms=5_000_000, duration_s=1.5)])
        windows = gapwindows.load(self.path, NOW)
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0].end_ms, 5_001_500)

    def test_i_resume_non_creano_finestre_doppie(self):
        """Un `resume` dichiara una durata ma sta dentro la finestra che lo ha
        generato: contarlo di nuovo gonfierebbe il tempo scollegato."""
        self.scrivi_registro_realistico()
        windows, audit = gapwindows.load_with_audit(self.path, NOW)
        self.assertEqual(len(windows), 3)
        self.assertEqual(audit["recuperati"], 0)
        self.assertEqual(audit["eventi_con_durata"],
                         {"close": 2, "manual": 1, "resume": 2})
        self.assertEqual(audit["record_con_durata"], 5)

    def test_record_senza_durata_ignorati(self):
        """`open` e `reconnect` non dicono quanto e' durato il buco: la loro
        semantica sta nel parser condiviso, non nel recupero."""
        self.scrivi([
            riga(event="open", start_ms=1_000_000, reason="x", channels=[]),
            riga(event="reconnect", start_ms=1_000_000, reconnect_ms=1_001_000),
        ])
        windows, audit = gapwindows.load_with_audit(self.path, NOW)
        self.assertEqual(audit["record_con_durata"], 0)
        self.assertEqual(audit["recuperati"], 0)
        # La finestra aperta resta aperta e viene estesa fino a `now`.
        self.assertEqual(len(windows), 1)
        self.assertTrue(windows[0].still_open)
        self.assertEqual(windows[0].end_ms, NOW)

    def test_righe_illeggibili_non_fanno_perdere_il_resto(self):
        self.scrivi_registro_realistico()
        with open(self.path, "a", encoding="utf-8") as f:
            f.write('{"event":"close","start_ms":')  # kill a meta' riga
        windows = gapwindows.load(self.path, NOW)
        self.assertIn(MANUAL_DURATA_S, self.durate(windows))

    def test_registro_assente(self):
        windows, audit = gapwindows.load_with_audit(
            os.path.join(self.tmp.name, "non-esiste.jsonl"), NOW)
        self.assertEqual(windows, [])
        self.assertEqual(audit["record_con_durata"], 0)


class TestMaterializzazione(GapWindowsTestCase):
    def test_le_finestre_finiscono_in_duckdb_con_origine(self):
        import duckdb

        self.scrivi_registro_realistico()
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(riga(event="patch", start_ms=9_000_000, duration_s=30.0) + "\n")
        windows = gapwindows.load(self.path, NOW)
        con = duckdb.connect()
        gapwindows.materialize(con, windows)
        tot = con.execute(
            "SELECT sum(end_ms - start_ms) / 1000.0 FROM gap_windows"
        ).fetchone()[0]
        self.assertAlmostEqual(tot, 60.0 + 5.0 + MANUAL_DURATA_S + 30.0)
        self.assertEqual(
            con.execute("SELECT count(*) FROM gap_windows WHERE origin = ?",
                        [gapwindows.ORIGIN_RECOVERED]).fetchone()[0], 1)
        con.close()

    def test_registro_vuoto_crea_comunque_la_tabella(self):
        import duckdb

        con = duckdb.connect()
        gapwindows.materialize(con, [])
        self.assertEqual(
            con.execute("SELECT count(*) FROM gap_windows").fetchone()[0], 0)
        con.close()


class TestLaVerificaSaFallire(GapWindowsTestCase):
    """Il filtro difettoso rimesso al suo posto: `event == "close"`."""

    @staticmethod
    def _solo_close(path: str) -> float:
        """La lettura sbagliata, quella dell'analisi che ha perso il blackout."""
        tot = 0.0
        with open(path, encoding="utf-8") as f:
            for line in f:
                rec = json.loads(line)
                if rec.get("event") == "close":
                    tot += rec["duration_s"]
        return tot

    def test_il_filtro_su_close_perde_il_blackout(self):
        self.scrivi_registro_realistico()
        corretto = sum(w.duration_s for w in gapwindows.load(self.path, NOW))
        difettoso = self._solo_close(self.path)

        with self.assertRaises(AssertionError):
            self.assertAlmostEqual(difettoso, corretto)
        # E l'errore non e' marginale: e' l'interruzione piu' grave.
        self.assertAlmostEqual(corretto - difettoso, MANUAL_DURATA_S)
        self.assertLess(difettoso, corretto / 50)

    def test_col_filtro_la_finestra_piu_lunga_diventa_un_altra(self):
        self.scrivi_registro_realistico()
        with open(self.path, encoding="utf-8") as f:
            righe = [json.loads(l) for l in f]
        max_close = max(r["duration_s"] for r in righe if r.get("event") == "close")
        with self.assertRaises(AssertionError):
            self.assertEqual(max_close, MANUAL_DURATA_S)
        self.assertEqual(max_close, 60.0)


if __name__ == "__main__":
    unittest.main()
