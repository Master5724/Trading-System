"""
Collector WebSocket Hyperliquid.

Perche' non l'SDK ufficiale: il suo WebsocketManager e' threaded, tiene un
dizionario di sottoscrizioni che cresce indefinitamente e non ha una politica
di riconnessione adatta a un processo che deve stare su per mesi. Per il
COLLECTOR conviene parlare direttamente col websocket. L'SDK lo userai per
il lato esecuzione, dove firma e nonce sono la parte difficile.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import signal
import time

import websockets
import yaml

from backfill import backfill
from writer import WriterPool

WS_URL = {
    "mainnet": "wss://api.hyperliquid.xyz/ws",
    "testnet": "wss://api.hyperliquid-testnet.xyz/ws",
}

log = logging.getLogger("collector")


def build_subscriptions(cfg: dict) -> list[dict]:
    subs: list[dict] = []
    pc = cfg["per_coin_channels"]
    for coin in cfg["coins"]:
        if pc.get("trades"):
            subs.append({"type": "trades", "coin": coin})
        if pc.get("l2Book"):
            subs.append({"type": "l2Book", "coin": coin})
        if pc.get("bbo"):
            subs.append({"type": "bbo", "coin": coin})
        if pc.get("activeAssetCtx"):
            subs.append({"type": "activeAssetCtx", "coin": coin})
        if pc.get("candle"):
            subs.append({"type": "candle", "coin": coin, "interval": pc["candle"]})
    if cfg["global_channels"].get("allMids"):
        subs.append({"type": "allMids"})
    user = cfg.get("user_address")
    if user:
        for ch, on in cfg["user_channels"].items():
            if on:
                subs.append({"type": ch, "user": user})
    return subs


def coin_of(channel: str, data) -> str:
    """Estrae il simbolo dal payload. Ogni canale lo mette in un posto diverso."""
    if channel in ("l2Book", "bbo", "activeAssetCtx"):
        return data.get("coin", "") if isinstance(data, dict) else ""
    if channel == "trades":
        if isinstance(data, list) and data:
            return data[0].get("coin", "")
        return ""
    if channel == "candle":
        # le candele usano chiavi corte: s = symbol, t = open time
        if isinstance(data, dict):
            return data.get("s", "")
        if isinstance(data, list) and data:
            return data[0].get("s", "")
    return ""


def exch_ts_of(channel: str, data) -> int:
    """Timestamp dichiarato dall'exchange, in ms. 0 se il canale non lo espone."""
    try:
        if channel == "l2Book":
            return int(data.get("time", 0))
        if channel == "trades":
            return int(data[0].get("time", 0)) if data else 0
        if channel == "candle":
            d = data[0] if isinstance(data, list) else data
            return int(d.get("t", 0))
        if channel == "userFills":
            fills = data.get("fills") or []
            return int(fills[0].get("time", 0)) if fills else 0
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return 0
    return 0


def truncate_book(data: dict, depth: int) -> dict:
    """l2Book e' uno SNAPSHOT completo a ogni push. Tagliare la profondita'
    riduce lo storage di un ordine di grandezza senza perdere nulla di utile
    per strategie che non fanno market making."""
    levels = data.get("levels")
    if isinstance(levels, list) and len(levels) == 2:
        data = dict(data)
        data["levels"] = [levels[0][:depth], levels[1][:depth]]
    return data


class Collector:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.url = WS_URL[cfg["network"]]
        self.subs = build_subscriptions(cfg)
        self.writer = WriterPool(
            cfg["data_dir"],
            flush_rows=cfg["writer"]["flush_rows"],
            flush_seconds=cfg["writer"]["flush_seconds"],
            compression=cfg["writer"]["compression"],
        )
        self.last_msg_at = 0.0
        self.last_by_channel: dict[str, float] = {}
        self.msg_count = 0
        self.stop = asyncio.Event()

    async def run(self) -> None:
        tasks = [
            asyncio.create_task(self._ws_loop()),
            asyncio.create_task(self._watchdog()),
            asyncio.create_task(self._periodic_flush()),
        ]
        await self.stop.wait()
        for t in tasks:
            t.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.gather(*tasks, return_exceptions=True)
        self.writer.flush_all()
        log.info("stop pulito, righe scritte in sessione: %d", self.writer.rows_written)

    async def _ws_loop(self) -> None:
        delay = 1.0
        first = True
        while not self.stop.is_set():
            try:
                async with websockets.connect(
                    self.url, ping_interval=None, max_size=None, close_timeout=5
                ) as ws:
                    log.info("connesso a %s", self.url)
                    for s in self.subs:
                        await ws.send(json.dumps({"method": "subscribe", "subscription": s}))
                    log.info("%d sottoscrizioni inviate", len(self.subs))

                    if not first:
                        # Ricuci il buco: le candele e il funding sono
                        # ricostruibili via REST, i trade tick-by-tick no.
                        asyncio.create_task(self._backfill())
                    first = False
                    delay = 1.0
                    self.last_msg_at = time.monotonic()

                    ping = asyncio.create_task(self._ping(ws))
                    try:
                        async for raw in ws:
                            self._handle(raw)
                    finally:
                        ping.cancel()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - il collector non deve mai morire
                log.warning("ws caduto (%s: %s), riconnetto tra %.1fs",
                            type(e).__name__, e, delay)
            if not self.stop.is_set():
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)

    async def _ping(self, ws) -> None:
        interval = self.cfg["watchdog"]["ping_seconds"]
        while True:
            await asyncio.sleep(interval)
            with contextlib.suppress(Exception):
                await ws.send(json.dumps({"method": "ping"}))

    def _handle(self, raw: str) -> None:
        now = time.monotonic()
        self.last_msg_at = now
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("messaggio non-JSON scartato")
            return

        channel = msg.get("channel", "")
        if channel in ("pong", "subscriptionResponse", "error"):
            if channel == "error":
                log.error("errore dall'exchange: %s", msg.get("data"))
            return

        data = msg.get("data")
        self.last_by_channel[channel] = now
        self.msg_count += 1

        if channel == "l2Book" and isinstance(data, dict):
            data = truncate_book(data, self.cfg["l2_depth"])

        self.writer.add(channel, coin_of(channel, data), exch_ts_of(channel, data), data)

    async def _watchdog(self) -> None:
        w = self.cfg["watchdog"]
        while True:
            await asyncio.sleep(10)
            now = time.monotonic()
            if self.last_msg_at and now - self.last_msg_at > w["global_silence_seconds"]:
                # Silenzio totale = connessione zombie. Capita, e senza questo
                # controllo il processo resta su a non fare niente per ore.
                log.error("silenzio da %.0fs: la connessione e' morta senza chiudersi",
                          now - self.last_msg_at)
                self.last_msg_at = now
            for ch, ts in list(self.last_by_channel.items()):
                if now - ts > w["stale_warn_seconds"]:
                    log.warning("canale %s fermo da %.0fs", ch, now - ts)

    async def _periodic_flush(self) -> None:
        while True:
            await asyncio.sleep(self.cfg["writer"]["flush_seconds"])
            self.writer.flush_all()

    async def _backfill(self) -> None:
        with contextlib.suppress(Exception):
            await asyncio.to_thread(backfill, self.cfg, self.writer)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)

    c = Collector(cfg)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, c.stop.set)
    loop.run_until_complete(c.run())


if __name__ == "__main__":
    main()
