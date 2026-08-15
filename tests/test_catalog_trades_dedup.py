"""Test della dedup per `tid` del canale trades.

Il caso reale: il collector si riconnette e il server rimanda gli ultimi
scambi. Gli stessi `tid` finiscono su disco due volte, a `ts_local_ns`
diversi. Sui dati mainnet e' circa lo 0,2% dei trade. Un backtester che li
sommasse due volte gonfierebbe il volume e — peggio — datarebbe il trade
all'istante della ritrasmissione, cioe' dopo il fatto.

Due invarianti che questi test difendono:
- la dedup avviene in LETTURA: i file su disco non cambiano, byte per byte;
- si tiene la PRIMA consegna: quella che un sistema live avrebbe visto.

`TestLaVerificaSaFallire` in fondo legge senza dedup e pretende che le
asserzioni esplodano.
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catalog import dataset, trades
from tests.catalog_fixture import trade, trade_payload, write_partition

T0 = 1_785_664_000_000_000_000  # ns


class TradesTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = os.path.join(self.tmp.name, "dati")
        self.addCleanup(self.tmp.cleanup)

    def con(self):
        con = dataset.connect(os.path.join(self.tmp.name, "tmpdb"))
        self.addCleanup(con.close)
        return con

    def scrivi(self, rows, coin="BTC"):
        write_partition(self.data_dir, "trades", coin, rows)

    def fingerprint(self) -> dict[str, str]:
        """Impronta dei file su disco: la dedup non deve toccarli."""
        out = {}
        for root, _, files in os.walk(self.data_dir):
            for name in files:
                p = os.path.join(root, name)
                with open(p, "rb") as f:
                    out[p] = hashlib.sha256(f.read()).hexdigest()
        return out


def messaggio(ts_ns: int, tids: list[int], **kw) -> tuple[int, int, str]:
    payload = trade_payload([trade(t, **kw) for t in tids])
    return (ts_ns, ts_ns // 1_000_000, payload)


class TestDedupInLettura(TradesTestCase):
    def setUp(self):
        super().setUp()
        # Prima consegna: tid 1,2,3. Dopo la riconnessione il server rimanda
        # 2 e 3 e aggiunge 4: due consegne in eccesso su sei.
        self.scrivi([
            messaggio(T0 + 0, [1, 2, 3]),
            messaggio(T0 + 60_000_000_000, [2, 3, 4]),
        ])

    def test_senza_dedup_i_duplicati_ci_sono(self):
        """Precondizione del test: se i dati grezzi non avessero duplicati,
        tutto il resto non proverebbe niente."""
        con = self.con()
        n = con.execute(
            f"SELECT count(*) FROM {trades.exploded_sql(self.data_dir)}"
        ).fetchone()[0]
        self.assertEqual(n, 6)

    def test_una_riga_per_tid(self):
        con = self.con()
        righe = con.execute(
            f"SELECT tid FROM {trades.dedup_sql(self.data_dir)} ORDER BY tid"
        ).fetchall()
        self.assertEqual([r[0] for r in righe], [1, 2, 3, 4])

    def test_si_tiene_la_prima_consegna(self):
        con = self.con()
        ts = con.execute(
            f"SELECT ts_local_ns FROM {trades.dedup_sql(self.data_dir)} WHERE tid = 2"
        ).fetchone()[0]
        self.assertEqual(ts, T0)

    def test_i_campi_sono_tipizzati(self):
        con = self.con()
        row = con.execute(
            f"SELECT px, sz, time_ms, side, coin, px_raw "
            f"FROM {trades.dedup_sql(self.data_dir)} WHERE tid = 1"
        ).fetchone()
        self.assertEqual(row[0], 100.0)
        self.assertEqual(row[1], 1.0)
        self.assertEqual(row[2], 1_785_664_000_000)
        self.assertEqual(row[3], "B")
        self.assertEqual(row[4], "BTC")
        self.assertEqual(row[5], "100.0")

    def test_i_file_su_disco_non_vengono_toccati(self):
        prima = self.fingerprint()
        con = self.con()
        con.execute(f"SELECT count(*) FROM {trades.dedup_sql(self.data_dir)}").fetchone()
        trades.duplicate_stats(con, self.data_dir, ["BTC"])
        trades.materialize_dedup(con, self.data_dir, ["BTC"])
        self.assertEqual(self.fingerprint(), prima)

    def test_materializzazione(self):
        con = self.con()
        n = trades.materialize_dedup(con, self.data_dir, ["BTC"])
        self.assertEqual(n, 4)
        self.assertEqual(
            con.execute("SELECT count(DISTINCT tid) FROM trades_dedup").fetchone()[0], 4
        )

    def test_statistiche_per_coin(self):
        con = self.con()
        stats = trades.duplicate_stats(con, self.data_dir, ["BTC"])
        self.assertEqual(len(stats), 1)
        s = stats[0]
        self.assertEqual(s["coin"], "BTC")
        self.assertEqual(s["n_trades"], 6)
        self.assertEqual(s["n_distinct_tid"], 4)
        self.assertEqual(s["n_dup_tid"], 2)
        self.assertEqual(s["n_tid_ripetuti"], 2)
        self.assertEqual(s["n_tid_contraddittori"], 0)


class TestPiuCoin(TradesTestCase):
    def test_le_coin_restano_separate(self):
        self.scrivi([messaggio(T0, [1, 1])], coin="BTC")
        self.scrivi([messaggio(T0, [9], coin="ETH")], coin="ETH")
        con = self.con()
        stats = {s["coin"]: s for s in
                 trades.duplicate_stats(con, self.data_dir, ["BTC", "ETH"])}
        self.assertEqual(stats["BTC"]["n_dup_tid"], 1)
        self.assertEqual(stats["ETH"]["n_dup_tid"], 0)
        self.assertEqual(trades.materialize_dedup(con, self.data_dir, ["BTC", "ETH"]), 2)

    def test_nessuna_coin_non_esplode(self):
        self.scrivi([messaggio(T0, [1])])
        con = self.con()
        self.assertEqual(trades.duplicate_stats(con, self.data_dir, []), [])
        self.assertEqual(trades.materialize_dedup(con, self.data_dir, []), 0)


class TestDuplicatiContraddittori(TradesTestCase):
    """Stesso `tid`, contenuto diverso. Non e' una ritrasmissione: e' un dato
    di cui non ci si puo' fidare, e il catalogo deve dirlo invece di lasciare
    che la dedup scelga in silenzio."""

    def test_vengono_contati(self):
        self.scrivi([
            messaggio(T0, [7], px="100.0"),
            messaggio(T0 + 1_000_000_000, [7], px="999.0"),
        ])
        con = self.con()
        s = trades.duplicate_stats(con, self.data_dir, ["BTC"])[0]
        self.assertEqual(s["n_dup_tid"], 1)
        self.assertEqual(s["n_tid_contraddittori"], 1)

    def test_la_dedup_resta_deterministica(self):
        self.scrivi([
            messaggio(T0, [7], px="100.0"),
            messaggio(T0 + 1_000_000_000, [7], px="999.0"),
        ])
        con = self.con()
        px = [
            con.execute(
                f"SELECT px FROM {trades.dedup_sql(self.data_dir)} WHERE tid = 7"
            ).fetchone()[0]
            for _ in range(3)
        ]
        self.assertEqual(px, [100.0, 100.0, 100.0])

    def test_stesso_ts_local_tie_break_stabile(self):
        """Due consegne nello stesso batch: senza tie-break l'esito
        dipenderebbe dall'ordine di scan dei file."""
        self.scrivi([
            messaggio(T0, [7], px="100.0", tx_hash="0xaa"),
            messaggio(T0, [7], px="200.0", tx_hash="0xbb"),
        ])
        con = self.con()
        row = con.execute(
            f"SELECT px, tx_hash FROM {trades.dedup_sql(self.data_dir)} WHERE tid = 7"
        ).fetchone()
        self.assertEqual(row, (100.0, "0xaa"))


