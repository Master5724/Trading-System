"""Funding: convenzioni di segno, bordi dei regolamenti, e i dati veri.

Il test che conta davvero e' `TestDieciGiorniSuiDatiReali`: prende il campione
registrato di `activeAssetCtx`, ne deriva la serie oraria, somma i 240
regolamenti di dieci giorni e confronta con due valori indipendenti —

1. una costante scritta qui dentro, calcolata una volta in SQL puro senza
   passare da `costs/`;
2. la serie REST `fundingHistory` presente nello stesso campione, che e'
   un'altra fonte e ha un'altra forma (il timestamp e' gia' l'istante di
   regolamento, quindi non richiede la traslazione oraria che il modello
   applica ai campioni di `activeAssetCtx`).

Il secondo confronto e' quello che verifica l'ALLINEAMENTO. Se il modello
sbagliasse di un'ora — l'errore piu' facile da fare e il piu' difficile da
vedere — la somma resterebbe di ordine giusto e plausibile, ma smetterebbe di
coincidere con la serie REST. `TestLaVerificaSaFallire` lo mostra
esplicitamente sbagliando l'allineamento apposta.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from costs import LONG, SHORT, FundingSeries, IncompleteFunding, settlement_hours
from costs.funding import NS_PER_HOUR
from costs.report import funding_report, funding_window
from tests.costs_fixture import SAMPLE_DIR

# Valori attesi del campione, ottenuti UNA VOLTA con una query SQL che non
# usa `costs/` (vedi il commento in fondo al file). Se un giorno cambiassero,
# o e' cambiato il campione o e' cambiato il modello: in entrambi i casi va
# guardato, non aggiornato d'ufficio.
ATTESO = {
    # coin: (somma dei rate sui 240 regolamenti, primo e ultimo indice orario)
    "BTC": (0.001535795, 496058, 496297),
    "HYPE": (0.0020704108, 496058, 496297),
}
FINESTRA_NS = (1785808800000000000, 1786672800000000000)


def _series(coin: str, unreliable=frozenset()) -> FundingSeries:
    """Serie derivata dal campione, attraverso il percorso vero (`sources`)."""
    from costs import sources
    with tempfile.TemporaryDirectory() as tmp:
        con = sources.connect(os.path.join(tmp, "duck"), memory_limit="512MB")
        try:
            return sources.funding_series(con, SAMPLE_DIR, coin,
                                          unreliable=unreliable)
        finally:
            con.close()


def _rest(coin: str) -> FundingSeries:
    from costs import sources
    with tempfile.TemporaryDirectory() as tmp:
        con = sources.connect(os.path.join(tmp, "duck"), memory_limit="512MB")
        try:
            return sources.rest_funding_series(con, SAMPLE_DIR, coin)
        finally:
            con.close()


class TestRegolamenti(unittest.TestCase):
    """Quali istanti di regolamento cadono in una finestra di detenzione."""

    H = NS_PER_HOUR

    def test_dieci_giorni_sono_duecentoquaranta(self):
        self.assertEqual(len(settlement_hours(0, 240 * self.H)), 240)

    def test_estremo_iniziale_incluso_finale_escluso(self):
        """Chi apre esattamente sull'ora paga quel regolamento; chi chiude
        esattamente sull'ora non lo paga. E' una convenzione dichiarata: sul
        vero exchange l'esito dipende dall'ordine di elaborazione, che non e'
        osservabile."""
        self.assertEqual(list(settlement_hours(0, self.H)), [0])
        self.assertEqual(list(settlement_hours(self.H, self.H)), [])

    def test_finestra_dentro_una_sola_ora_non_paga(self):
        """Aperta alle 10:15 e chiusa alle 10:45: nessun regolamento in mezzo,
        costo zero. Un modello che spalmasse il funding pro-rata inventerebbe
        un costo che l'exchange non addebita."""
        start = 10 * self.H + 15 * 60 * 10**9
        end = 10 * self.H + 45 * 60 * 10**9
        self.assertEqual(len(settlement_hours(start, end)), 0)

    def test_finestra_a_cavallo_di_un_ora_paga_una_volta(self):
        start = 10 * self.H + 59 * 60 * 10**9
        end = 11 * self.H + 1 * 60 * 10**9
        self.assertEqual(list(settlement_hours(start, end)), [11])

    def test_intervallo_invertito(self):
        with self.assertRaises(ValueError):
            settlement_hours(2 * self.H, self.H)


