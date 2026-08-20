# I cinque concetti

Non è un corso di trading. È il minimo indispensabile per **leggere criticamente
gli output** del sistema che stai per far costruire. Tutti i numeri sono calcolati
sul tuo caso reale: Hyperliquid, perp, orizzonte da poche ore a 10 giorni.

---

## 1. Look-ahead bias

**Cos'è:** la strategia usa, al momento della decisione, informazione che a quel
momento non esisteva ancora. Il backtest diventa profeta.

Non produce mai un errore. Produce un risultato bellissimo.

**Le quattro forme che vedrai nel tuo sistema:**

| forma | come si manifesta |
|---|---|
| **Chiusura della barra** | decidi sulla candela delle 14:00 e compri al prezzo delle 14:00. Ma quel prezzo lo conosci solo alle 15:00. Devi decidere sulla barra chiusa ed eseguire sulla **successiva**. |
| **Normalizzazione globale** | calcoli media e deviazione standard su *tutto* il dataset e le usi per normalizzare le feature del 2025. Hai iniettato il futuro in ogni riga. Le statistiche vanno calcolate su finestre mobili che guardano solo indietro. |
| **Selezione degli asset** | scegli di tradare SOL e HYPE perché *sai* che sono andati bene. Il tuo universo va deciso con una regola che sarebbe stata applicabile allora (es. "i 5 perp con maggior volume nei 30 giorni precedenti"). |
| **Dato rivisto** | usi un valore che l'exchange ha corretto dopo. Sui perp è raro, ma il funding *predetto* non è il funding *pagato*: non confonderli. |

**Il test che lo smaschera:** per ogni feature, verifica che ogni timestamp usato
sia **strettamente minore** di quello della decisione. Se il codice non rende
questo controllo automatico, prima o poi lo violerà.

---

## 2. Il funding: il costo che comanda a orizzonte lungo

**Come funziona su Hyperliquid.** <cite index="66-1">La formula produce un tasso a 8 ore, ma il pagamento avviene ogni ora a un ottavo del tasso calcolato.</cite> Il tasso a 8 ore si costruisce così:

```
F_8h = premio + clamp(0,01% − premio, −0,05%, +0,05%)
tasso orario = F_8h / 8
```

<cite index="66-1">La componente di interesse è fissa allo 0,01% ogni 8 ore</cite>, cioè 0,00125% ogni ora. Il **premio** misura di quanto il perp scambia sopra o sotto il prezzo dello spot secondo l'oracolo.

Se il perp sta sopra lo spot il funding è positivo e **i long pagano gli short**.

**Lo 0,00125% l'ora è un valore di default, non un minimo garantito.** Il pezzo
`clamp(x, −0,05%, +0,05%)` vuol dire "prendi x, ma non lasciarlo uscire dalla
banda ±0,05%". Da lì escono tre comportamenti diversi:

| premio (su 8 ore) | cosa fa il clamp | funding orario che ne esce |
|---|---|---|
| fra **−0,04% e +0,06%** | non satura: il premio si somma e si sottrae, e si cancella | **esattamente 0,00125%**, qualunque sia il premio dentro la banda |
| sotto **−0,04%** | satura in basso: `F_8h = premio + 0,05%` | **meno** di 0,00125%, fino a diventare negativo (allora pagano gli short) |
| sopra **+0,06%** | satura in alto: `F_8h = premio − 0,05%` | **più** di 0,00125%, senza limite superiore |

Quindi 0,00125% non è un pavimento e non è nemmeno un tetto: è il valore che
esce *finché il premio resta in mezzo*, e si esce da **entrambi** i lati.

**Cosa mostrano i dati che stiamo raccogliendo** (10 giorni, 9→19 agosto 2026,
240 ore di funding per coin su BTC, ETH, HYPE, SOL):

- su **BTC, 128 ore su 240** hanno il funding esattamente a 0,00125% — ed è
  anche il **massimo** osservato su quella coin. Nell'istogramma si vede un
  "muro" netto: tante ore impilate sul valore di default, coda libera a
  sinistra, niente a destra;
