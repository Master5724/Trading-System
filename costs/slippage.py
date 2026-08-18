"""Slippage derivato dal book registrato: si cammina l2Book, non si stima.

**Il regime che conta qui.** Il capitale di riferimento e' di poche centinaia
di dollari, e il primo livello del book vale in mediana molto di piu': sul
campione registrato ~98.000 $ su BTC, ~1.500 $ su HYPE. Quasi sempre, quindi,
un ordine da 100 $ sta dentro il best e il suo impatto e' esattamente zero:
tutto il costo di esecuzione e' mezzo spread.

**Quasi sempre non e' sempre, ed e' la ragione per cui il book va camminato
davvero.** Sugli stessi snapshot il primo livello scende fino a 10 $ (BTC),
22 $ (HYPE), 30 $ (SOL), 38 $ (ETH): nel 2-8% dei casi un ordine da 100 $ lo
esaurisce e paga fino a ~1,5 bps di impatto, cioe' quanto mezzo spread o piu'.
Un modello che assumesse "size piccola => solo mezzo spread" sarebbe corretto
nel caso tipico e cieco proprio nei momenti in cui il book e' sottile — che
sono quelli in cui si perde denaro. Lo stesso vale, all'opposto, per un modello
tarato sulle size grandi ("0,5 bps costanti", o una radice del notional): nel
regime normale restituirebbe un numero inventato, al rialzo o al ribasso a
seconda della coin.

Da qui la scelta di scomporre il costo in due termini invece di uno:

    slippage_vs_mid  =  half_spread  +  impact

- `half_spread` e' cio' che si paga per attraversare lo spread anche con size
  infinitesima. Non dipende dalla size, dipende dal book.
- `impact` e' cio' che si paga in piu' per aver consumato oltre il primo
  livello. E' **esattamente zero** finche' la size sta dentro il best, e cresce
  con la size.

L'identita' e' algebrica, non approssimata: (avg - mid) = (avg - best) +
(best - mid). Un test la verifica su book reali.

**Cosa NON fa questo modulo.**

- Non estrapola oltre i 10 livelli registrati. Se la size li esaurisce,
  `Fill.sufficient` e' falso e `avg_px` e' `None`: le proprieta' di prezzo
  sollevano `InsufficientDepth` invece di restituire un numero. E' l'unico
  modo per cui un chiamante distratto ottiene un'eccezione e non un costo
  plausibile. Il backtester dovra' saltare quella barra e dichiararlo
  (CLAUDE.md invariante 5).
- Non modella l'impatto permanente ne' il fatto che il book si ricostituisce
  fra un ordine e il successivo. Un solo ordine contro un solo snapshot.
- Non modella la latenza: il book usato e' quello registrato, e fra la
  decisione e l'arrivo dell'ordine il book cambia. Questo modulo restituisce
  il costo contro il book che gli viene dato; scegliere QUALE book (l'ultimo
  strettamente precedente alla decisione, mai il successivo) e' compito del
  backtester ed e' li' che si annida il look-ahead.
"""

from __future__ import annotations

from dataclasses import dataclass

from .side import Side

# Tolleranza relativa nel confronto fra size richiesta e liquidita' consumata.
# Serve perche' `remaining` si accumula per sottrazioni successive: su dieci
# livelli l'errore di rappresentazione arriva a qualche unita' nell'ultimo bit,
# e senza tolleranza un ordine che consuma ESATTAMENTE tutto il book verrebbe
# dichiarato insufficiente una volta su tante.
_REL_EPS = 1e-12


class InsufficientDepth(Exception):
    """La size richiesta eccede i livelli registrati.

    Non e' un errore di programmazione: e' un'informazione sul dato. Chi la
    riceve deve saltare la decisione, non stimarne il prezzo.
    """


class InvalidBook(Exception):
    """Lo snapshot non e' utilizzabile (lato vuoto, book incrociato, size o
    prezzi non positivi). Anche questo e' un dato: va contato, non corretto."""


@dataclass(frozen=True)
class Level:
    px: float
    sz: float

    @property
    def notional(self) -> float:
        return self.px * self.sz


