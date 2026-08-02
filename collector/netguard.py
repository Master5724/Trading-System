"""
Guardia rete/directory.

Un --config sbagliato scriverebbe dati mainnet dentro il dataset testnet (o
viceversa) in modo silenzioso: gli schemi sono identici, il collector gira, i
parquet si accumulano e nessun record porta con se' la rete di provenienza.
Il danno emergerebbe settimane dopo, a raccolta ormai mescolata e non piu'
separabile. Meglio un servizio che non parte.

Il marcatore e' un file di testo perche' deve restare leggibile con `cat` da
chi sta debuggando un servizio che rifiuta di avviarsi alle tre di notte.
"""

from __future__ import annotations

import os

MARKER_NAME = ".network"


class NetworkMismatchError(RuntimeError):
    """La data_dir e' gia' occupata dal dataset di un'altra rete."""


def marker_path(data_dir: str) -> str:
    return os.path.join(data_dir, MARKER_NAME)


def claim_data_dir(data_dir: str, network: str) -> str:
    """Verifica che `data_dir` appartenga a `network`, e la marca se e' nuova.

    Ritorna il percorso del marcatore. Solleva NetworkMismatchError se la
    directory e' gia' di un'altra rete: in quel caso non viene scritto nulla,
    nemmeno la directory stessa.
    """
    path = marker_path(data_dir)
    try:
        with open(path, encoding="utf-8") as f:
            found: str | None = f.read().strip()
    except FileNotFoundError:
        found = None

    if found is not None:
        if not found:
            raise NetworkMismatchError(
                f"marcatore di rete vuoto o troncato: {path}. Impossibile "
                f"stabilire a quale rete appartengono i dati gia' presenti in "
                f"{data_dir}. Il collector non parte: verifica il contenuto "
                f"della directory e riscrivi il marcatore a mano con la rete "
                f"corretta."
            )
        if found != network:
            raise NetworkMismatchError(
                f"conflitto di rete su {data_dir}: la directory contiene dati "
                f"di '{found}', ma il config chiede '{network}'. Il collector "
                f"non parte per non mescolare i due dataset. Correggi data_dir "
                f"nel config, oppure cancella {path} se sei certo che quella "
                f"directory non contenga dati di '{found}'."
            )
        return path

    # Il marcatore nasce solo dopo il controllo, mai prima: cosi' un avvio
    # sbagliato su una directory altrui non lascia traccia.
    os.makedirs(data_dir, exist_ok=True)
    # Scrittura atomica: un marcatore troncato da un kill a meta' write
    # bloccherebbe ogni avvio successivo, che e' il fallimento peggiore per un
    # controllo che dovrebbe solo proteggere.
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(network + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return path