- il muro c'è su tutte e quattro, di altezza diversa: ore esattamente al
  default 128/240 su BTC, 107 su ETH, 186 su HYPE, 75 su SOL;
- su **ETH e HYPE il default è stato superato**: massimo orario 0,00267% su ETH
  e 0,0145% su HYPE, cioè quasi dodici volte il default. Non è un tetto;
- nel complesso il costo di un long, annualizzato sul periodo, è stato
  **BTC 7,0% · ETH 7,6% · HYPE 10,4% · SOL 6,4%**: tutti *sotto* l'11%, perché
  il perp ha scambiato sotto lo spot quasi sempre (su BTC il mark price è stato
  sotto l'oracolo nel **98,3%** delle ore, su ETH nel 99,6%).

**Cosa aspettarti se apri un long.** Come situazione normale, circa **11%
annualizzato** (0,00125% × 24 × 365 = 10,95%): è il default, e in una quota
grande delle ore paghi esattamente quello — nel periodo misurato dal 31% delle
ore su SOL al 78% su HYPE. Meno — fino a **incassare** funding
invece di pagarlo — quando il premio è negativo, come nei dieci giorni che
abbiamo misurato. Molto di più nei regimi fortemente rialzisti, quando quasi
tutti sono long e il perp scambia sopra lo spot: lì il funding esce dalla banda
verso l'alto e i numeri della tabella sotto (0,02% l'ora) diventano realistici.

**Perché ti riguarda.** Confronta i costi di un long su 10 giorni, in percentuale
del notional:

| voce | costo |
|---|---|
| commissioni round-trip, taker in entrata e uscita | ~0,09% |
| funding 10 giorni al valore di default (240 ore × 0,00125%) | ~0,30% |
| funding 10 giorni davvero misurato su BTC (9→19 ago 2026, 240 ore) | ~0,19% |
| funding 10 giorni a 0,02%/ora (mercato in trend) | ~4,8% |

Il funding al valore di default costa già **più del triplo delle commissioni**. In un mercato
caldo, cinquanta volte tanto. <cite index="71-1">Su un hold di settimane il funding supera regolarmente le commissioni su cui tutti si concentrano, e quasi nessuno lo misura finché non compare nel PnL.</cite>

**Tre dettagli che cambiano i conti:**
- <cite index="71-1">Il funding si regola sul prezzo dell'oracolo, non sul mark price.</cite>
- <cite index="70-1">Una posizione aperta e chiusa dentro la stessa ora non paga funding.</cite>
- Il segno può girare mentre sei dentro. Un carry che sembrava garantito non lo è.

**Regola operativa:** una strategia long-only multi-giorno deve battere il
funding *prima* di generare un centesimo. Se il tuo backtest non sottrae il
funding orario reale, sta mentendo di parecchi punti percentuali l'anno.

---

## 3. Liquidazione: perché la leva ti uccide

<cite index="19-1">Il margine di mantenimento su Hyperliquid è metà della frazione di margine iniziale massima.</cite> Per SOL, con leva massima 20x, sono circa **2,5%** del notional.

**La formula che devi sapere a memoria.** Con leva `L` e margine di mantenimento
`m`, vieni liquidato dopo un movimento contrario di circa:

```
movimento = (1 / L) − m
```

| leva su SOL | movimento contrario che ti liquida |
|---|---|
| 20x | **2,5%** |
| 10x | 7,5% |
| 5x | 17,5% |
| 3x | 30,8% |
| 2x | 47,5% |

SOL si muove del 5-10% in una giornata ordinaria. **A 20x vieni liquidato da un
martedì qualunque.** Non serve un crash.

Tre aggravanti che la formula non mostra:
- commissioni e funding erodono l'equity, quindi avvicinano la liquidazione
- il trigger usa il **mark price**, non l'ultimo prezzo scambiato
- <cite index="49-1">la liquidazione è definitiva e scatta su un singolo tick del mark price</cite>

