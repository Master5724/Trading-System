"""
Test di coin_of / exch_ts_of / truncate_book.

I payload sono copiati dagli esempi della documentazione Hyperliquid
("WebSocket / Subscriptions"), non inventati: il valore di questi test sta
tutto nel fatto che la forma dei dati sia quella vera. Quando l'exchange
cambia un campo, e' qui che deve rompersi.

Esecuzione (nessuna dipendenza esterna, nessun pyarrow, nessun websockets):

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collector.parsing import coin_of, exch_ts_of, is_snapshot, truncate_book

# --- payload di esempio, dai doc ufficiali ---------------------------------

L2BOOK = {
    "coin": "BTC",
    "time": 1681247412573,
    "levels": [
        [{"px": "29792.0", "sz": "0.5", "n": 2},
         {"px": "29791.0", "sz": "1.0", "n": 3},
         {"px": "29790.0", "sz": "2.0", "n": 1}],
        [{"px": "29793.0", "sz": "0.4", "n": 1},
         {"px": "29794.0", "sz": "1.1", "n": 2},
         {"px": "29795.0", "sz": "3.0", "n": 5}],
    ],
}

TRADES = [
    {"coin": "SOL", "side": "B", "px": "25.1", "sz": "10.0", "hash": "0xabc",
     "time": 1681222254710, "tid": 118906512037719, "users": ["0x1", "0x2"]},
    {"coin": "SOL", "side": "A", "px": "25.0", "sz": "2.0", "hash": "0xdef",
     "time": 1681222254999, "tid": 118906512037720, "users": ["0x3", "0x4"]},
]

BBO = {
    "coin": "ETH",
    "time": 1708622398623,
    "bbo": [{"px": "3007.1", "sz": "3.0", "n": 1},
            {"px": "3007.2", "sz": "2.0", "n": 1}],
}

# Un lato puo' essere null: book vuoto da un lato, succede sui coin sottili.
BBO_ONE_SIDED = {"coin": "HYPE", "time": 1708622398700,
                 "bbo": [None, {"px": "12.0", "sz": "5.0", "n": 1}]}

CANDLE = {
    "t": 1681923600000, "T": 1681923659999, "s": "BTC", "i": "1m",
    "o": "29258.0", "c": "29262.0", "h": "29280.0", "l": "29258.0",
    "v": "0.98639", "n": 8,
}

ACTIVE_ASSET_CTX = {
    "coin": "BTC",
    "ctx": {"dayNtlVlm": "1169046.29406", "prevDayPx": "15.512",
            "markPx": "14.3", "midPx": "14.3005", "funding": "0.0000125",
            "openInterest": "688.11", "oraclePx": "14.3"},
}

# Sottoscrivendo activeAssetCtx su un asset spot, l'exchange risponde su un
# channel diverso da quello richiesto.
ACTIVE_SPOT_ASSET_CTX = {
    "coin": "@107",
    "ctx": {"dayNtlVlm": "8906.0", "prevDayPx": "0.20432", "markPx": "0.14",
            "midPx": "0.209265", "circulatingSupply": "598274.76"},
}

ALL_MIDS = {"mids": {"APE": "4.33245", "ARB": "1.21695", "BTC": "29793.0"}}

USER_FILLS = {
    "isSnapshot": True,
    "user": "0x0000000000000000000000000000000000000001",
    "fills": [{"coin": "ETH", "px": "1900.0", "sz": "0.1", "side": "B",
               "time": 1681222254710, "startPosition": "0.0",
               "dir": "Open Long", "closedPnl": "0.0", "hash": "0xaaa",
               "oid": 123, "crossed": True, "fee": "0.01", "tid": 1,
               "feeToken": "USDC"}],
}

USER_FUNDINGS = {
    "isSnapshot": True,
    "user": "0x0000000000000000000000000000000000000001",
    "fundings": [{"time": 1681222254710, "coin": "ETH", "usdc": "-3.5",
                  "szi": "49.1955", "fundingRate": "0.0000125"}],
}

ORDER_UPDATES = [
    {"order": {"coin": "BTC", "side": "B", "limitPx": "29792.0", "sz": "0.0",
               "oid": 91490942, "timestamp": 1681247412573, "origSz": "0.0",
               "cloid": None},
     "status": "open", "statusTimestamp": 1681247412573},
]

# userEvents risponde sul channel "user".
USER_EVENT_FILLS = {"fills": USER_FILLS["fills"]}


class TestCoinOf(unittest.TestCase):
    def test_canali_con_campo_coin(self):
        self.assertEqual(coin_of("l2Book", L2BOOK), "BTC")
        self.assertEqual(coin_of("bbo", BBO), "ETH")
        self.assertEqual(coin_of("activeAssetCtx", ACTIVE_ASSET_CTX), "BTC")

    def test_active_spot_asset_ctx(self):
        # Regressione: il channel restituito dall'exchange per gli asset spot
        # non e' quello sottoscritto. Se questo torna "", i dati spot finiscono
        # tutti mescolati nella partizione _global.
        self.assertEqual(coin_of("activeSpotAssetCtx", ACTIVE_SPOT_ASSET_CTX), "@107")

    def test_trades_legge_il_primo_elemento(self):
        self.assertEqual(coin_of("trades", TRADES), "SOL")

    def test_candle_oggetto_e_lista(self):
        # I doc dichiarano Candle[], il server invia un oggetto singolo.
        self.assertEqual(coin_of("candle", CANDLE), "BTC")
        self.assertEqual(coin_of("candle", [CANDLE]), "BTC")

    def test_canali_globali_e_utente_non_hanno_coin(self):
        self.assertEqual(coin_of("allMids", ALL_MIDS), "")
        self.assertEqual(coin_of("userFills", USER_FILLS), "")
        self.assertEqual(coin_of("orderUpdates", ORDER_UPDATES), "")

    def test_payload_degeneri_non_sollevano(self):
        for channel, data in [
            ("l2Book", None), ("l2Book", []), ("bbo", "boom"),
            ("trades", []), ("trades", None), ("trades", [None]),
            ("candle", []), ("candle", None), ("candle", 42),
            ("activeAssetCtx", {}), ("canaleIgnoto", {"coin": "BTC"}),
        ]:
            with self.subTest(channel=channel, data=data):
                self.assertEqual(coin_of(channel, data), "")


class TestExchTsOf(unittest.TestCase):
    def test_l2book(self):
        self.assertEqual(exch_ts_of("l2Book", L2BOOK), 1681247412573)

    def test_bbo(self):
        # Regressione: bbo espone `time` e veniva ignorato, scrivendo 0.
        self.assertEqual(exch_ts_of("bbo", BBO), 1708622398623)
        self.assertEqual(exch_ts_of("bbo", BBO_ONE_SIDED), 1708622398700)

    def test_trades_usa_il_primo_trade(self):
        self.assertEqual(exch_ts_of("trades", TRADES), 1681222254710)

    def test_candle_usa_apertura_non_chiusura(self):
        # `t` e non `T`: e' la chiave stabile della barra fra un update e l'altro.
        self.assertEqual(exch_ts_of("candle", CANDLE), 1681923600000)
        self.assertEqual(exch_ts_of("candle", [CANDLE]), 1681923600000)

    def test_canali_utente(self):
        self.assertEqual(exch_ts_of("userFills", USER_FILLS), 1681222254710)
        self.assertEqual(exch_ts_of("userFundings", USER_FUNDINGS), 1681222254710)
        self.assertEqual(exch_ts_of("orderUpdates", ORDER_UPDATES), 1681247412573)
        self.assertEqual(exch_ts_of("user", USER_EVENT_FILLS), 1681222254710)

    def test_canali_senza_timestamp_nel_payload(self):
        # Non e' un fallback: questi payload non contengono nessun timestamp.
        self.assertEqual(exch_ts_of("allMids", ALL_MIDS), 0)
        self.assertEqual(exch_ts_of("activeAssetCtx", ACTIVE_ASSET_CTX), 0)
        self.assertEqual(exch_ts_of("activeSpotAssetCtx", ACTIVE_SPOT_ASSET_CTX), 0)

    def test_payload_degeneri_danno_zero(self):
        for channel, data in [
            ("l2Book", None), ("l2Book", {}), ("l2Book", {"time": "non-un-numero"}),
            ("trades", []), ("trades", [{}]), ("trades", None),
            ("candle", []), ("candle", {}), ("candle", None),
            ("userFills", {"fills": []}), ("userFills", {}), ("userFills", None),
            ("userFundings", {"fundings": []}), ("orderUpdates", []),
            ("user", {"nonUserCancel": [{"coin": "BTC", "oid": 1}]}),
            ("user", None),
        ]:
            with self.subTest(channel=channel, data=data):
                self.assertEqual(exch_ts_of(channel, data), 0)


class TestTruncateBook(unittest.TestCase):
    def test_taglia_entrambi_i_lati(self):
        out = truncate_book(L2BOOK, 2)
        self.assertEqual(len(out["levels"][0]), 2)
        self.assertEqual(len(out["levels"][1]), 2)
        # Taglia dalla coda: i livelli migliori restano.
        self.assertEqual(out["levels"][0][0]["px"], "29792.0")
        self.assertEqual(out["levels"][1][0]["px"], "29793.0")

    def test_non_muta_l_originale(self):
        truncate_book(L2BOOK, 1)
        self.assertEqual(len(L2BOOK["levels"][0]), 3)
        self.assertEqual(len(L2BOOK["levels"][1]), 3)

    def test_conserva_gli_altri_campi(self):
        out = truncate_book(L2BOOK, 1)
        self.assertEqual(out["coin"], "BTC")
        self.assertEqual(out["time"], 1681247412573)

    def test_depth_maggiore_della_profondita_disponibile(self):
        out = truncate_book(L2BOOK, 50)
        self.assertEqual(len(out["levels"][0]), 3)

    def test_lato_vuoto(self):
        book = {"coin": "HYPE", "time": 1, "levels": [[], L2BOOK["levels"][1]]}
        out = truncate_book(book, 2)
        self.assertEqual(out["levels"][0], [])
        self.assertEqual(len(out["levels"][1]), 2)

    def test_payload_inatteso_passa_invariato(self):
        for data in [{}, {"coin": "BTC"}, {"levels": None}, {"levels": [[]]}]:
            with self.subTest(data=data):
                self.assertEqual(truncate_book(data, 5), data)
        self.assertIsNone(truncate_book(None, 5))


class TestIsSnapshot(unittest.TestCase):
    def test_snapshot_utente(self):
        # Lo snapshot torna a ogni riconnessione: chi legge deve deduplicare.
        self.assertTrue(is_snapshot(USER_FILLS))
        self.assertTrue(is_snapshot(USER_FUNDINGS))

    def test_non_snapshot(self):
        self.assertFalse(is_snapshot({"isSnapshot": False, "fills": []}))
        self.assertFalse(is_snapshot(L2BOOK))
        self.assertFalse(is_snapshot(TRADES))
        self.assertFalse(is_snapshot(None))


if __name__ == "__main__":
    unittest.main()
