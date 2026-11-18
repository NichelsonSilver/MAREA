from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from marea import scenarios as sc
from marea.data import store
from marea.pit import Period

NOW = datetime(2026, 11, 18, 15, 0, tzinfo=UTC)


def _raw(**kw: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "question": "¿Sigue la rotación defensiva hasta enero?",
        "horizon": "2027-01-13",
        "scenarios": [
            {
                "key": "defensa",
                "label": "Sigue la rotación defensiva",
                "prob": 0.5,
                "conditions": [{"metric": "riesgo:13s", "op": "<", "threshold": 0.0}],
            },
            {
                "key": "riesgo",
                "label": "Vuelve el apetito por riesgo",
                "prob": 0.3,
                "conditions": [
                    {"metric": "riesgo:13s", "op": ">", "threshold": 0.03},
                    {"metric": "fred_chg:DFII10", "op": "<", "threshold": 0.0},
                ],
            },
            {"key": "mixto", "label": "Ninguno de los dos", "prob": 0.2, "residual": True},
        ],
    }
    raw.update(kw)
    return raw


# --------------------------------------------------------------------------- esquema


def test_metricas_se_validan_contra_lo_que_marea_mide() -> None:
    assert sc.parse_metric("ret:spy") == ("ret", "SPY")
    assert sc.parse_metric("rel:gld/tlt") == ("rel", "GLD/TLT")
    assert sc.parse_metric("fred:DFII10") == ("fred", "DFII10")
    for bad in ("fred:NOEXISTE", "ret:AAPL", "rel:SPY", "riesgo:2s", "precio:SPY"):
        with pytest.raises(ValueError):
            sc.parse_metric(bad)


def test_probabilidades_suman_uno_y_escenario_sin_disparador_se_rechaza() -> None:
    raw = _raw()
    raw["scenarios"][0]["prob"] = 0.6
    with pytest.raises(ValueError, match="suman"):
        sc.validate(raw, NOW)
    raw = _raw()
    raw["scenarios"][1]["conditions"] = []
    with pytest.raises(ValueError, match="disparador medible"):
        sc.validate(raw, NOW)
    raw = _raw()
    raw["scenarios"][2]["conditions"] = [{"metric": "ret:SPY", "op": ">", "threshold": 0}]
    with pytest.raises(ValueError, match="residual"):
        sc.validate(raw, NOW)


def test_horizonte_entre_una_semana_y_un_anio() -> None:
    with pytest.raises(ValueError, match="horizonte"):
        sc.validate(_raw(horizon="2026-11-20"), NOW)
    with pytest.raises(ValueError, match="horizonte"):
        sc.validate(_raw(horizon="2028-01-01"), NOW)


def test_add_asigna_id_y_es_solo_agregar(tmp_path: Path) -> None:
    path = tmp_path / "r.jsonl"
    f1 = sc.add(_raw(), path, NOW)
    assert f1.id == "E-20261118-0001" and f1.base_day == date(2026, 11, 17)
    with pytest.raises(ValueError, match="asigna"):
        sc.add(_raw(id="x"), path, NOW)
    with pytest.raises(ValueError, match="supersedes"):
        sc.add(_raw(supersedes="E-nada"), path, NOW)
    f2 = sc.add(_raw(supersedes=f1.id), path, NOW)
    assert f2.id.endswith("0002")
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2


# --------------------------------------------------------------------------- resolución pura


def _forecast() -> sc.Forecast:
    f = sc.validate(_raw(), NOW)
    f.id, f.registered_at_utc = "E-1", NOW.isoformat()
    return f


AFTER = date(2027, 1, 20)


def test_brier_multiclase_y_reparto_parejo() -> None:
    assert sc.brier([0.5, 0.3, 0.2], 0) == pytest.approx(0.25 + 0.09 + 0.04)
    _, uniform, top = sc.grade(_forecast(), "riesgo")
    assert uniform == pytest.approx(2 / 3) and top is False


def test_resuelve_solo_un_escenario_unico() -> None:
    f = _forecast()
    o = sc.resolve_one(f, {"riesgo:13s": -0.05, "fred_chg:DFII10": 0.2}, None, AFTER)
    assert o.status == "resuelto" and o.scenario == "defensa" and o.top_hit
    assert o.brier == pytest.approx(0.25 + 0.09 + 0.04)


def test_residual_ocurre_cuando_no_ocurre_ninguno() -> None:
    o = sc.resolve_one(_forecast(), {"riesgo:13s": 0.01, "fred_chg:DFII10": -0.1}, None, AFTER)
    assert o.status == "resuelto" and o.scenario == "mixto"


def test_antes_del_horizonte_mas_gracia_queda_pendiente() -> None:
    f = _forecast()
    vals = {"riesgo:13s": -0.05, "fred_chg:DFII10": 0.2}
    assert sc.resolve_one(f, vals, None, f.horizon).status == "pendiente"
    assert (
        sc.resolve_one(f, vals, None, f.horizon + timedelta(days=sc.GRACE_DAYS)).status
        == "resuelto"
    )


