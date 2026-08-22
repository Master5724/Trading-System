# Partizioni, deriva delle soglie e profilo di memoria — output grezzo

Tutto quello che sta qui e' incollato dai log, non ricopiato a mano. I comandi
pesanti girano sotto `systemd-run --user --scope -p MemoryMax=2G nice -n 19
ionice -c 3`, in sola lettura su `/home/ubuntu/hl-data/mainnet`, col collector
attivo (PID 1063457, nessun riavvio).

## Le 17 partizioni, una per una

`parquet_file_metadata` sui footer, non una scansione: i conteggi righe costano i metadati dei file. `stato` confronta con le 12 partizioni che il backtester chiede (`backtest.feed.REQUIRED_CHANNELS` x 4 coin).

17 e non 16 perche' `allMids` e' un canale unico e non per coin: 4 coin x 4 canali per coin, piu' `allMids/_global`.

Script della misura: `/tmp/censimento_partizioni.py`.

```
partizioni nel file congelato: 17
coin: BTC, ETH, HYPE, SOL
canali del backtester (REQUIRED_CHANNELS): l2Book, trades, activeAssetCtx -> 4 coin x 3 canali = 12

partizione               stato          primo       ultimo      giorni        righe    righe/g     file
activeAssetCtx/BTC       gia' vista      2026-08-02  2026-08-22      21    1,701,881     81,041   29,082 rss=397MB
activeAssetCtx/ETH       gia' vista      2026-08-02  2026-08-22      21    1,701,873     81,041   29,082 rss=399MB
activeAssetCtx/HYPE      gia' vista      2026-08-02  2026-08-22      21    1,701,854     81,040   29,082 rss=399MB
activeAssetCtx/SOL       gia' vista      2026-08-02  2026-08-22      21    1,701,866     81,041   29,082 rss=400MB
allMids/_global          MAI CONTROLLATA 2026-08-02  2026-08-22      21      344,054     16,383   29,071 rss=400MB
candle/BTC               MAI CONTROLLATA 2026-08-02  2026-08-22      21    1,724,498     82,118   29,074 rss=400MB
candle/ETH               MAI CONTROLLATA 2026-08-02  2026-08-22      21    1,013,127     48,244   29,065 rss=400MB
candle/HYPE              MAI CONTROLLATA 2026-08-02  2026-08-22      21    1,350,632     64,315   29,074 rss=400MB
candle/SOL               MAI CONTROLLATA 2026-08-02  2026-08-22      21      640,797     30,514   29,063 rss=400MB
l2Book/BTC               gia' vista      2026-08-02  2026-08-22      21      323,225     15,391   29,073 rss=400MB
l2Book/ETH               gia' vista      2026-08-02  2026-08-22      21      323,224     15,391   29,073 rss=400MB
l2Book/HYPE              gia' vista      2026-08-02  2026-08-22      21      323,219     15,391   29,073 rss=400MB
l2Book/SOL               gia' vista      2026-08-02  2026-08-22      21      323,235     15,392   29,074 rss=400MB
trades/BTC               gia' vista      2026-08-02  2026-08-22      21    2,365,905    112,662   29,093 rss=400MB
trades/ETH               gia' vista      2026-08-02  2026-08-22      21    1,278,535     60,882   29,076 rss=400MB
trades/HYPE              gia' vista      2026-08-02  2026-08-22      21    1,779,052     84,716   29,090 rss=401MB
trades/SOL               gia' vista      2026-08-02  2026-08-22      21      748,510     35,643   29,071 rss=401MB

mai controllate: 5 -> allMids/_global, candle/BTC, candle/ETH, candle/HYPE, candle/SOL
primo giorno di raccolta (minimo su tutte le partizioni): 2026-08-02
  allMids/_global: primo giorno 2026-08-02 (stesso giorno), 21 giorni consecutivi: si
  candle/BTC: primo giorno 2026-08-02 (stesso giorno), 21 giorni consecutivi: si
  candle/ETH: primo giorno 2026-08-02 (stesso giorno), 21 giorni consecutivi: si
  candle/HYPE: primo giorno 2026-08-02 (stesso giorno), 21 giorni consecutivi: si
  candle/SOL: primo giorno 2026-08-02 (stesso giorno), 21 giorni consecutivi: si

partizione               giorno           righe   (primi 2 e ultimi 2 giorni)
allMids/_global          2026-08-02      10,102
allMids/_global          2026-08-03      17,129
allMids/_global          2026-08-21      17,149
allMids/_global          2026-08-22       9,456
candle/BTC               2026-08-02      38,420
candle/BTC               2026-08-03      84,117
candle/BTC               2026-08-21     139,614
candle/BTC               2026-08-22      59,510
candle/ETH               2026-08-02      30,030
candle/ETH               2026-08-03      50,555
candle/ETH               2026-08-21      99,714
candle/ETH               2026-08-22      53,270
candle/HYPE              2026-08-02      35,490
candle/HYPE              2026-08-03      65,790
candle/HYPE              2026-08-21     127,908
candle/HYPE              2026-08-22      76,589
candle/SOL               2026-08-02      16,682
candle/SOL               2026-08-03      26,431
candle/SOL               2026-08-21      71,055
candle/SOL               2026-08-22      43,274

censimento: 179.0 s, picco RSS 401.3 MB
```

