# Task 3 — output integrale

L'output grezzo delle esecuzioni e dei test sta qui, non nel messaggio di
riepilogo. Non e' ricopiato a mano: e' il contenuto dei log, ripulito solo
dell'intestazione di `systemd-run`.

Tutte le esecuzioni sotto
`systemd-run --user --scope -p MemoryMax=2G nice -n 19 ionice -c 3`, in sola
lettura su `/home/ubuntu/hl-data/mainnet`, col collector attivo.

## Indice

- [Esecuzione A — 24 ore, strategia casuale](#a)
- [Esecuzione B — 12 ore che attraversano quattro ore inaffidabili](#b)
- [Esecuzione C — 6 ore, strategia maker](#c)
- [Esecuzione su quattro coin — il picco che era da misurare](#quattro)
- [Memoria e tempo: le tre fasi della correzione](#memoria)
- [Equivalenza dei buchi derivati con e senza limitazione ai giorni](#soglie)
- [Suite di test completa, con i numeri stampati da ogni test](#test)
- [Pavimento fisso a 30 s: quante ore in piu'](#pavimento)
- [Il 32% di varianza mancante](#varianza)
- [Quante righe legge `funding_series`](#funding-righe)
- [Le tre esecuzioni rifatte col pavimento fisso](#rifatte)
- [Suite completa dopo le modifiche](#suite4)
- [78, 15 e 0: quale conteggio rispondeva a quale domanda](#contraddizione)
- [I due runner della suite](#runner)
- [Dov'era `pytest`: cronologia dell'ambiente](#ambiente)

<a name="a"></a>
## Esecuzione A — 24 ore, strategia casuale

```
.venv/bin/python -m backtest --data-dir /home/ubuntu/hl-data/mainnet \
    --coins BTC --from 2026-08-16T00:00 --to 2026-08-17T00:00 \
    --bar-s 60 --strategy random --seed 7 --out /tmp/bt-A2
```

```
=== CONFIGURAZIONE ===
data_dir              /home/ubuntu/hl-data/mainnet
coin                  BTC
finestra              1786838400000000000 -> 1786924800000000000 (24.00 ore)
giorni letti          2026-08-15,2026-08-16,2026-08-17,2026-08-18 (finestra + 1 per lato)
barra                 60s
strategia             random (seed=7, notional=1000.0)
equity iniziale       10000.0
eta' max del book     30.0s
fee                   maker 0.00015 / taker 0.00045 (tier base)

=== DATI ===
righe book lette      16112
book inutilizzabili   0
trade deduplicati     108168
BTC: ore bloccate 1, regolamenti di funding noti 458, primo 496018, ultimo definitivo 496475
BTC: ore bloccate DENTRO la finestra 0 []
BTC: soglie di buco (s) [('activeAssetCtx', 30.0, 'minimo_assoluto'), ('l2Book', 30.0, 'minimo_assoluto'), ('trades', 34.33, 'p99')]

=== BARRE E ORDINI ===
barre                 1441
barre con decisione   1440
barre tutte bloccate  0
ordini                93
fill taker            94
fill maker            0
rifiuti               0 {}
chiusure d'ufficio    0 (fallite 0)
eta' del book sui fill (ms)  n=94 min=23.940705 p50=2429.447695 p90=4498.482813 max=5432.465797

=== CONTO ===
equity iniziale       10000.0
equity finale         9937.394571856803
PnL                   -62.60542814319706
realizzato            -4.107688037746322
fee                   58.49644581896039
funding               0.0012942864953808322 (13 regolamenti, 0 non noti)
residuo conservazione 5.4569682106375694e-12
posizione finale      {'BTC': 0.0}

=== RIPRODUCIBILITA' ===
digest sha256         0e5cb0dfacb3fd7f41849249ab97e9367c5022f57cba340432de95c2f3a66217
righe di giornale     107
righe di equity       1441
scritti               /tmp/bt-A2/journal.csv, /tmp/bt-A2/equity.csv

=== TEMPI E MEMORIA ===
contesto (gap+funding) 35.77s
simulazione            7.21s
picco RSS dopo contesto 339.2 MB
picco RSS a fine run    339.2 MB
memory_limit DuckDB     512MB
```

<a name="b"></a>
## Esecuzione B — 12 ore che attraversano quattro ore inaffidabili

Ore bloccate reali dentro la finestra: `496311`, `496312`, `496313`, `496316`
(2026-08-14 15:00, 16:00, 17:00, 20:00 UTC). Le 360 barre non decise contro i
240 minuti di quelle quattro ore sono le due ore in piu' dei regolamenti `h+1`
derivati da ore di campionamento bucate su `activeAssetCtx`.

```
.venv/bin/python -m backtest --data-dir /home/ubuntu/hl-data/mainnet \
    --coins BTC --from 2026-08-14T12:00 --to 2026-08-15T00:00 \
    --bar-s 60 --strategy always_long --out /tmp/bt-B2
```

```
=== CONFIGURAZIONE ===
data_dir              /home/ubuntu/hl-data/mainnet
coin                  BTC
finestra              1786708800000000000 -> 1786752000000000000 (12.00 ore)
giorni letti          2026-08-13,2026-08-14,2026-08-15,2026-08-16 (finestra + 1 per lato)
barra                 60s
strategia             always_long (seed=0, notional=1000.0)
equity iniziale       10000.0
eta' max del book     30.0s
fee                   maker 0.00015 / taker 0.00045 (tier base)

=== DATI ===
righe book lette      7115
book inutilizzabili   0
trade deduplicati     124242
BTC: ore bloccate 5, regolamenti di funding noti 458, primo 496018, ultimo definitivo 496475
BTC: ore bloccate DENTRO la finestra 4 [496311, 496312, 496313, 496316]
BTC: soglie di buco (s) [('activeAssetCtx', 30.0, 'minimo_assoluto'), ('l2Book', 30.0, 'minimo_assoluto'), ('trades', 34.363, 'p99')]

=== BARRE E ORDINI ===
barre                 721
barre con decisione   360
barre tutte bloccate  360
ordini                3
fill taker            6
fill maker            0
rifiuti               0 {}
chiusure d'ufficio    2 (fallite 0)
eta' del book sui fill (ms)  n=6 min=1813.343878 p50=4608.194798 p90=5042.381056 max=5415.616824

=== CONTO ===
equity iniziale       10000.0
equity finale         9995.360529407786
PnL                   -4.639470592213911
realizzato            -1.8767544871132176
fee                   2.7002791274682103
funding               0.062436977630040795 (5 regolamenti, 0 non noti)
residuo conservazione -1.8189894035458565e-12
posizione finale      {'BTC': 0.0}

=== RIPRODUCIBILITA' ===
digest sha256         ba7b9e809af179f6d14e6a61befd17268bd8d06c7ea21a51fe88a0dacb16894a
righe di giornale     11
righe di equity       721
scritti               /tmp/bt-B2/journal.csv, /tmp/bt-B2/equity.csv

=== TEMPI E MEMORIA ===
contesto (gap+funding) 18.23s
simulazione            6.69s
picco RSS dopo contesto 336.6 MB
picco RSS a fine run    336.6 MB
memory_limit DuckDB     512MB
```

Chiusura d'ufficio nel giornale, con la sua provenienza:

```
180,1786719600000000000,2026-08-14 15:00:00.000,fill,BTC,SELL,market,taker,chiusura_forzata:buco_derivato_dai_dati,0.015904699043332353,0.015904699043332353,62605.0,995.7216359573437,995.713683607822,0.44807115762351984,0.007952349521666176,-0.0,-4.294268741699735,,0.0,1786719594584383176,5415.616824,/home/ubuntu/hl-data/mainnet/l2Book/BTC/date=2026-08-14/hour=14/part-1786719567518087063.parquet,,,,cammino_sul_book,/home/ubuntu/hl-data/mainnet/l2Book/BTC/date=2026-08-14/hour=14/part-1786719567518087063.parquet#1786719594584383176
```

Curva di equity a cavallo del buco:

```
bar_idx,ts_ns,utc,cash,realized_pnl,fees_cum,funding_cum,unrealized,equity,pos_BTC,mark_BTC
179,1786719540000000000,2026-08-14 14:59:00.000,9999.525071769955,0.0,0.45000357855728473,0.024924651488282212,-4.2067928969614075,9995.318278872994,0.015904699043332353,62610.5
180,1786719600000000000,2026-08-14 15:00:00.000,9994.782731870631,-4.294268741699735,0.8980747361808046,0.024924651488282212,0.0,9994.782731870631,0.0,62605.5
```

<a name="c"></a>
## Esecuzione C — 6 ore, strategia maker

```
.venv/bin/python -m backtest --data-dir /home/ubuntu/hl-data/mainnet \
    --coins BTC --from 2026-08-16T00:00 --to 2026-08-16T06:00 \
    --bar-s 60 --strategy maker --out /tmp/bt-C2
```

```
=== CONFIGURAZIONE ===
data_dir              /home/ubuntu/hl-data/mainnet
coin                  BTC
finestra              1786838400000000000 -> 1786860000000000000 (6.00 ore)
giorni letti          2026-08-15,2026-08-16,2026-08-17 (finestra + 1 per lato)
barra                 60s
strategia             maker (seed=0, notional=1000.0)
equity iniziale       10000.0
eta' max del book     30.0s
fee                   maker 0.00015 / taker 0.00045 (tier base)

=== DATI ===
righe book lette      4028
book inutilizzabili   0
trade deduplicati     15084
BTC: ore bloccate 1, regolamenti di funding noti 458, primo 496018, ultimo definitivo 496475
BTC: ore bloccate DENTRO la finestra 0 []
BTC: soglie di buco (s) [('activeAssetCtx', 30.0, 'minimo_assoluto'), ('l2Book', 30.0, 'minimo_assoluto'), ('trades', 37.69, 'p99')]

=== BARRE E ORDINI ===
barre                 361
barre con decisione   360
barre tutte bloccate  0
ordini                359
fill taker            1
fill maker            10
rifiuti               354 {'scaduto_senza_attraversamento': 354}
chiusure d'ufficio    0 (fallite 0)
eta' del book sui fill (ms)  n=11 min=2166.389369 p50=3006.991031 p90=3653.478254 max=3988.233257

=== CONTO ===
equity iniziale       10000.0
equity finale         9999.532988777319
PnL                   -0.46701122268132167
realizzato            1.4974116372022863
fee                   1.8140547762130255
funding               0.1503680836701038 (4 regolamenti, 0 non noti)
residuo conservazione -1.8189894035458565e-12
posizione finale      {'BTC': 0.0}

=== RIPRODUCIBILITA' ===
digest sha256         25caded75b508d9271633e61eae63d1d0942a90833902ed3d02f7350416c940e
righe di giornale     369
righe di equity       361
scritti               /tmp/bt-C2/journal.csv, /tmp/bt-C2/equity.csv

=== TEMPI E MEMORIA ===
contesto (gap+funding) 10.83s
simulazione            4.26s
picco RSS dopo contesto 283.5 MB
picco RSS a fine run    283.5 MB
memory_limit DuckDB     512MB
```

Un fill maker nel giornale, col `tid` del trade che ha attraversato:

```
97,1786844220000000000,2026-08-16 01:37:00.000,fill,BTC,BUY,limit,maker,maker,0.015859799373537924,0.00227,63006.98075,143.097395,143.02584630249999,0.021453876945374997,0.0,0.0,0.0,,0.00227,1786844217833610631,2166.389369,/home/ubuntu/hl-data/mainnet/l2Book/BTC/date=2026-08-16/hour=01/part-1786844185645805939.parquet,568048405097844,1786844387949754660,1786844387507,attraversamento,tid=568048405097844@1786844387949754660
```

<a name="quattro"></a>
## Esecuzione su quattro coin — il picco che era da misurare

24 ore su BTC, ETH, HYPE e SOL insieme. Strategia `flat`, che non manda
ordini: qui interessa il costo della LETTURA, non il PnL.

```
.venv/bin/python -m backtest --data-dir /home/ubuntu/hl-data/mainnet \
    --coins BTC,ETH,HYPE,SOL --from 2026-08-16T00:00 --to 2026-08-17T00:00 \
    --bar-s 60 --strategy flat --out /tmp/bt-4coin
```

```
=== CONFIGURAZIONE ===
data_dir              /home/ubuntu/hl-data/mainnet
coin                  BTC,ETH,HYPE,SOL
finestra              1786838400000000000 -> 1786924800000000000 (24.00 ore)
giorni letti          2026-08-15,2026-08-16,2026-08-17,2026-08-18 (finestra + 1 per lato)
barra                 60s
strategia             flat (seed=0, notional=1000.0)
equity iniziale       10000.0
eta' max del book     30.0s
fee                   maker 0.00015 / taker 0.00045 (tier base)

=== DATI ===
righe book lette      64448
book inutilizzabili   0
trade deduplicati     318561
BTC: ore bloccate 1, regolamenti di funding noti 458, primo 496018, ultimo definitivo 496475
BTC: ore bloccate DENTRO la finestra 0 []
BTC: soglie di buco (s) [('activeAssetCtx', 30.0, 'minimo_assoluto'), ('l2Book', 30.0, 'minimo_assoluto'), ('trades', 34.33, 'p99')]
ETH: ore bloccate 1, regolamenti di funding noti 458, primo 496018, ultimo definitivo 496475
ETH: ore bloccate DENTRO la finestra 0 []
ETH: soglie di buco (s) [('activeAssetCtx', 30.0, 'minimo_assoluto'), ('l2Book', 30.0, 'minimo_assoluto'), ('trades', 69.323, 'p99')]
HYPE: ore bloccate 1, regolamenti di funding noti 458, primo 496018, ultimo definitivo 496475
HYPE: ore bloccate DENTRO la finestra 0 []
HYPE: soglie di buco (s) [('activeAssetCtx', 30.0, 'minimo_assoluto'), ('l2Book', 30.0, 'minimo_assoluto'), ('trades', 36.684, 'p99')]
SOL: ore bloccate 1, regolamenti di funding noti 458, primo 496018, ultimo definitivo 496475
SOL: ore bloccate DENTRO la finestra 0 []
SOL: soglie di buco (s) [('activeAssetCtx', 30.0, 'minimo_assoluto'), ('l2Book', 30.0, 'minimo_assoluto'), ('trades', 109.694, 'p99')]

=== BARRE E ORDINI ===
barre                 1441
barre con decisione   1440
barre tutte bloccate  0
ordini                0
fill taker            0
fill maker            0
rifiuti               0 {}
chiusure d'ufficio    0 (fallite 0)
eta' del book sui fill (ms)  n=0 min=None p50=None p90=None max=None

=== CONTO ===
equity iniziale       10000.0
equity finale         10000.0
PnL                   0.0
realizzato            0.0
fee                   0.0
funding               0.0 (0 regolamenti, 0 non noti)
residuo conservazione 0.0
posizione finale      {'BTC': 0.0, 'ETH': 0.0, 'HYPE': 0.0, 'SOL': 0.0}

=== RIPRODUCIBILITA' ===
digest sha256         1d19c165fe6d5dd9140fcb5ad9f6bf7d04814577f00e85ebd913f569341e753e
righe di giornale     0
righe di equity       1441
scritti               /tmp/bt-4coin/journal.csv, /tmp/bt-4coin/equity.csv

=== TEMPI E MEMORIA ===
contesto (gap+funding) 97.46s
simulazione            23.94s
picco RSS dopo contesto 599.6 MB
picco RSS a fine run    599.6 MB
memory_limit DuckDB     512MB
```

<a name="memoria"></a>
## Memoria e tempo: le tre fasi della correzione

Stessa esecuzione A, stesso seme, **stesso digest** `0e5cb0df…` in tutte e tre.

| fase | dedup trade | derivazione buchi | tetto DuckDB | picco RSS | contesto | simulazione |
|---|---|---|---|---|---|---|
| iniziale | partizione intera | storico intero | 1GB | 1227.3 MB | 35.71s | 38.72s |
| dedup limitata | finestra + 1 g | storico intero | 512MB | 721.0 MB | 37.66s | 7.63s |
| + scansione limitata | finestra + 1 g | finestra + 1 g | 512MB | **339.2 MB** | 35.77s | 7.21s |

**L'esecuzione A gira su UNA coin, BTC**, 24 ore, strategia `random` seme 7.
I 339,2 MB della terza riga sono quindi il picco di una coin sola: non sono la
stessa misura dei 599,6 MB qui sotto a scala diversa, sono un altro
esperimento. Il confronto fra le tre righe resta valido perche' e' sempre la
stessa coin e la stessa finestra.

Quattro coin, stessa finestra: picco **599.6 MB**, contesto 97.46s, simulazione 23.94s.

In tutte le esecuzioni `picco RSS dopo contesto` == `picco RSS a fine run`: il
massimo si tocca leggendo, non simulando.

Output integrale dell'esecuzione iniziale, per confronto:

```
=== CONFIGURAZIONE ===
data_dir              /home/ubuntu/hl-data/mainnet
coin                  BTC
finestra              1786838400000000000 -> 1786924800000000000 (24.00 ore)
barra                 60s
strategia             random (seed=7, notional=1000.0)
equity iniziale       10000.0
eta' max del book     30.0s
fee                   maker 0.00015 / taker 0.00045 (tier base)

=== DATI ===
righe book lette      16112
book inutilizzabili   0
trade deduplicati     108168
BTC: ore bloccate 8, regolamenti di funding noti 435, primo 496018, ultimo definitivo 496452
BTC: ore bloccate DENTRO la finestra 0 []

=== BARRE E ORDINI ===
barre                 1441
barre con decisione   1440
barre tutte bloccate  0
ordini                93
fill taker            94
fill maker            0
rifiuti               0 {}
chiusure d'ufficio    0 (fallite 0)
eta' del book sui fill (ms)  n=94 min=23.940705 p50=2429.447695 p90=4498.482813 max=5432.465797

=== CONTO ===
equity iniziale       10000.0
equity finale         9937.394571856803
PnL                   -62.60542814319706
realizzato            -4.107688037746322
fee                   58.49644581896039
funding               0.0012942864953808322 (13 regolamenti, 0 non noti)
residuo conservazione 5.4569682106375694e-12
posizione finale      {'BTC': 0.0}

=== RIPRODUCIBILITA' ===
digest sha256         0e5cb0dfacb3fd7f41849249ab97e9367c5022f57cba340432de95c2f3a66217
righe di giornale     107
righe di equity       1441
scritti               /tmp/bt-run1/journal.csv, /tmp/bt-run1/equity.csv

=== TEMPI E MEMORIA ===
contesto (gap+funding) 35.71s
simulazione            38.72s
picco RSS del processo 1227.3 MB

real	1m14.665s
user	0m53.101s
sys	0m13.449s
```

<a name="soglie"></a>
## Equivalenza dei buchi derivati con e senza limitazione ai giorni

La soglia oltre la quale un intervallo e' un buco e' un multiplo del p99 degli
intervalli osservati, con un pavimento a 30 s. Leggere meno giorni cambia il
p99, quindi puo' cambiare la soglia: qui si verifica che i buchi trovati sulla
stessa finestra siano gli stessi, e si misura di quanto le soglie si spostano.

```
### BTC, finestra dell'esecuzione B (12 h con buchi reali)
giorni letti col margine: ['2026-08-13', '2026-08-14', '2026-08-15', '2026-08-16']
partizione                    storico     finestra
activeAssetCtx/BTC              30.00        30.00
l2Book/BTC                      30.00        30.00
trades/BTC                      30.00        34.36   <-- cambia
buchi che toccano la finestra: storico 6, finestra 6, IDENTICI: True
   activeAssetCtx/BTC  4961.6 s
   activeAssetCtx/BTC  97.1 s
   l2Book/BTC  4963.2 s
   l2Book/BTC  98.0 s
   trades/BTC  4962.1 s
   trades/BTC  97.9 s
tempo (ordinamento + soglie + buchi): storico 98.9s, finestra 5.7s

### Quattro coin, finestra dell'esecuzione A (24 h)
giorni letti col margine: ['2026-08-15', '2026-08-16', '2026-08-17', '2026-08-18']
partizione                    storico     finestra
activeAssetCtx/BTC              30.00        30.00
activeAssetCtx/ETH              30.00        30.00
activeAssetCtx/HYPE             30.00        30.00
activeAssetCtx/SOL              30.00        30.00
l2Book/BTC                      30.00        30.00
l2Book/ETH                      30.00        30.00
l2Book/HYPE                     30.00        30.00
l2Book/SOL                      30.00        30.00
trades/BTC                      30.00        34.33   <-- cambia
trades/ETH                      50.08        69.32   <-- cambia
trades/HYPE                     32.07        36.68   <-- cambia
trades/SOL                      82.48       109.69   <-- cambia
buchi che toccano la finestra: storico 0, finestra 0, IDENTICI: True
tempo (ordinamento + soglie + buchi): storico 318.7s, finestra 57.3s
```

<a name="test"></a>
## Suite di test completa

`pytest -q -s`. 321 test, 1733 subtest, zero falliti.

```
.............
[nulla] equity 10000.0 -> 10000.0  PnL 0.0  fee 0.0  funding 0.0  fill 0  ordini 0  barre 121
.
[casuale] seed mercato 3, seed strategia 11, barre 401, ordini 48, fill 49
[casuale] netto -44.30195095185263  atteso -36.24073061756545  scarto -8.061220334287185 (netto peggiore dell'atteso del 22.24% del conto) = -0.712 sigma (sigma 11.318181925517612)
[casuale] fee 29.642243751945827  spread 6.59848686561962  impact 0.0  lordo_al_mid -8.061220334272921
.
[casuale x100] media degli scarti 1.0051320602131486, deviazione standard campionaria 9.245025187867975, sigma teorico medio 11.194678134882036
[casuale x100] media in unita' di sigma: +0.0898 sigma del singolo tiro, +0.898 errori standard della media (errore standard 1.1194678134882037)
[casuale] progressivo N= 20: media   +5.3586, errore standard  2.5634, t +2.090
[casuale] progressivo N= 50: media   +1.8774, errore standard  1.5804, t +1.188
[casuale] progressivo N=100: media   +1.0051, errore standard  1.1195, t +0.898
.
[shuffle] etichette vere: lordo 230.13872379503965  t 16.60  netto 6.920416111384839
[shuffle] 20 permutazioni: |t| max 1.87, media t +0.056 (attesa 0 +/- 0.224), tutti i t [1.37, 0.84, 1.13, -0.34, 0.77, -0.23, 1.48, -0.01, -1.87, -1.55, 0.52, 0.64, -1.08, -1.1, 0.02, 0.14, 0.43, 1.08, -0.39, -0.72]
..
[look-ahead] offset 0 ns -> LookAheadError
[look-ahead] messaggio: richiesto un dato a ts=1785999600000000000 con decisione a ts=1785999600000000000: mancano 0 ns al futuro. La decisione usa solo dati STRETTAMENTE precedenti.
.
[look-ahead] 30 letture del book, distanza massima dal futuro -5000000000 ns (deve essere < 0)
.[look-ahead] offset -1 ns -> nessuna eccezione, barre decise 10
.
[conservazione] membro sinistro (equity finale)  9960.179849457238
[conservazione] membro destro  10000.0 + -13.631397568352677 - 26.201294844438163 - -0.012541870026746987 = 9960.179849457234
[conservazione] differenza 3.637978807091713e-12  (45 fill, 3 regolamenti)
.
[conservazione] posizione finale 0.0, chiusure d'ufficio 0
.
[taker] px 100.03227831942769  atteso 100.03227831942769  spread 0.015000000000007674  impact 0.0049999999999954525
.[maker] ordine 1.0, trade 0.25 -> eseguito 0.25
.[profondita'] rifiuti {'profondita_insufficiente': 4}  fill 0
.
[freschezza] soglia 30s  rifiuti {'book_troppo_vecchio': 9}  eta' massima registrata 570000.0 ms
.
[maker] limite oltre il best ask -> {'ordine_non_valido': 4}
.
[maker] limite 99.95  trade 99.949  fill maker 1
.[maker] limite 99.95  trade esattamente al limite  fill maker 0  rifiuti {'scaduto_senza_attraversamento': 1}
.
[buco] ora bloccata 496112  barre dentro 60  barre non decise 60  chiusure d'ufficio 1 (fallite 0)
.[buco] rate noti per 2 ore su 2  barre tutte bloccate 60  chiusure 1
.
[determinismo] digest A b3420a9ed27651cec0a11672b5d6f7d3cce5760662725a69b726dadd43402d2f
[determinismo] digest B b3420a9ed27651cec0a11672b5d6f7d3cce5760662725a69b726dadd43402d2f  righe 14
..
[feed] 6 eventi, 4 righe book, 3 trade, 1 book inutilizzabili
.[feed] tid consegnati [1001, 1002, 1002, 1003] -> letti [1001, 1002, 1003]
....[feed] date lette per una finestra di un minuto: ['2026-08-14', '2026-08-15', '2026-08-16']
.
[mut:guardiano] nessuna eccezione, barre decise 10 (senza mutazione: LookAheadError)
.[mut:taglio] senza mutazione barre decise 10; con mutazione LookAheadError
.
[mut:attraversamento] fill maker senza mutazione 0, con mutazione 1
.
[mut:costi] conto atteso senza mutazione 36.24073061756545, con mutazione 0.0; netto -44.30195095185263 -> -8.061220334271638
.
[mut:shuffle] con permutazione identica: lordo 230.13872379503965, t 16.60 contro t delle etichette vere 16.60; il test chiede t_vere > 3 * |t_mescolato|, cioe' 16.60 > 49.79
.
[mut:contabilita'] residuo senza mutazione 0.0, con mutazione 6.3351150335038255 (fee totali 6.335115033505985)
.
[mut:buchi] barre dentro l'ora bloccata 60, di cui con posizione aperta 60 (senza mutazione: 0)
.
[mut:freschezza] rifiuti con soglia 30s 9, con soglia 86400s 0; fill 2 -> 11
.
[mut:determinismo] byte del giornale 2385 -> 2027; residuo vero 0.0 scritto come 0.000000
.........................................................................................uu.uu.uu.......................................uu..uu.uu..uu.uu.uu.........uuu..........uuuu.....uuuuuuuuuuuu......uuuu........uuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuu.uuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuu.uuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuu.uuuu.uuuu.uuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuu.................[duckdb] righe materializzate con fetchall: 5000 su 5000, invariate dopo una seconda execute
.[duckdb] con un cursore per flusso: 1000 + 4000 = 5000 righe su 5000
.
[duckdb] primo blocco 1000 righe, dopo una seconda execute sulla stessa connessione 0 righe (attese 1000 su 5000 totali)
.[audit] controllo negativo: buono [], cattivo [(2, 'fetchmany')]
.
[audit] 48 moduli passati al setaccio in costs, catalog, collector, backtest, tools; fetch staccate dalla propria execute: {'backtest/feed.py': [(131, 'fetchmany'), (174, 'fetchmany')]}
.......................................................uuuuuuuuuuu.......uuuuuuuuuuuuuuuu......uuuu....
321 passed, 1733 subtests passed in 228.50s (0:03:48)

real	3m48.917s
user	3m20.315s
sys	0m33.540s
```

<a name="pavimento"></a>
## Pavimento fisso a 30 s: quante ore in piu' vengono marcate

Le due colonne sono la stessa `ts_ordered` — stessi giorni, stessa lettura —
passata due volte a `derivedgaps.build_thresholds`: una con la soglia derivata
dal p99 della finestra, una col pavimento fisso. Le ore "in piu'" sono la
differenza fra i due insiemi, per coin, sull'unione dei tre canali richiesti.

Sulle finestre A e B, che girano su BTC solo, la differenza e' **zero**: la
soglia di `trades/BTC` scendeva da 34,33 s a 30 s e nessun intervallo cade in
quella banda. Il costo si vede sulle coin lente, e va guardato prima di
estendere il backtest oltre BTC.

```
Running as unit: run-r9c13958cbf2043b58102643701613454.scope; invocation ID: d0d23879c3d84cb1ad1e9093ac024173

=== Finestra A: BTC 2026-08-16T00:00 -> 2026-08-17T00:00 (giorni letti 4) ===
-- p99 sulla finestra: buchi 4, ore marcate (somma sulle partizioni) 3
   activeAssetCtx   BTC   n=   339447 p99=   1.410s soglia=  30.000s minimo_assoluto
   l2Book           BTC   n=    64394 p99=   5.695s soglia=  30.000s minimo_assoluto
   trades           BTC   n=   314135 p99=   6.866s soglia=  34.330s p99
-- pavimento fisso 30 s: buchi 4, ore marcate (somma sulle partizioni) 3
   activeAssetCtx   BTC   n=   339447 p99=   1.410s soglia=  30.000s pavimento_fisso
   l2Book           BTC   n=    64394 p99=   5.695s soglia=  30.000s pavimento_fisso
   trades           BTC   n=   314135 p99=   6.866s soglia=  30.000s pavimento_fisso
   BTC: ore bloccate (unione canali) p99 1 -> fisso 1 (in piu' 0); DENTRO la finestra 0 -> 0

=== Finestra B: BTC 2026-08-14T12:00 -> 2026-08-15T00:00 (giorni letti 4) ===
-- p99 sulla finestra: buchi 10, ore marcate (somma sulle partizioni) 15
   activeAssetCtx   BTC   n=   334545 p99=   1.405s soglia=  30.000s minimo_assoluto
   l2Book           BTC   n=    63458 p99=   5.690s soglia=  30.000s minimo_assoluto
   trades           BTC   n=   316189 p99=   6.873s soglia=  34.363s p99
-- pavimento fisso 30 s: buchi 10, ore marcate (somma sulle partizioni) 15
   activeAssetCtx   BTC   n=   334545 p99=   1.405s soglia=  30.000s pavimento_fisso
   l2Book           BTC   n=    63458 p99=   5.690s soglia=  30.000s pavimento_fisso
   trades           BTC   n=   316189 p99=   6.873s soglia=  30.000s pavimento_fisso
   BTC: ore bloccate (unione canali) p99 5 -> fisso 5 (in piu' 0); DENTRO la finestra 4 -> 4

=== Finestra A, quattro coin: BTC,ETH,HYPE,SOL 2026-08-16T00:00 -> 2026-08-17T00:00 (giorni letti 4) ===
-- p99 sulla finestra: buchi 15, ore marcate (somma sulle partizioni) 12
   activeAssetCtx   BTC   n=   339447 p99=   1.410s soglia=  30.000s minimo_assoluto
   activeAssetCtx   ETH   n=   339446 p99=   1.410s soglia=  30.000s minimo_assoluto
   activeAssetCtx   HYPE  n=   339443 p99=   1.411s soglia=  30.000s minimo_assoluto
   activeAssetCtx   SOL   n=   339445 p99=   1.410s soglia=  30.000s minimo_assoluto
   l2Book           BTC   n=    64394 p99=   5.695s soglia=  30.000s minimo_assoluto
   l2Book           ETH   n=    64394 p99=   5.695s soglia=  30.000s minimo_assoluto
   l2Book           HYPE  n=    64394 p99=   5.696s soglia=  30.000s minimo_assoluto
   l2Book           SOL   n=    64394 p99=   5.696s soglia=  30.000s minimo_assoluto
   trades           BTC   n=   314135 p99=   6.866s soglia=  34.330s p99
   trades           ETH   n=   158430 p99=  13.865s soglia=  69.323s p99
   trades           HYPE  n=   247787 p99=   7.337s soglia=  36.684s p99
   trades           SOL   n=    94693 p99=  21.939s soglia= 109.694s p99
-- pavimento fisso 30 s: buchi 240, ore marcate (somma sulle partizioni) 59
   activeAssetCtx   BTC   n=   339447 p99=   1.410s soglia=  30.000s pavimento_fisso
   activeAssetCtx   ETH   n=   339446 p99=   1.410s soglia=  30.000s pavimento_fisso
   activeAssetCtx   HYPE  n=   339443 p99=   1.411s soglia=  30.000s pavimento_fisso
   activeAssetCtx   SOL   n=   339445 p99=   1.410s soglia=  30.000s pavimento_fisso
   l2Book           BTC   n=    64394 p99=   5.695s soglia=  30.000s pavimento_fisso
   l2Book           ETH   n=    64394 p99=   5.695s soglia=  30.000s pavimento_fisso
   l2Book           HYPE  n=    64394 p99=   5.696s soglia=  30.000s pavimento_fisso
   l2Book           SOL   n=    64394 p99=   5.696s soglia=  30.000s pavimento_fisso
   trades           BTC   n=   314135 p99=   6.866s soglia=  30.000s pavimento_fisso
   trades           ETH   n=   158430 p99=  13.865s soglia=  30.000s pavimento_fisso
   trades           HYPE  n=   247787 p99=   7.337s soglia=  30.000s pavimento_fisso
   trades           SOL   n=    94693 p99=  21.939s soglia=  30.000s pavimento_fisso
   BTC: ore bloccate (unione canali) p99 1 -> fisso 1 (in piu' 0); DENTRO la finestra 0 -> 0
   ETH: ore bloccate (unione canali) p99 1 -> fisso 7 (in piu' 6); DENTRO la finestra 0 -> 0
   ETH: ore in piu' [496324, 496325, 496326, 496330, 496331, 496332]
   HYPE: ore bloccate (unione canali) p99 1 -> fisso 1 (in piu' 0); DENTRO la finestra 0 -> 0
   SOL: ore bloccate (unione canali) p99 1 -> fisso 42 (in piu' 41); DENTRO la finestra 0 -> 11
   SOL: ore in piu' [496321, 496322, 496323, 496324, 496325, 496329, 496330, 496331, 496332, 496334, 496335, 496336, 496337, 496338, 496339, 496340, 496341, 496342, 496343, 496344, 496345, 496346, 496348, 496349, 496350, 496354, 496355, 496356, 496357, 496360, 496384, 496389, 496390, 496391, 496395, 496396, 496397, 496398, 496400, 496402, 496413]

picco RSS MB 639.25390625
```

<a name="varianza"></a>
## Il 32% di varianza mancante: quale delle due grandezze e' sbagliata

Tre misure, in ordine di quanto stringono.

1. I due conteggi di passi coincidono, su un singolo seme: `sigma_of` non somma
   passi che il motore non ha fatto.
2. Il PnL che il giornale registra e' esattamente il PnL del percorso
   `sum(pos_k * (mark_{k+1} - mark_k))`, a meno di 2e-15.
3. Il chi quadro `sum (scarto/sigma)^2`, che misura la dispersione senza
   passare per il sigma medio, regredisce a 1 allargando il campione: 0,670 a
   N=100 (-2,3 sigma), 1,018 a N=1000 (+0,4 sigma). La carenza era una
   fluttuazione.

Test:

```
Running as unit: run-re7422632c47d492faa50e7ce3876cd20.scope; invocation ID: 7aa8c6351a1649988a8467ba53798fdf
...
----------------------------------------------------------------------
Ran 3 tests in 7.967s

OK

[varianza] righe di equity 401, incrementi di mark 400, passi effettivi 267, passi nel sigma teorico 267, frazione flat 0.3342
[varianza] PnL di percorso sum(pos*dmark) -8.061220334272923 vs lordo_al_mid -8.061220334272921, differenza -1.7763568394002505e-15

[casuale] seed mercato 3, seed strategia 11, barre 401, ordini 48, fill 49
[casuale] netto -44.30195095185263  atteso -36.24073061756545  scarto -8.061220334287185 (netto peggiore dell'atteso del 22.24% del conto) = -0.712 sigma (sigma 11.318181925517612)
[casuale] fee 29.642243751945827  spread 6.59848686561962  impact 0.0  lordo_al_mid -8.061220334272921

[casuale x100] media degli scarti 1.0051320602131486, deviazione standard campionaria 9.245025187867975, sigma teorico medio 11.194678134882036
[casuale x100] media in unita' di sigma: +0.0898 sigma del singolo tiro, +0.898 errori standard della media (errore standard 1.1194678134882037)
[casuale] progressivo N= 20: media   +5.3586, errore standard  2.5634, t +2.090
[casuale] progressivo N= 50: media   +1.8774, errore standard  1.5804, t +1.188
[casuale] progressivo N=100: media   +1.0051, errore standard  1.1195, t +0.898
[casuale] primo blocco (1-20)      N= 20 media   +5.3586 t +2.141
[casuale] blocco aggiunto (21-100) N= 80 media   -0.0832 t -0.066
[casuale] totale (1-100)           N=100 media   +1.0051 t +0.898
[casuale] chi quadro sum (scarto/sigma)^2 = 66.97071656195179 su 100 gradi di liberta' (-2.34 sigma), dispersione osservata / sigma teorico 0.8258
```

Chi quadro su 1000 ripetizioni della stessa famiglia di semi (100+i, 200+i),
con la varianza teorica e con quella misurata sugli incrementi di mark:

```
Running as unit: run-rb2940915e68240f99ea896bb8aba5165.scope; invocation ID: 833ce05a27e74ffb80971be0f2ebbe50
N= 100  chi2/df teorico 0.6697 (chi2    66.97, scarto -2.34 sigma)   chi2/df con var empirica 0.6648   media z +0.0853
N= 200  chi2/df teorico 0.7447 (chi2   148.93, scarto -2.55 sigma)   chi2/df con var empirica 0.7428   media z +0.1347
N= 500  chi2/df teorico 0.9658 (chi2   482.89, scarto -0.54 sigma)   chi2/df con var empirica 0.9654   media z +0.0217
N=1000  chi2/df teorico 1.0179 (chi2  1017.90, scarto +0.40 sigma)   chi2/df con var empirica 1.0205   media z -0.0031
tempo 102.31782019138336
```

<a name="funding-righe"></a>
## Quante righe legge `funding_series`, e quanto pesano su disco

`funding_series` non e' stata limitata ai giorni della finestra (il motivo sta
nel report: l'ultima ora del campione e' marcata provvisoria, e restringere i
giorni la sposterebbe). Ecco cosa costa oggi, per coin, su tutto lo storico
raccolto:

```
Running as unit: run-r416ca4b7a645445f9e41c7a8844a9119.scope; invocation ID: 974a8edb477248b287ca9a7f7fe6457c
BTC: giorni 20 (2026-08-02..2026-08-21), righe lette 1646410, campioni orari restituiti 468, rapporto righe/campione 3518.0, tempo conteggio 17.53s, tempo funding_series 7.16s
ETH: giorni 20 (2026-08-02..2026-08-21), righe lette 1646461, campioni orari restituiti 468, rapporto righe/campione 3518.1, tempo conteggio 24.02s, tempo funding_series 7.12s
HYPE: giorni 20 (2026-08-02..2026-08-21), righe lette 1646442, campioni orari restituiti 468, rapporto righe/campione 3518.0, tempo conteggio 26.96s, tempo funding_series 7.88s
SOL: giorni 20 (2026-08-02..2026-08-21), righe lette 1646513, campioni orari restituiti 468, rapporto righe/campione 3518.2, tempo conteggio 23.76s, tempo funding_series 7.05s
picco RSS MB 69.7890625
```

Peso su disco delle partizioni attraversate (byte apparenti):

```
127047340   activeAssetCtx/BTC
121809886   activeAssetCtx/ETH
137961321   activeAssetCtx/HYPE
119645456   activeAssetCtx/SOL
795M        activeAssetCtx (intera directory, du -sh)
28133       file parquet nella sola partizione BTC
```

<a name="rifatte"></a>
## Le tre esecuzioni rifatte col pavimento fisso

Digest invariati su A e B (`0e5cb0df…`, `ba7b9e80…`) e sulle quattro coin
(`1d19c165…`): il pavimento non cambia nulla dove non marca ore in piu'.

```
##### ESECUZIONE A
Running as unit: run-ra0e55ea183eb4c129a0300b35c8fefd4.scope; invocation ID: ffadd9b6ee2e499bb0e3e3ba4ae64443
=== CONFIGURAZIONE ===
data_dir              /home/ubuntu/hl-data/mainnet
coin                  BTC
finestra              1786838400000000000 -> 1786924800000000000 (24.00 ore)
giorni letti          2026-08-15,2026-08-16,2026-08-17,2026-08-18 (finestra + 1 per lato)
barra                 60s
strategia             random (seed=7, notional=1000.0)
equity iniziale       10000.0
eta' max del book     30.0s
fee                   maker 0.00015 / taker 0.00045 (tier base)

=== DATI ===
righe book lette      16112
book inutilizzabili   0
trade deduplicati     108168
BTC: ore bloccate 1, regolamenti di funding noti 468, primo 496018, ultimo definitivo 496485
BTC: ore bloccate DENTRO la finestra 0 []
BTC: soglie di buco (s) [('activeAssetCtx', Decimal('30.000'), 'pavimento_fisso'), ('l2Book', Decimal('30.000'), 'pavimento_fisso'), ('trades', Decimal('30.000'), 'pavimento_fisso')]

=== BARRE E ORDINI ===
barre                 1441
barre con decisione   1440
barre tutte bloccate  0
ordini                93
fill taker            94
fill maker            0
rifiuti               0 {}
chiusure d'ufficio    0 (fallite 0)
eta' del book sui fill (ms)  n=94 min=23.940705 p50=2429.447695 p90=4498.482813 max=5432.465797

=== CONTO ===
equity iniziale       10000.0
equity finale         9937.394571856803
PnL                   -62.60542814319706
realizzato            -4.107688037746322
fee                   58.49644581896039
funding               0.0012942864953808322 (13 regolamenti, 0 non noti)
residuo conservazione 5.4569682106375694e-12
posizione finale      {'BTC': 0.0}

=== RIPRODUCIBILITA' ===
digest sha256         0e5cb0dfacb3fd7f41849249ab97e9367c5022f57cba340432de95c2f3a66217
righe di giornale     107
righe di equity       1441
scritti               /tmp/bt-A3/journal.csv, /tmp/bt-A3/equity.csv

=== TEMPI E MEMORIA ===
contesto (gap+funding) 13.69s
simulazione            7.26s
picco RSS dopo contesto 338.5 MB
picco RSS a fine run    338.5 MB
memory_limit DuckDB     512MB
##### ESECUZIONE B
Running as unit: run-raaca36bf5acc4ca4a16d3389c107993b.scope; invocation ID: 0485d349fbd84893a972bb9aa37ef26d
=== CONFIGURAZIONE ===
data_dir              /home/ubuntu/hl-data/mainnet
coin                  BTC
finestra              1786708800000000000 -> 1786752000000000000 (12.00 ore)
giorni letti          2026-08-13,2026-08-14,2026-08-15,2026-08-16 (finestra + 1 per lato)
barra                 60s
strategia             always_long (seed=0, notional=1000.0)
equity iniziale       10000.0
eta' max del book     30.0s
fee                   maker 0.00015 / taker 0.00045 (tier base)

=== DATI ===
righe book lette      7115
book inutilizzabili   0
trade deduplicati     124242
BTC: ore bloccate 5, regolamenti di funding noti 468, primo 496018, ultimo definitivo 496485
BTC: ore bloccate DENTRO la finestra 4 [496311, 496312, 496313, 496316]
BTC: soglie di buco (s) [('activeAssetCtx', Decimal('30.000'), 'pavimento_fisso'), ('l2Book', Decimal('30.000'), 'pavimento_fisso'), ('trades', Decimal('30.000'), 'pavimento_fisso')]

=== BARRE E ORDINI ===
barre                 721
barre con decisione   360
barre tutte bloccate  360
ordini                3
fill taker            6
fill maker            0
rifiuti               0 {}
chiusure d'ufficio    2 (fallite 0)
eta' del book sui fill (ms)  n=6 min=1813.343878 p50=4608.194798 p90=5042.381056 max=5415.616824

=== CONTO ===
equity iniziale       10000.0
equity finale         9995.360529407786
PnL                   -4.639470592213911
realizzato            -1.8767544871132176
fee                   2.7002791274682103
funding               0.062436977630040795 (5 regolamenti, 0 non noti)
residuo conservazione -1.8189894035458565e-12
posizione finale      {'BTC': 0.0}

=== RIPRODUCIBILITA' ===
digest sha256         ba7b9e809af179f6d14e6a61befd17268bd8d06c7ea21a51fe88a0dacb16894a
righe di giornale     11
righe di equity       721
scritti               /tmp/bt-B3/journal.csv, /tmp/bt-B3/equity.csv

=== TEMPI E MEMORIA ===
contesto (gap+funding) 12.27s
simulazione            6.63s
picco RSS dopo contesto 337.0 MB
picco RSS a fine run    337.0 MB
memory_limit DuckDB     512MB
```

```
Running as unit: run-r032a057ab3734b99a23363358d7dfc08.scope; invocation ID: 5ecc09f1e2a54856be2eba643cb31117
=== CONFIGURAZIONE ===
data_dir              /home/ubuntu/hl-data/mainnet
coin                  BTC,ETH,HYPE,SOL
finestra              1786838400000000000 -> 1786924800000000000 (24.00 ore)
giorni letti          2026-08-15,2026-08-16,2026-08-17,2026-08-18 (finestra + 1 per lato)
barra                 60s
strategia             flat (seed=0, notional=1000.0)
equity iniziale       10000.0
eta' max del book     30.0s
fee                   maker 0.00015 / taker 0.00045 (tier base)

=== DATI ===
righe book lette      64448
book inutilizzabili   0
trade deduplicati     318561
BTC: ore bloccate 1, regolamenti di funding noti 468, primo 496018, ultimo definitivo 496485
BTC: ore bloccate DENTRO la finestra 0 []
BTC: soglie di buco (s) [('activeAssetCtx', Decimal('30.000'), 'pavimento_fisso'), ('l2Book', Decimal('30.000'), 'pavimento_fisso'), ('trades', Decimal('30.000'), 'pavimento_fisso')]
ETH: ore bloccate 7, regolamenti di funding noti 468, primo 496018, ultimo definitivo 496485
ETH: ore bloccate DENTRO la finestra 0 []
ETH: soglie di buco (s) [('activeAssetCtx', Decimal('30.000'), 'pavimento_fisso'), ('l2Book', Decimal('30.000'), 'pavimento_fisso'), ('trades', Decimal('30.000'), 'pavimento_fisso')]
HYPE: ore bloccate 1, regolamenti di funding noti 468, primo 496018, ultimo definitivo 496485
HYPE: ore bloccate DENTRO la finestra 0 []
HYPE: soglie di buco (s) [('activeAssetCtx', Decimal('30.000'), 'pavimento_fisso'), ('l2Book', Decimal('30.000'), 'pavimento_fisso'), ('trades', Decimal('30.000'), 'pavimento_fisso')]
SOL: ore bloccate 42, regolamenti di funding noti 468, primo 496018, ultimo definitivo 496485
SOL: ore bloccate DENTRO la finestra 11 [496344, 496345, 496346, 496348, 496349, 496350, 496354, 496355, 496356, 496357, 496360]
SOL: soglie di buco (s) [('activeAssetCtx', Decimal('30.000'), 'pavimento_fisso'), ('l2Book', Decimal('30.000'), 'pavimento_fisso'), ('trades', Decimal('30.000'), 'pavimento_fisso')]

=== BARRE E ORDINI ===
barre                 1441
barre con decisione   1440
barre tutte bloccate  0
ordini                0
fill taker            0
fill maker            0
rifiuti               0 {}
chiusure d'ufficio    0 (fallite 0)
eta' del book sui fill (ms)  n=0 min=None p50=None p90=None max=None

=== CONTO ===
equity iniziale       10000.0
equity finale         10000.0
PnL                   0.0
realizzato            0.0
fee                   0.0
funding               0.0 (0 regolamenti, 0 non noti)
residuo conservazione 0.0
posizione finale      {'BTC': 0.0, 'ETH': 0.0, 'HYPE': 0.0, 'SOL': 0.0}

=== RIPRODUCIBILITA' ===
digest sha256         1d19c165fe6d5dd9140fcb5ad9f6bf7d04814577f00e85ebd913f569341e753e
righe di giornale     0
righe di equity       1441

=== TEMPI E MEMORIA ===
contesto (gap+funding) 53.35s
simulazione            24.33s
picco RSS dopo contesto 635.0 MB
picco RSS a fine run    635.0 MB
memory_limit DuckDB     512MB
```

<a name="suite4"></a>
## Suite completa dopo le modifiche

`python -m unittest discover -s tests`, il comando documentato nel README.
`pytest` non e' piu' installato nel virtualenv, quindi il conteggio dei
subtest — 1733 nella tornata precedente — non compare: `unittest` non lo
riporta.

```
Running as unit: run-rc384107178204ca5a4ebfaed31cf9005.scope; invocation ID: 13faad73747d41bcbc82c0221bf3cbcf
........................................................................................................................................................................................................................................................................................2026-08-21 21:41:45,944 INFO collector network=testnet data_dir=/tmp/tmp097v7d77/data
2026-08-21 21:41:45,944 ERROR collector un'altra istanza del collector sta gia' scrivendo in /tmp/tmp097v7d77/data: il lock /tmp/tmp097v7d77/data/.lock e' tenuto dal processo PID 1295086. Il collector non parte perche' due istanze sulla stessa directory duplicherebbero ogni riga negli stessi parquet. Ferma l'istanza attiva (`systemctl stop hl-collector`), oppure fai partire questa su una data_dir diversa nel config.
....../home/ubuntu/.local/share/uv/python/cpython-3.11.15-linux-aarch64-gnu/lib/python3.11/unittest/case.py:657: ResourceWarning: unclosed file <_io.TextIOWrapper name=3 encoding='UTF-8'>
  function, args, kwargs = self._cleanups.pop()
ResourceWarning: Enable tracemalloc to get the object allocation traceback
....................................
----------------------------------------------------------------------
Ran 322 tests in 149.943s

OK

[buco] ora bloccata 496112  barre dentro 60  barre non decise 60  chiusure d'ufficio 1 (fallite 0)
[buco] rate noti per 2 ore su 2  barre tutte bloccate 60  chiusure 1

[conservazione] membro sinistro (equity finale)  9960.179849457238
[conservazione] membro destro  10000.0 + -13.631397568352677 - 26.201294844438163 - -0.012541870026746987 = 9960.179849457234
[conservazione] differenza 3.637978807091713e-12  (45 fill, 3 regolamenti)

[conservazione] posizione finale 0.0, chiusure d'ufficio 0

[determinismo] digest A b3420a9ed27651cec0a11672b5d6f7d3cce5760662725a69b726dadd43402d2f
[determinismo] digest B b3420a9ed27651cec0a11672b5d6f7d3cce5760662725a69b726dadd43402d2f  righe 14

[shuffle] etichette vere: lordo 230.13872379503965  t 16.60  netto 6.920416111384839
[shuffle] 20 permutazioni: |t| max 1.87, media t +0.056 (attesa 0 +/- 0.224), tutti i t [1.37, 0.84, 1.13, -0.34, 0.77, -0.23, 1.48, -0.01, -1.87, -1.55, 0.52, 0.64, -1.08, -1.1, 0.02, 0.14, 0.43, 1.08, -0.39, -0.72]

[look-ahead] offset 0 ns -> LookAheadError
[look-ahead] messaggio: richiesto un dato a ts=1785999600000000000 con decisione a ts=1785999600000000000: mancano 0 ns al futuro. La decisione usa solo dati STRETTAMENTE precedenti.

[look-ahead] 30 letture del book, distanza massima dal futuro -5000000000 ns (deve essere < 0)
[look-ahead] offset -1 ns -> nessuna eccezione, barre decise 10

[taker] px 100.03227831942769  atteso 100.03227831942769  spread 0.015000000000007674  impact 0.0049999999999954525
[maker] ordine 1.0, trade 0.25 -> eseguito 0.25
[profondita'] rifiuti {'profondita_insufficiente': 4}  fill 0

[freschezza] soglia 30s  rifiuti {'book_troppo_vecchio': 9}  eta' massima registrata 570000.0 ms

[maker] limite oltre il best ask -> {'ordine_non_valido': 4}

[maker] limite 99.95  trade 99.949  fill maker 1
[maker] limite 99.95  trade esattamente al limite  fill maker 0  rifiuti {'scaduto_senza_attraversamento': 1}

[varianza] righe di equity 401, incrementi di mark 400, passi effettivi 267, passi nel sigma teorico 267, frazione flat 0.3342
[varianza] PnL di percorso sum(pos*dmark) -8.061220334272923 vs lordo_al_mid -8.061220334272921, differenza -1.7763568394002505e-15

[casuale] seed mercato 3, seed strategia 11, barre 401, ordini 48, fill 49
[casuale] netto -44.30195095185263  atteso -36.24073061756545  scarto -8.061220334287185 (netto peggiore dell'atteso del 22.24% del conto) = -0.712 sigma (sigma 11.318181925517612)
[casuale] fee 29.642243751945827  spread 6.59848686561962  impact 0.0  lordo_al_mid -8.061220334272921

[casuale x100] media degli scarti 1.0051320602131486, deviazione standard campionaria 9.245025187867975, sigma teorico medio 11.194678134882036
[casuale x100] media in unita' di sigma: +0.0898 sigma del singolo tiro, +0.898 errori standard della media (errore standard 1.1194678134882037)
[casuale] progressivo N= 20: media   +5.3586, errore standard  2.5634, t +2.090
[casuale] progressivo N= 50: media   +1.8774, errore standard  1.5804, t +1.188
[casuale] progressivo N=100: media   +1.0051, errore standard  1.1195, t +0.898
[casuale] primo blocco (1-20)      N= 20 media   +5.3586 t +2.141
[casuale] blocco aggiunto (21-100) N= 80 media   -0.0832 t -0.066
[casuale] totale (1-100)           N=100 media   +1.0051 t +0.898
[casuale] chi quadro sum (scarto/sigma)^2 = 66.97071656195179 su 100 gradi di liberta' (-2.34 sigma), dispersione osservata / sigma teorico 0.8258

[nulla] equity 10000.0 -> 10000.0  PnL 0.0  fee 0.0  funding 0.0  fill 0  ordini 0  barre 121

[feed] 6 eventi, 4 righe book, 3 trade, 1 book inutilizzabili
[feed] tid consegnati [1001, 1002, 1002, 1003] -> letti [1001, 1002, 1003]
[feed] date lette per una finestra di un minuto: ['2026-08-14', '2026-08-15', '2026-08-16']

[mut:costi] conto atteso senza mutazione 36.24073061756545, con mutazione 0.0; netto -44.30195095185263 -> -8.061220334271638

[mut:determinismo] byte del giornale 2385 -> 2027; residuo vero 0.0 scritto come 0.000000

[mut:guardiano] nessuna eccezione, barre decise 10 (senza mutazione: LookAheadError)
[mut:taglio] senza mutazione barre decise 10; con mutazione LookAheadError

[mut:contabilita'] residuo senza mutazione 0.0, con mutazione 6.3351150335038255 (fee totali 6.335115033505985)

[mut:buchi] barre dentro l'ora bloccata 60, di cui con posizione aperta 60 (senza mutazione: 0)

[mut:attraversamento] fill maker senza mutazione 0, con mutazione 1

[mut:freschezza] rifiuti con soglia 30s 9, con soglia 86400s 0; fill 2 -> 11

[mut:shuffle] con permutazione identica: lordo 230.13872379503965, t 16.60 contro t delle etichette vere 16.60; il test chiede t_vere > 3 * |t_mescolato|, cioe' 16.60 > 49.79
[duckdb] righe materializzate con fetchall: 5000 su 5000, invariate dopo una seconda execute
[duckdb] con un cursore per flusso: 1000 + 4000 = 5000 righe su 5000

[duckdb] primo blocco 1000 righe, dopo una seconda execute sulla stessa connessione 0 righe (attese 1000 su 5000 totali)
[audit] controllo negativo: buono [], cattivo [(2, 'fetchmany')]

[audit] 48 moduli passati al setaccio in costs, catalog, collector, backtest, tools; fetch staccate dalla propria execute: {'backtest/feed.py': [(131, 'fetchmany'), (174, 'fetchmany')]}

real	2m30.243s
user	2m13.927s
sys	0m21.280s
```

<a name="contraddizione"></a>
## 78, 15 e 0: quale conteggio di buchi rispondeva a quale domanda

Il report della tornata precedente diceva *0 buchi sulla finestra A a quattro
coin con le soglie storiche*; quello successivo *15 sulla stessa finestra*.
Nessuno dei due numeri era sbagliato: contavano cose diverse e li ho chiamati
tutti e due "buchi". Qui le due configurazioni girano oggi, con tre conteggi
espliciti per partizione — `totale` (righe di `derived_gaps`), `nei 4gg` (buchi
che iniziano nei quattro giorni letti dalla finestra), `toccano` (buchi che
intersecano le 24 ore simulate).

Le soglie storiche di oggi non coincidono con quelle del report precedente
(`trades/SOL` 80,80 s contro 82,48 s, `trades/ETH` 49,73 contro 50,08,
`trades/HYPE` 31,18 contro 32,07): lo storico e' cresciuto da 17 a 20 giorni e
il p99 si e' spostato. E' la stessa retroazione che ha motivato il pavimento
fisso, vista dall'altro capo.

```
Running as unit: run-r952875746853408eb2c53318d1d90f2b.scope; invocation ID: d8110ce15a98405e8328f06afb4205c8

=== STORICO, soglie dal p99 dello storico ===
giorni letti: TUTTI (storico)
righe in ts_ordered: 13621523   righe in derived_gaps: 78   tempo 380.8s
partizione                soglia            basis      p99  totale  nei 4gg  toccano
l2Book/BTC                 30.00  minimo_assoluto     5.71       6        1        0
trades/BTC                 30.00  minimo_assoluto     4.55       8        2        0
activeAssetCtx/BTC         30.00  minimo_assoluto     1.41       6        1        0
l2Book/ETH                 30.00  minimo_assoluto     5.71       6        1        0
trades/ETH                 49.73              p99     9.95       8        2        0
activeAssetCtx/ETH         30.00  minimo_assoluto     1.41       6        1        0
l2Book/HYPE                30.00  minimo_assoluto     5.71       6        1        0
trades/HYPE                31.18              p99     6.24       8        2        0
activeAssetCtx/HYPE        30.00  minimo_assoluto     1.41       6        1        0
l2Book/SOL                 30.00  minimo_assoluto     5.71       6        1        0
trades/SOL                 80.80              p99    16.16       6        2        0
activeAssetCtx/SOL         30.00  minimo_assoluto     1.41       6        1        0
TOTALE                                                          78       16        0
picco RSS finora 792.4 MB

=== FINESTRA, soglie dal p99 della finestra ===
giorni letti: 2026-08-15,2026-08-16,2026-08-17,2026-08-18
righe in ts_ordered: 2430414   righe in derived_gaps: 15   tempo 51.2s
partizione                soglia            basis      p99  totale  nei 4gg  toccano
l2Book/BTC                 30.00  minimo_assoluto     5.69       1        1        0
trades/BTC                 34.33              p99     6.87       2        2        0
activeAssetCtx/BTC         30.00  minimo_assoluto     1.41       1        1        0
l2Book/ETH                 30.00  minimo_assoluto     5.70       1        1        0
trades/ETH                 69.32              p99    13.86       2        2        0
activeAssetCtx/ETH         30.00  minimo_assoluto     1.41       1        1        0
l2Book/HYPE                30.00  minimo_assoluto     5.70       1        1        0
trades/HYPE                36.68              p99     7.34       2        2        0
activeAssetCtx/HYPE        30.00  minimo_assoluto     1.41       1        1        0
l2Book/SOL                 30.00  minimo_assoluto     5.70       1        1        0
trades/SOL                109.69              p99    21.94       1        1        0
activeAssetCtx/SOL         30.00  minimo_assoluto     1.41       1        1        0
TOTALE                                                          15       15        0
picco RSS finora 792.4 MB

=== FINESTRA, pavimento fisso 30 s (comportamento attuale) ===
giorni letti: 2026-08-15,2026-08-16,2026-08-17,2026-08-18
righe in ts_ordered: 2430414   righe in derived_gaps: 240   tempo 21.3s
partizione                soglia            basis      p99  totale  nei 4gg  toccano
l2Book/BTC                 30.00  pavimento_fisso     5.69       1        1        0
trades/BTC                 30.00  pavimento_fisso     6.87       2        2        0
activeAssetCtx/BTC         30.00  pavimento_fisso     1.41       1        1        0
l2Book/ETH                 30.00  pavimento_fisso     5.70       1        1        0
trades/ETH                 30.00  pavimento_fisso    13.86      16       16        0
activeAssetCtx/ETH         30.00  pavimento_fisso     1.41       1        1        0
l2Book/HYPE                30.00  pavimento_fisso     5.70       1        1        0
trades/HYPE                30.00  pavimento_fisso     7.34       2        2        0
activeAssetCtx/HYPE        30.00  pavimento_fisso     1.41       1        1        0
l2Book/SOL                 30.00  pavimento_fisso     5.70       1        1        0
trades/SOL                 30.00  pavimento_fisso    21.94     212      212       50
activeAssetCtx/SOL         30.00  pavimento_fisso     1.41       1        1        0
TOTALE                                                         240      240       50
picco RSS finora 792.4 MB

picco RSS finale 792.4 MB
```

Il confronto fra le prime due configurazioni mostra anche un buco vero che la
soglia della finestra NON trova: 16 buchi nei quattro giorni con le soglie
storiche, 15 con quelle della finestra, e la differenza sta tutta su
`trades/SOL`. Il buco mancante dura 102,5 s e cade fra 80,80 s (soglia storica)
e 109,69 s (soglia della finestra):

```
Running as unit: run-rdac8e34bdee748acbbb477e9854686a3.scope; invocation ID: 8780a40d2d344c1aa9b6f82334f527de

soglia 80.8s -> buchi su trades/SOL nei 4 giorni: 2
   2026-08-15 08:04:27 -> 2026-08-15 08:06:35  127.6s   fuori dalla finestra
   2026-08-15 08:06:35 -> 2026-08-15 08:08:17  102.5s   fuori dalla finestra

soglia 109.69s -> buchi su trades/SOL nei 4 giorni: 1
   2026-08-15 08:04:27 -> 2026-08-15 08:06:35  127.6s   fuori dalla finestra
```

<a name="runner"></a>
## I due runner della suite, sullo stesso codice

```
.[duckdb] con un cursore per flusso: 1000 + 4000 = 5000 righe su 5000
.
[duckdb] primo blocco 1000 righe, dopo una seconda execute sulla stessa connessione 0 righe (attese 1000 su 5000 totali)
.[audit] controllo negativo: buono [], cattivo [(2, 'fetchmany')]
.
[audit] 49 moduli passati al setaccio in costs, catalog, collector, backtest, tools; fetch staccate dalla propria execute: {'backtest/feed.py': [(131, 'fetchmany'), (174, 'fetchmany')]}
.......................................................uuuuuuuuuuu.......uuuuuuuuuuuuuuuu......uuuu....
322 passed, 1733 subtests passed in 228.97s (0:03:48)

real	3m49.390s
user	3m20.876s
sys	0m33.630s
```

```
[duckdb] primo blocco 1000 righe, dopo una seconda execute sulla stessa connessione 0 righe (attese 1000 su 5000 totali)
[audit] controllo negativo: buono [], cattivo [(2, 'fetchmany')]

[audit] 49 moduli passati al setaccio in costs, catalog, collector, backtest, tools; fetch staccate dalla propria execute: {'backtest/feed.py': [(131, 'fetchmany'), (174, 'fetchmany')]}

real	2m29.199s
user	2m13.799s
sys	0m20.638s
```

`pytest` 9.1.1 conta i subtest da solo: `pytest-subtests` non e' installato
(`pip list` nel virtualenv del progetto elenca solo pytest, iniconfig,
packaging, pluggy e Pygments). `unittest` esegue gli stessi 322 test e non
riporta i subtest.

<a name="ambiente"></a>
## Dov'era `pytest`: cronologia dell'ambiente

Quello che si dimostra dai timestamp:

```
.venv                       creato 2026-08-02 07:50:03, mai ricreato (birth intatta)
.venv/.../site-packages     ultima modifica 2026-08-14 14:40:18 (installazione di duckdb 1.5.5)
pytest*.dist-info           MAI presente in .venv
disco                       179 GB liberi su 192 (7% usato) — nessun evento di spazio
apt/dpkg su python3         ultimo cambiamento 2026-07-16; gli aggiornamenti
                            automatici del 19-21 agosto sono solo kernel e linux-tools
/tmp/pytestenv              creato 2026-08-19 23:27:44 — python 3.12.3,
                            --system-site-packages, pytest 9.1.1, duckdb 1.5.5, niente pyarrow
/tmp/pytest311              creato 2026-08-20 09:16:32 — python 3.11.15,
                            pytest 9.1.1 e nient'altro (niente duckdb)
tests/__pycache__/*.cpython-312-pytest-9.1.1.pyc   scritti 2026-08-19 23:28:07
```

Ventitre secondi separano la creazione di `/tmp/pytestenv` dai `.pyc` compilati
da CPython 3.12: nella tornata precedente almeno una esecuzione della suite e'
girata su un interprete diverso da quello del progetto (3.11.15). I `.pyc` 3.12
coprono i test di `catalog/` e `costs/` ma NON quelli di `backtest/`, che a
quell'ora non esistevano ancora.

Quello che NON si dimostra: quale dei due ambienti temporanei abbia prodotto il
`321 passed, 1733 subtests` finale. I `.pyc` 3.11 sono stati riscritti
dall'esecuzione di oggi e non c'e' cronologia di shell. `/tmp/pytest311` da
solo non puo' aver eseguito la suite — non ha duckdb — quindi, se e' stato lui,
e' stato con un `PYTHONPATH` preso in prestito dal virtualenv del progetto.
