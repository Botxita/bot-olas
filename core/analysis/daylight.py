"""Cálculo de amanecer y atardecer para un spot geográfico.

Implementación basada en el algoritmo NOAA Solar Calculator
(misma base que usa la librería `astral`), usando únicamente stdlib.
Precisión: ±1 minuto para latitudes entre 60°S y 60°N.

Sin API externa. Sin dependencias de terceros.

Uso:
    info = get_daylight(spot, date(2025, 1, 15))
    print(info.sunrise_local)   # datetime aware en tz del spot
    print(info.sunset_local)    # datetime aware en tz del spot
    if is_daylight(some_dt, info):
        ...
"""

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from core.scoring.models import SpotConfig


@dataclass
class DaylightInfo:
    """Información de luz solar para un día y spot específico."""
    fecha: date
    spot_key: str

    # Sunrise y sunset en UTC (always aware)
    sunrise_utc: datetime
    sunset_utc: datetime

    # Sunrise y sunset en la timezone local del spot (always aware)
    sunrise_local: datetime
    sunset_local: datetime

    @property
    def duration_h(self) -> float:
        """Horas de luz del día."""
        delta = self.sunset_utc - self.sunrise_utc
        return delta.total_seconds() / 3600

    def __str__(self) -> str:
        sr = self.sunrise_local.strftime("%H:%M")
        ss = self.sunset_local.strftime("%H:%M")
        return f"☀️ {sr} → {ss} ({self.duration_h:.1f}h de luz)"


def get_daylight(spot: SpotConfig, fecha: date) -> DaylightInfo:
    """
    Calcula amanecer y atardecer para el spot en la fecha dada.

    Args:
        spot: SpotConfig con lat, lon, y tz válidos.
        fecha: Fecha local del spot (date object).

    Returns:
        DaylightInfo con sunrise/sunset en UTC y en tz local del spot.

    Raises:
        ValueError: Si el sol no sale o no se pone ese día (latitudes extremas).
    """
    tz = spot.get_zoneinfo()

    sunrise_utc, sunset_utc = _calcular_sol(spot.lat, spot.lon, fecha)

    sunrise_local = sunrise_utc.astimezone(tz)
    sunset_local = sunset_utc.astimezone(tz)

    return DaylightInfo(
        fecha=fecha,
        spot_key=spot.key,
        sunrise_utc=sunrise_utc,
        sunset_utc=sunset_utc,
        sunrise_local=sunrise_local,
        sunset_local=sunset_local,
    )


def is_daylight(dt: datetime, info: DaylightInfo) -> bool:
    """
    Retorna True si el datetime cae dentro del período de luz solar.

    Args:
        dt: datetime aware (cualquier timezone). Se convierte a UTC internamente.
        info: DaylightInfo del día y spot correspondiente.
    """
    if dt.tzinfo is None:
        raise ValueError("dt debe ser timezone-aware.")
    dt_utc = dt.astimezone(timezone.utc)
    return info.sunrise_utc <= dt_utc <= info.sunset_utc


def get_daylight_for_forecast_hour(
    spot: SpotConfig,
    dt: datetime,
    _cache: dict = {},
) -> DaylightInfo:
    """
    Helper con cache in-process: dado un datetime, obtiene el DaylightInfo
    del día correspondiente en la tz del spot.
    Útil para iterar sobre un forecast sin recalcular cada hora.
    """
    tz = spot.get_zoneinfo()
    dt_local = dt.astimezone(tz)
    # La clave incluye lat/lon/tz, no solo spot.key — este caché vive toda
    # la vida del proceso (default mutable de argumento), así que si dos
    # spots comparten `key` por error de config, o un spot se corrige
    # (lat/lon) sin reiniciar el proceso, spot.key solo ya no identifica de
    # forma única las coordenadas usadas para calcular el amanecer/atardecer
    # — se devolvía el DaylightInfo viejo cacheado con las coordenadas
    # equivocadas (#19).
    cache_key = (spot.key, spot.lat, spot.lon, spot.tz, dt_local.date())
    if cache_key not in _cache:
        _cache[cache_key] = get_daylight(spot, dt_local.date())
    return _cache[cache_key]


# ---------------------------------------------------------------------------
# Implementación interna — Algoritmo NOAA
# ---------------------------------------------------------------------------

