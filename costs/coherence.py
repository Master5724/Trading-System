"""Il report che si accorge di contraddirsi.

**Perche' esiste.** Un report di costo e' fatto di numeri che si ricompongono
l'uno nell'altro: il round-trip taker E' due commissioni piu' lo slippage
pagato due volte, e il funding stampato nella tabella finale E' lo stesso
double che il cross-check confronta col catalogo. Queste non sono coincidenze
da verificare a occhio: sono identita'. Se una salta, uno dei due numeri e'
sbagliato — o, come e' successo su questa PR, la tabella non e' stata generata
dal codice ma ricopiata a mano, e una riga ha preso i valori di un'altra coin.

Un report che stampa i numeri e basta non puo' distinguere i due casi, e chi
legge nemmeno: `0,0900 %` e `0,0916 %` sembrano lo stesso ordine di grandezza.
Quindi il controllo sta qui, gira sempre, e quando fallisce il report lo dice
in cima e in fondo e il comando esce con codice diverso da zero.

**Le tolleranze, e perche' nessuna e' un margine di comodo.**

- `TOL_REL_FLOAT` (1e-12) e' l'errore di rappresentazione su una somma di
  pochi addendi in doppia precisione. Serve a non far fallire un confronto per
  l'ultimo bit; qualunque cosa piu' grande e' un fatto, non rumore.
- `TOL_PCT_FLOAT` (1e-12 punti percentuali) la stessa cosa, sulle grandezze
  gia' espresse in percentuale.
- L'identita' fra MEDIANE ha una tolleranza DERIVATA dai dati, non scelta:
  vedi `check_round_trip`. Non c'e' nessun numero tondo da allargare.

**Cosa questi controlli non sono.** Non verificano che il modello di costo sia
giusto: verificano che il report sia coerente con se stesso. Un modello
sbagliato in modo uniforme passerebbe tutti i controlli qui dentro. Servono i
test su fixture per l'altra meta'.
"""

from __future__ import annotations

from dataclasses import dataclass

# Errore di rappresentazione in doppia precisione, non un margine di modello.
TOL_REL_FLOAT = 1e-12
TOL_PCT_FLOAT = 1e-12


@dataclass(frozen=True)
class Check:
    """Un'uguaglianza che deve valere fra numeri stampati nel report.

    `eseguito` falso significa che il controllo non ha potuto girare (per
    esempio il cross-check e' stato saltato): non e' un successo, e il report
    lo stampa come NON VERIFICATO. Un controllo che non gira e uno che passa
    non devono poter avere lo stesso aspetto.
    """

    coin: str
    nome: str
    passato: bool
    eseguito: bool = True
    atteso: float | None = None
    ottenuto: float | None = None
    scarto: float | None = None
    tolleranza: float | None = None
    unita: str = ""
    nota: str = ""

    @property
    def esito(self) -> str:
        if not self.eseguito:
            return "NON VERIFICATO"
        return "ok" if self.passato else "FALLITO"

    def to_dict(self) -> dict:
        return {
            "coin": self.coin, "nome": self.nome, "esito": self.esito,
            "passato": self.passato, "eseguito": self.eseguito,
            "atteso": self.atteso, "ottenuto": self.ottenuto,
            "scarto": self.scarto, "tolleranza": self.tolleranza,
            "unita": self.unita, "nota": self.nota,
        }


