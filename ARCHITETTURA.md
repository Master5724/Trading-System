# ARCHITETTURA.md

Da leggere insieme a CLAUDE.md. Definisce *dove gira cosa*. È il documento che
evita l'errore più costoso di questo progetto: mettere logica di trading in un
posto che non può eseguirla.

## Il vincolo che decide tutto: Vercel non può ospitare il bot

Vercel esegue funzioni serverless: si svegliano su richiesta HTTP, hanno un
timeout, e muoiono. Non esiste un processo che resta acceso.

Il bot ha bisogno esattamente delle cose che Vercel non offre:

| serve al bot | Vercel |
|---|---|
| WebSocket aperto per giorni | no, la funzione muore |
| stato in memoria fra i tick | no, ogni invocazione è isolata |
| chiave privata in un processo persistente | no, ambiente effimero e condiviso |
| loop 24/7 | no, solo cron a intervalli minimi |

Un cron serverless che "controlla il mercato ogni 5 minuti" sembra una
scorciatoia accettabile. Non lo è: perde lo stream dei trade, non può gestire
ordini limit in coda, e riapre da zero la connessione ogni volta. Se hai una
posizione aperta e la funzione fallisce, nessuno se ne accorge.

**Vercel ospita la dashboard. Punto.**

## Tre livelli

```
┌─────────────────────────────────────────────┐
│ VPS (Linux, systemd, 24/7)                  │
│                                             │
│   collector    WebSocket -> parquet locale  │
│   runner       loop veloce: segnale->ordine │
│   risk         limiti + kill switch         │
│   ai-worker    layer lento, asincrono       │
│                                             │
│   chiavi: API wallet, solo in env           │
└──────────────────┬──────────────────────────┘
                   │ scrive (service key)
                   v
┌─────────────────────────────────────────────┐
│ Supabase (Postgres)                         │
│                                             │
│   equity_snapshots   positions   orders     │
│   fills   signals   risk_events   logs      │
│                                             │
│   RLS attiva. La dashboard legge e basta.   │
└──────────────────┬──────────────────────────┘
                   │ legge (anon key + RLS)
                   v
┌─────────────────────────────────────────────┐
│ Vercel (Next.js)                            │
│                                             │
│   dashboard read-only, single-user          │
│   auth Supabase, whitelist di 1 solo utente │
└─────────────────────────────────────────────┘
```

Il flusso dei dati è **a senso unico**: VPS → Supabase → dashboard.

## Regole sul confine

1. **La dashboard è read-only.** Nessun pulsante che apre o chiude posizioni.
   Un endpoint di scrittura esposto su internet è la superficie d'attacco più
   grande del sistema, e non serve a niente che non si possa fare via SSH.
   L'unica eccezione ammissibile, e solo dopo che tutto il resto è stabile, è
   un kill switch: scrive un flag su una tabella, il VPS lo legge e si ferma.
   Mai il contrario.
2. **Il VPS non espone porte in ascolto.** Non c'è un'API del bot. Scrive su
   Supabase in uscita, e basta.
3. **Chiavi separate.** Il VPS usa la service key (scrittura). La dashboard usa
   la anon key con RLS. La service key non entra mai nel bundle Next.js.
4. **Se Supabase è irraggiungibile, il bot continua.** La telemetria non è mai
   nel percorso critico: si accoda su disco e si riallinea dopo. Un bot che si
   ferma perché non riesce a loggare è un bug grave.
5. **Nessuna decisione di trading legge da Supabase.** Supabase è telemetria.
   Lo stato operativo del bot vive sul VPS.

## Auth single-user

Supabase Auth, un solo account, e un controllo esplicito sull'ID utente lato
server. Non basta "solo io ho la password": va scritta una policy RLS che
consente la lettura a quell'unico ID e nega tutto il resto. Se il login è
aperto, prima o poi qualcuno si registra.

## Cosa mostra la dashboard

Non un terminale di trading. Uno strumento per rispondere a una domanda:
*il sistema sta facendo quello che credo?*

- equity curve, paper e live sovrapposte
- posizioni aperte con distanza dal prezzo di liquidazione
- ultimi ordini e fill, con fee e funding pagati **separati** dal PnL lordo
- funding cumulato per posizione aperta
- stato di salute: uptime del collector, ultima finestra di disconnessione,
  età dell'ultimo dato, stato del kill switch
- log dei segnali rifiutati dal risk layer, con il motivo

L'ultima voce è quella che userai di più. È dove si vede se il sistema sta
provando a fare qualcosa di stupido.

## Costi ricorrenti, da mettere in conto

VPS, Supabase, dominio, e — se il layer AI gira in continuo — le chiamate API
al modello. Vanno misurati e confrontati con il capitale impiegato **prima**
di andare live, non dopo. Su capitale piccolo, questa somma è normalmente il
costo dominante dell'intero sistema, più delle commissioni di trading.

Metti un contatore dei costi nella dashboard fin dal primo giorno.