def test_dato_faltante_no_resuelve() -> None:
    o = sc.resolve_one(_forecast(), {"riesgo:13s": -0.05, "fred_chg:DFII10": None}, None, AFTER)
    assert o.status == "esperando_datos"


def _ambiguous() -> sc.Forecast:
    raw = _raw()
    raw["scenarios"][1]["conditions"] = [{"metric": "riesgo:13s", "op": "<", "threshold": 0.5}]
    f = sc.validate(raw, NOW)
    f.id, f.registered_at_utc = "E-1", NOW.isoformat()
    return f


def test_solapamiento_queda_ambiguo_y_admite_manual() -> None:
    f = _ambiguous()
    vals = {"riesgo:13s": -0.05}
    o = sc.resolve_one(f, vals, None, AFTER)
    assert o.status == "ambiguo" and o.matched == ["defensa", "riesgo"] and o.brier is None
    man = sc.Resolution(
        forecast_id="E-1", scenario="riesgo", note="se cumplieron los dos; pesa más X"
    )
    o = sc.resolve_one(f, vals, man, AFTER)
    assert o.status == "manual" and o.scenario == "riesgo"


def test_manual_no_reescribe_un_resultado_de_los_datos() -> None:
    man = sc.Resolution(forecast_id="E-1", scenario="riesgo", note="quiero que haya sido este")
    o = sc.resolve_one(_forecast(), {"riesgo:13s": -0.05, "fred_chg:DFII10": 0.2}, man, AFTER)
    assert o.status == "resuelto" and o.scenario == "defensa"


def test_summarize_sin_veredicto_bajo_el_piso() -> None:
    f = _forecast()
    o = sc.resolve_one(f, {"riesgo:13s": -0.05, "fred_chg:DFII10": 0.2}, None, AFTER)
    s = sc.summarize([o, sc.Outcome("E-2", "pendiente")])
    assert s["n_graded"] == 1 and s["by_status"] == {"resuelto": 1, "pendiente": 1}
    assert s["skill"] == pytest.approx(1 - 0.38 / (2 / 3))
    assert s["reading"] == "sin veredicto"


# --------------------------------------------------------------------------- datos


class FakeSource(sc.DataSource):
    def __init__(self, days: list[date], rets: dict[str, np.ndarray]) -> None:
        self._panel = (days, rets)
        self.conn = None  # type: ignore[assignment]
        self.bob_db = None


def test_ret_toma_las_sesiones_despues_de_la_base_hasta_el_horizonte() -> None:
    days = [date(2026, 11, 16) + timedelta(days=i) for i in range(5)]
    src = FakeSource(days, {"SPY": np.array([0.5, 0.10, 0.10, 0.0, 0.5]), "TLT": np.zeros(5)})
    # base 17-11: el +50% del 16 y el del 17 no cuentan; el 20 queda fuera del horizonte.
    assert src.ret("SPY", date(2026, 11, 16), date(2026, 11, 19)) == pytest.approx(1.1 * 1.1 - 1)
    assert src.value("rel:SPY/TLT", date(2026, 11, 16), date(2026, 11, 19)) == pytest.approx(0.21)
    src._panel[1]["SPY"][2] = np.nan
    assert src.ret("SPY", date(2026, 11, 16), date(2026, 11, 19)) is None


def test_outcomes_espera_sesiones_posteriores_al_horizonte() -> None:
    f = _forecast()
    days = [f.horizon - timedelta(days=1), f.horizon]
    src = FakeSource(days, {})
    pairs = sc.outcomes([f], src, AFTER)
    assert pairs[0][1].status == "esperando_datos"


def test_fred_se_lee_como_se_conocia_cada_dia(tmp_path: Path) -> None:
    conn = store.connect(tmp_path / "m.db")
    store.replace_periods(
        conn,
        "DFII10",
        [
            Period(date(2026, 11, 13), date(2026, 11, 14), date(2026, 11, 16), 1.80),
            Period(date(2026, 11, 13), date(2026, 11, 17), date(9999, 12, 31), 1.90),  # revisión
            Period(date(2027, 1, 12), date(2027, 1, 13), date(9999, 12, 31), 1.50),
        ],
    )
    store.upsert_series_meta(
        conn,
        {
            "id": "DFII10",
            "title": "t",
            "frequency_short": "D",
            "units_short": "%",
            "observation_start": "2003-01-02",
            "observation_end": "2027-01-12",
            "last_updated": "x",
        },
        date(2026, 11, 14),
        date(2027, 1, 13),
        1,
    )
    src = sc.DataSource(conn, None)
    assert src.fred("DFII10", date(2026, 11, 15)) == pytest.approx(1.80)
    assert src.value("fred_chg:DFII10", date(2026, 11, 17), date(2027, 1, 13)) == pytest.approx(
        -0.40
    )
    assert src.fred("DFII10", date(2027, 6, 1)) is None  # dato de más de 45 días
    conn.close()