def check_round_trip(coin: str, execution: dict) -> list[Check]:
    """Il round-trip taker e' due commissioni piu' lo slippage dei due lati.

    Due controlli, a due livelli diversi, ed e' la distinzione che rende il
    secondo onesto:

    1. *Per snapshot* l'identita' e' algebrica: `total = fee + spread + impact`
       sono gli stessi addendi ripresi dalla stessa struttura. Deve valere
       all'ultimo bit, e il report porta con se' il residuo massimo osservato
       su ~24.000 snapshot.

    2. *Fra mediane* l'identita' non puo' essere esatta, e dire il contrario
       sarebbe falso: la mediana di una somma non e' la somma delle mediane. Lo
       e' pero' a meno dello scarto puntuale fra i due termini costanti, perche'
       la mediana e' 1-Lipschitz rispetto alla norma del sup: se due serie
       differiscono punto per punto al piu' di `d`, le loro mediane differiscono
       al piu' di `d`. Qui le due serie sono `round_trip_taker_pct` e
       `2*taker + slippage_round_trip_pct`, che differiscono esattamente per
       `fee_pct - 2*taker` — cioe' per il fatto che la commissione si paga sul
       notional ESEGUITO e non su quello nominale. Quel massimo e' misurato
       durante la passata (`round_trip_taker_fee_scarto_max_pct`) ed e' la
       tolleranza: derivata dai dati, non scelta per far passare il controllo.
       Sui dati reali vale ~1e-8 punti percentuali, cioe' un controllo stretto.

    La mediana dello slippage usata qui e' quella del ROUND-TRIP (i due lati
    dello stesso snapshot, sommati), non quella della tabella per size, che
    aggrega acquisti e vendite come osservazioni separate. Sono due statistiche
    diverse della stessa grandezza e coincidono solo se il book e' simmetrico:
    `check_slippage_tabella` misura quanto distano invece di dare per scontato
    che siano la stessa cosa.
    """
    rt = execution.get("round_trip_taker_pct_p50")
    fee_p50 = execution.get("round_trip_taker_fee_pct_p50")
    slip_p50 = execution.get("round_trip_taker_slippage_pct_p50")
    fee_ref = execution.get("round_trip_taker_fee_riferimento_pct")
    fee_scarto = execution.get("round_trip_taker_fee_scarto_max_pct")
    residuo = execution.get("round_trip_identita_residuo_max_rel")
    out: list[Check] = []

    if residuo is None or execution.get("round_trip_taker_n", 0) == 0:
        out.append(Check(coin, "identita del round-trip, per snapshot",
                         passato=False, eseguito=False,
                         nota="nessuno snapshot con round-trip calcolabile"))
    else:
        out.append(Check(
            coin, "identita del round-trip, per snapshot",
            passato=residuo <= TOL_REL_FLOAT,
            ottenuto=residuo, tolleranza=TOL_REL_FLOAT, scarto=residuo,
            unita="scarto relativo",
            nota=f"su {execution['round_trip_taker_n']:,} snapshot: "
                 f"total = fee + spread + impatto",
        ))

    if None in (rt, fee_ref, slip_p50, fee_scarto):
        out.append(Check(coin, "round-trip taker = 2 commissioni + slippage",
                         passato=False, eseguito=False,
                         nota="mediane non disponibili"))
        return out
    atteso = fee_ref + slip_p50
    scarto = abs(rt - atteso)
    tol = fee_scarto + TOL_PCT_FLOAT
    out.append(Check(
        coin, "round-trip taker = 2 commissioni + slippage",
        passato=scarto <= tol,
        atteso=atteso, ottenuto=rt, scarto=scarto, tolleranza=tol,
        unita="punti percentuali",
        nota=f"2 commissioni {fee_ref:.6f} % (pagate {fee_p50:.6f} %) + "
             f"slippage round-trip {slip_p50:.6f} %",
    ))
    return out


