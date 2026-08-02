"""
Lock di istanza sulla data_dir.

La guardia .network impedisce di mescolare due reti nella stessa directory, ma
non dice niente su due processi con lo *stesso* config: entrambi trovano il
marcatore giusto, entrambi partono, entrambi si sottoscrivono agli stessi
canali e scrivono negli stessi parquet. Il risultato non e' un crash ma righe
duplicate, che e' peggio: il collector sembra sano e il dataset e' rotto.

Il lock e' un flock advisory del kernel, non un PID file. La differenza conta
per un processo che deve stare su per settimane: il lock del kernel viene
rilasciato quando il file descriptor si chiude, e questo succede sempre, anche
con SIGKILL, anche con OOM killer, anche con un reboot sporco. Un PID file
sopravvive alla morte del processo e al riavvio successivo tocca a un umano
decidere se e' orfano - esattamente il lavoro che nessuno fa alle tre di notte.

flock e non fcntl.lockf perche' i lock POSIX di lockf sono per-processo: due
open() nello stesso processo non si bloccherebbero a vicenda, e i test non
potrebbero verificare niente senza fare fork.
"""

from __future__ import annotations

import fcntl
import os

LOCK_NAME = ".lock"


class DataDirLockedError(RuntimeError):
    """La data_dir e' gia' in uso da un'altra istanza del collector."""


def lock_path(data_dir: str) -> str:
    return os.path.join(data_dir, LOCK_NAME)


def _holder_pid(fd: int) -> str | None:
    """PID scritto dal detentore, solo per il messaggio d'errore.

    Non entra mai nella decisione di acquisire o no: quella la prende il
    kernel. Se il contenuto e' illeggibile o non e' un numero (file appena
    creato, detentore ucciso prima di scriverlo) si rinuncia e basta.
    """
    try:
        raw = os.pread(fd, 64, 0).decode("utf-8", "replace").strip()
    except OSError:
        return None
    return raw if raw.isdigit() else None


class DataDirLock:
    """Lock esclusivo su `<data_dir>/.lock`, tenuto per la vita del processo.

    L'oggetto va conservato: se viene raccolto dal garbage collector il file
    descriptor si chiude e il lock sparisce senza che nessuno se ne accorga.
    """

    def __init__(self, data_dir: str) -> None:
        self.data_dir = data_dir
        self.path = lock_path(data_dir)
        self._fd: int | None = None

    def acquire(self) -> None:
        """Prende il lock o solleva DataDirLockedError senza scrivere nulla."""
        if self._fd is not None:
            return
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            pid = _holder_pid(fd)
            os.close(fd)
            chi = f" dal processo PID {pid}" if pid else ""
            raise DataDirLockedError(
                f"un'altra istanza del collector sta gia' scrivendo in "
                f"{self.data_dir}: il lock {self.path} e' tenuto{chi}. Il "
                f"collector non parte perche' due istanze sulla stessa "
                f"directory duplicherebbero ogni riga negli stessi parquet. "
                f"Ferma l'istanza attiva (`systemctl stop hl-collector`), "
                f"oppure fai partire questa su una data_dir diversa nel config."
            ) from None

        # Il PID e' diagnostica per chi legge il journal, non uno stato su cui
        # qualcuno decide: si scrive solo dopo aver gia' vinto il lock.
        os.ftruncate(fd, 0)
        os.pwrite(fd, f"{os.getpid()}\n".encode(), 0)
        self._fd = fd

    def release(self) -> None:
        """Rilascia esplicitamente. Non serve all'uscita del processo (ci pensa
        il kernel), serve ai test e a chi riusa l'oggetto."""
        fd, self._fd = self._fd, None
        if fd is None:
            return
        # Il file non viene cancellato: unlink di un lock file e' una corsa
        # classica (un altro processo puo' tenere aperto l'inode ormai
        # scollegato e credere di avere il lock su un file che non esiste piu').
        # Un .lock da un byte che resta li' non da' fastidio a nessuno.
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    def __enter__(self) -> DataDirLock:
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()
