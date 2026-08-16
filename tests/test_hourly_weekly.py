"""Tests unitarios para core/analysis/hourly_view.py y core/analysis/weekly.py.

Correr con:
    python tests/test_hourly_weekly.py
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import math
import pytest
from datetime import date, datetime, timezone, timedelta
from typing import List
from zoneinfo import ZoneInfo

from core.scoring.models import (
    ForecastHour, SwellData, WindData, TideData,
    ScoreBreakdown, SpotConfig,
)
from core.analysis.hourly_view import generar_vista_horaria, HourlyRow, HourlyViewResult
from core.analysis.weekly import analizar_semana, DayScore, WeeklyAnalysis


# ---------------------------------------------------------------------------
# Fixtures compartidos
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


def make_breakdown(score: float) -> ScoreBreakdown:
    return ScoreBreakdown(
        score_energia=score, score_periodo=score,
        score_dir_swell=score, score_viento=score, score_marea=score,
        score_total=score, energia_proxy=score * 20,
    )


def mock_score_fn(scores: dict):
    def _fn(hour: ForecastHour, spot: SpotConfig) -> ScoreBreakdown:
        return make_breakdown(scores.get(hour.timestamp, 0.5))
    return _fn


def make_forecast_dias(
    fecha_inicio: date,
    n_dias: int,
    tz_str: str = "America/Argentina/Buenos_Aires",
    scores_por_hora: dict = None,
) -> List[ForecastHour]:
    """
    Genera forecast para n_dias días consecutivos.
    scores_por_hora: {hora_local (int): score} — se aplica igual a todos los días.
    """
    tz = ZoneInfo(tz_str)
    result = []
    for dia_offset in range(n_dias):
        fecha = fecha_inicio + timedelta(days=dia_offset)
        medianoche = datetime(fecha.year, fecha.month, fecha.day, 0, 0, tzinfo=tz)
        for hora in range(24):
            dt_utc = (medianoche + timedelta(hours=hora)).astimezone(timezone.utc)
            score = (scores_por_hora or {}).get(hora, 0.5)
            h = ForecastHour(
                timestamp=dt_utc,
                swell=SwellData(altura_m=1.5, periodo_s=12.0, direccion_deg=95.0),
                wind=WindData(velocidad_kmh=15.0, rafaga_kmh=20.0, direccion_deg=180.0),
                tide=TideData(nivel_m=0.8, fuente="proxy_msl"),
            )
            h._test_score = score
            result.append(h)
    return result


def scores_dict(forecast):
    return {h.timestamp: getattr(h, '_test_score', 0.5) for h in forecast}


SPOT = make_spot()
FECHA = date(2025, 1, 15)  # verano AR


# ===========================================================================
# TESTS: hourly_view
# ===========================================================================

def test_hourly_view_retorna_resultado():
    forecast = make_forecast_dias(FECHA, 1)
    sf = mock_score_fn(scores_dict(forecast))
    result = generar_vista_horaria(forecast, SPOT, FECHA, score_fn=sf)
    assert result is not None
    assert isinstance(result, HourlyViewResult)


def test_hourly_view_filas_cronologicas():
    """Las filas deben estar en orden cronológico."""
    forecast = make_forecast_dias(FECHA, 1)
    sf = mock_score_fn(scores_dict(forecast))
    result = generar_vista_horaria(forecast, SPOT, FECHA, score_fn=sf)
    assert result is not None
    for i in range(1, len(result.filas)):
        assert result.filas[i].hour.timestamp > result.filas[i-1].hour.timestamp


def test_hourly_view_exactamente_un_es_mejor():
    """Exactamente una fila debe tener es_mejor=True."""
    forecast = make_forecast_dias(FECHA, 1, scores_por_hora={8: 0.4, 10: 0.9, 14: 0.6})
    sf = mock_score_fn(scores_dict(forecast))
    result = generar_vista_horaria(forecast, SPOT, FECHA, score_fn=sf)
    assert result is not None
    mejores = [f for f in result.filas if f.es_mejor]
    assert len(mejores) == 1, f"Esperada exactamente 1 fila con es_mejor=True, got {len(mejores)}"


def test_hourly_view_mejor_hora_tiene_mayor_score():
    """La fila con es_mejor=True debe tener el score más alto entre las diurnas."""
    scores_h = {8: 0.3, 10: 0.95, 12: 0.7, 15: 0.4}
    forecast = make_forecast_dias(FECHA, 1, scores_por_hora=scores_h)
    sf = mock_score_fn(scores_dict(forecast))
    result = generar_vista_horaria(forecast, SPOT, FECHA, score_fn=sf)
    assert result is not None
    mejor = next(f for f in result.filas if f.es_mejor)
    for f in result.filas:
        if f.es_dia and not f.es_mejor:
            assert mejor.breakdown.score_total >= f.breakdown.score_total


def test_hourly_view_con_noche_incluye_nocturnas():
    """Con incluir_noche=True debe haber filas con es_dia=False."""
    forecast = make_forecast_dias(FECHA, 1)
    sf = mock_score_fn(scores_dict(forecast))
    result = generar_vista_horaria(forecast, SPOT, FECHA, score_fn=sf, incluir_noche=True)
    assert result is not None
    nocturnas = [f for f in result.filas if not f.es_dia]
    assert len(nocturnas) > 0, "Con incluir_noche=True debe haber filas nocturnas"


def test_hourly_view_sin_noche_todas_diurnas():
    """Con incluir_noche=False (default True acá, pero probamos False) todas son diurnas."""
    forecast = make_forecast_dias(FECHA, 1)
    sf = mock_score_fn(scores_dict(forecast))
    result = generar_vista_horaria(forecast, SPOT, FECHA, score_fn=sf, incluir_noche=False)
    assert result is not None
    for f in result.filas:
        assert f.es_dia is True


def test_hourly_view_daylight_presente():
    """HourlyViewResult debe incluir DaylightInfo."""
    forecast = make_forecast_dias(FECHA, 1)
    sf = mock_score_fn(scores_dict(forecast))
    result = generar_vista_horaria(forecast, SPOT, FECHA, score_fn=sf)
    assert result is not None
    assert result.daylight is not None
    assert result.daylight.fecha == FECHA


def test_hourly_view_mejor_hora_property():
    """result.mejor_hora debe ser la misma fila que tiene es_mejor=True."""
    forecast = make_forecast_dias(FECHA, 1)
    sf = mock_score_fn(scores_dict(forecast))
    result = generar_vista_horaria(forecast, SPOT, FECHA, score_fn=sf)
    assert result is not None
    assert result.mejor_hora is not None
    assert result.mejor_hora.es_mejor is True


def test_hourly_view_vacio():
    result = generar_vista_horaria([], SPOT, FECHA, score_fn=mock_score_fn({}))
    assert result is None


def test_hourly_view_fecha_sin_datos():
    forecast = make_forecast_dias(FECHA, 1)
    sf = mock_score_fn(scores_dict(forecast))
    result = generar_vista_horaria(forecast, SPOT, date(2025, 1, 25), score_fn=sf)
    assert result is None


def test_hourly_view_spot_y_fecha_en_resultado():
    forecast = make_forecast_dias(FECHA, 1)
    sf = mock_score_fn(scores_dict(forecast))
    result = generar_vista_horaria(forecast, SPOT, FECHA, score_fn=sf)
    assert result is not None
    assert result.fecha == FECHA
    assert result.spot.key == SPOT.key


def test_hourly_view_solo_horas_nocturnas_no_crashea():
    """
    Regresión #12: generar_vista_horaria() usa incluir_noche=True por default,
    justo el caso que crasheaba en calcular_ranking_dia() cuando el día no
    tiene ninguna hora diurna. No debe crashear, y no debe haber mejor_hora.
    """
    tz = ZoneInfo("America/Argentina/Buenos_Aires")
    medianoche = datetime(FECHA.year, FECHA.month, FECHA.day, 0, 0, tzinfo=tz)
    horas_madrugada = [medianoche + timedelta(hours=h) for h in (1, 2, 3)]
    forecast = []
    for dt in horas_madrugada:
        h = ForecastHour(
            timestamp=dt.astimezone(timezone.utc),
            swell=SwellData(altura_m=1.5, periodo_s=12.0, direccion_deg=95.0),
            wind=WindData(velocidad_kmh=15.0, rafaga_kmh=20.0, direccion_deg=180.0),
            tide=TideData(nivel_m=0.8, fuente="proxy_msl"),
        )
        h._test_score = 0.9
        forecast.append(h)
    sf = mock_score_fn(scores_dict(forecast))

    result = generar_vista_horaria(forecast, SPOT, FECHA, score_fn=sf)

    assert result is not None
    assert len(result.filas) == 3
    assert all(not f.es_dia for f in result.filas)
    assert result.mejor_hora is None, "Sin horas diurnas no debería haber mejor_hora"


# ===========================================================================
# TESTS: weekly
# ===========================================================================

def test_weekly_retorna_weekly_analysis():
    forecast = make_forecast_dias(FECHA, 7)
    sf = mock_score_fn(scores_dict(forecast))
    result = analizar_semana(forecast, SPOT, score_fn=sf)
    assert result is not None
    assert isinstance(result, WeeklyAnalysis)


def test_weekly_cantidad_dias():
    """Debe analizar exactamente los días disponibles (hasta 7)."""
    forecast = make_forecast_dias(FECHA, 5)
    sf = mock_score_fn(scores_dict(forecast))
    result = analizar_semana(forecast, SPOT, score_fn=sf)
    assert result is not None
    assert len(result.scores_por_dia) == 5


def test_weekly_dias_ordenados_cronologicamente():
    forecast = make_forecast_dias(FECHA, 7)
    sf = mock_score_fn(scores_dict(forecast))
    result = analizar_semana(forecast, SPOT, score_fn=sf)
    assert result is not None
    for i in range(1, len(result.scores_por_dia)):
        assert result.scores_por_dia[i].fecha > result.scores_por_dia[i-1].fecha


def test_weekly_mejor_dia_tiene_mayor_score():
    """mejor_dia debe tener el score_promedio más alto."""
    # Día 3 (offset 2 = 17 enero) tendrá score 0.9, el resto 0.5
    # Usamos scores diferentes por día via override manual
    tz = ZoneInfo("America/Argentina/Buenos_Aires")
    forecast = []
    fecha_base = date(2025, 1, 15)
    for dia_offset in range(5):
        fecha = fecha_base + timedelta(days=dia_offset)
        score_dia = 0.9 if dia_offset == 2 else 0.4
        medianoche = datetime(fecha.year, fecha.month, fecha.day, 0, 0, tzinfo=tz)
        for hora in range(24):
            dt_utc = (medianoche + timedelta(hours=hora)).astimezone(timezone.utc)
            h = ForecastHour(
                timestamp=dt_utc,
                swell=SwellData(1.5, 12.0, 95.0),
                wind=WindData(15.0, 20.0, 180.0),
                tide=TideData(0.8),
            )
            h._test_score = score_dia
            forecast.append(h)

    sf = mock_score_fn({h.timestamp: getattr(h, '_test_score', 0.5) for h in forecast})
    result = analizar_semana(forecast, SPOT, score_fn=sf)
    assert result is not None

    fecha_esperada = fecha_base + timedelta(days=2)
    assert result.mejor_dia.fecha == fecha_esperada, \
        f"Mejor día esperado {fecha_esperada}, got {result.mejor_dia.fecha}"


def test_weekly_peor_dia_tiene_menor_score():
    """peor_dia debe tener el score_promedio más bajo."""
    forecast = make_forecast_dias(FECHA, 5)
    sf = mock_score_fn(scores_dict(forecast))
    result = analizar_semana(forecast, SPOT, score_fn=sf)
    assert result is not None
    for d in result.scores_por_dia:
        if d.tiene_datos:
            assert result.peor_dia.score_promedio <= d.score_promedio


def test_weekly_dias_buenos_threshold():
    """dias_buenos debe contener solo días con score_100 >= 55."""
    forecast = make_forecast_dias(FECHA, 5)
    sf = mock_score_fn(scores_dict(forecast))
    result = analizar_semana(forecast, SPOT, score_fn=sf)
    assert result is not None
    for d in result.dias_buenos:
        assert d.score_100 >= 55, f"Día {d.fecha} en dias_buenos pero score={d.score_100}"


def test_weekly_mejor_ventana_semana():
    """mejor_ventana_semana debe ser BestHourResult con el score más alto de la semana."""
    forecast = make_forecast_dias(FECHA, 3)
    sf = mock_score_fn(scores_dict(forecast))
    result = analizar_semana(forecast, SPOT, score_fn=sf)
    assert result is not None
    ventana = result.mejor_ventana_semana
    assert ventana is not None
    # Verificar que ningún día tiene una mejor hora con score mayor
    for d in result.scores_por_dia:
        if d.mejor_hora:
            assert ventana.score_100 >= d.mejor_hora.score_100


def test_weekly_excluye_horas_pasadas_de_hoy():
    """
    Regresión #11: analizar_semana() no debe promediar horas ya pasadas del
    día de hoy, ni elegir una como "mejor hora" si hay una futura disponible
    con score más bajo. Este es el efecto productivo original del hallazgo
    (antes, calcular_mejor_hora() no filtraba contra 'ahora', y weekly.py lo
    llama internamente para armar el promedio diario).

    Usa 'ahora' inyectado (no el reloj real) para que el test sea
    determinista sin depender de en qué fecha real corre.
    """
    tz = ZoneInfo("America/Argentina/Buenos_Aires")
    ahora_mediodia = datetime(
        FECHA.year, FECHA.month, FECHA.day, 12, 0, tzinfo=tz
    ).astimezone(timezone.utc)

    forecast_hoy = make_forecast_dias(FECHA, 1, scores_por_hora={9: 0.95, 15: 0.60})
    sf = mock_score_fn(scores_dict(forecast_hoy))

    result = analizar_semana(forecast_hoy, SPOT, score_fn=sf, ahora=ahora_mediodia)
    assert result is not None

    dia_hoy = result.scores_por_dia[0]
    assert dia_hoy.fecha == FECHA
    assert dia_hoy.tiene_datos is True

    # La mejor hora debe ser la futura (15h, score 0.60), no la pasada (9h, score 0.95).
    hora_local_mejor = dia_hoy.mejor_hora.hour.timestamp.astimezone(tz).hour
    assert hora_local_mejor == 15, \
        f"Debía elegir 15h (futura), no 9h (pasada con score más alto). Eligió {hora_local_mejor}h"

    # La hora de las 9 no debe estar ni en el ranking del día ni contarse en horas_con_luz.
    horas_en_ranking = [
        rh.hour.timestamp.astimezone(tz).hour
        for rh in dia_hoy.mejor_hora.todas_las_horas
    ]
    assert 9 not in horas_en_ranking, f"Hora pasada (9h) no debería estar en el ranking: {horas_en_ranking}"
    assert dia_hoy.horas_con_luz == len(horas_en_ranking)


def test_weekly_vacio():
    result = analizar_semana([], SPOT, score_fn=mock_score_fn({}))
    assert result is None


def test_weekly_max_dias_respetado():
    """Con max_dias=3, no debe analizar más de 3 días aunque haya 7."""
    forecast = make_forecast_dias(FECHA, 7)
    sf = mock_score_fn(scores_dict(forecast))
    result = analizar_semana(forecast, SPOT, score_fn=sf, max_dias=3)
    assert result is not None
    assert len(result.scores_por_dia) == 3


def test_weekly_day_score_nombre_dia():
    """DayScore.nombre_dia debe retornar el nombre correcto."""
    ds = DayScore(
        fecha=date(2025, 1, 13),  # lunes
        score_promedio=0.6, score_max=0.8, score_100=60,
        mejor_hora=None, horas_con_luz=10, tiene_datos=True,
    )
    assert ds.nombre_dia == "Lunes"
    assert ds.nombre_corto == "Lun"


def test_weekly_day_score_es_bueno():
    bueno = DayScore(date(2025, 1, 15), 0.6, 0.8, 60, None, 10, True)
    malo = DayScore(date(2025, 1, 15), 0.4, 0.6, 40, None, 10, True)
    assert bueno.es_bueno is True
    assert malo.es_bueno is False


def test_weekly_tiene_datos_false_para_dia_sin_forecast():
    """Un día sin horas en el forecast debe tener tiene_datos=False."""
    # Solo 2 días de forecast
    forecast = make_forecast_dias(FECHA, 2)
    # Pedimos analizar 5 días — los últimos 3 no tendrán datos
    sf = mock_score_fn(scores_dict(forecast))
    result = analizar_semana(forecast, SPOT, score_fn=sf, max_dias=5)
    assert result is not None
    # Solo los 2 primeros días deben tener datos
    dias_con = [d for d in result.scores_por_dia if d.tiene_datos]
    assert len(dias_con) == 2


def test_weekly_scoring_fallido_para_todos_los_dias_propaga_excepcion():
    """
    Regresión #16 (caso raíz del hallazgo original): si score_fn falla para
    TODAS las horas de TODOS los días con forecast disponible (ej. config
    de spot rota), analizar_semana() no debe degradar en silencio a un
    None genérico indistinguible de "no hay pronóstico" — se propaga como
    RuntimeError, mismo criterio que core/windows/detector.py (#23) para
    fallos sistémicos de scoring. bot/handlers/main.py ya envuelve
    analizar_semana() en un try/except genérico, así que esto no rompe
    el flujo real, solo el resultado observable deja de ser un None mudo.
    """
    forecast = make_forecast_dias(FECHA, 3)

    def score_fn_roto(hour, spot):
        raise RuntimeError("config de spot rota")

    tz = ZoneInfo("America/Argentina/Buenos_Aires")
    ahora = datetime(FECHA.year, FECHA.month, FECHA.day, 12, 0, tzinfo=tz).astimezone(timezone.utc)

    with pytest.raises(RuntimeError, match="scoring_fallo"):
        analizar_semana(forecast, SPOT, score_fn=score_fn_roto, ahora=ahora)


def test_weekly_semana_completa_nocturna_no_propaga_excepcion():
    """
    MOTIVO_SOLO_NOCTURNO_O_PASADO es un estado normal de disponibilidad
    (forecast corto que solo cubre horas nocturnas), no un fallo técnico —
    a diferencia del test anterior, si TODOS los días de la semana quedan
    sin datos por este motivo, analizar_semana() debe seguir devolviendo
    None como antes de #16, no una excepción.
    """
    tz = ZoneInfo("America/Argentina/Buenos_Aires")
    medianoche = datetime(FECHA.year, FECHA.month, FECHA.day, 0, 0, tzinfo=tz)
    horas_madrugada = [medianoche + timedelta(hours=h) for h in (1, 2, 3)]
    forecast = []
    for dt in horas_madrugada:
        h = ForecastHour(
            timestamp=dt.astimezone(timezone.utc),
            swell=SwellData(altura_m=1.5, periodo_s=12.0, direccion_deg=95.0),
            wind=WindData(velocidad_kmh=15.0, rafaga_kmh=20.0, direccion_deg=180.0),
            tide=TideData(nivel_m=0.8, fuente="proxy_msl"),
        )
        h._test_score = 0.9
        forecast.append(h)
    sf = mock_score_fn(scores_dict(forecast))

    result = analizar_semana(forecast, SPOT, score_fn=sf, ahora=medianoche.astimezone(timezone.utc))
    assert result is None


def test_weekly_motivo_sin_datos_solo_nocturno():
    """
    Regresión #16: un día cuyo forecast es exclusivamente nocturno debe
    quedar con motivo_sin_datos=MOTIVO_SOLO_NOCTURNO_O_PASADO — distinguible
    de un día con datos reales, que debe tener motivo_sin_datos=None. 'ahora'
    inyectado para no depender del reloj real (mismo patrón que
    test_weekly_excluye_horas_pasadas_de_hoy).

    Nota: fechas_disponibles (weekly.py) solo incluye fechas que ya tienen
    al menos una hora de forecast, así que MOTIVO_SIN_FORECAST nunca se
    produce a través de analizar_semana() — solo es alcanzable llamando
    calcular_mejor_hora_detallado() directamente con una fecha ausente del
    forecast (cubierto en test_best_hour.py).
    """
    from core.analysis.best_hour import MOTIVO_SOLO_NOCTURNO_O_PASADO

    tz = ZoneInfo("America/Argentina/Buenos_Aires")
    medianoche = datetime(FECHA.year, FECHA.month, FECHA.day, 0, 0, tzinfo=tz)
    horas_madrugada = [medianoche + timedelta(hours=h) for h in (1, 2, 3)]
    forecast_noche = []
    for dt in horas_madrugada:
        h = ForecastHour(
            timestamp=dt.astimezone(timezone.utc),
            swell=SwellData(altura_m=1.5, periodo_s=12.0, direccion_deg=95.0),
            wind=WindData(velocidad_kmh=15.0, rafaga_kmh=20.0, direccion_deg=180.0),
            tide=TideData(nivel_m=0.8, fuente="proxy_msl"),
        )
        h._test_score = 0.9
        forecast_noche.append(h)

    fecha_dia2 = FECHA + timedelta(days=1)
    forecast_dia2 = make_forecast_dias(fecha_dia2, 1, scores_por_hora={12: 0.7})

    forecast = forecast_noche + forecast_dia2
    sf = mock_score_fn(scores_dict(forecast))

    result = analizar_semana(forecast, SPOT, score_fn=sf, ahora=medianoche.astimezone(timezone.utc))
    assert result is not None

    dia1 = next(d for d in result.scores_por_dia if d.fecha == FECHA)
    dia2 = next(d for d in result.scores_por_dia if d.fecha == fecha_dia2)

    assert dia1.tiene_datos is False
    assert dia1.motivo_sin_datos == MOTIVO_SOLO_NOCTURNO_O_PASADO
    assert dia2.tiene_datos is True
    assert dia2.motivo_sin_datos is None


def test_weekly_spot_en_resultado():
    forecast = make_forecast_dias(FECHA, 3)
    sf = mock_score_fn(scores_dict(forecast))
    result = analizar_semana(forecast, SPOT, score_fn=sf)
    assert result is not None
    assert result.spot.key == SPOT.key


def test_weekly_hay_dias_buenos_property():
    forecast = make_forecast_dias(FECHA, 3)
    sf = mock_score_fn(scores_dict(forecast))
    result = analizar_semana(forecast, SPOT, score_fn=sf)
    assert result is not None
    # Con score 0.5 (score_100=50), no hay días buenos
    assert result.hay_dias_buenos is False


# ---------------------------------------------------------------------------
# Ejecutar directamente
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests_hourly = [
        test_hourly_view_retorna_resultado,
        test_hourly_view_filas_cronologicas,
        test_hourly_view_exactamente_un_es_mejor,
        test_hourly_view_mejor_hora_tiene_mayor_score,
        test_hourly_view_con_noche_incluye_nocturnas,
        test_hourly_view_sin_noche_todas_diurnas,
        test_hourly_view_daylight_presente,
        test_hourly_view_mejor_hora_property,
        test_hourly_view_vacio,
        test_hourly_view_fecha_sin_datos,
        test_hourly_view_spot_y_fecha_en_resultado,
        test_hourly_view_solo_horas_nocturnas_no_crashea,
    ]

    tests_weekly = [
        test_weekly_retorna_weekly_analysis,
        test_weekly_cantidad_dias,
        test_weekly_dias_ordenados_cronologicamente,
        test_weekly_mejor_dia_tiene_mayor_score,
        test_weekly_peor_dia_tiene_menor_score,
        test_weekly_dias_buenos_threshold,
        test_weekly_mejor_ventana_semana,
        test_weekly_excluye_horas_pasadas_de_hoy,
        test_weekly_vacio,
        test_weekly_max_dias_respetado,
        test_weekly_day_score_nombre_dia,
        test_weekly_day_score_es_bueno,
        test_weekly_tiene_datos_false_para_dia_sin_forecast,
        test_weekly_spot_en_resultado,
        test_weekly_hay_dias_buenos_property,
    ]

    all_tests = tests_hourly + tests_weekly
    passed = failed = 0

    print("── hourly_view ──────────────────────────────────")
    for t in tests_hourly:
        try:
            t()
            print(f"  ✅ {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  ❌ {t.__name__}: {e}")
            import traceback; traceback.print_exc()
            failed += 1

    print("\n── weekly ───────────────────────────────────────")
    for t in tests_weekly:
        try:
            t()
            print(f"  ✅ {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  ❌ {t.__name__}: {e}")
            import traceback; traceback.print_exc()
            failed += 1

    print(f"\n{'='*50}")
    print(f"  {passed} passed  |  {failed} failed  |  {len(all_tests)} total")
    print("  🎉 Todos los tests pasaron." if failed == 0 else "  ⚠️  Hay tests fallando.")