**La cosa importante:** la leva non aumenta il rendimento atteso. Moltiplica sia
il guadagno sia la perdita, ma introduce un livello a cui perdi *tutto e per
sempre* — un evento che non esiste senza leva. È asimmetrica contro di te.

---

## 4. Perché uno Sharpe alto su pochi trade non significa nulla

Lo Sharpe ratio è rendimento diviso volatilità. È una **stima**, e come ogni
stima ha un errore che dipende dal numero di osservazioni.

**Il conto che devi saper fare.** L'evidenza statistica si misura col t-statistic:

```
t ≈ Sharpe × √(anni di dati)
```

Serve `t ≈ 2` per un'evidenza appena decente. Quindi:

| Sharpe annualizzato | anni necessari per t ≈ 2 |
|---|---|
| 0,5 | 16 |
| 1,0 | **4** |
| 2,0 | 1 |

**La versione per trade**, più utile a te:

```
t ≈ (media / deviazione standard dei rendimenti per trade) × √(numero di trade)
```

Un edge realistico ha un rapporto media/deviazione per trade intorno a 0,05.
Per raggiungere t = 2 servono:

```
N = (2 / 0,05)² = 1.600 trade
```

Il tuo sistema swing su 4-6 coin farà forse 100-150 trade l'anno. **Sei circa un
ordine di grandezza sotto la soglia di significatività statistica.**

Questo non rende il progetto inutile. Rende obbligatoria una conclusione:
qualunque risultato vedrai nei primi mesi è **compatibile con la fortuna**. La
domanda giusta non è "quanto ha guadagnato", è "quanto sarebbe potuto andare
diversamente".

---

## 5. Overfitting e test multipli: il killer silenzioso

**Il fatto.** Prendi N strategie completamente inutili, tutte a rendimento atteso
zero. Il massimo t-statistic che osserverai fra loro cresce come:

```
t_max ≈ √(2 · ln N)
```

| strategie provate | t del migliore, per puro caso |
|---|---|
| 10 | 2,1 |
| 100 | 3,0 |
| 1.000 | **3,7** |

Un t di 3,7 è il genere di numero che ti fa mettere i soldi. E lì dentro non
c'era niente.

**Il punto che quasi tutti sbagliano: conta i tentativi, tutti.** Ogni parametro
che cambi, ogni soglia che sposti, ogni indicatore che aggiungi "per vedere", ogni
volta che scarti un risultato brutto e ne provi un altro — sono tutti tentativi.
Se hai guardato il risultato prima di decidere il cambiamento, hai speso un test.
Nessuno tiene il conto onestamente, ed è esattamente per questo che i sistemi
retail muoiono in live.

**Le tre difese, in ordine di forza:**

1. **Poche ipotesi, decise prima.** Scrivi cosa proverai *prima* di vedere i dati.
2. **Cassaforte out-of-sample.** Un blocco di dati che non guardi. Mai. Lo apri
   una volta sola, alla fine. Se lo usi due volte, non è più out-of-sample.
3. **Walk-forward.** Ottimizzi su una finestra, testi sulla successiva, avanzi.
   Simula la sola cosa che conta: decidere senza sapere.

**Corollario per il tuo progetto:** l'idea del "sistema che si autoottimizza in
continuo" è una macchina industriale per test multipli. È il motivo per cui nel
CLAUDE.md l'ottimizzazione dei parametri è vietata e ogni proposta deve passare
da un gate umano e da dati mai visti.

---

## Sintesi in cinque righe

1. Se il backtest è troppo bello, cerca il look-ahead prima di festeggiare.
2. A orizzonte multi-giorno il nemico è il funding, non le commissioni.
3. La leva non alza il rendimento atteso, aggiunge solo un modo di perdere tutto.
4. Sotto ~1.000 trade non stai misurando un edge, stai guardando rumore.
5. Ogni cosa che provi consuma credibilità statistica. Contale.