## Le cinque "mai controllate" erano gia' state controllate una volta

Il report del catalogo del 2026-08-16 ha scritto `derived_gaps.parquet` su tutte le partizioni, e quello del 2026-08-14 ne dichiara 25 (17 di stream piu' 8 di backfill). I conteggi di allora sono identici a quelli di oggi.

```
=== hl-reports/derived/derived_gaps.parquet, scritto il 2026-08-16 ===
partizione                buchi      tot_s     max_s
activeAssetCtx/BTC            6     5411.7    4961.6
activeAssetCtx/ETH            6     5412.0    4961.6
activeAssetCtx/HYPE           6     5412.8    4961.6
activeAssetCtx/SOL            6     5412.1    4961.6
allMids/_global               6     5426.2    4957.6
candle/BTC                    7     5645.3    4962.4
candle/ETH                    6     5661.9    4962.6
candle/HYPE                   6     5634.9    4961.5
candle/SOL                    4     5514.9    4962.9
l2Book/BTC                    6     5426.2    4963.2
l2Book/ETH                    6     5427.0    4963.2
l2Book/HYPE                   6     5427.8    4963.2
l2Book/SOL                    6     5427.0    4963.2
trades/BTC                    8     5612.8    4962.1
trades/ETH                    7     5570.8    4962.7
trades/HYPE                   8     5631.0    4961.5
trades/SOL                    6     5515.0    4962.8

=== meta del report del 2026-08-14 (lo stesso percorso, prima dei buchi derivati) ===
{
 "data_dir": "/home/ubuntu/hl-data/mainnet",
 "out_dir": "/home/ubuntu/hl-reports",
 "run_utc": "2026-08-14 15:35:33",
 "n_partitions": 25,
 "n_rows": 10954475,
 "first_utc": "2026-08-02 09:50:26",
 "last_utc": "2026-08-14 15:22:26",
 "span_days": 12.230556366412419,
 "elapsed_s": 841.016254901886,
 "hourly_rows": 7319,
 "parquet": "/home/ubuntu/hl-reports/hourly_metrics.parquet"
}
```

## Buchi e ore marcate sulle cinque partizioni, su tutto lo storico

21 giorni, soglie congelate. `%ore` e' sulle 504 ore dello storico.

Script della misura: `/tmp/buchi_mai_controllate.py`.

```
partizioni mai controllate: allMids/_global, candle/BTC, candle/ETH, candle/HYPE, candle/SOL
storico letto: 2026-08-02 -> 2026-08-22, 21 giorni, 504 ore
ts_ordered: 5,073,851 righe in 86.3 s, rss 1309.9 MB
buchi totali sulle mai controllate: 29

partizione                soglia_s provenienza                   buchi      tot_s     max_s   ore   %ore
allMids/_global              30.00 congelata@2026-08-22 (minimo_assoluto)      6     5426.2    4957.6     8   1.6%
candle/BTC                   30.00 congelata@2026-08-22 (minimo_assoluto)      7     5645.3    4962.4     9   1.8%
candle/ETH                   52.01 congelata@2026-08-22 (p99)        6     5661.9    4962.6     8   1.6%
candle/HYPE                  34.60 congelata@2026-08-22 (p99)        6     5634.9    4961.5     8   1.6%
candle/SOL                   83.04 congelata@2026-08-22 (p99)        4     5514.9    4962.9     6   1.2%
unione delle ore marcate                                                                        9   1.8%

i dieci buchi piu' lunghi:
  candle/SOL           2026-08-14 15:55:25 -> 2026-08-14 17:18:08    4962.9 s
  candle/ETH           2026-08-14 15:55:25 -> 2026-08-14 17:18:08    4962.6 s
  candle/BTC           2026-08-14 15:55:25 -> 2026-08-14 17:18:08    4962.4 s
  candle/HYPE          2026-08-14 15:55:26 -> 2026-08-14 17:18:08    4961.5 s
  allMids/_global      2026-08-14 15:55:30 -> 2026-08-14 17:18:08    4957.6 s
  candle/HYPE          2026-08-15 08:04:26 -> 2026-08-15 08:08:17     231.2 s
  candle/SOL           2026-08-15 08:04:27 -> 2026-08-15 08:08:17     230.0 s
  candle/BTC           2026-08-15 08:04:29 -> 2026-08-15 08:08:18     228.7 s
  candle/ETH           2026-08-15 08:04:30 -> 2026-08-15 08:08:17     226.6 s
  candle/HYPE          2026-08-08 08:34:18 -> 2026-08-08 08:38:00     222.5 s

buchi per giorno (solo i giorni che ne hanno):
  2026-08-02     4 buchi      271.7 s
  2026-08-07     4 buchi      210.7 s
  2026-08-08     5 buchi     1004.0 s
  2026-08-14    10 buchi    25320.1 s
  2026-08-15     5 buchi     1046.7 s
  2026-08-16     1 buchi       30.1 s

totale: 86.5 s, picco RSS 1309.9 MB
```

## Il controllo di deriva, come esce

`python -m catalog.deriva --data-dir /home/ubuntu/hl-data/mainnet`. Le prime tre righe sono la provenienza delle soglie e la copertura; la tabella mette il p99 congelato accanto a quello del solo ultimo giorno intero.

```
=== SOGLIE DI BUCO E DERIVA (2026-08-21) ===
soglie: congelate v1, calcolate il 2026-08-22T10:45:14Z
  storico di calcolo: 2026-08-02 -> 2026-08-22 (21 giorni, 19.223.057 righe)
  copertura: 17 partizioni nel file, 17 chieste, 17 presenti nei dati
partizione              soglia  p99cong   p99gg  rapp  oltre  ore
allMids/_global          30.00     5.35    5.39  1.01      0    0
l2Book/SOL               30.00     5.71    5.75  1.01      0    0
l2Book/HYPE              30.00     5.71    5.75  1.01      0    0
l2Book/ETH               30.00     5.71    5.74  1.01      0    0
l2Book/BTC               30.00     5.71    5.74  1.01      0    0
activeAssetCtx/HYPE      30.00     1.41    1.38  0.98      0    0
activeAssetCtx/SOL       30.00     1.41    1.38  0.98      0    0
activeAssetCtx/ETH       30.00     1.41    1.38  0.98      0    0
activeAssetCtx/BTC       30.00     1.41    1.38  0.98      0    0
trades/BTC               30.00     4.48    1.85  0.41      0    0
candle/BTC               30.00     5.17    2.12  0.41      0    0
candle/SOL               83.04    16.61    6.33  0.38      0    0
candle/ETH               52.01    10.40    3.83  0.37      0    0
trades/SOL               79.37    15.87    5.82  0.37      0    0
trades/ETH               48.69     9.74    3.32  0.34      0    0
trades/HYPE              30.37     6.07    2.03  0.33      0    0
candle/HYPE              34.60     6.92    2.28  0.33      0    0
scostamento massimo: allMids/_global p99 5.35 s congelato -> 5.39 s nel giorno (+0.8%)
intervalli oltre soglia: 0 su 1.534.911; ore marcate: 0
costo del controllo: 2 giorni letti, 17.9 s, picco RSS 218 MB
```

## Quanto costa, e da cosa dipende il costo

Il picco NON dipende dalla mole dei dati letti: dipende da `--memory-limit`. I risultati delle quattro esecuzioni sono identici (`diff` sulle tabelle), cambia solo quanto DuckDB versa su disco. Il default e' 128MB.

```
=== controllo di deriva: il picco lo fissa --memory-limit, non i dati ===
(stessa tabella di risultati in tutti e quattro i casi: verificato con diff)
512MB        costo del controllo: 2 giorni letti, 30.8 s, picco RSS 569 MB
256MB        costo del controllo: 2 giorni letti, 18.4 s, picco RSS 346 MB
128MB        costo del controllo: 2 giorni letti, 19.1 s, picco RSS 226 MB
64MB         costo del controllo: 2 giorni letti, 21.3 s, picco RSS 158 MB

128MB, due esecuzioni di seguito (cache fredda poi calda):
costo del controllo: 2 giorni letti, 35.6 s, picco RSS 217 MB
costo del controllo: 2 giorni letti, 17.9 s, picco RSS 218 MB
```

## Il report giornaliero, per intero, come verra' consegnato

`bash report24_v2.sh`, la sorgente di `report-24h.txt` che il cron consegna su Telegram. Da notare in fondo alla sezione CHANNEL/COIN STATUS: `allMids/_global` adesso c'e', e c'e' il conteggio delle partizioni elencate.

```
=== REPORT 2026-08-22T17:58:12Z ===
=== PROCESS ===
PID: 1063457
Active since: Fri 2026-08-14 20:33:59 UTC
Elapsed seconds: 681852

=== MAINNET DATA DIR ===
4.0G	/home/ubuntu/hl-data/mainnet

=== DISK FREE ===
Filesystem      Size  Used Avail Use% Mounted on
tmpfs           1.2G  1.4M  1.2G   1% /run
/dev/sda1       192G   14G  178G   8% /
tmpfs           5.9G     0  5.9G   0% /dev/shm
tmpfs           5.0M     0  5.0M   0% /run/lock
efivarfs        256K   14K  243K   6% /sys/firmware/efi/efivars
/dev/sda16      891M  134M  695M  17% /boot
/dev/sda15       98M  6.4M   92M   7% /boot/efi
tmpfs           1.2G   28K  1.2G   1% /run/user/1001

=== GAPS LAST 24H ===
Total gaps: 8
Total duration seconds: 37.212
Max duration seconds: 6.879 (cause=disconnect, end=2026-08-22T05:12:00.970000+00:00)
Reasons: {'closed by server': 8}

PROCESS FREEZE EVENTS (ultime 24h): 0

=== CHANNEL/COIN STATUS ===
channel        coin    age_min parquet_last24
activeAssetCtx BTC         0.5           1448
activeAssetCtx ETH         0.5           1448
activeAssetCtx HYPE        0.5           1448
activeAssetCtx SOL         0.5           1448
allMids        _global      0.5           1448
candle         BTC         0.5           1448
candle         ETH         0.5           1448
candle         HYPE        0.5           1448
candle         SOL         0.5           1448
l2Book         BTC         0.5           1448
l2Book         ETH         0.5           1448
l2Book         HYPE        0.5           1448
l2Book         SOL         0.5           1448
trades         BTC         0.5           1448
trades         ETH         0.5           1448
trades         HYPE        0.5           1449
trades         SOL         0.5           1448
partizioni elencate: 17

=== SOGLIE DI BUCO E DERIVA (2026-08-21) ===
soglie: congelate v1, calcolate il 2026-08-22T10:45:14Z
  storico di calcolo: 2026-08-02 -> 2026-08-22 (21 giorni, 19.223.057 righe)
  copertura: 17 partizioni nel file, 17 chieste, 17 presenti nei dati
partizione              soglia  p99cong   p99gg  rapp  oltre  ore
allMids/_global          30.00     5.35    5.39  1.01      0    0
l2Book/SOL               30.00     5.71    5.75  1.01      0    0
l2Book/HYPE              30.00     5.71    5.75  1.01      0    0
l2Book/ETH               30.00     5.71    5.74  1.01      0    0
l2Book/BTC               30.00     5.71    5.74  1.01      0    0
activeAssetCtx/HYPE      30.00     1.41    1.38  0.98      0    0
activeAssetCtx/SOL       30.00     1.41    1.38  0.98      0    0
activeAssetCtx/ETH       30.00     1.41    1.38  0.98      0    0
activeAssetCtx/BTC       30.00     1.41    1.38  0.98      0    0
trades/BTC               30.00     4.48    1.85  0.41      0    0
candle/BTC               30.00     5.17    2.12  0.41      0    0
candle/SOL               83.04    16.61    6.33  0.38      0    0
candle/ETH               52.01    10.40    3.83  0.37      0    0
trades/SOL               79.37    15.87    5.82  0.37      0    0
trades/ETH               48.69     9.74    3.32  0.34      0    0
trades/HYPE              30.37     6.07    2.03  0.33      0    0
candle/HYPE              34.60     6.92    2.28  0.33      0    0
scostamento massimo: allMids/_global p99 5.35 s congelato -> 5.39 s nel giorno (+0.8%)
intervalli oltre soglia: 0 su 1.534.911; ore marcate: 0
costo del controllo: 2 giorni letti, 67.7 s, picco RSS 226 MB
```

## Profilo di memoria di catalog.soglie — A, com'e' oggi (memory_limit=1GB)

Una riga per partizione accumulata in `ts_ordered`. Il picco e' gia' fatto dopo la PRIMA partizione: 1.201,5 MB su 1.718.388 righe di 19.565.145.

Script della misura: `/tmp/profilo_soglie.py`.

```
=== esecuzione a — 17 partizioni ===
RSS prima di connettersi: 52.8 MB
connect                                        t=    0.0s  rss=   56.5MB  spill=     0.0MB
build_ordered  1/17 activeAssetCtx/BTC (1.718.388 righe) t=   10.5s  rss= 1201.5MB  spill=     0.0MB
build_ordered  2/17 activeAssetCtx/ETH (3.436.768 righe) t=   21.4s  rss= 1201.5MB  spill=    61.8MB
build_ordered  3/17 activeAssetCtx/HYPE (5.155.129 righe) t=   37.7s  rss= 1210.6MB  spill=   204.7MB
build_ordered  4/17 activeAssetCtx/SOL (6.873.502 righe) t=   52.4s  rss= 1210.6MB  spill=   384.7MB
build_ordered  5/17 allMids/_global (7.220.914 righe) t=  103.5s  rss= 1210.6MB  spill=   384.7MB
build_ordered  6/17 candle/BTC (8.963.271 righe) t=  137.3s  rss= 1210.6MB  spill=   530.5MB
build_ordered  7/17 candle/ETH (9.988.589 righe) t=  169.9s  rss= 1210.6MB  spill=   530.5MB
build_ordered  8/17 candle/HYPE (11.361.060 righe) t=  201.8s  rss= 1210.6MB  spill=   661.2MB
build_ordered  9/17 candle/SOL (12.009.961 righe) t=  232.7s  rss= 1210.6MB  spill=   661.2MB
build_ordered 10/17 l2Book/BTC (12.336.357 righe) t=  269.9s  rss= 1210.6MB  spill=   661.2MB
build_ordered 11/17 l2Book/ETH (12.662.752 righe) t=  315.4s  rss= 1210.6MB  spill=   661.2MB
build_ordered 12/17 l2Book/HYPE (12.989.153 righe) t=  353.0s  rss= 1210.6MB  spill=   661.2MB
build_ordered 13/17 l2Book/SOL (13.315.569 righe) t=  388.9s  rss= 1210.6MB  spill=   677.7MB
build_ordered 14/17 trades/BTC (15.705.430 righe) t=  441.9s  rss= 1281.8MB  spill=  1071.3MB
build_ordered 15/17 trades/ETH (16.998.463 righe) t=  486.7s  rss= 1281.8MB  spill=  1071.3MB
build_ordered 16/17 trades/HYPE (18.807.240 righe) t=  536.4s  rss= 1281.8MB  spill=  1227.2MB
build_ordered 17/17 trades/SOL (19.565.145 righe) t=  578.2s  rss= 1281.8MB  spill=  1227.2MB
build_thresholds (p99 per partizione)          t=  582.2s  rss= 1286.7MB  spill=  1233.7MB
close                                          t=  582.5s  rss= 1286.7MB  spill=     0.0MB

A  com'e' oggi: 582.5 s totali (578.2 s in build_ordered). 19.565.145 righe. picco RSS 1286.7 MB
```

## B — stesso percorso, memory_limit=256MB

```
=== esecuzione b — 17 partizioni ===
RSS prima di connettersi: 52.6 MB
connect                                        t=    0.0s  rss=   56.2MB  spill=     0.0MB
build_ordered  1/17 activeAssetCtx/BTC (1.718.975 righe) t=   19.7s  rss=  402.3MB  spill=   297.9MB
build_ordered  2/17 activeAssetCtx/ETH (3.437.942 righe) t=   36.5s  rss=  405.3MB  spill=   478.9MB
build_ordered  3/17 activeAssetCtx/HYPE (5.156.890 righe) t=   55.7s  rss=  409.9MB  spill=   676.4MB
build_ordered  4/17 activeAssetCtx/SOL (6.875.850 righe) t=   73.6s  rss=  410.0MB  spill=   863.7MB
build_ordered  5/17 allMids/_global (7.223.381 righe) t=  124.6s  rss=  410.0MB  spill=   864.2MB
build_ordered  6/17 candle/BTC (8.966.482 righe) t=  159.3s  rss=  410.7MB  spill=  1064.9MB
build_ordered  7/17 candle/ETH (9.992.191 righe) t=  191.3s  rss=  414.5MB  spill=  1073.8MB
build_ordered  8/17 candle/HYPE (11.365.333 righe) t=  225.3s  rss=  414.5MB  spill=  1240.7MB
build_ordered  9/17 candle/SOL (12.014.513 righe) t=  255.8s  rss=  414.5MB  spill=  1244.2MB
build_ordered 10/17 l2Book/BTC (12.341.021 righe) t=  291.9s  rss=  415.5MB  spill=  1244.2MB
build_ordered 11/17 l2Book/ETH (12.667.528 righe) t=  330.0s  rss=  426.0MB  spill=  1252.2MB
build_ordered 12/17 l2Book/HYPE (12.994.041 righe) t=  368.5s  rss=  426.0MB  spill=  1272.2MB
build_ordered 13/17 l2Book/SOL (13.320.569 righe) t=  404.8s  rss=  426.0MB  spill=  1291.8MB
build_ordered 14/17 trades/BTC (15.711.332 righe) t=  462.7s  rss=  426.0MB  spill=  1722.8MB
build_ordered 15/17 trades/ETH (17.004.933 righe) t=  511.1s  rss=  426.0MB  spill=  1758.7MB
build_ordered 16/17 trades/HYPE (18.814.558 righe) t=  565.8s  rss=  426.0MB  spill=  1982.4MB
build_ordered 17/17 trades/SOL (19.572.813 righe) t=  609.2s  rss=  426.0MB  spill=  1988.2MB
build_thresholds (p99 per partizione)          t=  614.7s  rss=  577.1MB  spill=  2021.6MB
close                                          t=  614.9s  rss=  577.1MB  spill=     0.0MB

B  tetto piu' basso: 614.9 s totali (609.2 s in build_ordered). 19.572.813 righe. picco RSS 577.1 MB
```

## C — una partizione per volta, connessione nuova ogni volta

`ts_ordered` non contiene mai piu' di una partizione: spill zero.

```
=== esecuzione c — 17 partizioni ===
activeAssetCtx/BTC        1.719.562 righe    19.4s  rss=  413.1MB  spill=     0.0MB
activeAssetCtx/ETH        1.719.554 righe    14.6s  rss=  430.0MB  spill=     0.0MB
activeAssetCtx/HYPE       1.719.535 righe    14.9s  rss=  430.0MB  spill=     0.0MB
activeAssetCtx/SOL        1.719.606 righe    14.5s  rss=  430.0MB  spill=     0.0MB
allMids/_global             347.650 righe    45.0s  rss=  430.0MB  spill=     0.0MB
candle/BTC                1.743.656 righe    28.5s  rss=  430.5MB  spill=     0.0MB
candle/ETH                1.026.095 righe    28.0s  rss=  455.9MB  spill=     0.0MB
candle/HYPE               1.373.834 righe    29.1s  rss=  455.9MB  spill=     0.0MB
candle/SOL                  649.500 righe    26.9s  rss=  496.7MB  spill=     0.0MB
l2Book/BTC                  326.608 righe    32.4s  rss=  496.7MB  spill=     0.0MB
l2Book/ETH                  326.619 righe    35.5s  rss=  496.7MB  spill=     0.0MB
l2Book/HYPE                 326.625 righe    34.0s  rss=  496.7MB  spill=     0.0MB
l2Book/SOL                  326.629 righe    34.4s  rss=  496.7MB  spill=     0.0MB
trades/BTC                2.391.309 righe    59.4s  rss=  496.7MB  spill=     0.0MB
trades/ETH                1.293.973 righe    46.9s  rss=  496.7MB  spill=     0.0MB
trades/HYPE               1.810.467 righe    63.6s  rss=  496.7MB  spill=     0.0MB
trades/SOL                  758.567 righe    49.3s  rss=  505.8MB  spill=     0.0MB

C: 577.3 s totali, picco RSS 505.8 MB
```

## Le tre esecuzioni danno le stesse soglie?

Non al bit, e il motivo e' dichiarato: il collector ha scritto righe mentre le tre giravano in fila. Lo scarto residuo e' 0,04%.

```
=== le tre esecuzioni danno la stessa soglia? ===
partizione                    A (1GB)    B (256MB)  C (per part.)   max diff
activeAssetCtx/BTC            30.0000      30.0000        30.0000     0.0000
activeAssetCtx/ETH            30.0000      30.0000        30.0000     0.0000
activeAssetCtx/HYPE           30.0000      30.0000        30.0000     0.0000
activeAssetCtx/SOL            30.0000      30.0000        30.0000     0.0000
allMids/_global               30.0000      30.0000        30.0000     0.0000
candle/BTC                    30.0000      30.0000        30.0000     0.0000
candle/ETH                    51.5904      51.5829        51.5804     0.0099
candle/HYPE                   34.3483      34.3430        34.3399     0.0085
candle/SOL                    82.4639      82.4568        82.4284     0.0354
l2Book/BTC                    30.0000      30.0000        30.0000     0.0000
l2Book/ETH                    30.0000      30.0000        30.0000     0.0000
l2Book/HYPE                   30.0000      30.0000        30.0000     0.0000
l2Book/SOL                    30.0000      30.0000        30.0000     0.0000
trades/BTC                    30.0000      30.0000        30.0000     0.0000
trades/ETH                    48.2768      48.2740        48.2748     0.0029
trades/HYPE                   30.2539      30.2526        30.2516     0.0023
trades/SOL                    78.9227      78.9001        78.8925     0.0302

scarto massimo fra le tre: 0.0354 s su candle/SOL (0.043%)

intervalli letti da ciascuna (il collector scrive mentre si misura):
  A: activeAssetCtx/BTC 1.718.387
  B: activeAssetCtx/BTC 1.718.974
  C: activeAssetCtx/BTC 1.719.561

=== il file congelato contro il ricalcolo di oggi (esecuzione A) ===
  candle/ETH               file   52.011 s  oggi   51.590 s   -0.420 s (-0.81%)
  candle/HYPE              file   34.605 s  oggi   34.348 s   -0.257 s (-0.74%)
  candle/SOL               file   83.036 s  oggi   82.464 s   -0.573 s (-0.69%)
  trades/ETH               file   48.685 s  oggi   48.277 s   -0.408 s (-0.84%)
  trades/HYPE              file   30.370 s  oggi   30.254 s   -0.116 s (-0.38%)
  trades/SOL               file   79.374 s  oggi   78.923 s   -0.451 s (-0.57%)

=== riepilogo delle tre esecuzioni ===
  A  com'e' oggi (1GB)               582.5 s   picco  1286.7 MB
  B  tetto 256MB                     614.9 s   picco   577.1 MB
  C  una partizione per volta        577.3 s   picco   505.8 MB
```

## Suite completa, entrambi i runner

```
######## pytest ########
343 passed, 1753 subtests passed in 404.80s (0:06:44)
######## unittest (discover -s tests -p test_*.py) ########
Ran 343 tests in 308.815s
OK
```

