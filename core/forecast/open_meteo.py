"""Proveedor Open-Meteo — gratis, sin API key, cobertura global.

APIs utilizadas:
  - Marine Forecast: olas, swell, período, dirección
    https://marine-api.open-meteo.com/v1/marine

  - Weather Forecast: viento, ráfagas
    https://api.open-meteo.com/v1/forecast

  - Sea Level: sea_level_height_msl como proxy de marea
    (Variable disponible en la Marine API)

NOTA IMPORTANTE sobre mareas:
  sea_level_height_msl incorpora componentes mareales pero no está
  referenciado a LAT/MLLW estándar náutico. La precisión costera
  puede ser limitada. Se usa como indicador relativo para el scoring
  surfer, NO para navegación. Calibrar por spot via delta_altura.
"""

import logging
from datetime import datetime, timezone
from typing import List, Optional

import requests

from .provider_base import ForecastProviderBase, ForecastProviderError
from ..scoring.models import (
    ForecastHour,
    SpotConfig,
    SwellData,
    TideData,
    WindData,
)

logger = logging.getLogger(__name__)

# URLs base de Open-Meteo
_MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"
_WEATHER_URL = "https://api.open-meteo.com/v1/forecast"

# Timeout para requests
_TIMEOUT_S = 15


class OpenMeteoProvider(ForecastProviderBase):
    """
    Implementación completa del proveedor Open-Meteo.
    No requiere API key. Límite: ~10,000 req/día por IP (free tier).
    """

    @property
    def nombre(self) -> str:
        return "open-meteo"

    def get_forecast_48h(self, spot: SpotConfig) -> List[ForecastHour]:
        """Obtiene los próximos 7 días de pronóstico (168h).
        El nombre se mantiene por compatibilidad con la interfaz base."""
        marine_data = self._fetch_marine(spot)
        weather_data = self._fetch_weather(spot)
        return self._parse_combined(marine_data, weather_data)

    def get_forecast_current(self, spot: SpotConfig) -> ForecastHour:
        """Obtiene la hora más cercana al momento actual."""
        hours = self.get_forecast_48h(spot)
        if not hours:
            raise ForecastProviderError("No hay datos disponibles para el spot.")

        now = datetime.now(timezone.utc)
        # Encontrar la hora más cercana
        closest = min(hours, key=lambda h: abs((h.timestamp - now).total_seconds()))
        return closest

    # ------------------------------------------------------------------
    # Métodos privados
    # ------------------------------------------------------------------

    def _fetch_marine(self, spot: SpotConfig) -> dict:
        """Llama a la Marine API y retorna el JSON crudo."""
        params = {
            "latitude": spot.lat,
            "longitude": spot.lon,
            "hourly": ",".join([
                "wave_height",
                "wave_period",
                "wave_direction",
                "wind_wave_height",
                "swell_wave_height",
                "swell_wave_period",
                "swell_wave_direction",
                "sea_level_height_msl",
                "sea_surface_temperature",
            ]),
            "forecast_days": 7,  # 7 días = 168h
            "timezone": "UTC",
        }
        try:
            resp = requests.get(_MARINE_URL, params=params, timeout=_TIMEOUT_S)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            raise ForecastProviderError(f"Error al llamar Marine API: {e}") from e

    def _fetch_weather(self, spot: SpotConfig) -> dict:
        """Llama a la Weather API para datos de viento."""
        params = {
            "latitude": spot.lat,
            "longitude": spot.lon,
            "hourly": ",".join([
                "wind_speed_10m",
                "wind_gusts_10m",
                "wind_direction_10m",
            ]),
            "wind_speed_unit": "kmh",
            "forecast_days": 7,
            "timezone": "UTC",
        }
        try:
            resp = requests.get(_WEATHER_URL, params=params, timeout=_TIMEOUT_S)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            raise ForecastProviderError(f"Error al llamar Weather API: {e}") from e

    def _parse_combined(self, marine: dict, weather: dict) -> List[ForecastHour]:
        """
        Combina datos de marine + weather y construye la lista de ForecastHour.
        Prioriza swell_wave_* sobre wave_* cuando está disponible.
        """
        try:
            marine_h = marine["hourly"]
            weather_h = weather["hourly"]
            times = marine_h["time"]

            # Tomar todas las horas disponibles (hasta 168h = 7 días)
            limit = len(times)

            result = []
            for i in range(limit):
                ts_str = times[i]
                ts = datetime.fromisoformat(ts_str).replace(tzinfo=timezone.utc)

                # Swell: usar swell_wave_* si disponible, else wave_*
                swell_h = _safe_get(marine_h, "swell_wave_height", i)
                swell_p = _safe_get(marine_h, "swell_wave_period", i)
                swell_d = _safe_get(marine_h, "swell_wave_direction", i)

                wave_h = _safe_get(marine_h, "wave_height", i)
                wave_p = _safe_get(marine_h, "wave_period", i)
                wave_d = _safe_get(marine_h, "wave_direction", i)

                # Si hay componente swell separado, usarlo; sino usar wave total
                altura = swell_h if swell_h is not None else (wave_h or 0.0)
                periodo = swell_p if swell_p is not None else (wave_p or 8.0)
                direccion_ola = swell_d if swell_d is not None else (wave_d or 0.0)
                altura_viento = _safe_get(marine_h, "wind_wave_height", i) or 0.0

                swell = SwellData(
                    altura_m=float(altura),
                    periodo_s=float(periodo),
                    direccion_deg=float(direccion_ola),
                    altura_viento_m=float(altura_viento),
                )

                # Viento
                vel = _safe_get(weather_h, "wind_speed_10m", i) or 0.0
                raf = _safe_get(weather_h, "wind_gusts_10m", i) or 0.0
                dir_v = _safe_get(weather_h, "wind_direction_10m", i) or 0.0

                wind = WindData(
                    velocidad_kmh=float(vel),
                    rafaga_kmh=float(raf),
                    direccion_deg=float(dir_v),
                )

                # Marea (proxy MSL)
                nivel = _safe_get(marine_h, "sea_level_height_msl", i)
                if nivel is None:
                    nivel = 0.0  # fallback: sin dato de marea
                    logger.debug("sea_level_height_msl no disponible para %s", ts_str)

                tide = TideData(
                    nivel_m=float(nivel),
                    fuente="proxy_msl",
                    es_exacto=False,
                )

                temp_agua = _safe_get(marine_h, "sea_surface_temperature", i)

                result.append(ForecastHour(timestamp=ts, swell=swell, wind=wind, tide=tide, temp_agua_c=temp_agua))

            return result

        except (KeyError, IndexError, TypeError) as e:
            raise ForecastProviderError(f"Error al parsear respuesta Open-Meteo: {e}") from e


def _safe_get(data: dict, key: str, index: int):
    """Acceso seguro a lista anidada; retorna None si falta el dato."""
    arr = data.get(key)
    if arr is None or index >= len(arr):
        return None
    return arr[index]
