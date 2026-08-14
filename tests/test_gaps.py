"""
Test del registro delle finestre di disconnessione.

Due casi contano piu' degli altri, e sono i due che il registro sbagliava:

- processo ucciso mentre e' scollegato: se quel buco non sopravvive al
  riavvio, il registro e' peggio che inutile — dice "nessun buco" quando il
  buco c'era;
- buco chiuso alla riconnessione della socket invece che alla ripresa dei
  dati: dichiara validi dei minuti in cui non arrivava niente, che e' lo
  stesso errore mascherato meglio.

`TestLaVerificaSaFallire` in fondo rimette la logica difettosa al suo posto e
pretende che le asserzioni esplodano: un test che passa anche col difetto non
sta verificando niente.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collector.gaps import (
    CAUSE_FREEZE,
    FreezeDetector,
    Gap,
    GapRecorder,
    load_windows,
)


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


class FakeMonotonic:
    """Orologio monotono pilotato. Separato da FakeClock apposta: il salto si
    misura sul monotono, i timestamp registrati vengono da quello di sistema,
    e un test che li confondesse non accorgerebbe se il codice li confonde."""

    def __init__(self, t: float = 10_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class TestChiusuraOnesta(GapTestCase):
    """Una finestra si chiude quando i dati riprendono, per canale."""

    def _scenario_ripresa_ritardata(self, rec: GapRecorder):
        """Il caso reale del 2026-08-08: la socket torna su dopo 5s, l2Book
        riparte quasi subito, `trades` resta muto altri 90s."""
        rec.mark_disconnected("ConnectionClosedError: no close frame",
                              ["l2Book", "trades"])
        self.clock.advance(5)
        rec.mark_reconnected()
        self.clock.advance(2)
        rec.mark_data("l2Book")
        self.clock.advance(88)
        return rec.mark_data("trades")

    def _asserzioni_chiusura_onesta(self, rec: GapRecorder) -> None:
        """Estratte in un metodo perche' TestLaVerificaSaFallire le rilancia
        contro l'implementazione difettosa e pretende che esplodano."""
        closed = self._scenario_ripresa_ritardata(rec)
        self.assertIsNotNone(closed, "la finestra deve chiudersi alla ripresa dei dati")
        self.assertEqual(closed.duration_s, 95.0)

    def test_si_chiude_alla_ripresa_non_alla_riconnessione(self):
        rec = GapRecorder(self.path, clock=self.clock)
        self._asserzioni_chiusura_onesta(rec)

        eventi = [r["event"] for r in self.lines()]
        # due `resume` (un canale per volta) e poi la chiusura
        self.assertEqual(eventi, ["open", "reconnect", "resume", "resume", "close"])
        chiusura = self.lines()[-1]
        # La socket era su da 90s quando il buco e' finito davvero: e' il
        # numero che il registro precedente buttava via.
        self.assertEqual(chiusura["silence_after_reconnect_s"], 90.0)
        self.assertEqual(chiusura["duration_s"], 95.0)

    def test_canale_muto_piu_a_lungo_ha_una_finestra_piu_lunga(self):
        rec = GapRecorder(self.path, clock=self.clock)
        gap = self._scenario_ripresa_ritardata(rec)

        self.assertEqual(gap.duration_for("l2Book"), 7.0)
        self.assertEqual(gap.duration_for("trades"), 95.0)
        # L'aggregato e' il piu' lungo dei due: chi non ragiona per canale
        # deve comunque scartare tutto.
        self.assertEqual(gap.duration_s, 95.0)
        self.assertEqual(
            self.lines()[-1]["per_channel_duration_s"],
            {"l2Book": 7.0, "trades": 95.0},
        )

    def test_covers_per_canale(self):
        rec = GapRecorder(self.path, clock=self.clock)
        gap = self._scenario_ripresa_ritardata(rec)
        meta = gap.start_ms + 50_000  # 50s dopo il distacco

        self.assertTrue(gap.covers(meta, channel="trades"))
        self.assertFalse(gap.covers(meta, channel="l2Book"))
        self.assertTrue(gap.covers(meta))  # senza canale: conservativo

    def test_riconnessione_da_sola_non_chiude(self):
        rec = GapRecorder(self.path, clock=self.clock)
        rec.mark_disconnected("timeout", ["trades"])
        self.clock.advance(3)
        ancora_aperta = rec.mark_reconnected()

        self.assertIsNotNone(rec.current)
        self.assertEqual(ancora_aperta.pending_channels, ["trades"])
        self.assertEqual([r["event"] for r in self.lines()], ["open", "reconnect"])

    def test_canale_estraneo_non_chiude_la_finestra(self):
        # Un messaggio su un canale che non era dichiarato nella finestra non
        # dice niente sui canali che stiamo aspettando.
        rec = GapRecorder(self.path, clock=self.clock)
        rec.mark_disconnected("timeout", ["trades"])
        self.clock.advance(4)
        self.assertIsNone(rec.mark_data("allMids"))
        self.assertIsNotNone(rec.current)

    def test_secondo_distacco_annulla_i_ritorni_gia_visti(self):
        # Se cade di nuovo, i canali gia' ripartiti sono di nuovo muti: la
        # finestra non puo' chiudersi contando una ripresa che non vale piu'.
        rec = GapRecorder(self.path, clock=self.clock)
        rec.mark_disconnected("primo distacco", ["l2Book", "trades"])
        self.clock.advance(2)
        rec.mark_reconnected()
        self.clock.advance(1)
        rec.mark_data("l2Book")
        self.clock.advance(1)
        rec.mark_disconnected("secondo distacco", ["l2Book", "trades"])
        self.clock.advance(10)
        rec.mark_reconnected()
        self.clock.advance(1)
        self.assertIsNone(rec.mark_data("l2Book"))  # deve ricontare
        self.clock.advance(1)
        gap = rec.mark_data("trades")

        self.assertIsNotNone(gap)
        self.assertEqual(gap.duration_s, 16.0)  # dal PRIMO distacco
        self.assertIn("reopen", [r["event"] for r in self.lines()])

    def test_finestra_senza_elenco_canali_chiude_al_primo_dato(self):
        # Registro vecchio o apertura senza elenco: non si puo' ragionare per
        # canale, ma la finestra deve comunque poter finire.
        rec = GapRecorder(self.path, clock=self.clock)
        rec.mark_disconnected("senza elenco")
        self.clock.advance(9)
        gap = rec.mark_data("trades")
        self.assertEqual(gap.duration_s, 9.0)

    def test_nessun_falso_positivo_in_funzionamento_normale(self):
        # Riconnessione pulita: tutti i canali ripartono in un secondo. La
        # finestra deve restare corta, non allungarsi per costruzione.
        rec = GapRecorder(self.path, clock=self.clock)
        canali = ["activeAssetCtx", "allMids", "candle", "l2Book", "trades"]
        rec.mark_disconnected("closed by server", canali)
        self.clock.advance(1.4)
        rec.mark_reconnected()
        for ch in canali:
            self.clock.advance(0.1)
            gap = rec.mark_data(ch)
        self.assertIsNotNone(gap)
        self.assertLess(gap.duration_s, 2.0)
        self.assertIsNone(rec.current)

    def test_rilettura_ricostruisce_i_ritorni_per_canale(self):
        rec = GapRecorder(self.path, clock=self.clock)
        self._scenario_ripresa_ritardata(rec)

        (finestra,) = load_windows(self.path)
        self.assertEqual(finestra.duration_s, 95.0)
        self.assertEqual(finestra.duration_for("trades"), 95.0)
        self.assertEqual(finestra.duration_for("l2Book"), 7.0)
        self.assertIsNotNone(finestra.reconnect_ms)

    def test_ripresa_parziale_sopravvive_al_riavvio(self):
        # Processo ucciso mentre `trades` era ancora muto: il registro deve
        # ricordare che l2Book era tornato e che trades no.
        morto = GapRecorder(self.path, clock=self.clock)
        morto.mark_disconnected("kill durante il buco", ["l2Book", "trades"])
        self.clock.advance(5)
        morto.mark_reconnected()
        self.clock.advance(2)
        morto.mark_data("l2Book")
        del morto

        nuovo = GapRecorder(self.path, clock=self.clock)
        self.assertIsNotNone(nuovo.current)
        self.assertEqual(nuovo.current.pending_channels, ["trades"])
        self.clock.advance(60)
        gap = nuovo.mark_data("trades")
        self.assertEqual(gap.duration_s, 67.0)
        self.assertEqual(gap.duration_for("l2Book"), 7.0)


