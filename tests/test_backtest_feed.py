"""La lettura dei parquet: ordine, dedup, provenienza, snapshot inutilizzabili.

I parquet di questo test vengono scritti al volo in una directory temporanea,
con lo stesso schema del collector. Non si legge `/home/ubuntu/hl-data`: quella
directory e' scritta da un collector vivo e un test che la usasse non sarebbe
deterministico. E non si committa un campione binario: qui interessa che il
feed si comporti bene sui casi scomodi — un `tid` ritrasmesso, un book
incrociato, due file nello stesso `hour=` — e quei casi vanno costruiti, non
cercati.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catalog import dataset

from backtest.events import BookEvent, TradeEvent, sort_key
from backtest.feed import ParquetFeed, dates_between

NS = 1_000_000_000
# 2026-08-15 20:00:00 UTC. Le righe vengono scritte di proposito sotto
# `date=2026-08-14/hour=12`: la cartella e' l'istante in cui il writer ha
# APERTO il batch, non quello della riga, e il feed deve trovarle lo stesso
# guardando i giorni confinanti e filtrando su `ts_local_ns`.
BASE_NS = 1_786_824_000 * NS
COIN = "BTC"


def book_payload(mid: float, crossed: bool = False) -> str:
    bid, ask = mid - 0.5, mid + 0.5
    if crossed:
        bid, ask = ask, bid          # book incrociato: `costs` lo rifiuta
    return json.dumps({
        "coin": COIN,
        "time": BASE_NS // 1_000_000,
        "levels": [
            [{"px": f"{bid - i}", "sz": "1.5"} for i in range(3)],
            [{"px": f"{ask + i}", "sz": "1.5"} for i in range(3)],
        ],
    })


def trade_payload(items: list[tuple[int, float, float]]) -> str:
    return json.dumps([
        {"coin": COIN, "side": "B", "px": f"{px}", "sz": f"{sz}",
         "time": BASE_NS // 1_000_000, "hash": f"0x{tid:064x}", "tid": tid}
        for tid, px, sz in items
    ])


class TestParquetFeed(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.root = tempfile.mkdtemp(prefix="bt-feed-")
        cls.data_dir = os.path.join(cls.root, "data")
        cls.con = dataset.connect(os.path.join(cls.root, "tmp"))
        cls._write_books()
        cls._write_trades()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.con.close()
        shutil.rmtree(cls.root, ignore_errors=True)

    @classmethod
    def _part(cls, channel: str, name: str) -> str:
        d = os.path.join(cls.data_dir, channel, COIN, "date=2026-08-14",
                         "hour=12")
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, name)

    @classmethod
    def _copy(cls, rows: list[tuple], path: str) -> None:
        values = ", ".join(
            f"({ts}, {ts // 1_000_000}, '{ch}', '{COIN}', "
            f"{dataset.sql_str(raw)})" for ts, ch, raw in rows
        )
        cls.con.execute(
            f"COPY (SELECT * FROM (VALUES {values}) "
            f"AS t(ts_local_ns, ts_exch_ms, channel, coin, raw)) "
            f"TO {dataset.sql_str(path)} (FORMAT PARQUET)"
        )

    @classmethod
    def _write_books(cls) -> None:
        # Due file nella stessa partizione, uno con uno snapshot incrociato.
        cls._copy([(BASE_NS + 0 * NS, "l2Book", book_payload(50_000.0)),
                   (BASE_NS + 5 * NS, "l2Book", book_payload(50_010.0))],
                  cls._part("l2Book", "part-1.parquet"))
        cls._copy([(BASE_NS + 10 * NS, "l2Book",
                    book_payload(50_020.0, crossed=True)),
                   (BASE_NS + 15 * NS, "l2Book", book_payload(50_030.0))],
                  cls._part("l2Book", "part-2.parquet"))

    @classmethod
    def _write_trades(cls) -> None:
        # Il tid 1002 e' consegnato due volte: e' la ritrasmissione che il
        # collector produce dopo una riconnessione.
        cls._copy([(BASE_NS + 2 * NS, "trades",
                    trade_payload([(1001, 50_000.5, 0.1),
                                   (1002, 50_001.0, 0.2)]))],
                  cls._part("trades", "part-1.parquet"))
        cls._copy([(BASE_NS + 12 * NS, "trades",
                    trade_payload([(1002, 50_001.0, 0.2),
                                   (1003, 50_002.0, 0.3)]))],
                  cls._part("trades", "part-2.parquet"))

    # -- i test ----------------------------------------------------------------

    def _events(self) -> list:
        feed = ParquetFeed(self.con, self.data_dir, (COIN,),
                           BASE_NS, BASE_NS + 60 * NS)
        return feed, list(iter(feed))

    def test_gli_eventi_escono_in_ordine_di_timestamp(self) -> None:
        feed, events = self._events()
        chiavi = [sort_key(e) for e in events]
        print(f"\n[feed] {len(events)} eventi, {feed.n_book_rows} righe book, "
              f"{feed.n_trade_rows} trade, {feed.n_book_invalid} book "
              f"inutilizzabili")
        self.assertEqual(chiavi, sorted(chiavi))

    def test_lo_snapshot_incrociato_viene_contato_e_saltato(self) -> None:
        feed, events = self._events()
        books = [e for e in events if isinstance(e, BookEvent)]
        self.assertEqual(feed.n_book_rows, 4)
        self.assertEqual(feed.n_book_invalid, 1)
        self.assertEqual(len(books), 3)

    def test_i_trade_sono_deduplicati_per_tid(self) -> None:
        feed, events = self._events()
        trades = [e for e in events if isinstance(e, TradeEvent)]
        tid = sorted(t.tid for t in trades)
        print(f"[feed] tid consegnati [1001, 1002, 1002, 1003] -> letti {tid}")
        self.assertEqual(tid, [1001, 1002, 1003])

    def test_ogni_evento_porta_la_propria_provenienza(self) -> None:
        _, events = self._events()
        for e in events:
            if isinstance(e, BookEvent):
                self.assertTrue(e.src_file.endswith(".parquet"), e.src_file)
                self.assertIn("#", e.ref)
            else:
                self.assertIn("tid=", e.ref)

    def test_la_finestra_esclude_cio_che_sta_fuori(self) -> None:
        feed = ParquetFeed(self.con, self.data_dir, (COIN,),
                           BASE_NS + 6 * NS, BASE_NS + 13 * NS)
        events = list(iter(feed))
        ts = [e.ts_local_ns for e in events]
        self.assertTrue(all(BASE_NS + 6 * NS <= x < BASE_NS + 13 * NS
                            for x in ts), ts)
        self.assertEqual(feed.n_trade_rows, 1)      # solo il tid 1003

    def test_si_leggono_anche_i_giorni_confinanti(self) -> None:
        """Le cartelle `date=` sono l'ora in cui il writer ha aperto il batch,
        non quella della riga: il giorno prima e quello dopo vanno guardati."""
        giorni = dates_between(BASE_NS, BASE_NS + 60 * NS)
        # I dati di questo test stanno in `date=2026-08-14` pur essendo del 15:
        # se il feed guardasse solo il giorno della riga non troverebbe niente,
        # e infatti gli altri test di questa classe fallirebbero tutti.
        print(f"[feed] date lette per una finestra di un minuto: {giorni}")
        self.assertEqual(giorni, ["2026-08-14", "2026-08-15", "2026-08-16"])


if __name__ == "__main__":
    unittest.main()
