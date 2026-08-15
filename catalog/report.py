"""Resa testuale del report.

Separato dal calcolo di proposito: i moduli di analisi ritornano numeri, qui
si decide solo come si leggono. Se domani il report diventa HTML o finisce in
una dashboard, nessuna query cambia.

Regola di stampa: le tabelle per ora NON si stampano tutte (sono migliaia di
righe). Si stampano i totali e le sole ore marcate, troncate con un conteggio
esplicito di quante ne restano. Il parquet contiene tutto: il report e' un
riassunto che deve stare in uno schermo, non l'archivio.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .sanity import COHERENCE_ANOMALY_BPS, QUANTILES

RULE = "=" * 78
THIN = "-" * 78


def _f(v, nd: int = 2, na: str = "-") -> str:
    if v is None:
        return na
    if isinstance(v, bool):
        return "si" if v else "no"
    if isinstance(v, float):
        if v != v:  # NaN
            return na
        return f"{v:,.{nd}f}"
    if isinstance(v, int):
        return f"{v:,}"
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M")
    return str(v)


def table(rows: list[dict], columns: list[tuple[str, str, int]]) -> list[str]:
    """`columns` = (chiave, intestazione, decimali). Larghezza automatica."""
    if not rows:
        return ["  (nessun dato)"]
    cells = [[_f(r.get(k), nd) for k, _, nd in columns] for r in rows]
    heads = [h for _, h, _ in columns]
    widths = [max(len(h), *(len(c[i]) for c in cells)) for i, h in enumerate(heads)]
    out = ["  " + "  ".join(h.rjust(w) for h, w in zip(heads, widths))]
    out.append("  " + "  ".join("-" * w for w in widths))
    out += ["  " + "  ".join(c.rjust(w) for c, w in zip(row, widths)) for row in cells]
    return out


def quantile_table(rows: list[dict], key: str, label: str, nd: int = 3) -> list[str]:
    """Distribuzione: una riga per coin/canale, una colonna per percentile."""
    heads = [f"p{q*100:g}" for q in QUANTILES]
    out = [f"  {label}"]
    if not rows:
        return out + ["  (nessun dato)"]
    name_w = max(len(f"{r.get('channel','')}/{r.get('coin','')}".strip("/")) for r in rows)
    out.append("  " + " " * name_w + "  " + "  ".join(h.rjust(10) for h in heads))
    for r in rows:
        name = f"{r.get('channel','')}/{r.get('coin','')}".strip("/")
        q = r.get(key) or []
        vals = "  ".join(_f(v, nd).rjust(10) for v in q)
        out.append(f"  {name.ljust(name_w)}  {vals}")
    return out


def section(title: str) -> list[str]:
    return ["", RULE, title, RULE]


def build(res: dict) -> list[str]:
    L: list[str] = []
    m = res["meta"]

    L += [RULE, "CATALOGO E INTEGRITA' DEI DATI", RULE]
    L += [
        f"data_dir       : {m['data_dir']}",
        f"out_dir        : {m['out_dir']}",
        f"eseguito il    : {m['run_utc']} UTC",
        f"partizioni     : {m['n_partitions']} (canale/coin)",
        f"righe totali   : {_f(m['n_rows'])}",
        f"finestra dati  : {m['first_utc']} -> {m['last_utc']} UTC "
        f"({_f(m['span_days'], 2)} giorni)",
        f"durata scan    : {_f(m['elapsed_s'], 1)} s",
    ]

    # ------------------------------------------------------------------ 1
    L += section("1. COPERTURA")
    L += [
        "Le righe, non i file: il writer flusha a tempo, quindi il numero di",
        "file misura la cadenza del flush e non il volume dei dati.",
        "",
    ]
    L += table(
        res["coverage"],
        [("channel", "canale", 0), ("coin", "coin", 0), ("n_rows", "righe", 0),
         ("first_utc", "prima riga", 0), ("last_utc", "ultima riga", 0),
         ("n_hours", "ore", 0), ("n_hours_zero", "ore_vuote", 0),
         ("rows_per_hour_median", "righe/ora_med", 1),
         ("rows_per_hour_mean", "righe/ora_avg", 1)],
    )

    L += ["", "Cadenza fra messaggi consecutivi (secondi):"]
    L += quantile_table(res["interarrival"], "q", "percentili del delta di arrivo", 3)
    L += table(
        res["interarrival"],
        [("channel", "canale", 0), ("coin", "coin", 0), ("mean_s", "media_s", 3),
         ("max_s", "pausa_max_s", 1)],
    )

    # ------------------------------------------------------------------ 2
    L += section("2. FINESTRE INAFFIDABILI")
    g = res["gaps"]
    L += [
        f"Registro buchi : {g['path']}"
        + ("" if g["exists"] else "   [ASSENTE]"),
        f"finestre       : {g['n']} (di cui ancora aperte: {g['n_open']})",
        f"tempo scollegato totale: {_f(g['total_s'], 1)} s "
        f"({_f(g['total_s'] / 3600.0, 3)} ore) su {_f(m['span_days'] * 24, 1)} ore di dati",
        f"finestra piu' lunga    : {_f(g['max_s'], 1)} s",
    ]
    audit = g.get("audit") or {}
    if audit:
        eventi = ", ".join(
            f"{k}={v}" for k, v in (audit.get("eventi_con_durata") or {}).items()
        )
        L += [
            "",
            "Lettura del registro (contratto: TUTTI i record che dichiarano una",
            "durata, qualunque sia `event` — un filtro su event=='close' salta la",
            "riga `manual` del blackout piu' lungo mai registrato):",
            f"  record con durata trovati : {_f(audit.get('record_con_durata'))}"
            + (f"   [{eventi}]" if eventi else ""),
            f"  gia' coperti da finestre  : "
            f"{_f((audit.get('record_con_durata') or 0) - (audit.get('recuperati') or 0))}",
            f"  recuperati a parte        : {_f(audit.get('recuperati'))}"
            + ("" if not audit.get("eventi_recuperati") else
               "   [" + ", ".join(f"{k}={v}" for k, v in
                                  audit["eventi_recuperati"].items()) + "]"),
        ]
    if g.get("longest"):
        L += ["", "Interruzioni piu' lunghe:"]
        L += table(
            g["longest"],
            [("start_utc", "inizio UTC", 0), ("duration_s", "durata_s", 1),
             ("event", "evento", 0), ("origin", "origine", 0),
             ("reason", "motivo", 0)],
        )
    L += [
        "",
        "Le due marcature sono separate e non vanno fuse:",
        "  gap_overlap = l'ora interseca una disconnessione REGISTRATA dal collector",
        "  low_volume  = le righe dell'ora sono sotto il 90% della mediana oraria",
        "                di quella coppia canale/coin (mediana calcolata sulle ore",
        "                non-bordo e senza gap). Alzato SOLO sui canali a cadenza",
        "                fissa: " + (", ".join(res.get("fixed_rate_channels") or [])
                                     or "(nessuno)"),
        "  below_median= stessa statistica, calcolata su tutti i canali ma senza",
        "                marcare: su trades e candle il volume orario segue",
        "                l'attivita' del mercato, non la salute della raccolta",
        "Il catalogo marca. Non esclude e non cancella niente.",
        "",
    ]
    if g["by_reason"]:
        L += ["Motivi registrati:"]
        L += table(g["by_reason"], [("reason", "motivo", 0), ("n", "finestre", 0),
                                    ("total_s", "secondi_tot", 1),
                                    ("max_s", "max_s", 1)])
    fc = res["flag_counts"]
    L += [
        "",
        f"Ore di stream totali          : {_f(fc['ore_totali_stream'])}",
        f"  con gap_overlap             : {_f(fc['gap_overlap'])}",
        f"  con low_volume              : {_f(fc['low_volume'])}",
        f"  con entrambi                : {_f(fc['entrambi'])}",
        f"  solo low_volume (nessun gap): {_f(fc['solo_low_volume'])}",
        f"    di cui non ore di bordo   : {_f(fc['solo_low_volume_non_edge'])}",
        f"  senza nemmeno una riga      : {_f(fc['ore_vuote'])}",
        f"Ore su canali a cadenza fissa : {_f(fc.get('ore_cadenza_fissa'))}"
        "   (le sole valutabili con low_volume)",
        f"  sotto mediana ma NON marcate: {_f(fc.get('sotto_mediana_non_valutate'))}"
        "   (canali guidati dall'attivita')",
        "",
        "Ore marcate (le prime per timestamp):",
    ]
    L += table(
        res["flagged"],
        [("hour_utc", "ora UTC", 0), ("channel", "canale", 0), ("coin", "coin", 0),
         ("n_rows", "righe", 0), ("median_rows", "mediana", 1),
         ("rows_ratio", "rapporto", 3), ("gap_seconds", "gap_s", 1),
         ("gap_overlap", "gap", 0), ("low_volume", "low_vol", 0),
         ("is_edge_hour", "bordo", 0), ("is_fixed_rate", "cad_fissa", 0)],
    )
    if res["flagged_truncated"] > 0:
        L += [f"  ... e altre {_f(res['flagged_truncated'])} ore marcate "
              f"(tutte nel parquet)"]

    # ------------------------------------------------------------------ 3
    L += section("3. CONTROLLI DI SANITA'")

    L += ["3.1 Duplicati", ""]
    L += table(
        res["duplicates"],
        [("channel", "canale", 0), ("coin", "coin", 0), ("n_rows", "righe", 0),
         ("n_exact_dup", "righe_identiche", 0),
         ("n_payload_dup", "payload_ripetuti", 0),
         ("n_dup_ts_local", "ts_local_ripetuti", 0),
         ("n_dup_ts_exch", "ts_exch_ripetuti", 0)],
    )
    L += [
        "",
        "  righe_identiche  = stessa terna (ts_local, ts_exch, payload). Non",
        "                     dovrebbe mai esistere.",
        "  payload_ripetuti = stesso payload a istanti diversi. Atteso dove",
        "                     l'exchange ripubblica uno stato invariato.",
        "  ts_exch_ripetuti = atteso su candle (la barra viene ripubblicata a",
        "                     ogni tick con `t` fisso) e su trades (piu' trade",
        "                     nello stesso blocco condividono `time`).",
    ]
    td = res["trade_dup"]
    if td:
        tot = sum(r["n_trades"] for r in td) or 1
        dup = sum(r["n_dup_tid"] for r in td)
        conflitti = sum(r["n_tid_contraddittori"] for r in td)
        L += ["", "  Duplicati di trade per `tid` (identificatore di esecuzione):"]
        L += table(td, [("coin", "coin", 0), ("n_trades", "consegnati", 0),
                        ("n_distinct_tid", "tid_distinti", 0),
                        ("n_dup_tid", "consegne_extra", 0),
                        ("n_tid_ripetuti", "tid_ripetuti", 0),
                        ("n_tid_contraddittori", "tid_incoerenti", 0)])
        L += [
            "",
            f"  Totale: {_f(dup)} consegne in eccesso su {_f(tot)} "
            f"({_f(100.0 * dup / tot, 3)}%), {_f(conflitti)} tid con contenuto",
            "  diverso fra due consegne.",
            "  Sono ritrasmissioni del server dopo una riconnessione, non un guasto.",
            "  I file NON vengono modificati: la dedup avviene in lettura, in",
            "  `catalog.trades.dedup_sql` (si tiene la prima consegna osservata),",
            "  ed e' da li' che il backtester leggera' i trade.",
            "  `tid_incoerenti` deve essere 0: se non lo e', la dedup sta",
            "  scegliendo fra due verita'.",
        ]

    L += ["", "3.2 Monotonia di ts_local_ns (ordine di scrittura)", ""]
    L += table(
        res["monotonicity"],
        [("channel", "canale", 0), ("coin", "coin", 0), ("n_pairs", "coppie", 0),
         ("n_decreasing", "passi_indietro", 0), ("n_equal", "ts_uguali", 0),
         ("worst_backstep_ns", "peggior_delta_ns", 0)],
    )
    if res["monotonicity_breaks"]:
        L += ["", "  Rotture peggiori:"]
        L += table(
            res["monotonicity_breaks"],
            [("channel", "canale", 0), ("coin", "coin", 0), ("at_utc", "quando", 0),
             ("delta_ns", "delta_ns", 0), ("rn", "riga", 0), ("filename", "file", 0)],
        )
    else:
        L += ["", "  Nessun passo indietro: l'ordine di scrittura e' crescente ovunque."]

    L += ["", "3.3 ts_exch_ms assente (valore 0)", ""]
    L += table(
        res["exch_ts_zero"],
        [("channel", "canale", 0), ("coin", "coin", 0), ("n_rows", "righe", 0),
         ("n_zero", "con_zero", 0), ("pct_zero", "%", 2),
         ("n_set", "valorizzate", 0), ("n_distinct_set", "valori_distinti", 0)],
    )
    L += [
        "",
        "  Riportato, non corretto: allMids e activeAssetCtx non contengono",
        "  nessun timestamp nel payload, quindi 0 e' il valore corretto e per",
        "  quei canali l'unico riferimento temporale e' ts_local_ns.",
    ]

    L += ["", "3.4 Latenza exchange -> host, in millisecondi", ""]
    L += quantile_table(res["latency"], "q", "percentili di (ts_local_ms - ts_exch_ms)", 1)
    L += [""]
    L += table(
        res["latency"],
        [("channel", "canale", 0), ("coin", "coin", 0), ("n", "righe", 0),
         ("mean_ms", "media", 1), ("min_ms", "min", 1), ("max_ms", "max", 1),
         ("n_negative", "negative", 0)],
    )
    L += [
        "",
        "  Solo l2Book e trades misurano un ritardo di rete. Su candle il",
        "  timestamp e' l'apertura della barra, quindi il valore cresce fino a",
        "  60 s dentro il minuto ed e' tempo trascorso nella barra, non latenza.",
        "  Su backfill_* e' il tempo della richiesta REST.",
        "  Valori negativi = orologio locale indietro rispetto all'exchange.",
    ]

    L += ["", "3.5 Coerenza prezzi: mid da l2Book contro allMids (bps)", ""]
    L += quantile_table(res["coherence"], "q", "percentili dello scostamento in bps", 3)
    L += [""]
    L += table(
        res["coherence"],
        [("coin", "coin", 0), ("n", "confronti", 0), ("mean_bps", "media_bps", 4),
         ("max_abs_bps", "max_abs_bps", 2), ("n_gt_5bps", ">5bps", 0),
         ("n_gt_10bps", ">10bps", 0), ("n_gt_50bps", ">50bps", 0),
         ("median_lag_s", "lag_med_s", 3)],
    )
    if res["coherence_worst"]:
        L += ["", f"  Scostamenti oltre {COHERENCE_ANOMALY_BPS:g} bps, i peggiori:"]
        L += table(
            res["coherence_worst"],
            [("coin", "coin", 0), ("at_utc", "quando", 0), ("book_mid", "mid_book", 4),
             ("allmids_mid", "mid_allMids", 4), ("dev_bps", "dev_bps", 2),
             ("lag_s", "lag_s", 3)],
        )
    if res["allmids_missing"]:
        L += ["", "  Presenza delle coin dentro allMids:"]
        L += table(
            res["allmids_missing"],
            [("coin", "coin", 0), ("n_allmids_rows", "righe_allMids", 0),
             ("n_present", "presente", 0), ("n_missing", "assente", 0)],
        )

    L += ["", "3.6 Partizionamento su disco", ""]
    L += table(
        res["partition_drift"],
        [("channel", "canale", 0), ("coin", "coin", 0), ("n_rows", "righe", 0),
         ("n_hour_drift", "ora_dir_diversa", 0),
         ("n_coin_mismatch", "coin_incoerente", 0),
         ("n_channel_mismatch", "canale_incoerente", 0)],
    )
    L += [
        "",
        "  ora_dir_diversa e' atteso e documentato nel writer (il batch eredita",
        "  l'ora del primo record): e' la prova che le ore vanno ricavate da",
        "  ts_local_ns, come fa questo catalogo. coin_incoerente e",
        "  canale_incoerente devono essere zero.",
    ]

    # ------------------------------------------------------------------ 4
    L += section("4. SPREAD (da l2Book)")
    L += quantile_table(res["spread"], "spread_bps_q", "spread in bps", 3)
    L += [""]
    L += quantile_table(res["spread"], "spread_abs_q", "spread assoluto (unita' di prezzo)", 5)
    L += [""]
    L += table(
        res["spread"],
        [("coin", "coin", 0), ("n", "snapshot", 0), ("spread_bps_mean", "media_bps", 3),
         ("spread_bps_max", "max_bps", 2), ("n_crossed", "book_incrociati", 0),
         ("n_locked", "book_bloccati", 0), ("n_no_bid", "senza_bid", 0),
         ("n_no_ask", "senza_ask", 0), ("px_min", "mid_min", 4),
         ("px_max", "mid_max", 4)],
    )
    L += [
        "",
        "  Un ordine market paga almeno mezzo spread. Questi percentili sono il",
        "  pavimento del costo di esecuzione, prima delle fee.",
    ]

    # ------------------------------------------------------------------ 5
    L += section("5. FUNDING (da activeAssetCtx)")
    L += [
        "Serie oraria: un valore per ora, l'ultimo campione osservato prima del",
        "confine dell'ora. Sommare tutti i campioni conterebbe lo stesso funding",
        "migliaia di volte (activeAssetCtx arriva circa una volta al secondo).",
        "",
    ]
    L += table(
        res["funding_stats"],
        [("coin", "coin", 0), ("n_hours", "ore", 0), ("mean", "media_oraria", 9),
         ("median", "mediana", 9), ("stddev", "dev_std", 9), ("min", "min", 9),
         ("max", "max", 9), ("n_pos", "ore_pos", 0), ("n_neg", "ore_neg", 0),
         ("n_zero", "ore_zero", 0), ("n_sign_changes", "cambi_segno", 0),
         ("mean_annual_pct", "media_ann_%", 3)],
    )
    L += [""]
    L += quantile_table(res["funding_stats"], "q", "percentili del rate orario", 9)

    fd = res["funding_days"]
    L += [
        "",
        f"5.1 Funding cumulato di un LONG tenuto {fd} giorni, sui dati reali",
        "",
        "Segno: funding positivo = i long pagano. Un numero positivo qui e'",
        "denaro uscito, in percentuale del notional. Notional costante.",
        "",
    ]
    L += table(
        res["funding_cum"],
        [("coin", "coin", 0), ("from_hour", "da", 0), ("to_hour", "a", 0),
         ("hours_observed", "ore_osservate", 0), ("hours_missing", "ore_mancanti", 0),
         ("cum_funding_pct", "costo_long_%", 4),
         ("worst_hour_pct", "ora_peggiore_%", 5),
         ("best_hour_pct", "ora_migliore_%", 5)],
    )

    rf = res["rest_funding_meta"]
    L += [
        "",
        "5.2 Verifica incrociata contro la serie REST (backfill_funding)",
        "",
        f"  regolamenti distinti nel dump : {_f(rf['n_settlements'])}",
        f"  righe prima della dedup       : {_f(rf['n_rows_before_dedup'])}",
        f"  istanti con rate contraddittori: {_f(rf['n_contradictory'])}",
        f"  regolamenti non sull'ora esatta: {_f(rf['n_off_hour'])}",
        "",
    ]
    L += table(
        res["funding_cross"],
        [("coin", "coin", 0), ("lag_h", "sfasamento_h", 0), ("n", "ore", 0),
         ("mean_abs_diff", "err_medio_ass", 10), ("max_abs_diff", "err_max", 10),
         ("corr", "correlazione", 4)],
    )
    L += ["", f"  Stesso cumulato calcolato dalla serie REST ({fd} giorni):", ""]
    L += table(
        res["funding_cum_rest"],
        [("coin", "coin", 0), ("from_hour", "da", 0), ("to_hour", "a", 0),
         ("hours_observed", "ore", 0), ("cum_funding_pct", "costo_long_%", 4)],
    )

    L += ["", RULE, "FINE REPORT", RULE, ""]
    return L


def now_utc_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
