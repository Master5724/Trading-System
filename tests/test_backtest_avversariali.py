"""I test che CLAUDE.md chiama "l'unico motivo per cui potrai credere a un
risultato che ti piace".

Sono cinque, piu' le regole di esecuzione che li rendono sensati. Ognuno
stampa i NUMERI che ha ottenuto — non solo verde o rosso: un test che passa
con un margine di un millesimo dice una cosa diversa da uno che passa con un
margine di dieci volte, e la differenza si vede solo se il numero e' scritto.

I controlli negativi — la prova che ogni test sia capace di fallire — stanno
in `tests/test_backtest_mutazioni.py`.
"""

from __future__ import annotations

import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from costs import Side

from backtest import BlockedHours, Engine, EngineConfig, Order, OrderKind
from backtest.orders import Reject
from backtest.strategies import (
    AlwaysLong,
    Flat,
    LabelFollower,
    PeeksAhead,
    RandomTaker,
)
from backtest.view import LookAheadError
from tests import backtest_fixture as fx

BAR_S = 60
NOTIONAL = 1_000.0


def run(strategy, m: fx.Market, bar_s: int = BAR_S, rate: float = 0.0,
        blocked: BlockedHours | None = None, **kw):
    cfg = EngineConfig(bar_s=bar_s, **kw)
    engine = Engine(cfg, strategy, [m.coin],
                    funding={m.coin: fx.funding(m.coin, rate=rate)},
                    blocked=blocked)
    return engine.run(iter(m.events), m.start_ns, m.end_ns)


def decompose(r) -> dict:
    """PnL scomposto nelle voci che il giornale registra.

    `lordo_al_mid` e' il PnL che si sarebbe avuto eseguendo al mid: e' la somma
    del realizzato piu' il mezzo spread e l'impatto, che nel prezzo di
    esecuzione ci sono gia' dentro. E' la quantita' con valore atteso zero su
    una martingala, ed e' quella su cui si misura l'errore statistico.
    """
    j = r.journal
    realized = j.total("realized_pnl", "fill")
    fee = j.total("fee", "fill")
    spread = j.total("spread_cost", "fill")
    impact = j.total("impact_cost", "fill")
    return {
        "realizzato": realized, "fee": fee, "spread": spread, "impact": impact,
        "conto_atteso": fee + spread + impact,
        "lordo_al_mid": realized + spread + impact,
        "netto": r.final_equity - r.initial_equity,
    }


def sigma_of(r, m: fx.Market, bar_s: int = BAR_S) -> float:
    """Deviazione standard del PnL lordo, dato il percorso di posizione tenuto.

    La posizione della barra k e' nota e indipendente dagli incrementi futuri,
    quindi la varianza totale e' la somma di `pos_k^2 * var_barra`. Non e' una
    stima sui dati: e' il numero che la passeggiata ha per costruzione.
    """
    var_bar = (m.step_sigma ** 2) * (bar_s / m.dt_s)
    total = 0.0
    for row in r.journal.equity_rows:
        pos = row.get(f"pos_{m.coin}") or 0.0
        total += (pos ** 2) * var_bar
    return total ** 0.5


def marks(r, m: fx.Market) -> list[float]:
    return [row[f"mark_{m.coin}"] for row in r.journal.equity_rows]


class TestStrategiaNulla(unittest.TestCase):
    """Segnale sempre piatto: PnL esattamente 0, zero fee.

    "Esattamente" e' letterale — uguaglianza fra float, non `almostEqual`. Un
    motore che sposta un centesimo senza che nessuno abbia mandato un ordine
    ha un difetto, e la tolleranza sarebbe il posto in cui nasconderlo.
    """

    def test_pnl_esattamente_zero_e_nessuna_commissione(self) -> None:
        m = fx.market(n_bars=120, seed=1)
        r = run(Flat(), m, rate=1.25e-05)
        print(f"\n[nulla] equity {r.initial_equity!r} -> {r.final_equity!r}  "
              f"PnL {r.pnl!r}  fee {r.fees_paid!r}  funding {r.funding_paid!r}  "
              f"fill {r.n_fills}  ordini {r.n_orders}  barre {r.n_bars}")
        self.assertEqual(r.final_equity, r.initial_equity)
        self.assertEqual(r.pnl, 0.0)
        self.assertEqual(r.fees_paid, 0.0)
        self.assertEqual(r.funding_paid, 0.0)
        self.assertEqual(r.n_fills, 0)
        self.assertEqual(r.n_orders, 0)
        self.assertEqual(r.journal.count("fill"), 0)
        self.assertTrue(all(row["equity"] == r.initial_equity
                            for row in r.journal.equity_rows))


