"""
Test del riconoscimento degli ack di sottoscrizione.

I due payload di riferimento non sono inventati e non vengono dai doc: sono
catturati sul WebSocket di **testnet** il 2026-08-02, aprendo una connessione e
inviando una sola subscribe `l2Book` e una sola `trades` su BTC. Sono copiati
qui come stringhe grezze, esattamente come sono arrivati sul filo, perche' il
valore di questi test sta tutto nel fatto che la forma sia quella vera: l'eco di
`l2Book` porta tre campi opzionali che non abbiamo inviato, quella di `trades`
no. E' la differenza che faceva fallire il confronto per uguaglianza.

Esecuzione:

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collector.acks import ack_matches, sub_key  # noqa: E402

try:  # il test di audit tocca il Collector, che tira dentro websockets/pyarrow
    from collector.collector import Collector
except ModuleNotFoundError:  # pragma: no cover - dipende dalla macchina
    Collector = None


# --- payload grezzi, testnet, 2026-08-02 ------------------------------------

SENT_L2BOOK = {"type": "l2Book", "coin": "BTC"}
RAW_ACK_L2BOOK = (
    '{"channel":"subscriptionResponse","data":{"method":"subscribe",'
    '"subscription":{"type":"l2Book","coin":"BTC","nSigFigs":null,'
    '"mantissa":null,"fast":false}}}'
)

SENT_TRADES = {"type": "trades", "coin": "BTC"}
RAW_ACK_TRADES = (
    '{"channel":"subscriptionResponse","data":{"method":"subscribe",'
    '"subscription":{"type":"trades","coin":"BTC"}}}'
)


def echo_of(raw: str) -> dict:
    return json.loads(raw)["data"]["subscription"]


class TestAckMatches(unittest.TestCase):
    def test_l2book_ack_reale_riconosciuto(self):
        """Il caso che falliva: l'eco aggiunge nSigFigs, mantissa e fast."""
        self.assertTrue(ack_matches(SENT_L2BOOK, echo_of(RAW_ACK_L2BOOK)))

    def test_trades_ack_reale_riconosciuto(self):
        """Eco identica alla subscribe: funzionava prima, deve funzionare ora."""
        self.assertTrue(ack_matches(SENT_TRADES, echo_of(RAW_ACK_TRADES)))

    def test_uguaglianza_esatta_fallirebbe_su_l2book(self):
        """Fissa la causa del difetto: e' il confronto vecchio a essere sbagliato,
        non il canale. Se un giorno l'exchange smettesse di aggiungere i campi
        opzionali, questo test lo segnala invece di lasciarlo passare in
        silenzio."""
        echo = echo_of(RAW_ACK_L2BOOK)
        self.assertNotEqual(sub_key(SENT_L2BOOK), sub_key(echo))
        self.assertEqual(sub_key(SENT_TRADES), sub_key(echo_of(RAW_ACK_TRADES)))

    def test_ack_di_un_altra_coin_non_conferma(self):
        self.assertFalse(ack_matches({"type": "l2Book", "coin": "ETH"},
                                     echo_of(RAW_ACK_L2BOOK)))

    def test_ack_di_un_altro_canale_non_conferma(self):
        self.assertFalse(ack_matches(SENT_TRADES, echo_of(RAW_ACK_L2BOOK)))

    def test_campo_inviato_e_assente_nell_eco_non_conferma(self):
        """Se chiediamo candle 1m e l'eco non dice quale intervallo, non e' un
        ack di quella subscribe."""
        self.assertFalse(
            ack_matches({"type": "candle", "coin": "BTC", "interval": "1m"},
                        {"type": "candle", "coin": "BTC"})
        )

    def test_campo_opzionale_valorizzato_non_conferma(self):
        """`fast: true` nell'eco descrive una sottoscrizione diversa da quella
        chiesta: la tolleranza vale solo per i default riempiti dal server."""
        echo = dict(echo_of(RAW_ACK_L2BOOK), fast=True)
        self.assertFalse(ack_matches(SENT_L2BOOK, echo))

    def test_indirizzo_utente_normalizzato_in_minuscolo(self):
        """L'exchange restituisce gli indirizzi in minuscolo. Assunzione non
        verificata sul filo (user_address e' null in config): se un giorno i
        canali utente venissero attivati, e' qui che si vede."""
        addr = "0xAbCdEf0123456789AbCdEf0123456789AbCdEf01"
        self.assertTrue(ack_matches({"type": "userFills", "user": addr},
                                    {"type": "userFills", "user": addr.lower()}))

    def test_eco_non_dizionario(self):
        self.assertFalse(ack_matches(SENT_L2BOOK, None))


@unittest.skipIf(Collector is None, "richiede websockets/pyarrow installati")
class TestAuditSubscriptions(unittest.IsolatedAsyncioTestCase):
    """Percorso completo: messaggio grezzo -> _handle -> audit.

    Il Collector viene costruito senza __init__ per non aprire un WriterPool
    (pyarrow) ne' un registro dei buchi su disco: nessuno dei due entra nel
    percorso di un `subscriptionResponse`.
    """

    def _collector(self, subs: list[dict]):
        c = object.__new__(Collector)
        c.cfg = {"watchdog": {"subscription_ack_seconds": 0}}
        c.subs = subs
        c._acked = set()
        c.last_msg_at = 0.0
        c.last_by_channel = {}
        c.msg_count = 0
        return c

    async def test_ack_reali_nessun_errore(self):
        c = self._collector([SENT_L2BOOK, SENT_TRADES])
        c._handle(RAW_ACK_L2BOOK)
        c._handle(RAW_ACK_TRADES)
        with self.assertLogs("collector", level="INFO") as cm:
            await c._audit_subscriptions()
        self.assertEqual([r.levelname for r in cm.records], ["INFO"])
        self.assertIn("tutte le 2 sottoscrizioni confermate", cm.output[0])

    async def test_subscribe_senza_risposta_resta_un_errore(self):
        """Il test negativo. Il server risponde solo su trades: la l2Book deve
        continuare a comparire come mancante. Se la rilevazione degli ack viene
        disattivata (commentando la chiamata a _record_ack in _handle), questo
        test NON passa a verde: elenca due mancanti invece di una."""
        c = self._collector([SENT_L2BOOK, SENT_TRADES])
        c._handle(RAW_ACK_TRADES)
        with self.assertLogs("collector", level="ERROR") as cm:
            await c._audit_subscriptions()
        self.assertIn("1 sottoscrizioni senza ack", cm.output[0])
        self.assertIn(sub_key(SENT_L2BOOK), cm.output[0])
        self.assertNotIn(sub_key(SENT_TRADES), cm.output[0])

    async def test_silenzio_totale_resta_un_errore(self):
        """Nessuna risposta dal server: tutte mancanti."""
        c = self._collector([SENT_L2BOOK, SENT_TRADES])
        with self.assertLogs("collector", level="ERROR") as cm:
            await c._audit_subscriptions()
        self.assertIn("2 sottoscrizioni senza ack", cm.output[0])

    async def test_ack_non_riconducibile_viene_segnalato(self):
        c = self._collector([SENT_L2BOOK])
        raw = json.dumps({"channel": "subscriptionResponse",
                          "data": {"method": "subscribe",
                                   "subscription": {"type": "l2Book", "coin": "DOGE"}}})
        with self.assertLogs("collector", level="WARNING") as cm:
            c._handle(raw)
        self.assertIn("non riconducibile", cm.output[0])
        self.assertEqual(c._acked, set())


if __name__ == "__main__":
    unittest.main()
