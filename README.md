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

[Install]
WantedBy=multi-user.target
```

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

Il collector le registra da solo in `data/_gaps.jsonl` (modulo `collector/gaps.py`):
due righe per finestra, `open` all'istante del distacco e `close` alla
riconnessione, con durata e canali coinvolti. Le si rilegge cosi':

```python
from collector.gaps import load_windows
for g in load_windows("data/_gaps.jsonl"):
    print(g.start_ms, g.end_ms, g.duration_s, g.reason, g.channels)
```

`end_ms is None` significa finestra mai chiusa — il processo e' morto mentre era
scollegato. `Gap.covers(ts_ms)` la tratta come aperta fino a ora: e' il default
conservativo, un periodo che non sappiamo classificare va scartato.

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
