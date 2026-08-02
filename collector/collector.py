"""
Collector WebSocket Hyperliquid.

Perche' non l'SDK ufficiale: il suo WebsocketManager e' threaded, tiene un
dizionario di sottoscrizioni che cresce indefinitamente e non ha una politica
di riconnessione adatta a un processo che deve stare su per mesi. Per il
COLLECTOR conviene parlare direttamente col websocket. L'SDK lo userai per
il lato esecuzione, dove firma e nonce sono la parte difficile.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import signal
import time

import websockets
import yaml

from .acks import ack_matches, sub_key
from .backfill import backfill
from .gaps import GapRecorder
from .netguard import NetworkMismatchError, claim_data_dir
from .parsing import coin_of, exch_ts_of, truncate_book
from .writer import WriterPool

# Ri-esportati: erano definiti qui prima di essere isolati in parsing.py e
# acks.py, e restano importabili da questo modulo per non rompere chi li
# importava.
__all__ = ["Collector", "build_subscriptions", "ack_matches", "sub_key",
           "coin_of", "exch_ts_of", "truncate_book", "main"]

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


class Collector:
    def __init__(self, cfg: dict):
        # Prima di ogni altra cosa: WriterPool e GapRecorder creano directory e
        # file dentro data_dir gia' in fase di costruzione. Se la rete non
        # coincide dobbiamo fermarci prima che qualcuno tocchi il disco.
        claim_data_dir(cfg["data_dir"], cfg["network"])
        self.cfg = cfg
        self.url = WS_URL[cfg["network"]]
        self.subs = build_subscriptions(cfg)
        self.writer = WriterPool(
            cfg["data_dir"],
            flush_rows=cfg["writer"]["flush_rows"],
            flush_seconds=cfg["writer"]["flush_seconds"],
            compression=cfg["writer"]["compression"],
        )
        self.gaps = GapRecorder(
            cfg.get("gaps_file") or os.path.join(cfg["data_dir"], "_gaps.jsonl")
        )
        self.last_msg_at = 0.0
        self.last_by_channel: dict[str, float] = {}
        self.msg_count = 0
        self.stop = asyncio.Event()
        self._ws = None
        self._close_reason: str | None = None
        self._acked: set[str] = set()
        self._tasks: set[asyncio.Task] = set()

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
        # Da qui in poi non stiamo piu' raccogliendo: e' un buco a tutti gli
        # effetti. Resta aperto nel registro e verra' chiuso dal prossimo avvio.
        self.gaps.mark_disconnected("shutdown", self._subscribed_channels())
        log.info("stop pulito, righe scritte in sessione: %d", self.writer.rows_written)

    def _subscribed_channels(self) -> list[str]:
        return sorted({s["type"] for s in self.subs})

    async def _ws_loop(self) -> None:
        delay = 1.0
        first = True
        while not self.stop.is_set():
            try:
                async with websockets.connect(
                    self.url, ping_interval=None, max_size=None, close_timeout=5
                ) as ws:
                    self._ws = ws
                    self._acked = set()
                    log.info("connesso a %s", self.url)
                    for s in self.subs:
                        await ws.send(json.dumps({"method": "subscribe", "subscription": s}))
                    log.info("%d sottoscrizioni inviate", len(self.subs))

                    closed = self.gaps.mark_connected()
                    if closed is not None:
                        log.warning("buco chiuso: %.1fs senza dati (%s)",
                                    closed.duration_s or 0.0, closed.reason)
                    if not first:
                        # Ricuci il buco: le candele e il funding sono
                        # ricostruibili via REST, i trade tick-by-tick no.
                        self._spawn(self._backfill())
                    first = False
                    delay = 1.0
                    self.last_msg_at = time.monotonic()

                    ping = asyncio.create_task(self._ping(ws))
                    audit = asyncio.create_task(self._audit_subscriptions())
                    try:
                        async for raw in ws:
                            self._handle(raw)
                    finally:
                        ping.cancel()
                        audit.cancel()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - il collector non deve mai morire
                log.warning("ws caduto (%s: %s), riconnetto tra %.1fs",
                            type(e).__name__, e, delay)
                self.gaps.mark_disconnected(f"{type(e).__name__}: {e}",
                                            self._subscribed_channels())
            else:
                # Uscita dall'`async for` senza eccezione: chiusura pulita, dal
                # server o dal watchdog. E' comunque un buco.
                self.gaps.mark_disconnected(self._close_reason or "closed by server",
                                            self._subscribed_channels())
            finally:
                self._ws = None
                self._close_reason = None
            if not self.stop.is_set():
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)

    def _spawn(self, coro) -> None:
        """asyncio tiene solo una reference debole ai task: senza conservarla,
        un task puo' sparire a meta' esecuzione per garbage collection."""
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _audit_subscriptions(self) -> None:
        """Una subscribe rifiutata non interrompe niente: il collector resta su,
        e quel canale semplicemente non arriva mai. Senza questo controllo te ne
        accorgi settimane dopo, guardando una cartella vuota."""
        await asyncio.sleep(self.cfg["watchdog"].get("subscription_ack_seconds", 15))
        missing = [sub_key(s) for s in self.subs if sub_key(s) not in self._acked]
        if missing:
            log.error("%d sottoscrizioni senza ack: %s", len(missing), missing)
        else:
            log.info("tutte le %d sottoscrizioni confermate", len(self.subs))

    def _record_ack(self, echo: dict) -> None:
        """L'eco del server non e' identica alla subscribe inviata (aggiunge i
        parametri opzionali al loro default), quindi va ricondotta alla
        subscribe di partenza invece che confrontata per uguaglianza."""
        matched = [s for s in self.subs if ack_matches(s, echo)]
        if not matched:
            # Non e' un errore fatale, ma vuol dire che non sappiamo piu' leggere
            # gli ack: senza questa riga il difetto tornerebbe invisibile.
            log.warning("ack non riconducibile a nessuna subscribe inviata: %s", echo)
            return
        for s in matched:
            self._acked.add(sub_key(s))

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
            if channel == "subscriptionResponse":
                # L'ack rimanda indietro {"method": .., "subscription": {..}}.
                data = msg.get("data") or {}
                sub = data.get("subscription") if isinstance(data, dict) else None
                if isinstance(sub, dict) and data.get("method") != "unsubscribe":
                    self._record_ack(sub)
            elif channel == "error":
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
                # Loggarlo non basta: va chiusa la socket, cosi' `async for`
                # termina e `_ws_loop` riconnette da capo.
                log.error("silenzio da %.0fs: chiudo la connessione e riconnetto",
                          now - self.last_msg_at)
                self.last_msg_at = now
                ws = self._ws
                self._close_reason = f"watchdog: {w['global_silence_seconds']}s di silenzio"
                if ws is not None:
                    with contextlib.suppress(Exception):
                        await ws.close()
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


DEFAULT_CONFIG = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.yaml")


def load_config(path: str | None = None) -> dict:
    """Ordine: argomento esplicito, env HL_CONFIG, config.yaml accanto al
    package. Prima veniva letto dalla cwd: sotto systemd, con la WorkingDirectory
    sbagliata, il collector partiva con un FileNotFoundError invece che con i
    default sicuri."""
    path = path or os.environ.get("HL_CONFIG") or DEFAULT_CONFIG
    with open(path) as f:
        cfg = yaml.safe_load(f)
    if cfg.get("network") not in WS_URL:
        raise ValueError(f"network non valido in {path}: {cfg.get('network')!r}")
    return cfg


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m collector",
        description="Collector market data Hyperliquid.",
    )
    p.add_argument(
        "--config",
        metavar="PERCORSO",
        default=None,
        help="file di configurazione (default: config.yaml accanto al package, "
        "o $HL_CONFIG se definita)",
    )
    # Forma posizionale deprecata: l'unit systemd in produzione passa ancora il
    # config cosi'. Toglierla romperebbe il collector al primo restart, che e'
    # esattamente il momento in cui nessuno sta guardando.
    p.add_argument("config_pos", nargs="?", default=None, help=argparse.SUPPRESS)
    return p.parse_args(argv)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    args = parse_args()
    cfg = load_config(args.config or args.config_pos)
    log.info("network=%s data_dir=%s", cfg["network"], cfg["data_dir"])

    try:
        c = Collector(cfg)
    except NetworkMismatchError as e:
        # Un traceback qui non aggiunge niente: il messaggio dice gia' cosa
        # fare. Exit non-zero perche' systemd deve registrarlo come fallimento.
        log.error("%s", e)
        raise SystemExit(2) from None
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, c.stop.set)
    loop.run_until_complete(c.run())


if __name__ == "__main__":
    main()
