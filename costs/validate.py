"""Il modello di slippage messo contro le esecuzioni realmente avvenute.

Il resto di `costs/` e' verificabile sulla carta: la fee e' una moltiplicazione,
il funding e' una somma, il cammino sul book e' aritmetica su un dato
registrato. Nessuno di quei test dice se il modello descrive il mercato — dice
solo che il codice fa quello che il codice dice.

Qui si fa l'unica verifica che guarda fuori: per ogni trade realmente eseguito
si prende l'ultimo snapshot del book STRETTAMENTE precedente e si chiede al
modello a che prezzo avrebbe eseguito quella size su quel lato. Poi si
confronta col prezzo a cui il trade e' avvenuto davvero.

**Perche' un disaccordo non e' automaticamente un errore del modello.** Fra lo
snapshot e il trade passa del tempo — `l2Book` arriva circa ogni mezzo secondo
— e in quel tempo il book cambia. La differenza misurata contiene quindi due
cose sommate: l'errore del modello e il movimento del mercato. Per questo il
risultato riporta anche l'eta' del book usato: se l'errore mediano e' dello
stesso ordine del movimento tipico del mid in quell'intervallo, la misura non
sta dicendo niente sul modello. E' un limite del metodo, non si aggira con piu'
dati, e la conclusione onesta e' che questa verifica ESCLUDE errori grossolani
(lato invertito, livelli letti al contrario, size in unita' sbagliate) senza
poter confermare la precisione fine.

**Convenzione `side` dei trade, misurata e non assunta.** Sul canale `trades`
di Hyperliquid `side` e' il lato dell'AGGRESSORE: `B` = ha comprato colpendo
gli ask, `A` = ha venduto colpendo i bid. Misurato su un'ora di BTC
(2026-08-14): il 90% dei trade `B` ha prezzo >= best ask precedente e il 92%
dei trade `A` ha prezzo <= best bid precedente. Il residuo e' proprio l'effetto
dell'eta' del book descritto sopra. Il report ricalcola questa frazione: se un
giorno scendesse verso il 50%, la convenzione sarebbe cambiata e ogni costo di
esecuzione avrebbe il segno sbagliato.
"""

from __future__ import annotations

import json
from statistics import median

from catalog import dataset, trades as catalog_trades

from .side import Side
from .slippage import L2Book


def compare_sample(con, data_dir: str, coin: str, date: str,
                   hour: str = "*", max_trades: int = 20000) -> dict:
    """Confronta modello ed esecuzioni reali su una finestra circoscritta.

    Circoscritta per costo: il join asof e il cammino sul book in Python su
    diciassette giorni di trade sarebbero ore di CPU su una macchina che deve
    lasciare respirare il collector. Una giornata per coin basta a intercettare
    un errore di segno o di unita', che e' cio' che questa verifica puo'
    davvero trovare.

    **`max_trades` e' un tetto che spesso morde, e va letto sapendolo.** Su BTC
    un giorno vale ~140.000 trade contro un tetto di 20.000: il campione sono i
    PRIMI ventimila della giornata in ordine di arrivo, cioe' le prime ore, non
    una giornata intera ne' un campione sparso su tutte le ore. Per un errore
    di segno o di unita' e' indifferente; per una mediana di slippage no,
    perche' spread e profondita' cambiano con l'ora.

    Il risultato riporta quindi `cap_trades` e **`ore_coperte`**, e non un
    booleano "tetto raggiunto": il conteggio delle righe che escono dal join
    non coincide col tetto (l'ASOF join scarta i trade senza uno snapshot
    precedente), quindi un booleano dedotto da li' direbbe "no" anche quando il
    tetto ha morso. Le ore coperte sono il numero che risponde alla domanda
    vera: su BTC il 2026-08-16 il campione copre **8,94 ore su 24**, e una
    mediana su un terzo di giornata va letta come tale.

    Il join e' `t.ts_local_ns > b.ts_local_ns`, disuguaglianza STRETTA: usare
    il book contemporaneo o successivo al trade sarebbe look-ahead, cioe'
    esattamente l'errore che rende un backtest bello e falso.
    """
    trades_sub = catalog_trades.dedup_sql(data_dir, coin)
    # Il giorno si seleziona restringendo il GLOB, non con un WHERE: i file
    # esclusi non vengono nemmeno aperti, e su questa macchina la differenza
    # fra leggere un giorno e diciassette e' l'unica cosa che rende questa
    # verifica eseguibile mentre il collector lavora.
    book_glob = dataset.glob_partition(data_dir, "l2Book", coin)
    day_books = dataset.read(
        book_glob.replace("date=*", f"date={date}").replace("hour=*", f"hour={hour}")
    )
    day_trades_where = f"WHERE strftime(epoch_ms(time_ms), '%Y-%m-%d') = {dataset.sql_str(date)}"

    rows = con.execute(
        f"""
        SELECT t.side, t.px, t.sz, t.ts_local_ns, b.ts_local_ns, b.raw
        FROM (SELECT side, px, sz, ts_local_ns FROM {trades_sub} {day_trades_where}
              ORDER BY ts_local_ns LIMIT {int(max_trades)}) t
        ASOF JOIN (SELECT ts_local_ns, raw FROM {day_books}) b
          ON t.ts_local_ns > b.ts_local_ns
        """
    ).fetchall()

    n = 0
    n_side_coherent = 0
    n_insufficient = 0
    err_bps: list[float] = []
    ts: list[int] = []
    real_bps: list[float] = []
    pred_bps: list[float] = []
    age_ms: list[float] = []
    for side_raw, px, sz, t_ns, b_ns, raw in rows:
        ts.append(t_ns)
        side = Side.BUY if side_raw == "B" else Side.SELL
        book = L2Book.try_from_payload(json.loads(raw), b_ns)
        if book is None:
            continue
        n += 1
        age_ms.append((t_ns - b_ns) / 1e6)
        # Coerenza della convenzione: un aggressore che compra paga almeno il
        # miglior ask che vedeva un istante prima.
        if (side is Side.BUY and px >= book.best_ask) or (
            side is Side.SELL and px <= book.best_bid
        ):
            n_side_coherent += 1
        fill = book.walk(side, sz)
        if not fill.sufficient:
            n_insufficient += 1
            continue
        realized = 1e4 * side.sign * (px - book.mid) / book.mid
        predicted = fill.slippage_bps
        real_bps.append(realized)
        pred_bps.append(predicted)
        err_bps.append(realized - predicted)

    def med(v: list[float]) -> float | None:
        return median(v) if v else None

    return {
        "coin": coin,
        "date": date,
        "n_trades": n,
        "cap_trades": int(max_trades),
        "ore_coperte": ((max(ts) - min(ts)) / 3.6e12) if ts else None,
        "frac_side_coerente": (n_side_coherent / n) if n else None,
        "n_size_oltre_book": n_insufficient,
        "slippage_reale_bps_p50": med(real_bps),
        "slippage_modello_bps_p50": med(pred_bps),
        "errore_bps_p50": med(err_bps),
        "errore_bps_p90": (sorted(err_bps)[int(0.9 * len(err_bps))]
                           if err_bps else None),
        "eta_book_ms_p50": med(age_ms),
        "eta_book_ms_p99": (sorted(age_ms)[int(0.99 * len(age_ms))]
                            if age_ms else None),
    }
