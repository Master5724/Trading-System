"""I controlli di coerenza interna del report: girano, e vedono davvero.

**Cosa e' successo, e perche' questi test esistono.** Il report stampava un
blocco per coin, e il confronto fra coin veniva ricopiato a mano in un
documento. Una riga ha preso i numeri di un'altra coin: il funding di BTC e'
diventato quello di HYPE, e il round-trip di BTC e' diventato "due commissioni
tonde" senza spread. Nessuno dei due numeri era assurdo a occhio, e niente nel
report se ne e' accorto.

Un controllo che gira ma che non fallirebbe mai non serve a nulla. Quindi
questi test sono due per ogni controllo:

1. sul campione vero il controllo passa;
2. perturbando il numero che nella realta' e' stato sbagliato — la riga presa
   da un'altra coin, il round-trip senza spread — il controllo FALLISCE.

Il secondo e' quello che conta: dimostra che il controllo distingue, invece di
essere un `assertTrue(True)` travestito.

Tutto gira su `tests/fixtures/costs_sample/`, committata. Nessun test legge
`/home/ubuntu/hl-data`.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from costs import coherence, crosscheck
from costs.model import CostModel
from costs.report import funding_report, slippage_over_books
from tests.costs_fixture import SAMPLE_DIR, sample_available

NOTIONALS = [100.0, 500.0, 2000.0]
GIORNI = 10

# Le due coin per cui il campione ha SIA il book SIA il funding: sono le sole
# su cui l'intero insieme dei controlli e' esercitabile.
COINS = ("BTC", "HYPE")


def _connect(tmp: str):
    from costs import sources
    return sources.connect(os.path.join(tmp, "duck"), memory_limit="512MB")


def _materiale() -> dict:
    """Le stesse strutture che il report passa ai controlli, dal campione.

    Percorso identico a quello di produzione (`sources` -> `report` ->
    `crosscheck`): se un giorno il report cambiasse forma senza aggiornare i
    controlli, questo test se ne accorgerebbe prima dei dati veri.

    La maschera dei buchi e' spenta: sul campione i buchi derivati non sono
    quelli della produzione, e cio' che qui si verifica e' la coerenza fra i
    due percorsi, non l'esclusione.
    """
    from costs import sources

    model = CostModel()
    out: dict = {"coin": {}}
    with tempfile.TemporaryDirectory() as tmp:
        con = _connect(tmp)
        try:
            for coin in COINS:
                books = sources.sample_books(con, SAMPLE_DIR, coin, every_s=60)
                sizes, execution = slippage_over_books(model, books, NOTIONALS)
                series = sources.funding_series(con, SAMPLE_DIR, coin)
                rest = sources.rest_funding_series(con, SAMPLE_DIR, coin)
                funding = funding_report(series, rest, NOTIONALS[0], GIORNI)
                out["coin"][coin] = {"sizes": sizes, "esecuzione": execution,
                                     "funding": funding}
            rows = crosscheck.run(con, SAMPLE_DIR, days=GIORNI,
                                  coins=list(COINS), apply_gap_mask=False)
        finally:
            con.close()
    out["cross"] = {r["coin"]: r for r in rows}
    return out


class TestCoerenzaSulCampione(unittest.TestCase):
    """Sul campione registrato ogni identita' del report tiene."""

    @classmethod
    def setUpClass(cls):
        if not sample_available():
            raise AssertionError(f"campione assente: {SAMPLE_DIR}")
        cls.m = _materiale()

    def _checks(self, coin: str, **override) -> list[coherence.Check]:
        d = self.m["coin"][coin]
        funding = {**d["funding"], **override.pop("funding", {})}
        execution = {**d["esecuzione"], **override.pop("esecuzione", {})}
        cross = override.pop("cross", self.m["cross"].get(coin))
        assert not override, override
        return coherence.check_coin(coin, execution, d["sizes"], funding, cross)

    def test_tutto_coerente(self):
        for coin in COINS:
            with self.subTest(coin=coin):
                checks = self._checks(coin)
                falliti = [c.nome for c in checks if not (c.passato and c.eseguito)]
                self.assertEqual(falliti, [], f"{coin}: {falliti}")
                self.assertTrue(coherence.all_ok(checks))

    def test_ci_sono_controlli_di_ogni_tipo(self):
        """Un `all_ok` su una lista vuota sarebbe vero e non direbbe niente."""
        nomi = {c.nome for c in self._checks("BTC")}
        self.assertIn("identita del round-trip, per snapshot", nomi)
        self.assertIn("round-trip taker = 2 commissioni + slippage", nomi)
        self.assertIn("funding tabella = funding cross-check", nomi)
        self.assertIn("funding di costs = funding di catalog (stessa finestra)",
                      nomi)

    def test_l_identita_per_snapshot_e_esatta_all_ultimo_bit(self):
        """`total` e `fee + spread + impact` sono gli stessi addendi.

        Non vengono sommati nello stesso ORDINE (`total` somma entrata e uscita,
        `fee + slippage` somma prima le commissioni fra loro), quindi il residuo
        non e' zero esatto ma l'errore di associativita' in doppia precisione:
        misurato 1,5e-16 sul campione, contro una tolleranza di 1e-12. Se un
        giorno crescesse di quattro ordini di grandezza vorrebbe dire che il
        round-trip ha cominciato a contare qualcosa che le componenti non
        contengono — ed e' proprio cio' che nessuno noterebbe guardando le
        mediane.
        """
        for coin in COINS:
            with self.subTest(coin=coin):
                res = self.m["coin"][coin]["esecuzione"][
                    "round_trip_identita_residuo_max_rel"]
                self.assertLessEqual(res, 1e-15)

    def test_la_tolleranza_fra_mediane_e_derivata_e_piccola(self):
        """La tolleranza dell'identita' fra mediane non e' un numero scelto.

        E' lo scarto massimo fra la commissione pagata (sul notional ESEGUITO,
        che dipende dallo slippage dei due lati) e due commissioni sul notional
        nominale. Sul campione vale 1,3e-6 punti percentuali su BTC e 3,7e-6 su
        HYPE: circa cento volte meno della cifra meno significativa che il
        report stampa (1e-4 punti). Il limite qui e' 1e-5 — se un giorno lo
        superasse, il controllo diventerebbe permissivo rispetto a cio' che si
        legge, e questo test lo direbbe prima che accada in silenzio.
        """
        for coin in COINS:
            with self.subTest(coin=coin):
                tol = self.m["coin"][coin]["esecuzione"][
                    "round_trip_taker_fee_scarto_max_pct"]
                self.assertGreater(tol, 0.0)
                self.assertLess(tol, 1e-5)


