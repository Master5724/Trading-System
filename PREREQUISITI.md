# Prerequisiti

Il documento precedente dava per scontati due concetti — **Sharpe** e
**significatività statistica** — su cui poggiava metà del contenuto. Questo li
costruisce da zero, con analogie prese dal tuo mestiere invece che dal trading.

---

## 1. Sharpe ratio: rendimento diviso affidabilità

Immagina due sviluppatori. Entrambi consegnano in media 10 feature al mese.

- **Anna:** 10, 10, 9, 11, 10, 10 → media 10, molto regolare
- **Bruno:** 0, 30, 0, 25, 0, 5 → media 10, completamente imprevedibile

Stessa media. Ma se devi promettere una data a un cliente, Anna vale molto di
più. Lo **Sharpe ratio** misura esattamente questa differenza:

```
Sharpe = rendimento medio / oscillazione del rendimento
```

Anna ha uno Sharpe alto, Bruno bassissimo. Nel trading il numeratore è quanto
guadagni, il denominatore è quanto ballano i tuoi risultati.

**Ordini di grandezza, per orientarti:**

| Sharpe annualizzato | significato |
|---|---|
| 0,5 | comprare e tenere BTC, più o meno |
| 1,0 | buono, un fondo professionale ne va fiero |
| 2,0 | eccellente, raro |
| > 3,0 in un backtest retail | **quasi sempre un bug o overfitting** |

Se il tuo sistema riporta Sharpe 4, la spiegazione più probabile non è che hai
scoperto qualcosa. È che il backtester è rotto.

---

## 2. Significatività: è il tuo A/B test

Questa è la parte che ti manca, ed è la più importante. La conosci già, solo con
un altro nome.

**Sul tuo SaaS.** Provi un bottone verde contro uno blu. Dopo **40 visitatori**,
il verde converte al 12% e il blu al 10%. Fai il deploy del verde?

No. Con 40 visitatori quella differenza è rumore. Ti serve un campione grande
abbastanza perché la differenza non possa essere fortuna.

**Il trading è identico.** Un backtest è un A/B test dove:
- ogni **trade** è un visitatore
- il **rendimento medio per trade** è il tasso di conversione
- lo **Sharpe** è la dimensione dell'effetto

E come nell'A/B test, esiste un numero che ti dice se puoi fidarti.

### Il t-statistic

È una sola cosa: **quante volte l'effetto misurato è più grande del suo margine
di errore.**

```
t ≈ Sharpe × √(anni di dati)
```

- **t sotto 2** → non hai imparato niente, potrebbe essere fortuna
- **t sopra 2** → evidenza debole ma reale
- **t sopra 3** → evidenza seria

Perché la radice degli anni: raddoppiare i dati non dimezza l'errore, lo riduce
solo di un fattore 1,41. **I dati che servono crescono col quadrato.** È lo
stesso motivo per cui un A/B test che vuole rilevare una differenza piccola
richiede un campione enorme.

### Applichiamolo alla domanda 1

Sharpe 2,4 su 6 mesi:

```
t = 2,4 × √0,5 = 1,7
```

Sotto 2. **Nessuna evidenza.** La tua risposta ("troppi pochi trade, potrebbe
essere fortuna") era giusta; questo è il conto che la dimostra in un numero.

---

## 3. Look-ahead bias = data leakage

Se hai mai lavorato con modelli, lo conosci già con quel nome.

**L'esempio più chiaro.** Costruisci un filtro antispam. Fra le feature, per
sbaglio, includi il campo "l'utente ha poi spostato questa mail in Spam".
Accuratezza in test: 99,8%. In produzione: inutile. Il modello non ha imparato
a riconoscere lo spam, ha imparato a leggere la risposta.

**Nel trading è la stessa identica cosa:** la strategia usa, nel momento in cui
decide, un'informazione che a quel momento non era ancora disponibile.

Il backtest non dà errore. Dà un risultato meraviglioso. È il modo numero uno
in cui questi sistemi ingannano chi li costruisce.

### La domanda 3, sciolta

> **a)** Normalizzi il volume con media e deviazione degli **ultimi 30 giorni
> precedenti** a ogni riga.

Corretto, nessun problema. Guarda solo indietro. È come calcolare la conversione
media della settimana scorsa: informazione che avevi davvero.

> **b)** Selezioni i 4 perp guardando quali hanno avuto più volume **nell'intero
> periodo di backtest**.

**Questo è look-ahead.** A gennaio non potevi sapere quali coin sarebbero state
liquide a dicembre. Hai scelto i vincitori conoscendo il finale. Analogia: scegli
quali feature del tuo SaaS mostrare nella landing page in base a quelle che
l'anno prossimo avranno più uso — e poi ti stupisci che la landing converta bene
"in test".

> **c)** Decidi sulla candela chiusa alle 14:00 ed esegui al prezzo di
> **apertura** delle 14:00.