class TestSegni(unittest.TestCase):
    """Rate positivo = i long pagano i short. Un costo positivo e' denaro
    uscito, per entrambi i lati."""

    def setUp(self):
        # Tre regolamenti da 0,01% ciascuno.
        self.s = FundingSeries.from_settlements("X", {1: 0.0001, 2: 0.0001,
                                                      3: 0.0001})
        self.start, self.end = 1 * NS_PER_HOUR, 4 * NS_PER_HOUR

    def test_long_paga(self):
        c = self.s.cost(LONG, 1000.0, self.start, self.end)
        self.assertEqual(c.n_settlements, 3)
        self.assertEqual(c.n_known, 3)
        self.assertAlmostEqual(c.cost, 0.30, places=12)      # 3 x 0,01% x 1000
        self.assertAlmostEqual(c.cost_pct, 0.03, places=12)
        self.assertTrue(c.complete)

    def test_short_incassa(self):
        c = self.s.cost(SHORT, 1000.0, self.start, self.end)
        self.assertAlmostEqual(c.cost, -0.30, places=12)

    def test_rate_negativo_inverte(self):
        s = FundingSeries.from_settlements("X", {1: -0.0002})
        self.assertLess(s.cost(LONG, 1000.0, NS_PER_HOUR, 2 * NS_PER_HOUR).cost, 0)
        self.assertGreater(s.cost(SHORT, 1000.0, NS_PER_HOUR, 2 * NS_PER_HOUR).cost, 0)

    def test_costo_proporzionale_al_notional(self):
        a = self.s.cost(LONG, 1000.0, self.start, self.end)
        b = self.s.cost(LONG, 3000.0, self.start, self.end)
        self.assertAlmostEqual(b.cost, 3 * a.cost, places=12)
        self.assertAlmostEqual(b.cost_frac, a.cost_frac, places=15)


class TestOreMancanti(unittest.TestCase):
    """Un'ora che non c'e' non vale zero: zero e' un rate legittimo."""

    def setUp(self):
        self.s = FundingSeries.from_settlements("X", {1: 0.0001, 3: 0.0001})

    def test_contate_e_dichiarate(self):
        c = self.s.cost(LONG, 1000.0, NS_PER_HOUR, 4 * NS_PER_HOUR)
        self.assertEqual(c.n_settlements, 3)
        self.assertEqual(c.n_known, 2)
        self.assertEqual(c.n_missing, 1)
        self.assertFalse(c.complete)
        self.assertAlmostEqual(c.coverage, 2 / 3, places=12)

    def test_strict_solleva(self):
        with self.assertRaises(IncompleteFunding):
            self.s.cost(LONG, 1000.0, NS_PER_HOUR, 4 * NS_PER_HOUR, strict=True)

    def test_ore_inaffidabili_non_sono_ore_mancanti(self):
        """Distinzione voluta: 'il campione c'e' ma la raccolta era bucata' e
        'campione assente' hanno cause diverse e vanno contate separatamente."""
        s = FundingSeries.from_settlements("X", {1: 0.0001, 2: 0.0001, 3: 0.0001},
                                           unreliable={2})
        c = s.cost(LONG, 1000.0, NS_PER_HOUR, 4 * NS_PER_HOUR)
        self.assertEqual(c.n_unreliable, 1)
        self.assertEqual(c.n_missing, 0)
        self.assertEqual(c.n_known, 2)
        self.assertFalse(c.complete)
        self.assertAlmostEqual(c.cost, 0.20, places=12)   # l'ora esclusa non entra
        self.assertIsNone(s.rate(2))


