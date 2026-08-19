# PR #12 — Modello di costo: verifica delle tre discrepanze e report sui dati reali

**Esecuzione:** 2026-08-19 08:53 UTC
**Dati:** `/home/ubuntu/hl-data/mainnet`, sola lettura, collector PID 1063457 vivo durante tutta l'esecuzione
**Report JSON:** `/tmp/hl-reports-pr12/costs_report.json`
**Test:** 270/270 verdi, tutti sulla fixture committata

---

## Come leggere questo documento

Due cose distinte, che la versione precedente di questo file confondeva:

- I **test** girano solo su `tests/fixtures/costs_sample/`, che è committata e
  immutabile. Non toccano `/home/ubuntu/hl-data`, che un collector vivo sta
  riscrivendo mentre i test girano. Sono deterministici: stesso risultato oggi,
  fra un mese, su un'altra macchina.
- Il **report** gira sui dati di produzione, quattro coin, dieci giorni. È lì
  che stanno i numeri qui sotto. Non è riproducibile per costruzione — la
  finestra si sposta ogni ora — e per questo nessun test ci si ancora.

---

## Discrepanza 1 — Round-trip conta una sola esecuzione

**Esito: il codice era già corretto, il vecchio output aveva un'etichetta
fuorviante.**

Il round-trip include entry + exit. Su BTC, notional 100 $:

- taker: 4,5 bps × 2 esecuzioni = 9 bps, più lo spread attraversato due volte
  (~0,156 bps) → **0,0916 %**
- maker: 1,5 bps × 2 = 3 bps esatti → **0,0300 %**

Il vecchio "RT taker 0,0450 %" era la fee di *una* esecuzione, stampata sotto
un'etichetta che diceva round-trip.

Il test permanente `test_round_trip_somma_di_due_esecuzioni` confronta
`rt.total` con `entry.total + exit.total` calcolati separatamente, contro un
valore scritto a mano (mid 100, mezzo spread 0,05, taker → 0,19 $ = 0,19 %).

---

## Discrepanza 2 — Funding contraddice il catalogo (fattore ~4x)

**Esito: la discrepanza non esiste più. `costs/` e `catalog/` danno lo stesso
double, bit per bit.**

Sulla fixture, con la stessa finestra e gli stessi dati:

| Coin | costs/ | catalog/ | differenza |
|------|--------|----------|-----------|
| BTC  | 0,15284673999999984 % | 0,15284673999999984 % | 0,000e+00 |
| HYPE | 0,20677241999999943 % | 0,20677241999999943 % | 0,000e+00 |

Il test permanente `test_funding_costs_e_catalog_coincidono` blocca questa
uguaglianza con una tolleranza di **1e-9 punti percentuali**. Non è un margine
di comodo: i due moduli danno lo stesso bit, e 1e-9 lascia spazio solo a una
diversa associatività nella somma di 240 addendi (~1e-17). Un disallineamento
di **un'ora** vale ~6e-4 punti percentuali, seicentomila volte la soglia, e il
meta-test `test_il_confronto_vede_un_ora_di_scarto` dimostra che quella
perturbazione fa davvero fallire il confronto.

La causa della vecchia divergenza non è ricostruibile dai dati attuali:
finestre diverse e una versione precedente del codice. Non ho un modo onesto
di dire quale delle due, e non lo invento.

---

## Discrepanza 3 — Long e short non simmetrici

**Esito: simmetrici, e ora verificato in modo non tautologico.**

Il test che c'era confrontava `long.cost` con `-short.cost`. Quei due numeri
escono dalla **stessa riga di codice** moltiplicata per `Side.sign`:
l'uguaglianza vale per costruzione, anche se il segno fosse invertito, anche se
la somma dei rate fosse sbagliata, anche se metà dei regolamenti fossero andati
persi. Era una tautologia travestita da verifica.

La versione riscritta confronta **ciascun lato separatamente** con un valore
calcolato fuori da `costs/` (la costante in `ATTESO`, ottenuta in SQL puro), e
in più verifica che:

- i due lati abbiano contato gli **stessi** regolamenti (`n_settlements`,
  `n_known`, `n_missing`, `n_provisional`, `first_hour`, `last_hour`) — una
  simmetria ottenuta scartando ore su un lato solo sarebbe simmetrica e falsa;