class TestLaVerificaSaFallire(TradesTestCase):
    """Con la dedup tolta di mezzo — cioe' leggendo la vista grezza — le
    asserzioni sopra devono esplodere."""

    def setUp(self):
        super().setUp()
        self.scrivi([
            messaggio(T0 + 0, [1, 2, 3]),
            messaggio(T0 + 60_000_000_000, [2, 3, 4]),
        ])

    def test_senza_dedup_i_tid_si_ripetono(self):
        con = self.con()
        tids = [r[0] for r in con.execute(
            f"SELECT tid FROM {trades.exploded_sql(self.data_dir)} ORDER BY tid"
        ).fetchall()]
        with self.assertRaises(AssertionError):
            self.assertEqual(tids, [1, 2, 3, 4])
        self.assertEqual(tids, [1, 2, 2, 3, 3, 4])

    def test_senza_dedup_il_trade_viene_datato_alla_ritrasmissione(self):
        con = self.con()
        ts = [r[0] for r in con.execute(
            f"SELECT ts_local_ns FROM {trades.exploded_sql(self.data_dir)} "
            f"WHERE tid = 2 ORDER BY ts_local_ns"
        ).fetchall()]
        with self.assertRaises(AssertionError):
            self.assertEqual(ts, [T0])
        self.assertEqual(ts, [T0, T0 + 60_000_000_000])


if __name__ == "__main__":
    unittest.main()