class TestAllineamento(unittest.TestCase):
    """Il campione osservato durante l'ora H regola all'inizio dell'ora H+1."""

    def test_traslazione_di_un_ora(self):
        s = FundingSeries.from_hourly_samples("X", {10: 0.0003})
        self.assertIsNone(s.rate(10))
        self.assertEqual(s.rate(11), 0.0003)

    def test_le_ore_inaffidabili_traslano_insieme(self):
        s = FundingSeries.from_hourly_samples("X", {10: 0.0003},
                                              unreliable_sample_hours={10})
        self.assertIsNone(s.rate(11))


class TestDieciGiorniSuiDatiReali(unittest.TestCase):
    """Il funding cumulato di una posizione tenuta 10 giorni, sul campione.

    240 regolamenti consecutivi, nessuno mancante: il campione e' stato
    estratto da un periodo continuo apposta, cosi' che un `n_missing > 0` in
    questo test significhi un errore di lettura e non una lacuna nota.
    """

    def test_finestra(self):
        s = _series("BTC")
        self.assertEqual(funding_window(s, 10), FINESTRA_NS)

    def test_long_e_short_sui_valori_attesi(self):
        for coin, (somma, primo, ultimo) in ATTESO.items():
            with self.subTest(coin=coin):
                s = _series(coin)
                start, end = FINESTRA_NS
                lungo = s.cost(LONG, 10_000.0, start, end)
                corto = s.cost(SHORT, 10_000.0, start, end)
                self.assertEqual(lungo.n_settlements, 240)
                self.assertEqual(lungo.n_known, 240)
                self.assertEqual(lungo.n_missing, 0)
                self.assertTrue(lungo.complete)
                self.assertEqual((lungo.first_hour, lungo.last_hour),
                                 (primo, ultimo))
                # Il valore atteso, scritto a mano dopo un calcolo in SQL puro.
                self.assertAlmostEqual(lungo.cost_frac, somma, places=12)
                self.assertAlmostEqual(lungo.cost_pct, somma * 100, places=10)
                # Su 10.000 $ di notional il costo in dollari.
                self.assertAlmostEqual(lungo.cost, somma * 10_000.0, places=8)
                # Lo short e' l'esatto opposto.
                self.assertAlmostEqual(corto.cost, -lungo.cost, places=12)

    def test_il_long_paga_su_entrambe_le_coin(self):
        """Nel periodo campionato il funding e' positivo: tenere long e' un
        costo. Non e' una legge di natura — e' quello che dicono questi dieci
        giorni, e il test lo registra."""
        for coin in ATTESO:
            with self.subTest(coin=coin):
                start, end = FINESTRA_NS
                self.assertGreater(_series(coin).cost(LONG, 100.0, start, end).cost,
                                   0.0)

    def test_coincide_con_la_serie_rest(self):
        """La verifica indipendente. Due fonti diverse, due percorsi diversi,
        stesso numero: sul campione la differenza e' esattamente zero."""
        for coin in ATTESO:
            with self.subTest(coin=coin):
                s, r = _series(coin), _rest(coin)
                d = s.disagreement(r)
                self.assertGreaterEqual(d["n_common"], 240)
                self.assertEqual(d["max_abs_diff"], 0.0)
                start, end = FINESTRA_NS
                self.assertAlmostEqual(s.cost(LONG, 1000.0, start, end).cost,
                                       r.cost(LONG, 1000.0, start, end).cost,
                                       places=12)

    def test_report_completo(self):
        """Il report che finisce a schermo usa lo stesso percorso del modello."""
        rep = funding_report(_series("BTC"), _rest("BTC"), 100.0, 10)
        self.assertTrue(rep["disponibile"])
        self.assertTrue(rep["completa"])
        self.assertEqual(rep["n_regolamenti"], 240)
        self.assertAlmostEqual(rep["long_pct"], ATTESO["BTC"][0] * 100, places=10)
        self.assertAlmostEqual(rep["short_pct"], -ATTESO["BTC"][0] * 100, places=10)
        self.assertAlmostEqual(rep["controllo_rest"]["differenza_pct"], 0.0,
                               places=12)


