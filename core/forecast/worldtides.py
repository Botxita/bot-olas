"""Proveedor WorldTides (STUB — pago).

WorldTides ofrece:
  - Predicciones de marea de alta precisión basadas en datos de estaciones reales
  - Cobertura global (14,000+ ubicaciones costeras)
  - Extremos y alturas horarias referenciadas a LAT/MLLW estándar

Requiere API key de https://www.worldtides.info
Precios: ~$7/año para uso básico (créditos).

Ideal como complemento a Open-Meteo para mejorar la precisión de mareas.
Los datos de ola y viento seguirían viniendo de Open-Meteo; WorldTides
solo mejora la capa de marea.

Para activar:
  1. Registrar en worldtides.info y obtener API key
  2. Agregar WORLDTIDES_API_KEY al .env
  3. Este proveedor NO reemplaza al principal (Open-Meteo/Stormglass);
     se usa como proveedor de marea adicional en TideEnricher (futuro)

Documentación: https://www.worldtides.info/apidocs
"""

import logging
import os
from datetime import datetime, timezone
from typing import List

from .provider_base import ForecastProviderBase, ForecastProviderError
from ..scoring.models import ForecastHour, SpotConfig, TideData

logger = logging.getLogger(__name__)


class WorldTidesProvider(ForecastProviderBase):
    """
    STUB — Proveedor especializado en mareas reales.
    Pensado para ser usado como enricher de TideData, no como proveedor completo.
    """

    _BASE_URL = "https://www.worldtides.info/api/v3"

    def __init__(self):
        self._api_key = os.getenv("WORLDTIDES_API_KEY", "")

    @property
    def nombre(self) -> str:
        return "worldtides"

    def get_forecast_48h(self, spot: SpotConfig) -> List[ForecastHour]:
        """No implementado como proveedor completo — solo mareas."""
        raise NotImplementedError(
            "WorldTides es un proveedor de mareas, no de forecast completo. "
            "Usar get_tides_48h() y combinar con otro proveedor."
        )

    def get_forecast_current(self, spot: SpotConfig) -> ForecastHour:
        raise NotImplementedError("Ver get_forecast_48h().")

    def get_tides_48h(self, spot: SpotConfig) -> List[TideData]:
        """
        Obtiene mareas reales para las próximas 48h.
        Retorna lista de TideData con es_exacto=True.

        TODO: Implementar llamada a:
          GET /heights?lat=&lon=&start=&length=172800&step=3600&datum=LAT&key=
        """
        self._check_key()
        raise NotImplementedError(
            "WorldTides provider no implementado aún. "
            "Ver docstring de este módulo para instrucciones."
        )

    def _check_key(self):
        if not self._api_key:
            raise ForecastProviderError(
                "WORLDTIDES_API_KEY no configurada en el .env."
            )

    # ------------------------------------------------------------------
    # Estructura de implementación futura
    # ------------------------------------------------------------------
    # def _fetch_heights(self, lat, lon, start_unix, length_s=172800, step_s=3600):
    #     params = {
    #         "lat": lat, "lon": lon,
    #         "start": start_unix, "length": length_s, "step": step_s,
    #         "datum": "LAT",  # Lowest Astronomical Tide = referencia estándar
    #         "heights": "",   # solicitar array de alturas
    #         "key": self._api_key,
    #     }
    #     resp = requests.get(f"{self._BASE_URL}/heights", params=params)
    #     resp.raise_for_status()
    #     data = resp.json()
    #     return [
    #         TideData(nivel_m=h["height"], fuente="worldtides", es_exacto=True)
    #         for h in data.get("heights", [])
    #     ]