@dataclass(frozen=True)
class L2Book:
    """Snapshot a 10 livelli per lato, come lo registra il collector.

    `bids` e' ordinato per prezzo decrescente, `asks` crescente: e' l'ordine in
    cui l'exchange li manda ed e' quello in cui vanno consumati. Il costruttore
    lo verifica invece di assumerlo, perche' un book ordinato al contrario
    produrrebbe uno slippage negativo — cioe' un guadagno — senza nessun errore.
    """

    coin: str
    ts_local_ns: int
    ts_exch_ms: int
    bids: tuple[Level, ...]
    asks: tuple[Level, ...]

    def __post_init__(self) -> None:
        if not self.bids or not self.asks:
            raise InvalidBook(f"{self.coin}: lato vuoto "
                              f"(bid={len(self.bids)}, ask={len(self.asks)})")
        for name, levels, descending in (("bid", self.bids, True),
                                         ("ask", self.asks, False)):
            prev: float | None = None
            for lv in levels:
                if lv.px <= 0 or lv.sz <= 0:
                    raise InvalidBook(f"{self.coin}: livello {name} non positivo "
                                      f"(px={lv.px}, sz={lv.sz})")
                if prev is not None and ((lv.px >= prev) if descending
                                         else (lv.px <= prev)):
                    raise InvalidBook(f"{self.coin}: livelli {name} non ordinati "
                                      f"({prev} -> {lv.px})")
                prev = lv.px
        if self.asks[0].px < self.bids[0].px:
            raise InvalidBook(f"{self.coin}: book incrociato "
                              f"(bid {self.bids[0].px} > ask {self.asks[0].px})")

    # -- prezzi di riferimento ------------------------------------------------

    @property
    def best_bid(self) -> float:
        return self.bids[0].px

    @property
    def best_ask(self) -> float:
        return self.asks[0].px

    @property
    def mid(self) -> float:
        return (self.bids[0].px + self.asks[0].px) / 2.0

    @property
    def spread(self) -> float:
        return self.asks[0].px - self.bids[0].px

    @property
    def spread_bps(self) -> float:
        return 1e4 * self.spread / self.mid

    @property
    def half_spread_frac(self) -> float:
        """Costo minimo di un taker, in frazione del mid, per size -> 0."""
        return (self.spread / 2.0) / self.mid

    # -- liquidita' disponibile ----------------------------------------------

    def levels(self, side: Side) -> tuple[Level, ...]:
        """I livelli che un ordine di questo lato consuma: chi compra prende
        dagli ask, chi vende dai bid."""
        return self.asks if side is Side.BUY else self.bids

    def best(self, side: Side) -> float:
        return self.levels(side)[0].px

    def depth_size(self, side: Side) -> float:
        return sum(lv.sz for lv in self.levels(side))

    def depth_notional(self, side: Side) -> float:
        """Quanto denaro il book registrato assorbe su questo lato. E' il tetto
        oltre il quale questo modulo si rifiuta di rispondere."""
        return sum(lv.notional for lv in self.levels(side))

    def size_for_notional(self, notional: float) -> float:
        """Size in unita' di base per un notional nominale in valuta di quote.

        **Assunzione dichiarata:** la size si decide al mid osservato PRIMA di
        eseguire. Il notional effettivamente eseguito e' quindi leggermente
        superiore per un acquisto e inferiore per una vendita — la differenza e'
        lo slippage, che e' esattamente cio' che si sta misurando. Dimensionare
        al prezzo di esecuzione sarebbe circolare.
        """
        if notional <= 0:
            raise ValueError(f"notional non positivo: {notional}")
        return notional / self.mid

    # -- il modello ----------------------------------------------------------

    def walk(self, side: Side, size: float) -> Fill:
        """Consuma i livelli finche' la size e' coperta.

        Non solleva se la profondita' non basta: restituisce un `Fill` con
        `sufficient=False`, che porta con se' quanto sarebbe stato eseguito e
        quanto mancava. Chi vuole l'eccezione usa `walk_or_raise`.
        """
        if size <= 0:
            raise ValueError(f"size non positiva: {size}")
        eps = size * _REL_EPS
        remaining = size
        quote = 0.0
        used = 0
        levels = self.levels(side)
        for lv in levels:
            take = lv.sz if lv.sz < remaining else remaining
            quote += take * lv.px
            remaining -= take
            used += 1
            if remaining <= eps:
                remaining = 0.0
                break
        filled = size - remaining
        if remaining == 0.0 and used == 1:
            # Un solo livello consumato: il prezzo medio E' il prezzo di quel
            # livello, esattamente. Ricavarlo da `quote / filled` darebbe lo
            # stesso numero a meno dell'ultimo bit — e quell'ultimo bit
            # renderebbe l'impatto 1e-19 invece di 0, cioe' trasformerebbe la
            # proprieta' "sotto il primo livello non c'e' impatto" da esatta in
            # approssimata. E' il regime in cui opera questo sistema: deve
            # essere esatto proprio li'.
            avg_px: float | None = levels[0].px
        elif remaining == 0.0 and filled > 0:
            avg_px = quote / filled
        else:
            avg_px = None
        return Fill(
            coin=self.coin,
            side=side,
            ts_local_ns=self.ts_local_ns,
            size=size,
            filled_size=filled,
            avg_px=avg_px,
            mid=self.mid,
            best_px=self.best(side),
            half_spread_frac=self.half_spread_frac,
            levels_used=used,
            depth_size=self.depth_size(side),
            depth_notional=self.depth_notional(side),
            sufficient=(remaining == 0.0),
        )

    def walk_or_raise(self, side: Side, size: float) -> Fill:
        f = self.walk(side, size)
        if not f.sufficient:
            raise InsufficientDepth(
                f"{self.coin} {side.name} size={size:.8g}: il book registrato "
                f"copre {f.depth_size:.8g} ({f.depth_notional:,.0f} di notional) "
                f"su {len(self.levels(side))} livelli"
            )
        return f

    # -- costruzione dal payload registrato ----------------------------------

    @classmethod
    def from_payload(cls, payload: dict, ts_local_ns: int,
                     ts_exch_ms: int = 0) -> L2Book:
        """`payload` e' il JSON di un messaggio l2Book cosi' come sta su disco.

        `levels[0]` sono i bid e `levels[1]` gli ask: verificato sui payload
        reali e ri-verificato qui dal controllo di non-incrocio, che fallirebbe
        rumorosamente se un giorno l'ordine si invertisse.
        """
        levels = payload.get("levels") or []
        if len(levels) < 2:
            raise InvalidBook(f"payload senza due lati: {list(payload)}")
        def side_levels(raw) -> tuple[Level, ...]:
            return tuple(Level(float(x["px"]), float(x["sz"])) for x in raw)
        return cls(
            coin=str(payload.get("coin", "")),
            ts_local_ns=int(ts_local_ns),
            ts_exch_ms=int(ts_exch_ms or payload.get("time") or 0),
            bids=side_levels(levels[0]),
            asks=side_levels(levels[1]),
        )

    @classmethod
    def try_from_payload(cls, payload: dict, ts_local_ns: int,
                         ts_exch_ms: int = 0) -> L2Book | None:
        """Come sopra ma restituisce None su book non valido, per i conteggi di
        massa: sulle statistiche serve sapere QUANTI snapshot sono inutilizzabili,
        e un'eccezione per ognuno renderebbe scomodo contarli."""
        try:
            return cls.from_payload(payload, ts_local_ns, ts_exch_ms)
        except (InvalidBook, KeyError, TypeError, ValueError):
            return None


