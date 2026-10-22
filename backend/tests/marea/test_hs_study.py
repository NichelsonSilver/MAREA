import numpy as np
import pytest

from marea.hs_study import (
    TradeRule,
    b1_entries,
    bootstrap_diff,
    bootstrap_mean,
    iso_week,
    liquidation_10x,
    longest_losing_streak,
    make_trade,
    placebo_candidates,
    prior_atr,
    rising_level,
    simulate,
    summarize,
    thesis_entries,
)
from marea.patterns import HSEvent, Pivot

RULE = TradeRule()


def _flat(n=20, px=100.0):
    o = np.full(n, px)
    return o.copy(), o + 1.0, o - 1.0, o.copy()


def test_stop_en_la_vela_de_entrada_cuenta_como_stop():
    o, h, lo, c = _flat()
    lo[5] = 90.0
    assert simulate(o, h, lo, c, 5, 100.0, 5.0, "long", RULE) == (95.0, 5, "stop")


def test_gap_bajo_el_stop_sale_a_la_apertura():
    o, h, lo, c = _flat()
    o[7], lo[7] = 80.0, 79.0
    assert simulate(o, h, lo, c, 5, 100.0, 5.0, "long", RULE) == (80.0, 7, "stop")


def test_salida_por_tiempo_al_cierre_de_entrada_mas_seis():
    o, h, lo, c = _flat()
    c[11] = 103.0
    assert simulate(o, h, lo, c, 5, 100.0, 5.0, "long", RULE) == (103.0, 11, "time")


def test_objetivo_y_stop_en_la_misma_vela_es_stop():
    o, h, lo, c = _flat()
    h[7], lo[7] = 120.0, 90.0
    rule = TradeRule(target_r=2.0)
    assert simulate(o, h, lo, c, 5, 100.0, 5.0, "long", rule) == (95.0, 7, "stop")
    lo[7] = 99.0
    assert simulate(o, h, lo, c, 5, 100.0, 5.0, "long", rule) == (110.0, 7, "target")


def test_serie_cortada_no_resuelve():
    o, h, lo, c = _flat(n=9)
    assert simulate(o, h, lo, c, 5, 100.0, 5.0, "long", RULE) is None
    o, h, lo, c = _flat()
    c[8] = np.nan
    assert simulate(o, h, lo, c, 5, 100.0, 5.0, "long", RULE) is None


def test_short_es_el_espejo_del_long():
    o, h, lo, c = _flat()
    h[7] = 110.0
    assert simulate(o, h, lo, c, 5, 100.0, 5.0, "short", RULE) == (105.0, 7, "stop")
    c[11] = 96.0
    h[7] = 101.0
    assert simulate(o, h, lo, c, 5, 100.0, 5.0, "short", RULE) == (96.0, 11, "time")


def test_r_neto_descuenta_costo_y_funding():
    o, h, lo, c = _flat()
    c[11] = 105.0
    atr = np.full(20, 5.0 / 1.1)
    t = make_trade("X", np.arange(20) * 86_400_000, o, h, lo, c, atr, 5, 100.0, "long", RULE)
    cost = 100.0 * (0.0014 + 0.0003 * 7)
    assert t is not None and t.r == pytest.approx((5.0 - cost) / 5.0)
    assert not t.liq10 and not t.stop_beyond_liq10


def test_liquidacion_10x_por_gap_y_por_stop_detras():
    liq = liquidation_10x(100.0, "long")
    assert liq == pytest.approx(90.0 / 0.995)
    o, h, lo, c = _flat()
    o[7], lo[7] = 85.0, 84.0  # gap que atraviesa la liquidación
    atr = np.full(20, 5.0 / 1.1)
    t = make_trade("X", np.arange(20), o, h, lo, c, atr, 5, 100.0, "long", RULE)
    assert t is not None and t.liq10 and not t.stop_beyond_liq10
    o, h, lo, c = _flat()
    lo[7] = 89.0  # stop a 15%: la liquidación llega antes
    t = make_trade("X", np.arange(20), o, h, lo, c, np.full(20, 15.0 / 1.1), 5, 100.0, "long", RULE)
    assert t is not None and t.stop_beyond_liq10 and t.liq10


def test_atr_previo_no_usa_la_vela_actual():
    h = np.array([11.0, 11, 11, 50])
    lo = np.array([9.0, 9, 9, 9])
    c = np.array([10.0, 10, 10, 10])
    a = prior_atr(h, lo, c, n=2)
    assert a[3] == pytest.approx(2.0)


def _event(confirm):
    p = Pivot(0, "H", 1.0)
    return HSEvent(confirm, p, p, p, p, p, 1.0)


def test_tesis_toma_el_primer_toque_y_exige_pendiente_en_ese():
    n = 40
    c = np.full(n, 110.0)
    o = c.copy()
    lo = c - 1
    level = np.full(n, 100.0)
    elig = np.ones(n, dtype=bool)
    rising = np.ones(n, dtype=bool)
    lo[15], o[15] = 99.0, 101.0
    lo[18] = 95.0
    assert thesis_entries([_event(10)], o, lo, c, elig, level, rising) == [(15, 100.0)]
    rising[15] = False  # el primer toque falla la pendiente: no hay trade
    assert thesis_entries([_event(10)], o, lo, c, elig, level, rising) == []
    rising[15] = True
    o[15] = 98.0  # abre bajo el nivel: se llena a la apertura
    assert thesis_entries([_event(10), _event(12)], o, lo, c, elig, level, rising) == [(15, 98.0)]


def test_b1_respeta_el_enfriamiento():
    n = 60
    c = np.full(n, 110.0)
    lo = c - 1
    level = np.full(n, 100.0)
    for j in (10, 20, 35):
        lo[j] = 99.0
    ones = np.ones(n, dtype=bool)
    assert [j for j, _ in b1_entries(c, lo, c, ones, level, ones)] == [10, 35]


def test_placebo_exige_sobre_la_media_y_pendiente():
    c = np.array([110.0, 90, 110, 110])
    level = np.full(4, 100.0)
    ones = np.ones(4, dtype=bool)
    assert placebo_candidates(c, ones, level, ones).tolist() == [1, 3]
    assert rising_level(np.arange(12.0), lag=10).tolist() == [False] * 10 + [True, True]


def test_semana_iso_arranca_el_lunes():
    day = 86_400_000
    # 1970-01-04 domingo y 1970-01-05 lunes
    assert iso_week(np.array([3 * day, 4 * day])).tolist() == [0, 1]


def test_bootstrap_de_constante_y_diferencia():
    r = np.full(50, 0.3)
    w = np.arange(50) // 5
    lo, hi = bootstrap_mean(r, w, n_boot=200)
    assert lo == pytest.approx(0.3) and hi == pytest.approx(0.3)
    dlo, dhi = bootstrap_diff(r, w, r - 0.1, w, n_boot=200)
    assert dlo == pytest.approx(0.1) and dhi == pytest.approx(0.1)


def test_resumen_y_racha():
    assert longest_losing_streak(np.array([1, -1, -1, 2, -1, -1, -1])) == 3
    o, h, lo, c = _flat()
    atr = np.full(20, 5.0 / 1.1)
    t = make_trade("X", np.arange(20) * 86_400_000 * 7, o, h, lo, c, atr, 5, 100.0, "long", RULE)
    assert t is not None
    s = summarize([t, t])
    assert s["n"] == 2 and s["stop_rate"] == 0.0 and s["mean_r"] == pytest.approx(t.r)
    assert summarize([]) == {"n": 0}