class TestStrategiaCasuale(unittest.TestCase):
    """Segnale casuale: il PnL deve valere meno il conto, entro l'errore.

    Il "conto" non e' solo la commissione: un taker paga anche mezzo spread, e
    quello sta dentro il prezzo di esecuzione. Confrontare il PnL netto con le
    sole fee farebbe apparire il motore pessimista di una quantita' pari allo
    spread, e la tentazione successiva sarebbe di correggere il motore.
    """

    SEED_MERCATO = 3
    SEED_STRATEGIA = 11
    # Cento e non venti. Con venti la media degli scarti e' uscita a +2,09
    # errori standard: dentro la soglia, ma abbastanza vicino al bordo da non
    # poter dire "compatibile con zero" e passare oltre. La risposta a un
    # campione troppo piccolo e' allargarlo, non ripescare i semi: la famiglia
    # e' la stessa (100+i, 200+i), estesa da i<=20 a i<=100. Il progressivo
    # stampato dal test mostra il t che scende da +2,09 a +0,90, che e' il
    # comportamento di una media che tende a zero, non di un bias.
    RIPETIZIONI = 100

    def _tiro(self, seed_mercato: int, seed_strategia: int):
        """Un mercato e una strategia: restituisce scarto, sigma e scomposizione.

        `scarto` e' PnL netto meno il conto atteso col segno cambiato. Se il
        motore e' corretto ha valore atteso zero, perche' coincide col PnL
        lordo al mid di una martingala.
        """
        m = fx.market(n_bars=400, seed=seed_mercato)
        r = run(RandomTaker(m.coin, notional=NOTIONAL, seed=seed_strategia,
                            p_change=0.2), m)
        d = decompose(r)
        s = sigma_of(r, m)
        return d["netto"] + d["conto_atteso"], s, d, r

    def test_su_cento_ripetizioni_la_media_degli_scarti_e_zero(self) -> None:
        """Un tiro solo non dimostra niente: con sigma 11 un valore compatibile
        col caso lo sarebbe anche parecchio piu' in la'. Qui si guarda la MEDIA
        degli scarti su cento mercati e cento strategie diversi, il cui errore
        standard e' sigma/radice(N) — dieci volte piu' stretto del singolo."""
        scarti, sigmi = [], []
        for i in range(1, self.RIPETIZIONI + 1):
            scarto, s, _, _ = self._tiro(100 + i, 200 + i)
            scarti.append(scarto)
            sigmi.append(s)
        media = sum(scarti) / len(scarti)
        var = sum((x - media) ** 2 for x in scarti) / (len(scarti) - 1)
        dev = var ** 0.5
        sigma_medio = sum(sigmi) / len(sigmi)
        errore_standard = sigma_medio / self.RIPETIZIONI ** 0.5
        print(f"\n[casuale x{self.RIPETIZIONI}] media degli scarti "
              f"{media!r}, deviazione standard campionaria {dev!r}, "
              f"sigma teorico medio {sigma_medio!r}")
        print(f"[casuale x{self.RIPETIZIONI}] media in unita' di sigma: "
              f"{media / sigma_medio:+.4f} sigma del singolo tiro, "
              f"{media / errore_standard:+.3f} errori standard della media "
              f"(errore standard {errore_standard!r})")
        for n in (20, 50, self.RIPETIZIONI):
            mu = sum(scarti[:n]) / n
            es = (sum(sigmi[:n]) / n) / n ** 0.5
            print(f"[casuale] progressivo N={n:3d}: media {mu:+9.4f}, "
                  f"errore standard {es:7.4f}, t {mu / es:+6.3f}")
        # Il campione e' stato esteso DOPO aver visto il t di 20 ripetizioni
        # (+2,09). L'estensione e' legittima solo se dichiarata cosi': i due
        # blocchi separati, non il solo totale. Se il blocco aggiunto avesse un
        # t molto diverso dal primo, l'estensione avrebbe cambiato il
        # risultato invece di precisarlo.
        for nome, campione in (("primo blocco (1-20)", scarti[:20]),
                               ("blocco aggiunto (21-100)", scarti[20:]),
                               ("totale (1-100)", scarti)):
            n = len(campione)
            mu = sum(campione) / n
            es = sigma_medio / n ** 0.5
            print(f"[casuale] {nome:24s} N={n:3d} media {mu:+9.4f} t {mu / es:+6.3f}")
        # Terza statistica, indipendente dalla media: ogni scarto diviso per il
        # SUO sigma da' un chi quadro con N gradi di liberta'. Misura se la
        # dispersione e' quella prevista senza passare per il sigma medio, che
        # varia da tiro a tiro. Stampato e non asserito di proposito: su questa
        # famiglia di semi vale 66,97 (-2,3 sigma), e su 1000 ripetizioni della
        # stessa famiglia 1017,9 (+0,4 sigma) — la carenza di varianza a 100 e'
        # una fluttuazione, e una soglia scelta ora sarebbe scelta su di essa.
        chi2 = sum((x / s) ** 2 for x, s in zip(scarti, sigmi))
        print(f"[casuale] chi quadro sum (scarto/sigma)^2 = {chi2!r} su "
              f"{self.RIPETIZIONI} gradi di liberta' "
              f"({(chi2 - self.RIPETIZIONI) / (2 * self.RIPETIZIONI) ** 0.5:+.2f} "
              f"sigma), dispersione osservata / sigma teorico "
              f"{dev / sigma_medio:.4f}")
        # Stessa forma dello shuffle: la media deve stare dentro tre errori
        # standard, dove l'errore standard viene dalla varianza NOTA della
        # passeggiata, non dai numeri appena visti.
        self.assertLess(abs(media), 3.0 * errore_standard)
        # E la dispersione osservata deve essere quella prevista, non un
        # decimo: una media a zero ottenuta perche' il motore non muove niente
        # passerebbe il controllo sopra e fallisce questo.
        self.assertGreater(dev, 0.3 * sigma_medio)
        self.assertLess(dev, 3.0 * sigma_medio)

    def test_i_passi_del_sigma_sono_quelli_che_il_motore_ha_davvero_fatto(
            self) -> None:
        """Il sigma teorico conta gli stessi passi che il motore ha percorso?

        La domanda nasce da un numero: su 100 ripetizioni la dispersione
        osservata degli scarti era 0,826 volte il sigma teorico — un 32% di
        varianza mancante. Delle due grandezze una poteva essere sbagliata, e la
        prima cosa da escludere e' che `sigma_of` sommi passi che non esistono:
        somma `pos^2 * var_barra` su TUTTE le righe di equity, ma solo le righe
        con posizione aperta e con una barra successiva contribuiscono davvero.

        Qui si confrontano i due conteggi sullo stesso tiro. Non e' una soglia:
        e' un'uguaglianza fra numeri interi.
        """
        scarto, s, d, r = self._tiro(self.SEED_MERCATO, self.SEED_STRATEGIA)
        m = fx.market(n_bars=400, seed=self.SEED_MERCATO)
        righe = r.journal.equity_rows
        pos = [row.get(f"pos_{m.coin}") or 0.0 for row in righe]
        mark = [row[f"mark_{m.coin}"] for row in righe]
        # Un passo "effettivo" e' una barra in cui il motore teneva una
        # posizione E in cui esisteva la barra dopo su cui guadagnarla o
        # perderla: l'ultima riga non ha un incremento davanti a se'.
        effettivi = sum(1 for k in range(len(righe) - 1) if pos[k] != 0.0)
        nel_sigma = sum(1 for p in pos if p != 0.0)
        flat = sum(1 for p in pos if p == 0.0) / len(pos)
        percorso = sum(pos[k] * (mark[k + 1] - mark[k])
                       for k in range(len(righe) - 1))
        print(f"\n[varianza] righe di equity {len(righe)}, incrementi di mark "
              f"{len(righe) - 1}, passi effettivi {effettivi}, passi nel sigma "
              f"teorico {nel_sigma}, frazione flat {flat:.4f}")
        print(f"[varianza] PnL di percorso sum(pos*dmark) {percorso!r} vs "
              f"lordo_al_mid {d['lordo_al_mid']!r}, differenza "
              f"{percorso - d['lordo_al_mid']!r}")
        # I due conteggi coincidono, e la ragione e' strutturale: l'ultima riga
        # e' sempre piatta perche' il motore chiude d'ufficio a fine run. Se
        # quella chiusura sparisse, questa uguaglianza diventerebbe rossa.
        self.assertEqual(effettivi, nel_sigma)
        self.assertEqual(pos[-1], 0.0)
        # E il PnL che il giornale registra E' il PnL del percorso, riga per
        # riga: se non lo fosse, confrontarlo con un sigma calcolato sul
        # percorso non avrebbe senso.
        self.assertAlmostEqual(percorso, d["lordo_al_mid"], places=6)

    def test_pnl_vale_meno_il_conto_entro_tre_sigma(self) -> None:
        scarto, s, d, r = self._tiro(self.SEED_MERCATO, self.SEED_STRATEGIA)
        m = fx.market(n_bars=400, seed=self.SEED_MERCATO)
        atteso = -d["conto_atteso"]
        print(f"\n[casuale] seed mercato {self.SEED_MERCATO}, seed strategia "
              f"{self.SEED_STRATEGIA}, barre {r.n_bars}, ordini {r.n_orders}, "
              f"fill {r.n_fills}")
        print(f"[casuale] netto {d['netto']!r}  atteso {atteso!r}  "
              f"scarto {scarto!r} (netto peggiore dell'atteso del "
              f"{100.0 * abs(scarto / atteso):.2f}% del conto) "
              f"= {scarto / s:.3f} sigma (sigma {s!r})")
        print(f"[casuale] fee {d['fee']!r}  spread {d['spread']!r}  "
              f"impact {d['impact']!r}  lordo_al_mid {d['lordo_al_mid']!r}")
        # Nessuna soglia inventata sul numero di fill: su un mercato sano ogni
        # ordine della strategia diventa un fill, e l'unico fill in piu' e' la
        # chiusura di fine run, che ordine non e'. E' un fatto verificabile,
        # invece di un numero scelto dopo aver visto il risultato.
        di_strategia = [x for x in r.journal.rows
                        if x["event"] == "fill" and x["tag"] == "random"]
        chiusure = [x for x in r.journal.rows
                    if x["event"] == "fill" and x["tag"] == "chiusura_fine_run"]
        self.assertGreater(r.n_orders, 0)
        self.assertEqual(len(di_strategia), r.n_orders)
        self.assertEqual(r.n_fills, r.n_orders + len(chiusure))
        self.assertEqual(r.journal.count("reject"), 0)
        # Lo scarto fra netto e conto atteso E' il PnL lordo: se le due
        # scomposizioni non coincidessero, il giornale non descriverebbe il
        # conto.
        self.assertAlmostEqual(scarto, d["lordo_al_mid"], places=6)
        self.assertLess(abs(scarto), 3.0 * s,
                        "il PnL lordo si scosta da zero di piu' di tre sigma")
        self.assertGreater(d["conto_atteso"], 0.0)
        self.assertLess(d["netto"], 0.0, "una strategia casuale ha guadagnato")


