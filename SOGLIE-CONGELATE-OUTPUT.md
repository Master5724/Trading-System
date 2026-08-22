# Soglie di buco congelate — output grezzo

Tutto quello che sta qui e' incollato dai log, non ricopiato a mano. I comandi
pesanti girano sotto `systemd-run --user --scope -p MemoryMax=2G nice -n 19
ionice -c 3`, in sola lettura su `/home/ubuntu/hl-data/mainnet`, col collector
attivo (PID 1063457, nessun riavvio).

## Soglie congelate: generazione sullo storico

`systemd-run --user --scope -p MemoryMax=2G nice -n 19 ionice -c 3 .venv/bin/python -m catalog.soglie --data-dir /home/ubuntu/hl-data/mainnet --scrivi`

Lettura dell'intero storico: 21 giorni, 17 partizioni. Le soglie prodotte sono quelle scritte in `gap_thresholds.json`, che da qui in poi nessun comando ricalcola da solo.

```
storico 2026-08-02 -> 2026-08-22 (21 giorni, 19223057 righe), 592.4 s, picco 1393.3 MB
  activeAssetCtx   BTC     30.000 s  (minimo_assoluto, p99 1.406 s su 1692604 intervalli)
  activeAssetCtx   ETH     30.000 s  (minimo_assoluto, p99 1.406 s su 1692596 intervalli)
  activeAssetCtx   HYPE    30.000 s  (minimo_assoluto, p99 1.408 s su 1692636 intervalli)
  activeAssetCtx   SOL     30.000 s  (minimo_assoluto, p99 1.407 s su 1692648 intervalli)
  allMids          _global   30.000 s  (minimo_assoluto, p99 5.350 s su 342184 intervalli)
  candle           BTC     30.000 s  (minimo_assoluto, p99 5.167 s su 1715006 intervalli)
  candle           ETH     52.011 s  (p99, p99 10.402 s su 1005659 intervalli)
  candle           HYPE    34.605 s  (p99, p99 6.921 s su 1338481 intervalli)
  candle           SOL     83.036 s  (p99, p99 16.607 s su 635650 intervalli)
  l2Book           BTC     30.000 s  (minimo_assoluto, p99 5.705 s su 321491 intervalli)
  l2Book           ETH     30.000 s  (minimo_assoluto, p99 5.707 s su 321501 intervalli)
  l2Book           HYPE    30.000 s  (minimo_assoluto, p99 5.708 s su 321496 intervalli)
  l2Book           SOL     30.000 s  (minimo_assoluto, p99 5.709 s su 321511 intervalli)
  trades           BTC     30.000 s  (minimo_assoluto, p99 4.481 s su 2354167 intervalli)
  trades           ETH     48.685 s  (p99, p99 9.737 s su 1269795 intervalli)
  trades           HYPE    30.370 s  (p99, p99 6.074 s su 1762792 intervalli)
  trades           SOL     79.374 s  (p99, p99 15.875 s su 742823 intervalli)
scritto /home/ubuntu/hl-trading/Trading-System/gap_thresholds.json
```

## Diff dei conteggi: soglie congelate contro pavimento fisso

Finestre A (24 h dal 2026-08-16) e B (12 h dal 2026-08-14T12:00), quattro coin, tre canali. `buchi` = righe di `derived_gaps`; `nei gg` = buchi che iniziano nei giorni letti; `toccano` = buchi che intersecano le ore simulate; `ore` = ore marcate inaffidabili; `dentro` = quelle che cadono nella finestra, cioe' il costo vero.

Script: `/tmp/confronto_soglie.py`.

