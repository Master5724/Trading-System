# Revisione del collector — Task 0

Revisione critica del collector contro la documentazione ufficiale Hyperliquid
(*For developers → API → WebSocket → Subscriptions* e *Timeouts and heartbeats*).
Il collector **non è stato eseguito** contro l'API: tutto quello che segue è
verificato sui doc e sui test, non sul traffico reale.

---

## A. Discrepanze rispetto alla documentazione

### A1. `bbo` espone `time`, e veniva buttato via
`exch_ts_of` gestiva `l2Book` ma non `bbo`, che ha lo stesso campo `time`.
Risultato: ogni riga `bbo` sarebbe stata scritta con `ts_exch_ms = 0`, cioè
allineabile solo sul clock locale. `bbo` è `false` in config oggi, quindi il
danno si sarebbe visto al primo giorno del ramo intraday — che è esattamente il
modulo in cui la differenza fra clock locale e clock exchange conta di più.
**Corretto.**

### A2. `activeAssetCtx` su asset spot risponde su un channel diverso
Sottoscrivendo `{"type":"activeAssetCtx","coin":"@107"}` l'exchange risponde con
`channel: "activeSpotAssetCtx"`, non con il tipo sottoscritto. `coin_of`
riconosceva solo `activeAssetCtx`, quindi quei messaggi sarebbero finiti in
`data/activeSpotAssetCtx/_global/` con la coin persa. Oggi il config ha solo
perp e il caso non si presenta; si presenterebbe in silenzio il giorno in cui si
aggiunge un ticker spot. **Corretto** (channel aggiunto, con test di regressione).

### A3. `userEvents` risponde sul channel `"user"`
Il nome della subscription e il nome del channel di risposta non coincidono.
Né `coin_of` né `exch_ts_of` lo conoscevano. **Corretto.**