class TestEtichetteMescolate(unittest.TestCase):
    """Con le etichette vere l'edge c'e'; mescolate, deve sparire.

    Il primo dei due e' importante quanto il secondo: se il motore non
    riuscisse a rappresentare nemmeno un edge costruito a tavolino, il fatto
    che lo shuffle azzeri il risultato non direbbe niente.
    """

    RIPETIZIONI = 20

    def _labels(self, m: fx.Market) -> dict[int, float]:
        """Etichetta della barra k: il rendimento della barra SUCCESSIVA."""
        px = marks(run(Flat(), m), m)
        return {k: (px[k + 1] - px[k]) for k in range(len(px) - 1)}

    def _t_di(self, m: fx.Market, labels: dict[int, float]) -> tuple[float, dict]:
        """Il t del PnL lordo: quante deviazioni standard sopra lo zero."""
        r = run(LabelFollower(m.coin, labels, notional=NOTIONAL), m)
        d = decompose(r)
        return d["lordo_al_mid"] / sigma_of(r, m), d

    def test_lo_shuffle_azzera_il_vantaggio(self) -> None:
        """Una permutazione sola non basta: con `t` distribuito attorno a zero,
        un singolo campione sotto la soglia potrebbe essere fortuna. Se ne
        fanno `RIPETIZIONI` e si guarda il peggiore."""
        m = fx.market(n_bars=400, seed=5)
        vere = self._labels(m)
        t_vere, d_v = self._t_di(m, vere)
        ts = []
        for seed in range(1, self.RIPETIZIONI + 1):
            mescolate = dict(zip(vere.keys(),
                                 _shuffled(list(vere.values()), seed)))
            t, _ = self._t_di(m, mescolate)
            ts.append(t)
        peggiore = max(abs(t) for t in ts)
        media = sum(ts) / len(ts)
        print(f"\n[shuffle] etichette vere: lordo {d_v['lordo_al_mid']!r}  "
              f"t {t_vere:.2f}  netto {d_v['netto']!r}")
        print(f"[shuffle] {self.RIPETIZIONI} permutazioni: |t| max "
              f"{peggiore:.2f}, media t {media:+.3f} (attesa 0 +/- "
              f"{1 / self.RIPETIZIONI ** 0.5:.3f}), tutti i t "
              f"{[round(t, 2) for t in ts]}")
        self.assertGreater(t_vere, 5.0,
                           "il motore non rappresenta nemmeno un edge finto")
        # Niente soglia assoluta inventata sul t mescolato: si chiede che
        # l'edge vero sia molto piu' grande del piu' grande dei falsi, e che
        # la media dei falsi stia nell'errore standard di N campioni.
        self.assertGreater(t_vere, 3.0 * peggiore,
                           "mescolando le etichette resta un vantaggio")
        self.assertLess(abs(media), 3.0 / self.RIPETIZIONI ** 0.5)


