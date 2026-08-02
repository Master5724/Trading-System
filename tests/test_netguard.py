"""
Test della guardia rete/directory.

Il caso che conta e' il terzo: puntare un config mainnet sulla directory di
testnet deve essere un errore rumoroso all'avvio, non una raccolta mescolata
che si scopre settimane dopo. Il quarto verifica la parte facile da sbagliare:
quando rifiuta, non deve aver scritto niente.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collector.netguard import (
    MARKER_NAME,
    NetworkMismatchError,
    claim_data_dir,
    marker_path,
)


class TestClaimDataDir(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def _read_marker(self, data_dir: str) -> str:
        with open(marker_path(data_dir), encoding="utf-8") as f:
            return f.read().strip()

    def test_marcatore_assente_viene_creato(self) -> None:
        d = os.path.join(self.root, "data")
        claim_data_dir(d, "testnet")
        self.assertEqual(self._read_marker(d), "testnet")

    def test_directory_inesistente_viene_creata(self) -> None:
        d = os.path.join(self.root, "nuova", "annidata")
        claim_data_dir(d, "mainnet")
        self.assertTrue(os.path.isdir(d))
        self.assertEqual(self._read_marker(d), "mainnet")

    def test_stessa_rete_riparte(self) -> None:
        d = os.path.join(self.root, "data")
        claim_data_dir(d, "testnet")
        # Il riavvio e' il caso normale: deve essere idempotente.
        claim_data_dir(d, "testnet")
        claim_data_dir(d, "testnet")
        self.assertEqual(self._read_marker(d), "testnet")

    def test_rete_diversa_errore(self) -> None:
        d = os.path.join(self.root, "data")
        claim_data_dir(d, "testnet")
        with self.assertRaises(NetworkMismatchError) as ctx:
            claim_data_dir(d, "mainnet")
        msg = str(ctx.exception)
        # Il messaggio deve dire quale rete ha trovato e quale si aspettava,
        # altrimenti chi lo legge nel journal non sa quale dei due correggere.
        self.assertIn("testnet", msg)
        self.assertIn("mainnet", msg)
        self.assertIn(d, msg)

    def test_mismatch_non_scrive_nulla(self) -> None:
        d = os.path.join(self.root, "data")
        claim_data_dir(d, "testnet")
        prima = sorted(os.listdir(d))

        with self.assertRaises(NetworkMismatchError):
            claim_data_dir(d, "mainnet")

        self.assertEqual(sorted(os.listdir(d)), prima)
        # Nessun file temporaneo lasciato indietro, e il marcatore dell'altra
        # rete e' intatto.
        self.assertEqual(prima, [MARKER_NAME])
        self.assertEqual(self._read_marker(d), "testnet")

    def test_marcatore_vuoto_e_un_errore(self) -> None:
        # Un marcatore troncato non autorizza a scrivere: non sappiamo di chi
        # sia la directory.
        d = os.path.join(self.root, "data")
        os.makedirs(d)
        with open(marker_path(d), "w", encoding="utf-8") as f:
            f.write("")
        with self.assertRaises(NetworkMismatchError):
            claim_data_dir(d, "mainnet")

    def test_marcatore_tollera_spazi_e_newline(self) -> None:
        # Scritto a mano con un editor: non deve diventare un mismatch.
        d = os.path.join(self.root, "data")
        os.makedirs(d)
        with open(marker_path(d), "w", encoding="utf-8") as f:
            f.write("  mainnet \n\n")
        claim_data_dir(d, "mainnet")


class TestCollectorSiRifiutaDiPartire(unittest.TestCase):
    """La guardia deve stare prima di WriterPool e GapRecorder, non dopo.

    Testare solo netguard non basta: il bug realistico e' chiamarla troppo
    tardi, quando i writer hanno gia' creato file nella directory sbagliata.
    """

    def test_costruttore_alza_prima_di_costruire_i_writer(self) -> None:
        from collector import collector as mod

        with tempfile.TemporaryDirectory() as root:
            d = os.path.join(root, "data")
            claim_data_dir(d, "testnet")

            cfg = {
                "network": "mainnet",
                "data_dir": d,
                "coins": ["BTC"],
                "per_coin_channels": {"trades": True},
                "global_channels": {"allMids": True},
                "user_address": None,
                "user_channels": {},
                "writer": {
                    "flush_rows": 5000,
                    "flush_seconds": 60,
                    "compression": "zstd",
                },
                "gaps_file": None,
            }
            # Non basta guardare il filesystem: GapRecorder crea il file solo
            # al primo gap, quindi una guardia messa dopo di lui lascerebbe la
            # directory pulita lo stesso e il test passerebbe per caso. Quello
            # che va verificato e' che i writer non vengano proprio costruiti.
            with mock.patch.object(mod, "WriterPool") as writer_pool, \
                    mock.patch.object(mod, "GapRecorder") as gap_recorder:
                with self.assertRaises(NetworkMismatchError):
                    mod.Collector(cfg)

            writer_pool.assert_not_called()
            gap_recorder.assert_not_called()
            self.assertEqual(sorted(os.listdir(d)), [MARKER_NAME])


if __name__ == "__main__":
    unittest.main()