class TestCoerenzaVedeGliErroriVeri(unittest.TestCase):
    """Le perturbazioni sono quelle realmente osservate sulla PR #12."""

    @classmethod
    def setUpClass(cls):
        if not sample_available():
            raise AssertionError(f"campione assente: {SAMPLE_DIR}")
        cls.m = _materiale()

    def _fallisce(self, checks: list[coherence.Check], nome: str) -> None:
        c = next(c for c in checks if c.nome == nome)
        self.assertFalse(c.passato, f"il controllo '{nome}' non ha visto niente")
        self.assertFalse(coherence.all_ok(checks))
        testo = "\n".join(coherence.format_checks(checks))
        self.assertIn("REPORT INCOERENTE", testo)
        self.assertIn(nome, testo)

    def test_la_riga_di_una_coin_prende_il_funding_di_un_altra(self):
        """Il difetto osservato: BTC stampato col funding di HYPE."""
        d = self.m["coin"]["BTC"]
        altrui = self.m["coin"]["HYPE"]["funding"]["long_pct"]
        self.assertNotAlmostEqual(d["funding"]["long_pct"], altrui, places=6)
        funding = {**d["funding"], "long_pct": altrui}
        checks = coherence.check_coin("BTC", d["esecuzione"], d["sizes"],
                                      funding, self.m["cross"]["BTC"])
        self._fallisce(checks, "funding tabella = funding cross-check")

    def test_il_round_trip_perde_lo_spread(self):
        """L'altro difetto osservato: round-trip pari alle sole commissioni."""
        d = self.m["coin"]["BTC"]
        solo_fee = d["esecuzione"]["round_trip_taker_fee_riferimento_pct"]
        execution = {**d["esecuzione"], "round_trip_taker_pct_p50": solo_fee}
        checks = coherence.check_coin("BTC", execution, d["sizes"],
                                      d["funding"], self.m["cross"]["BTC"])
        self._fallisce(checks, "round-trip taker = 2 commissioni + slippage")

    def test_uno_scarto_di_un_millesimo_di_bps_basta_a_farlo_fallire(self):
        """La tolleranza e' stretta davvero, non solo dichiarata tale.

        1e-5 punti percentuali sono 0,001 bps: un decimo della cifra meno
        significativa che il report stampa, e dieci volte la tolleranza
        derivata su BTC (1,3e-6). Se il controllo passasse anche cosi', non
        starebbe verificando niente di utile.
        """
        d = self.m["coin"]["BTC"]
        rt = d["esecuzione"]["round_trip_taker_pct_p50"]
        execution = {**d["esecuzione"], "round_trip_taker_pct_p50": rt + 1e-5}
        checks = coherence.check_coin("BTC", execution, d["sizes"],
                                      d["funding"], self.m["cross"]["BTC"])
        self._fallisce(checks, "round-trip taker = 2 commissioni + slippage")

    def test_un_ora_di_funding_di_scarto_e_vista(self):
        """Il caso realistico: due esecuzioni a cavallo di un'ora.

        E' la differenza che il vecchio report aveva davvero fra la tabella e
        il cross-check quando i due comandi giravano separati (0,1834 % contro
        0,1824 %). Non e' un errore di modello, ma nemmeno una cosa che si puo'
        stampare come se i due numeri fossero lo stesso.
        """
        d = self.m["coin"]["BTC"]
        funding = {**d["funding"],
                   "long_pct": d["funding"]["long_pct"] + 1e-3}
        checks = coherence.check_coin("BTC", d["esecuzione"], d["sizes"],
                                      funding, self.m["cross"]["BTC"])
        self._fallisce(checks, "funding tabella = funding cross-check")

    def test_senza_cross_check_il_controllo_non_e_passato_ma_saltato(self):
        """Un controllo che non gira non deve assomigliare a uno che passa."""
        d = self.m["coin"]["BTC"]
        checks = coherence.check_coin("BTC", d["esecuzione"], d["sizes"],
                                      d["funding"], None)
        c = next(c for c in checks
                 if c.nome == "funding tabella = funding cross-check")
        self.assertFalse(c.eseguito)
        self.assertEqual(c.esito, "NON VERIFICATO")
        self.assertFalse(coherence.all_ok(checks))
        testo = "\n".join(coherence.format_checks(checks))
        self.assertIn("NON VERIFICATO", testo)

    def test_long_e_short_scambiati_di_riga(self):
        """Se lo short di una coin finisse nella riga di un'altra, la somma
        long+short smetterebbe di essere zero."""
        d = self.m["coin"]["BTC"]
        funding = {**d["funding"],
                   "short_pct": self.m["coin"]["HYPE"]["funding"]["short_pct"]}
        checks = coherence.check_coin("BTC", d["esecuzione"], d["sizes"],
                                      funding, self.m["cross"]["BTC"])
        self._fallisce(checks, "funding long + short = 0 nella tabella")


if __name__ == "__main__":
    unittest.main()
