# hl-collector

Collector 24/7 dei dati di mercato Hyperliquid. Fase 0: nessuna strategia, nessun ordine.

## Avvio

```bash
pip install pyarrow pyyaml websockets duckdb
python -m collector                              # config.yaml accanto al package (testnet)
python -m collector --config config.mainnet.yaml # mainnet
python -m collector --config /etc/hl/config.yaml # percorso esplicito (o env HL_CONFIG)
```

`config.yaml` resta su `network: testnet`: il default del repo non deve poter
puntare a mainnet per distrazione. `config.mainnet.yaml` e' identico tranne
quella riga, e va scelto esplicitamente.

I due profili scrivono in directory separate: `./data` contiene i dati testnet,
`./data-mainnet` quelli mainnet. Non vanno mai mescolati: le due reti hanno
liquidita', volumi e funding completamente diversi, e un catalogo che le
leggesse insieme tratterebbe due popolazioni distinte come una sola.

Test (solo stdlib, non serve installare niente):

```bash
python -m unittest discover -s tests -v
```

Con `pytest` installato nello stesso virtualenv si ottiene lo stesso esito piu'
il conteggio dei subtest, che `unittest` non riporta:

```bash
python -m pytest -q -s
```

Vale la pena dirlo perche' l'ambiguita' e' gia' costata un errore in un report:
i due comandi vanno lanciati **col python del virtualenv del progetto**
(`.venv/bin/python`), non con uno creato al volo altrove. Un interprete diverso
esegue codice diverso da quello che gira in produzione.

Parti su `network: testnet`. Passa a `mainnet` solo quando hai 24h di dati puliti
e il watchdog non ha loggato nulla di anomalo.

In produzione, systemd (non `nohup`): ti serve il restart automatico.

```ini
[Unit]
Description=hl-collector
After=network-online.target

[Service]
WorkingDirectory=/opt/hl-collector
ExecStart=/opt/hl-collector/.venv/bin/python -m collector
Restart=always
RestartSec=5
# Se la macchina resta senza memoria, il kernel sceglie un'altra vittima.
OOMScoreAdjust=-900

[Install]
WantedBy=multi-user.target
```

## Memoria: il collector ha la precedenza

`OOMScoreAdjust=-900` sposta la preferenza dell'OOM killer sugli altri
processi, ma **non e' un limite di memoria** e non impedisce il caso peggiore:
la macchina che va in affanno e il collector che resta *congelato* — vivo, con
la socket TCP aperta, senza scrivere niente. E' successo il 2026-08-14 per 82
minuti, e il registro dei buchi ha segnato 1.5s (ora c'e' un rilevatore
apposta, vedi sotto).

Quindi la regola, sulla macchina che ospita il collector:

> **Ogni job di analisi gira con un tetto di memoria esplicito.**

```bash
systemd-run --user --scope -p MemoryMax=2G \
    python -m catalog.report        # il job muore da solo prima di far male
```

`nice` e `ionice` non servono a questo: spostano la priorita' di CPU e di I/O,
la RAM non la limitano di un byte. Un notebook che carica un mese di `l2Book` in
un DataFrame prende tutta la memoria della macchina restando gentilissimo in
priorita'.

## Cosa raccoglie e perche'

| canale | cadenza | a cosa serve |
|---|---|---|
| `activeAssetCtx` | per blocco | funding, open interest, mark price — **il dato che comanda il P&L swing** |
| `trades` | per esecuzione | flusso reale, non ricostruibile a posteriori |
| `l2Book` | snapshot, min 0.5s | profondita' e spread → modello di slippage nel backtester |
| `candle 1m` | 1m | check di integrita' incrociato coi trade |
| `bbo` | su cambio BBO | serve solo al modulo intraday, off di default |
| `allMids` | continuo | prezzi di riferimento globali |

`l2Book` e' uno **snapshot completo**, non un delta: niente ricostruzione di
sequenza, ma volume alto. Per questo viene troncato a `l2_depth` livelli.

## Query

Nessun database da gestire, DuckDB legge i Parquet direttamente.

```sql
-- funding orario di SOL nell'ultima settimana
SELECT ts_local_ns//1000000000 AS ts,
       json_extract_string(raw, '$.ctx.funding')::DOUBLE AS funding
FROM read_parquet('data/activeAssetCtx/SOL/**/*.parquet')
ORDER BY ts;

-- costo di funding cumulato di un long tenuto 10 giorni
SELECT exp(sum(ln(1 + funding))) - 1 FROM (...);
```

