"""Test dei buchi derivati dai dati e della riconciliazione col registro.

Il difetto che questi test coprono e' stato osservato sui dati veri: fino al
2026-08-14 20:33 il collector chiudeva le finestre di `_gaps.jsonl` alla
riconnessione della socket invece che alla ripresa dei dati, quindi le
registrava piu' corte del vero. Un'esclusione statistica alimentata da quel
registro lasciava dentro buchi reali — uno da circa 92,9 s l'8 agosto 2026, su
tutte e quattro le coin di `trades`.

Quattro cose devono valere, e per ognuna c'e' la prova che il test sa fallire
(classe `TestSannoFallire`, che disattiva la logica e pretende l'esplosione):

1. Un'assenza nei dati senza record nel registro risulta NON spiegata.
2. Una finestra registrata piu' corta dell'assenza vera produce una sottostima
   misurata, pari alla differenza.
3. Una serie continua non genera falsi buchi.
4. Una finestra registrata a cui non corrisponde nessuna assenza viene
   riportata a parte, distinguendo il caso "piu' corta della soglia di
   rilevamento" (invisibile per costruzione) da quello lungo.

Tutto su directory temporanee: i dati di produzione sono scritti da un
collector vivo, cambiano sotto i piedi e sono in sola lettura.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catalog import dataset, derivedgaps, gapwindows, sanity
from catalog.derivedgaps import DerivedGap
from tests.catalog_fixture import BASE_HOUR_IDX, NS_PER_HOUR, write_partition

NS = 1_000_000_000
BASE_NS = BASE_HOUR_IDX * NS_PER_HOUR
BASE_MS = BASE_NS // 1_000_000

# Il buco reale che ha motivato questa modifica, in secondi.
BUCO_REALE_S = 92.9


def regolari(n: int, step_ns: int = NS, start_ns: int = BASE_NS) -> list[tuple]:
    return [(start_ns + i * step_ns, 0, "{}") for i in range(n)]


def serie_con_buco(buco_s: float, prima: int = 300, dopo: int = 300,
                   step_ns: int = NS) -> list[tuple]:
    """Serie regolare interrotta una volta sola da `buco_s` secondi di silenzio."""
    rows = regolari(prima, step_ns)
    ripresa = BASE_NS + (prima - 1) * step_ns + int(buco_s * NS)
    return rows + regolari(dopo, step_ns, start_ns=ripresa)


def window(start_ms: int, end_ms: int, reason: str = "test",
           event: str = "open") -> gapwindows.Window:
    return gapwindows.Window(start_ms=start_ms, end_ms=end_ms, reason=reason,
                             channels=(), still_open=False, event=event)


def gap(start_s: float, end_s: float, channel: str = "trades",
        coin: str = "BTC") -> DerivedGap:
    """Buco espresso in secondi dall'istante base, per leggibilita'."""
    return DerivedGap(channel, coin,
                      BASE_NS + int(start_s * NS), BASE_NS + int(end_s * NS))


def win_s(start_s: float, end_s: float, **kw) -> gapwindows.Window:
    return window(BASE_MS + int(start_s * 1000), BASE_MS + int(end_s * 1000), **kw)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = os.path.join(self.tmp.name, "dati")
        self.addCleanup(self.tmp.cleanup)

    def build(self, p99_multiple=derivedgaps.DEFAULT_P99_MULTIPLE,
              min_gap_s=derivedgaps.DEFAULT_MIN_GAP_S):
        con = dataset.connect(os.path.join(self.tmp.name, "tmpdb"))
        self.addCleanup(con.close)
        partitions = dataset.discover(self.data_dir)
        sanity.build_ordered(con, self.data_dir, partitions)
        self.soglie = {
            (r["channel"], r["coin"]): r
            for r in derivedgaps.build_thresholds(con, p99_multiple, min_gap_s)
        }
        self.n_gaps = derivedgaps.build(con)
        self.gaps = derivedgaps.fetch(con)
        self.stats = {(r["channel"], r["coin"]): r for r in derivedgaps.stats(con)}
        return con


# ---------------------------------------------------------------------------
# 1. La soglia si deriva dalla distribuzione, non e' un numero cablato
# ---------------------------------------------------------------------------


class TestSoglia(Base):
    def test_multiplo_del_p99_della_partizione(self):
        """Due partizioni con cadenze diverse devono avere soglie diverse: e'
        tutto il punto del "derivata dai dati". Con un minimo basso la soglia
        e' 5 x p99 e si legge direttamente dai numeri."""
        write_partition(self.data_dir, "trades", "BTC", regolari(300, step_ns=NS))
        write_partition(self.data_dir, "trades", "ETH", regolari(300, step_ns=10 * NS))
        self.build(min_gap_s=1.0)
        self.assertAlmostEqual(self.soglie[("trades", "BTC")]["threshold_s"], 5.0)
        self.assertAlmostEqual(self.soglie[("trades", "ETH")]["threshold_s"], 50.0)
        self.assertEqual(self.soglie[("trades", "BTC")]["basis"],
                         derivedgaps.BASIS_P99)

    def test_il_minimo_assoluto_fa_da_pavimento(self):
        """Su un canale velocissimo 5 x p99 sono pochi secondi, e pochi secondi
        di silenzio non sono un'interruzione della raccolta."""
        write_partition(self.data_dir, "activeAssetCtx", "BTC",
                        regolari(300, step_ns=NS))
        self.build()
        s = self.soglie[("activeAssetCtx", "BTC")]
        self.assertAlmostEqual(s["threshold_s"], derivedgaps.DEFAULT_MIN_GAP_S)
        self.assertEqual(s["basis"], derivedgaps.BASIS_FLOOR)

    def test_pochi_intervalli_niente_p99(self):
        """Sotto il minimo di osservazioni il p99 e' una singola osservazione
        travestita da percentile: resta il solo pavimento."""
        n = derivedgaps.MIN_INTERVALS_FOR_P99 - 10
        write_partition(self.data_dir, "candle", "BTC",
                        regolari(n, step_ns=100 * NS))
        self.build()
        s = self.soglie[("candle", "BTC")]
        self.assertEqual(s["basis"], derivedgaps.BASIS_TOO_FEW)
        self.assertAlmostEqual(s["threshold_s"], derivedgaps.DEFAULT_MIN_GAP_S)

    def test_i_canali_di_backfill_non_hanno_soglia(self):
        """Un dump REST per riconnessione non ha cadenza: la distanza fra due
        dump e' il funzionamento previsto, non un buco. Niente soglia, quindi
        niente rilevamento e niente esclusione."""
        write_partition(self.data_dir, "backfill_candle", "BTC",
                        regolari(300, step_ns=3600 * NS))
        write_partition(self.data_dir, "l2Book", "BTC", regolari(300))
        self.build()
        self.assertNotIn(("backfill_candle", "BTC"), self.soglie)
        self.assertIn(("l2Book", "BTC"), self.soglie)
        self.assertEqual([g.channel for g in self.gaps], [])


