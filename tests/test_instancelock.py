"""
Test del lock di istanza sulla data_dir.

Il caso che conta e' il secondo: due processi con lo stesso config passano
entrambi la guardia .network e scrivono righe duplicate negli stessi parquet,
senza che nessuno dei due si lamenti. Il quinto e' l'altra meta' del problema:
un lock che protegge dalla doppia istanza ma resta orfano dopo un kill
trasformerebbe un crash notturno in un collector che non riparte piu'.

Ogni test lavora in una directory temporanea: le data_dir di produzione non
vengono mai toccate.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collector import instancelock
from collector.instancelock import (
    LOCK_NAME,
    DataDirLock,
    DataDirLockedError,
    lock_path,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Figlio che prende il lock e resta appeso finche' non lo si ammazza. Stampa
# una riga per dire al padre che il lock e' preso davvero: senza la conferma il
# test diventerebbe una corsa contro l'avvio dell'interprete.
CHILD = """
import sys, time
from collector.instancelock import DataDirLock
lock = DataDirLock(sys.argv[1])
lock.acquire()
print("locked", flush=True)
time.sleep(300)
"""


class TestDataDirLock(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def _data_dir(self, name: str = "data") -> str:
        d = os.path.join(self.root, name)
        os.makedirs(d, exist_ok=True)
        return d

    def _spawn_holder(self, data_dir: str) -> subprocess.Popen:
        env = dict(os.environ, PYTHONPATH=REPO_ROOT)
        p = subprocess.Popen(
            [sys.executable, "-c", CHILD, data_dir],
            stdout=subprocess.PIPE, text=True, env=env,
        )
        self.addCleanup(self._kill, p)
        line = p.stdout.readline()
        self.assertEqual(line.strip(), "locked", "il figlio non ha preso il lock")
        return p

    @staticmethod
    def _kill(p: subprocess.Popen) -> None:
        if p.poll() is None:
            p.kill()
            p.wait(timeout=10)

    # --- primo processo -----------------------------------------------------

    def test_primo_acquisisce(self) -> None:
        d = self._data_dir()
        lock = DataDirLock(d)
        lock.acquire()
        self.addCleanup(lock.release)
        self.assertTrue(os.path.exists(lock_path(d)))

    def test_il_lock_registra_il_pid_del_detentore(self) -> None:
        # Diagnostica, non stato: serve a chi legge il journal per capire chi
        # sta tenendo la directory.
        d = self._data_dir()
        with DataDirLock(d):
            with open(lock_path(d), encoding="utf-8") as f:
                self.assertEqual(f.read().strip(), str(os.getpid()))

    def test_acquire_ripetuta_sullo_stesso_oggetto_e_idempotente(self) -> None:
        d = self._data_dir()
        lock = DataDirLock(d)
        lock.acquire()
        self.addCleanup(lock.release)
        lock.acquire()  # non deve ne' fallire ne' perdere il fd precedente

    # --- secondo processo ---------------------------------------------------

    def test_secondo_fallisce_sulla_stessa_data_dir(self) -> None:
        d = self._data_dir()
        primo = DataDirLock(d)
        primo.acquire()
        self.addCleanup(primo.release)

        with self.assertRaises(DataDirLockedError) as ctx:
            DataDirLock(d).acquire()

        msg = str(ctx.exception)
        # Chi legge l'errore nel journal deve sapere quale directory e cosa
        # fare, senza aprire il codice.
        self.assertIn(d, msg)
        self.assertIn(LOCK_NAME, msg)
        self.assertIn(str(os.getpid()), msg)

    def test_secondo_non_scrive_nulla(self) -> None:
        d = self._data_dir()
        primo = DataDirLock(d)
        primo.acquire()
        self.addCleanup(primo.release)

        prima_elenco = sorted(os.listdir(d))
        with open(lock_path(d), "rb") as f:
            prima_contenuto = f.read()

        with self.assertRaises(DataDirLockedError):
            DataDirLock(d).acquire()

        self.assertEqual(sorted(os.listdir(d)), prima_elenco)
        self.assertEqual(prima_elenco, [LOCK_NAME])
        with open(lock_path(d), "rb") as f:
            # In particolare il PID del detentore deve restare il suo: un
            # secondo processo che sovrascrive il lock prima di scoprire di aver
            # perso renderebbe l'errore successivo bugiardo.
            self.assertEqual(f.read(), prima_contenuto)

    def test_secondo_processo_vero_fallisce(self) -> None:
        # Il flock e' per open file description: senza questo caso non sapremmo
        # se il lock funziona davvero tra processi distinti o solo nel test.
        d = self._data_dir()
        self._spawn_holder(d)
        with self.assertRaises(DataDirLockedError):
            DataDirLock(d).acquire()

    # --- rilascio e riavvio -------------------------------------------------

    def test_dopo_il_rilascio_un_nuovo_processo_acquisisce(self) -> None:
        d = self._data_dir()
        primo = DataDirLock(d)
        primo.acquire()
        primo.release()

        secondo = DataDirLock(d)
        secondo.acquire()  # il riavvio normale non deve trovare ostacoli
        self.addCleanup(secondo.release)

    def test_release_senza_acquire_non_esplode(self) -> None:
        DataDirLock(self._data_dir()).release()

    # --- il lock e' per dataset, non globale --------------------------------

    def test_due_data_dir_diverse_partono_entrambe(self) -> None:
        a, b = self._data_dir("testnet"), self._data_dir("mainnet")
        la, lb = DataDirLock(a), DataDirLock(b)
        la.acquire()
        self.addCleanup(la.release)
        lb.acquire()  # testnet e mainnet devono poter girare insieme
        self.addCleanup(lb.release)

    # --- morte brutale ------------------------------------------------------

    def test_kill_non_lascia_il_lock_orfano(self) -> None:
        d = self._data_dir()
        holder = self._spawn_holder(d)

        # Controprova: finche' e' vivo il lock deve reggere, altrimenti il test
        # dopo il kill non proverebbe niente.
        with self.assertRaises(DataDirLockedError):
            DataDirLock(d).acquire()

        holder.send_signal(signal.SIGKILL)
        holder.wait(timeout=10)

        # Il rilascio e' del kernel alla chiusura del fd, ma la reap del
        # processo non e' istantanea su ogni piattaforma: si riprova per un
        # attimo invece di affidarsi al tempismo.
        deadline = time.monotonic() + 5.0
        while True:
            lock = DataDirLock(d)
            try:
                lock.acquire()
            except DataDirLockedError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.05)
            else:
                self.addCleanup(lock.release)
                break

        # Il file resta sul disco: e' il lock del kernel a essere sparito, non
        # il file. Un riavvio non deve avere bisogno di cancellarlo a mano.
        self.assertTrue(os.path.exists(lock_path(d)))

    # --- controllo negativo -------------------------------------------------

    def test_senza_flock_il_doppio_acquire_passerebbe(self) -> None:
        """Prova che i test negativi sanno fallire.

        Se qualcuno svuotasse la chiamata a flock, `acquire` smetterebbe di
        proteggere ma continuerebbe a creare il file e a scrivere il PID: tutti
        i test "positivi" resterebbero verdi. Qui si disattiva il flock e si
        verifica che il doppio acquire diventa possibile - cioe' che i test
        sopra dipendono davvero dal lock e non dalla forma del file.
        """
        d = self._data_dir()
        primo = DataDirLock(d)
        primo.acquire()
        self.addCleanup(primo.release)

        with mock.patch.object(instancelock.fcntl, "flock", lambda *a: None):
            secondo = DataDirLock(d)
            secondo.acquire()  # con la logica disabilitata nessuno ferma nulla
            self.addCleanup(secondo.release)

        # E con il flock di nuovo attivo torna a fallire.
        with self.assertRaises(DataDirLockedError):
            DataDirLock(d).acquire()


class TestCollectorSiRifiutaDiPartire(unittest.TestCase):
    """Il lock deve stare prima dei writer e dopo la guardia di rete.

    Un lock preso troppo tardi lascerebbe alla seconda istanza il tempo di
    aprire i parquet e la socket, che e' il momento in cui i duplicati
    nascono.
    """

    def _cfg(self, data_dir: str) -> dict:
        return {
            "network": "testnet",
            "data_dir": data_dir,
            "coins": ["BTC"],
            "per_coin_channels": {"trades": True},
            "global_channels": {"allMids": True},
            "user_address": None,
            "user_channels": {},
            "writer": {"flush_rows": 5000, "flush_seconds": 60, "compression": "zstd"},
            "watchdog": {"ping_seconds": 30, "global_silence_seconds": 60,
                         "stale_warn_seconds": 120},
            "gaps_file": None,
        }

    def test_seconda_istanza_non_costruisce_i_writer(self) -> None:
        from collector import collector as mod

        with tempfile.TemporaryDirectory() as root:
            d = os.path.join(root, "data")
            cfg = self._cfg(d)

            primo = mod.Collector(cfg)
            self.addCleanup(primo._lock.release)

            with mock.patch.object(mod, "WriterPool") as writer_pool, \
                    mock.patch.object(mod, "GapRecorder") as gap_recorder:
                with self.assertRaises(DataDirLockedError):
                    mod.Collector(cfg)

            writer_pool.assert_not_called()
            gap_recorder.assert_not_called()

    def test_la_rete_sbagliata_ha_la_precedenza_sul_lock(self) -> None:
        # Un config puntato sulla directory dell'altra rete deve dare il
        # messaggio sulla rete, non quello sul lock: e' l'errore piu' grave dei
        # due e il primo da correggere.
        from collector import collector as mod
        from collector.netguard import NetworkMismatchError, claim_data_dir

        with tempfile.TemporaryDirectory() as root:
            d = os.path.join(root, "data")
            claim_data_dir(d, "mainnet")
            with self.assertRaises(NetworkMismatchError):
                mod.Collector(self._cfg(d))
            # E non deve aver lasciato un lock nella directory altrui.
            self.assertNotIn(LOCK_NAME, os.listdir(d))

    def test_main_esce_con_codice_2(self) -> None:
        # systemd deve vedere un fallimento, non un'uscita pulita: altrimenti
        # una seconda istanza avviata per sbaglio passa inosservata.
        from collector import collector as mod

        with tempfile.TemporaryDirectory() as root:
            d = os.path.join(root, "data")
            cfg = self._cfg(d)
            primo = mod.Collector(cfg)
            self.addCleanup(primo._lock.release)

            args = mock.Mock(config=None, config_pos=None)
            with mock.patch.object(mod, "parse_args", return_value=args), \
                    mock.patch.object(mod, "load_config", return_value=cfg), \
                    mock.patch.object(mod.asyncio, "new_event_loop") as loop:
                with self.assertRaises(SystemExit) as ctx:
                    mod.main()

            self.assertEqual(ctx.exception.code, 2)
            loop.assert_not_called()


if __name__ == "__main__":
    unittest.main()