- `long.cost + short.cost == 0.0` **esatto**, non approssimato: il funding è un
  trasferimento, fra i due lati non si crea né si distrugge denaro.

Aggiunto `test_simmetria_anche_con_rate_di_segno_misto`: sul campione il
funding è sempre positivo, quindi la simmetria potrebbe reggere per un motivo
sbagliato (un `abs()` da qualche parte). Con rate +0,02 % / −0,05 % / +0,01 %
su 10.000 $ il long **incassa** 2 $ e lo short paga 2 $, valori scritti a mano
nel test.

---

## Cosa è cambiato nei test, e perché

I test fallivano (24 su 99) per una ragione sola: **il modello è migliorato e i
test sono rimasti indietro.**

`costs/funding.py` ha introdotto la nozione di rate **provvisorio**. L'ultimo
regolamento derivabile dai campioni `activeAssetCtx` viene dall'ora più
recente, che può essere ancora in corso mentre si legge: il suo "ultimo
campione" non è ancora l'ultimo, e due letture a un minuto di distanza danno
due numeri diversi. Quel rate non entra in nessuna somma.

Le conseguenze, tutte corrette e tutte non recepite dai test:

| Test | Perché falliva | Correzione |
|------|----------------|-----------|
| `TestDieciGiorniSuiDatiReali` (4 test) | `ATTESO` e `FINESTRA_NS` erano stati calcolati includendo il regolamento provvisorio → `n_known=239` invece di 240 | Costanti ricalcolate in SQL puro con la regola del provvisorio scritta esplicitamente nella query |
| `TestAllineamento.test_traslazione_di_un_ora` | Con un solo campione, l'unico regolamento derivato è provvisorio e `rate()` restituisce `None` | Due campioni, così la traslazione si osserva su un'ora definitiva. Aggiunti tre test sul comportamento del provvisorio |
| `test_allineamento_sbagliato_di_un_ora` | Sulla finestra vecchia la serie giusta e quella disallineata coincidevano per caso | Con la finestra corretta discriminano: 1,5285 vs 1,5358 (0,48 % di scarto) |
| `test_senza_esclusione_l_ora_bucata_entrerebbe_nel_conto` | Il regolamento provvisorio rendeva `complete` falso da solo, mascherando proprio il fatto che il test doveva mostrare | Finestra spostata a `[H0+1, H0+3)`, che contiene due regolamenti definitivi |
| `test_cento_dollari_non_hanno_impatto` (15 subtest) | Vedi sotto | Riscritto |

### Il test di slippage asseriva un fatto di mercato falso

Il test affermava che un ordine da 100 $ resta **sempre** dentro il primo
livello, su tutte e quattro le coin. Sui dati registrati è falso. Su 320 coppie
(snapshot, lato) della fixture succede **15 volte** che 100 $ debbano
attraversare: 2 su BTC, 3 su ETH, 4 su SOL, 6 su HYPE. La produzione conferma:
`impact_bps_max` per 100 $ è 0,93 (BTC), 1,50 (ETH), 2,37 (SOL), 2,94 (HYPE).

Non descriveva il modello: descriveva una congettura sul mercato, e la
congettura era sbagliata. Ora sono due test distinti:

1. **`test_dentro_il_primo_livello_si_paga_mezzo_spread_e_basta`** — l'invariante
   del modello. Il ramo lo decide la profondità del book, snapshot per
   snapshot: se la size entra nel primo livello, impatto **esattamente** zero e
   slippage **esattamente** il mezzo spread; se lo eccede, impatto > 0 e
   slippage > mezzo spread. Il test verifica anche che **entrambi** i rami
   siano esercitati (320 = 305 dentro + 15 oltre), altrimenti passerebbe
   verificando metà dell'invariante senza che nessuno se ne accorga.
2. **`test_cento_dollari_costano_pochi_bps_su_questo_mercato`** — il fatto di
   mercato, separato. Massimi misurati sulla fixture: 1,556 bps di impatto,
   1,622 bps di costo totale (entrambi su SOL). Soglie a 3 e 5 bps: arrotondate
   sopra i massimi con margine dichiarato, per intercettare un cambio di regime
   di liquidità, non per certificare decimali.