class TestCongelamento(GapTestCase):
    """Il processo fermo con la socket viva: nessun errore, nessuna
    riconnessione, nessuna riga. Resta solo il salto dell'orologio."""

    def setUp(self):
        super().setUp()
        self.mono = FakeMonotonic()
        self.rec = GapRecorder(self.path, clock=self.clock)
        self.det = FreezeDetector(self.rec, tick_s=10, threshold_s=60,
                                  monotonic=self.mono)

    def _passa(self, secondi: float):
        """Il tempo passa per davvero: sia il monotono che l'orologio di
        sistema. Farli avanzare separatamente maschererebbe uno scambio."""
        self.mono.advance(secondi)
        self.clock.advance(secondi)
        return self.det.tick(["l2Book", "trades"])

    def test_salto_monotono_registra_un_congelamento(self):
        # Il caso del 2026-08-14: 4963s di processo fermo, socket aperta.
        self.assertIsNone(self._passa(10))
        gap = self._passa(4973)

        self.assertIsNotNone(gap)
        self.assertEqual(gap.cause, CAUSE_FREEZE)
        # La finestra parte dal risveglio precedente del watchdog: il momento
        # esatto in cui il processo si e' fermato non e' osservabile, e
        # sovrastimare di un tick e' la direzione giusta in cui sbagliare.
        self.assertEqual(gap.duration_s, 4973.0)
        self.assertEqual(gap.channels, ["l2Book", "trades"])

        apertura, chiusura = self.lines()
        self.assertEqual(apertura["event"], "open")
        self.assertEqual(chiusura["event"], "close")
        self.assertEqual(chiusura["monotonic_jump_s"], 4963.0)
        self.assertEqual(chiusura["duration_s"], 4973.0)

    def test_timestamp_dall_orologio_di_sistema(self):
        # I timestamp registrati devono essere confrontabili coi dati, quindi
        # vengono dall'orologio di sistema; il monotono serve solo a misurare.
        self._passa(10)
        gap = self._passa(1000)
        self.assertEqual(gap.end_ms, int(self.clock.t * 1000))
        self.assertEqual(gap.start_ms, gap.end_ms - 1_000_000)

    def test_nessun_falso_positivo_a_regime(self):
        # Mille risvegli con jitter dello scheduler, piu' un ritardo grosso ma
        # sotto soglia: nessuno di questi e' un congelamento.
        for i in range(1000):
            self.assertIsNone(self._passa(10 + (i % 7) * 0.03))
        self.assertIsNone(self._passa(69))          # salto di 59s, sotto soglia
        self.assertIsNone(self._passa(70))          # salto di 60s, soglia esatta
        self.assertFalse(os.path.exists(self.path))

        self.assertIsNotNone(self._passa(70.5))     # salto di 60.5s: sopra

    def test_non_disturba_la_finestra_aperta(self):
        # Un congelamento e' un fatto indipendente dalla disconnessione che
        # puo' averlo seguito: due finestre sovrapposte sono l'unione dei
        # periodi da scartare, ed e' cosi' che deve restare.
        self.rec.mark_disconnected("gia' scollegato", ["l2Book", "trades"])
        aperta = self.rec.current
        self._passa(10)
        self._passa(300)

        self.assertIs(self.rec.current, aperta)
        self.assertIsNone(self.rec.current.end_ms)

        finestre = load_windows(self.path)
        self.assertEqual(len(finestre), 2)
        self.assertIsNone(finestre[0].end_ms)                 # la disconnessione
        self.assertEqual(finestre[1].cause, CAUSE_FREEZE)
        self.assertEqual(finestre[1].duration_s, 300.0)


