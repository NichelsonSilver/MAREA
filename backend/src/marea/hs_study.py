# ruff: noqa: E501 — lineas de formato del reporte
"""E1 del estratega: ¿el long en la MA99 después de un HCH de techo gana?

    uv run python -m marea.hs_study --until 2026-10-21

Pre-registro: `docs/PREREGISTRO_HCH_MA99.md` (`0fea916`), fijado antes de
correr. Corre la confirmatoria (C1), las comparaciones obligatorias (B1,
placebo, liquidación a 10x) y las exploratorias declaradas, en ese orden, y
escribe `artifacts/hch-ma99-*.txt|json`. Ninguna exploratoria reemplaza a C1.

El núcleo (`simulate`, las funciones de entradas y el bootstrap) es puro; la
única I/O es `main`, que lee el panel diario de la DB.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from typing import Any

import numpy as np

from bob.models.projection import DEFAULT_MMR
from marea.patterns import HSEvent, HSParams, detect_hs_top, prior_sma

PREREG = "docs/PREREGISTRO_HCH_MA99.md"
PREREG_COMMIT = "0fea916"
SEED = 20261022
N_BOOT = 10_000
N_PLACEBO = 5_000
MS_DAY = 86_400_000
LIQ_LEVERAGE = 10.0


@dataclass(frozen=True)
class TradeRule:
    stop_atr: float = 1.1
    horizon: int = 7
    #: Objetivo en R (exploratoria 3). Sin salida por tiempo, con tope de
    #: `max_hold` velas para que el trade termine.
    target_r: float | None = None
    max_hold: int = 30
    cost: float = 0.0014  # ida y vuelta, fracción del notional de entrada
    funding_per_day: float = 0.0003  # 0,01% cada 8 h


@dataclass(frozen=True)
class Trade:
    symbol: str
    open_time: int  # de la vela de entrada, ms UTC
    direction: str
    entry: float
    stop: float
    exit: float
    days: int
    reason: str  # "stop" | "time" | "target"
    r: float
    liq10: bool  # la pérdida se realizó más allá de la liquidación a 10x
    stop_beyond_liq10: bool  # el stop quedaba detrás de la liquidación a 10x


# --------------------------------------------------------------------------- núcleo


def prior_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int = 14) -> np.ndarray:
    """ATR de las `n` velas **anteriores**: media simple del rango verdadero."""
    prev = np.concatenate([[np.nan], close[:-1]])
    tr = np.fmax(high - low, np.fmax(np.abs(high - prev), np.abs(low - prev)))
    tr[0] = high[0] - low[0]
    return prior_sma(tr, n)


def rising_level(level: np.ndarray, lag: int = 10) -> np.ndarray:
    out = np.zeros(level.shape, dtype=bool)
    out[lag:] = level[lag:] > level[:-lag]
    return out


def _finite(*xs: float) -> bool:
    return all(np.isfinite(x) for x in xs)


def simulate(
    o: np.ndarray,
    h: np.ndarray,
    low: np.ndarray,
    c: np.ndarray,
    j: int,
    entry: float,
    risk: float,
    direction: str,
    rule: TradeRule,
) -> tuple[float, int, str] | None:
    """Recorre el trade desde la vela `j`. Devuelve (salida, vela de salida, motivo).

    `None` si la serie se corta (fin de datos o vela faltante) antes de la
    salida: ese trade no está resuelto y no entra. Un short se resuelve como
    un long sobre los precios negados.
    """
    if direction == "short":
        res = simulate(-o, -low, -h, -c, j, -entry, risk, "long", rule)
        return None if res is None else (-res[0], res[1], res[2])
    stop = entry - risk
    tgt = entry + rule.target_r * risk if rule.target_r is not None else None
    if low[j] <= stop:  # la vela de entrada toca el stop: cuenta como stop
        return stop, j, "stop"
    hold = rule.max_hold if tgt is not None else rule.horizon
    n = len(c)
    for k in range(j + 1, j + hold):
        if k >= n or not _finite(o[k], h[k], low[k], c[k]):
            return None
        if o[k] <= stop:
            return float(o[k]), k, "stop"
        if tgt is not None and o[k] >= tgt:
            return float(o[k]), k, "target"
        if low[k] <= stop:
            return stop, k, "stop"
        if tgt is not None and h[k] >= tgt:
            return tgt, k, "target"
    last = j + hold - 1
    return float(c[last]), last, "time"


def liquidation_10x(entry: float, direction: str) -> float:
    """Liquidación aislada a 10x, tier 1 (la fórmula de `projection.py` con cumB = 0)."""
    inv = 1.0 / LIQ_LEVERAGE
    if direction == "long":
        return entry * (1.0 - inv) / (1.0 - DEFAULT_MMR)
    return entry * (1.0 + inv) / (1.0 + DEFAULT_MMR)


def make_trade(
    symbol: str,
    open_time: np.ndarray,
    o: np.ndarray,
    h: np.ndarray,
    low: np.ndarray,
    c: np.ndarray,
    atr: np.ndarray,
    j: int,
    fill: float,
    direction: str,
    rule: TradeRule,
) -> Trade | None:
    risk = rule.stop_atr * float(atr[j])
    if not (np.isfinite(risk) and risk > 0 and np.isfinite(fill) and fill > 0):
        return None
    res = simulate(o, h, low, c, j, fill, risk, direction, rule)
    if res is None:
        return None
    exit_px, k, reason = res
    sign = 1.0 if direction == "long" else -1.0
    stop = fill - sign * risk
    days = k - j + 1
    pnl = sign * (exit_px - fill) - fill * (rule.cost + rule.funding_per_day * days)
    liq = liquidation_10x(fill, direction)
    beyond = stop <= liq if direction == "long" else stop >= liq
    if beyond:
        extreme = (
            float(np.min(low[j : k + 1])) if direction == "long" else float(np.max(h[j : k + 1]))
        )
        liq_hit = extreme <= liq if direction == "long" else extreme >= liq
    else:
        liq_hit = reason == "stop" and (exit_px <= liq if direction == "long" else exit_px >= liq)
    return Trade(
        symbol,
        int(open_time[j]),
        direction,
        fill,
        stop,
        exit_px,
        days,
        reason,
        pnl / risk,
        liq_hit,
        beyond,
    )


def thesis_entries(
    events: list[HSEvent],
    o: np.ndarray,
    low: np.ndarray,
    c: np.ndarray,
    elig: np.ndarray,
    level: np.ndarray,
    rising: np.ndarray,
    window: int = 30,
    require_rising: bool = True,
) -> list[tuple[int, float]]:
    """(vela de entrada, precio) de la tesis, §3.3. Una entrada por vela."""
    n = len(c)
    out: dict[int, float] = {}
    for ev in events:
        t = ev.confirm
        if t >= n or t < 1 or not elig[t] or not c[t - 1] > level[t - 1]:
            continue
        for j in range(t, min(t + window, n)):
            if low[j] <= level[j]:
                if (rising[j] or not require_rising) and elig[j]:
                    out.setdefault(j, float(min(o[j], level[j])))
                break
    return sorted(out.items())


def b1_entries(
    o: np.ndarray,
    low: np.ndarray,
    c: np.ndarray,
    elig: np.ndarray,
    level: np.ndarray,
    rising: np.ndarray,
    cooldown: int = 20,
) -> list[tuple[int, float]]:
    """B1: todo toque desde arriba de la MA99 creciente, sin figura."""
    out = []
    last = -(10**9)
    for j in range(1, len(c)):
        if (
            elig[j]
            and rising[j]
            and c[j - 1] > level[j - 1]
            and low[j] <= level[j]
            and j - last > cooldown
        ):
            out.append((j, float(min(o[j], level[j]))))
            last = j
    return out


def placebo_candidates(
    c: np.ndarray, elig: np.ndarray, level: np.ndarray, rising: np.ndarray
) -> np.ndarray:
    """Días elegibles, sobre la MA99 y con MA99 creciente: se entra a la apertura."""
    above = np.zeros(c.shape, dtype=bool)
    with np.errstate(invalid="ignore"):
        above[1:] = c[:-1] > level[:-1]
    return np.flatnonzero(elig & rising & above)


def short_entries(
    events: list[HSEvent], o: np.ndarray, c: np.ndarray, elig: np.ndarray, window: int = 30
) -> list[tuple[int, float]]:
    """Exploratoria 9: short a la apertura siguiente al primer cierre bajo el cuello."""
    n = len(c)
    out: dict[int, float] = {}
    for ev in events:
        for j in range(ev.confirm, min(ev.confirm + window, n - 1)):
            if c[j] < ev.neckline:
                if elig[j + 1]:
                    out.setdefault(j + 1, float(o[j + 1]))
                break
    return sorted(out.items())


def iso_week(open_time_ms: np.ndarray) -> np.ndarray:
    """Semana ISO en UTC (lunes a domingo). El día 0 de la época fue jueves."""
    return (np.asarray(open_time_ms, dtype=np.int64) // MS_DAY + 3) // 7


def bootstrap_mean(
    r: np.ndarray, weeks: np.ndarray, n_boot: int = N_BOOT, seed: int = SEED
) -> tuple[float, float]:
    """IC95% de la media remuestreando semanas enteras."""
    uw, inv = np.unique(weeks, return_inverse=True)
    sums = np.bincount(inv, weights=r)
    cnts = np.bincount(inv).astype(np.float64)
    idx = np.random.default_rng(seed).integers(0, len(uw), (n_boot, len(uw)))
    means = sums[idx].sum(1) / cnts[idx].sum(1)
    lo, hi = np.percentile(means, [2.5, 97.5])
    return float(lo), float(hi)


def bootstrap_diff(
    ra: np.ndarray,
    wa: np.ndarray,
    rb: np.ndarray,
    wb: np.ndarray,
    n_boot: int = N_BOOT,
    seed: int = SEED,
) -> tuple[float, float]:
    """IC95% de media(a) − media(b) con las mismas semanas remuestreadas en las dos."""
    uw = np.union1d(wa, wb)
    ia, ib = np.searchsorted(uw, wa), np.searchsorted(uw, wb)
    m = len(uw)
    sa, ca = np.bincount(ia, ra, m), np.bincount(ia, minlength=m).astype(np.float64)
    sb, cb = np.bincount(ib, rb, m), np.bincount(ib, minlength=m).astype(np.float64)
    idx = np.random.default_rng(seed).integers(0, m, (n_boot, m))
    with np.errstate(invalid="ignore", divide="ignore"):
        d = sa[idx].sum(1) / ca[idx].sum(1) - sb[idx].sum(1) / cb[idx].sum(1)
    lo, hi = np.nanpercentile(d, [2.5, 97.5])
    return float(lo), float(hi)


def longest_losing_streak(r: np.ndarray) -> int:
    best = cur = 0
    for x in r:
        cur = cur + 1 if x < 0 else 0
        best = max(best, cur)
    return best


def summarize(trades: list[Trade], *, with_ci: bool = True) -> dict[str, Any]:
    if not trades:
        return {"n": 0}
    tr = sorted(trades, key=lambda t: t.open_time)
    r = np.array([t.r for t in tr])
    ot = np.array([t.open_time for t in tr], dtype=np.int64)
    weeks = iso_week(ot)
    years = np.array([datetime.fromtimestamp(x / 1000, UTC).year for x in ot])
    out: dict[str, Any] = {
        "n": len(tr),
        "weeks": int(np.unique(weeks).size),
        "symbols": len({t.symbol for t in tr}),
        "mean_r": float(r.mean()),
        "median_r": float(np.median(r)),
        "std_r": float(r.std(ddof=1)) if len(r) > 1 else float("nan"),
        "win_rate": float((r > 0).mean()),
        "stop_rate": float(np.mean([t.reason == "stop" for t in tr])),
        "target_rate": float(np.mean([t.reason == "target" for t in tr])),
        "worst_r": float(r.min()),
        "best_r": float(r.max()),
        "worst_trade": asdict(tr[int(r.argmin())]),
        "longest_losing_streak": longest_losing_streak(r),
        "liq10_rate": float(np.mean([t.liq10 for t in tr])),
        "stop_beyond_liq10_rate": float(np.mean([t.stop_beyond_liq10 for t in tr])),
        "mean_stop_pct": float(np.mean([abs(t.entry - t.stop) / t.entry for t in tr])),
        "by_year": {
            int(y): {"n": int((years == y).sum()), "mean_r": float(r[years == y].mean())}
            for y in np.unique(years)
        },
    }
    if with_ci and out["weeks"] > 1:
        out["ci95"] = bootstrap_mean(r, weeks)
    return out


# --------------------------------------------------------------------------- I/O


@dataclass
class SymbolData:
    symbol: str
    open_time: np.ndarray
    o: np.ndarray
    h: np.ndarray
    low: np.ndarray
    c: np.ndarray
    elig: np.ndarray
    level: np.ndarray
    rising: np.ndarray
    atr: np.ndarray
    events: dict[int, list[HSEvent]]


def _end_time_ms(until: str) -> int:
    d = datetime.strptime(until, "%Y-%m-%d").replace(tzinfo=UTC)
    return int(d.timestamp() * 1000)


def load_symbols(until: str, ks: tuple[int, ...]) -> list[SymbolData]:
    from bob.data.panel import load_panel, load_universe
    from bob.signals.universe import eligibility_mask

    u = load_universe()
    P = load_panel([m.symbol for m in u.symbols], "1d", end_time=_end_time_ms(until))
    elig = eligibility_mask(P.close, P.quote_volume)
    out = []
    for i, sym in enumerate(P.symbols):
        obs = np.flatnonzero(np.isfinite(P.close[i]))
        if obs.size < 200:
            continue
        a, b = obs[0], obs[-1] + 1
        o, h, lo, c = (x[i, a:b] for x in (P.open, P.high, P.low, P.close))
        level = prior_sma(c, 99)
        out.append(
            SymbolData(
                sym,
                P.open_time[a:b],
                o,
                h,
                lo,
                c,
                elig[i, a:b],
                level,
                rising_level(level),
                prior_atr(h, lo, c),
                {k: detect_hs_top(h, lo, HSParams(k=k)) for k in ks},
            )
        )
    return out


def _trades(
    data: list[SymbolData],
    entries: Callable[[SymbolData], list[tuple[int, float]]],
    direction: str,
    rule: TradeRule,
) -> list[Trade]:
    out = []
    for s in data:
        for j, fill in entries(s):
            t = make_trade(
                s.symbol, s.open_time, s.o, s.h, s.low, s.c, s.atr, j, fill, direction, rule
            )
            if t is not None:
                out.append(t)
    return out


def _thesis(
    data: list[SymbolData],
    rule: TradeRule,
    *,
    k: int = 3,
    window: int = 30,
    require_rising: bool = True,
) -> list[Trade]:
    return _trades(
        data,
        lambda s: thesis_entries(
            s.events[k], s.o, s.low, s.c, s.elig, s.level, s.rising, window, require_rising
        ),
        "long",
        rule,
    )


def _placebo(data: list[SymbolData], symbols: set[str], rule: TradeRule) -> list[Trade]:
    cands = [
        (si, int(j))
        for si, s in enumerate(data)
        if s.symbol in symbols
        for j in placebo_candidates(s.c, s.elig, s.level, s.rising)
    ]
    rng = np.random.default_rng(SEED)
    pick = rng.choice(len(cands), size=min(N_PLACEBO, len(cands)), replace=False)
    out = []
    for p in sorted(pick):
        si, j = cands[p]
        s = data[si]
        t = make_trade(
            s.symbol, s.open_time, s.o, s.h, s.low, s.c, s.atr, j, float(s.o[j]), "long", rule
        )
        if t is not None:
            out.append(t)
    return out


def run(data: list[SymbolData]) -> dict[str, Any]:
    base = TradeRule()
    thesis = _thesis(data, base)
    b1 = _trades(
        data, lambda s: b1_entries(s.o, s.low, s.c, s.elig, s.level, s.rising), "long", base
    )
    placebo = _placebo(data, {t.symbol for t in thesis}, base)

    def arr(ts: list[Trade]) -> tuple[np.ndarray, np.ndarray]:
        return np.array([t.r for t in ts]), iso_week(
            np.array([t.open_time for t in ts], dtype=np.int64)
        )

    (ra, wa), (rb, wb) = arr(thesis), arr(b1)
    diff = float(ra.mean() - rb.mean())
    res: dict[str, Any] = {
        "C1": summarize(thesis),
        "B1": summarize(b1),
        "thesis_minus_B1": {"diff": diff, "ci95": bootstrap_diff(ra, wa, rb, wb)},
        "placebo": summarize(placebo),
        "cost_only_r_thesis": float(
            np.mean(
                [
                    -(t.entry * (base.cost + base.funding_per_day * t.days)) / abs(t.entry - t.stop)
                    for t in thesis
                ]
            )
        ),
    }
    exp: dict[str, list[Trade]] = {
        "1a H = 5": _thesis(data, replace(base, horizon=5)),
        "1b H = 10": _thesis(data, replace(base, horizon=10)),
        "2 stop 1,5 ATR": _thesis(data, replace(base, stop_atr=1.5)),
        "3 objetivo 2R (tope 30 d)": _thesis(data, replace(base, target_r=2.0)),
        "4 sin MA99 creciente": _thesis(data, base, require_rising=False),
        "5a pivotes k = 2": _thesis(data, base, k=2),
        "5b pivotes k = 4": _thesis(data, base, k=4),
        "6a ventana 15 d": _thesis(data, base, window=15),
        "6b ventana 60 d": _thesis(data, base, window=60),
        "7 costo doble 0,28%": _thesis(data, replace(base, cost=0.0028)),
        "8a solo BTC y ETH": [t for t in thesis if t.symbol in ("BTCUSDT", "ETHUSDT")],
        "8b solo ETH": [t for t in thesis if t.symbol == "ETHUSDT"],
        "9 short clásico bajo el cuello": _trades(
            data, lambda s: short_entries(s.events[3], s.o, s.c, s.elig), "short", base
        ),
    }
    res["exploratory"] = {name: summarize(ts) for name, ts in exp.items()}
    res["eth_trades"] = [asdict(t) for t in exp["8b solo ETH"]]
    return res


def _verdict(c1: dict[str, Any], placebo: dict[str, Any]) -> str:
    lo = c1["ci95"][0]
    plo, phi = placebo["ci95"]
    lines = []
    if lo > 0:
        lines.append("C1 PASA: el límite inferior del IC95% es > 0.")
    else:
        lines.append(f"C1 NO PASA: el límite inferior del IC95% es {lo:+.3f} R, no > 0.")
    lines.append(f"Placebo: media {placebo['mean_r']:+.3f} R, IC95% [{plo:+.3f}, {phi:+.3f}].")
    return "\n".join(lines)


def _row(name: str, s: dict[str, Any]) -> str:
    if s.get("n", 0) == 0:
        return f"  {name:34s} sin trades"
    ci = s.get("ci95")
    ci_txt = f"[{ci[0]:+.3f}, {ci[1]:+.3f}]" if ci else "      —        "
    return (
        f"  {name:34s} n={s['n']:5d} sem={s['weeks']:4d}  media {s['mean_r']:+.3f} R {ci_txt}  "
        f"gana {s['win_rate'] * 100:4.1f}%  stops {s['stop_rate'] * 100:4.1f}%  peor {s['worst_r']:+.2f}  racha {s['longest_losing_streak']}"
    )


def render(res: dict[str, Any], meta: dict[str, Any]) -> str:
    c1, b1, pl = res["C1"], res["B1"], res["placebo"]
    d = res["thesis_minus_B1"]
    out = [
        "E1 — LONG EN LA MA99 DESPUÉS DE UN HCH DE TECHO (panel diario)",
        f"Pre-registro {PREREG} ({PREREG_COMMIT}). Velas hasta {meta['until']}. {meta['n_symbols']} símbolos con ≥ 200 velas.",
        "",
        "VEREDICTO",
        _verdict(c1, pl),
        "",
        "CONFIRMATORIA Y COMPARACIONES (R neto de costo y funding)",
        _row("C1 tesis", c1),
        _row("B1 toque de MA99 sin figura", b1),
        _row("placebo (entrada al azar)", pl),
        f"  tesis − B1: {d['diff']:+.3f} R, IC95% [{d['ci95'][0]:+.3f}, {d['ci95'][1]:+.3f}]",
        f"  costo solo, en R, promedio de la tesis: {res['cost_only_r_thesis']:+.3f} R",
        "",
        "RIESGO DE LA TESIS",
        f"  stop medio {c1['mean_stop_pct'] * 100:.2f}% de la entrada; desvío {c1['std_r']:.2f} R por trade",
        f"  a 10x aislado: el stop quedaba DETRÁS de la liquidación en {c1['stop_beyond_liq10_rate'] * 100:.1f}% de los trades;",
        f"  la pérdida se realizó más allá de la liquidación en {c1['liq10_rate'] * 100:.1f}%",
        f"  peor trade: {c1['worst_r']:+.2f} R ({c1['worst_trade']['symbol']}, {datetime.fromtimestamp(c1['worst_trade']['open_time'] / 1000, UTC):%Y-%m-%d})",
        "",
        "POR AÑO (tesis)",
        *[f"  {y}: n={v['n']:4d}  media {v['mean_r']:+.3f} R" for y, v in c1["by_year"].items()],
        "",
        "EXPLORATORIAS (no deciden; 13 pruebas sobre la misma muestra)",
        *[_row(k, v) for k, v in res["exploratory"].items()],
        "",
        "ETH (descriptivo, sin potencia)",
        *[
            f"  {datetime.fromtimestamp(t['open_time'] / 1000, UTC):%Y-%m-%d}  entrada {t['entry']:,.2f}  stop {t['stop']:,.2f}  salida {t['exit']:,.2f} ({t['reason']}, {t['days']} d)  {t['r']:+.2f} R"
            for t in res["eth_trades"]
        ],
        "",
        f"Corrida {meta['run_at']} en {meta['seconds']:.0f} s. Nunca ejecuta órdenes.",
    ]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--until", default="2026-10-21")
    p.add_argument("--no-artifact", action="store_true")
    args = p.parse_args(argv)
    t0 = time.time()
    data = load_symbols(args.until, (2, 3, 4))
    res = run(data)
    run_at = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    try:
        head = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=False
        ).stdout.strip()
    except OSError:
        head = ""
    meta = {
        "until": args.until,
        "n_symbols": len(data),
        "run_at": run_at,
        "seconds": time.time() - t0,
        "git_head": head,
        "prereg": PREREG,
        "prereg_commit": PREREG_COMMIT,
    }
    txt = render(res, meta)
    print(txt)
    if not args.no_artifact:
        from marea.power_run import ARTIFACTS

        stem = ARTIFACTS / f"hch-ma99-{run_at}"
        stem.with_suffix(".txt").write_text(txt + "\n", encoding="utf-8")
        stem.with_suffix(".json").write_text(
            json.dumps({"meta": meta, "results": res}, indent=1, default=float), encoding="utf-8"
        )
        print(f"\nArtefacto: {stem}.txt|json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