class TestLookAhead(unittest.TestCase):
    """Chiedere un dato all'istante della decisione deve fallire."""

    def test_accesso_a_ts_uguale_alla_decisione_solleva(self) -> None:
        m = fx.market(n_bars=10, seed=2)
        with self.assertRaises(LookAheadError) as ctx:
            run(PeeksAhead(m.coin, offset_ns=0), m)
        print(f"\n[look-ahead] offset 0 ns -> {type(ctx.exception).__name__}\n"
              f"[look-ahead] messaggio: {ctx.exception}")

    def test_accesso_a_ts_maggiore_solleva(self) -> None:
        m = fx.market(n_bars=10, seed=2)
        with self.assertRaises(LookAheadError):
            run(PeeksAhead(m.coin, offset_ns=1), m)

    def test_un_nanosecondo_prima_e_legittimo(self) -> None:
        """Il confine e' stretto da un lato solo: `t - 1` e' passato."""
        m = fx.market(n_bars=10, seed=2)
        r = run(PeeksAhead(m.coin, offset_ns=-1), m)
        print(f"[look-ahead] offset -1 ns -> nessuna eccezione, "
              f"barre decise {r.n_bars_decided}")
        self.assertGreater(r.n_bars_decided, 0)

    def test_la_view_non_espone_mai_un_ts_futuro(self) -> None:
        """Il rivelatore di fuga: dopo ogni decisione il motore verifica che il
        massimo timestamp uscito dalla view sia minore dell'istante di
        decisione. Qui si controlla che una strategia normale lo rispetti."""
        m = fx.market(n_bars=30, seed=2)
        visti: list[tuple[int, int]] = []

        class Spia:
            tag = "spia"

            def decide(self, view):
                ev = view.book_event(m.coin)
                if ev is not None:
                    visti.append((ev.ts_local_ns, view.as_of_ns))
                return ()

        run(Spia(), m)
        self.assertTrue(visti)
        peggiore = max(ts - t for ts, t in visti)
        print(f"\n[look-ahead] {len(visti)} letture del book, distanza massima "
              f"dal futuro {peggiore} ns (deve essere < 0)")
        self.assertLess(peggiore, 0)


