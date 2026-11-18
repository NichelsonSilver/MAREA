"""Orden mundial, económico y geopolítico: lo que se puede medir de él. Descriptivo.

    uv run python -m marea.world_order
    uv run python -m marea.world_order --json

Un "cambio de orden" no se mide con un número. Lo que sí se mide son sus
huellas en los precios, y este módulo las ordena en seis dimensiones, cada una
con una métrica principal y algunas de apoyo:

1. **Dólar**: dólar amplio (FRED `DTWEXBGS`).
2. **Activo de reserva**: oro contra el Tesoro largo (GLD/TLT). Si los bancos
   centrales y los inversores pasan sus reservas de bonos de EE.UU. a oro, la
   razón sube.
3. **Costo del dinero**: tasa real a 10 años, y el Tesoro a 10 años contra la
   tasa de la Fed. Que el 10 años suba con la Fed quieta es el mercado
   cobrándole al fisco, no a la política monetaria.
4. **Shock real y geopolítico**: Brent y energía contra tecnología.
5. **Excepcionalismo de EE.UU.**: emergentes contra el S&P 500 (EEM/SPY). Sube
   si el resto del mundo le gana a EE.UU.
6. **Crecimiento global**: cobre contra oro (CPER/GLD).

Para cada métrica: el nivel o la razón de hoy, su cambio a 13 semanas, 1, 3 y
5 años, y un percentil:

- **Niveles** (tasas, dólar, Brent, VIX): fracción de la historia disponible
  por debajo del valor de hoy.
- **Razones** de ETFs: el nivel de una razón no significa nada (depende de la
  fecha de lanzamiento), así que el percentil es el de su **cambio a 1 año**
  contra los cambios a 1 año del pasado estricto que no solapan la ventana.

Cada dimensión resume dos lecturas con la métrica principal: la **tendencia
larga** (signo del cambio a 5 años) y la **fase corta** (signo del cambio a 13
semanas). Cuando difieren, la fase corta va contra la tendencia, y eso es lo
que conviene mirar. **No es pronóstico** y no lleva pesos ni índice compuesto:
interpretar en qué etapa está el orden mundial es juicio, y va declarado como
tal en el skill `/macro`, no acá.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal

import numpy as np
from numpy.typing import NDArray

from marea.config import DB_PATH
from marea.data import store
from marea.pit import as_of
from marea.returns import total_returns

Kind = Literal["level", "ratio"]
Unit = Literal["pct", "pp"]

#: Ventanas de cambio: (clave, días de calendario para FRED, sesiones para ETFs).
HORIZONS: tuple[tuple[str, int, int], ...] = (
    ("13s", 91, 65),
    ("1a", 365, 252),
    ("3a", 1095, 756),
    ("5a", 1826, 1260),
)


@dataclass(frozen=True)
class Metric:
    key: str
    kind: Kind
    #: FRED: el id; razón: (numerador, denominador); diferencia FRED: (a, b) con a − b.
    source: tuple[str, ...]
    label: str
    #: "pp" = el cambio se mide en puntos (tasas); "pct" = cambio relativo.
    unit: Unit = "pct"


@dataclass(frozen=True)
class Dimension:
    key: str
    label: str
    headline: Metric
    support: tuple[Metric, ...]
    #: Qué significa que la métrica principal suba, en palabras.
    up_means: str


DIMENSIONS: tuple[Dimension, ...] = (
    Dimension(
        "dolar",
        "Dólar",
        Metric("dolar_amplio", "level", ("DTWEXBGS",), "Dólar amplio (FRED)"),
        (
            Metric("eurusd", "level", ("DEXUSEU",), "Euro en dólares (sube = euro más fuerte)"),
            Metric("usdjpy", "level", ("DEXJPUS",), "Yenes por dólar"),
            Metric("usdcny", "level", ("DEXCHUS",), "Yuanes por dólar"),
        ),
        "el dólar se fortalece contra sus socios comerciales",
    ),
    Dimension(
        "reserva",
        "Activo de reserva",
        Metric("oro_vs_tesoro", "ratio", ("GLD", "TLT"), "Oro contra Tesoro largo (GLD/TLT)"),
        (Metric("oro_vs_acciones", "ratio", ("GLD", "SPY"), "Oro contra S&P 500 (GLD/SPY)"),),
        "el oro le gana al Tesoro como reserva de valor",
    ),
    Dimension(
        "costo_dinero",
        "Costo del dinero",
        Metric("tasa_real", "level", ("DFII10",), "Tasa real 10 años (TIPS)", "pp"),
        (
            Metric("tesoro_10a", "level", ("DGS10",), "Tesoro 10 años", "pp"),
            Metric(
                "tesoro_vs_fed", "level", ("DGS10", "DFF"), "Tesoro 10 años menos fed funds", "pp"
            ),
            Metric("inflacion_esperada", "level", ("T10YIE",), "Inflación esperada 10 años", "pp"),
        ),
        "el dinero se encarece en términos reales",
    ),
    Dimension(
        "shock_real",
        "Shock real y geopolítico",
        Metric("brent", "level", ("DCOILBRENTEU",), "Petróleo Brent"),
        (
            Metric("energia_vs_tecno", "ratio", ("XLE", "XLK"), "Energía contra tecnología"),
            Metric("wti", "level", ("DCOILWTICO",), "Petróleo WTI"),
        ),
        "sube el costo de la energía",
    ),
    Dimension(
        "excepcionalismo",
        "Excepcionalismo de EE.UU.",
        Metric("emergentes_vs_eeuu", "ratio", ("EEM", "SPY"), "Emergentes contra S&P 500"),
        (
            Metric("desarrollados_vs_eeuu", "ratio", ("EFA", "SPY"), "Desarrollados ex-EE.UU."),
            Metric("china_vs_eeuu", "ratio", ("FXI", "SPY"), "China contra S&P 500"),
        ),
        "el resto del mundo le gana a EE.UU.",
    ),
    Dimension(
        "crecimiento",
        "Crecimiento global",
        Metric("cobre_vs_oro", "ratio", ("CPER", "GLD"), "Cobre contra oro"),
        (Metric("vix", "level", ("VIXCLS",), "VIX", "pp"),),
        "el mercado paga crecimiento por sobre protección",
    ),
)


def _r(v: float | None, nd: int = 6) -> float | None:
    return None if v is None or not math.isfinite(v) else round(float(v), nd)


# --- Cálculo puro ------------------------------------------------------------


def level_metric(days: Sequence[date], values: NDArray[np.float64], unit: Unit) -> dict[str, Any]:
    """Último valor, cambios por horizonte de calendario y percentil del nivel."""
    if len(values) == 0:
        raise ValueError("serie vacía")
    last_day, last = days[-1], float(values[-1])
    out: dict[str, Any] = {
        "as_of": last_day.isoformat(),
        "value": _r(last),
        "pct": _r(float(np.mean(values[:-1] < last))) if len(values) > 1 else None,
        "pct_basis": "nivel contra toda la historia",
        "since": days[0].isoformat(),
    }
    for key, cal, _ in HORIZONS:
        target = last_day - timedelta(days=cal)
        i = (
            int(
                np.searchsorted(
                    np.array(days, dtype="datetime64[D]"), np.datetime64(target), "right"
                )
            )
            - 1
        )
        if i < 0:
            out[f"chg_{key}"] = None
            continue
        prev = float(values[i])
        out[f"chg_{key}"] = _r(last - prev if unit == "pp" else last / prev - 1.0)
    return out


def ratio_metric(days: Sequence[date], log_ratio: NDArray[np.float64]) -> dict[str, Any]:
    """Cambio de una razón por horizonte de sesiones y percentil de su cambio a 1 año."""
    n = len(log_ratio)
    if n < 2:
        raise ValueError("serie vacía")
    out: dict[str, Any] = {
        "as_of": days[-1].isoformat(),
        "value": None,
        "since": days[0].isoformat(),
        "pct_basis": "cambio a 1 año contra el pasado estricto",
    }
    for key, _, sess in HORIZONS:
        out[f"chg_{key}"] = (
            _r(math.expm1(log_ratio[-1] - log_ratio[-1 - sess])) if n > sess else None
        )
    year = 252
    if n > 2 * year:
        changes = log_ratio[year:] - log_ratio[:-year]
        now = changes[-1]
        past = changes[: len(changes) - year]
        out["pct"] = _r(float(np.mean(past < now)))
    else:
        out["pct"] = None
    return out


def sign_word(v: float | None) -> str | None:
    if v is None:
        return None
    return "sube" if v > 0 else "baja" if v < 0 else "plano"


def reading(head: dict[str, Any]) -> dict[str, Any]:
    long_, short = sign_word(head.get("chg_5a")), sign_word(head.get("chg_13s"))
    return {
        "long_trend": long_,
        "short_phase": short,
        "against_trend": long_ is not None and short is not None and long_ != short,
    }


# --- I/O ---------------------------------------------------------------------


def _fred(
    conn: sqlite3.Connection, sid: str, today: date
) -> tuple[list[date], NDArray[np.float64]]:
    meta = store.series_meta(conn, sid)
    if meta is None:
        raise LookupError(f"falta {sid}: correr `marea.data.ingest fred`")
    fv = date.fromisoformat(meta["first_vintage"]) if meta["first_vintage"] else None
    raw = as_of(store.load_periods(conn, sid), today, fv, meta["fallback_lag_days"])
    pts = [(p.obs_date, p.value) for p in raw if p.value is not None]
    return [d for d, _ in pts], np.array([float(v) for _, v in pts])


def _fred_diff(
    conn: sqlite3.Connection, a: str, b: str, today: date
) -> tuple[list[date], NDArray[np.float64]]:
    da, va = _fred(conn, a, today)
    db, vb = _fred(conn, b, today)
    bmap = dict(zip(db, vb, strict=True))
    days = [d for d in da if d in bmap]
    amap = dict(zip(da, va, strict=True))
    return days, np.array([amap[d] - bmap[d] for d in days])


def _log_index(conn: sqlite3.Connection, t: str) -> dict[date, float]:
    bars = store.load_bars(conn, t)
    r = total_returns(
        np.array([b.close for b in bars]),
        np.array([b.div_cash for b in bars]),
        np.array([b.split_factor for b in bars]),
    )
    lr = np.concatenate([[0.0], np.cumsum(np.log1p(r[1:]))]) if len(bars) else np.array([])
    return {b.day: float(x) for b, x in zip(bars, lr, strict=True)}


def _ratio(conn: sqlite3.Connection, num: str, den: str) -> tuple[list[date], NDArray[np.float64]]:
    a, b = _log_index(conn, num), _log_index(conn, den)
    days = sorted(set(a) & set(b))
    return days, np.array([a[d] - b[d] for d in days])


def metric_from_db(conn: sqlite3.Connection, m: Metric, today: date) -> dict[str, Any]:
    base: dict[str, Any] = {"key": m.key, "label": m.label, "kind": m.kind, "unit": m.unit}
    try:
        if m.kind == "ratio":
            days, lr = _ratio(conn, *m.source)
            base.update(ratio_metric(days, lr))
        else:
            if len(m.source) == 2:
                days, v = _fred_diff(conn, m.source[0], m.source[1], today)
            else:
                days, v = _fred(conn, m.source[0], today)
            base.update(level_metric(days, v, m.unit))
    except (LookupError, ValueError) as exc:
        base["error"] = str(exc)
    return base


def from_db(conn: sqlite3.Connection, today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    dims = []
    for d in DIMENSIONS:
        head = metric_from_db(conn, d.headline, today)
        dims.append(
            {
                "key": d.key,
                "label": d.label,
                "up_means": d.up_means,
                "headline": head,
                "support": [metric_from_db(conn, m, today) for m in d.support],
                "reading": reading(head),
            }
        )
    return {
        "evidence": "medido (descriptivo: huellas de precio, sin pronóstico ni índice compuesto)",
        "known_at": today.isoformat(),
        "dimensions": dims,
        "method": dict(METHOD),
    }


METHOD: dict[str, str] = {
    "level_pct": "niveles: fracción de toda la historia disponible por debajo del valor de hoy",
    "ratio_pct": (
        "razones: percentil del cambio a 1 año contra los cambios a 1 año del pasado estricto "
        "que no solapan la ventana; el nivel de una razón no tiene significado propio"
    ),
    "reading": "tendencia larga = signo del cambio a 5 años; fase corta = signo a 13 semanas",
    "point_in_time": "FRED tal como se conocía hoy (vintages)",
    "no_forecast": "no es pronóstico ni índice: la etapa del orden mundial es juicio declarado",
}


def _chg(m: dict[str, Any], key: str) -> str:
    v = m.get(f"chg_{key}")
    if v is None:
        return f"{'—':>9}"
    return f"{v:>+8.2f}p" if m["unit"] == "pp" else f"{100 * v:>+8.1f}%"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m marea.world_order", description=__doc__)
    p.add_argument("--as-of", type=date.fromisoformat, default=None)
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    conn = store.connect(DB_PATH)
    try:
        rep = from_db(conn, args.as_of)
    finally:
        conn.close()
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0
    print(f"ORDEN MUNDIAL · conocido al {rep['known_at']} · {rep['evidence']}")
    hdr = "".join(f"{k:>10}" for k, _, _ in HORIZONS)
    for d in rep["dimensions"]:
        r = d["reading"]
        flag = "  <- fase corta contra la tendencia" if r["against_trend"] else ""
        print(f"\n[{d['label']}] sube = {d['up_means']}")
        print(f"  tendencia 5a: {r['long_trend']} · fase 13s: {r['short_phase']}{flag}")
        print(f"  {'métrica':<44}{'valor':>10}{hdr}{'pct':>7}")
        for m in [d["headline"], *d["support"]]:
            if "error" in m:
                print(f"  {m['label']:<44}  {m['error']}")
                continue
            val = "" if m["value"] is None else f"{m['value']:.2f}"
            pct = "—" if m.get("pct") is None else f"{m['pct']:.2f}"
            print(
                f"  {m['label'][:43]:<44}{val:>10}"
                + "".join(_chg(m, k) + " " for k, _, _ in HORIZONS)[:-1]
                + f"{pct:>7}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