# ---------------------------------------------------------------------------
# 2. Il rilevamento
# ---------------------------------------------------------------------------


class TestRilevamento(Base):
    def test_serie_continua_nessun_falso_buco(self):
        write_partition(self.data_dir, "l2Book", "BTC", regolari(500))
        self.build()
        self.assertEqual(self.n_gaps, 0)
        self.assertEqual(self.stats[("l2Book", "BTC")]["n_gaps"], 0)
        self.assertEqual(self.stats[("l2Book", "BTC")]["total_s"], 0)

    def test_il_buco_reale_dell_8_agosto(self):
        """92,9 s su tutte e quattro le coin di `trades`: e' il caso che
        l'esclusione basata sul registro lasciava dentro. Gli estremi devono
        essere i due messaggi che lo delimitano, non stime."""
        for coin in ("BTC", "ETH", "HYPE", "SOL"):
            write_partition(self.data_dir, "trades", coin,
                            serie_con_buco(BUCO_REALE_S))
        self.build()
        self.assertEqual(self.n_gaps, 4)
        for g in self.gaps:
            self.assertAlmostEqual(g.duration_s, BUCO_REALE_S, places=3)
            self.assertEqual(g.start_ns, BASE_NS + 299 * NS)
        # Stessi estremi su quattro coin: e' una interruzione della raccolta, e
        # il report lo rende leggibile senza deciderlo al posto di chi legge.
        self.assertEqual(len({(g.start_ns, g.end_ns) for g in self.gaps}), 1)

    def test_pausa_sotto_soglia_non_e_un_buco(self):
        """Una pausa di 20 s su un canale a 1 msg/s e' sotto il pavimento di
        30 s: puo' essere il mercato, e il catalogo non la chiama interruzione."""
        write_partition(self.data_dir, "trades", "BTC", serie_con_buco(20.0))
        self.build()
        self.assertEqual(self.n_gaps, 0)

    def test_una_riga_sola_non_produce_niente(self):
        """Nessun intervallo, nessun p99, nessun buco: il caso degenere non
        deve far esplodere la query ne' inventare una soglia."""
        write_partition(self.data_dir, "l2Book", "BTC", regolari(1))
        self.build()
        self.assertEqual(self.n_gaps, 0)


