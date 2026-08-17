"""Tests unitarios para bot/formatters.py.

Verifica que cada función retorna strings válidos con el contenido esperado.
No depende de Telegram ni de IO.

Correr con:
    python tests/test_formatters.py
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import date, datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import patch

from core.scoring.models import (
    ForecastHour, SwellData, WindData, TideData,
    ScoreBreakdown, SpotConfig, VentanaOptima,
)
from core.analysis.daylight import DaylightInfo
from core.analysis.tides import TideAnalysis, TideEvent
from core.analysis.best_hour import BestHourResult, RankedHour
from core.analysis.hourly_view import HourlyRow, HourlyViewResult
from core.analysis.weekly import DayScore, WeeklyAnalysis

from bot.formatters import (
    formato_condiciones_actuales,
    formato_ventanas,
    formato_breakdown_pro,
    formato_lista_ventanas_corta,
    formato_no_disponible,
    formato_luz_solar,
    formato_mareas,
    formato_mejor_hora,
    formato_vista_horaria,
    formato_semana,
    formato_dia_completo,
    formato_proximas_olas,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TZ_AR = ZoneInfo("America/Argentina/Buenos_Aires")
TS_BASE = datetime(2025, 1, 15, 14, 0, tzinfo=timezone.utc)  # 11:00 AR


def make_spot() -> SpotConfig:
    return SpotConfig(
        key="mdq_varese", nombre="Varese", ciudad="Mar del Plata",
        pais="AR", region="buenos_aires",
        lat=-38.0, lon=-57.5,
        orientacion_costa_deg=95.0, tolerancia_swell_deg=45.0,
        tipo_break="beach", fondo="arena",
        marea_min_m=0.4, marea_max_m=1.6, marea_tipo_efecto="mid_better",
        swell_altura_min=0.5, swell_altura_max=3.0, swell_periodo_min=7.0,
        viento_max_offshore=35.0, viento_max_onshore=15.0,
        tz="America/Argentina/Buenos_Aires",
    )


def make_hour(ts=TS_BASE) -> ForecastHour:
    return ForecastHour(
        timestamp=ts,
        swell=SwellData(altura_m=1.5, periodo_s=12.0, direccion_deg=135.0),
        wind=WindData(velocidad_kmh=18.0, rafaga_kmh=22.0, direccion_deg=180.0),
        tide=TideData(nivel_m=0.85, fuente="proxy_msl"),
    )


def make_breakdown(score=0.75) -> ScoreBreakdown:
    return ScoreBreakdown(
        score_energia=score, score_periodo=score,
        score_dir_swell=score, score_viento=score, score_marea=score,
        score_total=score, energia_proxy=score * 30,
        flags_positivos=["Groundswell largo (12s)"],
        flags_negativos=["Viento onshore moderado"],
        flags_neutros=["Marea: proxy MSL (orientativo)"],
    )


def make_spot_obj():
    return make_spot()

SPOT = make_spot()


def make_daylight() -> DaylightInfo:
    sr = datetime(2025, 1, 15, 9, 32, tzinfo=timezone.utc)
    ss = datetime(2025, 1, 15, 23, 18, tzinfo=timezone.utc)
    tz = TZ_AR
    return DaylightInfo(
        fecha=date(2025, 1, 15),
        spot_key="mdq_varese",
        sunrise_utc=sr, sunset_utc=ss,
        sunrise_local=sr.astimezone(tz),
        sunset_local=ss.astimezone(tz),
    )


def make_tide_analysis_con_eventos() -> TideAnalysis:
    t1 = datetime(2025, 1, 15, 9, 0, tzinfo=timezone.utc)
    t2 = datetime(2025, 1, 15, 15, 30, tzinfo=timezone.utc)
    t3 = datetime(2025, 1, 15, 21, 45, tzinfo=timezone.utc)
    return TideAnalysis(
        eventos=[
            TideEvent(t1, 1.35, "alta"),
            TideEvent(t2, 0.22, "baja"),
            TideEvent(t3, 1.28, "alta"),
        ],
        tendencia_actual="bajando",
        proximo_cambio=t2,
        nivel_actual=0.85,
        tiene_extremos_claros=True,
        fuente="proxy_msl",
    )


def make_tide_analysis_sin_extremos() -> TideAnalysis:
    return TideAnalysis(
        eventos=[],
        tendencia_actual="subiendo",
        proximo_cambio=datetime(2025, 1, 15, 14, 0, tzinfo=timezone.utc),
        nivel_actual=0.7,
        tiene_extremos_claros=False,
        fuente="proxy_msl",
    )


def make_best_hour_result() -> BestHourResult:
    h = make_hour(datetime(2025, 1, 15, 13, 0, tzinfo=timezone.utc))  # 10:00 AR
    bd = make_breakdown(0.82)
    dl = make_daylight()
    rh = RankedHour(hour=h, breakdown=bd, rank=1, es_dia=True)
    return BestHourResult(
        hour=h, breakdown=bd, score_100=82,
        rank_en_dia=1, horas_evaluadas=13,
        daylight=dl, todas_las_horas=[rh],
    )


def make_hourly_view() -> HourlyViewResult:
    dl = make_daylight()
    filas = []
    for hora_utc in range(12, 22):  # 09:00–19:00 AR
        ts = datetime(2025, 1, 15, hora_utc, 0, tzinfo=timezone.utc)
        h = make_hour(ts)
        bd = make_breakdown(0.5 + hora_utc * 0.02)
        es_mejor = hora_utc == 14
        filas.append(HourlyRow(hour=h, breakdown=bd, rank=22-hora_utc, es_dia=True, es_mejor=es_mejor))
    mejor = next(f for f in filas if f.es_mejor)
    return HourlyViewResult(fecha=date(2025, 1, 15), spot=SPOT, filas=filas, daylight=dl, mejor_hora=mejor)


def make_weekly_analysis() -> WeeklyAnalysis:
    scores = [0.72, 0.45, 0.58, 0.38, 0.61, 0.30, 0.55]
    dias = []
    for i, s in enumerate(scores):
        fecha = date(2025, 1, 15) + timedelta(days=i)
        h = make_hour(datetime(2025, 1, 15 + i, 13, 0, tzinfo=timezone.utc))
        bd = make_breakdown(s)
        dl = make_daylight()
        rh = RankedHour(hour=h, breakdown=bd, rank=1, es_dia=True)
        bhr = BestHourResult(hour=h, breakdown=bd, score_100=round(s*100),
                             rank_en_dia=1, horas_evaluadas=13, daylight=dl, todas_las_horas=[rh])
        dias.append(DayScore(
            fecha=fecha, score_promedio=s, score_max=s+0.05,
            score_100=round(s*100), mejor_hora=bhr,
            horas_con_luz=13, tiene_datos=True,
        ))
    mejor = max(dias, key=lambda d: d.score_promedio)
    peor = min(dias, key=lambda d: d.score_promedio)
    buenos = [d for d in dias if d.score_100 >= 55]
    return WeeklyAnalysis(scores_por_dia=dias, mejor_dia=mejor, peor_dia=peor, dias_buenos=buenos, spot=SPOT)


# ---------------------------------------------------------------------------
# Tests: funciones existentes
# ---------------------------------------------------------------------------

def test_condiciones_retorna_str():
    s = formato_condiciones_actuales(make_hour(), make_breakdown(), SPOT)
    assert isinstance(s, str) and len(s) > 0


def test_condiciones_contiene_ciudad():
    s = formato_condiciones_actuales(make_hour(), make_breakdown(), SPOT)
    assert "Mar del Plata" in s


def test_condiciones_contiene_nombre_spot():
    s = formato_condiciones_actuales(make_hour(), make_breakdown(), SPOT)
    assert "Varese" in s


def test_condiciones_contiene_score():
    s = formato_condiciones_actuales(make_hour(), make_breakdown(0.75), SPOT)
    assert "75" in s


def test_condiciones_contiene_swell():
    s = formato_condiciones_actuales(make_hour(), make_breakdown(), SPOT)
    assert "1.5m" in s


def test_condiciones_sin_texto_pro():
    """No debe aparecer ningún texto de Pro/upgrade."""
    s = formato_condiciones_actuales(make_hour(), make_breakdown(), SPOT)
    assert "Pro" not in s
    assert "🔒" not in s
    assert "upgrade" not in s.lower()


def test_condiciones_proxy_msl_label():
    """Debe indicar que la marea es estimada."""
    s = formato_condiciones_actuales(make_hour(), make_breakdown(), SPOT)
    assert "proxy MSL" in s or "estimada" in s


def test_condiciones_calidad_antes_que_datos_crudos():
    """El score debe aparecer antes de los datos de swell/viento (#A1: lo
    que decide si vale la pena seguir leyendo va primero)."""
    s = formato_condiciones_actuales(make_hour(), make_breakdown(0.75), SPOT)
    assert s.index("75/100") < s.index("1.5m")


def test_condiciones_flags_positivos_en_una_sola_linea():
    """Varios flags positivos van condensados en una sola línea con ✅,
    no uno por línea (#A1)."""
    bd = ScoreBreakdown(
        score_energia=0.8, score_periodo=0.8, score_dir_swell=0.8,
        score_viento=0.8, score_marea=0.8, score_total=0.8, energia_proxy=20.0,
        flags_positivos=["Groundswell largo (14s)", "Offshore limpio (10 km/h)", "Marea óptima"],
    )
    s = formato_condiciones_actuales(make_hour(), bd, SPOT)
    assert s.count("✅") == 1
    assert "Groundswell largo (14s) · Offshore limpio (10 km/h) · Marea óptima" in s


def test_condiciones_proxy_msl_una_sola_vez():
    """El disclaimer de proxy MSL no debe duplicarse entre el flag neutro
    (suprimido) y la línea de marea inline, que ya lo muestra (#A1)."""
    s = formato_condiciones_actuales(make_hour(), make_breakdown(), SPOT)
    assert s.count("proxy MSL") == 1


def test_condiciones_hora_local_ar():
    """La hora mostrada debe ser la local del spot (AR = UTC-3)."""
    # TS_BASE = 14:00 UTC → 11:00 AR
    s = formato_condiciones_actuales(make_hour(), make_breakdown(), SPOT)
    assert "11:00" in s


def test_ventanas_sin_ventanas():
    s = formato_ventanas([], SPOT)
    assert "No se encontraron" in s


def test_ventanas_con_ventanas():
    v = VentanaOptima(
        inicio=datetime(2025, 1, 15, 12, 0, tzinfo=timezone.utc),
        fin=datetime(2025, 1, 15, 15, 0, tzinfo=timezone.utc),
        score_promedio=0.72,
        score_max=0.80,
        hora_pico=datetime(2025, 1, 15, 13, 0, tzinfo=timezone.utc),
        descripcion="Offshore + período largo",
        horas_count=3,
    )
    s = formato_ventanas([v], SPOT)
    assert "09:00" in s or "12:00" in s  # hora local AR (UTC-3)
    assert "72" in s


def test_breakdown_contiene_scores():
    s = formato_breakdown_pro(make_hour(), make_breakdown(0.6), SPOT)
    assert "60" in s
    assert "Varese" in s


def test_no_disponible():
    s = formato_no_disponible(SPOT, "timeout")
    assert "Varese" in s
    assert "timeout" in s


# ---------------------------------------------------------------------------
# Tests: formato_luz_solar
# ---------------------------------------------------------------------------

def test_luz_solar_retorna_str():
    s = formato_luz_solar(make_daylight(), SPOT)
    assert isinstance(s, str)


def test_luz_solar_contiene_emojis():
    s = formato_luz_solar(make_daylight(), SPOT)
    assert "🌅" in s
    assert "🌇" in s


def test_luz_solar_contiene_horas():
    s = formato_luz_solar(make_daylight(), SPOT)
    # Sunrise 09:32 UTC → 06:32 AR
    assert "06:32" in s


def test_luz_solar_contiene_duracion():
    s = formato_luz_solar(make_daylight(), SPOT)
    assert "h de luz" in s


# ---------------------------------------------------------------------------
# Tests: formato_mareas
# ---------------------------------------------------------------------------

def test_mareas_con_eventos():
    s = formato_mareas(make_tide_analysis_con_eventos(), SPOT)
    assert isinstance(s, str)
    assert "MAREAS" in s
    assert "▲" in s  # alta
    assert "▼" in s  # baja


def test_mareas_contiene_proxy_msl():
    """Siempre debe aclarar que es proxy MSL."""
    s = formato_mareas(make_tide_analysis_con_eventos(), SPOT)
    assert "proxy MSL" in s


def test_mareas_horas_en_local_ar():
    """09:00 UTC → 06:00 AR."""
    s = formato_mareas(make_tide_analysis_con_eventos(), SPOT)
    assert "06:00" in s


def test_mareas_sin_extremos_muestra_tendencia():
    s = formato_mareas(make_tide_analysis_sin_extremos(), SPOT)
    assert "subiendo" in s or "Tendencia" in s
    assert "↑" in s


def test_mareas_sin_extremos_no_muestra_triangulos():
    s = formato_mareas(make_tide_analysis_sin_extremos(), SPOT)
    assert "▲" not in s
    assert "▼" not in s


def test_mareas_max_eventos_respetado():
    s = formato_mareas(make_tide_analysis_con_eventos(), SPOT, max_eventos=1)
    # Solo debe haber 1 alta/baja mostrada
    count = s.count("▲") + s.count("▼")
    assert count == 1, f"Con max_eventos=1 esperado 1 evento, got {count}"


# ---------------------------------------------------------------------------
# Tests: formato_mejor_hora
# ---------------------------------------------------------------------------

def test_mejor_hora_retorna_str():
    s = formato_mejor_hora(make_best_hour_result(), SPOT)
    assert isinstance(s, str)


def test_mejor_hora_contiene_titulo():
    s = formato_mejor_hora(make_best_hour_result(), SPOT)
    assert "MEJOR HORA" in s


def test_mejor_hora_contiene_score():
    s = formato_mejor_hora(make_best_hour_result(), SPOT)
    assert "82" in s


def test_mejor_hora_contiene_hora_local():
    # 13:00 UTC → 10:00 AR
    s = formato_mejor_hora(make_best_hour_result(), SPOT)
    assert "10:00" in s


def test_mejor_hora_contiene_swell():
    s = formato_mejor_hora(make_best_hour_result(), SPOT)
    assert "1.5m" in s


def test_mejor_hora_contiene_info_luz():
    s = formato_mejor_hora(make_best_hour_result(), SPOT)
    assert "h" in s  # "13h de luz" o similar


# ---------------------------------------------------------------------------
# Tests: formato_vista_horaria
# ---------------------------------------------------------------------------

def test_vista_horaria_retorna_str():
    s = formato_vista_horaria(make_hourly_view(), SPOT)
    assert isinstance(s, str)


def test_vista_horaria_contiene_spot():
    s = formato_vista_horaria(make_hourly_view(), SPOT)
    assert "Varese" in s


def test_vista_horaria_contiene_icono_mejor():
    s = formato_vista_horaria(make_hourly_view(), SPOT)
    assert "🌟" in s


def test_vista_horaria_contiene_iconos_sol():
    s = formato_vista_horaria(make_hourly_view(), SPOT)
    assert "☀️" in s


def test_vista_horaria_marker_mejor():
    """La mejor hora debe tener el marcador ◀."""
    s = formato_vista_horaria(make_hourly_view(), SPOT)
    assert "◀" in s


def test_vista_horaria_contiene_horas():
    s = formato_vista_horaria(make_hourly_view(), SPOT)
    # Alguna hora local debe aparecer
    assert ":00" in s


# ---------------------------------------------------------------------------
# Tests: formato_semana
# ---------------------------------------------------------------------------

def test_semana_retorna_str():
    s = formato_semana(make_weekly_analysis(), SPOT)
    assert isinstance(s, str)


def test_semana_contiene_mejor_dia():
    s = formato_semana(make_weekly_analysis(), SPOT)
    assert "MEJOR DÍA" in s


def test_semana_contiene_ranking():
    s = formato_semana(make_weekly_analysis(), SPOT)
    assert "RANKING" in s


def test_semana_contiene_spot():
    s = formato_semana(make_weekly_analysis(), SPOT)
    assert "Varese" in s


def test_semana_contiene_marker_mejor():
    s = formato_semana(make_weekly_analysis(), SPOT)
    assert "◀" in s


def test_semana_contiene_dias_buenos():
    s = formato_semana(make_weekly_analysis(), SPOT)
    assert "recomendados" in s or "buenos" in s.lower()


def test_semana_marker_correcto_en_mejor_dia():
    """El ◀ debe estar solo en la línea del mejor día."""
    wa = make_weekly_analysis()
    s = formato_semana(wa, SPOT)
    lineas = s.split("\n")
    lineas_con_marker = [l for l in lineas if "◀" in l]
    assert len(lineas_con_marker) == 1, f"Esperado 1 línea con ◀, got {len(lineas_con_marker)}"


# ---------------------------------------------------------------------------
# Tests: formato_dia_completo
# ---------------------------------------------------------------------------

def test_dia_completo_retorna_str():
    s = formato_dia_completo(
        make_hour(), make_breakdown(), SPOT,
        make_daylight(), make_tide_analysis_con_eventos(), make_best_hour_result()
    )
    assert isinstance(s, str) and len(s) > 0


def test_dia_completo_contiene_todos_los_bloques():
    s = formato_dia_completo(
        make_hour(), make_breakdown(), SPOT,
        make_daylight(), make_tide_analysis_con_eventos(), make_best_hour_result(),
        es_hoy=True,
    )
    assert "CONDICIONES" in s
    assert "75/100" in s  # bloque de calidad (#A1: ya no hay header "CALIDAD" aparte)
    assert "MAREAS" in s
    assert s.index("75/100") < s.index("1.5m")  # calidad antes que datos crudos (#A1)
    assert "MEJOR HORA" in s
    assert "🌅" in s  # luz solar
    # Con tide_analysis, el disclaimer de proxy MSL solo debe aparecer una
    # vez (en el encabezado del bloque MAREAS) — el flag suelto se filtra
    # para no duplicarlo.
    assert s.count("proxy MSL") == 1


def test_dia_completo_sin_mejor_hora_ok():
    """Debe funcionar aunque mejor_hora sea None."""
    s = formato_dia_completo(
        make_hour(), make_breakdown(), SPOT,
        make_daylight(), make_tide_analysis_con_eventos(), None
    )
    assert isinstance(s, str)
    assert "MEJOR HORA" not in s


def test_dia_completo_sin_mareas_ok():
    """Debe funcionar aunque tide_analysis sea None."""
    s = formato_dia_completo(
        make_hour(), make_breakdown(), SPOT,
        make_daylight(), None, make_best_hour_result()
    )
    assert isinstance(s, str)
    # Sin tide_analysis no hay bloque MAREAS — el flag suelto es la única
    # fuente del disclaimer, no debe filtrarse acá.
    assert s.count("proxy MSL") == 1
    assert "MAREAS" not in s


# ---------------------------------------------------------------------------
# formato_proximas_olas — "próxima oportunidad" más allá de 48h (#A2)
# ---------------------------------------------------------------------------

def test_proximas_olas_umbral_compara_score_total_no_score_100_redondeado():
    """
    Regresión #A2: comparar contra round(score_total*100) en vez de
    score_total directo introduce falsos positivos en el borde —
    score_total=0.699 redondea a score_100=70, que pasaría un umbral de
    70 aunque 0.699 sea estrictamente menor a 0.70 en la escala real que
    usa detectar_ventanas() para armar las ventanas óptimas.
    """
    ahora = datetime.now(timezone.utc)
    ts = ahora + timedelta(hours=50)  # más allá de las 48h que ya cubren `ventanas`
    hour = make_hour(ts=ts)

    with patch("core.analysis.daylight.is_daylight", return_value=True):
        with patch("core.scoring.engine.calcular_score", return_value=make_breakdown(0.699)):
            s_bajo = formato_proximas_olas([hour], [], SPOT, umbral_score=0.70)
        with patch("core.scoring.engine.calcular_score", return_value=make_breakdown(0.700)):
            s_alto = formato_proximas_olas([hour], [], SPOT, umbral_score=0.70)

    assert "Próxima oportunidad" not in s_bajo, "0.699 no debería calificar contra un umbral de 0.70"
    assert "Próxima oportunidad" in s_alto, "0.700 sí debería calificar contra un umbral de 0.70"


# ---------------------------------------------------------------------------
# Ejecutar directamente
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        test_condiciones_retorna_str,
        test_condiciones_contiene_ciudad,
        test_condiciones_contiene_nombre_spot,
        test_condiciones_contiene_score,
        test_condiciones_contiene_swell,
        test_condiciones_sin_texto_pro,
        test_condiciones_proxy_msl_label,
        test_condiciones_hora_local_ar,
        test_ventanas_sin_ventanas,
        test_ventanas_con_ventanas,
        test_breakdown_contiene_scores,
        test_no_disponible,
        test_luz_solar_retorna_str,
        test_luz_solar_contiene_emojis,
        test_luz_solar_contiene_horas,
        test_luz_solar_contiene_duracion,
        test_mareas_con_eventos,
        test_mareas_contiene_proxy_msl,
        test_mareas_horas_en_local_ar,
        test_mareas_sin_extremos_muestra_tendencia,
        test_mareas_sin_extremos_no_muestra_triangulos,
        test_mareas_max_eventos_respetado,
        test_mejor_hora_retorna_str,
        test_mejor_hora_contiene_titulo,
        test_mejor_hora_contiene_score,
        test_mejor_hora_contiene_hora_local,
        test_mejor_hora_contiene_swell,
        test_mejor_hora_contiene_info_luz,
        test_vista_horaria_retorna_str,
        test_vista_horaria_contiene_spot,
        test_vista_horaria_contiene_icono_mejor,
        test_vista_horaria_contiene_iconos_sol,
        test_vista_horaria_marker_mejor,
        test_vista_horaria_contiene_horas,
        test_semana_retorna_str,
        test_semana_contiene_mejor_dia,
        test_semana_contiene_ranking,
        test_semana_contiene_spot,
        test_semana_contiene_marker_mejor,
        test_semana_contiene_dias_buenos,
        test_semana_marker_correcto_en_mejor_dia,
        test_dia_completo_retorna_str,
        test_dia_completo_contiene_todos_los_bloques,
        test_dia_completo_sin_mejor_hora_ok,
        test_dia_completo_sin_mareas_ok,
    ]

    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  ✅ {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  ❌ {t.__name__}: {e}")
            import traceback; traceback.print_exc()
            failed += 1

    print(f"\n{'='*50}")
    print(f"  {passed} passed  |  {failed} failed  |  {len(tests)} total")
    print("  🎉 Todos los tests pasaron." if failed == 0 else "  ⚠️  Hay tests fallando.")
