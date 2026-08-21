"""Dai parquet agli eventi, in ordine di timestamp e senza reimplementare nulla.

Tre vincoli del prompt diventano codice qui dentro.

**Sola lettura.** Il collector sta scrivendo in `data_dir` mentre questo modulo
legge: nessuna query di scrittura, `temp_directory` di DuckDB fuori dalla
directory dati (riuso di `catalog.dataset.connect` via `costs.sources.connect`).

**Trade deduplicati per `tid` dalla funzione del catalogo.** Si importa
`catalog.trades.dedup_sql`, non se ne scrive una seconda versione. La dedup
gira sui giorni della finestra **piu' un giorno per lato**, non sull'intera
partizione della coin: `dedup_sql` tiene la prima consegna osservata di ogni
`tid`, quindi il margine deve coprire la distanza fra una consegna e la sua
ritrasmissione, altrimenti quest'ultima verrebbe promossa a "prima consegna".

Il margine e' dimensionato su una misura, non su una sensazione: sull'intero
mainnet raccolto la distanza massima fra due consegne dello stesso `tid` e'
264 s, e un giorno per lato e' 327 volte tanto. Leggere l'intera partizione
darebbe lo stesso risultato ma con un costo che cresce con lo storico invece
che con la finestra, e su questa macchina un esaurimento di memoria ha gia'
congelato il collector per 82 minuti.

**Finestre inaffidabili dai buchi DERIVATI DAI DATI.** `costs.sources.
unreliable_hours` riusa `catalog.derivedgaps`; il registro `_gaps.jsonl` non
viene letto da nessuna parte di questo modulo.

**L'ordine.** Una query per (canale, coin) sulla sola finestra richiesta, con
`ORDER BY ts_local_ns`, poi una fusione a k vie fra i flussi. L'ordine finale
e' totale (vedi `backtest.events.sort_key`), quindi due run producono la
stessa sequenza. Le righe si tirano a blocchi con `fetchmany`: l'alternativa
`fetchall` terrebbe in RAM tutta la finestra, e su questa macchina la RAM la
sta usando il collector.
"""

from __future__ import annotations

import heapq
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Iterator

from catalog import dataset, trades as catalog_trades
from costs import FundingSeries, L2Book
from costs import sources
from costs.funding import NS_PER_HOUR

from .engine import BlockedHours
from .events import BookEvent, Event, TradeEvent, sort_key

BOOK_CHANNEL = "l2Book"
TRADE_CHANNEL = "trades"
FUNDING_CHANNEL = "activeAssetCtx"

# Canali da cui il motore dipende: un buco su uno qualunque rende l'ora
# inaffidabile per quella coin. Il book serve a eseguire, i trade a riempire i
# maker, activeAssetCtx a sapere quanto costa tenere la posizione.
REQUIRED_CHANNELS = (BOOK_CHANNEL, TRADE_CHANNEL, FUNDING_CHANNEL)

_CHUNK = 20_000


def dates_between(start_ns: int, end_ns: int) -> list[str]:
    """Le date UTC toccate dalla finestra, estremi compresi.

    Si legge anche il giorno precedente e quello successivo, per due motivi che
    chiedono lo stesso margine. Il primo: le directory `date=`/`hour=` sono
    l'istante in cui il writer ha APERTO il batch, non quello della riga (lo
    dice `catalog.dataset.read`), quindi una riga di mezzanotte puo' stare
    nella cartella del giorno prima. Il secondo: la dedup per `tid` deve poter
    vedere la consegna originale di una ritrasmissione che cade dentro la
    finestra (vedi la docstring del modulo). Il filtro vero resta sempre su
    `ts_local_ns`.
    """
    d0 = datetime.fromtimestamp(start_ns / 1e9, tz=timezone.utc).date()
    d1 = datetime.fromtimestamp(end_ns / 1e9, tz=timezone.utc).date()
    out, d = [], d0 - timedelta(days=1)
    while d <= d1 + timedelta(days=1):
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _globs(data_dir: str, channel: str, coin: str, dates: list[str]) -> list[str]:
    out = []
    for date in dates:
        d = os.path.join(data_dir, channel, coin, f"date={date}")
        if os.path.isdir(d):
            out.append(os.path.join(d, "hour=*", "*.parquet"))
    return out