# ---------------------------------------------------------------------------
# 3. La riconciliazione: tre insiemi distinti
# ---------------------------------------------------------------------------


class TestRiconciliazione(unittest.TestCase):
    """Funzione pura: buchi e finestre entrano come oggetti, niente DuckDB.
    I secondi sono relativi all'istante base, cosi' le attese sono esatte."""

    def test_assenza_senza_record_e_non_spiegata(self):
        rec = derivedgaps.reconcile([gap(100, 200)], [])
        self.assertEqual(rec["totali"]["n_non_spiegati"], 1)
        self.assertEqual(rec["totali"]["n_spiegati"], 0)
        r = rec["non_spiegati"][0]
        self.assertEqual((r["channel"], r["coin"]), ("trades", "BTC"))
        self.assertAlmostEqual(r["duration_s"], 100.0)
        self.assertIn("start_utc", r)
        self.assertIn("end_utc", r)

    def test_finestra_altrove_non_spiega(self):
        """Una finestra che non tocca il buco non lo spiega: la riconciliazione
        confronta il tempo, non i conteggi."""
        rec = derivedgaps.reconcile([gap(100, 200)], [win_s(1000, 1100)])
        self.assertEqual(rec["totali"]["n_non_spiegati"], 1)
        self.assertEqual(rec["totali"]["n_finestre_senza_assenza"], 1)

    def test_finestra_piu_corta_produce_una_sottostima(self):
        """Il difetto del registro prima del 14 agosto, misurato: assenza vera
        100 s, finestra registrata 40 s, sottostima 60 s."""
        rec = derivedgaps.reconcile([gap(100, 200)], [win_s(100, 140)])
        self.assertEqual(rec["totali"]["n_spiegati"], 1)
        r = rec["spiegati"][0]
        self.assertAlmostEqual(r["duration_s"], 100.0)
        self.assertAlmostEqual(r["coperto_s"], 40.0)
        self.assertAlmostEqual(r["sottostima_s"], 60.0)
        self.assertAlmostEqual(r["copertura_pct"], 40.0)

    def test_finestra_che_copre_tutto_non_sottostima(self):
        rec = derivedgaps.reconcile([gap(100, 200)], [win_s(90, 210)])
        self.assertAlmostEqual(rec["spiegati"][0]["sottostima_s"], 0.0)
        self.assertAlmostEqual(rec["spiegati"][0]["copertura_pct"], 100.0)

    def test_finestre_sovrapposte_non_contano_due_volte(self):
        """Nel registro vero un `resume` per canale sta dentro la finestra
        aggregata che lo ha generato. Sommando le intersezioni una finestra per
        volta la copertura supererebbe la durata e la sottostima diventerebbe
        negativa: un registro perfetto sembrerebbe migliore del perfetto."""
        rec = derivedgaps.reconcile(
            [gap(100, 200)],
            [win_s(100, 150), win_s(120, 160), win_s(110, 140)],
        )
        r = rec["spiegati"][0]
        self.assertAlmostEqual(r["coperto_s"], 60.0)
        self.assertAlmostEqual(r["sottostima_s"], 40.0)
        self.assertGreaterEqual(r["sottostima_s"], 0.0)

    def test_finestra_senza_assenza_nei_dati(self):
        """Il registro dichiara un'interruzione che nei dati non si vede."""
        rec = derivedgaps.reconcile([], [win_s(100, 700, reason="socket chiusa")])
        self.assertEqual(rec["totali"]["n_finestre_senza_assenza"], 1)
        w = rec["finestre_senza_assenza"][0]
        self.assertAlmostEqual(w["duration_s"], 600.0)
        self.assertEqual(w["reason"], "socket chiusa")

    def test_una_finestra_che_tocca_un_buco_di_un_altro_canale_conta(self):
        """Le finestre sono di processo, non di canale: se la raccolta si e'
        fermata non riceveva niente per nessuno. Una finestra che spiega il buco
        di `l2Book` non va riportata come "senza assenza" solo perche' su
        `trades` quel silenzio non compare."""
        rec = derivedgaps.reconcile(
            [gap(100, 200, channel="l2Book")], [win_s(120, 180)]
        )
        self.assertEqual(rec["totali"]["n_finestre_senza_assenza"], 0)
        self.assertEqual(rec["totali"]["n_spiegati"], 1)

    def test_i_tre_insiemi_sono_una_partizione(self):
        """Ogni buco sta in uno e un solo insieme: se la somma non torna, il
        report ne sta nascondendo qualcuno."""
        gaps = [gap(100, 200), gap(500, 600), gap(900, 1000, coin="ETH")]
        rec = derivedgaps.reconcile(gaps, [win_s(100, 150)])
        t = rec["totali"]
        self.assertEqual(t["n_derivati"], 3)
        self.assertEqual(t["n_spiegati"] + t["n_non_spiegati"], t["n_derivati"])
        self.assertAlmostEqual(t["durata_non_spiegata_s"], 200.0)

    def test_sottostima_per_giorno(self):
        """La qualita' del registro nel tempo: una riga per giorno UTC, che e'
        come si vede se e quando e' migliorata."""
        giorno = 86_400
        rec = derivedgaps.reconcile(
            [gap(100, 200), gap(giorno + 100, giorno + 200)],
            [win_s(100, 150), win_s(giorno + 100, giorno + 190)],
        )
        per_giorno = rec["sottostima_per_giorno"]
        self.assertEqual(len(per_giorno), 2)
        self.assertAlmostEqual(per_giorno[0]["sottostima_totale_s"], 50.0)
        self.assertAlmostEqual(per_giorno[1]["sottostima_totale_s"], 10.0)
        self.assertEqual([r["n"] for r in per_giorno], [1, 1])

    def test_liste_troncate_ma_totali_completi(self):
        """Il report deve stare in uno schermo; i conteggi no."""
        gaps = [gap(i * 1000, i * 1000 + 100) for i in range(1, 20)]
        rec = derivedgaps.reconcile(gaps, [], limit=5)
        self.assertEqual(len(rec["non_spiegati"]), 5)
        self.assertEqual(rec["totali"]["n_non_spiegati"], 19)

    def test_registro_vuoto_e_dati_vuoti(self):
        rec = derivedgaps.reconcile([], [])
        self.assertEqual(rec["totali"]["n_derivati"], 0)
        self.assertEqual(rec["totali"]["sottostima_totale_s"], 0.0)
        self.assertEqual(rec["sottostima_per_giorno"], [])


