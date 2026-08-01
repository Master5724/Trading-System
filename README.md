# hl-collector

Collector 24/7 dei dati di mercato Hyperliquid. Fase 0: nessuna strategia, nessun ordine.

## Avvio

```bash
pip install pyarrow pyyaml websockets duckdb
python collector.py            # legge config.yaml dalla cwd
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
ExecStart=/opt/hl-collector/.venv/bin/python collector.py
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
da' un risultato falso e plausibile — che e' molto peggio. Il log del
collector e' la fonte di verita': tienilo, non ruotarlo via.

## Storage

Ordine di grandezza con 4 coin, `l2_depth: 10`, zstd: qualche centinaio di MB
al giorno, dominati da `l2Book`. Verifica sui tuoi dati dopo 24h e dimensiona
il disco su 6 mesi, non su una settimana. Se stringe, la leva e' `l2_depth`,
non il numero di coin.

## Prossimo passo

Non scrivere strategie. Dopo 3-4 settimane di dati:
1. notebook di sanity check — gap, distribuzione degli spread, funding per coin
2. scheletro del backtester event-driven che legge questi stessi Parquet
