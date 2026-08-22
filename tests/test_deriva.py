"""Test del controllo di deriva delle soglie congelate.

Il controllo esiste perche' una soglia congelata non si accorge da sola che il
regime della partizione e' cambiato. Tre cose devono valere, e sono le tre che
possono rompersi in silenzio:

1. Si conta il giorno richiesto, non il margine. Il giorno precedente viene
   letto solo per poter calcolare il primo `delta_ns`, e i suoi intervalli non
   devono finire nel conteggio: altrimenti ogni buco verrebbe riportato due
   giorni di fila.
2. Un buco a cavallo della mezzanotte si vede. E' il caso per cui il margine
   esiste, ed e' anche l'ora in cui e' meno probabile che qualcuno stia
   guardando.
3. Il p99 del giorno e quello congelato restano DUE numeri distinti. Il
   controllo li mette accanto: se il codice usasse il p99 del giorno anche come
   soglia, la deriva si annullerebbe da sola e il report direbbe sempre "tutto
   a posto".
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catalog import dataset, deriva, derivedgaps, sanity
from tests.catalog_fixture import write_partition

NS = 1_000_000_000
PRIMA = "2026-08-20"
GIORNO = "2026-08-21"
DOPO = "2026-08-22"


def ns_di(giorno: str, ora: int = 0, minuto: int = 0, secondo: int = 0) -> int:
    d = datetime.strptime(giorno, "%Y-%m-%d").replace(
        hour=ora, minute=minuto, second=secondo, tzinfo=timezone.utc)
    return int(d.timestamp()) * NS


def serie(inizio_ns: int, n: int, passo_s: int = 1) -> list[tuple[int, int, str]]:
    return [(inizio_ns + i * passo_s * NS, 0, "{}") for i in range(n)]


def documento(soglie: list[dict]) -> dict:
    return {
        "versione": derivedgaps.FROZEN_VERSION,
        "calcolate_il": "2026-08-19T10:00:00Z",
        "storico": {"primo_giorno": "2026-08-02", "ultimo_giorno": "2026-08-19",
                    "n_giorni": 18, "n_righe_ts_ordered": 19_223_057},
        "soglie": soglie,
    }


def riga(channel: str, coin: str, threshold_s: float, p99_s: float) -> dict:
    return {"channel": channel, "coin": coin, "n_intervals": 100_000,
            "p99_s": p99_s, "threshold_s": threshold_s,
            "basis": derivedgaps.BASIS_P99}


class TestGiornoDaControllare(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name
        for g in (PRIMA, GIORNO, DOPO):
            write_partition(self.dir, "trades", "TEST", serie(ns_di(g), 3),
                            date=g, hour="00")

    def test_e_l_ultimo_giorno_intero_non_quello_in_corso(self):
        """Il giorno in corso ha solo le ore trascorse: alle 06:10 UTC il suo
        p99 sarebbe quello delle sole ore di notte, e il confronto direbbe
        'deriva' ogni mattina."""
        self.assertEqual(
            deriva.giorno_da_controllare(self.dir, [("trades", "TEST")],
                                         oggi=DOPO),
            GIORNO)

    def test_nessun_giorno_intero(self):
        self.assertIsNone(
            deriva.giorno_da_controllare(self.dir, [("trades", "TEST")],
                                         oggi=PRIMA))


class TestControlla(unittest.TestCase):
    """Due giorni di dati con silenzi noti, e una soglia congelata che li
    supera o no per costruzione."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = os.path.join(self.tmp.name, "dati")

        # 2026-08-20: 100 righe a 1 s, poi un silenzio di 120 s, poi altre 100.
        # E' il giorno di MARGINE: i suoi intervalli non devono essere contati.
        r = serie(ns_di(PRIMA, 10), 100)
        r += serie(ns_di(PRIMA, 10) + (99 + 120) * NS, 100)
        # ... e finisce alle 23:59:00, per il buco di mezzanotte.
        r += serie(ns_di(PRIMA, 23, 59), 1)
        # Il nome del part-file porta il ts della prima riga del batch: e' cosi'
        # che `build_ordered` ricostruisce l'ordine di scrittura. Due file
        # chiamati entrambi `part-0.parquet` verrebbero interlacciati per numero
        # di riga e ogni delta sarebbe inventato.
        write_partition(self.dir, "trades", "TEST", r, date=PRIMA, hour="10",
                        part=f"part-{ns_di(PRIMA, 10)}.parquet")

        # 2026-08-21: riprende alle 00:01:30 (buco di 150 s a cavallo della
        # mezzanotte), poi 200 righe a 1 s con un silenzio di 90 s in mezzo.
        r2 = serie(ns_di(GIORNO, 0, 1, 30), 100)
        r2 += serie(ns_di(GIORNO, 0, 1, 30) + (99 + 90) * NS, 100)
        write_partition(self.dir, "trades", "TEST", r2, date=GIORNO, hour="00",
                        part=f"part-{ns_di(GIORNO, 0, 1, 30)}.parquet")

    def esegui(self, threshold_s: float, p99_congelato: float = 3.0):
        con = dataset.connect(os.path.join(self.tmp.name, f"db{threshold_s}"))
        self.addCleanup(con.close)
        doc = documento([riga("trades", "TEST", threshold_s, p99_congelato)])
        frozen = derivedgaps.frozen_rows(doc, [("trades", "TEST")])
        return deriva.controlla(con, self.dir, [("trades", "TEST")], GIORNO,
                                frozen)

    def test_conta_solo_il_giorno_richiesto_non_il_margine(self):
        # Soglia 60 s: nel giorno ci sono il buco di mezzanotte (150 s) e quello
        # interno (90 s) -> 2. Il buco da 120 s del giorno prima non si conta.
        righe = self.esegui(60.0)
        self.assertEqual(len(righe), 1)
        self.assertEqual(righe[0]["n_oltre"], 2)

    def test_il_buco_di_mezzanotte_si_vede(self):
        # Soglia 100 s: solo il buco a cavallo della mezzanotte la supera.
        righe = self.esegui(100.0)
        self.assertEqual(righe[0]["n_oltre"], 1)
        # Tocca l'ultima ora del giorno prima e la prima del giorno richiesto.
        self.assertEqual(righe[0]["n_ore"], 2)

    def test_senza_il_giorno_di_margine_quel_buco_sparisce(self):
        """La prova che il margine serve: leggendo il solo giorno richiesto, il
        `delta_ns` della prima riga e' NULL e il buco di mezzanotte non esiste
        per nessuno."""
        con = dataset.connect(os.path.join(self.tmp.name, "db_senza"))
        self.addCleanup(con.close)
        sanity.build_ordered(con, self.dir, [("trades", "TEST")], [GIORNO])
        oltre = con.execute(
            "SELECT count(*) FROM ts_ordered WHERE delta_ns >= ?",
            [int(100 * NS)]).fetchone()[0]
        self.assertEqual(oltre, 0)

    def test_i_due_p99_sono_numeri_distinti(self):
        righe = self.esegui(60.0, p99_congelato=3.0)
        r = righe[0]
        self.assertEqual(r["p99_congelato_s"], 3.0)
        # Il p99 del giorno lo fanno i 198 intervalli da 1 s piu' i due silenzi:
        # sta sopra 1 s e ben lontano dal 3.0 congelato.
        self.assertGreater(r["p99_giorno_s"], 1.0)
        self.assertNotAlmostEqual(r["p99_giorno_s"], 3.0, places=3)
        self.assertAlmostEqual(r["rapporto"], r["p99_giorno_s"] / 3.0, places=9)

    def test_la_soglia_usata_e_quella_del_file_col_suo_marchio(self):
        r = self.esegui(60.0)[0]
        self.assertEqual(r["threshold_s"], 60.0)
        self.assertEqual(r["basis"], "congelata@2026-08-19 (p99)")


class TestIntestazione(unittest.TestCase):
    def test_dichiara_provenienza_e_copertura(self):
        doc = documento([riga("trades", "A", 30.0, 1.0),
                         riga("trades", "B", 30.0, 1.0)])
        righe = deriva.intestazione(doc, [("trades", "A"), ("trades", "B")], 5)
        testo = "\n".join(righe)
        self.assertIn("congelate v1", testo)
        self.assertIn("2026-08-19T10:00:00Z", testo)
        # Il numero che era cambiato da 12 a 17 senza che nessuno lo dicesse.
        self.assertIn("2 partizioni nel file", testo)
        self.assertIn("2 chieste", testo)
        self.assertIn("5 presenti nei dati", testo)

    def test_senza_file_dichiara_che_sono_misurate(self):
        righe = deriva.intestazione(None, [("trades", "A")], 3)
        self.assertIn("misurate", "\n".join(righe))


if __name__ == "__main__":
    unittest.main()