class TestContrattoDelRegistro(unittest.TestCase):
    """`_gaps.jsonl` non contiene solo open/close: contiene `manual`, `resume`,
    e domani conterra' qualcosa che oggi non esiste. Il contratto e' "tutti i
    record che dichiarano una durata" — e vale anche qui, perche' un evento
    ignorato farebbe risultare non spiegato un buco che il registro spiega."""

    def test_evento_sconosciuto_spiega_lo_stesso(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "_gaps.jsonl")
            with open(path, "w", encoding="utf-8") as f:
                f.write(json.dumps({
                    "event": "blackout_ricostruito_a_mano",
                    "start_ms": BASE_MS + 100_000,
                    "duration_s": 40.0,
                    "reason": "evento che il parser condiviso non conosce",
                }) + "\n")
            windows = gapwindows.load(path, now_ms=BASE_MS + 10**7)
            rec = derivedgaps.reconcile([gap(100, 200)], windows)
            self.assertEqual(rec["totali"]["n_spiegati"], 1)
            self.assertAlmostEqual(rec["spiegati"][0]["sottostima_s"], 60.0)


class TestFinestreFuse(unittest.TestCase):
    """La copertura si calcola per bisect su intervalli disgiunti: se la fusione
    sbaglia, sbaglia ogni sottostima."""

    def test_sovrapposte_e_annidate(self):
        w = [window(1000, 2000), window(1500, 1800), window(1900, 3000)]
        self.assertEqual(gapwindows.merge_windows(w),
                         [(1000 * 10**6, 3000 * 10**6)])

    def test_disgiunte_restano_separate(self):
        self.assertEqual(len(gapwindows.merge_windows(
            [window(1000, 2000), window(5000, 6000)])), 2)

    def test_adiacenti_vengono_fuse(self):
        self.assertEqual(gapwindows.merge_windows(
            [window(1000, 2000), window(2000, 3000)]),
            [(1000 * 10**6, 3000 * 10**6)])

    def test_registro_vuoto(self):
        self.assertEqual(gapwindows.merge_windows([]), [])
        self.assertEqual(gapwindows.merge_spans([]), [])

    def test_merge_spans_non_cambia_unita(self):
        self.assertEqual(gapwindows.merge_spans([(5, 7), (1, 3)]), [(1, 3), (5, 7)])


