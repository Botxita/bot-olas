"""Proveedor Stormglass (STUB — pago).

Stormglass ofrece:
  - Pronóstico marino de alta precisión (fuentes combinadas: NOAA, MetNo, ECMWF, etc.)
  - Datos de mareas con estaciones reales (tide.extremes + tide.hours)
  - Resolución horaria global

Requiere API key de https://stormglass.io
Precios: ~$29/mes para 1,000 req/día.

Para activar este proveedor:
  1. Completar _fetch_marine() y _fetch_tides() con la API real
  2. Agregar STORMGLASS_API_KEY al archivo .env
  3. Cambiar fuente_datos: "stormglass" en los JSONs de spots donde quieras usarlo
  4. Registrar el proveedor en core/forecast/__init__.py

Documentación: https://docs.stormglass.io
"""

import logging
import os
from datetime import datetime, timezone
from typing import List

from .provider_base import ForecastProviderBase, ForecastProviderError
from ..scoring.models import ForecastHour, SpotConfig

logger = logging.getLogger(__name__)


class StormglassProvider(ForecastProviderBase):
    """
    STUB — No implementado.
    Activa cuando tengas API key de Stormglass.
    """

    _BASE_URL = "https://api.stormglass.io/v2"

    def __init__(self):
        self._api_key = os.getenv("STORMGLASS_API_KEY", "")

    @property
    def nombre(self) -> str:
        return "stormglass"

    def get_forecast_48h(self, spot: SpotConfig) -> List[ForecastHour]:
        self._check_key()
        # TODO: Implementar llamadas a:
        #   GET /weather/point con params: lat, lng, params, start, end
        #   GET /tide/extremes/point para mareas reales
        # Combinar y construir lista de ForecastHour
        raise NotImplementedError(
            "Stormglass provider no implementado aún. "
            "Ver docstring de este módulo para instrucciones."
        )

    def get_forecast_current(self, spot: SpotConfig) -> ForecastHour:
        self._check_key()
        hours = self.get_forecast_48h(spot)
        now = datetime.now(timezone.utc)
        return min(hours, key=lambda h: abs((h.timestamp - now).total_seconds()))

    def _check_key(self):
        if not self._api_key:
            raise ForecastProviderError(
                "STORMGLASS_API_KEY no configurada. "
                "Agrega la variable al archivo .env para usar este proveedor."
            )

    # ------------------------------------------------------------------
    # Estructura de implementación futura (referencia)
    # ------------------------------------------------------------------
    # def _fetch_marine(self, lat, lon, start, end):
    #     headers = {"Authorization": self._api_key}
    #     params = {
    #         "lat": lat, "lng": lon,
    #         "params": "waveHeight,wavePeriod,waveDirection,windSpeed,windDirection,windGusts",
    #         "start": start.isoformat(), "end": end.isoformat(),
    #         "source": "sg",  # usar modelo combinado de Stormglass
    #     }
    #     resp = requests.get(f"{self._BASE_URL}/weather/point", headers=headers, params=params)
    #     resp.raise_for_status()
    #     return resp.json()
    #
    # def _fetch_tides(self, lat, lon, start, end):
    #     headers = {"Authorization": self._api_key}
    #     params = {"lat": lat, "lng": lon,
    #               "start": start.isoformat(), "end": end.isoformat()}
    #     resp = requests.get(f"{self._BASE_URL}/tide/extremes/point", headers=headers, params=params)
    #     resp.raise_for_status()
    #     return resp.json()