**Questo è look-ahead.** La candela delle 14:00 copre 14:00→15:00: la conosci
solo alle 15:00. Ma stai comprando al prezzo delle 14:00, un'ora prima di avere
l'informazione. Compri sapendo già come andrà l'ora successiva.

La versione corretta è: decidi sulla candela chiusa alle 14:00 ed esegui
all'apertura delle **15:00**.

Due su tre erano trappole. Non era una domanda facile.

---

## 4. Test multipli: A/B test ripetuti fino a vincere

Torniamo al tuo SaaS. Provi **200 varianti** del bottone. Una converte molto
meglio delle altre, con t = 3,1. Ottima notizia?

No. Se **tutte e 200** le varianti fossero identiche fra loro, la migliore
avrebbe comunque un t alto per pura fortuna — perché stai prendendo il massimo
di 200 estrazioni casuali. La formula:

```
t_max atteso ≈ √(2 · ln N)
```

| varianti provate | t del migliore, **per puro caso** |
|---|---|
| 10 | 2,1 |
| 100 | 3,0 |
| 200 | **3,26** |
| 1.000 | 3,7 |

### La domanda 5, sciolta

Claude Code dice: 200 varianti, la migliore ha t = 3,1.

Il caso puro, su 200 tentativi, produce t ≈ **3,26**. Il risultato è **sotto**
quello che ci si aspetta dalla fortuna. Non è evidenza debole: è **zero**
evidenza, e anzi un risultato leggermente deludente.

La risposta corretta è: *"su 200 tentativi, 3,1 è meno di quanto darebbe il
caso. Non hai trovato niente."*

Il tuo istinto — chiedere periodo e numero di trade — era giusto e va tenuto. Ma
la domanda decisiva è un'altra, ed è quella che nessuno pensa a fare:

> **"Quante cose hai provato prima di trovare questa?"**

Contano tutte: ogni parametro spostato, ogni soglia ritoccata, ogni idea scartata
perché andava male. È il **p-hacking**, ed è il motivo per cui metà degli studi
scientifici non si replica.

### E quindi la domanda 7

*Perché "il sistema ottimizza i propri parametri in continuo" è pericoloso?*

Perché è una macchina che fa milioni di A/B test e tiene solo i vincitori, senza
che nessuno conti i tentativi. Genera garantitamente qualcosa che sembra
funzionare sul passato, e non funziona sul futuro. Con l'aggravante che
l'entusiasmo di chi guarda cresce proprio quando il rischio è massimo.

---

## 5. I due chiarimenti mancanti

### Domanda 4: perché la liquidazione arriva un po' prima

Avevi risposto "intorno al 20%" — il conto dà 17,5%, sei nel giusto. Perché nella
pratica succede prima, per due ragioni:

1. **Commissioni e funding erodono il margine.** La formula assume che il tuo
   capitale resti intatto e si muova solo il prezzo. In realtà ogni ora paghi
   funding e all'apertura hai pagato commissioni: il cuscinetto si assottiglia
   da solo, anche se il prezzo sta fermo.

2. **Il trigger usa il mark price.** Il *mark price* è un prezzo di riferimento
   calcolato dal protocollo aggregando più fonti, non l'ultimo prezzo scambiato
   su Hyperliquid. Serve a evitare che qualcuno ti liquidi con una candela
   fasulla, ma significa che puoi essere liquidato a un prezzo che sul grafico
   non vedi.

### Domanda 6: backtest positivo, paper trading peggiore

La tua risposta ("lo Sharpe") confondeva la **misura** con la **causa**: lo
Sharpe è il termometro, non la malattia.

La causa quasi sempre è una sola: **il backtest ipotizzava esecuzioni migliori di
quelle reali.** Nell'ordine di probabilità:

1. **Fill ottimistici.** Il backtest assume che un ordine limite venga eseguito
   perché il prezzo ha *toccato* il livello. Nella realtà, se il prezzo tocca e
   torna indietro, tu non sei stato eseguito — eri in fondo alla coda.
2. **Slippage sottostimato.** Assumi di comprare al miglior prezzo mostrato,
   ma il tuo ordine mangia più livelli del book.
3. **Costi incompleti.** Manca il funding, o è stimato invece che preso dai dati.
4. **Latenza.** Il backtest decide e opera nello stesso istante. Il sistema
   reale ci mette qualche secondo, e il prezzo si è mosso.

**Cosa controlli per primo:** metti a confronto, trade per trade, il prezzo di
esecuzione ipotizzato dal backtest e quello ottenuto in paper. La differenza
media è il tuo errore di modello, in numeri. Ed è esattamente il motivo per cui
il paper trading esiste nel piano.

---

## Da ricordare

| concetto | in una riga |
|---|---|
| Sharpe | quanto guadagni per quanto ballano i risultati |
| t-statistic | quante volte l'effetto supera il suo margine d'errore; sotto 2 non conta |
| look-ahead | data leakage: la strategia legge il futuro |
| test multipli | se provi tanto, qualcosa sembra funzionare per forza |
| leva | non alza il rendimento atteso, aggiunge solo un modo di perdere tutto |