def check_slippage_tabella(coin: str, execution: dict, sizes: dict) -> Check:
    """Quanto la tabella per size dice sullo stesso costo del round-trip.

    Non e' un'uguaglianza e non viene imposta come tale: la tabella ha la
    mediana degli acquisti e delle vendite messi insieme come osservazioni
    separate, il round-trip ha la mediana della loro SOMMA snapshot per
    snapshot. Coincidono solo su un book simmetrico. Il controllo esiste per
    stampare la distanza fra le due letture — se un giorno diventasse grande,
    vorrebbe dire che il book e' sistematicamente sbilanciato su un lato, che e'
    un fatto di mercato da sapere, non un errore del report.

    Passa sempre: e' una misura, non un vincolo. Il numero e' nel JSON.
    """
    notional = execution.get("round_trip_notional_usd")
    row = sizes.get(notional) or {}
    per_lato = row.get("slippage_bps_p50")
    rt_slip_pct = execution.get("round_trip_taker_slippage_pct_p50")
    if per_lato is None or rt_slip_pct is None:
        return Check(coin, "tabella per size contro round-trip",
                     passato=True, eseguito=False,
                     nota="slippage non disponibile")
    due_volte = 2.0 * per_lato          # bps
    rt_slip_bps = 100.0 * rt_slip_pct   # da % a bps
    return Check(
        coin, "tabella per size contro round-trip",
        passato=True,
        atteso=due_volte, ottenuto=rt_slip_bps,
        scarto=abs(due_volte - rt_slip_bps), unita="bps (misura, non vincolo)",
        nota=f"2 x slippage p50 della tabella su {notional:,.0f} $ contro "
             f"slippage p50 del round-trip",
    )


def check_funding(coin: str, funding: dict, crosscheck_row: dict | None,
                  ) -> list[Check]:
    """Il funding della tabella finale e' quello del cross-check.

    Sono la stessa espressione — `FundingSeries.cost(LONG, ...)` sulla finestra
    di `funding_window` — quindi la tolleranza e' l'ultimo bit e nient'altro.
    Non e' un controllo sul modello: e' il controllo che i due numeri stampati
    nello stesso report vengano davvero dallo stesso calcolo, che e' proprio
    cio' che una tabella ricopiata a mano non garantisce.

    Gli altri due esiti del cross-check (finestre coincidenti e uguaglianza col
    catalogo) vengono riportati qui invece di restare in fondo al report: sono
    la ragione per cui il numero di funding e' credibile, e devono stare
    accanto al numero.
    """
    disponibile = funding.get("disponibile")
    if not disponibile:
        return [Check(coin, "funding tabella = funding cross-check",
                      passato=False, eseguito=False,
                      nota="nessun funding disponibile per questa coin")]
    if crosscheck_row is None:
        return [Check(coin, "funding tabella = funding cross-check",
                      passato=False, eseguito=False,
                      nota="cross-check non eseguito (--no-crosscheck)")]
    if not crosscheck_row.get("disponibile"):
        return [Check(coin, "funding tabella = funding cross-check",
                      passato=False, eseguito=False,
                      nota="il cross-check non ha dati per questa coin")]

    w = crosscheck_row["finestra"]
    ore = crosscheck_row["ore"]
    mio, suo = funding["long_pct"], w["costs_pct"]
    scarto = abs(mio - suo)
    out = [Check(
        coin, "funding tabella = funding cross-check",
        passato=scarto <= TOL_PCT_FLOAT,
        atteso=suo, ottenuto=mio, scarto=scarto, tolleranza=TOL_PCT_FLOAT,
        unita="punti percentuali",
        nota="stesso calcolo, stessa finestra: deve essere lo stesso double",
    ), Check(
        coin, "finestra di costs = finestra di catalog",
        passato=bool(w["finestre_coincidono"]),
        unita="ore di regolamento",
        nota=f"costs {w['costs_first_hour']}..{w['costs_last_hour']}   "
             f"catalog {w['catalog_first_hour']}..{w['catalog_last_hour']}",
    ), Check(
        coin, "funding di costs = funding di catalog (stessa finestra)",
        passato=bool(w["coincidono"]),
        atteso=w["catalog_pct"], ottenuto=w["costs_pct_senza_maschera"],
        scarto=abs(w["diff_pct_finestra_comune"]),
        unita="punti percentuali",
        nota=f"senza la maschera dei buchi ({w['n_ore_mascherate']} ore, "
             f"{w['mask_delta_pct']:+.6f} punti); ore divergenti "
             f"{ore['n_diverging']}",
    ), Check(
        coin, "serie oraria di costs = serie oraria di catalog",
        passato=ore["n_diverging"] == 0,
        ottenuto=float(ore["n_diverging"]), tolleranza=0.0,
        scarto=ore["max_abs_diff"], unita="ore divergenti",
        nota=f"{ore['n_common']} ore in comune",
    )]

    # Simmetria long/short: non e' una verifica del modello (i due numeri
    # escono dalla stessa riga per `Side.sign`), ma la tabella finale li stampa
    # entrambi, e se una riga fosse ricopiata da un'altra coin questo lo
    # vedrebbe.
    somma = funding["long_pct"] + funding["short_pct"]
    out.append(Check(
        coin, "funding long + short = 0 nella tabella",
        passato=abs(somma) <= TOL_PCT_FLOAT,
        atteso=0.0, ottenuto=somma, scarto=abs(somma),
        tolleranza=TOL_PCT_FLOAT, unita="punti percentuali",
        nota="il funding e' un trasferimento: fra i due lati non si crea denaro",
    ))
    return out


