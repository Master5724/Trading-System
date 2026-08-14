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
rilevatore esistesse.

Il log del collector resta comunque utile per il contesto: tienilo, non
ruotarlo via.

## Storage

Ordine di grandezza con 4 coin, `l2_depth: 10`, zstd: qualche centinaio di MB
al giorno, dominati da `l2Book`. Verifica sui tuoi dati dopo 24h e dimensiona
il disco su 6 mesi, non su una settimana. Se stringe, la leva e' `l2_depth`,
non il numero di coin.

## Prossimo passo

Non scrivere strategie. Dopo 3-4 settimane di dati:
1. notebook di sanity check — gap, distribuzione degli spread, funding per coin
2. scheletro del backtester event-driven che legge questi stessi Parquet
