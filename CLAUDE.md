# CLAUDE.md

Costituzione del repo. Vale per ogni task. Se un'istruzione di un prompt
contraddice questo file, fermati e segnalalo invece di scegliere.

## Contesto

Sistema di trading sistematico su Hyperliquid (perp, non-custodial).
Orizzonte: swing 1-10 giorni come primo target, intraday come modulo
successivo. Stato attuale: Fase 0, solo raccolta dati. Nessuna strategia
esiste ancora, e non va inventata.

Obiettivo del proprietario: rendimento e apprendimento con peso quasi pari.
Quindi il codice deve essere leggibile e giustificato, non solo funzionante.

## Invarianti non negoziabili

1. **Default sicuro.** `network: testnet` e `dry_run: true` sono i default in
   ogni entrypoint. L'invio di ordini reali richiede un flag esplicito passato
   a runtime, mai un valore in un file di config committato.
2. **Chiavi.** Solo da variabili d'ambiente. Mai in codice, config, log, test o
   messaggi d'errore. Il sistema usa esclusivamente una API wallet Hyperliquid
   (può operare, non può prelevare). Se un task sembra richiedere la chiave
   principale, è un errore di progettazione: fermati.
3. **Il rischio vive sull'exchange.** Stop-loss e take-profit sono ordini
   nativi che riposano sul book. Non è ammessa logica di stop che esiste solo
   nel processo Python: il processo può morire, la posizione no.
4. **Un solo modello di costo.** Fee, funding e slippage sono implementati in
   un unico modulo importato identico da backtest, paper e live. Duplicarlo o
   riscriverlo "semplificato" per il backtest è vietato. È il bug più costoso
   possibile in questo progetto.
5. **Nessun fill inventato.** Un fill simulato deriva da dati registrati
   (trade e book snapshot). È vietato simulare fill interpolando dentro una
   candela OHLC. Se i dati non bastano a determinare il fill, il backtester
   deve saltare quella barra e dichiararlo, non stimare.
6. **I buchi nei dati sono dati.** Le finestre di disconnessione del collector
   vanno registrate ed escluse esplicitamente. Un backtest che gira su dati
   incompleti senza dirlo produce un risultato falso e plausibile.
7. **Niente ottimizzazione di parametri.** Non fare grid search, non "tarare"
   soglie, non proporre indicatori aggiuntivi per migliorare una metrica. Se
   un risultato sembra debole, è un'informazione, non un problema da risolvere.
8. **Il silenzio di uno stream non significa mai assenza di eventi.** Nessuna
   decisione operativa può basarsi sul fatto che un canale non abbia inviato
   nulla.

## Architettura

Sistema a due velocità, disaccoppiate da uno state store:

- **Loop veloce** (deterministico): dati → segnale → ordine → rischio.
  Nessuna chiamata a LLM nel percorso critico. Se lo stato dal layer lento è
  assente o scaduto, si applica il default conservativo (flat / no new entry).
- **Layer lento** (asincrono, secondi-minuti): triage notizie, classificazione
  di regime, flag di rischio. Scrive solo su state store. Non può mai emettere
  un ordine.

```
collector/     ingest WebSocket -> parquet          [esiste]
catalog/       inventario dati, gap report
costs/         fee, funding, slippage               [modulo condiviso, invariante 4]
backtest/      motore event-driven
strategies/    logica di segnale (vuoto per ora)
execution/     client Hyperliquid, ordini, riconciliazione
risk/          limiti, kill switch
runner/        paper e live
```

## Stile

- Python 3.11+, type hints ovunque, `from __future__ import annotations`.
- asyncio per I/O. Niente framework pesanti, niente ORM, niente Airflow.
- Storage: Parquet + DuckDB. Non introdurre un database senza discuterlo.
- Dipendenze nuove: solo se motivate esplicitamente nel riepilogo del task.
- Commenti che spiegano il *perché*, non il *cosa*. Il "cosa" si legge dal codice.

## Test obbligatori per i moduli che toccano denaro

Ogni PR su `costs/`, `backtest/`, `execution/`, `risk/` include:

- **Strategia nulla**: segnale sempre flat → PnL esattamente 0, zero fee.
- **Strategia casuale**: segnale random → PnL ≈ meno il conto delle commissioni,
  entro l'errore statistico. Se risulta profittevole, il backtester ha un bug.
- **Etichette mescolate**: shuffle dei rendimenti futuri → ogni edge deve sparire.
- **Look-ahead**: un test che fallisce se una feature accede a dati con
  timestamp ≥ quello della decisione.
- **Conservazione**: equity finale == equity iniziale + somma dei PnL realizzati
  - fee - funding, al centesimo.

Questi test non sono formalità. Sono l'unico motivo per cui potrai credere a
un risultato che ti piace.