Sistemato anche un `ResourceWarning` preesistente (file non chiuso in
`_fingerprint`).

---

## Report sui dati reali — 4 coin, 10 giorni

Comando eseguito (sola lettura, priorità minima, memoria limitata):

```bash
export XDG_RUNTIME_DIR=/run/user/1001
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1001/bus
systemd-run --user --scope -p MemoryMax=2G nice -n 19 ionice -c 3 \
  /home/ubuntu/hl-trading/.venv/bin/python -m costs \
  --data-dir /home/ubuntu/hl-data/mainnet \
  --out-dir /tmp/hl-reports-pr12 \
  --funding-days 10 --memory-limit 1GB --threads 2
```

205 s totali, ~24.000 snapshot book per coin (uno al minuto).

### Round-trip su 100 $ (mediana sugli snapshot)

| Coin | Taker % | Maker % | Spread mediano (bps) |
|------|---------|---------|---------------------|
| BTC  | 0,0916  | 0,0300  | 0,156 |
| ETH  | 0,0953  | 0,0300  | 0,530 |
| HYPE | 0,0918  | 0,0300  | 0,180 |
| SOL  | 0,0913  | 0,0300  | 0,133 |

Il maker a 0,0300 % è un **limite inferiore**, non una stima: assume esecuzione
certa e nessuna selezione avversa. Nessuna delle due cose è vera.

### Funding cumulato su 10 giorni (notional 100 $)

**Tutte e quattro le coin: 235/240 regolamenti noti, 0 mancanti, 5 in ore
inaffidabili, 0 provvisori. `completa: false`.**

| Coin | Long % | Short % | Annualizzato % |
|------|--------|---------|----------------|
| BTC  | +0,1834 | −0,1834 | +6,69 |
| ETH  | +0,2136 | −0,2136 | +7,80 |
| HYPE | +0,2449 | −0,2449 | +8,94 |
| SOL  | +0,1830 | −0,1830 | +6,68 |

**Questi numeri sottostimano il funding reale di 5 regolamenti su 240.** Le 5
ore escluse cadono in finestre in cui il collector aveva un buco derivato dai
dati: il rate esiste nel campione ma la raccolta di quell'ora non è affidabile,
e per l'invariante 6 non viene sostituita con zero né inclusa. Il flag
`completa: false` è l'unica cosa che distingue questo numero da uno calcolato
su dieci giorni sani, ed è per questo che esiste.

Costo sul capitale a leva (long, BTC): 1x +0,183 %, 2x +0,367 %, 5x +0,917 %.

### Controllo incrociato con la serie REST `fundingHistory`

| Coin | Ore in comune | Diff. max sul rate | Cumulato REST | Scarto (punti) |
|------|---------------|--------------------|---------------|----------------|
| BTC  | 405 | 0,000e+00 | +0,1892 % | −0,00584 |
| ETH  | 405 | 0,000e+00 | +0,2173 % | −0,00368 |
| HYPE | 405 | 0,000e+00 | +0,2499 % | −0,00500 |
| SOL  | 405 | **2,239e-07** | +0,1848 % | −0,00180 |

Lo scarto sul cumulato **non è un disaccordo fra le fonti**: sulle ore in
comune i rate coincidono. È l'effetto delle 5 ore che `costs/` esclude come
inaffidabili e che la serie REST invece copre (239 regolamenti noti contro
235). Le due fonti dicono la stessa cosa sulle stesse ore; contano ore diverse.

**SOL è l'eccezione da guardare.** La differenza massima sul rate è 2,239e-07
invece di zero esatto come sulle altre tre. È piccola (0,0022 bps su un
singolo regolamento) ma non è rumore di virgola mobile su un `double`: è
troppo grande. Non l'ho spiegata. Va guardata prima che qualcuno ci costruisca
sopra.

### Slippage mediano per size (bps sul mid)

| Coin | 100 $ | 500 $ | 2.000 $ | p99 a 2.000 $ | size oltre il book |
|------|-------|-------|---------|---------------|--------------------|
| BTC  | 0,0782 | 0,0782 | 0,0782 | 0,7526 | 4 |
| ETH  | 0,2650 | 0,2651 | 0,2651 | 1,2673 | 0 |
| HYPE | 0,0904 | 0,0907 | 0,0916 | 1,9549 | 5 |
| SOL  | 0,0663 | 0,0664 | 0,0664 | 1,4412 | 12 |

