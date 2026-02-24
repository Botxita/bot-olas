"""Módulo de forecast — registro de proveedores y factory.

Para agregar un proveedor nuevo:
  1. Crear clase en core/forecast/mi_proveedor.py heredando ForecastProviderBase
  2. Importarla aquí
  3. Agregarla al dict _PROVIDERS con su nombre clave
"""

from .provider_base import ForecastProviderBase, ForecastProviderError
from .open_meteo import OpenMeteoProvider
from .stormglass import StormglassProvider
from .worldtides import WorldTidesProvider
from .cache import forecast_cache, InMemoryForecastCache, RedisForecastCache

# Registro de proveedores disponibles
_PROVIDERS: dict = {
    "open-meteo": OpenMeteoProvider,
    "stormglass": StormglassProvider,
    "worldtides": WorldTidesProvider,
}


def get_provider(nombre: str) -> ForecastProviderBase:
    """Factory: retorna instancia del proveedor solicitado."""
    cls = _PROVIDERS.get(nombre)
    if cls is None:
        raise ValueError(
            f"Proveedor '{nombre}' no registrado. "
            f"Disponibles: {list(_PROVIDERS.keys())}"
        )
    return cls()


__all__ = [
    "ForecastProviderBase",
    "ForecastProviderError",
    "OpenMeteoProvider",
    "StormglassProvider",
    "WorldTidesProvider",
    "forecast_cache",
    "get_provider",
]