```

--- A  2026-08-16T00:00 -> 2026-08-17T00:00 (24 h)  |  soglie congelate  |  giorni 2026-08-15,2026-08-16,2026-08-17,2026-08-18 ---
righe in ts_ordered 2430414   righe in derived_gaps 16   tempo 49.1s
partizione                soglia                        basis   buchi  nei gg  toccano    ore  dentro
l2Book/BTC                 30.00 congelata@2026-08-22 (minimo_assoluto)       1       1        0      1       0
trades/BTC                 30.00 congelata@2026-08-22 (minimo_assoluto)       2       2        0      1       0
activeAssetCtx/BTC         30.00 congelata@2026-08-22 (minimo_assoluto)       1       1        0      1       0
l2Book/ETH                 30.00 congelata@2026-08-22 (minimo_assoluto)       1       1        0      1       0
trades/ETH                 48.69   congelata@2026-08-22 (p99)       2       2        0      1       0
activeAssetCtx/ETH         30.00 congelata@2026-08-22 (minimo_assoluto)       1       1        0      1       0
l2Book/HYPE                30.00 congelata@2026-08-22 (minimo_assoluto)       1       1        0      1       0
trades/HYPE                30.37   congelata@2026-08-22 (p99)       2       2        0      1       0
activeAssetCtx/HYPE        30.00 congelata@2026-08-22 (minimo_assoluto)       1       1        0      1       0
l2Book/SOL                 30.00 congelata@2026-08-22 (minimo_assoluto)       1       1        0      1       0
trades/SOL                 79.37   congelata@2026-08-22 (p99)       2       2        0      1       0
activeAssetCtx/SOL         30.00 congelata@2026-08-22 (minimo_assoluto)       1       1        0      1       0
TOTALE                                                             16      16        0     12       0
  ore bloccate nella finestra, BTC: 0 []
  ore bloccate nella finestra, ETH: 0 []
  ore bloccate nella finestra, HYPE: 0 []
  ore bloccate nella finestra, SOL: 0 []
picco RSS finora 595.1 MB

--- A  2026-08-16T00:00 -> 2026-08-17T00:00 (24 h)  |  soglie fisse  |  giorni 2026-08-15,2026-08-16,2026-08-17,2026-08-18 ---
righe in ts_ordered 2430414   righe in derived_gaps 240   tempo 20.2s
partizione                soglia                        basis   buchi  nei gg  toccano    ore  dentro
l2Book/BTC                 30.00              pavimento_fisso       1       1        0      1       0
trades/BTC                 30.00              pavimento_fisso       2       2        0      1       0
activeAssetCtx/BTC         30.00              pavimento_fisso       1       1        0      1       0
l2Book/ETH                 30.00              pavimento_fisso       1       1        0      1       0
trades/ETH                 30.00              pavimento_fisso      16      16        0      7       0
activeAssetCtx/ETH         30.00              pavimento_fisso       1       1        0      1       0
l2Book/HYPE                30.00              pavimento_fisso       1       1        0      1       0
trades/HYPE                30.00              pavimento_fisso       2       2        0      1       0
activeAssetCtx/HYPE        30.00              pavimento_fisso       1       1        0      1       0
l2Book/SOL                 30.00              pavimento_fisso       1       1        0      1       0
trades/SOL                 30.00              pavimento_fisso     212     212       50     42      11
activeAssetCtx/SOL         30.00              pavimento_fisso       1       1        0      1       0
TOTALE                                                            240     240       50     59      11
  ore bloccate nella finestra, BTC: 0 []
  ore bloccate nella finestra, ETH: 0 []
  ore bloccate nella finestra, HYPE: 0 []
  ore bloccate nella finestra, SOL: 11 [496344, 496345, 496346, 496348, 496349, 496350, 496354, 496355, 496356, 496357, 496360]
picco RSS finora 595.1 MB

--- B  2026-08-14T12:00 -> 2026-08-15T00:00 (12 h)  |  soglie congelate  |  giorni 2026-08-13,2026-08-14,2026-08-15,2026-08-16 ---
righe in ts_ordered 2372587   righe in derived_gaps 40   tempo 42.9s
partizione                soglia                        basis   buchi  nei gg  toccano    ore  dentro
l2Book/BTC                 30.00 congelata@2026-08-22 (minimo_assoluto)       3       3        2      5       4
trades/BTC                 30.00 congelata@2026-08-22 (minimo_assoluto)       4       4        2      5       4
activeAssetCtx/BTC         30.00 congelata@2026-08-22 (minimo_assoluto)       3       3        2      5       4
l2Book/ETH                 30.00 congelata@2026-08-22 (minimo_assoluto)       3       3        2      5       4
trades/ETH                 48.69   congelata@2026-08-22 (p99)       4       4        2      5       4
activeAssetCtx/ETH         30.00 congelata@2026-08-22 (minimo_assoluto)       3       3        2      5       4
l2Book/HYPE                30.00 congelata@2026-08-22 (minimo_assoluto)       3       3        2      5       4
trades/HYPE                30.37   congelata@2026-08-22 (p99)       4       4        2      5       4
activeAssetCtx/HYPE        30.00 congelata@2026-08-22 (minimo_assoluto)       3       3        2      5       4
l2Book/SOL                 30.00 congelata@2026-08-22 (minimo_assoluto)       3       3        2      5       4
trades/SOL                 79.37   congelata@2026-08-22 (p99)       4       4        2      5       4
activeAssetCtx/SOL         30.00 congelata@2026-08-22 (minimo_assoluto)       3       3        2      5       4
TOTALE                                                             40      40       24     60      16
  ore bloccate nella finestra, BTC: 4 [496311, 496312, 496313, 496316]
  ore bloccate nella finestra, ETH: 4 [496311, 496312, 496313, 496316]
  ore bloccate nella finestra, HYPE: 4 [496311, 496312, 496313, 496316]
  ore bloccate nella finestra, SOL: 4 [496311, 496312, 496313, 496316]
picco RSS finora 595.1 MB

--- B  2026-08-14T12:00 -> 2026-08-15T00:00 (12 h)  |  soglie fisse  |  giorni 2026-08-13,2026-08-14,2026-08-15,2026-08-16 ---
righe in ts_ordered 2372587   righe in derived_gaps 265   tempo 19.9s
partizione                soglia                        basis   buchi  nei gg  toccano    ore  dentro
l2Book/BTC                 30.00              pavimento_fisso       3       3        2      5       4
trades/BTC                 30.00              pavimento_fisso       4       4        2      5       4
activeAssetCtx/BTC         30.00              pavimento_fisso       3       3        2      5       4
l2Book/ETH                 30.00              pavimento_fisso       3       3        2      5       4
trades/ETH                 30.00              pavimento_fisso      20      20        4     13       6
activeAssetCtx/ETH         30.00              pavimento_fisso       3       3        2      5       4
l2Book/HYPE                30.00              pavimento_fisso       3       3        2      5       4
trades/HYPE                30.00              pavimento_fisso       4       4        2      5       4
activeAssetCtx/HYPE        30.00              pavimento_fisso       3       3        2      5       4
l2Book/SOL                 30.00              pavimento_fisso       3       3        2      5       4
trades/SOL                 30.00              pavimento_fisso     213     213        3     54       5
activeAssetCtx/SOL         30.00              pavimento_fisso       3       3        2      5       4
TOTALE                                                            265     265       27    117      19
  ore bloccate nella finestra, BTC: 4 [496311, 496312, 496313, 496316]
  ore bloccate nella finestra, ETH: 6 [496311, 496312, 496313, 496316, 496317, 496318]
  ore bloccate nella finestra, HYPE: 4 [496311, 496312, 496313, 496316]
  ore bloccate nella finestra, SOL: 5 [496311, 496312, 496313, 496316, 496317]
picco RSS finora 595.1 MB

=== DIFF ===
finestra                      congelate                        fisse
A                              buchi 16                    buchi 240  diff +224
  BTC                  nella finestra 0             nella finestra 0  diff +0
  ETH                  nella finestra 0             nella finestra 0  diff +0
  HYPE                 nella finestra 0             nella finestra 0  diff +0
  SOL                  nella finestra 0            nella finestra 11  diff +11
B                              buchi 40                    buchi 265  diff +225
  BTC                  nella finestra 4             nella finestra 4  diff +0
  ETH                  nella finestra 4             nella finestra 6  diff +2
  HYPE                 nella finestra 4             nella finestra 4  diff +0
  SOL                  nella finestra 4             nella finestra 5  diff +1
picco RSS totale 595.1 MB
```

