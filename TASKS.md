# Sequenza task per Claude Code

Un task per sessione. Non accorparli: la ragione per cui questi progetti
falliscono non è che il codice non compila, è che nessuno si accorge di quando
ha smesso di avere senso.

Prima di ogni task: `git checkout -b task-N`. Alla fine, leggi il diff.

---

## Task 0 — Hardening del collector

> Leggi CLAUDE.md. Il collector in `collector/` funziona ma non è mai stato
> eseguito contro l'API reale. Fai una revisione critica: verifica i nomi dei
> canali e la forma dei payload contro la documentazione ufficiale Hyperliquid
> (WebSocket subscriptions), correggi le discrepanze, e aggiungi test unitari
> su `coin_of`, `exch_ts_of` e `truncate_book` usando payload di esempio presi
> dalla documentazione.
> Aggiungi un modulo che registra in un file JSONL ogni finestra di
> disconnessione (inizio, fine, durata, canali coinvolti).
> Non riscrivere l'architettura. Dimmi cosa hai trovato di sbagliato.

**Accetti se:** ti elenca discrepanze concrete rispetto ai doc, non
"ho migliorato la robustezza".

---

## Task 1 — Catalogo e integrità dei dati

> Costruisci `catalog/`: un modulo che scansiona `data/` e produce un report
> di integrità per coin e per canale — copertura oraria, buchi rilevati,
> incroci fra le finestre di disconnessione del collector e i dati presenti,
> distribuzione degli spread, statistiche del funding.
> Output: un comando CLI che stampa il report e salva un Parquet riassuntivo.
> Il report deve rendere impossibile usare inconsapevolmente un'ora incompleta.

**Rivedi tu:** la definizione di "ora incompleta". È una scelta tua, non sua.

---

## Task 2 — Modello di costo

> Implementa `costs/` secondo l'invariante 4 di CLAUDE.md: un modulo unico con
> fee maker/taker (schedule Hyperliquid, tier configurabile), funding orario
> applicato sulle posizioni aperte, e un modello di slippage derivato dagli
> snapshot L2 registrati — non una costante.
> Include test che verificano il costo di un round-trip noto contro un calcolo
> a mano, e il funding cumulato di una posizione tenuta 10 giorni contro i dati
> reali di `activeAssetCtx`.

**Questo file lo leggi riga per riga.** È il modulo che decide se crederai ai
tuoi backtest.

---

## Task 3 — Backtester event-driven

> Costruisci `backtest/`: motore event-driven che consuma i Parquet in ordine
> di timestamp, importa `costs/` senza duplicarne la logica, supporta ordini
> limit e market, e produce equity curve e giornale operazioni.
> Regola di fill: taker eseguito al book registrato; maker eseguito solo se un
> trade successivo è passato *attraverso* il livello, mai se lo ha solo toccato.
> Implementa tutti i test obbligatori elencati in CLAUDE.md.
> Non implementare strategie: solo il motore e una strategia fittizia per i test.

**Accetti solo se** i test adversarial passano davvero. Fai fallire di
proposito il test dello shuffle commentando una riga e verifica che diventi
rosso — un test che non può fallire non è un test.

---

## Task 4 — Client di esecuzione (testnet)

> Costruisci `execution/`: wrapper sull'SDK ufficiale hyperliquid-python-sdk.
> Chiave API wallet da env. Default testnet + dry_run.
> Requisiti: ordini idempotenti (client order id), riconciliazione dello stato
> reale delle posizioni all'avvio (mai fidarsi dello stato interno dopo un
> restart), gestione esplicita dei rifiuti, e logging di ogni richiesta e
> risposta su file.
> Nessuna logica di strategia. Nessun ordine inviato senza flag esplicito.

**Nota:** prima di fidarsi degli ack sui canali utente (`userFills`,
`orderUpdates`), verificare come il server rimanda indietro l'indirizzo
nell'eco della subscribe: una capitalizzazione diversa (checksum contro
minuscolo) farebbe fallire il match per inclusione e produrrebbe un falso
allarme proprio al momento della configurazione della API wallet.

**Rivedi tu:** il percorso di firma e la gestione dei nonce. È l'altro punto
dove un bug non ti dà un errore, ti dà una posizione che non volevi.

---

## Task 5 — Risk layer e kill switch

> Costruisci `risk/`: limiti hard applicati prima di ogni ordine (perdita
> giornaliera max, drawdown max, notional max, leva max, numero max di
> posizioni) e un kill switch che chiude tutto e disabilita nuove entrate.
> Trigger del kill switch: superamento limiti, dati stantii oltre soglia,
> divergenza fra stato interno e stato riconciliato dall'exchange, eccezioni
> ripetute.
> Lo stato "disabilitato" deve persistere su disco: un restart non lo azzera.
> Verifica che stop e take-profit siano ordini nativi sull'exchange
> (invariante 3).

**L'ultimo punto è quello che conta.** Se un restart riabilita il sistema, il
kill switch non esiste.

---

## Task 6 — Runner paper

> Costruisci `runner/`: esecuzione live-shadow. Consuma lo stream reale,
> genera ordini, li registra ma non li invia, e li valuta con lo stesso
> modulo `costs/` del backtester.
> Produce un confronto giornaliero paper vs backtest sulla stessa finestra.
> Se i due divergono oltre soglia, lo segnala.

Poi ti fermi. Le strategie vengono dopo, quando la pipeline è provata.

---

## Cosa NON delegare

Rivedi personalmente, riga per riga: `costs/`, `risk/`, e il percorso di firma
in `execution/`. Sono i tre punti in cui un errore non produce un'eccezione:
produce un numero plausibile e sbagliato, o una posizione che non volevi.
Tutto il resto puoi accettarlo leggendo il diff.