@dataclass(frozen=True)
class Fill:
    """Esito del cammino sul book.

    Quando `sufficient` e' falso, `avg_px` e' None e ogni proprieta' derivata
    dal prezzo solleva `InsufficientDepth`. E' deliberato: il chiamante che
    dimentica di controllare il flag deve fermarsi, non ricevere uno zero.
    """

    coin: str
    side: Side
    ts_local_ns: int
    size: float
    filled_size: float
    avg_px: float | None
    mid: float
    best_px: float
    half_spread_frac: float
    levels_used: int
    depth_size: float
    depth_notional: float
    sufficient: bool

    def require_avg_px(self) -> float:
        if not self.sufficient or self.avg_px is None:
            raise InsufficientDepth(
                f"{self.coin} {self.side.name}: size {self.size:.8g} eccede la "
                f"profondita' registrata ({self.depth_size:.8g}); eseguibile "
                f"{self.filled_size:.8g}. Nessun prezzo viene estrapolato."
            )
        return self.avg_px

    @property
    def notional_nominal(self) -> float:
        """Size valutata al mid: e' la base su cui si esprimono i costi in
        frazione, cosi' che 'costo dello 0,05%' significhi la stessa cosa per
        un acquisto e per una vendita."""
        return self.size * self.mid

    @property
    def notional_executed(self) -> float:
        """Su cui si paga la commissione."""
        return self.require_avg_px() * self.filled_size

    @property
    def slippage_frac(self) -> float:
        """(avg - mid)/mid col segno del lato: positivo = costo, sempre."""
        return self.side.sign * (self.require_avg_px() - self.mid) / self.mid

    @property
    def impact_frac(self) -> float:
        """Quota di slippage dovuta ai livelli oltre il primo. Zero quando la
        size sta dentro il best, per costruzione e non per approssimazione."""
        return self.side.sign * (self.require_avg_px() - self.best_px) / self.mid

    @property
    def slippage_bps(self) -> float:
        return 1e4 * self.slippage_frac

    @property
    def impact_bps(self) -> float:
        return 1e4 * self.impact_frac

    @property
    def half_spread_bps(self) -> float:
        return 1e4 * self.half_spread_frac

    @property
    def slippage_cost(self) -> float:
        """Slippage in valuta di quote, sul notional nominale."""
        return self.slippage_frac * self.notional_nominal