Quel secondo calcolo e' la prima cosa da guardare quando avrai due settimane di
dati: ti dice, in numeri tuoi e non stimati, quanto deve muoversi il prezzo
perche' una posizione multi-giorno abbia senso.

## Il punto che si sottovaluta: i buchi

Il WebSocket non espone numeri di sequenza sui dati di mercato — non puoi
sapere se hai perso un messaggio. Il backfill REST ricuce solo cio' che l'API
espone storicamente (candele, funding). Trade e book persi sono persi.

Quindi: **registra le finestre di disconnessione ed escludile dal backtest.**
Un backtest che gira su un'ora in cui mancava meta' del flusso non da' errore,
da' un risultato falso e plausibile — che e' molto peggio.

Il collector le registra da solo in `data/_gaps.jsonl` (modulo `collector/gaps.py`),
JSONL append-only, un evento per riga: `open` all'istante del distacco,
`reconnect` quando la socket torna su, un `resume` per canale quando quel canale
ricomincia a produrre, `close` quando ha ripreso l'ultimo. Le si rilegge cosi':

```python
from collector.gaps import load_windows
for g in load_windows("data/_gaps.jsonl"):
    print(g.start_ms, g.end_ms, g.duration_s, g.reason, g.channels)
    print(g.duration_for("trades"))     # durata del buco su un singolo canale
```

`end_ms is None` significa finestra mai chiusa — il processo e' morto mentre era
scollegato. `Gap.covers(ts_ms)` la tratta come aperta fino a ora: e' il default
conservativo, un periodo che non sappiamo classificare va scartato.
`Gap.covers(ts_ms, channel="trades")` fa la stessa domanda per un canale solo.

### Una finestra finisce quando riprendono i dati, non quando torna la socket

Il registro chiudeva la finestra alla riconnessione. E' sbagliato, e si vede su
un caso reale: il 2026-08-08 alle 08:36:28 il canale `trades` e' rimasto muto
92s dopo una riconnessione mentre gli altri canali erano gia' ripartiti — nel
registro risultava un buco di 1.5s. Una finestra chiusa troppo presto e' peggio
di un buco non registrato: il backtest legge dati mancanti come dati validi.

Adesso ogni canale chiude la sua parte quando arriva il primo messaggio *suo*;
la finestra aggregata si chiude sull'ultimo, e `close` porta anche
`silence_after_reconnect_s` (quanto del buco e' avvenuto a socket gia' viva) e
`per_channel_duration_s`.

### Congelamenti del processo

