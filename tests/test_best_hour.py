"""Tests unitarios para core/analysis/best_hour.py.

Usa score_fn mock para ser completamente independiente del motor de scoring real.
No requiere core.scoring.engine ni ningún archivo externo.

Correr con:
    python -m pytest tests/test_best_hour.py -v
    python tests/test_best_hour.py
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import math
from datetime import date, datetime, timezone, timedelta
from typing import List
from zoneinfo import ZoneInfo

from core.scoring.models import (
    ForecastHour, SwellData, WindData, TideData,
    ScoreBreakdown, SpotConfig,
)
from unittest.mock import patch

from core.analysis.best_hour import (
    calcular_mejor_hora,
    calcular_mejor_hora_detallado,
    calcular_ranking_dia,
    BestHourResult,
    RankedHour,
    MOTIVO_SIN_FORECAST,
    MOTIVO_SOLO_NOCTURNO_O_PASADO,
    MOTIVO_FALLO_SOLAR,
    MOTIVO_SCORING_FALLO,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_spot(lat=-38.0, lon=-57.5, tz="America/Argentina/Buenos_Aires") -> SpotConfig:
    return SpotConfig(
        key="test_spot",
        nombre="Test Spot",
        ciudad="Mar del Plata",
        pais="AR",
        region="buenos_aires",
        lat=lat,
        lon=lon,
        orientacion_costa_deg=95.0,
        tolerancia_swell_deg=45.0,
        tipo_break="beach",
        fondo="arena",
        marea_min_m=0.4,
        marea_max_m=1.6,
        marea_tipo_efecto="mid_better",
        swell_altura_min=0.5,
        swell_altura_max=3.0,
        swell_periodo_min=7.0,
        viento_max_offshore=35.0,
        viento_max_onshore=15.0,
        tz=tz,
    )


def make_hour(ts: datetime, score_override: float = None) -> ForecastHour:
    """Crea un ForecastHour con datos genéricos. score_override se usa en el mock."""
    h = ForecastHour(
        timestamp=ts,
        swell=SwellData(altura_m=1.5, periodo_s=12.0, direccion_deg=95.0),
        wind=WindData(velocidad_kmh=15.0, rafaga_kmh=20.0, direccion_deg=180.0),
        tide=TideData(nivel_m=0.8, fuente="proxy_msl"),
    )
    # Guardamos el score en un atributo dinámico para el mock
    h._test_score = score_override
    return h


def make_breakdown(score: float) -> ScoreBreakdown:
    return ScoreBreakdown(
        score_energia=score,
        score_periodo=score,
        score_dir_swell=score,
        score_viento=score,
        score_marea=score,
        score_total=score,
        energia_proxy=score * 20,
        flags_positivos=["Test flag"],
        flags_negativos=[],
        flags_neutros=[],
    )


def mock_score_fn(scores: dict):
    """
    Retorna una función de scoring que usa el timestamp como clave.
    scores: {datetime_utc: float (0.0–1.0)}
    """
    def _fn(hour: ForecastHour, spot: SpotConfig) -> ScoreBreakdown:
        score = scores.get(hour.timestamp, 0.5)
        return make_breakdown(score)
    return _fn


def make_forecast_dia(
    fecha_local: date,
    tz_str: str = "America/Argentina/Buenos_Aires",
    horas: int = 24,
    scores_por_hora: dict = None,  # {hora_local (int): score (float)}
) -> List[ForecastHour]:
    """
    Genera un forecast para un día completo (todas las horas, UTC-aware).
    scores_por_hora: {0: 0.3, 8: 0.9, 12: 0.7, ...}
    """
    tz = ZoneInfo(tz_str)
    medianoche_local = datetime(fecha_local.year, fecha_local.month, fecha_local.day,
                                 0, 0, tzinfo=tz)
    result = []
    for i in range(horas):
        dt_local = medianoche_local + timedelta(hours=i)
        dt_utc = dt_local.astimezone(timezone.utc)
        score = (scores_por_hora or {}).get(i, 0.5)
        h = make_hour(dt_utc)
        h._test_score = score
        result.append(h)
    return result


def scores_dict_from_forecast(forecast: List[ForecastHour]) -> dict:
    """Extrae el diccionario {timestamp_utc: score} de un forecast de test."""
    return {h.timestamp: getattr(h, '_test_score', 0.5) for h in forecast}


def ahora_antes_del_amanecer(fecha: date, tz_str: str = "America/Argentina/Buenos_Aires") -> datetime:
    """
    Timestamp 'ahora' determinista para tests: medianoche local de 'fecha',
    garantizado anterior a cualquier hora de luz solar de ese día.

    calcular_mejor_hora() ahora filtra horas ya pasadas respecto de 'ahora'
    (regresión #11) — sin esto, fixtures con fecha fija en el pasado (ej.
    2025-01-15) quedarían con todas sus horas filtradas por comparación
    contra el reloj real, rompiendo tests que no tienen nada que ver con
    ese fix. Se inyecta explícitamente en vez de dejar el default
    (datetime.now(timezone.utc)) para que estos tests no dependan del
    momento real en que corren.
    """
    tz = ZoneInfo(tz_str)
    medianoche_local = datetime(fecha.year, fecha.month, fecha.day, 0, 0, tzinfo=tz)
    return medianoche_local.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Tests de calcular_mejor_hora
# ---------------------------------------------------------------------------

FECHA_TEST = date(2025, 1, 15)  # Verano AR, muchas horas de luz
SPOT_MDQ = make_spot()
AHORA_TEST = ahora_antes_del_amanecer(FECHA_TEST)


def test_mejor_hora_retorna_best_hour_result():
    forecast = make_forecast_dia(FECHA_TEST, scores_por_hora={8: 0.8, 10: 0.9, 14: 0.6})
    sf = mock_score_fn(scores_dict_from_forecast(forecast))
    result = calcular_mejor_hora(forecast, SPOT_MDQ, FECHA_TEST, score_fn=sf, ahora=AHORA_TEST)
    assert result is not None
    assert isinstance(result, BestHourResult)


def test_mejor_hora_elige_mayor_score():
    """Debe elegir la hora con score más alto."""
    scores = {8: 0.4, 10: 0.95, 12: 0.7, 14: 0.5}
    forecast = make_forecast_dia(FECHA_TEST, scores_por_hora=scores)
    sf = mock_score_fn(scores_dict_from_forecast(forecast))

    result = calcular_mejor_hora(forecast, SPOT_MDQ, FECHA_TEST, score_fn=sf, ahora=AHORA_TEST)
    assert result is not None

    tz = ZoneInfo("America/Argentina/Buenos_Aires")
    hora_local = result.hour.timestamp.astimezone(tz).hour
    assert hora_local == 10, f"Hora con mayor score esperada 10h, got {hora_local}h"


def test_mejor_hora_score_100_consistente():
    """score_100 debe ser round(score_total * 100)."""
    scores = {9: 0.78}
    forecast = make_forecast_dia(FECHA_TEST, scores_por_hora=scores)
    sf = mock_score_fn(scores_dict_from_forecast(forecast))

    result = calcular_mejor_hora(forecast, SPOT_MDQ, FECHA_TEST, score_fn=sf, ahora=AHORA_TEST)
    assert result is not None
    assert result.score_100 == result.breakdown.score_100
    assert result.score_100 == 78


def test_mejor_hora_rank_siempre_1():
    """rank_en_dia siempre debe ser 1 (es la mejor)."""
    forecast = make_forecast_dia(FECHA_TEST)
    sf = mock_score_fn(scores_dict_from_forecast(forecast))
    result = calcular_mejor_hora(forecast, SPOT_MDQ, FECHA_TEST, score_fn=sf, ahora=AHORA_TEST)
    assert result is not None
    assert result.rank_en_dia == 1


def test_mejor_hora_excluye_noche():
    """
    Horas nocturnas (< sunrise o > sunset) NO deben ser candidatas.
    En enero MDP: sunrise ~09:30 AR, sunset ~20:30 AR.
    Una hora a las 03:00 AR no debe ganar aunque tenga el score más alto.
    """
    scores = {3: 0.99, 10: 0.70}  # hora 3 nocturna ganaría si no filtramos
    forecast = make_forecast_dia(FECHA_TEST, scores_por_hora=scores)
    sf = mock_score_fn(scores_dict_from_forecast(forecast))

    result = calcular_mejor_hora(forecast, SPOT_MDQ, FECHA_TEST, score_fn=sf, ahora=AHORA_TEST)
    assert result is not None

    tz = ZoneInfo("America/Argentina/Buenos_Aires")
    hora_local = result.hour.timestamp.astimezone(tz).hour
    # La hora 3 está de noche en enero MDP → debe elegir hora 10
    assert hora_local != 3, "La hora nocturna no debe ganar aunque tenga score máximo"
    assert hora_local == 10, f"Debe elegir hora 10 (diurna con mayor score), got {hora_local}h"


def test_mejor_hora_daylight_en_resultado():
    """BestHourResult debe incluir DaylightInfo válido."""
    forecast = make_forecast_dia(FECHA_TEST)
    sf = mock_score_fn(scores_dict_from_forecast(forecast))
    result = calcular_mejor_hora(forecast, SPOT_MDQ, FECHA_TEST, score_fn=sf, ahora=AHORA_TEST)
    assert result is not None
    assert result.daylight is not None
    assert result.daylight.fecha == FECHA_TEST
    assert result.daylight.sunrise_local is not None
    assert result.daylight.sunset_local is not None


def test_mejor_hora_horas_evaluadas_solo_diurnas():
    """horas_evaluadas debe contar solo horas con luz solar."""
    forecast = make_forecast_dia(FECHA_TEST, horas=24)
    sf = mock_score_fn(scores_dict_from_forecast(forecast))
    result = calcular_mejor_hora(forecast, SPOT_MDQ, FECHA_TEST, score_fn=sf, ahora=AHORA_TEST)
    assert result is not None
    # En enero MDP hay ~13h de luz → entre 12 y 14 horas evaluadas
    assert 10 <= result.horas_evaluadas <= 16, \
        f"Horas evaluadas esperadas 10-16, got {result.horas_evaluadas}"


def test_mejor_hora_todas_las_horas_ordenadas_por_score():
    """todas_las_horas debe estar ordenado por score descendente."""
    scores = {8: 0.3, 10: 0.9, 12: 0.6, 14: 0.45}
    forecast = make_forecast_dia(FECHA_TEST, scores_por_hora=scores)
    sf = mock_score_fn(scores_dict_from_forecast(forecast))
    result = calcular_mejor_hora(forecast, SPOT_MDQ, FECHA_TEST, score_fn=sf, ahora=AHORA_TEST)
    assert result is not None

    ranked = result.todas_las_horas
    for i in range(1, len(ranked)):
        assert ranked[i].breakdown.score_total <= ranked[i-1].breakdown.score_total, \
            "todas_las_horas no está ordenado por score descendente"


def test_mejor_hora_excluye_horas_ya_pasadas_de_hoy():
    """
    Regresión #11: si 'ahora' cae después de una hora con score alto pero
    antes de otra con score más bajo, debe elegir la futura de score más
    bajo, no la pasada de score más alto — no tiene sentido recomendar una
    ventana horaria que ya no se puede usar.
    """
    scores = {9: 0.95, 15: 0.60}  # 9h tiene mejor score, pero va a quedar en el pasado
    forecast = make_forecast_dia(FECHA_TEST, scores_por_hora=scores)
    sf = mock_score_fn(scores_dict_from_forecast(forecast))

    tz = ZoneInfo("America/Argentina/Buenos_Aires")
    ahora_mediodia = datetime(
        FECHA_TEST.year, FECHA_TEST.month, FECHA_TEST.day, 12, 0, tzinfo=tz
    ).astimezone(timezone.utc)

    result = calcular_mejor_hora(forecast, SPOT_MDQ, FECHA_TEST, score_fn=sf, ahora=ahora_mediodia)
    assert result is not None

    hora_local = result.hour.timestamp.astimezone(tz).hour
    assert hora_local == 15, \
        f"Debía elegir la hora futura (15h), no la pasada con score más alto (9h). Eligió {hora_local}h"

    # La hora de las 9 (ya pasada respecto de 'ahora') no debe estar ni siquiera en el ranking evaluado.
    horas_evaluadas = [rh.hour.timestamp.astimezone(tz).hour for rh in result.todas_las_horas]
    assert 9 not in horas_evaluadas, f"La hora pasada (9h) no debería estar en el ranking: {horas_evaluadas}"


def test_mejor_hora_forecast_vacio():
    """Forecast vacío debe retornar None."""
    result = calcular_mejor_hora([], SPOT_MDQ, FECHA_TEST, score_fn=mock_score_fn({}))
    assert result is None


def test_mejor_hora_fecha_sin_datos():
    """Si la fecha no tiene horas en el forecast, retornar None."""
    forecast = make_forecast_dia(FECHA_TEST)
    fecha_diferente = date(2025, 1, 20)
    sf = mock_score_fn(scores_dict_from_forecast(forecast))
    result = calcular_mejor_hora(forecast, SPOT_MDQ, fecha_diferente, score_fn=sf)
    assert result is None


def test_mejor_hora_invierno_menos_horas():
    """En invierno debe evaluar menos horas que en verano."""
    fecha_invierno = date(2025, 6, 21)
    fecha_verano = date(2025, 1, 15)

    forecast_inv = make_forecast_dia(fecha_invierno, horas=24)
    forecast_ver = make_forecast_dia(fecha_verano, horas=24)

    sf_inv = mock_score_fn(scores_dict_from_forecast(forecast_inv))
    sf_ver = mock_score_fn(scores_dict_from_forecast(forecast_ver))

    result_inv = calcular_mejor_hora(
        forecast_inv, SPOT_MDQ, fecha_invierno, score_fn=sf_inv,
        ahora=ahora_antes_del_amanecer(fecha_invierno),
    )
    result_ver = calcular_mejor_hora(
        forecast_ver, SPOT_MDQ, fecha_verano, score_fn=sf_ver,
        ahora=ahora_antes_del_amanecer(fecha_verano),
    )

    assert result_inv is not None
    assert result_ver is not None
    assert result_inv.horas_evaluadas < result_ver.horas_evaluadas, \
        f"Invierno ({result_inv.horas_evaluadas}h) debe tener menos horas que verano ({result_ver.horas_evaluadas}h)"


def test_mejor_hora_timezone_brasil():
    """Funciona correctamente con timezone de Brasil (Sao Paulo)."""
    spot_br = make_spot(lat=-27.6, lon=-48.5, tz="America/Sao_Paulo")
    fecha = date(2025, 7, 1)
    forecast = make_forecast_dia(fecha, tz_str="America/Sao_Paulo",
                                  scores_por_hora={9: 0.85, 14: 0.6})
    sf = mock_score_fn(scores_dict_from_forecast(forecast))
    result = calcular_mejor_hora(
        forecast, spot_br, fecha, score_fn=sf,
        ahora=ahora_antes_del_amanecer(fecha, tz_str="America/Sao_Paulo"),
    )
    assert result is not None
    tz = ZoneInfo("America/Sao_Paulo")
    hora_local = result.hour.timestamp.astimezone(tz).hour
    assert hora_local == 9, f"Hora esperada 9h local, got {hora_local}h"


def test_mejor_hora_timezone_costa_rica():
    """Funciona con Costa Rica (UTC-6, cerca del ecuador — 12h de luz)."""
    spot_cr = make_spot(lat=10.3, lon=-85.8, tz="America/Costa_Rica")
    fecha = date(2025, 3, 15)
    forecast = make_forecast_dia(fecha, tz_str="America/Costa_Rica",
                                  scores_por_hora={7: 0.9, 12: 0.5})
    sf = mock_score_fn(scores_dict_from_forecast(forecast))
    result = calcular_mejor_hora(
        forecast, spot_cr, fecha, score_fn=sf,
        ahora=ahora_antes_del_amanecer(fecha, tz_str="America/Costa_Rica"),
    )
    assert result is not None


# ---------------------------------------------------------------------------
# Tests de calcular_mejor_hora_detallado (#16 — distinguir los motivos
# detrás de "no hay mejor hora", antes todos colapsaban en un mismo None)
# ---------------------------------------------------------------------------

def test_detallado_sin_forecast_para_la_fecha():
    """Ninguna hora del forecast corresponde a la fecha pedida."""
    forecast = make_forecast_dia(FECHA_TEST)
    fecha_diferente = date(2025, 1, 20)
    sf = mock_score_fn(scores_dict_from_forecast(forecast))
    result, motivo = calcular_mejor_hora_detallado(forecast, SPOT_MDQ, fecha_diferente, score_fn=sf)
    assert result is None
    assert motivo == MOTIVO_SIN_FORECAST


def test_detallado_solo_horas_nocturnas():
    """Hay forecast para la fecha, pero ninguna hora cae en luz solar vigente."""
    tz = ZoneInfo("America/Argentina/Buenos_Aires")
    medianoche = datetime(FECHA_TEST.year, FECHA_TEST.month, FECHA_TEST.day, 0, 0, tzinfo=tz)
    horas_madrugada = [medianoche + timedelta(hours=h) for h in (1, 2, 3)]
    forecast = [make_hour(dt.astimezone(timezone.utc), score_override=0.9) for dt in horas_madrugada]
    sf = mock_score_fn(scores_dict_from_forecast(forecast))

    result, motivo = calcular_mejor_hora_detallado(
        forecast, SPOT_MDQ, FECHA_TEST, score_fn=sf, ahora=AHORA_TEST,
    )
    assert result is None
    assert motivo == MOTIVO_SOLO_NOCTURNO_O_PASADO


def test_detallado_fallo_solar():
    """get_daylight() lanza ValueError (fenómeno polar) -> motivo específico, no confundido con 'sin datos'."""
    forecast = make_forecast_dia(FECHA_TEST)
    sf = mock_score_fn(scores_dict_from_forecast(forecast))
    with patch("core.analysis.best_hour.get_daylight", side_effect=ValueError("polar")):
        result, motivo = calcular_mejor_hora_detallado(forecast, SPOT_MDQ, FECHA_TEST, score_fn=sf, ahora=AHORA_TEST)
    assert result is None
    assert motivo == MOTIVO_FALLO_SOLAR


def test_detallado_scoring_fallo_para_todas_las_horas():
    """
    Regresión #16: si score_fn lanza excepción para TODAS las horas diurnas
    vigentes, el motivo debe distinguirse de "no había horas que evaluar" —
    antes ambos casos daban el mismo None sin forma de diferenciarlos.
    """
    forecast = make_forecast_dia(FECHA_TEST, scores_por_hora={8: 0.8, 10: 0.9})

    def score_fn_roto(hour, spot):
        raise RuntimeError("config de spot rota")

    result, motivo = calcular_mejor_hora_detallado(
        forecast, SPOT_MDQ, FECHA_TEST, score_fn=score_fn_roto, ahora=AHORA_TEST,
    )
    assert result is None
    assert motivo == MOTIVO_SCORING_FALLO


def test_detallado_motivo_none_cuando_hay_resultado():
    """Cuando sí hay mejor hora, motivo debe ser None."""
    forecast = make_forecast_dia(FECHA_TEST, scores_por_hora={8: 0.8, 10: 0.9, 14: 0.6})
    sf = mock_score_fn(scores_dict_from_forecast(forecast))
    result, motivo = calcular_mejor_hora_detallado(forecast, SPOT_MDQ, FECHA_TEST, score_fn=sf, ahora=AHORA_TEST)
    assert result is not None
    assert motivo is None


def test_calcular_mejor_hora_sigue_retornando_solo_el_resultado():
    """calcular_mejor_hora() (API pública original) no cambia su firma ni comportamiento."""
    forecast = make_forecast_dia(FECHA_TEST, scores_por_hora={8: 0.8, 10: 0.9})
    sf = mock_score_fn(scores_dict_from_forecast(forecast))
    result = calcular_mejor_hora(forecast, SPOT_MDQ, FECHA_TEST, score_fn=sf, ahora=AHORA_TEST)
    assert isinstance(result, BestHourResult)


# ---------------------------------------------------------------------------
# Tests de calcular_ranking_dia
# ---------------------------------------------------------------------------

def test_ranking_dia_retorna_lista():
    forecast = make_forecast_dia(FECHA_TEST)
    sf = mock_score_fn(scores_dict_from_forecast(forecast))
    result = calcular_ranking_dia(forecast, SPOT_MDQ, FECHA_TEST, score_fn=sf)
    assert isinstance(result, list)
    assert len(result) > 0


def test_ranking_dia_orden_cronologico():
    """calcular_ranking_dia debe retornar en orden cronológico (no por score)."""
    scores = {8: 0.9, 10: 0.3, 12: 0.7, 14: 0.5}
    forecast = make_forecast_dia(FECHA_TEST, scores_por_hora=scores)
    sf = mock_score_fn(scores_dict_from_forecast(forecast))

    result = calcular_ranking_dia(forecast, SPOT_MDQ, FECHA_TEST, score_fn=sf)
    for i in range(1, len(result)):
        assert result[i].hour.timestamp > result[i-1].hour.timestamp, \
            "Ranking no está en orden cronológico"


def test_ranking_dia_todos_es_dia_true_por_defecto():
    """Sin incluir_noche, todos los RankedHour deben tener es_dia=True."""
    forecast = make_forecast_dia(FECHA_TEST)
    sf = mock_score_fn(scores_dict_from_forecast(forecast))
    result = calcular_ranking_dia(forecast, SPOT_MDQ, FECHA_TEST, score_fn=sf)
    for rh in result:
        assert rh.es_dia is True, f"Hora {rh.hour.timestamp} marcada como nocturna en modo sin noche"


def test_ranking_dia_con_noche_incluye_es_dia_false():
    """Con incluir_noche=True, debe haber horas con es_dia=False."""
    forecast = make_forecast_dia(FECHA_TEST, horas=24)
    sf = mock_score_fn(scores_dict_from_forecast(forecast))
    result = calcular_ranking_dia(forecast, SPOT_MDQ, FECHA_TEST,
                                   score_fn=sf, incluir_noche=True)

    horas_noche = [rh for rh in result if not rh.es_dia]
    horas_dia = [rh for rh in result if rh.es_dia]

    assert len(horas_noche) > 0, "Con incluir_noche=True debe haber horas nocturnas"
    assert len(horas_dia) > 0, "Debe haber horas diurnas"
    assert len(horas_noche) + len(horas_dia) == len(result)


def test_ranking_dia_rank_1_es_mayor_score():
    """La hora con rank=1 debe tener el score más alto entre las diurnas."""
    scores = {8: 0.4, 10: 0.95, 12: 0.7, 15: 0.5, 17: 0.3}
    forecast = make_forecast_dia(FECHA_TEST, scores_por_hora=scores)
    sf = mock_score_fn(scores_dict_from_forecast(forecast))

    result = calcular_ranking_dia(forecast, SPOT_MDQ, FECHA_TEST, score_fn=sf)
    rank1 = next((rh for rh in result if rh.rank == 1), None)
    assert rank1 is not None

    for rh in result:
        if rh.rank != 1:
            assert rank1.breakdown.score_total >= rh.breakdown.score_total, \
                f"rank=1 ({rank1.breakdown.score_total:.2f}) debe ser >= rank={rh.rank} ({rh.breakdown.score_total:.2f})"


def test_ranking_dia_ranks_unicos():
    """No deben repetirse los ranks cuando los scores son distintos."""
    scores = {8: 0.1, 10: 0.5, 12: 0.9, 14: 0.3, 16: 0.7}
    forecast = make_forecast_dia(FECHA_TEST, scores_por_hora=scores)
    sf = mock_score_fn(scores_dict_from_forecast(forecast))

    result = calcular_ranking_dia(forecast, SPOT_MDQ, FECHA_TEST, score_fn=sf)
    ranks = [rh.rank for rh in result]
    # Los ranks de horas diurnas deben ser secuenciales
    ranks_dia = sorted([rh.rank for rh in result if rh.es_dia])
    assert ranks_dia == list(range(1, len(ranks_dia) + 1)), \
        f"Ranks no secuenciales: {ranks_dia}"


def test_ranking_dia_vacio():
    """Forecast vacío retorna lista vacía."""
    result = calcular_ranking_dia([], SPOT_MDQ, FECHA_TEST, score_fn=mock_score_fn({}))
    assert result == []


def test_ranking_dia_solo_horas_nocturnas_no_crashea():
    """
    Regresión #12: si incluir_noche=True y ninguna hora del día cae dentro
    de la ventana de luz solar, calcular_ranking_dia() no debe crashear con
    ValueError — debe degradar devolviendo las horas nocturnas con rank=999.
    """
    tz = ZoneInfo("America/Argentina/Buenos_Aires")
    medianoche = datetime(FECHA_TEST.year, FECHA_TEST.month, FECHA_TEST.day, 0, 0, tzinfo=tz)
    # En enero MDP el sunrise es ~05:30-06:00 local — 1h, 2h y 3h son de madrugada, antes del sunrise.
    horas_madrugada = [medianoche + timedelta(hours=h) for h in (1, 2, 3)]
    forecast = [make_hour(dt.astimezone(timezone.utc), score_override=0.9) for dt in horas_madrugada]
    sf = mock_score_fn(scores_dict_from_forecast(forecast))

    result = calcular_ranking_dia(forecast, SPOT_MDQ, FECHA_TEST, score_fn=sf, incluir_noche=True)

    assert len(result) == 3
    assert all(not rh.es_dia for rh in result), "Las 3 horas de madrugada deben quedar es_dia=False"
    assert all(rh.rank == 999 for rh in result), "Sin horas diurnas, todas deben quedar con rank=999"


def test_ranking_dia_fecha_sin_datos():
    """Fecha sin horas en el forecast retorna lista vacía."""
    forecast = make_forecast_dia(FECHA_TEST)
    sf = mock_score_fn(scores_dict_from_forecast(forecast))
    result = calcular_ranking_dia(forecast, SPOT_MDQ, date(2025, 1, 25), score_fn=sf)
    assert result == []


# ---------------------------------------------------------------------------
# Tests de RankedHour
# ---------------------------------------------------------------------------

def test_ranked_hour_es_dataclass():
    ts = datetime(2025, 1, 15, 12, 0, tzinfo=timezone.utc)
    h = make_hour(ts)
    bd = make_breakdown(0.75)
    rh = RankedHour(hour=h, breakdown=bd, rank=1, es_dia=True)
    assert rh.rank == 1
    assert rh.es_dia is True
    assert rh.breakdown.score_100 == 75


# ---------------------------------------------------------------------------
# Ejecutar directamente
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        test_mejor_hora_retorna_best_hour_result,
        test_mejor_hora_elige_mayor_score,
        test_mejor_hora_score_100_consistente,
        test_mejor_hora_rank_siempre_1,
        test_mejor_hora_excluye_noche,
        test_mejor_hora_daylight_en_resultado,
        test_mejor_hora_horas_evaluadas_solo_diurnas,
        test_mejor_hora_todas_las_horas_ordenadas_por_score,
        test_mejor_hora_excluye_horas_ya_pasadas_de_hoy,
        test_mejor_hora_forecast_vacio,
        test_mejor_hora_fecha_sin_datos,
        test_mejor_hora_invierno_menos_horas,
        test_mejor_hora_timezone_brasil,
        test_mejor_hora_timezone_costa_rica,
        test_ranking_dia_retorna_lista,
        test_ranking_dia_orden_cronologico,
        test_ranking_dia_todos_es_dia_true_por_defecto,
        test_ranking_dia_con_noche_incluye_es_dia_false,
        test_ranking_dia_rank_1_es_mayor_score,
        test_ranking_dia_ranks_unicos,
        test_ranking_dia_vacio,
        test_ranking_dia_fecha_sin_datos,
        test_ranking_dia_solo_horas_nocturnas_no_crashea,
        test_ranked_hour_es_dataclass,
    ]

    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  ✅ {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  ❌ {t.__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\n{'='*50}")
    print(f"  {passed} passed  |  {failed} failed  |  {len(tests)} total")
    if failed == 0:
        print("  🎉 Todos los tests pasaron.")
    else:
        print("  ⚠️  Hay tests fallando.")
