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

**Requisito trasversale — ogni lettura di dati che alimenta un risultato deve
avere un conteggio atteso con cui confrontarsi.** Vale per `catalog/`,
`costs/`, `backtest/` e per tutto ciò che verrà dopo, non solo per il modulo in
cui è stato scritto.

Il motivo è un difetto vero, trovato nel Task 3: il feed del backtester
consegnava 20.000 trade su 108.168 perché un cursore DuckDB veniva invalidato
da una query successiva. Nessuna eccezione, nessun log, un risultato calcolato
su un quinto dei dati e perfettamente plausibile. La correzione ha eliminato
*quella* causa, e un test verifica che nessun altro modulo tenga aperto un
result set — ma quel test dimostra solo che nessuno usa quel meccanismo. Non
dimostra che ogni query restituisca le righe che dovrebbe: un `WHERE`
sbagliato, un glob che non copre un giorno, una `date=` che non esiste danno
tutti un conteggio basso e credibile.

L'unica difesa che non dipende dall'aver previsto la causa è **confrontare il
numero di righe lette con un numero atteso calcolato per un'altra strada**, e
fermarsi se non torna — come fa `backtest.feed._verifica`, che confronta le
righe consegnate con quelle contate sulla finestra. Oggi quel confronto esiste
solo lì. Dove non esiste, un conteggio va almeno **riportato**: un numero che
nessuno guarda non è un controllo.

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
>
> **Riconciliazione dopo ogni riconnessione.** La riconciliazione dello stato
> via REST non va fatta solo all'avvio, ma dopo OGNI riconnessione del
> WebSocket. Evidenza: dopo una riconnessione un singolo canale può restare
> muto oltre 100 secondi mentre gli altri funzionano (misurato l'8 e il 15
> agosto 2026, su canali diversi). Se quel canale è `userFills` o
> `orderUpdates`, il sistema resterebbe convinto di non avere fill mentre ne
> ha, o di avere una posizione diversa da quella reale. Lo stato operativo va
> sempre riletto dall'exchange, mai dedotto dal silenzio dello stream.

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
>
> **Trigger aggiuntivo — canale fermo oltre soglia.** Se un canale necessario
> all'operatività non produce dati oltre una soglia configurata, il sistema
> deve considerarsi cieco e attivare il kill switch, anche se la connessione
> risulta viva e gli altri canali funzionano. Il silenzio di un singolo canale
> è già stato osservato due volte in due settimane di raccolta.
>
> **Come si sceglie la soglia di staleness.** Non è un numero fisso e non va
> indovinata: si deriva dai dati già raccolti. Il catalogo misura la
> distribuzione degli intervalli fra messaggi per ogni canale e coin; la soglia
> è un multiplo del p99 osservato per quel canale, ricalcolato periodicamente
> dallo storico invece che scritto a mano nel config. Un canale che rallenta
> perché il mercato è tranquillo non deve far scattare nulla; un canale che si
> ferma sì.
>
> I canali si dividono in due classi con difese diverse:
>
> 1. **Canali di mercato** (`l2Book`, `activeAssetCtx`, `allMids`, `trades`,
>    `candle`). Hanno una cadenza misurabile, quindi ammettono una soglia di
>    staleness derivata come sopra.
>
> 2. **Canali utente** (`userFills`, `orderUpdates`). NON hanno cadenza
>    naturale: possono legittimamente tacere per giorni, semplicemente perché
>    non ci sono stati fill. Per questi nessuna soglia è possibile, e il
>    silenzio non è mai informazione. L'unica difesa è la riconciliazione REST
>    periodica dello stato: posizioni, ordini aperti ed equity vanno riletti
>    dall'exchange a intervalli regolari, indipendentemente da cosa arrivi (o
>    non arrivi) sullo stream.
>
> Conseguenza pratica: il kill switch usa la staleness per i canali di mercato,
> e il disaccordo fra stato interno e stato riconciliato via REST per i canali
> utente.

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

Appendice — Idee parcheggiate (NON sono task)
Nota per chiunque legga questo file, umano o agente: questa sezione non
contiene lavoro da svolgere. Sono idee registrate per non perderle, con le
ragioni per cui non si fanno adesso. Non implementare nulla di quanto segue
senza una richiesta esplicita che citi questa sezione.
Operazioni on-chain su Solana
Idea. Affiancare al sistema su Hyperliquid delle operazioni direttamente
sulla blockchain Solana, per diversificare e sfruttare l'attività di quel
mercato.
Perché è parcheggiata. Tre ragioni, in ordine di peso.
La volatilità non è rendimento. Un mercato molto movimentato non produce
ritorni maggiori: senza un edge è solo un costo, perché aumenta lo slippage e
moltiplica le occasioni di sbagliare alla stessa velocità con cui moltiplica
quelle di indovinare. La volatilità amplifica un edge già dimostrato, non lo
crea.
Un secondo venue non è diversificazione. Comprare SOL spot su un DEX
Solana e comprare il perp SOL su Hyperliquid è la stessa scommessa sulla
stessa beta, eseguita due volte. La diversificazione vera nasce da flussi di
rendimento scorrelati. Quello che si aggiunge davvero è una seconda pipeline
dati, un secondo modello di costo, un secondo client di esecuzione, un secondo
insieme di modi di rompersi e una contabilità fiscale più complessa.
Il trading attivo on-chain ha un pavimento di costo più alto. Fee
dell'AMM, priority fee ed esposizione al MEV superano ampiamente il costo
taker di un perp DEX. On-chain conviene per detenere e per generare
rendimento, non per operare a orizzonti brevi.
Cosa invece sarebbe genuinamente diverso. Non il trading direzionale degli
stessi asset, ma le fonti di rendimento che su Hyperliquid non esistono:
Commissioni da liquidity provider sugli AMM: reddito da fee invece che da
direzione. Contropartita: impermanent loss.
Cattura del funding (basis trade): spot long contro perp short quando il
funding è positivo. Non si prevede la direzione, si incassa il costo pagato da
chi la prevede. È la direzione più promettente delle due, anche perché
utilizzerebbe SOL già detenuto.
Rischi da modellare prima di considerarla: liquidazione della gamba short,
esecuzione coordinata su due venue con latenze diverse, inversione di segno del
funding mentre la posizione è aperta, e il fatto che la gamba spot è
illiquidabile in modo indipendente.
Condizioni per riaprire il discorso. Tutte, non alcune:
il backtester supera i test avversariali (strategia nulla, casuale, shuffle)
almeno una strategia ha superato paper trading con divergenza spiegata rispetto
al backtest
esiste uno storico di funding sufficiente a stimare il rendimento del basis
trade sui dati raccolti, non su assunzioni
il sistema su un solo venue è stabile e non richiede più interventi frequenti
Finché queste condizioni non sono soddisfatte, allargare il perimetro sposta
lavoro dal problema irrisolto a uno nuovo, con l'impressione di avanzare.