## Esecuzione A, una coin: lo stesso digest nelle due modalita'

`.venv/bin/python -m backtest --data-dir /home/ubuntu/hl-data/mainnet --coins BTC --from 2026-08-16T00:00 --to 2026-08-17T00:00 --bar-s 60 --strategy random --seed 7 --soglie <fisse|congelate>`

Su BTC la soglia congelata vale 30 s su tutti e tre i canali, esattamente come il pavimento: il digest deve restare quello approvato `0e5cb0df...`, ed e' anche la prova che il venv di produzione riproduce il risultato approvato.

```
######## soglie=fisse ########
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
BTC: ore bloccate 1, regolamenti di funding noti 481, primo 496018, ultimo definitivo 496498
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
scritti               /tmp/bt-A-fisse/journal.csv, /tmp/bt-A-fisse/equity.csv

=== TEMPI E MEMORIA ===
contesto (gap+funding) 13.68s
simulazione            7.02s
picco RSS dopo contesto 346.5 MB
picco RSS a fine run    346.5 MB
memory_limit DuckDB     512MB
######## soglie=congelate ########
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
BTC: ore bloccate 1, regolamenti di funding noti 481, primo 496018, ultimo definitivo 496498
BTC: ore bloccate DENTRO la finestra 0 []
BTC: soglie di buco (s) [('l2Book', 30.0, 'congelata@2026-08-22 (minimo_assoluto)'), ('trades', 30.0, 'congelata@2026-08-22 (minimo_assoluto)'), ('activeAssetCtx', 30.0, 'congelata@2026-08-22 (minimo_assoluto)')]

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
scritti               /tmp/bt-A-congelate/journal.csv, /tmp/bt-A-congelate/equity.csv

=== TEMPI E MEMORIA ===
contesto (gap+funding) 13.36s
simulazione            7.05s
picco RSS dopo contesto 302.0 MB
picco RSS a fine run    302.0 MB
memory_limit DuckDB     512MB
```