def check_coin(coin: str, execution: dict, sizes: dict, funding: dict,
               crosscheck_row: dict | None) -> list[Check]:
    return [
        *check_round_trip(coin, execution),
        check_slippage_tabella(coin, execution, sizes),
        *check_funding(coin, funding, crosscheck_row),
    ]


def all_ok(checks: list[Check]) -> bool:
    """Vero solo se ogni controllo e' stato eseguito ED e' passato.

    Un controllo saltato non e' un controllo superato: se il cross-check non
    gira, il report non sa se il suo funding e' coerente, e deve dirlo.
    """
    return all(c.passato and c.eseguito for c in checks)


def _fmt(v: float | None, unita: str) -> str:
    if v is None:
        return "n/d"
    if unita.startswith("punti") or unita.startswith("bps"):
        return f"{v:+.6g}"
    return f"{v:.3e}"


def format_checks(checks: list[Check]) -> list[str]:
    """Il blocco a schermo. Se qualcosa fallisce, si vede prima dei numeri."""
    falliti = [c for c in checks if c.eseguito and not c.passato]
    saltati = [c for c in checks if not c.eseguito]
    L = ["", "=== coerenza interna del report ===", "",
         "  Ogni riga e' un'uguaglianza fra numeri stampati qui sopra. Non",
         "  verifica che il modello sia giusto: verifica che il report non si",
         "  contraddica. Tolleranze: l'ultimo bit, o un limite derivato dai",
         "  dati (vedi costs/coherence.py).", ""]
    coin = None
    for c in checks:
        if c.coin != coin:
            coin, _ = c.coin, L.append(f"  {c.coin}:")
        riga = f"      {c.esito:>14}  {c.nome}"
        if c.scarto is not None:
            riga += (f"   scarto {_fmt(c.scarto, c.unita)}"
                     f"{' ' + c.unita if c.unita else ''}")
        if c.tolleranza is not None:
            riga += f"   tolleranza {c.tolleranza:.3g}"
        L.append(riga)
        if c.nota:
            L.append(f"                      ({c.nota})")

    n_ok = sum(1 for c in checks if c.eseguito and c.passato)
    L.append("")
    if falliti or saltati:
        L += ["  " + "!" * 68,
              f"  !!! REPORT INCOERENTE: {len(falliti)} controlli falliti, "
              f"{len(saltati)} non eseguiti su {len(checks)}",
              "  !!! I numeri qui sopra si contraddicono fra loro. Non usarli.",
              "  " + "!" * 68]
        for c in falliti:
            L.append(f"  !!! {c.coin} — {c.nome}: atteso "
                     f"{_fmt(c.atteso, c.unita)}, ottenuto "
                     f"{_fmt(c.ottenuto, c.unita)}, scarto "
                     f"{_fmt(c.scarto, c.unita)} > tolleranza "
                     f"{c.tolleranza if c.tolleranza is not None else 0}")
        for c in saltati:
            L.append(f"  !!! {c.coin} — {c.nome}: NON VERIFICATO ({c.nota})")
    else:
        L.append(f"  esito: COERENTE — {n_ok} controlli su {len(checks)}, "
                 f"nessuno fallito, nessuno saltato")
    return L
