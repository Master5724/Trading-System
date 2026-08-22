"""Test delle soglie di buco congelate nel file versionato.

Il difetto coperto qui non e' un errore di calcolo: e' che la soglia CAMBIAVA
DA SOLA. Finche' veniva ricalcolata a ogni esecuzione, il numero dipendeva da
quanti dati erano stati raccolti nel frattempo — misurato sui dati veri: la
soglia storica di `trades/SOL` e' passata da 82,48 s a 80,80 s in nove giorni
perche' lo storico e' cresciuto da 17 a 20 giorni, e con lei sono cambiati i
buchi rilevati su finestre identiche. Nel caso peggiore, con la soglia derivata
dal p99 della finestra stessa, saliva proprio quando la raccolta in quella
finestra era andata peggio.

Quattro cose devono valere:

1. La soglia usata e' esattamente quella scritta nel file, qualunque cosa dicano
   i dati letti.
2. Una partizione senza soglia congelata ferma l'esecuzione invece di ripiegare
   su un calcolo al volo: due coin giudicate con criteri diversi nello stesso
   report sarebbero invisibili.
3. Il file committato copre tutte le partizioni che il collector sta scrivendo.
   Chi aggiunge una coin a `config.mainnet.yaml` senza rigenerare le soglie
   trova questo test rosso, non un backtest che gira lo stesso.
4. La provenienza (data di calcolo e criterio originale) viaggia insieme al
   numero fino al report.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml

from catalog import dataset, derivedgaps, sanity
from costs import sources
from tests.catalog_fixture import BASE_HOUR_IDX, NS_PER_HOUR, write_partition

NS = 1_000_000_000
BASE_NS = BASE_HOUR_IDX * NS_PER_HOUR
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# I canali da cui il backtester dipende. Un buco su uno solo di questi rende
# l'ora inaffidabile, quindi tutti e tre devono avere una soglia congelata.
CANALI_RICHIESTI = ("l2Book", "trades", "activeAssetCtx")


def documento(soglie: list[dict], calcolate_il: str = "2026-08-22T00:00:00Z"
              ) -> dict:
    return {
        "versione": derivedgaps.FROZEN_VERSION,
        "calcolate_il": calcolate_il,
        "storico": {"primo_giorno": "2026-08-02", "ultimo_giorno": "2026-08-21",
                    "n_giorni": 20},
        "soglie": soglie,
    }


def riga(channel: str, coin: str, threshold_s: float, p99_s: float = 1.0,
         basis: str = derivedgaps.BASIS_P99, n_intervals: int = 100_000) -> dict:
    return {"channel": channel, "coin": coin, "n_intervals": n_intervals,
            "p99_s": p99_s, "threshold_s": threshold_s, "basis": basis}


class TestFileCommittato(unittest.TestCase):
    """Il file che sta nel repo, letto come lo leggerebbe un'esecuzione vera."""

    def setUp(self):
        self.doc = derivedgaps.load_frozen()

    def test_copre_le_partizioni_del_collector(self):
        with open(os.path.join(ROOT, "config.mainnet.yaml"), encoding="utf-8") as f:
            coins = yaml.safe_load(f)["coins"]
        partitions = [(ch, c) for c in coins for ch in CANALI_RICHIESTI]
        # Non solleva: e' l'asserzione. Se il collector raccoglie una coin per
        # cui nessuno ha congelato una soglia, il backtest su quella coin non
        # puo' partire, ed e' meglio saperlo qui.
        righe = derivedgaps.frozen_rows(self.doc, partitions)
        self.assertEqual(len(righe), len(partitions))

    def test_ogni_soglia_ha_una_provenienza_e_un_valore_sensato(self):
        for r in self.doc["soglie"]:
            with self.subTest(partizione=f"{r['channel']}/{r['coin']}"):
                self.assertGreaterEqual(r["threshold_s"],
                                        derivedgaps.DEFAULT_MIN_GAP_S)
                self.assertGreater(r["n_intervals"], 0)
                self.assertIn(r["basis"], (derivedgaps.BASIS_P99,
                                           derivedgaps.BASIS_FLOOR,
                                           derivedgaps.BASIS_TOO_FEW))

    def test_lo_storico_dichiarato_e_scritto_per_intero(self):
        s = self.doc["storico"]
        for campo in ("primo_giorno", "ultimo_giorno", "n_giorni",
                      "p99_multiple", "min_gap_s"):
            self.assertIsNotNone(s.get(campo), campo)


