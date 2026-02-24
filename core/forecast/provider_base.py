"""Provider base para proveedores de datos de pronóstico.

Pattern: ABC (Abstract Base Class) que define el contrato.
Cada proveedor implementa esta interface.

Proveedores disponibles:
  - OpenMeteoProvider (IMPLEMENTADO — gratis, sin API key)
  - StormglassProvider (STUB — pago, alta precisión, mareas reales)
  - WorldTidesProvider  (STUB — pago, estaciones de marea globales)

Para agregar un proveedor nuevo:
  1. Crear archivo en core/forecast/
  2. Heredar de ForecastProviderBase
  3. Implementar get_forecast_48h()
  4. Registrar en core/forecast/__init__.py
"""

from abc import ABC, abstractmethod
from typing import List

from ..scoring.models import ForecastHour, SpotConfig


class ForecastProviderBase(ABC):
    """Interface que todo proveedor de forecast debe implementar."""

    @property
    @abstractmethod
    def nombre(self) -> str:
        """Identificador legible del proveedor."""
        ...

    @abstractmethod
    def get_forecast_48h(self, spot: SpotConfig) -> List[ForecastHour]:
        """
        Obtiene pronóstico horario para las próximas 48 horas.

        Args:
            spot: configuración del spot con lat/lon y ajustes

        Returns:
            Lista de ForecastHour ordenada cronológicamente.
            Típicamente 48 elementos (una entrada por hora).

        Raises:
            ForecastProviderError: si la API falla o los datos son inválidos.
        """
        ...

    @abstractmethod
    def get_forecast_current(self, spot: SpotConfig) -> ForecastHour:
        """
        Obtiene las condiciones más cercanas a ahora mismo.

        Returns:
            Un único ForecastHour correspondiente a la hora actual.
        """
        ...


class ForecastProviderError(Exception):
    """Error al obtener o procesar datos del proveedor."""
    pass
