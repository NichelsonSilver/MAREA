# ruff: noqa: E501 — lineas de formato del reporte
"""Registro calificable de escenarios de `/macro`.

    uv run python -m marea.scenarios check --file esc.json   # valida y muestra los disparadores hoy
    uv run python -m marea.scenarios add --file esc.json     # valida y agrega
    uv run python -m marea.scenarios resolve E-20261118-0001 --scenario base --note "..."
    uv run python -m marea.scenarios report [--json]

Los escenarios de `/macro` son **juicio sin respaldo forward**. Este registro
existe para que dejen de ser juicio sin medir: cada análisis guarda sus
escenarios con probabilidad, horizonte y una **regla de resolución hecha con
métricas de MAREA**, y al vencer el horizonte se resuelve solo y se califica
con Brier. Con el tiempo, eso dice cuánto vale la parte de juicio.

El registro es `escenarios/registro.jsonl` en la raíz del repo, versionado y
**solo se agrega**, igual que la bitácora del estratega: un pronóstico no se
edita (se registra otro con `supersedes`), y la resolución automática **no se
guarda**, se recalcula de los datos cada vez.

Reglas, declaradas antes de tener un solo pronóstico:

- **Resolución automática.** Cada escenario trae condiciones (`métrica op
  umbral`) que tienen que cumplirse **todas** al horizonte. Uno puede ser
  `residual` y sin condiciones: ocurre si no ocurre ningún otro. Si se cumple
  exactamente un escenario, ese es el resultado. Si se cumplen cero (sin
  residual) o más de uno, queda **ambiguo**, y solo entonces se acepta una
  resolución manual (`resolve`). Una resolución manual sobre un caso que los
  datos ya resolvieron se rechaza: no se reescribe el resultado.
- **Punto de partida.** Lo conocido el día UTC anterior al registro: cierre de
  ETFs de la sesión previa y FRED tal como se conocía ese día (vintages).
  Lo que pasó el mismo día del registro no cuenta.
- **Al horizonte.** FRED tal como se conocía el día del horizonte (el Brent
  lleva ~1 semana de rezago, y eso queda dentro); ETFs y cripto al cierre de
  la última sesión ≤ horizonte. Se resuelve recién cuando la base tiene
  sesiones posteriores al horizonte y pasaron `GRACE_DAYS`.
- **Calificación.** Brier multiclase Σ(p − o)², 0 es perfecto. Se compara
  contra repartir la probabilidad en partes iguales (1 − 1/k) y se reporta
  cuántas veces acertó el escenario que se dio como más probable.
- **Potencia.** Los análisis semanales con horizonte de 8 semanas se solapan
  y no son independientes. Bajo `MIN_FOR_READING` resueltos el reporte es
  descriptivo y no emite veredicto.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from marea import flow
from marea.config import DB_PATH
from marea.data import store
from marea.data.catalog import BY_ID
from marea.pit import as_of
from marea.strength import Panel

REGISTRY = Path(__file__).resolve().parent.parent.parent.parent / "escenarios" / "registro.jsonl"
GRACE_DAYS = 3
#: Resueltos mínimos para leer el Brier como algo más que anécdota. Es un piso
#: declarado, no una potencia calculada: con solapamiento, n efectivo es menor.
MIN_FOR_READING = 20
#: Una observación de FRED más vieja que esto al horizonte no resuelve nada.
MAX_STALE_DAYS = 45
PROB_TOL = 0.005

Op = Literal[">", "<"]
_METRIC = re.compile(r"^(fred|fred_chg|fred_ret|ret|rel|riesgo):(.+)$")
UNIVERSE = frozenset({*(t for ts in flow.GROUPS.values() for t in ts), *flow.REFERENCES})
RISK_WINDOWS = dict(flow.WINDOWS)

METRIC_HELP = {
    "fred:SID": "nivel de la serie de FRED al horizonte (ej. fred:DFII10, fred:VIXCLS)",
    "fred_chg:SID": "cambio en unidades de la serie desde el registro (pp para tasas)",
    "fred_ret:SID": "cambio relativo desde el registro (0,10 = +10%)",
    "ret:TICKER": "retorno total desde el registro (ETFs del panel de flujo, BTC, ETH)",
    "rel:A/B": "retorno de A contra B desde el registro: (1+rA)/(1+rB) − 1",
    "riesgo:VENTANA": "apetito por riesgo de marea.flow al horizonte (ej. riesgo:13s)",
}


def parse_metric(metric: str) -> tuple[str, str]:
    """Valida la métrica contra lo que MAREA mide y devuelve (tipo, argumento)."""
    m = _METRIC.match(metric.strip())
    if not m:
        raise ValueError(f"métrica {metric!r} desconocida; formatos: {', '.join(METRIC_HELP)}")
    kind, arg = m.group(1), m.group(2).strip()
    if kind.startswith("fred"):
        if arg not in BY_ID:
            raise ValueError(f"{arg} no está en el catálogo de FRED")
    elif kind == "ret":
        arg = arg.upper()
        if arg not in UNIVERSE:
            raise ValueError(f"{arg} no está en el panel de flujo")
    elif kind == "rel":
        a, _, b = arg.upper().partition("/")
        if a not in UNIVERSE or b not in UNIVERSE:
            raise ValueError(f"rel:{arg} necesita dos tickers del panel de flujo")
        arg = f"{a}/{b}"
    elif kind == "riesgo" and arg not in RISK_WINDOWS:
        raise ValueError(f"ventana {arg} inválida; opciones: {sorted(RISK_WINDOWS)}")
    return kind, arg


class Condition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: str
    op: Op
    threshold: float

    @model_validator(mode="after")
    def _metric(self) -> Condition:
        kind, arg = parse_metric(self.metric)
        self.metric = f"{kind}:{arg}"
        return self

    def holds(self, value: float) -> bool:
        return value > self.threshold if self.op == ">" else value < self.threshold


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=r"^[a-z0-9_]{2,30}$")
    label: str = Field(min_length=5, max_length=200)
    prob: float = Field(ge=0.0, le=1.0)
    residual: bool = False
    conditions: list[Condition] = Field(default_factory=list)
    wins: list[str] = Field(default_factory=list)
    loses: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _shape(self) -> Scenario:
        if self.residual and self.conditions:
            raise ValueError(f"{self.key}: el residual no lleva condiciones")
        if not self.residual and not self.conditions:
            raise ValueError(f"{self.key}: un escenario sin disparador medible no se registra")
        return self


class Forecast(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["pronostico"] = "pronostico"
    id: str = ""
    registered_at_utc: str = ""
    question: str = Field(min_length=10, max_length=400)
    horizon: date
    scenarios: list[Scenario] = Field(min_length=2, max_length=5)
    #: Memo o comando de donde salen los escenarios.
    source: str = ""
    notes: str = Field(default="", max_length=800)
    supersedes: str | None = None

    @model_validator(mode="after")
    def _probs(self) -> Forecast:
        total = sum(s.prob for s in self.scenarios)
        if abs(total - 1.0) > PROB_TOL:
            raise ValueError(f"las probabilidades suman {total:.3f}, no 1")
        keys = [s.key for s in self.scenarios]
        if len(set(keys)) != len(keys):
            raise ValueError("claves de escenario repetidas")
        if sum(s.residual for s in self.scenarios) > 1:
            raise ValueError("a lo más un escenario residual")
        return self

    @property
    def base_day(self) -> date:
        """Último día cuyo dato se conocía al registrar: el día UTC anterior."""
        return datetime.fromisoformat(self.registered_at_utc).date() - timedelta(days=1)

    def metrics(self) -> list[str]:
        return sorted({c.metric for s in self.scenarios for c in s.conditions})


class Resolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["resolucion"] = "resolucion"
    forecast_id: str
    scenario: str
    note: str = Field(min_length=10, max_length=800)
    registered_at_utc: str = ""


Line = Forecast | Resolution


def read(path: Path = REGISTRY) -> list[Line]:
    if not path.exists():
        return []
    out: list[Line] = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        raw = json.loads(line)
        try:
            out.append(
                Resolution.model_validate(raw)
                if raw.get("kind") == "resolucion"
                else Forecast.model_validate(raw)
            )
        except ValidationError as exc:
            raise ValueError(f"{path.name}:{n} no cumple el esquema: {exc}") from exc
    return out


def _append(line: Line, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line.model_dump_json() + "\n")


def _now(now: datetime | None) -> datetime:
    return (now or datetime.now(UTC)).replace(microsecond=0)


def validate(raw: dict[str, Any], now: datetime | None = None) -> Forecast:
    if raw.get("id") or raw.get("registered_at_utc"):
        raise ValueError("id y registered_at_utc los asigna el registro")
    f = Forecast.model_validate(raw)
    today = _now(now).date()
    if f.horizon < today + timedelta(days=7) or f.horizon > today + timedelta(days=366):
        raise ValueError("el horizonte va entre 1 semana y 1 año desde hoy")
    return f


def add(raw: dict[str, Any], path: Path = REGISTRY, now: datetime | None = None) -> Forecast:
    """Valida y agrega un pronóstico. `id` y la hora los asigna el registro."""
    f = validate(raw, now)
    existing = [x for x in read(path) if isinstance(x, Forecast)]
    if f.supersedes and f.supersedes not in {x.id for x in existing}:
        raise ValueError(f"supersedes={f.supersedes} no existe en el registro")
    ts = _now(now)
    f.id = f"E-{ts:%Y%m%d}-{len(existing) + 1:04d}"
    f.registered_at_utc = ts.isoformat()
    _append(f, path)
    return f


# --------------------------------------------------------------------------- resolución (PURA)

Values = dict[str, float | None]


@dataclass(frozen=True)
class Outcome:
    forecast_id: str
    status: str  # pendiente | esperando_datos | resuelto | ambiguo | manual
    scenario: str | None = None
    matched: list[str] = field(default_factory=list)
    values: Values = field(default_factory=dict)
    brier: float | None = None
    brier_uniform: float | None = None
    top_hit: bool | None = None
    note: str = ""


def brier(probs: list[float], hit: int) -> float:
    return float(sum((p - (1.0 if i == hit else 0.0)) ** 2 for i, p in enumerate(probs)))


def matched_scenarios(f: Forecast, values: Values) -> list[str] | None:
    """Escenarios cuyas condiciones se cumplen todas; None si falta algún dato."""
    if any(values.get(m) is None for m in f.metrics()):
        return None
    hit = [
        s.key
        for s in f.scenarios
        if not s.residual and all(c.holds(float(values[c.metric])) for c in s.conditions)  # type: ignore[arg-type]
    ]
    if not hit:
        hit = [s.key for s in f.scenarios if s.residual]
    return hit


def grade(f: Forecast, scenario: str) -> tuple[float, float, bool]:
    keys = [s.key for s in f.scenarios]
    probs = [s.prob for s in f.scenarios]
    i = keys.index(scenario)
    k = len(keys)
    return brier(probs, i), 1.0 - 1.0 / k, probs[i] == max(probs)


def resolve_one(
    f: Forecast, values: Values | None, manual: Resolution | None, today: date
) -> Outcome:
    if today < f.horizon + timedelta(days=GRACE_DAYS):
        return Outcome(f.id, "pendiente")
    if values is None:
        return Outcome(f.id, "esperando_datos")
    hit = matched_scenarios(f, values)
    if hit is None:
        return Outcome(f.id, "esperando_datos", values=values)
    if len(hit) == 1:
        b, bu, top = grade(f, hit[0])
        return Outcome(f.id, "resuelto", hit[0], hit, values, b, bu, top)
    if manual is not None:
        b, bu, top = grade(f, manual.scenario)
        return Outcome(f.id, "manual", manual.scenario, hit, values, b, bu, top, manual.note)
    return Outcome(f.id, "ambiguo", None, hit, values)


def summarize(outcomes: list[Outcome]) -> dict[str, Any]:
    graded = [o for o in outcomes if o.brier is not None]
    n = len(graded)
    by_status: dict[str, int] = {}
    for o in outcomes:
        by_status[o.status] = by_status.get(o.status, 0) + 1
    out: dict[str, Any] = {"n_forecasts": len(outcomes), "by_status": by_status, "n_graded": n}
    if n:
        b = np.array([o.brier for o in graded], dtype=float)
        u = np.array([o.brier_uniform for o in graded], dtype=float)
        out |= {
            "brier": float(b.mean()),
            "brier_uniform": float(u.mean()),
            "skill": float(1.0 - b.mean() / u.mean()),
            "top_hits": sum(bool(o.top_hit) for o in graded),
            "n_manual": sum(o.status == "manual" for o in graded),
        }
    out["reading"] = (
        "sin veredicto"
        if n < MIN_FOR_READING
        else (
            "el juicio le gana al reparto parejo"
            if out["skill"] > 0
            else "el juicio no le gana al reparto parejo"
        )
    )
    return out


# --------------------------------------------------------------------------- datos (I/O)


class DataSource:
    """Lee las métricas del registro desde `marea.db` (y `bob.db` para cripto)."""

    def __init__(self, conn: sqlite3.Connection, bob_db: Path | None) -> None:
        self.conn = conn
        self.bob_db = bob_db
        self._panel: Panel | None = None

    def panel(self) -> Panel:
        if self._panel is None:
            self._panel = flow.load_panel(self.conn, self.bob_db)
        return self._panel

    def last_session(self) -> date | None:
        days, _ = self.panel()
        return days[-1] if days else None

    def fred(self, sid: str, known: date) -> float | None:
        meta = store.series_meta(self.conn, sid)
        if meta is None:
            return None
        fv = date.fromisoformat(meta["first_vintage"]) if meta["first_vintage"] else None
        pts = [
            p
            for p in as_of(store.load_periods(self.conn, sid), known, fv, meta["fallback_lag_days"])
            if p.value is not None
        ]
        if not pts or (known - pts[-1].obs_date).days > MAX_STALE_DAYS:
            return None
        return float(pts[-1].value)  # type: ignore[arg-type]

    def ret(self, ticker: str, start: date, end: date) -> float | None:
        """Retorno total de las sesiones en (start, end]."""
        days, rets = self.panel()
        r = rets.get(ticker)
        if r is None:
            return None
        sel = np.array([start < d <= end for d in days])
        x = r[sel]
        if x.size == 0 or not np.all(np.isfinite(x)):
            return None
        return float(np.prod(1.0 + x) - 1.0)

    def risk(self, window: str, end: date) -> float | None:
        days, rets = self.panel()
        k = sum(d <= end for d in days)
        if k == 0:
            return None
        rep = flow.build((days[:k], {t: r[:k] for t, r in rets.items()}))
        score = rep["risk_appetite"][window]["score"]
        return None if score is None else float(score)

    def value(self, metric: str, base: date, end: date) -> float | None:
        kind, arg = parse_metric(metric)
        if kind == "fred":
            return self.fred(arg, end)
        if kind in ("fred_chg", "fred_ret"):
            a, b = self.fred(arg, base), self.fred(arg, end)
            if a is None or b is None:
                return None
            return b - a if kind == "fred_chg" else (b / a - 1.0 if a else None)
        if kind == "ret":
            return self.ret(arg, base, end)
        if kind == "rel":
            na, _, nb = arg.partition("/")
            ra, rb = self.ret(na, base, end), self.ret(nb, base, end)
            return None if ra is None or rb is None else (1.0 + ra) / (1.0 + rb) - 1.0
        return self.risk(arg, end)

    def values(self, f: Forecast, end: date) -> Values:
        return {m: self.value(m, f.base_day, end) for m in f.metrics()}


def outcomes(
    lines: list[Line], src: DataSource | None, today: date
) -> list[tuple[Forecast, Outcome]]:
    forecasts = [x for x in lines if isinstance(x, Forecast)]
    superseded = {f.supersedes for f in forecasts if f.supersedes}
    manual = {r.forecast_id: r for r in lines if isinstance(r, Resolution)}
    last = src.last_session() if src is not None else None
    out = []
    for f in forecasts:
        if f.id in superseded:
            continue
        due = today >= f.horizon + timedelta(days=GRACE_DAYS)
        values = (
            src.values(f, f.horizon)
            if (due and src is not None and last is not None and last > f.horizon)
            else None
        )
        out.append((f, resolve_one(f, values, manual.get(f.id), today)))
    return out


def resolve(
    forecast_id: str,
    scenario: str,
    note: str,
    src: DataSource,
    path: Path = REGISTRY,
    now: datetime | None = None,
) -> Resolution:
    """Resolución manual: solo para un pronóstico vencido que los datos dejaron ambiguo."""
    forecast_id = forecast_id.strip()
    lines = read(path)
    f = next((x for x in lines if isinstance(x, Forecast) and x.id == forecast_id), None)
    if f is None:
        raise ValueError(f"{forecast_id} no existe en el registro")
    if scenario not in {s.key for s in f.scenarios}:
        raise ValueError(f"{scenario} no es un escenario de {forecast_id}")
    if any(isinstance(x, Resolution) and x.forecast_id == forecast_id for x in lines):
        raise ValueError(f"{forecast_id} ya tiene resolución manual")
    today = _now(now).date()
    o = dict((x.id, y) for x, y in outcomes(lines, src, today))[forecast_id]
    if o.status != "ambiguo":
        raise ValueError(f"{forecast_id} está '{o.status}': solo se resuelve a mano lo ambiguo")
    r = Resolution(
        forecast_id=forecast_id,
        scenario=scenario,
        note=note,
        registered_at_utc=_now(now).isoformat(),
    )
    _append(r, path)
    return r


# --------------------------------------------------------------------------- CLI


def _fmt(v: float | None) -> str:
    return "—" if v is None else f"{v:+.4f}"


def _with_source(fn: Callable[[DataSource], int]) -> int:
    conn = store.connect(DB_PATH)
    try:
        bob = DB_PATH.parent / "bob.db"
        return fn(DataSource(conn, bob if bob.exists() else None))
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m marea.scenarios", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("check", "add"):
        s = sub.add_parser(name)
        s.add_argument("--file", type=Path, required=True)
    r = sub.add_parser("resolve")
    r.add_argument("forecast_id")
    r.add_argument("--scenario", required=True)
    r.add_argument("--note", required=True)
    rep = sub.add_parser("report")
    rep.add_argument("--json", action="store_true")
    rep.add_argument("--today", type=date.fromisoformat, default=None)
    args = p.parse_args(argv)

    try:
        if args.cmd in ("check", "add"):
            raw = json.loads(args.file.read_text(encoding="utf-8"))
            if args.cmd == "add":
                f = add(raw)
                print(f"registrado {f.id} · horizonte {f.horizon} · base {f.base_day}")
                return 0
            f = validate(raw)
            f.registered_at_utc = _now(None).isoformat()

            def show(src: DataSource) -> int:
                print(f"VÁLIDO · horizonte {f.horizon} · base {f.base_day} · disparadores hoy:")
                today = _now(None).date()
                for m in f.metrics():
                    kind = parse_metric(m)[0]
                    now_v = (
                        src.value(m, f.base_day - timedelta(days=28), today)
                        if kind not in ("fred", "riesgo")
                        else src.value(m, f.base_day, today)
                    )
                    print(
                        f"  {m:<28} {'nivel hoy' if kind in ('fred', 'riesgo') else 'últimas 4 sem'}: {_fmt(now_v)}"
                    )
                for s in f.scenarios:
                    cond = (
                        " y ".join(f"{c.metric} {c.op} {c.threshold}" for c in s.conditions)
                        or "ninguno de los otros"
                    )
                    print(f"  [{s.prob:.0%}] {s.key}: {cond}")
                return 0

            return _with_source(show)
        if args.cmd == "resolve":

            def do(src: DataSource) -> int:
                res = resolve(args.forecast_id, args.scenario, args.note, src)
                print(f"resuelto a mano {res.forecast_id} -> {res.scenario}")
                return 0

            return _with_source(do)

        today = args.today or _now(None).date()

        def report(src: DataSource) -> int:
            pairs = outcomes(read(), src, today)
            summary = summarize([o for _, o in pairs])
            if args.json:
                print(
                    json.dumps(
                        {
                            "summary": summary,
                            "forecasts": [
                                {
                                    "id": f.id,
                                    "question": f.question,
                                    "horizon": f.horizon.isoformat(),
                                    **o.__dict__,
                                }
                                for f, o in pairs
                            ],
                        },
                        ensure_ascii=False,
                        indent=2,
                        default=str,
                    )
                )
                return 0
            print(
                f"ESCENARIOS DE /macro · al {today} · {summary['n_forecasts']} pronósticos · {summary['by_status']}"
            )
            for f, o in pairs:
                extra = (
                    f" -> {o.scenario}  Brier {o.brier:.3f} (parejo {o.brier_uniform:.3f})"
                    if o.brier is not None
                    else ""
                )
                if o.status == "ambiguo":
                    extra = f" -> se cumplen {o.matched or 'ninguno'}: resolver a mano"
                print(f"  {f.id}  {f.horizon}  {o.status:<15}{extra}")
                print(f"      {f.question[:90]}")
            if summary["n_graded"]:
                print(
                    f"\nBrier medio {summary['brier']:.3f} contra {summary['brier_uniform']:.3f} del reparto parejo"
                    f" (skill {summary['skill']:+.2f}); el más probable acertó {summary['top_hits']} de {summary['n_graded']}"
                    f"; {summary['n_manual']} a mano."
                )
            print(
                f"Lectura: {summary['reading']} (piso declarado: {MIN_FOR_READING} resueltos, con solapamiento)."
            )
            return 0

        return _with_source(report)
    except (ValueError, LookupError, ValidationError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