## Esecuzione a quattro coin: dove il digest cambia

`--coins BTC,ETH,HYPE,SOL --strategy flat`, stessa finestra A. Qui le soglie divergono (trades ETH 48,69 s, HYPE 30,37 s, SOL 79,37 s) e con esse le ore bloccate.

```
######## 4 coin, soglie=fisse ########
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
BTC: ore bloccate 1, regolamenti di funding noti 482, primo 496018, ultimo definitivo 496499
BTC: ore bloccate DENTRO la finestra 0 []
BTC: soglie di buco (s) [('activeAssetCtx', Decimal('30.000'), 'pavimento_fisso'), ('l2Book', Decimal('30.000'), 'pavimento_fisso'), ('trades', Decimal('30.000'), 'pavimento_fisso')]
ETH: ore bloccate 7, regolamenti di funding noti 482, primo 496018, ultimo definitivo 496499
ETH: ore bloccate DENTRO la finestra 0 []
ETH: soglie di buco (s) [('activeAssetCtx', Decimal('30.000'), 'pavimento_fisso'), ('l2Book', Decimal('30.000'), 'pavimento_fisso'), ('trades', Decimal('30.000'), 'pavimento_fisso')]
HYPE: ore bloccate 1, regolamenti di funding noti 482, primo 496018, ultimo definitivo 496499
HYPE: ore bloccate DENTRO la finestra 0 []
HYPE: soglie di buco (s) [('activeAssetCtx', Decimal('30.000'), 'pavimento_fisso'), ('l2Book', Decimal('30.000'), 'pavimento_fisso'), ('trades', Decimal('30.000'), 'pavimento_fisso')]
SOL: ore bloccate 42, regolamenti di funding noti 482, primo 496018, ultimo definitivo 496499
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
scritti               /tmp/bt-4c-fisse/journal.csv, /tmp/bt-4c-fisse/equity.csv

=== TEMPI E MEMORIA ===
contesto (gap+funding) 57.14s
simulazione            24.77s
picco RSS dopo contesto 566.4 MB
picco RSS a fine run    566.4 MB
memory_limit DuckDB     512MB
######## 4 coin, soglie=congelate ########
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
BTC: ore bloccate 1, regolamenti di funding noti 482, primo 496018, ultimo definitivo 496499
BTC: ore bloccate DENTRO la finestra 0 []
BTC: soglie di buco (s) [('activeAssetCtx', 30.0, 'congelata@2026-08-22 (minimo_assoluto)'), ('l2Book', 30.0, 'congelata@2026-08-22 (minimo_assoluto)'), ('trades', 30.0, 'congelata@2026-08-22 (minimo_assoluto)')]
ETH: ore bloccate 1, regolamenti di funding noti 482, primo 496018, ultimo definitivo 496499
ETH: ore bloccate DENTRO la finestra 0 []
ETH: soglie di buco (s) [('activeAssetCtx', 30.0, 'congelata@2026-08-22 (minimo_assoluto)'), ('l2Book', 30.0, 'congelata@2026-08-22 (minimo_assoluto)'), ('trades', 48.685, 'congelata@2026-08-22 (p99)')]
HYPE: ore bloccate 1, regolamenti di funding noti 482, primo 496018, ultimo definitivo 496499
HYPE: ore bloccate DENTRO la finestra 0 []
HYPE: soglie di buco (s) [('activeAssetCtx', 30.0, 'congelata@2026-08-22 (minimo_assoluto)'), ('l2Book', 30.0, 'congelata@2026-08-22 (minimo_assoluto)'), ('trades', 30.37, 'congelata@2026-08-22 (p99)')]
SOL: ore bloccate 1, regolamenti di funding noti 482, primo 496018, ultimo definitivo 496499
SOL: ore bloccate DENTRO la finestra 0 []
SOL: soglie di buco (s) [('activeAssetCtx', 30.0, 'congelata@2026-08-22 (minimo_assoluto)'), ('l2Book', 30.0, 'congelata@2026-08-22 (minimo_assoluto)'), ('trades', 79.374, 'congelata@2026-08-22 (p99)')]

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
scritti               /tmp/bt-4c-congelate/journal.csv, /tmp/bt-4c-congelate/equity.csv

=== TEMPI E MEMORIA ===
contesto (gap+funding) 54.99s
simulazione            24.24s
picco RSS dopo contesto 576.2 MB
picco RSS a fine run    576.2 MB
memory_limit DuckDB     512MB
```