class ParquetFeed:
    """Il feed di eventi di una finestra. Iterabile una volta sola."""

    def __init__(self, con, data_dir: str, coins: tuple[str, ...],
                 start_ns: int, end_ns: int) -> None:
        self.con = con
        self.data_dir = data_dir
        self.coins = tuple(sorted(coins))
        self.start_ns = int(start_ns)
        self.end_ns = int(end_ns)
        self.dates = dates_between(start_ns, end_ns)
        self.n_book_invalid = 0
        self.n_book_rows = 0
        self.n_trade_rows = 0

    # -- book -------------------------------------------------------------------

    def _books(self, coin: str) -> Iterator[BookEvent]:
        globs = _globs(self.data_dir, BOOK_CHANNEL, coin, self.dates)
        if not globs:
            return
        union = " UNION ALL ".join(
            f"SELECT ts_local_ns, ts_exch_ms, raw, filename "
            f"FROM {dataset.read(g, filename=True)}" for g in globs
        )
        cur = self.con.cursor()
        where = "WHERE ts_local_ns >= ? AND ts_local_ns < ?"
        atteso = cur.execute(
            f"SELECT count(*) FROM ({union}) {where}",
            [self.start_ns, self.end_ns],
        ).fetchone()[0]
        cur.execute(
            f"SELECT ts_local_ns, ts_exch_ms, raw, filename FROM ({union}) "
            f"{where} ORDER BY ts_local_ns",
            [self.start_ns, self.end_ns],
        )
        letti = 0
        while True:
            rows = cur.fetchmany(_CHUNK)
            if not rows:
                _verifica(coin, BOOK_CHANNEL, letti, atteso)
                return
            for ts, ts_exch, raw, src in rows:
                letti += 1
                self.n_book_rows += 1
                book = L2Book.try_from_payload(json.loads(raw), ts, ts_exch or 0)
                if book is None:
                    # Snapshot inutilizzabile (lato vuoto, book incrociato).
                    # Contato e saltato: e' un dato sul dato, non un errore.
                    self.n_book_invalid += 1
                    continue
                yield BookEvent(ts_local_ns=ts, coin=coin, book=book,
                                ts_exch_ms=ts_exch or 0, src_file=src)

    # -- trade ------------------------------------------------------------------

    def _materialize_trades(self, cur, coin: str, table: str) -> int:
        dataset.drop(cur, table)
        cur.execute(
            f"CREATE TABLE {table} AS "
            f"SELECT tid, side, px, sz, time_ms, ts_local_ns "
            f"FROM {catalog_trades.dedup_sql(self.data_dir, coin, self.dates)} "
            f"WHERE ts_local_ns >= ? AND ts_local_ns < ?",
            [self.start_ns, self.end_ns],
        )
        return cur.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

    def _trades(self, coin: str) -> Iterator[TradeEvent]:
        # Una tabella per coin, viva finche' il flusso di quella coin non e'
        # esaurito: la fusione a k vie tira da tutti i flussi insieme, quindi
        # un nome condiviso li farebbe leggere l'uno dai dati dell'altro.
        table = f"bt_trades_{coin.lower()}"
        cur = self.con.cursor()
        atteso = self._materialize_trades(cur, coin, table)
        letti = 0
        try:
            cur.execute(
                f"SELECT tid, side, px, sz, time_ms, ts_local_ns FROM {table} "
                f"ORDER BY ts_local_ns, tid"
            )
            while True:
                rows = cur.fetchmany(_CHUNK)
                if not rows:
                    _verifica(coin, TRADE_CHANNEL, letti, atteso)
                    return
                for tid, side, px, sz, time_ms, ts in rows:
                    letti += 1
                    self.n_trade_rows += 1
                    yield TradeEvent(ts_local_ns=ts, coin=coin, px=float(px),
                                     sz=float(sz), side=side or "",
                                     tid=int(tid), time_ms=int(time_ms or 0))
        finally:
            dataset.drop(cur, table)

    # -- fusione ----------------------------------------------------------------

    def __iter__(self) -> Iterator[Event]:
        streams: list[Iterator[Event]] = [self._trades(c) for c in self.coins]
        streams += [self._books(c) for c in self.coins]
        return heapq.merge(*streams, key=sort_key)


