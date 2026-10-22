# ruff: noqa: E501 — lineas de formato del reporte
"""E4 del estratega: ¿comprar el primer retorno a un order block diario gana?

    uv run python -m marea.ob_study --until 2026-10-21

Pre-registro: `docs/PREREGISTRO_OB_DIARIO.md` (`46f3780`), fijado antes de
correr. La zona sale de `bob.signals.technical.scan` y el trade de
`bob.models.technical_rules`, **sin cambios**: es la misma regla que dibuja el
Mapa y que se midió el 05-10 a 15m y 1h, ahora en diario y en el panel.
Escribe `artifacts/ob-diario-*.txt|json`.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from typing import Any

import numpy as np

from bob.models.technical_rules import RuleConfig, Trade, _make_trade, order_block_trades
from bob.signals.technical import LONG, SHORT, OrderBlockTouch, scan
from marea.hs_study import (
    SEED,
    _end_time_ms,
    bootstrap_mean,
    iso_week,
    liquidation_10x,
    longest_losing_streak,
    rising_level,
)
from marea.patterns import prior_sma

PREREG = "docs/PREREGISTRO_OB_DIARIO.md"
PREREG_COMMIT = "46f3780"

PRIMARY = RuleConfig(r_multiple=2.0, horizon=10, cost_pct=0.0014, funding_pct_per_bar=0.0003)


@dataclass(frozen=True)
class Rec:
    symbol: str
    open_time: int
    direction: str
    entry: float
    stop: float
    exit: float
    kind: str  # "target" | "stop" | "time"
    days: int
    risk_pct: float
    r: float
    liq10: bool
    stop_beyond_liq10: bool


@dataclass
class Sym:
    symbol: str
    open_time: np.ndarray
    o: np.ndarray
    h: np.ndarray
    low: np.ndarray
    c: np.ndarray
    elig: np.ndarray
    atr: np.ndarray
    touches: list[OrderBlockTouch]
    level: np.ndarray
    rising: np.ndarray


# --------------------------------------------------------------------------- núcleo


def to_rec(s: Sym, t: Trade) -> Rec:
    """Agrega al trade del 05-10 el cruce de la liquidación aislada a 10x."""
    long = t.direction == LONG
    liq = liquidation_10x(t.entry, "long" if long else "short")
    beyond = t.stop <= liq if long else t.stop >= liq
    path = slice(t.entry_idx, t.exit_idx + 1)
    if beyond:
        hit = float(np.min(s.low[path])) <= liq if long else float(np.max(s.h[path])) >= liq
    else:
        hit = t.exit_kind == "stop" and (t.exit_price <= liq if long else t.exit_price >= liq)
    return Rec(
        s.symbol,
        int(s.open_time[t.entry_idx]),
        t.direction,
        t.entry,
        t.stop,
        t.exit_price,
        t.exit_kind,
        t.exit_idx - t.entry_idx + 1,
        t.risk_pct,
        t.net_r,
        bool(hit),
        bool(beyond),
    )


def ma99_ok(s: Sym, idx: int) -> bool:
    """Exploratoria 4: cierre previo sobre la MA99 previa y MA99 creciente."""
    return idx >= 1 and bool(s.c[idx - 1] > s.level[idx - 1]) and bool(s.rising[idx])


def ob_records(
    data: list[Sym],
    cfg: RuleConfig,
    direction: str,
    keep: Callable[[Sym, int], bool] | None = None,
) -> list[Rec]:
    out = []
    for s in data:
        tcs = [
            tc
            for tc in s.touches
            if tc.direction == direction and s.elig[tc.idx] and (keep is None or keep(s, tc.idx))
        ]
        for t in order_block_trades(tcs, s.o, s.h, s.low, s.c, cfg):
            out.append(to_rec(s, t))
    return out


def placebo_records(
    data: list[Sym], cfg: RuleConfig, direction: str, seed: int = SEED
) -> list[Rec]:
    """Cada trade, re-sorteado en un día elegible al azar del mismo símbolo.

    Misma dirección y mismo riesgo en ATR; entra a la apertura del día
    siguiente, como `technical_rules.placebo_trades`, pero solo en días
    elegibles: el panel tiene años previos a la elegibilidad que la regla
    nunca podría operar.
    """
    rng = np.random.default_rng(seed)
    out = []
    for s in data:
        tcs = [tc for tc in s.touches if tc.direction == direction and s.elig[tc.idx]]
        real = order_block_trades(tcs, s.o, s.h, s.low, s.c, cfg)
        n = len(s.c)
        days = (
            np.flatnonzero(s.elig[1 : n - cfg.horizon])
            if n > cfg.horizon + 1
            else np.array([], dtype=int)
        )
        if not real or days.size == 0:
            continue
        sign = 1.0 if direction == LONG else -1.0
        for t in real:
            i = int(days[rng.integers(0, days.size)])  # entra en i + 1
            a = s.atr[i]
            if not (np.isfinite(a) and a > 0 and np.isfinite(t.risk_atr)):
                continue
            entry = float(s.o[i + 1])
            stop = entry - sign * t.risk_atr * a
            pt = _make_trade(
                i,
                i + 1,
                direction,
                entry,
                stop,
                a,
                cfg,
                s.o,
                s.h,
                s.low,
                s.c,
                first_bar_stop_only=False,
            )
            if pt is not None:
                out.append(to_rec(s, pt))
    return out


def summarize(recs: list[Rec]) -> dict[str, Any]:
    if not recs:
        return {"n": 0}
    rs = sorted(recs, key=lambda x: x.open_time)
    r = np.array([x.r for x in rs])
    ot = np.array([x.open_time for x in rs], dtype=np.int64)
    weeks = iso_week(ot)
    years = np.array([datetime.fromtimestamp(x / 1000, UTC).year for x in ot])
    out: dict[str, Any] = {
        "n": len(rs),
        "weeks": int(np.unique(weeks).size),
        "symbols": len({x.symbol for x in rs}),
        "mean_r": float(r.mean()),
        "median_r": float(np.median(r)),
        "std_r": float(r.std(ddof=1)) if len(r) > 1 else float("nan"),
        "win_rate": float((r > 0).mean()),
        "target_rate": float(np.mean([x.kind == "target" for x in rs])),
        "stop_rate": float(np.mean([x.kind == "stop" for x in rs])),
        "time_rate": float(np.mean([x.kind == "time" for x in rs])),
        "worst_r": float(r.min()),
        "worst_trade": asdict(rs[int(r.argmin())]),
        "longest_losing_streak": longest_losing_streak(r),
        "median_risk_pct": float(np.median([x.risk_pct for x in rs])),
        "liq10_rate": float(np.mean([x.liq10 for x in rs])),
        "stop_beyond_liq10_rate": float(np.mean([x.stop_beyond_liq10 for x in rs])),
        "by_year": {
            int(y): {"n": int((years == y).sum()), "mean_r": float(r[years == y].mean())}
            for y in np.unique(years)
        },
    }
    if out["weeks"] > 1:
        out["ci95"] = bootstrap_mean(r, weeks)
    return out


# --------------------------------------------------------------------------- I/O


def load(until: str) -> list[Sym]:
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
        sc = scan(o, h, lo, c)
        level = prior_sma(c, 99)
        out.append(
            Sym(
                sym,
                P.open_time[a:b],
                o,
                h,
                lo,
                c,
                elig[i, a:b],
                sc.atr,
                sc.ob_touches,
                level,
                rising_level(level),
            )
        )
    return out


def run(data: list[Sym]) -> dict[str, Any]:
    base = PRIMARY
    c1 = ob_records(data, base, LONG)
    res: dict[str, Any] = {
        "C1": summarize(c1),
        "placebo": summarize(placebo_records(data, base, LONG)),
        "bajistas": summarize(ob_records(data, base, SHORT)),
        "placebo_bajistas": summarize(placebo_records(data, base, SHORT)),
        "cost_only_r": float(
            np.mean([-(base.cost_pct + base.funding_pct_per_bar * x.days) / x.risk_pct for x in c1])
        ),
    }
    exp: dict[str, list[Rec]] = {
        "1a H = 5": ob_records(data, replace(base, horizon=5), LONG),
        "1b H = 48": ob_records(data, replace(base, horizon=48), LONG),
        "2a objetivo 1R": ob_records(data, replace(base, r_multiple=1.0), LONG),
        "2b objetivo 3R": ob_records(data, replace(base, r_multiple=3.0), LONG),
        "3 costo doble 0,28%": ob_records(data, replace(base, cost_pct=0.0028), LONG),
        "4 con la MA99 a favor": ob_records(data, base, LONG, keep=ma99_ok),
        "5 solo BTC y ETH": [x for x in c1 if x.symbol in ("BTCUSDT", "ETHUSDT")],
    }
    res["exploratory"] = {k: summarize(v) for k, v in exp.items()}
    eth = [x for x in c1 if x.symbol == "ETHUSDT"]
    res["eth"] = summarize(eth)
    res["eth_trades"] = [asdict(x) for x in sorted(eth, key=lambda x: x.open_time)]
    return res


def _row(name: str, s: dict[str, Any]) -> str:
    if s.get("n", 0) == 0:
        return f"  {name:28s} sin trades"
    ci = s.get("ci95")
    ci_txt = f"[{ci[0]:+.3f}, {ci[1]:+.3f}]" if ci else "      —        "
    return (
        f"  {name:28s} n={s['n']:5d} sem={s['weeks']:4d}  media {s['mean_r']:+.3f} R {ci_txt}  gana {s['win_rate'] * 100:4.1f}%  "
        f"obj/stop/tiempo {s['target_rate'] * 100:3.0f}/{s['stop_rate'] * 100:3.0f}/{s['time_rate'] * 100:3.0f}%  racha {s['longest_losing_streak']}"
    )


def render(res: dict[str, Any], meta: dict[str, Any]) -> str:
    c1, pl = res["C1"], res["placebo"]
    lo = c1["ci95"][0]
    verdict = (
        "C1 PASA: el límite inferior del IC95% es > 0."
        if lo > 0
        else f"C1 NO PASA: el límite inferior del IC95% es {lo:+.3f} R, no > 0."
    )
    eth = res["eth"]
    out = [
        "E4 — ORDER BLOCK ALCISTA EN VELAS DIARIAS (panel)",
        f"Pre-registro {PREREG} ({PREREG_COMMIT}). Velas hasta {meta['until']}. {meta['n_symbols']} símbolos con ≥ 200 velas.",
        "Regla: límite en el borde superior, stop bajo la zona − 0,1 ATR, objetivo 2R, salida a los 10 días.",
        "",
        "VEREDICTO",
        verdict,
        f"Placebo: media {pl['mean_r']:+.3f} R, IC95% [{pl['ci95'][0]:+.3f}, {pl['ci95'][1]:+.3f}]; costo solo {res['cost_only_r']:+.3f} R.",
        "",
        "CONFIRMATORIA Y COMPARACIONES (R neto)",
        _row("C1 alcistas", c1),
        _row("placebo alcistas", pl),
        _row("bajistas (no decide)", res["bajistas"]),
        _row("placebo bajistas", res["placebo_bajistas"]),
        "",
        "RIESGO",
        f"  stop mediano {c1['median_risk_pct'] * 100:.2f}% de la entrada; desvío {c1['std_r']:.2f} R por trade",
        f"  a 10x aislado: stop DETRÁS de la liquidación en {c1['stop_beyond_liq10_rate'] * 100:.1f}% de los trades;",
        f"  pérdida realizada más allá de la liquidación en {c1['liq10_rate'] * 100:.1f}%",
        f"  peor trade {c1['worst_r']:+.2f} R ({c1['worst_trade']['symbol']}, {datetime.fromtimestamp(c1['worst_trade']['open_time'] / 1000, UTC):%Y-%m-%d})",
        "",
        "POR AÑO (alcistas)",
        *[f"  {y}: n={v['n']:5d}  media {v['mean_r']:+.3f} R" for y, v in c1["by_year"].items()],
        "",
        "EXPLORATORIAS (no deciden; 7 pruebas sobre la misma muestra)",
        *[_row(k, v) for k, v in res["exploratory"].items()],
        "",
        "ETH (descriptivo, sin potencia)",
        _row("ETH alcistas", eth),
        *[
            f"  {datetime.fromtimestamp(t['open_time'] / 1000, UTC):%Y-%m-%d}  entrada {t['entry']:,.2f}  stop {t['stop']:,.2f}  salida {t['exit']:,.2f} ({t['kind']}, {t['days']} d)  {t['r']:+.2f} R"
            for t in res["eth_trades"][-12:]
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
    data = load(args.until)
    res = run(data)
    run_at = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    meta = {
        "until": args.until,
        "n_symbols": len(data),
        "run_at": run_at,
        "seconds": time.time() - t0,
        "prereg": PREREG,
        "prereg_commit": PREREG_COMMIT,
    }
    txt = render(res, meta)
    print(txt)
    if not args.no_artifact:
        from marea.power_run import ARTIFACTS

        stem = ARTIFACTS / f"ob-diario-{run_at}"
        stem.with_suffix(".txt").write_text(txt + "\n", encoding="utf-8")
        stem.with_suffix(".json").write_text(
            json.dumps({"meta": meta, "results": res}, indent=1, default=float), encoding="utf-8"
        )
        print(f"\nArtefacto: {stem}.txt|json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