class TestLaVerificaSaFallire(unittest.TestCase):
    """Gli errori che questo file deve intercettare, provocati apposta."""

    def test_allineamento_sbagliato_di_un_ora(self):
        """L'errore piu' insidioso: usare il campione dell'ora H come rate del
        regolamento dell'ora H. Il totale resta plausibile — cambia di poco,
        perche' il funding e' persistente — ma smette di coincidere con la
        serie REST. Senza il confronto incrociato, nessun test se ne
        accorgerebbe."""
        s = _series("BTC")
        # Ricostruisco la serie SENZA la traslazione: e' l'errore.
        sbagliata = FundingSeries.from_settlements(
            "BTC", {h - 1: r for h, r in s.rates.items()}
        )
        start, end = FINESTRA_NS
        giusto = s.cost(LONG, 1000.0, start, end).cost
        sbagliato = sbagliata.cost(LONG, 1000.0, start, end).cost
        # Plausibile: stesso ordine di grandezza, stesso segno.
        self.assertGreater(sbagliato, 0.0)
        self.assertLess(abs(sbagliato - giusto) / abs(giusto), 0.2)
        # E comunque diverso, sia dal vero sia dalla serie REST.
        self.assertNotAlmostEqual(sbagliato, giusto, places=8)
        self.assertGreater(sbagliata.disagreement(_rest("BTC"))["max_abs_diff"],
                           0.0)

    def test_ore_mancanti_trattate_come_zero(self):
        """Se un'ora assente valesse 0, il costo scenderebbe e `complete`
        resterebbe vero: un backtest su dati bucati produrrebbe un numero
        pulito e falso."""
        vera = FundingSeries.from_settlements("X", {1: 0.0001, 2: 0.0001,
                                                    3: 0.0001})
        bucata = FundingSeries.from_settlements("X", {1: 0.0001, 3: 0.0001})
        a = vera.cost(LONG, 1000.0, NS_PER_HOUR, 4 * NS_PER_HOUR)
        b = bucata.cost(LONG, 1000.0, NS_PER_HOUR, 4 * NS_PER_HOUR)
        self.assertTrue(a.complete)
        self.assertFalse(b.complete)      # <- il flag e' l'unica differenza visibile
        with self.assertRaises(AssertionError):
            self.assertAlmostEqual(a.cost, b.cost, places=8)

    def test_segno_invertito_fra_long_e_short(self):
        """Se il segno fosse invertito, un long su funding positivo
        risulterebbe un incasso: dieci giorni di detenzione sembrerebbero un
        guadagno dello 0,15%."""
        start, end = FINESTRA_NS
        s = _series("BTC")
        lungo = s.cost(LONG, 1000.0, start, end).cost
        corto = s.cost(SHORT, 1000.0, start, end).cost
        self.assertGreater(lungo, 0)
        self.assertLess(corto, 0)
        with self.assertRaises(AssertionError):
            self.assertAlmostEqual(lungo, corto, places=8)


# Come sono stati ottenuti i valori in ATTESO, senza passare da `costs/`:
#
#   WITH s AS (
#     SELECT ts_local_ns // 3600000000000 AS h,
#            arg_max(TRY_CAST(json_extract_string(raw,'$.ctx.funding') AS DOUBLE),
#                    ts_local_ns) AS f
#     FROM read_parquet('tests/fixtures/costs_sample/activeAssetCtx/BTC/date=*/hour=*/*.parquet',
#                       hive_partitioning=false)
#     GROUP BY 1),
#   st AS (SELECT h + 1 AS sh, f FROM s)
#   SELECT count(*), min(sh), max(sh), sum(f)
#   FROM st WHERE sh > (SELECT max(sh) - 240 FROM st);
#
#   -> 240 | 496058 | 496297 | 0.0015357949999999982

if __name__ == "__main__":
    unittest.main()