### A4. `userFundings` e `orderUpdates` erano senza timestamp
Entrambi sono `true` in `user_channels`, ed entrambi cadevano nel ramo di
default di `exch_ts_of` → `0`. I campi giusti sono `fundings[0].time` e
`statusTimestamp` (il timestamp dell'ordine, `order.timestamp`, è un'altra cosa:
è quando l'ordine è stato piazzato, non quando è cambiato di stato).
Con `user_address: null` questi stream oggi non partono, quindi il bug era
latente e sarebbe emerso alla prima configurazione della API wallet.
**Corretto.**

### A5. `candle`: i doc dicono `Candle[]`, il server manda un oggetto
Il codice gestiva già entrambe le forme. Non è un bug — ma è il motivo per cui
va lasciato così, e adesso c'è un test che lo fissa in modo che nessuno
"semplifichi" scegliendone una.

### A6. `l2Book` accetta ora un flag `fast`
Presente nei doc, non usato. Non l'ho attivato di proposito: cambia la cadenza
dei push e quindi il regime dei dati registrati, e non si mescolano due regimi
nello stesso storico senza deciderlo. Segnalato, non toccato.

### A7. Ping e timeout — confermati corretti
Il server chiude una connessione ferma da 60s; il ping applicativo è
`{"method":"ping"}` → `{"channel":"pong"}`. `ping_seconds: 30` sta sotto metà
soglia (regge un ping perso), e `ping_interval=None` su `websockets` è giusto:
il ping del protocollo WebSocket non è quello che Hyperliquid conta. Nessuna
modifica, solo un commento in config perché il numero non venga "ottimizzato".

### A8. Gli snapshot iniziali dei canali utente si ripetono
I canali utente rispondono alla subscribe con un messaggio `isSnapshot: true`
contenente lo storico. Arriva **di nuovo a ogni riconnessione**: chi somma i
fill sullo storico Parquet senza deduplicare conta più volte gli stessi
riempimenti. Aggiunto `parsing.is_snapshot()` e documentato; la deduplica vera
è lavoro del catalogo (Task 1), non dell'ingest — qui si salva grezzo.

---

## B. Bug indipendenti dai doc

### B1. Il watchdog non riconnetteva (il più grave)
`config.yaml` prometteva «se NESSUN messaggio arriva per questi secondi →
reconnect forzato». Il codice loggava un errore e resettava il timer, e basta.
Una connessione zombie — aperta a livello TCP, morta a livello applicativo —
sarebbe rimasta lì per ore, con il processo vivo, il log quasi pulito e i
Parquet che non distinguono "mercato fermo" da "non stavamo ascoltando".
Ora il watchdog chiude la socket, `async for` termina e `_ws_loop` riconnette.
**È anche la ragione per cui i buchi vanno registrati e non dedotti dai dati.**

### B2. Una subscribe rifiutata era invisibile
`subscriptionResponse` veniva scartato senza confrontarlo con quanto inviato, e
un `error` veniva loggato ma non cambiava niente. Con 17 sottoscrizioni, una
rifiutata (coin delistata, typo, limite) significa una cartella che resta vuota
e di cui ci si accorge settimane dopo. Ora gli ack vengono tracciati e dopo
`subscription_ack_seconds` viene loggato l'elenco esatto di quelle mancanti.

### B3. Il task di backfill poteva sparire a metà
`asyncio.create_task(...)` senza conservare la reference: asyncio tiene solo un
riferimento debole, il task può essere raccolto dal GC mentre gira. Introdotto
`_spawn()` che mantiene il set dei task vivi.

### B4. `config.yaml` letto dalla cwd
Sotto systemd, con `WorkingDirectory` sbagliata, il collector moriva con
`FileNotFoundError` invece di partire con i default sicuri. Ora: argomento
esplicito → `HL_CONFIG` → `config.yaml` accanto al package.

### B5. Nessuna validazione di `network`
Un typo (`mainet`) dava un `KeyError` opaco; peggio, non c'era nessuna traccia
in log di quale rete fosse attiva. Per l'invariante 1 il default sicuro deve
essere **visibile**, non solo scritto. Ora `network` è validato e loggato a ogni
avvio insieme a `data_dir`.

### B6. Invariante 6 non era implementata
CLAUDE.md dice «i buchi nei dati sono dati» e il README diceva di registrare le
finestre di disconnessione, ma nessun codice lo faceva. → `collector/gaps.py`.

---

## C. Il registro dei buchi — `collector/gaps.py`

JSONL append-only in `data/_gaps.jsonl` (configurabile con `gaps_file`), **due
righe per finestra**:

```
{"event":"open","start_ms":..,"start_iso":..,"reason":"..","channels":[..]}
{"event":"close","start_ms":..,"end_ms":..,"end_iso":..,"duration_s":..,...}
```

Perché due righe e non una sola scritta a finestra chiusa: se il processo muore
mentre è scollegato, una riga scritta solo alla chiusura non esisterebbe mai — e
sparirebbe dal registro proprio il buco più lungo. Con due righe, un `open`
spaiato **è** il dato: finestra mai chiusa. Il processo successivo la adotta e
la chiude alla prima riconnessione.

Altre scelte, e il motivo:

- **`fsync` per riga.** Un registro dei buchi che si perde in un buffer del
  filesystem al riavvio della macchina è peggio di nessun registro: dice «nessun
  buco» quando il buco c'era. Il costo è irrilevante alla frequenza dei buchi
  (non lo sarebbe per i dati, infatti il writer non lo fa).
- **Riconnessioni fallite a ripetizione = una finestra sola**, dal primo
  distacco alla prima riconnessione riuscita. Spezzarla in tanti buchi corti
  maschera l'unica cosa che serve sapere: da quando a quando mancano i dati.
- **Lo shutdown pulito apre una finestra** che resta aperta fino al prossimo
  avvio. Un collector spento è un buco come gli altri.
- **`Gap.covers()` su finestra aperta copre fino a ora**: default conservativo,
  un periodo che non sappiamo classificare va scartato, non tenuto.
- **`load_windows()` salta le righe illeggibili**: un JSONL troncato da un kill
  a metà riga non deve rendere inutilizzabile lo storico precedente.

---

## D. Riorganizzazione delle cartelle

CLAUDE.md dichiarava già `collector/` nell'albero dell'architettura, ma i file
stavano piatti nella root. Allineati:

```
collector/__init__.py    vuoto di proposito (vedi sotto)
collector/__main__.py    python -m collector
collector/collector.py   processo, watchdog, riconnessione
collector/parsing.py     coin_of / exch_ts_of / truncate_book / is_snapshot
collector/writer.py      invariato
collector/backfill.py    invariato
tests/test_parsing.py    payload presi dai doc
tests/test_gaps.py
config.yaml              resta in root
```

Due scelte da motivare:

1. **`parsing.py` estratto da `collector.py`.** Sono le uniche funzioni che
   decidono *sotto quale chiave un dato finisce sul disco*: un errore lì non
   genera un'eccezione, genera mesi di dati archiviati male. Isolate, si testano
   senza `websockets` e senza `pyarrow` — infatti i test girano su una macchina
   dove pyarrow non è installato. Restano importabili da `collector.collector`
   per compatibilità.
2. **`__init__.py` vuoto.** Importare il package non deve tirarsi dietro
   pyarrow e websockets.

Non ho toccato lo schema Parquet, il layout delle partizioni, `writer.py` né
`backfill.py`.

---

## E. Cosa non ho fatto, e perché

- **Nessuna nuova dipendenza.** I test sono `unittest` della stdlib: girano con
  `python -m unittest discover -s tests`, e anche sotto pytest se un giorno lo si
  vorrà, senza che oggi sia obbligatorio installarlo.
- **Nessuna strategia, nessun ordine, nessuna modifica ai default di rete.**
- **Non ho toccato `l2_depth`, la lista delle coin, le cadenze**: sono scelte del
  proprietario, non parametri da tarare (invariante 7).

---

## F. Assunzioni, e cosa potrebbe essere ancora sbagliato

Questa sezione è quella che conta (CLAUDE.md, *definition of done*).

1. **Non ho eseguito il collector.** Le correzioni sono verificate contro i doc e
   i test unitari, non contro il traffico reale. La prima sessione va guardata a
   mano: se qualche sottoscrizione manca l'ack, adesso c'è una riga `ERROR` che
   lo dice esplicitamente — è la prima cosa da cercare nel log.
2. **`coin_of("trades", ...)` legge solo `data[0]`.** Assume che un messaggio
   `trades` non mescoli coin diverse, il che è vero per una subscribe per-coin.
   Se in futuro si sottoscrivesse un canale aggregato, questa funzione
   etichetterebbe l'intero batch con la coin del primo elemento — silenziosamente.
3. **`candle` usa `t` (apertura) e non `T`.** È la chiave stabile della barra fra
   un update e l'altro, ma significa che `ts_exch_ms` **si ripete** su più righe
   dello stesso minuto. Chi a valle assume un timestamp monotono per riga si
   troverà dei duplicati che non sono duplicati.
4. **L'audit delle sottoscrizioni confronta chiavi normalizzate** (campi
   ordinati). Se l'exchange rimandasse indietro l'ack con un campo normalizzato
   diversamente da come l'abbiamo inviato, l'audit segnalerebbe un falso
   positivo. È un log, non un blocco: nel dubbio rumore, non dati persi.
5. **Un `SIGKILL` non lascia traccia nel registro.** Lo shutdown pulito
   (SIGINT/SIGTERM) scrive la finestra; un kill -9 o un crash della macchina no,
   e quel buco resta invisibile a `gaps.jsonl`. Rilevarlo richiede il confronto
   fra l'ultimo timestamp presente nei Parquet e il primo della sessione
   successiva: è lavoro del catalogo (Task 1), e va fatto lì.
6. **Il registro dei buchi dice quando eravamo scollegati, non quando abbiamo
   perso messaggi.** Senza numeri di sequenza, una perdita a connessione aperta
   resta indistinguibile dal silenzio. Un buco non registrato è ancora possibile;
   quello che è cambiato è che i buchi *da disconnessione* ora sono espliciti.
7. **`_gaps.jsonl` sta dentro `data/`, che è in `.gitignore`.** È corretto (segue
   i dati che descrive) ma significa che nei backup va incluso esplicitamente:
   perdere il registro e tenere i Parquet produce dati che sembrano completi.