La mediana è piatta perché a queste size l'impatto mediano è zero: si paga il
mezzo spread. La coda no — il p99 è da 10 a 20 volte la mediana. Su SOL 12
snapshot su ~24.000 non hanno abbastanza profondità nei 10 livelli registrati
per assorbire 2.000 $, e vengono dichiarati insufficienti invece di produrre un
prezzo estrapolato.

---

## Assunzioni fatte

1. **L'allineamento campione → regolamento è +1 ora.** L'ultimo `ctx.funding`
   osservato durante l'ora H è il rate del regolamento a inizio ora H+1.
   Misurato, non assunto: sulle 405 ore in comune con la serie REST la
   differenza è 0 su BTC, ETH e HYPE (2,239e-07 su SOL).
2. **Notional costante per tutta la detenzione.** Il funding di ogni ora si
   applica alla stessa dimensione. Sottostima il costo di una posizione in
   guadagno, lo sovrastima per una in perdita.
3. **Un campione book al minuto** (`--sample-every-s 60`). Su una mediana non
   sposta nulla; su un p99 potrebbe, perché gli allargamenti di spread sono
   brevi e un campionamento rado ne perde una parte.
4. **Fee al tier base** (maker 1,5 bps, taker 4,5 bps), nessuno sconto volume,
   nessun rebate.
5. **Il book registrato ha 10 livelli.** Oltre quelli il modello dichiara
   insufficienza invece di estrapolare (invariante 5).
6. **La fixture è rappresentativa della forma dei dati, non del mercato.** 40
   snapshot per coin bastano a verificare che il modello legga book reali; non
   bastano a dire nulla sulla liquidità.

## Cosa potrebbe essere sbagliato

- **I 2,239e-07 su SOL non sono spiegati.** È l'unica cosa in questo report che
  non torna. Finché non si sa da dove viene, non si può escludere che sia il
  sintomo di qualcosa di più grande su una coin sola.
- **Le 5 ore inaffidabili su 240 sono il 2 % della finestra.** Il funding vero
  su questi dieci giorni è più alto di quello riportato, di una quantità che
  non conosco. Se le ore escluse fossero sistematicamente quelle di alta
  volatilità (disconnessioni e movimenti bruschi correlano), la sottostima
  sarebbe distorta, non solo rumorosa. Non l'ho verificato.
- **Il maker a 0,0300 % non è raggiungibile.** Un ordine limite che riposa sul
  book viene eseguito quando il mercato gli va contro. Il costo vero del maker
  sta fra 0,03 % e il taker, e questo modello non sa dove.
- **Le soglie 3 e 5 bps nel test di slippage sono osservazioni, non teoremi.**
  Sono tarate sopra i massimi misurati sulla fixture. Un cambio di regime le
  farebbe scattare, ed è il loro scopo — ma il numero esatto non ha
  giustificazione teorica.
- **`p99` su ~24.000 snapshot** significa che la coda è descritta da ~240
  osservazioni per coin. È poco per un p99 stabile.
- **La causa della discrepanza originale sul funding (fattore 4x) resta
  ignota.** Il codice attuale è coerente e i test lo bloccano, ma non ho
  ricostruito cosa producesse quei numeri. Se fosse un bug ancora presente in
  un percorso che non ho esercitato, questi test non lo vedrebbero.

---

## Comandi di verifica

```bash
# Tutta la suite (270 test, sulla sola fixture)
/home/ubuntu/hl-trading/.venv/bin/python -m unittest \
  $(ls tests/test_*.py | sed 's|/|.|;s|\.py$||')

# Solo i test di costs (99)
/home/ubuntu/hl-trading/.venv/bin/python -m unittest \
  tests.test_costs_funding tests.test_costs_fees tests.test_costs_slippage \
  tests.test_costs_sources tests.test_costs_leverage

# Rigenerare la fixture (sola lettura sulla produzione)
/home/ubuntu/hl-trading/.venv/bin/python tests/fixtures/make_costs_sample.py \
  --data-dir /home/ubuntu/hl-data/mainnet
```
