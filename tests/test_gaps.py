"""
Test del registro delle finestre di disconnessione.

Il caso che conta davvero e' l'ultimo: processo ucciso mentre e' scollegato.
Se quel buco non sopravvive al riavvio, il registro e' peggio che inutile —
e' un registro che dice "nessun buco" quando il buco c'era.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collector.gaps import Gap, GapRecorder, load_windows


class FakeClock:
    """Orologio pilotato: le durate attese devono essere esatte, non 'circa'."""

    def __init__(self, t: float = 1_700_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class GapTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "nested", "_gaps.jsonl")
        self.clock = FakeClock()

    def tearDown(self):
        self.tmp.cleanup()

    def lines(self) -> list[dict]:
        with open(self.path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]


class TestRegistrazione(GapTestCase):
    def test_finestra_completa(self):
        rec = GapRecorder(self.path, clock=self.clock)
        rec.mark_disconnected("ConnectionClosed: 1006", ["trades", "l2Book"])
        self.clock.advance(42.5)
        gap = rec.mark_connected()

        self.assertIsNotNone(gap)
        self.assertEqual(gap.duration_s, 42.5)
        self.assertEqual(gap.channels, ["l2Book", "trades"])  # ordinati
        self.assertEqual(gap.reason, "ConnectionClosed: 1006")

        opened, closed = self.lines()
        self.assertEqual(opened["event"], "open")
        self.assertEqual(closed["event"], "close")
        self.assertEqual(closed["start_ms"], opened["start_ms"])
        self.assertEqual(closed["duration_s"], 42.5)
        self.assertIn("start_iso", opened)

    def test_crea_la_cartella_se_manca(self):
        GapRecorder(self.path, clock=self.clock).mark_disconnected("boh")
        self.assertTrue(os.path.exists(self.path))

    def test_disconnessioni_ripetute_non_riaprono(self):
        # Riconnessioni che falliscono a ripetizione sono UN buco solo, che
        # inizia al primo distacco. Contarli come tanti buchi corti maschera
        # la sola cosa che interessa: da quando a quando mancano i dati.
        rec = GapRecorder(self.path, clock=self.clock)
        first = rec.mark_disconnected("timeout", ["trades"])
        self.clock.advance(5)
        again = rec.mark_disconnected("timeout", ["trades"])
        self.assertIs(again, first)
        self.clock.advance(5)
        gap = rec.mark_connected()

        self.assertEqual(gap.duration_s, 10.0)
        self.assertEqual(len(self.lines()), 2)

    def test_connessione_senza_buco_aperto(self):
        rec = GapRecorder(self.path, clock=self.clock)
        self.assertIsNone(rec.mark_connected())
        self.assertFalse(os.path.exists(self.path))

    def test_current(self):
        rec = GapRecorder(self.path, clock=self.clock)
        self.assertIsNone(rec.current)
        rec.mark_disconnected("x")
        self.assertIsNotNone(rec.current)
        rec.mark_connected()
        self.assertIsNone(rec.current)


class TestRilettura(GapTestCase):
    def test_load_windows_ordine_e_contenuto(self):
        rec = GapRecorder(self.path, clock=self.clock)
        rec.mark_disconnected("primo", ["trades"])
        self.clock.advance(10)
        rec.mark_connected()
        self.clock.advance(100)
        rec.mark_disconnected("secondo", ["l2Book"])
        self.clock.advance(3)
        rec.mark_connected()

        windows = load_windows(self.path)
        self.assertEqual([w.reason for w in windows], ["primo", "secondo"])
        self.assertEqual([w.duration_s for w in windows], [10.0, 3.0])

    def test_file_assente(self):
        self.assertEqual(load_windows(self.path), [])

    def test_riga_troncata_non_invalida_lo_storico(self):
        # Un kill a meta' write lascia una riga monca in coda.
        rec = GapRecorder(self.path, clock=self.clock)
        rec.mark_disconnected("buono", ["trades"])
        self.clock.advance(7)
        rec.mark_connected()
        with open(self.path, "a", encoding="utf-8") as f:
            f.write('{"event":"open","start_m')

        windows = load_windows(self.path)
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0].duration_s, 7.0)

    def test_finestra_aperta_sopravvive_al_riavvio(self):
        # Processo ucciso mentre era scollegato: nessuna riga `close`.
        morto = GapRecorder(self.path, clock=self.clock)
        morto.mark_disconnected("SIGKILL durante la disconnessione", ["trades"])
        del morto

        windows = load_windows(self.path)
        self.assertEqual(len(windows), 1)
        self.assertIsNone(windows[0].end_ms)
        self.assertIsNone(windows[0].duration_s)

        # Il processo nuovo adotta la finestra e la chiude alla riconnessione,
        # invece di lasciarla aperta per sempre o di aprirne una seconda.
        self.clock.advance(3600)
        nuovo = GapRecorder(self.path, clock=self.clock)
        self.assertIsNotNone(nuovo.current)
        gap = nuovo.mark_connected()
        self.assertEqual(gap.duration_s, 3600.0)

        windows = load_windows(self.path)
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0].duration_s, 3600.0)


class TestCovers(unittest.TestCase):
    def test_finestra_chiusa(self):
        gap = Gap(start_ms=1000, reason="x", end_ms=2000)
        self.assertTrue(gap.covers(1500))
        self.assertTrue(gap.covers(1000))
        self.assertTrue(gap.covers(2000))
        self.assertFalse(gap.covers(999))
        self.assertFalse(gap.covers(2001))

    def test_finestra_aperta_copre_fino_a_ora(self):
        # Default conservativo: un periodo che non sappiamo classificare va
        # trattato come mancante, non come buono.
        gap = Gap(start_ms=1000, reason="x")
        self.assertTrue(gap.covers(5000, now_ms=9000))
        self.assertFalse(gap.covers(10_000, now_ms=9000))


if __name__ == "__main__":
    unittest.main()
