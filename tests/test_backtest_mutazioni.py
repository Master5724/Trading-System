"""Un test che non puo' fallire non e' un test.

Per ogni controllo avversariale, qui si spegne DI PROPOSITO la logica che
dovrebbe coprire e si verifica che il controllo diventi rosso. Se una di
queste mutazioni non rompe niente, il test corrispondente non stava
verificando quello che il suo nome dice.

E' la versione automatica di "commenta una riga e guarda se diventa rosso"
chiesta da TASKS.md: automatica perche' una verifica fatta a mano una volta
sola smette di valere al primo refactoring.

Ogni test stampa il numero ottenuto con la mutazione attiva, accanto a quello
che si ottiene senza.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from costs import CostModel, ExecutionCost, Side

from backtest import BlockedHours, Engine, Order
from backtest import fills as bt_fills
from backtest.orders import Reject
from backtest.portfolio import Portfolio
from backtest.strategies import AlwaysLong, LabelFollower, RandomTaker
from backtest.view import LookAheadError, MarketView
from tests import backtest_fixture as fx
# Il modulo si importa intero e non per nomi: importare le classi `TestCase`
# le farebbe raccogliere una seconda volta anche da qui, e ogni test
# avversariale girerebbe due volte per niente.
from tests import test_backtest_avversariali as adv

NOTIONAL = adv.NOTIONAL
decompose, run, sigma_of = adv.decompose, adv.run, adv.sigma_of


class Spia:
    """Legge il book a ogni barra. Serve a far uscire un timestamp dalla view."""

    tag = "spia"

    def __init__(self, coin: str) -> None:
        self.coin = coin

    def decide(self, view):
        view.book_event(self.coin)
        return ()


class TestLaBarrieraAntiLookAheadServe(unittest.TestCase):

    def test_senza_il_guardiano_l_accesso_al_futuro_non_solleva(self) -> None:
        """Mutazione: `MarketView._seen` diventa un passacarte.

        Il test anti look-ahead diventa rosso perche' il suo `assertRaises` non
        vede piu' nessuna eccezione. La fuga vera resta impedita un livello
        piu' sotto — i dati futuri non sono in memoria — ma il RIFIUTO
        sparisce, e con esso l'unica cosa che un test possa osservare.
        """
        from backtest.strategies import PeeksAhead

        m = fx.market(n_bars=10, seed=2)
        with patch.object(MarketView, "_seen", lambda self, ts: ts):
            r = run(PeeksAhead(m.coin, offset_ns=0), m)
        print(f"\n[mut:guardiano] nessuna eccezione, barre decise "
              f"{r.n_bars_decided} (senza mutazione: LookAheadError)")
        self.assertGreater(r.n_bars_decided, 0)

    def test_un_taglio_del_feed_non_stretto_viene_intercettato(self) -> None:
        """Mutazione: il feed si consuma fino a `ts <= t` invece che `ts < t`.

        E' l'errore di confine piu' facile da commettere. Il rivelatore di
        fuga scatta subito, perche' nel mercato sintetico esiste uno snapshot
        esattamente a ogni frontiera di barra.
        """
        m = fx.market(n_bars=10, seed=2)
        r_ok = run(Spia(m.coin), m)
        originale = Engine._drain
        with patch.object(Engine, "_drain",
                          lambda self, until: originale(self, until + 1)):
            with self.assertRaises(LookAheadError) as ctx:
                run(Spia(m.coin), m)
        print(f"[mut:taglio] senza mutazione barre decise {r_ok.n_bars_decided}; "
              f"con mutazione {type(ctx.exception).__name__}")


class TestLaRegolaDellAttraversamentoServe(unittest.TestCase):

    def test_con_il_confronto_non_stretto_un_tocco_riempie(self) -> None:
        """Mutazione: `<` diventa `<=` in `fills.crosses`.

        E' la riga citata nella docstring di `backtest/fills.py`. Il test del
        tocco diventa rosso: da zero fill maker si passa a uno.
        """
        caso = adv.TestRegoleDiEsecuzione("run")
        m, limite = caso._maker_market(0.0)
        senza = run(caso._posa_limite(limite), m)

        def tocca(side, limit_px, trade_px):
            return trade_px <= limit_px if side is Side.BUY else trade_px >= limit_px

        with patch.object(bt_fills, "crosses", tocca):
            con = run(caso._posa_limite(limite), m)
        print(f"\n[mut:attraversamento] fill maker senza mutazione "
              f"{senza.n_fills_maker}, con mutazione {con.n_fills_maker}")
        self.assertEqual(senza.n_fills_maker, 0)
        self.assertEqual(con.n_fills_maker, 1)


class TestIlContoDeiCostiServe(unittest.TestCase):

    def test_con_esecuzione_gratis_il_conto_atteso_si_azzera(self) -> None:
        """Mutazione: si esegue al mid, senza commissione ne' spread.

        E' il motore ottimista che il test della strategia casuale esiste per
        smascherare: l'asserzione `conto_atteso > 0` diventa rossa, e con essa
        `netto < 0` perde ogni significato.
        """
        def gratis(self, book, side, size, liquidity):
            return ExecutionCost(
                coin=book.coin, side=side, liquidity=liquidity, size=size,
                notional_nominal=size * book.mid,
                notional_executed=size * book.mid,
                fee=0.0, spread=0.0, impact=0.0, avg_px=book.mid,
                mid=book.mid, fill=None,
            )

        m = fx.market(n_bars=400, seed=3)
        senza = decompose(run(
            RandomTaker(m.coin, notional=NOTIONAL, seed=11, p_change=0.2), m))
        with patch.object(CostModel, "execution_from_size", gratis):
            con = decompose(run(
                RandomTaker(m.coin, notional=NOTIONAL, seed=11, p_change=0.2), m))
        print(f"\n[mut:costi] conto atteso senza mutazione "
              f"{senza['conto_atteso']!r}, con mutazione {con['conto_atteso']!r}; "
              f"netto {senza['netto']!r} -> {con['netto']!r}")
        self.assertGreater(senza["conto_atteso"], 0.0)
        self.assertEqual(con["conto_atteso"], 0.0)


class TestLoShuffleServe(unittest.TestCase):

    def test_con_la_permutazione_identica_il_vantaggio_resta(self) -> None:
        """Mutazione: `_shuffled` restituisce la lista com'e'.

        E' letteralmente il controllo chiesto da TASKS.md. Con l'identita' le
        etichette "mescolate" sono quelle vere, il vantaggio riappare e
        l'asserzione `|lordo| < 3 sigma` diventa rossa.
        """
        m = fx.market(n_bars=400, seed=5)
        caso = adv.TestEtichetteMescolate("run")
        vere = caso._labels(m)
        t_vere, _ = caso._t_di(m, vere)
        with patch.object(adv, "_shuffled", lambda values, seed: list(values)):
            finte = dict(zip(vere.keys(), adv._shuffled(list(vere.values()), 1)))
        t_finto, d = caso._t_di(m, finte)
        print(f"\n[mut:shuffle] con permutazione identica: lordo "
              f"{d['lordo_al_mid']!r}, t {t_finto:.2f} contro t delle etichette "
              f"vere {t_vere:.2f}; il test chiede t_vere > 3 * |t_mescolato|, "
              f"cioe' {t_vere:.2f} > {3.0 * abs(t_finto):.2f}")
        self.assertLess(t_vere, 3.0 * abs(t_finto))


class TestLaContabilitaServe(unittest.TestCase):

    def test_una_fee_non_addebitata_rompe_la_conservazione(self) -> None:
        """Mutazione: la cassa dimentica di pagare la commissione.

        L'identita' di conservazione e' l'unico controllo che se ne accorge:
        equity e giornale smettono di essere d'accordo esattamente della somma
        delle fee.
        """
        originale = Portfolio.apply_fill

        def dimentica(self, coin, signed_size, px, fee):
            realized = originale(self, coin, signed_size, px, fee)
            self.cash += fee
            return realized

        m = fx.market(n_bars=200, seed=7)
        senza = run(RandomTaker(m.coin, notional=NOTIONAL, seed=3), m,
                    rate=1.25e-05)
        with patch.object(Portfolio, "apply_fill", dimentica):
            con = run(RandomTaker(m.coin, notional=NOTIONAL, seed=3), m,
                      rate=1.25e-05)
        print(f"\n[mut:contabilita'] residuo senza mutazione "
              f"{senza.conservation_residual!r}, con mutazione "
              f"{con.conservation_residual!r} (fee totali "
              f"{con.journal.total('fee', 'fill')!r})")
        self.assertEqual(round(senza.conservation_residual, 2), 0.0)
        self.assertNotEqual(round(con.conservation_residual, 2), 0.0)
        self.assertAlmostEqual(con.conservation_residual,
                               con.journal.total("fee", "fill"), places=6)


class TestLaPoliticaSuiBuchiServe(unittest.TestCase):

    def test_senza_chiusura_d_ufficio_la_posizione_attraversa_il_buco(self) -> None:
        """Mutazione: il motore non chiude piu' prima di un'ora inaffidabile."""
        m = fx.market(n_bars=180, seed=4)
        bloccata = fx.HOUR0 + 1
        blocked = BlockedHours({m.coin: frozenset({bloccata})})
        with patch.object(Engine, "_force_close_blocked",
                          lambda self, t, b, blk: None):
            r = run(AlwaysLong(m.coin, notional=NOTIONAL), m, rate=1.25e-05,
                    blocked=blocked)
        dentro = [row for row in r.journal.equity_rows
                  if bloccata * 3_600 * fx.NS <= row["ts_ns"]
                  < (bloccata + 1) * 3_600 * fx.NS]
        aperte = [row for row in dentro if row[f"pos_{m.coin}"] != 0.0]
        print(f"\n[mut:buchi] barre dentro l'ora bloccata {len(dentro)}, di cui "
              f"con posizione aperta {len(aperte)} (senza mutazione: 0)")
        self.assertEqual(len(aperte), len(dentro))


class TestLaSogliaDiFreschezzaServe(unittest.TestCase):

    def test_con_soglia_altissima_un_book_vecchio_di_ore_esegue(self) -> None:
        """Non e' una mutazione del codice ma della config, e prova la stessa
        cosa: senza il limite, il motore esegue su uno snapshot di ore prima."""
        m = fx.market(n_bars=10, seed=1, with_trades=False)
        taglio = m.start_ns + 30 * fx.NS
        eventi = [e for e in m.events if e.ts_local_ns <= taglio]
        m2 = fx.Market(events=eventi, start_ns=m.start_ns, end_ns=m.end_ns,
                       mids=m.mids, step_sigma=m.step_sigma, dt_s=m.dt_s,
                       coin=m.coin)

        class Ogni:
            tag = "ogni"

            def decide(self, view):
                return [Order(coin=m.coin, side=Side.BUY, size=0.001)]

        stretta = run(Ogni(), m2, max_book_age_s=30.0)
        larga = run(Ogni(), m2, max_book_age_s=86_400.0)
        print(f"\n[mut:freschezza] rifiuti con soglia 30s "
              f"{stretta.rejects.get(Reject.BOOK_STALE.value, 0)}, con soglia "
              f"86400s {larga.rejects.get(Reject.BOOK_STALE.value, 0)}; fill "
              f"{stretta.n_fills} -> {larga.n_fills}")
        self.assertGreater(stretta.rejects.get(Reject.BOOK_STALE.value, 0), 0)
        self.assertEqual(larga.rejects.get(Reject.BOOK_STALE.value, 0), 0)
        self.assertGreater(larga.n_fills, stretta.n_fills)


class TestIlDeterminismoServe(unittest.TestCase):

    def test_un_giornale_che_perde_cifre_nasconde_le_differenze(self) -> None:
        """Mutazione: i float nel giornale si scrivono con sei decimali.

        Il digest di due run diversi puo' coincidere pur essendo diversi i
        numeri: e' il motivo per cui `journal` usa `repr` e non un formato a
        cifre fisse. Qui si mostra che il residuo di conservazione, che vale
        1e-12, sparisce del tutto.
        """
        from backtest import journal as jn

        m = fx.market(n_bars=60, seed=9)
        r = run(RandomTaker(m.coin, notional=NOTIONAL, seed=5), m,
                rate=1.25e-05)
        vero = r.journal.journal_csv()
        with patch.object(jn, "_cell",
                          lambda v: "" if v is None else
                          (f"{v:.6f}" if isinstance(v, float) else str(v))):
            troncato = r.journal.journal_csv()
        print(f"\n[mut:determinismo] byte del giornale {len(vero)} -> "
              f"{len(troncato)}; residuo vero {r.conservation_residual!r} "
              f"scritto come {r.conservation_residual:.6f}")
        self.assertNotEqual(vero, troncato)


if __name__ == "__main__":
    unittest.main()