class TestLettura(unittest.TestCase):
    def scrivi(self, doc: dict) -> str:
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                          encoding="utf-8")
        json.dump(doc, tmp)
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        return tmp.name

    def test_versione_diversa_non_si_legge(self):
        doc = documento([riga("trades", "BTC", 40.0)])
        doc["versione"] = derivedgaps.FROZEN_VERSION + 1
        with self.assertRaises(ValueError) as e:
            derivedgaps.load_frozen(self.scrivi(doc))
        self.assertIn("versione", str(e.exception))

    def test_campo_obbligatorio_mancante_non_si_legge(self):
        for campo in ("calcolate_il", "storico", "soglie"):
            doc = documento([riga("trades", "BTC", 40.0)])
            del doc[campo]
            with self.subTest(campo=campo):
                with self.assertRaises(ValueError):
                    derivedgaps.load_frozen(self.scrivi(doc))

    def test_partizione_mancante_solleva_e_dice_quale(self):
        doc = documento([riga("trades", "BTC", 40.0)])
        with self.assertRaises(ValueError) as e:
            derivedgaps.frozen_rows(doc, [("trades", "BTC"), ("trades", "SOL")])
        msg = str(e.exception)
        self.assertIn("trades/SOL", msg)
        self.assertNotIn("trades/BTC", msg)
        # Il messaggio dice anche come rimediare: una soglia mancante non e' un
        # bug da capire, e' un file da rigenerare.
        self.assertIn("catalog.soglie", msg)

    def test_i_canali_di_backfill_non_sono_richiesti(self):
        """Non hanno cadenza, quindi non hanno soglia: chiederla sarebbe un
        errore permanente."""
        doc = documento([riga("trades", "BTC", 40.0)])
        righe = derivedgaps.frozen_rows(
            doc, [("trades", "BTC"), ("backfill_funding", "BTC")])
        self.assertEqual([r["coin"] for r in righe], ["BTC"])

    def test_la_provenienza_viaggia_col_numero(self):
        doc = documento([riga("trades", "BTC", 40.0)],
                        calcolate_il="2026-08-22T09:15:00Z")
        r = derivedgaps.frozen_rows(doc, [("trades", "BTC")])[0]
        self.assertEqual(r["basis"], "congelata@2026-08-22 (p99)")
        self.assertEqual(r["threshold_s"], 40.0)


class TestSoglieSuiDati(unittest.TestCase):
    """La soglia congelata vince sui dati letti, ed e' l'unico punto che conta.

    Si costruisce una serie in cui il p99 misurato darebbe una soglia molto
    diversa da quella del file: se il rilevamento seguisse i dati invece del
    file, i conteggi divergerebbero.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data_dir = os.path.join(self.tmp.name, "dati")
        # 600 righe a 1 s, con due silenzi da 45 s. Il p99 di questa serie sta
        # appena sopra 1 s, quindi la soglia MISURATA vale il pavimento (30 s) e
        # trova entrambi i silenzi.
        righe = [(BASE_NS + i * NS, 0, "{}") for i in range(300)]
        t = BASE_NS + 299 * NS + 45 * NS
        righe += [(t + i * NS, 0, "{}") for i in range(300)]
        t2 = t + 299 * NS + 45 * NS
        righe += [(t2 + i * NS, 0, "{}") for i in range(300)]
        write_partition(self.data_dir, "trades", "TEST", righe)

    def conta(self, **kw) -> tuple[int, list[dict]]:
        # Una temp dir per chiamata: due connessioni DuckDB vive insieme sullo
        # stesso `temp_directory` si contenderebbero gli stessi file di spill.
        self.n_conn = getattr(self, "n_conn", 0) + 1
        con = dataset.connect(os.path.join(self.tmp.name, f"db{self.n_conn}"))
        self.addCleanup(con.close)
        sanity.build_ordered(con, self.data_dir, [("trades", "TEST")])
        usate = derivedgaps.build_thresholds(con, **kw)
        return derivedgaps.build(con), usate

    def test_soglia_congelata_alta_nasconde_i_buchi_che_la_misurata_trova(self):
        n_misurata, s_misurata = self.conta()
        doc = documento([riga("trades", "TEST", 60.0)])
        n_congelata, s_congelata = self.conta(
            frozen=derivedgaps.frozen_rows(doc, [("trades", "TEST")]))
        self.assertEqual(s_misurata[0]["threshold_s"],
                         derivedgaps.DEFAULT_MIN_GAP_S)
        self.assertEqual(n_misurata, 2)
        # 60 s congelati: i due silenzi da 45 s non li superano. Il numero non
        # e' "giusto" — e' RIPRODUCIBILE, ed e' scritto in un file che sta in
        # git accanto al codice che lo usa.
        self.assertEqual(s_congelata[0]["threshold_s"], 60.0)
        self.assertEqual(n_congelata, 0)

    def test_soglia_congelata_bassa_trova_quel_che_la_misurata_perde(self):
        doc = documento([riga("trades", "TEST", 40.0)])
        n, usate = self.conta(
            frozen=derivedgaps.frozen_rows(doc, [("trades", "TEST")]))
        self.assertEqual(n, 2)
        self.assertEqual(usate[0]["threshold_s"], 40.0)

    def test_il_p99_del_file_non_e_quello_della_finestra(self):
        """`n_intervals` e `p99_s` restano quelli dello storico: sono la
        provenienza del numero, non una misura dei giorni letti."""
        doc = documento([riga("trades", "TEST", 40.0, p99_s=16.5,
                              n_intervals=1_234_567)])
        _, usate = self.conta(
            frozen=derivedgaps.frozen_rows(doc, [("trades", "TEST")]))
        self.assertEqual(usate[0]["p99_s"], 16.5)
        self.assertEqual(usate[0]["n_intervals"], 1_234_567)


class TestModi(unittest.TestCase):
    def test_modo_sconosciuto_solleva_prima_di_leggere_i_dati(self):
        with self.assertRaises(ValueError) as e:
            sources.unreliable_hours(None, "/non/esiste", [("trades", "BTC")],
                                     soglie="p99_della_finestra")
        self.assertIn("soglie=", str(e.exception))


if __name__ == "__main__":
    unittest.main()
