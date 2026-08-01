"""
Estrazione di coin e timestamp dai payload WebSocket, e troncamento del book.

Vive in un modulo separato da `collector.py` per una ragione precisa: sono le
uniche funzioni del collector che decidono *sotto quale chiave finisce un dato
sul disco*. Un errore qui non produce un'eccezione, produce mesi di dati
archiviati sotto la coin sbagliata o con `ts_exch_ms = 0`. Isolate qui sono
testabili senza websockets ne' pyarrow, quindi i test girano ovunque.

Riferimento: Hyperliquid docs, "WebSocket / Subscriptions". La forma di ogni
payload e' annotata sul ramo che la gestisce.
"""

from __future__ import annotations

# Canali che portano la coin in un campo `coin` di primo livello.
# activeSpotAssetCtx non si sottoscrive: e' il channel che l'exchange
# RESTITUISCE quando `activeAssetCtx` viene chiesto su un asset spot (@107 e
# simili). Senza questa voce quei messaggi finirebbero in data/_global.
_COIN_FIELD_CHANNELS = frozenset(
    {"l2Book", "bbo", "activeAssetCtx", "activeSpotAssetCtx"}
)

# Canali utente: un singolo messaggio puo' contenere piu' coin (i fill di un
# batch, gli aggiornamenti di ordini su asset diversi). Non hanno una coin
# sola, quindi restano globali e la coin si estrae a valle dal raw.
_USER_CHANNELS = frozenset(
    {"userFills", "userFundings", "orderUpdates", "user", "userNonFundingLedgerUpdates"}
)


def coin_of(channel: str, data) -> str:
    """Estrae il simbolo dal payload. Ogni canale lo mette in un posto diverso.

    Ritorna "" per i canali globali (allMids) e utente: e' un valore legittimo,
    non un errore, e il writer lo mappa sulla partizione `_global`.
    """
    if channel in _COIN_FIELD_CHANNELS:
        # {"coin": "BTC", "time": ..., "levels": [...]}  /  {"coin": ..., "ctx": {...}}
        return data.get("coin", "") if isinstance(data, dict) else ""

    if channel == "trades":
        # WsTrade[]: tutti gli elementi appartengono alla coin sottoscritta.
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return data[0].get("coin", "")
        return ""

    if channel == "candle":
        # Candle usa chiavi corte: s = symbol, t = open millis, T = close millis.
        # I doc dichiarano Candle[], il server in pratica invia un singolo
        # oggetto: gestiamo entrambe le forme invece di scommettere.
        if isinstance(data, dict):
            return data.get("s", "")
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return data[0].get("s", "")
        return ""

    # allMids, canali utente, e qualunque canale non previsto.
    return ""


def exch_ts_of(channel: str, data) -> int:
    """Timestamp dichiarato dall'exchange, in ms. 0 se il canale non lo espone.

    0 non e' un fallback difensivo: allMids e activeAssetCtx non contengono
    nessun timestamp nel payload, quindi per quei canali l'unico riferimento
    temporale possibile e' `ts_local_ns` scritto dal writer.
    """
    try:
        if channel in ("l2Book", "bbo"):
            # bbo espone `time` esattamente come l2Book. Prima veniva ignorato.
            return int(data.get("time", 0))

        if channel == "trades":
            return int(data[0].get("time", 0)) if data else 0

        if channel == "candle":
            # `t` (apertura) e non `T` (chiusura): la stessa candela viene
            # ripubblicata a ogni aggiornamento, e `t` e' l'unica chiave stabile
            # che identifica la barra. Conseguenza da tenere a mente a valle:
            # ts_exch_ms si ripete su piu' righe dello stesso minuto.
            d = data[0] if isinstance(data, list) and data else data
            return int(d.get("t", 0))

        if channel == "userFills":
            # {"isSnapshot": true, "user": "0x..", "fills": [WsFill]}
            fills = data.get("fills") or []
            return int(fills[0].get("time", 0)) if fills else 0

        if channel == "userFundings":
            # {"isSnapshot": true, "user": "0x..", "fundings": [{"time": ..}]}
            fundings = data.get("fundings") or []
            return int(fundings[0].get("time", 0)) if fundings else 0

        if channel == "orderUpdates":
            # WsOrder[]: {"order": {...}, "status": .., "statusTimestamp": ..}
            return int(data[0].get("statusTimestamp", 0)) if data else 0

        if channel == "user":
            # userEvents risponde sul channel "user", non "userEvents".
            # Union type: solo il ramo `fills` porta un timestamp usabile.
            fills = data.get("fills") or [] if isinstance(data, dict) else []
            return int(fills[0].get("time", 0)) if fills else 0
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return 0
    return 0


def truncate_book(data: dict, depth: int) -> dict:
    """l2Book e' uno SNAPSHOT completo a ogni push. Tagliare la profondita'
    riduce lo storage di un ordine di grandezza senza perdere nulla di utile
    per strategie che non fanno market making.

    Non muta l'input: il chiamante puo' ancora loggare il payload originale.
    """
    if not isinstance(data, dict):
        return data
    levels = data.get("levels")
    if isinstance(levels, list) and len(levels) == 2:
        data = dict(data)
        data["levels"] = [levels[0][:depth], levels[1][:depth]]
    return data


def is_snapshot(data) -> bool:
    """True se il messaggio e' lo snapshot storico che l'exchange invia in coda
    alla subscribe dei canali utente. Arriva di nuovo a ogni riconnessione:
    chi legge lo storico deve deduplicare, non sommare."""
    return isinstance(data, dict) and bool(data.get("isSnapshot"))