## SOL, strategia casuale: qui il digest cambia davvero

`--coins SOL --strategy random --seed 7`, stessa finestra A. E' il caso in cui la differenza di soglia si trasforma in un risultato diverso: col pavimento fisso 660 barre su 1441 sono interamente bloccate e la simulazione ne usa 780; con le soglie congelate nessuna.

```
######## SOL random, soglie=fisse ########
=== CONFIGURAZIONE ===
data_dir              /home/ubuntu/hl-data/mainnet
coin                  SOL
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
trade deduplicati     32951
SOL: ore bloccate 42, regolamenti di funding noti 482, primo 496018, ultimo definitivo 496499
SOL: ore bloccate DENTRO la finestra 11 [496344, 496345, 496346, 496348, 496349, 496350, 496354, 496355, 496356, 496357, 496360]
SOL: soglie di buco (s) [('activeAssetCtx', Decimal('30.000'), 'pavimento_fisso'), ('l2Book', Decimal('30.000'), 'pavimento_fisso'), ('trades', Decimal('30.000'), 'pavimento_fisso')]

=== BARRE E ORDINI ===
barre                 1441
barre con decisione   780
barre tutte bloccate  660
ordini                50
fill taker            54
fill maker            0
rifiuti               0 {}
chiusure d'ufficio    3 (fallite 0)
eta' del book sui fill (ms)  n=54 min=239.161996 p50=2413.538274 p90=4916.242172 max=5279.582902

=== CONTO ===
equity iniziale       10000.0
equity finale         9958.67068789417
PnL                   -41.32931210583047
realizzato            -7.330580309036073
fee                   34.00093420994665
funding               -0.0022024131444305494 (7 regolamenti, 0 non noti)
residuo conservazione 7.275957614183426e-12
posizione finale      {'SOL': 0.0}

=== RIPRODUCIBILITA' ===
digest sha256         6e8e876348c68dd2257e6379ab5142053979b2accd235a3fbe626f14f19cce7d
righe di giornale     61
righe di equity       1441
scritti               /tmp/bt-SOL-fisse/journal.csv, /tmp/bt-SOL-fisse/equity.csv

=== TEMPI E MEMORIA ===
contesto (gap+funding) 14.85s
simulazione            4.98s
picco RSS dopo contesto 316.7 MB
picco RSS a fine run    316.7 MB
memory_limit DuckDB     512MB
######## SOL random, soglie=congelate ########
=== CONFIGURAZIONE ===
data_dir              /home/ubuntu/hl-data/mainnet
coin                  SOL
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
trade deduplicati     32951
SOL: ore bloccate 1, regolamenti di funding noti 482, primo 496018, ultimo definitivo 496499
SOL: ore bloccate DENTRO la finestra 0 []
SOL: soglie di buco (s) [('activeAssetCtx', 30.0, 'congelata@2026-08-22 (minimo_assoluto)'), ('l2Book', 30.0, 'congelata@2026-08-22 (minimo_assoluto)'), ('trades', 79.374, 'congelata@2026-08-22 (p99)')]

=== BARRE E ORDINI ===
barre                 1441
barre con decisione   1440
barre tutte bloccate  0
ordini                93
fill taker            94
fill maker            0
rifiuti               0 {}
chiusure d'ufficio    0 (fallite 0)
eta' del book sui fill (ms)  n=94 min=23.728224 p50=2429.189734 p90=4498.311172 max=5432.284516

=== CONTO ===
equity iniziale       10000.0
equity finale         9931.283949383404
PnL                   -68.71605061659648
realizzato            -10.220089542438622
fee                   58.48519186402034
funding               0.010769210136737036 (13 regolamenti, 0 non noti)
residuo conservazione 0.0
posizione finale      {'SOL': 0.0}

=== RIPRODUCIBILITA' ===
digest sha256         47ec0de5c35db8aaffb8cac0d3b9ae76b08a9d95f809a1873715a38d88276c25
righe di giornale     107
righe di equity       1441
scritti               /tmp/bt-SOL-congelate/journal.csv, /tmp/bt-SOL-congelate/equity.csv

=== TEMPI E MEMORIA ===
contesto (gap+funding) 13.20s
simulazione            4.75s
picco RSS dopo contesto 308.0 MB
picco RSS a fine run    308.0 MB
memory_limit DuckDB     512MB
```

