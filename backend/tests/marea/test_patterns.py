import numpy as np
import pytest

from marea.patterns import HSParams, detect_hs_top, prior_sma

# Hombro 110, valle 100, cabeza 120, valle 102, hombro 111, y caída.
_KNOTS = [(0, 90), (8, 110), (14, 100), (22, 120), (30, 102), (38, 111), (46, 92), (60, 95)]


def _hs_series(head: float = 120.0):
    x = np.arange(61)
    xs, ys = zip(*[(j, head if j == 22 else y) for j, y in _KNOTS], strict=True)
    mid = np.interp(x, xs, ys)
    return mid + 0.5, mid - 0.5


def test_detecta_el_hch_y_su_cuello():
    h, lo = _hs_series()
    ev = detect_hs_top(h, lo)
    assert len(ev) == 1
    e = ev[0]
    assert (e.h1.j, e.h2.j, e.h3.j) == (8, 22, 38)
    assert e.neckline == pytest.approx(99.5)
    assert e.confirm == 38 + HSParams().k


def test_no_existe_antes_de_confirmar_el_hombro_derecho():
    h, lo = _hs_series()
    k = HSParams().k
    assert detect_hs_top(h[: 38 + k], lo[: 38 + k]) == []
    assert len(detect_hs_top(h[: 38 + k + 1], lo[: 38 + k + 1])) == 1


def test_mutar_el_futuro_no_cambia_los_eventos_ya_conocidos():
    h, lo = _hs_series()
    h2, lo2 = h.copy(), lo.copy()
    h2[45:] *= 2.0
    lo2[45:] *= 0.5
    a = detect_hs_top(h[:45], lo[:45])
    b = [e for e in detect_hs_top(h2, lo2) if e.confirm < 45]
    assert a == b


def test_cabeza_sin_exceso_no_es_hch():
    # La cabeza supera al hombro derecho por menos del 0,5% exigido.
    h, lo = _hs_series(head=111.3)
    assert detect_hs_top(h, lo) == []


def test_hombros_muy_distintos_no_son_hch():
    h, lo = _hs_series()
    assert detect_hs_top(h, lo, HSParams(max_shoulder_diff=0.001)) == []


def test_vela_envolvente_entra_con_los_dos_pivotes():
    h, lo = _hs_series()
    h, lo = h.copy(), lo.copy()
    # La vela del hombro derecho también es el mínimo de su vecindad.
    lo[38] = 95.0
    ev = detect_hs_top(h, lo)
    assert ev and ev[0].h3.j == 38


def test_prior_sma_no_incluye_la_vela_actual():
    c = np.arange(1.0, 11.0)
    s = prior_sma(c, 3)
    assert np.isnan(s[:3]).all()
    assert s[3] == pytest.approx((1 + 2 + 3) / 3)
    assert s[9] == pytest.approx((7 + 8 + 9) / 3)