class RecorderConVecchioDifetto(GapRecorder):
    """L'implementazione precedente: la finestra si chiude quando la socket si
    riconnette, e i dati che arrivano non contano. Esiste solo per verificare
    che i test qui sopra sappiano fallire."""

    def mark_reconnected(self):
        return self.mark_connected()

    def mark_data(self, channel):
        return None


class RilevatoreSpento(FreezeDetector):
    """Rilevatore di congelamento disattivato."""

    def tick(self, channels=None):
        return None


class TestLaVerificaSaFallire(GapTestCase):
    """Un test che passa anche col difetto rimesso dentro non sta verificando
    niente. Qui il difetto viene rimesso dentro apposta."""

    def test_chiusura_onesta(self):
        prova = TestChiusuraOnesta("test_si_chiude_alla_ripresa_non_alla_riconnessione")
        prova.clock = self.clock
        rotto = RecorderConVecchioDifetto(self.path, clock=self.clock)
        with self.assertRaises(AssertionError):
            prova._asserzioni_chiusura_onesta(rotto)

        # ...e lo stesso scenario, sul recorder corretto, passa.
        prova.clock = FakeClock()
        buono = GapRecorder(os.path.join(self.tmp.name, "buono.jsonl"),
                            clock=prova.clock)
        prova._asserzioni_chiusura_onesta(buono)

    def test_durata_per_canale(self):
        rotto = RecorderConVecchioDifetto(self.path, clock=self.clock)
        rotto.mark_disconnected("x", ["l2Book", "trades"])
        self.clock.advance(5)
        gap = rotto.mark_reconnected()
        self.clock.advance(90)
        rotto.mark_data("trades")
        with self.assertRaises(AssertionError):
            self.assertEqual(gap.duration_for("trades"), 95.0)

    def test_rilevamento_congelamento(self):
        mono = FakeMonotonic()
        rec = GapRecorder(self.path, clock=self.clock)
        spento = RilevatoreSpento(rec, tick_s=10, threshold_s=60, monotonic=mono)
        mono.advance(4973)
        self.clock.advance(4973)
        with self.assertRaises(AssertionError):
            self.assertIsNotNone(spento.tick(["trades"]))
        self.assertFalse(os.path.exists(self.path))


if __name__ == "__main__":
    unittest.main()
