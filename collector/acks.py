"""
Riconoscimento degli ack di sottoscrizione (`subscriptionResponse`).

Modulo separato per la stessa ragione di `parsing.py`: e' logica pura, senza
websockets ne' pyarrow, quindi i test girano ovunque.

Il punto delicato e' che l'eco del server **non e' identica** alla subscribe
inviata. Su testnet, catturato il 2026-08-02:

    inviato:  {"type":"l2Book","coin":"BTC"}
    eco:      {"type":"l2Book","coin":"BTC","nSigFigs":null,"mantissa":null,"fast":false}

    inviato:  {"type":"trades","coin":"BTC"}
    eco:      {"type":"trades","coin":"BTC"}

Il server completa l'eco con i parametri opzionali del canale, valorizzati al
loro default. Un confronto per uguaglianza esatta funziona per `trades` e
fallisce per `l2Book`, che e' esattamente il falso positivo previsto nella
sezione F4 di REVISIONE-COLLECTOR.md.
"""

from __future__ import annotations

from typing import Any


def sub_key(sub: dict) -> str:
    """Chiave stabile di una subscribe *inviata*, usata per identificarla nei
    log e nel set degli ack. Non e' una chiave di confronto con l'eco: per
    quello serve `ack_matches`, perche' l'eco ha campi in piu'."""
    return "|".join(f"{k}={sub[k]}" for k in sorted(sub))


def _norm(value: Any) -> Any:
    """I confronti fra stringhe sono case-insensitive: l'exchange normalizza gli
    indirizzi (il `user` dei canali utente arriva in minuscolo anche se inviato
    in checksum case). Coin e tipi di canale non hanno varianti che si
    distinguano solo per il maiuscolo, quindi non si perde risoluzione."""
    if isinstance(value, str):
        return value.casefold()
    return value


def _is_default_fill_in(value: Any) -> bool:
    """Un campo presente solo nell'eco e' accettato solo se vale il suo default
    (`null` o `false`), cioe' se il server lo ha aggiunto perche' non l'avevamo
    specificato. Un campo aggiuntivo con un valore vero descrive una
    sottoscrizione *diversa* da quella chiesta, e non deve contare come ack."""
    return value is None or value is False


def ack_matches(sent: dict, echo: dict) -> bool:
    """True se `echo` (il campo `data.subscription` di un `subscriptionResponse`)
    conferma la subscribe `sent`.

    Regole, in ordine di importanza:

    1. ogni campo inviato deve comparire nell'eco con lo stesso valore
       (normalizzato): e' quello che impedisce a un ack di BTC di confermare
       una subscribe di ETH;
    2. i campi presenti solo nell'eco sono tollerati **solo al valore di
       default**: sono i parametri opzionali che il server compila da se'.

    Il confronto non e' mai piu' permissivo di cosi': se il server non risponde,
    o risponde per un'altra subscribe, non c'e' nessun match e la sottoscrizione
    resta senza ack.
    """
    if not isinstance(echo, dict):
        return False
    for key, value in sent.items():
        if key not in echo or _norm(echo[key]) != _norm(value):
            return False
    for key, value in echo.items():
        if key not in sent and not _is_default_fill_in(value):
            return False
    return True
