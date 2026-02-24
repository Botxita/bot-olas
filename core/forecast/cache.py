"""Caché de forecasts con TTL configurable.

Estrategia:
  - In-memory para instancia única (Render free tier / single dyno)
  - Redis-ready: la interface es idéntica; cambiar el backend no requiere
    modificar el código que usa el caché.

TTL por defecto: 30 minutos (1800s). Los pronósticos de Open-Meteo se
actualizan cada ~1 hora, así que 30min es un balance razonable entre
frescura y ahorro de requests.

Para escalar a multi-instancia (Redis):
  1. pip install redis
  2. Cambiar InMemoryForecastCache por RedisForecastCache
  3. Agregar REDIS_URL al .env (Upstash Redis free tier disponible)
"""

import json
import logging
import os
import time
from abc import ABC, abstractmethod
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_TTL = int(os.getenv("FORECAST_CACHE_TTL_SECONDS", "1800"))  # 30 min


class ForecastCacheBase(ABC):
    """Interface para backends de caché."""

    @abstractmethod
    def get(self, key: str) -> Optional[Any]:
        ...

    @abstractmethod
    def set(self, key: str, value: Any, ttl: int = DEFAULT_TTL) -> None:
        ...

    @abstractmethod
    def invalidate(self, key: str) -> None:
        ...

    @abstractmethod
    def clear_all(self) -> None:
        ...


class InMemoryForecastCache(ForecastCacheBase):
    """
    Caché en memoria. Thread-safe para una sola instancia.
    Se pierde al reiniciar el proceso (esperado y aceptable).
    """

    def __init__(self):
        self._store: dict = {}  # key → (value, expiry_timestamp)

    def get(self, key: str) -> Optional[Any]:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expiry = entry
        if time.monotonic() > expiry:
            del self._store[key]
            logger.debug("Cache MISS (expirado): %s", key)
            return None
        logger.debug("Cache HIT: %s", key)
        return value

    def set(self, key: str, value: Any, ttl: int = DEFAULT_TTL) -> None:
        expiry = time.monotonic() + ttl
        self._store[key] = (value, expiry)
        logger.debug("Cache SET: %s (TTL=%ds)", key, ttl)

    def invalidate(self, key: str) -> None:
        self._store.pop(key, None)

    def clear_all(self) -> None:
        self._store.clear()
        logger.info("Cache limpiado completamente.")

    def stats(self) -> dict:
        now = time.monotonic()
        total = len(self._store)
        activos = sum(1 for _, (_, exp) in self._store.items() if exp > now)
        return {"total_entries": total, "active_entries": activos}


class RedisForecastCache(ForecastCacheBase):
    """
    STUB — Backend Redis para multi-instancia.

    Activar con:
      pip install redis
      REDIS_URL=rediss://... en .env (Upstash provee URL de conexión)
    """

    def __init__(self, redis_url: Optional[str] = None):
        self._url = redis_url or os.getenv("REDIS_URL", "")
        self._client = None
        self._prefix = "olas_forecast:"

    def _get_client(self):
        if self._client is None:
            try:
                import redis  # type: ignore
                self._client = redis.from_url(self._url, decode_responses=True)
            except ImportError:
                raise RuntimeError(
                    "redis no está instalado. Ejecutar: pip install redis"
                )
        return self._client

    def get(self, key: str) -> Optional[Any]:
        try:
            raw = self._get_client().get(self._prefix + key)
            if raw is None:
                return None
            return json.loads(raw)
        except Exception as e:
            logger.warning("Redis GET error: %s", e)
            return None

    def set(self, key: str, value: Any, ttl: int = DEFAULT_TTL) -> None:
        try:
            self._get_client().setex(
                self._prefix + key, ttl, json.dumps(value, default=str)
            )
        except Exception as e:
            logger.warning("Redis SET error: %s", e)

    def invalidate(self, key: str) -> None:
        try:
            self._get_client().delete(self._prefix + key)
        except Exception as e:
            logger.warning("Redis DEL error: %s", e)

    def clear_all(self) -> None:
        try:
            keys = self._get_client().keys(self._prefix + "*")
            if keys:
                self._get_client().delete(*keys)
        except Exception as e:
            logger.warning("Redis CLEAR error: %s", e)


# ------------------------------------------------------------------
# Instancia global (cambiar a RedisForecastCache si escalás)
# ------------------------------------------------------------------
_use_redis = bool(os.getenv("REDIS_URL"))

if _use_redis:
    logger.info("Usando RedisForecastCache")
    forecast_cache = RedisForecastCache()
else:
    logger.info("Usando InMemoryForecastCache (single-instance)")
    forecast_cache = InMemoryForecastCache()