def context(con, data_dir: str, coins: tuple[str, ...],
            dates: list[str] | None = None
            ) -> tuple[dict[str, FundingSeries], BlockedHours, dict]:
    """Serie di funding e ore bloccate, dalle funzioni gia' esistenti.

    Le ore bloccate sono l'unione, per coin, dei buchi derivati sui tre canali
    da cui il motore dipende. Marcare in eccesso e' l'errore giusto: costa
    ore di dati, mentre l'errore opposto costa un risultato che sembra sano.

    `dates` limita la derivazione dei buchi ai giorni della finestra piu' il
    margine. E' la fase che fa il picco di memoria dell'intera esecuzione —
    misurato, non supposto — perche' ricostruisce l'ordine di scrittura di tre
    canali per coin su tutto lo storico raccolto.
    """
    partitions = [(ch, c) for c in sorted(coins) for ch in REQUIRED_CHANNELS]
    soglie: list = []
    unreliable = sources.unreliable_hours(con, data_dir, partitions,
                                          dates=dates, thresholds_out=soglie)
    per_coin: dict[str, frozenset[int]] = {}
    funding: dict[str, FundingSeries] = {}
    stats: dict[str, dict] = {}
    for coin in sorted(coins):
        hours: set[int] = set()
        for ch in REQUIRED_CHANNELS:
            hours |= set(unreliable.get((ch, coin), frozenset()))
        per_coin[coin] = frozenset(hours)
        funding[coin] = sources.funding_series(
            con, data_dir, coin,
            unreliable.get((FUNDING_CHANNEL, coin), frozenset()),
        )
        stats[coin] = {
            "ore_bloccate": len(hours),
            "regolamenti_noti": len(funding[coin]),
            "primo_regolamento": (funding[coin].span or (None, None))[0],
            "ultimo_definitivo": funding[coin].last_final,
            "soglie": [
                (r["channel"], round(r["threshold_s"], 3), r["basis"])
                for r in soglie if r["coin"] == coin
            ],
        }
    return funding, BlockedHours(per_coin=per_coin), stats


def hour_of(ts_ns: int) -> int:
    return ts_ns // NS_PER_HOUR


def _verifica(coin: str, channel: str, letti: int, atteso: int) -> None:
    """Il flusso ha consegnato tutte le righe che la query aveva contato?

    Esiste per un difetto vero, trovato alla prima esecuzione su dati reali:
    tutti i flussi condividevano una connessione DuckDB, e ogni nuova
    `execute` invalidava il cursore aperto dal flusso precedente. Il feed si
    fermava a `_CHUNK` righe di trade e la simulazione proseguiva senza
    errori — 24 ore di backtest su 20.000 trade tondi tondi invece di 68.000.
    Un backtest che perde meta' dei dati e' esattamente il "risultato falso e
    plausibile" che CLAUDE.md vieta. Ora ogni flusso ha il proprio cursore, e
    questo controllo e' la rete: se il conto non torna, si ferma.
    """
    if letti != atteso:
        raise ValueError(
            f"{coin}/{channel}: il feed ha consegnato {letti} righe delle "
            f"{atteso} contate sulla finestra. Il flusso e' stato troncato: il "
            f"risultato sarebbe calcolato su meno dati di quelli disponibili."
        )