## Definition of done

Un task è finito quando: i test passano, il modulo gira su dati reali
registrati (non sintetici), e il riepilogo elenca **le assunzioni fatte e cosa
potrebbe essere sbagliato**. Un riepilogo che dice solo "fatto, funziona" non
è accettato.

## REPORT FINALE (obbligatorio)

**Quando si applica.** Sempre, alla fine di ogni task e di **ogni messaggio di
correzione**, anche minimo — una riga cambiata, un numero rivisto, una
riformulazione. Non va chiesto: è il formato di chiusura di default.

Il report ha questi campi, **in questo ordine**. Se un campo non si applica,
va scritto esplicitamente ("non applicabile, perché …"): non si omette.

1. **GIT E PR** — link della PR, stato, branch, hash dei commit, elenco dei
   file toccati con righe aggiunte e rimosse.
2. **TEST** — quanti test esistono, quanti passano, quali sono stati aggiunti o
   modificati. Per ogni test negativo: conferma di averlo fatto fallire di
   proposito disabilitando la logica che dovrebbe coprire, e l'esito.
3. **ESECUZIONE SUI DATI REALI** — comando esatto, durata, picco di memoria e
   output **integrale incollato**: non riassunto, non ricopiato a mano, non
   riformattato in una tabella scritta dall'assistente. Se il task non prevede
   esecuzione, dirlo.
4. **STATO DEL COLLECTOR** — PID prima e dopo, e conferma che il servizio non è
   stato fermato né riavviato.
5. **VINCOLI** — elenco dei vincoli del prompt, con conferma per ciascuno. Se
   uno è stato violato, dirlo.
6. **COSA HAI CAMBIATO** — file per file, col motivo.
7. **ASSUNZIONI** — ogni assunzione fatta.
8. **CAUSA RADICE** — se è stato corretto un difetto: la causa concreta e
   dimostrata, e la verifica che quella causa spieghi l'**entità** dell'errore
   osservato. Se non si riesce a ricostruirla, dichiararlo invece di proporre
   una spiegazione parziale.
9. **COSA POTREBBE ESSERE SBAGLIATO** — senza minimizzare. Se non c'è nulla,
   dirlo e indicare quale controllo avrebbe rilevato un problema.

**Regole sui numeri.** Grezzi, mai trascritti a mano. Se un numero non torna,
si dichiara invece di aggiustare la conclusione. Non si allargano le tolleranze
per far passare un controllo.

## Come si scrive un report

I nove campi restano quelli. Cambia quanto spazio prende ciascuno: chi legge
deve capire **in dieci secondi** se c'è qualcosa che non va, e scendere nei
dettagli solo dopo.

1. **Lunghezza proporzionale alla sorpresa.** Ciò che è andato come previsto sta
   in una riga. Lo spazio si spende su deviazioni, dubbi e output grezzo.
2. **Elenca le eccezioni, non le conferme.** Sui vincoli niente tabella riga per
   riga: si scrive "tutti i vincoli rispettati" e poi **solo** quelli violati,
   aggirati o interpretati, col motivo. Se sono tutti rispettati, il campo è una
   riga sola.
3. **Tetto di righe per i campi di routine.** GIT E PR, TEST, STATO DEL
   COLLECTOR e VINCOLI: **tre righe ciascuno** quando non c'è nulla di anomalo.
   Se ne servono di più è perché c'è una deviazione — va dichiarata in apertura
   del campo.
4. **Niente da incollare due volte.** Ogni informazione compare in un campo
   solo. Ciò che sta in CAUSA RADICE non si ripete in COSA HAI CAMBIATO.
5. **Niente output di tentativi già risolti.** Un errore capito e superato è una
   riga: cos'era e come è stato escluso. L'output grezzo si incolla per i
   risultati che contano e per ciò che non torna.
6. **Niente ricapitolazione del prompt.** Chi legge il report è chi ha scritto
   il compito.
7. **Niente preamboli né chiusure di cortesia.** Si comincia dal risultato.
   Nessun "lavoro completato", nessun "spero sia utile", nessun elenco di
   ringraziamenti a sé stessi.
8. **Cosa non va mai accorciato.** Queste regole non riducono mai: i numeri
   grezzi e l'output non trascritto a mano; ciò che potrebbe essere sbagliato;
   una causa radice che non spieghi l'**entità** dell'errore; un vincolo
   violato; un dubbio che si è avuto e messo a tacere. Fra breve e completo su
   uno di questi punti, si sceglie **completo**.
9. **Verifica prima di inviare.** Rileggere e togliere ogni frase che non cambia
   una decisione di chi legge. Un campo che si potrebbe cancellare senza perdere
   informazione va cancellato e sostituito con "nulla da segnalare".