## Dispersione: una sola formula per i due N

`.venv/bin/python -m tools.chi2_strategia_casuale 100`. La prima riga e' in unita' di varianza, la seconda nelle stesse unita' della domanda originale. `0,6648` e' **chi2/gdl calcolato con la varianza misurata degli incrementi di mark**, cioe' una VARIANZA relativa: in dispersione sono 0,8154, la sua radice.

```
N=  100  chi2     66.97 su 100 gdl  chi2/gdl 0.6697  (-2.34 sigma)  chi2/gdl con varianza misurata 0.6648  media z +0.0853 (+0.853 errori standard)
        dispersione radice(chi2/N) 0.8184  con varianza misurata 0.8154  sd campionaria / sigma medio 0.8258  (media degli scarti +1.0051, sigma medio 11.1947)
tempo 8.1s
```

## Il test committato

Le righe stampate dal test della strategia casuale dopo la modifica.

```

[varianza] righe di equity 401, incrementi di mark 400, passi effettivi 267, passi nel sigma teorico 267, frazione flat 0.3342
[varianza] PnL di percorso sum(pos*dmark) -8.061220334272923 vs lordo_al_mid -8.061220334272921, differenza -1.7763568394002505e-15
.
[casuale x100] media degli scarti 1.0051320602131486, deviazione standard campionaria 9.245025187867975, sigma teorico medio 11.194678134882036
[casuale x100] media in unita' di sigma: +0.0898 sigma del singolo tiro, +0.898 errori standard della media (errore standard 1.1194678134882037)
[casuale] progressivo N= 20: media   +5.3586, errore standard  2.5634, t +2.090
[casuale] progressivo N= 50: media   +1.8774, errore standard  1.5804, t +1.188
[casuale] progressivo N=100: media   +1.0051, errore standard  1.1195, t +0.898
[casuale] primo blocco (1-20)      N= 20 media   +5.3586 t +2.141
[casuale] blocco aggiunto (21-100) N= 80 media   -0.0832 t -0.066
[casuale] totale (1-100)           N=100 media   +1.0051 t +0.898
[casuale] chi quadro sum (scarto/sigma)^2 = 66.97071656195179 su 100 gdl (-2.34 sigma, chi2/gdl 0.6697)  |  riferimento su 1000 ripetizioni della stessa famiglia di semi: 1017.9 su 1000 gdl (+0.40 sigma, chi2/gdl 1.0179) -> la carenza di varianza a N=100 e' una fluttuazione
[casuale] dispersione radice(chi2/N) 0.8184 a N=100  |  1.0089 a N=1000
[casuale] altro stimatore, non confrontabile col precedente: sd campionaria / sigma teorico medio 0.8258 a N=100
.
2 passed, 20 deselected in 9.12s
```

