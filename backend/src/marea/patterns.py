"""Figuras chartistas detectadas de forma causal. PURO.

Fase E1 del estratega: antes de medir si un hombro-cabeza-hombro (HCH) sirve
para algo, hay que poder **reconocerlo sin mirar el futuro**. Este módulo solo
detecta. No calcula ningún resultado: eso lo hace el runner, y recién después
de que el criterio quede fijado en el pre-registro.

Definición del HCH de techo (la forma que traza Nichelson en ETH 1D,
2026-09-11 → 2026-10-02):

- Pivotes de `bob.signals.technical.swing_points` con `k` velas por lado, y
  un zigzag que alterna alto/bajo. Dos pivotes seguidos del mismo tipo se
  funden en el más extremo. Con k=5 el zigzag se come el hombro derecho de
  ETH (no hay bajo confirmado entre la cabeza y el hombro), y por eso el
  default es k=3.
- Cinco pivotes seguidos: H1, L1, H2, L2, H3, con la cabeza H2 sobre los dos
  hombros por al menos `min_head_excess`, y los hombros a no más de
  `max_shoulder_diff` uno del otro (relativo a la cabeza).
- Cuello **horizontal** en `min(L1, L2)`. Es una simplificación declarada: el
  cuello inclinado depende de qué valles se elijan, y esa elección es justo
  la que no se puede hacer a mano sin sesgo.
- Cada hombro queda al menos `min_shoulder_height` sobre el cuello, y el
  ancho H1→H3 está entre `min_width` y `max_width` velas.
- **La figura se conoce recién cuando H3 se confirma**, en `j_H3 + k`. Ese es
  el índice del evento, y nada del análisis puede usar una vela anterior a él
  como si ya supiera que había figura.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from bob.signals.technical import swing_points


@dataclass(frozen=True)
class HSParams:
    k: int = 3
    max_shoulder_diff: float = 0.05
    min_head_excess: float = 0.005
    min_shoulder_height: float = 0.03
    min_width: int = 10
    max_width: int = 90


@dataclass(frozen=True)
class Pivot:
    j: int  # índice del extremo
    kind: str  # "H" o "L"
    price: float


@dataclass(frozen=True)
class HSEvent:
    confirm: int  # primer índice en que la figura es conocida
    h1: Pivot
    l1: Pivot
    h2: Pivot
    l2: Pivot
    h3: Pivot
    neckline: float


def _zigzag_push(zz: list[Pivot], p: Pivot) -> bool:
    """Agrega `p` al zigzag; devuelve True si el último pivote cambió."""
    if zz and zz[-1].kind == p.kind:
        last = zz[-1]
        more = p.price > last.price if p.kind == "H" else p.price < last.price
        if not more:
            return False
        zz[-1] = p
        return True
    zz.append(p)
    return True


def _is_hs(seq: list[Pivot], prm: HSParams) -> float | None:
    h1, l1, h2, l2, h3 = seq
    if h2.price < max(h1.price, h3.price) * (1.0 + prm.min_head_excess):
        return None
    if abs(h1.price - h3.price) / h2.price > prm.max_shoulder_diff:
        return None
    width = h3.j - h1.j
    if not prm.min_width <= width <= prm.max_width:
        return None
    neck = min(l1.price, l2.price)
    if min(h1.price, h3.price) < neck * (1.0 + prm.min_shoulder_height):
        return None
    return neck


def detect_hs_top(high: np.ndarray, low: np.ndarray, prm: HSParams | None = None) -> list[HSEvent]:
    """HCH de techo de una serie, en orden de confirmación.

    Recorre los pivotes en el orden en que se **confirman**, así que el zigzag
    en cada paso es el que se conocía en ese momento. Una vela que es pivote
    alto y bajo a la vez (vela envolvente) entra con los dos, en el orden que
    alterna con el último pivote del zigzag: descartarla se comía el hombro
    derecho de ETH del 2026-10-02. Una misma cabeza da a lo sumo un evento, el
    primero que se confirmó.
    """
    prm = prm or HSParams()
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    ok = np.isfinite(high) & np.isfinite(low)
    hi = np.where(ok, high, -np.inf)
    lo = np.where(ok, low, np.inf)
    is_h, is_l = swing_points(hi, lo, prm.k)
    is_h &= ok
    is_l &= ok
    zz: list[Pivot] = []
    events: list[HSEvent] = []
    heads: set[int] = set()
    for j in np.flatnonzero(is_h | is_l):
        jj = int(j)
        ph = Pivot(jj, "H", float(high[jj]))
        pl = Pivot(jj, "L", float(low[jj]))
        if is_h[jj] and is_l[jj]:
            pair = [ph, pl] if zz and zz[-1].kind == "L" else [pl, ph]
        else:
            pair = [ph] if is_h[jj] else [pl]
        for p in pair:
            if not _zigzag_push(zz, p) or p.kind != "H" or len(zz) < 5:
                continue
            seq = zz[-5:]
            neck = _is_hs(seq, prm)
            if neck is None or seq[2].j in heads:
                continue
            heads.add(seq[2].j)
            h1, l1, h2, l2, h3 = seq
            events.append(HSEvent(jj + prm.k, h1, l1, h2, l2, h3, neck))
    return events


def prior_sma(close: np.ndarray, n: int) -> np.ndarray:
    """Media simple de las `n` velas **anteriores** a cada índice.

    En `j` usa `close[j-n .. j-1]`: es el nivel que se conoce al abrir la vela
    `j`, donde se dejaría una orden límite. La MA99 de Binance en la vela `j`
    incluye su propio cierre y no se conoce hasta que la vela termina.
    """
    close = np.asarray(close, dtype=np.float64)
    out = np.full(close.shape, np.nan)
    if close.size <= n:
        return out
    c = np.concatenate([[0.0], np.cumsum(close)])
    out[n:] = (c[n:-1] - c[: -n - 1]) / n
    return out
