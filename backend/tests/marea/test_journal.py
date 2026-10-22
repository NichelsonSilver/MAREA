from datetime import UTC, datetime

import numpy as np
import pytest

from marea.journal import Plan, add, aggregate, decide, decisions, live_plans, read, resolve

DAY = 86_400_000
T0 = datetime(2026, 10, 1, tzinfo=UTC)


def _raw(**kw):
    base = dict(
        symbol="ethusdt",
        direction="long",
        order="limite",
        entry=100.0,
        stop=95.0,
        targets=[110.0],
        horizon_days=7,
        valid_days=5,
        capital=500.0,
        risk=0.01,
        leverage=10,
        thesis="long en la MA99 tras HCH de techo",
    )
    return base | kw


def _plan(registered="2026-10-01T15:00:00+00:00", **kw):
    return Plan.model_validate(_raw(**kw) | {"id": "P-1", "registered_at_utc": registered})


def _candles(n=20, px=102.0):
    ot = np.int64(T0.timestamp() * 1000) + np.arange(n, dtype=np.int64) * DAY
    o = np.full(n, px)
    return ot, o.copy(), o + 1.0, o - 1.0, o.copy()


def test_geometria_invalida_se_rechaza():
    with pytest.raises(ValueError, match="stop"):
        Plan.model_validate(_raw(stop=101.0))
    with pytest.raises(ValueError, match="objetivo"):
        Plan.model_validate(_raw(targets=[99.0]))
    assert Plan.model_validate(_raw(direction="short", stop=105, targets=[80, 90])).targets == [
        90,
        80,
    ]


def test_add_asigna_id_y_hora_y_no_los_acepta(tmp_path):
    path = tmp_path / "planes.jsonl"
    p = add(_raw(), path, now=T0)
    assert p.id == "P-20261001-0001" and p.symbol == "ETHUSDT"
    with pytest.raises(ValueError, match="asigna"):
        add(_raw() | {"id": "X"}, path)
    with pytest.raises(ValueError, match="no existe"):
        add(_raw(supersedes="P-nada"), path)
    p2 = add(_raw(supersedes=p.id), path, now=T0)
    decide(p2.id, True, path=path, now=T0)
    lines = read(path)
    assert [x.id for x in live_plans(lines)] == [p2.id]
    assert decisions(lines) == {p2.id: True}
    with pytest.raises(ValueError):
        decide("P-otro", False, path=path)


def test_lo_que_paso_el_dia_del_registro_no_cuenta():
    ot, o, h, lo, c = _candles()
    lo[0] = 90.0  # el límite y el stop se tocaron ANTES de registrar (vela del día 0)
    x = resolve(_plan(), ot, o, h, lo, c)
    assert x.status == "sin_llenar"


def test_limite_llenado_y_salida_en_objetivo():
    ot, o, h, lo, c = _candles()
    lo[2] = 99.5
    h[4] = 111.0
    x = resolve(_plan(), ot, o, h, lo, c)
    assert x.status == "cerrado" and x.fill == 100.0 and x.reason == "objetivo" and x.exit == 110.0
    cost = 100.0 * (0.0014 + 0.0003 * 3)
    assert x.r == pytest.approx((10.0 - cost) / 5.0)
    assert x.usd == pytest.approx(x.r * 5.0)
    assert x.targets_hit == [True] and not x.liq_crossed


def test_entrada_y_stop_en_la_misma_vela_es_stop():
    ot, o, h, lo, c = _candles()
    lo[2] = 94.0
    x = resolve(_plan(), ot, o, h, lo, c)
    assert x.reason == "stop" and x.exit == 95.0 and x.r < -1.0


def test_pendiente_abierto_y_vencido():
    ot, o, h, lo, c = _candles(n=3)
    assert resolve(_plan(), ot, o, h, lo, c).status == "pendiente"
    lo[2] = 99.0
    assert resolve(_plan(), ot, o, h, lo, c).status == "abierto"
    ot, o, h, lo, c = _candles()
    assert resolve(_plan(), ot, o, h, lo, c).status == "sin_llenar"


def test_mercado_llena_a_la_apertura_y_sale_por_tiempo():
    ot, o, h, lo, c = _candles()
    c[7] = 104.0
    x = resolve(_plan(order="mercado", targets=[]), ot, o, h, lo, c)
    assert x.fill == 102.0 and x.reason == "tiempo" and x.exit == 104.0


def test_short_por_ruptura():
    ot, o, h, lo, c = _candles(px=98.0)
    p = _plan(direction="short", order="stop", entry=97.0, stop=101.0, targets=[90.0])
    lo[3] = 96.0
    h[5] = 102.0
    x = resolve(p, ot, o, h, lo, c)
    assert x.fill == 97.0 and x.reason == "stop" and x.exit == 101.0


def test_hueco_en_las_velas():
    ot, o, h, lo, c = _candles()
    ot[4:] += DAY
    lo[2] = 99.5
    assert resolve(_plan(), ot, o, h, lo, c).status == "hueco"


def test_cruce_de_liquidacion_por_gap():
    ot, o, h, lo, c = _candles()
    lo[2] = 99.5
    o[4], lo[4] = 85.0, 84.0  # gap por debajo de la liquidación a 10x (~90,45)
    x = resolve(_plan(), ot, o, h, lo, c)
    assert x.reason == "stop" and x.exit == 85.0 and x.liq_crossed


def test_agregado_y_potencia():
    ot, o, h, lo, c = _candles()
    lo[2] = 99.5
    h[4] = 111.0
    x = resolve(_plan(), ot, o, h, lo, c)
    a = aggregate([x] * 12)
    assert a["n_closed"] == 12 and a["mean_r"] == pytest.approx(x.r)
    assert aggregate([]) == {"n_closed": 0}


def test_decide_tolera_el_fin_de_linea_de_windows(tmp_path):
    path = tmp_path / "planes.jsonl"
    p = add(_raw(), path, now=T0)
    d = decide(p.id + "\r", False, path=path, now=T0)
    assert d.plan_id == p.id and decisions(read(path)) == {p.id: False}
