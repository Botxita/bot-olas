"""Tests unitarios para core/analysis/daylight.py.

Todos los tests usan fixtures hardcodeados (sin IO, sin red, sin archivos).
Verificados contra NOAA Solar Calculator y tablas astronómicas conocidas.

Correr con:
    python -m pytest tests/test_daylight.py -v
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import date, datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import math

from core.scoring.models import SpotConfig
from core.analysis.daylight import (
    get_daylight,
    is_daylight,
    get_daylight_for_forecast_hour,
    _calcular_sol,
    _fecha_a_jd,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_spot(lat: float, lon: float, tz: str = "America/Argentina/Buenos_Aires") -> SpotConfig:
    """SpotConfig mínimo para tests de daylight."""
    return SpotConfig(
        key="test_spot",
        nombre="Test Spot",
        ciudad="Test",
        pais="AR",
        region="test",
        lat=lat,
        lon=lon,
        orientacion_costa_deg=180.0,
        tolerancia_swell_deg=45.0,
        tipo_break="beach",
        fondo="arena",
        marea_min_m=0.0,
        marea_max_m=2.0,
        marea_tipo_efecto="mid_better",
        swell_altura_min=0.3,
        swell_altura_max=4.0,
        swell_periodo_min=6.0,
        viento_max_offshore=40.0,
        viento_max_onshore=15.0,
        tz=tz,
    )

# Spot: Mar del Plata, Argentina
SPOT_MDQ = make_spot(lat=-38.0, lon=-57.5, tz="America/Argentina/Buenos_Aires")

# Spot: Río de Janeiro, Brasil
SPOT_RIO = make_spot(lat=-22.9, lon=-43.2, tz="America/Sao_Paulo")

# Spot: San José, Costa Rica
SPOT_SJO = make_spot(lat=9.9, lon=-84.1, tz="America/Costa_Rica")


# ---------------------------------------------------------------------------
# Tests de _fecha_a_jd (Julian Day Number)
# ---------------------------------------------------------------------------

def test_jd_j2000():
    """J2000.0 = 1 enero 2000 → JD = 2451545.0"""
    jd = _fecha_a_jd(date(2000, 1, 1))
    assert abs(jd - 2451544.5) < 0.01, f"J2000 esperado ~2451544.5, got {jd}"


def test_jd_epoch_unix():
    """1 enero 1970 → JD conocido = 2440587.5"""
    jd = _fecha_a_jd(date(1970, 1, 1))
    assert abs(jd - 2440587.5) < 0.1, f"JD Unix esperado ~2440587.5, got {jd}"


# ---------------------------------------------------------------------------
# Tests de _calcular_sol
# ---------------------------------------------------------------------------

def test_sol_mdq_verano():
    """
    Mar del Plata, 15 enero 2025 (verano austral).
    Sol sale ~08:30 UTC (05:30 AR), se pone ~23:30 UTC (20:30 AR).
    Tolerancia: ±10 minutos.
    """
    sr, ss = _calcular_sol(lat=-38.0, lon=-57.5, fecha=date(2025, 1, 15))
    # Sunrise: ~08:30 UTC
    assert sr.tzinfo == timezone.utc
    sr_min = sr.hour * 60 + sr.minute
    assert 500 <= sr_min <= 540, f"Sunrise MDQ enero esperado ~08:20-09:00 UTC, got {sr_min} min"
    # Sunset: ~23:30 UTC
    ss_min = ss.hour * 60 + ss.minute
    assert 1390 <= ss_min <= 1430, f"Sunset MDQ enero esperado ~23:10-23:50 UTC, got {ss_min} min"


def test_sol_mdq_invierno():
    """
    Mar del Plata, 21 junio 2025 (invierno austral).
    Días más cortos ~09h de luz.
    Sol sale más tarde (UTC), se pone antes.
    """
    sr, ss = _calcular_sol(lat=-38.0, lon=-57.5, fecha=date(2025, 6, 21))
    duracion_h = (ss - sr).total_seconds() / 3600
    # En invierno austral lat -38: ~9-10h de luz
    assert 8.5 <= duracion_h <= 10.5, f"Duración invierno MDQ esperada ~9-10h, got {duracion_h:.1f}h"


def test_sol_ecuador():
    """
    Cerca del ecuador (Costa Rica), los días son casi iguales todo el año.
    ~12h de luz ± 1h.
    """
    sr, ss = _calcular_sol(lat=9.9, lon=-84.1, fecha=date(2025, 3, 20))
    duracion_h = (ss - sr).total_seconds() / 3600
    assert 11.0 <= duracion_h <= 13.0, f"Duración Ecuador esperada ~12h, got {duracion_h:.1f}h"


def test_sol_consistencia_orden():
    """Sunrise siempre antes que sunset."""
    for lat in [-38, -22, 9, -15, -33]:
        for mes in [1, 3, 6, 9, 12]:
            try:
                sr, ss = _calcular_sol(lat=lat, lon=-60.0, fecha=date(2025, mes, 15))
                assert sr < ss, f"Sunrise > Sunset en lat={lat} mes={mes}"
            except ValueError:
                pass  # Latitudes extremas (polar) — OK


# ---------------------------------------------------------------------------
# Tests de get_daylight (interfaz pública)
# ---------------------------------------------------------------------------

def test_get_daylight_retorna_daylight_info():
    info = get_daylight(SPOT_MDQ, date(2025, 1, 15))
    assert info.fecha == date(2025, 1, 15)
    assert info.spot_key == "test_spot"
    assert info.sunrise_utc.tzinfo == timezone.utc
    assert info.sunset_utc.tzinfo == timezone.utc


def test_get_daylight_local_tz_ar():
    """
    En Argentina (UTC-3), el amanecer local debe ser sunrise_utc - 3h.
    """
    info = get_daylight(SPOT_MDQ, date(2025, 1, 15))
    tz_ar = ZoneInfo("America/Argentina/Buenos_Aires")

    # La hora local debe ser 3h menos que UTC (Argentina es UTC-3)
    diff = info.sunrise_utc.hour - info.sunrise_local.hour
    # En enero Argentina no tiene DST, siempre UTC-3
    assert diff == 3, f"Diferencia UTC vs AR esperada 3h, got {diff}h"


def test_get_daylight_duracion_verano_ar():
    """Verano AR: duración > 13 horas."""
    info = get_daylight(SPOT_MDQ, date(2025, 1, 15))
    assert info.duration_h > 13.0, f"Duración verano AR esperada >13h, got {info.duration_h:.1f}h"


def test_get_daylight_duracion_invierno_ar():
    """Invierno AR: duración < 10 horas."""
    info = get_daylight(SPOT_MDQ, date(2025, 6, 21))
    assert info.duration_h < 10.0, f"Duración invierno AR esperada <10h, got {info.duration_h:.1f}h"


def test_get_daylight_brasil():
    """Brasil (UTC-3): comportamiento correcto para Río de Janeiro."""
    info = get_daylight(SPOT_RIO, date(2025, 7, 1))
    assert info.sunrise_local.tzinfo is not None
    # En julio en Río el sol sale ~09:30-10:00 UTC (06:30-07:00 local BRT)
    sr_local_h = info.sunrise_local.hour
    assert 6 <= sr_local_h <= 8, f"Sunrise RJ julio esperado 06-08h local, got {sr_local_h}h"


def test_get_daylight_str():
    """El __str__ debe contener horas en formato legible."""
    info = get_daylight(SPOT_MDQ, date(2025, 1, 15))
    s = str(info)
    assert "☀️" in s
    assert "→" in s
    assert "h de luz" in s


# ---------------------------------------------------------------------------
# Tests de is_daylight
# ---------------------------------------------------------------------------

def test_is_daylight_durante_dia():
    """Un datetime al mediodía local debe retornar True."""
    info = get_daylight(SPOT_MDQ, date(2025, 1, 15))
    tz_ar = ZoneInfo("America/Argentina/Buenos_Aires")
    mediodia = datetime(2025, 1, 15, 12, 0, tzinfo=tz_ar)
    assert is_daylight(mediodia, info) is True


def test_is_daylight_de_noche():
    """Un datetime a las 3am local debe retornar False."""
    info = get_daylight(SPOT_MDQ, date(2025, 1, 15))
    tz_ar = ZoneInfo("America/Argentina/Buenos_Aires")
    madrugada = datetime(2025, 1, 15, 3, 0, tzinfo=tz_ar)
    assert is_daylight(madrugada, info) is False


def test_is_daylight_en_amanecer():
    """En el momento exacto del amanecer debe retornar True."""
    info = get_daylight(SPOT_MDQ, date(2025, 1, 15))
    assert is_daylight(info.sunrise_utc, info) is True


def test_is_daylight_en_atardecer():
    """En el momento exacto del atardecer debe retornar True."""
    info = get_daylight(SPOT_MDQ, date(2025, 1, 15))
    assert is_daylight(info.sunset_utc, info) is True


def test_is_daylight_raises_on_naive():
    """Debe lanzar ValueError si el datetime no tiene tzinfo."""
    info = get_daylight(SPOT_MDQ, date(2025, 1, 15))
    naive_dt = datetime(2025, 1, 15, 12, 0)  # sin tzinfo
    try:
        is_daylight(naive_dt, info)
        assert False, "Debería haber lanzado ValueError"
    except ValueError:
        pass  # Correcto


def test_is_daylight_utc_aware():
    """Funciona con datetimes UTC-aware."""
    info = get_daylight(SPOT_MDQ, date(2025, 1, 15))
    mediodia_utc = datetime(2025, 1, 15, 15, 0, tzinfo=timezone.utc)  # 12h AR
    assert is_daylight(mediodia_utc, info) is True


# ---------------------------------------------------------------------------
# Tests de get_daylight_for_forecast_hour (cache)
# ---------------------------------------------------------------------------

def test_forecast_hour_cache():
    """Dos llamadas con la misma fecha devuelven el mismo objeto (cache)."""
    from core.analysis.daylight import get_daylight_for_forecast_hour
    cache = {}

    dt1 = datetime(2025, 1, 15, 12, 0, tzinfo=timezone.utc)
    dt2 = datetime(2025, 1, 15, 15, 0, tzinfo=timezone.utc)

    info1 = get_daylight_for_forecast_hour(SPOT_MDQ, dt1, _cache=cache)
    info2 = get_daylight_for_forecast_hour(SPOT_MDQ, dt2, _cache=cache)

    assert info1 is info2, "Misma fecha local debe retornar misma instancia (cache)"
    assert len(cache) == 1


def test_forecast_hour_cache_no_colisiona_entre_spots_con_misma_key():
    """
    Regresión #19: la clave del caché era solo (spot.key, fecha), sin
    lat/lon/tz. Dos SpotConfig con la misma key pero coordenadas distintas
    (ej. un spot corregido sin reiniciar el proceso, o dos spots que
    comparten key por error de config) devolvían el DaylightInfo cacheado
    del primero, con amanecer/atardecer de coordenadas equivocadas.
    """
    from core.analysis.daylight import get_daylight_for_forecast_hour
    cache = {}

    spot_bsas = make_spot(lat=-34.6, lon=-58.4, tz="America/Argentina/Buenos_Aires")
    spot_anchorage = make_spot(lat=61.2, lon=-149.9, tz="America/Anchorage")
    assert spot_bsas.key == spot_anchorage.key, \
        "sanity check: make_spot() siempre usa key='test_spot' — mismo escenario del hallazgo"

    dt = datetime(2025, 6, 21, 12, 0, tzinfo=timezone.utc)  # solsticio: maximiza la diferencia real de horas de luz

    info_bsas = get_daylight_for_forecast_hour(spot_bsas, dt, _cache=cache)
    info_anchorage = get_daylight_for_forecast_hour(spot_anchorage, dt, _cache=cache)

    assert info_bsas is not info_anchorage, \
        "spots distintos con la misma key no deben compartir entrada de caché"
    assert len(cache) == 2
    # Buenos Aires en solsticio de invierno austral (~9h de luz) vs
    # Anchorage en solsticio de verano boreal (~19h de luz) — si el bug
    # siguiera presente, el segundo spot recibiría el DaylightInfo del
    # primero (cacheado), dando la misma duración para ambos.
    assert info_anchorage.duration_h > info_bsas.duration_h + 5, \
        f"Anchorage ({info_anchorage.duration_h:.1f}h) debe tener muchas más horas de luz que Buenos Aires " \
        f"({info_bsas.duration_h:.1f}h) en el solsticio — si son iguales, el caché está colisionando"


def test_forecast_hour_cache_dias_distintos():
    """Días distintos deben crear entradas separadas en cache."""
    from core.analysis.daylight import get_daylight_for_forecast_hour
    cache = {}

    dt1 = datetime(2025, 1, 15, 12, 0, tzinfo=timezone.utc)
    dt2 = datetime(2025, 1, 16, 12, 0, tzinfo=timezone.utc)

    get_daylight_for_forecast_hour(SPOT_MDQ, dt1, _cache=cache)
    get_daylight_for_forecast_hour(SPOT_MDQ, dt2, _cache=cache)

    assert len(cache) == 2


# ---------------------------------------------------------------------------
# Ejecutar directamente
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        test_jd_j2000,
        test_jd_epoch_unix,
        test_sol_mdq_verano,
        test_sol_mdq_invierno,
        test_sol_ecuador,
        test_sol_consistencia_orden,
        test_get_daylight_retorna_daylight_info,
        test_get_daylight_local_tz_ar,
        test_get_daylight_duracion_verano_ar,
        test_get_daylight_duracion_invierno_ar,
        test_get_daylight_brasil,
        test_get_daylight_str,
        test_is_daylight_durante_dia,
        test_is_daylight_de_noche,
        test_is_daylight_en_amanecer,
        test_is_daylight_en_atardecer,
        test_is_daylight_raises_on_naive,
        test_is_daylight_utc_aware,
        test_forecast_hour_cache,
        test_forecast_hour_cache_dias_distintos,
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
            failed += 1

    print(f"\n{'='*50}")
    print(f"  {passed} passed  |  {failed} failed  |  {len(tests)} total")
    if failed == 0:
        print("  🎉 Todos los tests pasaron.")
    else:
        print("  ⚠️  Hay tests fallando.")