## Ambiente: venv di produzione e i due venv temporanei

Il primo blocco e' il venv che usa il collector; il secondo l'inventario dei due venv in `/tmp`, preso prima di rimuoverli.

```
=== venv di produzione: /home/ubuntu/hl-trading/.venv ===
usato da hl-collector: /home/ubuntu/hl-trading/.venv/bin/python
creato 2026-08-02 07:50:03.964530351 +0000
Python 3.11.15
--- pip list ---
Package    Version
---------- -------
duckdb     1.5.5
iniconfig  2.3.0
packaging  26.3
pip        26.2
pluggy     1.6.0
pyarrow    25.0.0
Pygments   2.21.0
pytest     9.1.1
PyYAML     6.0.3
setuptools 79.0.1
websockets 17.0.1
--- versioni che contano ---
duckdb 1.5.5
pyarrow 25.0.0

=== /tmp/pytestenv (duckdb dal sistema, niente pyarrow) ===
Python 3.12.3
duckdb 1.5.5 da /home/ubuntu/.local/lib/python3.12/site-packages/duckdb/__init__.py
ModuleNotFoundError: No module named 'pyarrow'

=== chi importa pyarrow nel repo ===
/home/ubuntu/hl-trading/Trading-System/collector/writer.py:29:import pyarrow as pa
/home/ubuntu/hl-trading/Trading-System/collector/writer.py:30:import pyarrow.parquet as pq
```

```
=== /tmp/pytestenv ===
creato 2026-08-19 23:27:44.181506438 +0000   ultima modifica 2026-08-19 23:27:44.183741248 +0000   dimensione 4096 byte (albero: 25965355 byte)
Python 3.12.3
pyvenv.cfg:
  home = /usr/bin
  include-system-site-packages = true
  version = 3.12.3
  executable = /usr/bin/python3.12
  command = /usr/bin/python3 -m venv --system-site-packages /tmp/pytestenv
pacchetti installati NEL venv (site-packages proprio):
  _pytest
  iniconfig
  iniconfig-2.3.0.dist-info
  packaging
  packaging-26.3.dist-info
  pip
  pip-24.0.dist-info
  pluggy
  pluggy-1.6.0.dist-info
  py.py
  pygments
  pygments-2.21.0.dist-info
  pytest
  pytest-9.1.1.dist-info

=== /tmp/pytest311 ===
creato 2026-08-20 09:16:32.474682323 +0000   ultima modifica 2026-08-20 09:16:32.476766613 +0000   dimensione 4096 byte (albero: 37004774 byte)
Python 3.11.15
pyvenv.cfg:
  home = /home/ubuntu/.local/share/uv/python/cpython-3.11.15-linux-aarch64-gnu/bin
  include-system-site-packages = false
  version = 3.11.15
  executable = /home/ubuntu/.local/share/uv/python/cpython-3.11.15-linux-aarch64-gnu/bin/python3.11
  command = /home/ubuntu/.local/share/uv/python/cpython-3.11.15-linux-aarch64-gnu/bin/python3.11 -m venv /tmp/pytest311
pacchetti installati NEL venv (site-packages proprio):
  _distutils_hack
  _pytest
  distutils-precedence.pth
  iniconfig
  iniconfig-2.3.0.dist-info
  packaging
  packaging-26.3.dist-info
  pip
  pip-24.0.dist-info
  pkg_resources
  pluggy
  pluggy-1.6.0.dist-info
  py.py
  pygments
  pygments-2.21.0.dist-info
  pytest
  pytest-9.1.1.dist-info
  setuptools
  setuptools-79.0.1.dist-info
```

## Suite completa, entrambi i runner

```
######## pytest ########
334 passed, 1753 subtests passed in 245.77s (0:04:05)
######## unittest ########
Ran 334 tests in 160.255s
OK
```