def _calcular_sol(lat: float, lon: float, fecha: date):
    """
    Calcula sunrise y sunset en UTC usando el algoritmo NOAA.
    Retorna (sunrise_utc, sunset_utc) como datetime aware UTC.
    """
    # Número de días desde J2000.0
    jd = _fecha_a_jd(fecha)
    jc = (jd - 2451545.0) / 36525.0

    # Longitud media del sol (grados)
    geom_mean_long = (280.46646 + jc * (36000.76983 + jc * 0.0003032)) % 360

    # Anomalía media del sol (grados)
    geom_mean_anom = 357.52911 + jc * (35999.05029 - 0.0001537 * jc)

    # Excentricidad de la órbita terrestre
    eccent = 0.016708634 - jc * (0.000042037 + 0.0000001267 * jc)

    # Centro del sol (grados)
    eq_ctr = (
        math.sin(math.radians(geom_mean_anom)) * (1.914602 - jc * (0.004817 + 0.000014 * jc))
        + math.sin(math.radians(2 * geom_mean_anom)) * (0.019993 - 0.000101 * jc)
        + math.sin(math.radians(3 * geom_mean_anom)) * 0.000289
    )

    # Longitud verdadera del sol
    sun_true_long = geom_mean_long + eq_ctr

    # Longitud aparente del sol (corrección de aberración)
    sun_app_long = sun_true_long - 0.00569 - 0.00478 * math.sin(
        math.radians(125.04 - 1934.136 * jc)
    )

    # Oblicuidad eclíptica media
    mean_obliq = (
        23.0
        + (26.0 + (21.448 - jc * (46.8150 + jc * (0.00059 - jc * 0.001813))) / 60.0) / 60.0
    )

    # Oblicuidad corregida
    obliq_corr = mean_obliq + 0.00256 * math.cos(math.radians(125.04 - 1934.136 * jc))

    # Declinación solar (radianes)
    sun_declin = math.asin(
        math.sin(math.radians(obliq_corr)) * math.sin(math.radians(sun_app_long))
    )

    # Ecuación del tiempo (minutos)
    var_y = math.tan(math.radians(obliq_corr / 2)) ** 2
    eq_time = 4 * math.degrees(
        var_y * math.sin(2 * math.radians(geom_mean_long))
        - 2 * eccent * math.sin(math.radians(geom_mean_anom))
        + 4 * eccent * var_y * math.sin(math.radians(geom_mean_anom)) * math.cos(2 * math.radians(geom_mean_long))
        - 0.5 * var_y ** 2 * math.sin(4 * math.radians(geom_mean_long))
        - 1.25 * eccent ** 2 * math.sin(2 * math.radians(geom_mean_anom))
    )

    # Ángulo horario del amanecer (grados)
    lat_rad = math.radians(lat)
    cos_ha = (
        math.cos(math.radians(90.833)) / (math.cos(lat_rad) * math.cos(sun_declin))
        - math.tan(lat_rad) * math.tan(sun_declin)
    )

    # cos_ha fuera de [-1, 1] indica sol de medianoche o noche polar
    if cos_ha < -1 or cos_ha > 1:
        raise ValueError(
            f"Sol no sale/no se pone en lat={lat} el {fecha} "
            f"(cos_ha={cos_ha:.3f}). Fenómeno polar."
        )

    ha_sunrise_deg = math.degrees(math.acos(cos_ha))

    # Mediodía solar (minutos desde medianoche UTC)
    solar_noon_minutes = (720 - 4 * lon - eq_time)

    # Sunrise y sunset en minutos desde medianoche UTC
    sunrise_minutes = solar_noon_minutes - ha_sunrise_deg * 4
    sunset_minutes = solar_noon_minutes + ha_sunrise_deg * 4

    # Convertir a datetime UTC
    midnight_utc = datetime(fecha.year, fecha.month, fecha.day, tzinfo=timezone.utc)
    sunrise_utc = midnight_utc + timedelta(minutes=sunrise_minutes)
    sunset_utc = midnight_utc + timedelta(minutes=sunset_minutes)

    return sunrise_utc, sunset_utc


def _fecha_a_jd(fecha: date) -> float:
    """Convierte date a Número de Día Juliano (JD)."""
    y, m, d = fecha.year, fecha.month, fecha.day
    if m <= 2:
        y -= 1
        m += 12
    a = y // 100
    b = 2 - a + a // 4
    return int(365.25 * (y + 4716)) + int(30.6001 * (m + 1)) + d + b - 1524.5
