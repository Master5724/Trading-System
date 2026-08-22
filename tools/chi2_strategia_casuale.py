"""Il chi quadro della strategia casuale su 1000 ripetizioni.

Analisi una-tantum per la PR #15. Non fa parte di `backtest/`: e' lo strumento
che produce il numero di riferimento stampato da
`tests/test_backtest_avversariali.py`, ed esiste perche' quel numero non deve
essere una costante di cui nessuno sa piu' da dove viene.

**La domanda.** Su 100 ripetizioni la dispersione osservata degli scarti fra
PnL e conto atteso valeva 0,826 volte il sigma teorico: un 32% di varianza
mancante, cioe' -2,3 sigma sul chi quadro. Delle due grandezze una poteva
essere sbagliata — o il motore muove meno di quanto dovrebbe, o `sigma_of`
somma passi che il motore non ha fatto.

**La risposta si legge allargando il campione, non ragionandoci.** Il chi
quadro `sum (scarto_i / sigma_i)^2` ha N gradi di liberta' e deviazione
standard `sqrt(2N)`: se la carenza fosse strutturale resterebbe una frazione
costante, e a N=1000 varrebbe -7 sigma invece di -2,3. Se e' una fluttuazione,
il rapporto torna a 1.

Si stampano anche i progressivi a 100, 200 e 500: e' la stessa famiglia di semi
del test (100+i, 200+i), quindi il primo blocco coincide riga per riga con
quello che stampa la suite.

**Due unita', non due misure.** Per ogni N escono sia il chi quadro ridotto sia
la dispersione, che ne e' la radice: la domanda originale era posta in unita' di
deviazione standard ("0,826 volte il sigma teorico") e il chi quadro sta in
unita' di varianza. Un deficit del 33% sulla varianza e' un deficit del 18%
sulla dispersione, ed e' lo stesso fatto detto due volte. Accanto compare lo
stimatore alternativo (sd campionaria / sigma medio) per quantificare quanto i
due modi di misurare la dispersione si discostano fra loro, che e' un'altra
cosa ancora dal discostamento da 1.

Costa un centinaio di secondi di CPU e nessun accesso ai dati: mercato
sintetico, tutto in memoria. Per questo sta qui e non nella suite.

    python -m tools.chi2_strategia_casuale [N]
"""

from __future__ import annotations

import os
import statistics as st
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.strategies import RandomTaker                       # noqa: E402
from tests import backtest_fixture as fx                          # noqa: E402
from tests.test_backtest_avversariali import (                    # noqa: E402
    BAR_S,
    NOTIONAL,
    decompose,
    run,
    sigma_of,
)

TAPPE = (100, 200, 500)


def tiro(seme_mercato: int, seme_strategia: int) -> tuple[float, float, float]:
    """Scarto, sigma teorico e sigma ricalcolato sugli incrementi osservati.

    Il secondo sigma serve a separare due ipotesi che il primo confonde: se il
    chi quadro fosse basso perche' `var_barra` e' sovrastimata, il rapporto
    calcolato con la varianza MISURATA degli incrementi di mark tornerebbe a 1
    e quello teorico no.
    """
    m = fx.market(n_bars=400, seed=seme_mercato)
    r = run(RandomTaker(m.coin, notional=NOTIONAL, seed=seme_strategia,
                        p_change=0.2), m)
    d = decompose(r)
    righe = r.journal.equity_rows
    pos = [row.get(f"pos_{m.coin}") or 0.0 for row in righe]
    mark = [row[f"mark_{m.coin}"] for row in righe]
    incrementi = [mark[k + 1] - mark[k] for k in range(len(righe) - 1)]
    var_oss = st.variance(incrementi)
    sigma_oss = sum(pos[k] ** 2 * var_oss for k in range(len(incrementi))) ** 0.5
    return d["netto"] + d["conto_atteso"], sigma_of(r, m, BAR_S), sigma_oss


def main(argv: list[str]) -> int:
    n_max = int(argv[1]) if len(argv) > 1 else 1000
    t0 = time.monotonic()
    z, z_oss, scarti, sigmi = [], [], [], []
    for i in range(1, n_max + 1):
        scarto, sigma, sigma_oss = tiro(100 + i, 200 + i)
        z.append(scarto / sigma)
        z_oss.append(scarto / sigma_oss)
        scarti.append(scarto)
        sigmi.append(sigma)
    for n in [t for t in TAPPE if t < n_max] + [n_max]:
        chi2 = sum(x * x for x in z[:n])
        chi2_oss = sum(x * x for x in z_oss[:n])
        print(f"N={n:5d}  chi2 {chi2:9.2f} su {n} gdl  chi2/gdl {chi2 / n:.4f}  "
              f"({(chi2 - n) / (2 * n) ** 0.5:+.2f} sigma)  "
              f"chi2/gdl con varianza misurata {chi2_oss / n:.4f}  "
              f"media z {st.mean(z[:n]):+.4f} ({st.mean(z[:n]) * n ** 0.5:+.3f} "
              f"errori standard)")
        # La stessa cosa in unita' di dispersione invece che di varianza: e' la
        # radice delle prime due colonne. Serve perche' la domanda di partenza
        # era posta cosi' — "0,826 volte il sigma teorico" — e un rapporto fra
        # deviazioni standard e' la radice del rapporto fra varianze, non il
        # rapporto stesso: 0,6697 di chi2/gdl SONO 0,8184 di dispersione, e
        # confondere i due fa sembrare la carenza il doppio di quello che e'.
        #
        # La terza colonna e' lo stimatore alternativo (sd campionaria degli
        # scarti / media dei sigma teorici). Sta qui per far vedere DI QUANTO
        # differisce dal primo: sottrae la media campionaria e usa un sigma solo
        # per tutti i tiri, quindi non e' la stessa quantita' e non va messa a
        # confronto con la prima colonna a un altro N.
        media = st.mean(scarti[:n])
        dev = st.stdev(scarti[:n]) if n > 1 else 0.0
        sigma_medio = st.mean(sigmi[:n])
        print(f"        dispersione radice(chi2/N) {(chi2 / n) ** 0.5:.4f}  "
              f"con varianza misurata {(chi2_oss / n) ** 0.5:.4f}  "
              f"sd campionaria / sigma medio {dev / sigma_medio:.4f}  "
              f"(media degli scarti {media:+.4f}, sigma medio {sigma_medio:.4f})")
    print(f"tempo {time.monotonic() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