class TestConservazione(unittest.TestCase):
    """equity finale == iniziale + realizzato - fee - funding, al centesimo.

    L'identita' si ricalcola dal GIORNALE scritto, non dai contatori del
    portafoglio: e' il file che una persona legge, ed e' li' che deve tornare.
    """

    def test_identita_al_centesimo_con_funding_non_nullo(self) -> None:
        m = fx.market(n_bars=400, seed=7)
        r = run(RandomTaker(m.coin, notional=NOTIONAL, seed=3, p_change=0.15),
                m, rate=1.25e-05)
        j = r.journal
        atteso = (r.initial_equity + j.total("realized_pnl", "fill")
                  - j.total("fee", "fill") - j.total("funding", "funding"))
        residuo = r.final_equity - atteso
        print(f"\n[conservazione] membro sinistro (equity finale)  "
              f"{r.final_equity!r}")
        print(f"[conservazione] membro destro  {r.initial_equity!r} "
              f"+ {j.total('realized_pnl', 'fill')!r} "
              f"- {j.total('fee', 'fill')!r} "
              f"- {j.total('funding', 'funding')!r} = {atteso!r}")
        print(f"[conservazione] differenza {residuo!r}  "
              f"({r.n_fills} fill, {r.n_settlements} regolamenti)")
        self.assertGreater(r.n_settlements, 0)
        self.assertNotEqual(j.total("funding", "funding"), 0.0)
        self.assertEqual(round(residuo, 2), 0.0)
        self.assertEqual(round(r.conservation_residual, 2), 0.0)

    def test_il_run_finisce_piatto(self) -> None:
        m = fx.market(n_bars=60, seed=7)
        r = run(AlwaysLong(m.coin, notional=NOTIONAL), m, rate=1.25e-05)
        ultima = r.journal.equity_rows[-1]
        print(f"\n[conservazione] posizione finale {ultima[f'pos_{m.coin}']!r}, "
              f"chiusure d'ufficio {r.n_forced_close}")
        self.assertEqual(ultima[f"pos_{m.coin}"], 0.0)


