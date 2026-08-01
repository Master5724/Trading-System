"""
Ricucitura dei buchi via REST /info.

Il WebSocket non ti da' numeri di sequenza sui dati di mercato: non puoi
sapere se hai perso un messaggio. Quello che puoi fare e' ricostruire, dopo
ogni riconnessione, le serie che l'API espone anche in modo storico:

  candleSnapshot  -> OHLCV, ricostruibile
  fundingHistory  -> funding orario, ricostruibile   <-- il dato che conta per lo swing

I trade tick-by-tick e gli snapshot del book NON sono ricostruibili. Il buco
resta, e il modo giusto di gestirlo e' segnarlo: tieni traccia delle finestre
di disconnessione ed escludile dal backtest, invece di far finta che
quell'ora esista.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request

INFO_URL = {
    "mainnet": "https://api.hyperliquid.xyz/info",
    "testnet": "https://api.hyperliquid-testnet.xyz/info",
}

log = logging.getLogger("backfill")


def _post(url: str, body: dict, timeout: int = 15):
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def backfill(cfg: dict, writer) -> None:
    url = INFO_URL[cfg["network"]]
    bf = cfg["backfill"]
    now_ms = int(time.time() * 1000)

    for coin in cfg["coins"]:
        try:
            candles = _post(url, {
                "type": "candleSnapshot",
                "req": {
                    "coin": coin,
                    "interval": bf["candle_interval"],
                    "startTime": now_ms - bf["candle_lookback_hours"] * 3600_000,
                    "endTime": now_ms,
                },
            })
            writer.add("backfill_candle", coin, now_ms, candles)

            funding = _post(url, {
                "type": "fundingHistory",
                "coin": coin,
                "startTime": now_ms - bf["funding_lookback_hours"] * 3600_000,
                "endTime": now_ms,
            })
            writer.add("backfill_funding", coin, now_ms, funding)
            log.info("backfill %s ok", coin)
        except Exception as e:  # noqa: BLE001
            log.warning("backfill %s fallito: %s", coin, e)

    writer.flush_all()