# ---------------------------------------------------------------------------
# 4. I test sanno fallire
# ---------------------------------------------------------------------------


class TestSannoFallire(Base):
    """Con la logica disattivata le asserzioni di sopra devono esplodere. Se
    passassero lo stesso non starebbero verificando niente."""

    def setUp(self):
        super().setUp()
        write_partition(self.data_dir, "trades", "BTC",
                        serie_con_buco(BUCO_REALE_S))

    def test_col_rilevamento_spento_il_buco_non_esiste(self):
        """Mutazione: soglia irraggiungibile. Nessun buco viene trovato, quindi
        nessuno risulta non spiegato — ed e' esattamente lo stato precedente a
        questa modifica, in cui i buchi li dichiarava solo il registro."""
        self.build(min_gap_s=10_000.0)
        self.assertEqual(self.n_gaps, 0)
        with self.assertRaises(AssertionError):
            self.assertEqual(self.n_gaps, 1)
        rec = derivedgaps.reconcile(self.gaps, [])
        with self.assertRaises(AssertionError):
            self.assertEqual(rec["totali"]["n_non_spiegati"], 1)

        # Con la logica accesa, invece, il buco c'e' e non e' spiegato.
        self.build()
        self.assertEqual(self.n_gaps, 1)
        self.assertEqual(
            derivedgaps.reconcile(self.gaps, [])["totali"]["n_non_spiegati"], 1)

    def test_senza_il_pavimento_una_serie_regolare_diventa_un_colabrodo(self):
        """Mutazione opposta: pavimento e multiplo azzerati. Ogni intervallo
        diventa un buco, e la misura perde ogni significato. E' il motivo per
        cui il minimo assoluto esiste."""
        self.build(p99_multiple=0.0, min_gap_s=0.0)
        self.assertGreater(self.n_gaps, 500)
        with self.assertRaises(AssertionError):
            self.assertEqual(self.n_gaps, 1)

    def test_senza_riconciliazione_una_sottostima_passa_inosservata(self):
        """Mutazione: fidarsi della durata dichiarata invece di misurarla. La
        finestra dice 2 s, l'assenza vera ne dura 92,9: chi guarda solo il
        registro vede un buco trascurabile."""
        self.build()
        g = self.gaps[0]
        corta = window(g.start_ns // 10**6, g.start_ns // 10**6 + 2000)
        self.assertAlmostEqual(corta.duration_s, 2.0)
        with self.assertRaises(AssertionError):
            self.assertAlmostEqual(corta.duration_s, BUCO_REALE_S, places=1)

        rec = derivedgaps.reconcile([g], [corta])
        self.assertAlmostEqual(rec["spiegati"][0]["duration_s"], BUCO_REALE_S,
                               places=1)
        self.assertAlmostEqual(rec["spiegati"][0]["sottostima_s"],
                               BUCO_REALE_S - 2.0, places=1)


if __name__ == "__main__":
    unittest.main()