Un processo fermo — swap, macchina in esaurimento di memoria, sospensione — con
la socket TCP ancora aperta non produce **nessun** errore e nessuna
riconnessione: e' invisibile per costruzione. L'unico segnale che resta e' il
tempo che salta: il watchdog si sveglia ogni `watchdog.tick_seconds` e, se fra
due risvegli sono passati piu' di `watchdog.freeze_jump_seconds` oltre
l'intervallo previsto (default 60s), scrive una finestra con
`cause: "process_freeze"` e `monotonic_jump_s`. Il salto si misura sull'orologio
monotono (NTP e cambi d'ora non lo spostano), i timestamp registrati vengono da
quello di sistema, perche' devono essere confrontabili coi dati.

La finestra parte dal risveglio precedente del watchdog: quando il processo si
sia fermato davvero non e' osservabile, e sovrastimare di un tick e' la
direzione giusta in cui sbagliare.

### Correzioni a mano

Il file e' append-only e le righe esistenti non si toccano mai. Una finestra
ricostruita a posteriori si aggiunge come riga singola e autoconclusiva:

```json
{"event":"manual","start_ms":..,"end_ms":..,"cause":"process_freeze","source":"manual_correction","note":".."}
```

`load_windows` la legge come finestra chiusa. Ce n'e' una in mainnet, per il
congelamento da OOM del 2026-08-14 15:55:29→17:18:08 UTC, avvenuto prima che il
rilevatore esistesse. Dura 4959 s ed e' l'interruzione piu' lunga dell'intera
raccolta: piu' del quadruplo della somma di tutte le altre.

### Regola di lettura di `_gaps.jsonl` (vale per chiunque, non solo per il catalogo)

> **Si considerano tutti i record che dichiarano una durata, qualunque sia il
> valore di `event`.** Un record dichiara una durata se ha `start_ms` e ha
> `end_ms` oppure `duration_s`.

Non e' una raffinatezza. Il file contiene almeno cinque tipi di evento e ne
guadagnera' altri: e' append-only e additivo per costruzione. Un'analisi che
filtra su `event == "close"` — la forma piu' naturale da scrivere, e quella
scritta davvero — salta la riga `manual` e **sottostima il tempo scollegato di
oltre un'ora**, senza nessun errore e senza nessun segnale: il risultato e'
solo... piu' bello.

Il modo giusto e' non scrivere un parser proprio:

```python
from collector.gaps import load_windows      # semantica di open/close/resume/manual
from catalog.gapwindows import load_with_audit

windows, audit = load_with_audit("data/_gaps.jsonl", now_ms)
audit["record_con_durata"]   # quanti record dichiaravano una durata
audit["recuperati"]          # quanti non erano coperti da nessuna finestra
```

`load_with_audit` rilegge il file una seconda volta di sola verifica e pretende
che ogni record con una durata sia contenuto in una finestra; quelli che non lo
sono diventano finestre a se' stanti (`origin = "recuperato"`) invece di sparire.
I due numeri dell'audit finiscono nel report del catalogo apposta: sono la prova
che il contratto e' stato rispettato in quel run, verificabile senza riaprire il
JSONL. Se un giorno `recuperati` e' maggiore di zero, il registro contiene un
evento che il parser condiviso non conosce — va guardato, non ignorato.

Il log del collector resta comunque utile per il contesto: tienilo, non
ruotarlo via.

### I buchi si derivano dai dati; il registro conferma e spiega

> **L'assenza di righe e' la misura. `_gaps.jsonl` e' una dichiarazione.** Un
> buco esiste se i dati mancano, non se il collector ha scritto che mancavano.
> Il registro serve a dire *perche'* la raccolta si e' fermata, non *se* si e'
> fermata.

L'ordine conta perche' il registro e' affidabile solo **dal 2026-08-14 20:33 in
poi**. Prima di quella data chiudeva le finestre alla riconnessione della socket
invece che alla ripresa effettiva dei dati (vedi sopra), quindi ne sottostimava
la durata — nel caso dell'8 agosto un'assenza reale di 92,9s su tutte e quattro
le coin di `trades` compariva come 1,5s. Un'esclusione statistica alimentata da
quel registro e' buona solo quanto il registro: quel buco restava dentro le
statistiche come intervallo valido.

Quindi il catalogo **misura prima e legge il registro dopo**. Per ogni
partizione (canale, coin) calcola il p99 degli intervalli fra righe consecutive
e chiama buco ogni intervallo che supera `--gap-p99-multiple` volte quel p99,
con un pavimento di `--gap-min-s` secondi. La soglia e' per partizione perche' su
un giorno reale il p99 di `trades/BTC` e' 2,8s e quello di `trades/ETH` 17,0s:
una soglia unica di canale sarebbe dettata dalla coin piu' lenta. Sono questi
buchi — non le finestre del registro — a essere esclusi dalla distribuzione
degli intervalli.

Il registro entra dopo, per la riconciliazione, che pubblica **tre insiemi
distinti** (punto 2.1 del report):

1. **buchi spiegati** — assenza nei dati che una finestra registrata copre. Per
   ognuno viene riportato di quanto la finestra **sottostima** la durata
   osservata: e' la misura diretta della qualita' del registro nel tempo,
   aggregata per giorno UTC;
2. **buchi non spiegati** — assenza nei dati che nessuna finestra tocca: la
   raccolta si e' fermata e il collector non se n'e' accorto;
3. **finestre senza assenza** — il registro dichiara un'interruzione che nei
   dati non si vede. Una finestra piu' corta della soglia di rilevamento e'
   invisibile per costruzione e non e' un errore; una lunga lo e'.

Questo **non contraddice l'invariante 8** ("il silenzio di uno stream non
significa mai assenza di eventi"). L'invariante vieta di concludere *non e'
successo niente* dal silenzio, e di prenderci decisioni operative. Qui il
silenzio porta alla conclusione opposta e conservativa: *non sappiamo cosa e'
successo*, quindi quell'intervallo esce dalle statistiche e viene stampato. Il
costo di un falso positivo e' un intervallo perso; quello di un falso negativo
e' una statistica che sembra sana.

Cosa questo metodo **non** usa: la simultaneita' fra canali, che sarebbe il
segnale piu' forte — un'interruzione della raccolta ferma tutti i canali nello
stesso istante, una pausa di mercato no. Il rilevamento e' per partizione e
indipendente, quindi un silenzio isolato su una coin poco scambiata puo'
comparire fra i non spiegati. Il report mostra gli estremi cosi' che il caso si
riconosca (stessi estremi su quattro coin = interruzione), ma non lo decide.

## Catalogo (`python -m catalog`)

Report di integrita' sui parquet, in sola lettura sulla directory dati:

```bash
python -m catalog --data-dir /home/ubuntu/hl-data/mainnet --out-dir ~/hl-reports
```

Produce `report.txt`, `summary.json`, `hourly_metrics.parquet` (una riga per
canale/coin/ora), `intervals.parquet` (una riga per canale/coin) e
`derived_gaps.parquet` (una riga per buco osservato: canale, coin, estremi,
durata, soglia in vigore). Tre scelte che conviene conoscere prima di leggerlo:

**I buchi vengono misurati sui dati, non letti dal registro** — vedi la sezione
sopra. Le due manopole sono `--gap-p99-multiple` (default 5) e `--gap-min-s`
(default 30). Non sono tarate su un risultato: il fattore viene dalla forma
della distribuzione (sui dati reali il p999 sta circa a 1,4 volte il p99, quindi
la coda naturale muore ben prima di 5 volte), il pavimento esiste perche' su
`activeAssetCtx` cinque volte il p99 sono sette secondi e sette secondi di
silenzio non sono un'interruzione. Il report stampa per ogni partizione il p99
misurato, la soglia applicata e quanti buchi ha trovato: chi non e' d'accordo
puo' cambiarli senza rifare i conti a mano.

**`low_volume` si alza solo sui canali a cadenza fissa.** `l2Book`,
`activeAssetCtx` e `allMids` arrivano a intervalli regolari qualunque cosa
faccia il mercato: un'ora sotto il 90% della mediana e' un'anomalia di
raccolta. Su `trades` e `candle` il volume orario *e'* il mercato — fra un
rilascio macro e le quattro del mattino di domenica cambia di multipli — e la
stessa soglia produce centinaia di ore marcate che non dicono niente. Per quei
canali il report pubblica comunque la statistica (`median_rows`, `rows_ratio`,
`below_median`) ma non marca l'ora. L'elenco si cambia con
`--fixed-rate-channels l2Book,allMids` (stringa vuota = nessun canale).

**I trade si leggono deduplicati per `tid`.** Dopo una riconnessione il server
rimanda gli ultimi scambi: sui dati mainnet e' circa lo 0,2% dei trade. I file
su disco non si toccano — sono la registrazione onesta di cio' che e' arrivato —
e la dedup avviene in lettura:

```python
from catalog.trades import dedup_sql
con.execute(f"SELECT count(*) FROM {dedup_sql(data_dir, 'BTC')}")
```

Si tiene la **prima** consegna (`ts_local_ns` minimo): e' quella che un sistema
live avrebbe visto, e tenere la ritrasmissione daterebbe il trade fino a decine
di secondi dopo il fatto. E' da qui che il backtester leggera' i trade, per lo
stesso motivo per cui il modello di costo stara' in un modulo solo. Il report
riporta per coin le consegne in eccesso e `tid_incoerenti` (stesso `tid`,
contenuto diverso): quel numero deve restare 0, altrimenti la dedup sta
scegliendo fra due verita'.

## Storage

Ordine di grandezza con 4 coin, `l2_depth: 10`, zstd: qualche centinaio di MB
al giorno, dominati da `l2Book`. Verifica sui tuoi dati dopo 24h e dimensiona
il disco su 6 mesi, non su una settimana. Se stringe, la leva e' `l2_depth`,
non il numero di coin.

## Il backtester

`backtest/` e' il motore event-driven che legge questi stessi Parquet. Sola
lettura sulla directory dati, un ordine di eventi per timestamp locale, e il
modello di costo importato da `costs/` senza duplicarne una riga.

```
.venv/bin/python -m backtest --data-dir /home/ubuntu/hl-data/mainnet \
    --coins BTC --from 2026-08-16T00:00 --to 2026-08-17T00:00 \
    --strategy random --out /tmp/bt
```

La strategia di default e' `flat` e non manda ordini: le altre (`random`,
`always_long`, `maker`) sono **fittizie**, servono a misurare il motore. Il
riepilogo stampa il residuo di conservazione: se non e' zero al centesimo, il
resto dei numeri non va letto. Le regole che il motore non negozia — niente
fill inventati, maker solo per attraversamento, niente look-ahead, buchi non
attraversati — stanno nelle docstring di `backtest/engine.py` e
`backtest/fills.py`.

## Prossimo passo

Non scrivere strategie. Dopo 3-4 settimane di dati:
1. notebook di sanity check — gap, distribuzione degli spread, funding per coin
2. client di esecuzione su testnet, con lo stesso motore usato dal backtest
