"""Nessun risultato DuckDB consumato in ritardo, fuori da `backtest/feed.py`.

**Il difetto che ha reso necessario questo file.** Su una connessione DuckDB
esiste un solo result set alla volta: ogni nuova `execute` invalida quello
precedente. Chi apre una query e ne consuma le righe piu' tardi — a blocchi,
o dentro un generatore — riceve righe fino alla prossima `execute` e poi zero,
senza nessuna eccezione. Nel feed del backtester questo ha troncato i trade a
20.000 righe esatte su 108.168, e la simulazione e' proceduta come se i dati
finissero li'.

`costs/` e `catalog/` leggono i trade con DuckDB e i numeri gia' approvati del
modello di costo vengono da quelle letture, quindi la domanda "sono esposti
anche loro?" non si risponde a occhio. Qui si risponde in tre modi:

1. si **riproduce** il troncamento, per fissare per iscritto il comportamento
   di DuckDB su cui tutto il resto si appoggia;
2. si mostra che `fetchall()` ne e' **immune**, perche' materializza le righe
   in Python prima che qualunque altra query possa girare;
3. si passa al setaccio l'AST di ogni modulo del repo e si verifica che
   l'unico posto in cui una `fetch` e' staccata dalla sua `execute` sia
   `backtest/feed.py`, che ha un cursore per flusso e un controllo sul numero
   di righe.

Il punto 3 e' un test e non un'ispezione fatta una volta: un'ispezione vale
fino al prossimo commit.
"""

from __future__ import annotations

import ast
import os
import pathlib
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catalog import dataset

RADICE = pathlib.Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PACCHETTI = ("costs", "catalog", "collector", "backtest", "tools")

# I metodi che consumano un result set. Se il ricevente di uno di questi non e'
# la `execute` stessa, il risultato e' stato tenuto da parte: e' il pattern che
# ha prodotto il troncamento.
FETCH = ("fetchall", "fetchone", "fetchmany", "fetchdf", "fetchnumpy",
         "fetch_df", "fetch_arrow_table", "fetch_record_batch", "arrow", "df")

# L'unico posto autorizzato a tenere un cursore aperto, perche' ne apre uno
# proprio per flusso e verifica il conteggio delle righe consegnate.
AUTORIZZATI = {"backtest/feed.py"}


def fetch_differite(path: pathlib.Path) -> list[tuple[int, str]]:
    """Righe in cui una `fetch*` non e' incatenata alla propria `execute`."""
    fuori = []
    for n in ast.walk(ast.parse(path.read_text())):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr in FETCH):
            continue
        ric = n.func.value
        incatenata = (isinstance(ric, ast.Call)
                      and isinstance(ric.func, ast.Attribute)
                      and ric.func.attr == "execute")
        if not incatenata:
            fuori.append((n.lineno, n.func.attr))
    return fuori


class TestIlComportamentoDiDuckDB(unittest.TestCase):
    """Cosa fa davvero DuckDB. Verificato, non ricordato."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.root = tempfile.mkdtemp(prefix="duckdb-cursori-")
        cls.con = dataset.connect(os.path.join(cls.root, "tmp"))
        cls.con.execute("CREATE TABLE a AS SELECT range AS i FROM range(5000)")
        cls.con.execute("CREATE TABLE b AS SELECT range AS i FROM range(10)")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.con.close()
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_una_seconda_execute_azzera_il_risultato_aperto(self) -> None:
        """La riproduzione del difetto, in otto righe."""
        self.con.execute("SELECT i FROM a ORDER BY i")
        primo = len(self.con.fetchmany(1000))
        self.con.execute("SELECT i FROM b").fetchall()      # l'intrusa
        dopo = len(self.con.fetchmany(1000))
        print(f"\n[duckdb] primo blocco {primo} righe, dopo una seconda "
              f"execute sulla stessa connessione {dopo} righe (attese 1000 "
              f"su 5000 totali)")
        self.assertEqual(primo, 1000)
        self.assertEqual(dopo, 0)          # zero righe, zero eccezioni

    def test_un_cursore_per_flusso_non_si_fa_invalidare(self) -> None:
        """La correzione applicata al feed."""
        a = self.con.cursor()
        b = self.con.cursor()
        a.execute("SELECT i FROM a ORDER BY i")
        primo = len(a.fetchmany(1000))
        b.execute("SELECT i FROM b").fetchall()
        dopo = len(a.fetchmany(4000))
        print(f"[duckdb] con un cursore per flusso: {primo} + {dopo} = "
              f"{primo + dopo} righe su 5000")
        self.assertEqual(primo + dopo, 5000)

    def test_fetchall_e_immune(self) -> None:
        """Perche' `costs/` e `catalog/` non sono esposti: le loro righe sono
        gia' in Python prima che la query successiva parta."""
        righe = self.con.execute("SELECT i FROM a ORDER BY i").fetchall()
        self.con.execute("SELECT i FROM b").fetchall()
        print(f"[duckdb] righe materializzate con fetchall: {len(righe)} "
              f"su 5000, invariate dopo una seconda execute")
        self.assertEqual(len(righe), 5000)


class TestNessunRisultatoTenutoDaParte(unittest.TestCase):

    def test_solo_il_feed_del_backtester_consuma_in_ritardo(self) -> None:
        trovate: dict[str, list[tuple[int, str]]] = {}
        n_file = 0
        for pacchetto in PACCHETTI:
            for f in sorted((RADICE / pacchetto).glob("*.py")):
                n_file += 1
                fuori = fetch_differite(f)
                if fuori:
                    trovate[str(f.relative_to(RADICE))] = fuori
        print(f"\n[audit] {n_file} moduli passati al setaccio in "
              f"{', '.join(PACCHETTI)}; fetch staccate dalla propria execute: "
              f"{ {k: v for k, v in trovate.items()} }")
        self.assertEqual(set(trovate), AUTORIZZATI,
                         "un modulo tiene aperto un result set DuckDB: se fra "
                         "la execute e la fetch gira un'altra query, le righe "
                         "mancanti spariscono senza errore")

    def test_il_setaccio_riconosce_il_pattern_che_cerca(self) -> None:
        """Controllo negativo: se l'analisi non vedesse il caso incriminato,
        il test sopra passerebbe per il motivo sbagliato."""
        root = tempfile.mkdtemp(prefix="audit-")
        try:
            buono = pathlib.Path(root) / "buono.py"
            cattivo = pathlib.Path(root) / "cattivo.py"
            buono.write_text("r = con.execute('SELECT 1').fetchall()\n")
            cattivo.write_text("cur = con.execute('SELECT 1')\n"
                               "r = cur.fetchmany(10)\n")
            print(f"[audit] controllo negativo: buono "
                  f"{fetch_differite(buono)}, cattivo {fetch_differite(cattivo)}")
            self.assertEqual(fetch_differite(buono), [])
            self.assertEqual(fetch_differite(cattivo), [(2, "fetchmany")])
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
