import numpy as np
import pytest

from bob.signals.technical import LONG, OrderBlockTouch
from marea.ob_study import PRIMARY, Sym, ma99_ok, ob_records, placebo_records, summarize

DAY = 86_400_000


def _sym(n=60, px=100.0, touches=(), elig=None):
    o = np.full(n, px)
    level = np.full(n, 90.0)
    rising = np.ones(n, dtype=bool)
    return Sym(
        "XUSDT",
        np.arange(n, dtype=np.int64) * DAY,
        o.copy(),
        o + 1.0,
        o - 1.0,
        o.copy(),
        np.ones(n, dtype=bool) if elig is None else elig,
        np.full(n, 2.0),
        list(touches),
        level,
        rising,
    )


def _touch(idx, top=100.0, bottom=96.0):
    return OrderBlockTouch(
        idx=idx, direction=LONG, top=top, bottom=bottom, created_idx=idx - 5, atr=2.0
    )


def test_toque_llega_al_objetivo_2r_neto_de_costos():
    s = _sym(touches=[_touch(20)])
    s.h[23] = 110.0  # entrada 100, stop 95,8 (riesgo 4,2), objetivo 108,4
    (r,) = ob_records([s], PRIMARY, LONG)
    assert r.kind == "target" and r.exit == pytest.approx(108.4)
    days = 4
    assert r.r == pytest.approx((0.084 - 0.0014 - 0.0003 * days) / 0.042)
    assert not r.stop_beyond_liq10 and not r.liq10


def test_solo_cuenta_si_es_elegible_en_el_toque_y_el_filtro_ma99():
    elig = np.ones(60, dtype=bool)
    elig[20] = False
    s = _sym(touches=[_touch(20), _touch(30)], elig=elig)
    assert [r.open_time // DAY for r in ob_records([s], PRIMARY, LONG)] == [30]
    s.rising[30] = False
    assert ob_records([s], PRIMARY, LONG, keep=ma99_ok) == []
    assert not ma99_ok(s, 0)


def test_stop_detras_de_la_liquidacion_a_10x():
    s = _sym(touches=[_touch(20, top=100.0, bottom=85.0)])
    s.low[22] = 80.0  # pasa la liquidación (~90,45) antes del stop (84,8)
    (r,) = ob_records([s], PRIMARY, LONG)
    assert r.stop_beyond_liq10 and r.liq10 and r.kind == "stop"


def test_placebo_usa_solo_dias_elegibles_y_es_reproducible():
    elig = np.zeros(60, dtype=bool)
    elig[40:] = True
    s = _sym(touches=[_touch(45)], elig=elig)
    a = placebo_records([s], PRIMARY, LONG)
    b = placebo_records([s], PRIMARY, LONG)
    assert a == b and len(a) == 1
    assert a[0].open_time // DAY >= 41  # se sorteó i ≥ 40 y entra en i + 1


def test_resumen():
    s = _sym(touches=[_touch(20), _touch(40)])
    recs = ob_records([s], PRIMARY, LONG)
    out = summarize(recs)
    assert out["n"] == 2 and out["time_rate"] == 1.0
    assert summarize([]) == {"n": 0}