class TestRegoleDiEsecuzione(unittest.TestCase):
    """Taker sul book camminato, maker solo per attraversamento."""

    def test_il_taker_cammina_i_livelli(self) -> None:
        """Una size che eccede il primo livello paga il secondo, e l'impatto
        smette di essere zero. Il numero atteso e' scritto a mano."""
        m = fx.market(n_bars=5, seed=1, half_spread=0.01, depth=1.0,
                      with_trades=False)
        size = 1.5   # un livello intero piu' meta' del secondo

        class Uno:
            tag = "uno"

            def __init__(self):
                self.fatto = False

            def decide(self, view):
                if self.fatto or view.book(m.coin) is None:
                    return ()
                self.fatto = True
                return [Order(coin=m.coin, side=Side.BUY, size=size)]

        r = run(Uno(), m)
        fill = [x for x in r.journal.rows if x["event"] == "fill"][0]
        mid = fill["px"] - (fill["spread_cost"] + fill["impact_cost"]) / size
        atteso = (1.0 * (mid + 0.01) + 0.5 * (mid + 0.02)) / 1.5
        print(f"\n[taker] px {fill['px']!r}  atteso {atteso!r}  "
              f"spread {fill['spread_cost']!r}  impact {fill['impact_cost']!r}")
        self.assertAlmostEqual(fill["px"], atteso, places=10)
        self.assertGreater(fill["impact_cost"], 0.0)
        self.assertEqual(fill["liquidity"], "taker")

    def _maker_market(self, delta: float, sz: float = 100.0
                      ) -> tuple[fx.Market, float]:
        """Mercato immobile con UN trade a `limite + delta`, nella seconda barra.

        `delta = 0` e' il caso che conta: il trade tocca il livello senza
        attraversarlo, e non deve riempire niente.
        """
        m = fx.market(n_bars=6, seed=1, sigma=0.0, half_spread=0.01,
                      with_trades=False)
        limite = m.mids[0] - 0.05
        ts = m.start_ns + fx.NS * 90        # dentro la seconda barra
        eventi = list(m.events)
        eventi.append(fx.TradeEvent(ts_local_ns=ts, coin=m.coin,
                                    px=limite + delta, sz=sz, side="A",
                                    tid=42, time_ms=ts // 1_000_000))
        eventi.sort(key=lambda e: e.ts_local_ns)
        return fx.Market(events=eventi, start_ns=m.start_ns, end_ns=m.end_ns,
                         mids=m.mids, step_sigma=m.step_sigma, dt_s=m.dt_s,
                         coin=m.coin), limite

    def _posa_limite(self, limite: float):
        class Posa:
            tag = "posa"

            def __init__(self):
                self.fatto = False

            def decide(self, view):
                if self.fatto or view.book("TEST") is None:
                    return ()
                self.fatto = True
                return [Order(coin="TEST", side=Side.BUY, size=1.0,
                              kind=OrderKind.LIMIT, limit_px=limite,
                              ttl_bars=4)]
        return Posa()

    def test_un_trade_attraverso_il_livello_riempie(self) -> None:
        m2, limite = self._maker_market(-0.001)
        r = run(self._posa_limite(limite), m2)
        print(f"\n[maker] limite {limite!r}  trade {limite - 0.001!r}  "
              f"fill maker {r.n_fills_maker}")
        self.assertEqual(r.n_fills_maker, 1)
        fill = [x for x in r.journal.rows if x["event"] == "fill"][0]
        self.assertEqual(fill["px"], limite)
        self.assertEqual(fill["liquidity"], "maker")
        self.assertEqual(fill["trade_tid"], 42)

    def test_un_trade_che_tocca_il_livello_non_riempie(self) -> None:
        """E' la riga che separa un backtest da una favola."""
        m, limite = self._maker_market(0.0)
        r = run(self._posa_limite(limite), m)
        print(f"[maker] limite {limite!r}  trade esattamente al limite  "
              f"fill maker {r.n_fills_maker}  "
              f"rifiuti {r.rejects}")
        self.assertEqual(r.n_fills_maker, 0)
        self.assertEqual(r.rejects.get(Reject.EXPIRED.value), 1)

    def test_la_size_eseguita_non_supera_quella_passata(self) -> None:
        m2, limite = self._maker_market(-0.001, sz=0.25)
        r = run(self._posa_limite(limite), m2)
        fill = [x for x in r.journal.rows if x["event"] == "fill"][0]
        print(f"[maker] ordine 1.0, trade 0.25 -> eseguito "
              f"{fill['size_filled']!r}")
        self.assertEqual(fill["size_filled"], 0.25)

    def test_un_limite_che_attraversa_il_book_viene_rifiutato(self) -> None:
        m = fx.market(n_bars=4, seed=1, sigma=0.0, with_trades=False)

        class Marketable:
            tag = "mkt"

            def decide(self, view):
                b = view.book(m.coin)
                if b is None:
                    return ()
                return [Order(coin=m.coin, side=Side.BUY, size=1.0,
                              kind=OrderKind.LIMIT, limit_px=b.best_ask + 1.0)]

        r = run(Marketable(), m)
        print(f"\n[maker] limite oltre il best ask -> {r.rejects}")
        self.assertEqual(r.n_fills_maker, 0)
        self.assertGreater(r.rejects.get(Reject.INVALID.value, 0), 0)

    def test_profondita_insufficiente_rifiuta_invece_di_estrapolare(self) -> None:
        m = fx.market(n_bars=4, seed=1, depth=1.0, with_trades=False)

        class Enorme:
            tag = "enorme"

            def decide(self, view):
                if view.book(m.coin) is None:
                    return ()
                return [Order(coin=m.coin, side=Side.BUY, size=10_000.0)]

        r = run(Enorme(), m)
        print(f"[profondita'] rifiuti {r.rejects}  fill {r.n_fills}")
        self.assertEqual(r.n_fills, 0)
        self.assertGreater(r.rejects.get(Reject.INSUFFICIENT_DEPTH.value, 0), 0)

    def test_un_book_troppo_vecchio_rifiuta_il_fill(self) -> None:
        """Gli snapshot si fermano dopo la prima barra: da li' in poi l'eta'
        cresce e supera la soglia."""
        m = fx.market(n_bars=10, seed=1, with_trades=False)
        taglio = m.start_ns + 30 * fx.NS
        eventi = [e for e in m.events if e.ts_local_ns <= taglio]
        m2 = fx.Market(events=eventi, start_ns=m.start_ns, end_ns=m.end_ns,
                       mids=m.mids, step_sigma=m.step_sigma, dt_s=m.dt_s,
                       coin=m.coin)

        class Ogni:
            """Un ordinetto a ogni barra: serve un ordine anche quando il book
            e' vecchio, altrimenti non c'e' niente da rifiutare."""

            tag = "ogni"

            def decide(self, view):
                return [Order(coin=m.coin, side=Side.BUY, size=0.001)]

        r = run(Ogni(), m2, max_book_age_s=30.0)
        eta = [x["book_age_ms"] for x in r.journal.rows
               if x["event"] == "reject"]
        print(f"\n[freschezza] soglia 30s  rifiuti {r.rejects}  "
              f"eta' massima registrata {max(eta) if eta else None} ms")
        self.assertGreater(r.rejects.get(Reject.BOOK_STALE.value, 0), 0)


class TestBuchi(unittest.TestCase):
    """Un'ora inaffidabile non si attraversa fingendo continuita'."""

    def test_la_posizione_viene_chiusa_prima_del_buco(self) -> None:
        m = fx.market(n_bars=180, seed=4)          # tre ore
        bloccata = fx.HOUR0 + 1
        r = run(AlwaysLong(m.coin, notional=NOTIONAL), m, rate=1.25e-05,
                blocked=BlockedHours({m.coin: frozenset({bloccata})}))
        dentro = [row for row in r.journal.equity_rows
                  if bloccata * 3_600 * fx.NS <= row["ts_ns"]
                  < (bloccata + 1) * 3_600 * fx.NS]
        chiusure = [x for x in r.journal.rows
                    if x["event"] == "fill" and "chiusura_forzata" in x["tag"]]
        print(f"\n[buco] ora bloccata {bloccata}  barre dentro {len(dentro)}  "
              f"barre non decise {r.n_bars_all_blocked}  chiusure d'ufficio "
              f"{r.n_forced_close} (fallite {r.n_forced_close_failed})")
        self.assertEqual(len(chiusure), 1)
        self.assertEqual(r.n_forced_close, 1)
        self.assertEqual(r.n_bars_all_blocked, 60)
        self.assertTrue(all(row[f"pos_{m.coin}"] == 0.0 for row in dentro),
                        "posizione aperta dentro un'ora inaffidabile")
        # e dopo il buco si riparte
        dopo = [row for row in r.journal.equity_rows
                if row["ts_ns"] >= (bloccata + 1) * 3_600 * fx.NS]
        self.assertTrue(any(row[f"pos_{m.coin}"] != 0.0 for row in dopo))

    def test_un_ora_senza_rate_di_funding_e_bloccata(self) -> None:
        """Non serve un buco nei book: se il costo di tenere la posizione non
        e' noto, quell'ora non e' negoziabile."""
        from costs import FundingSeries

        m = fx.market(n_bars=120, seed=4)
        parziale = FundingSeries.from_settlements(
            m.coin, {fx.HOUR0: 1.25e-05}
        )
        engine = Engine(EngineConfig(bar_s=BAR_S),
                        AlwaysLong(m.coin, notional=NOTIONAL), [m.coin],
                        funding={m.coin: parziale})
        r = engine.run(iter(m.events), m.start_ns, m.end_ns)
        print(f"[buco] rate noti per 2 ore su 2  barre tutte bloccate "
              f"{r.n_bars_all_blocked}  chiusure {r.n_forced_close}")
        self.assertGreater(r.n_bars_all_blocked, 0)
        self.assertEqual(r.n_funding_unknown, 0)


class TestDeterminismo(unittest.TestCase):
    """Stesso input, stesso output, bit per bit."""

    def test_due_run_producono_gli_stessi_byte(self) -> None:
        m = fx.market(n_bars=200, seed=9)
        a = run(RandomTaker(m.coin, notional=NOTIONAL, seed=5), m,
                rate=1.25e-05)
        b = run(RandomTaker(m.coin, notional=NOTIONAL, seed=5), m,
                rate=1.25e-05)
        print(f"\n[determinismo] digest A {a.digest()}\n"
              f"[determinismo] digest B {b.digest()}  "
              f"righe {len(a.journal.rows)}")
        self.assertEqual(a.digest(), b.digest())
        self.assertEqual(a.journal.journal_csv(), b.journal.journal_csv())
        self.assertEqual(a.journal.equity_csv(), b.journal.equity_csv())
        self.assertEqual(a.final_equity, b.final_equity)

    def test_un_seme_diverso_cambia_il_risultato(self) -> None:
        """Il controllo opposto: se due semi diversi dessero lo stesso digest,
        il test sopra passerebbe anche con un motore che non fa niente."""
        m = fx.market(n_bars=200, seed=9)
        a = run(RandomTaker(m.coin, notional=NOTIONAL, seed=5), m)
        b = run(RandomTaker(m.coin, notional=NOTIONAL, seed=6), m)
        self.assertNotEqual(a.digest(), b.digest())


def _shuffled(values: list[float], seed: int) -> list[float]:
    """Permutazione deterministica. Isolata in una funzione perche' il test
    delle mutazioni la sostituisce con l'identita' per verificare che il test
    dello shuffle sappia diventare rosso."""
    out = list(values)
    random.Random(seed).shuffle(out)
    return out


if __name__ == "__main__":
    unittest.main()
