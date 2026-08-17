"""Registro de spots — carga y valida todos los JSONs de config.

Lee todos los archivos en config/spots/*.json y construye
un índice plano de SpotConfig indexado por spot_key.

La carga es lazy (al primer acceso) y se cachea en memoria.
"""

import json
import logging
import os
from typing import Dict, List, Optional, Tuple

from ..scoring.models import SpotConfig, _TZ_FALLBACK_POR_PAIS

logger = logging.getLogger(__name__)

_CONFIG_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "config", "spots")
_registry: Optional[Dict[str, SpotConfig]] = None
_raw_tree: Optional[dict] = None  # Árbol completo para navegación país/región/spot


def _cargar_registry():
    global _registry, _raw_tree
    _registry = {}
    _raw_tree = {}

    config_dir = os.path.abspath(_CONFIG_DIR)
    if not os.path.isdir(config_dir):
        logger.error("Directorio de config no encontrado: %s", config_dir)
        return

    for fname in sorted(os.listdir(config_dir)):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(config_dir, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
            _procesar_pais(data)
        except Exception as e:
            logger.error("Error cargando %s: %s", fname, e)

    logger.info("Registry cargado: %d spots en %d países", len(_registry), len(_raw_tree))


def _procesar_pais(data: dict):
    pais_key = data.get("pais", "XX").lower()
    pais_nombre = data.get("nombre", pais_key)
    regiones = data.get("regiones", {})

    _raw_tree[pais_key] = {
        "nombre": pais_nombre,
        "regiones": {}
    }

    for region_key, region_data in regiones.items():
        region_nombre = region_data.get("nombre", region_key)
        _raw_tree[pais_key]["regiones"][region_key] = {
            "nombre": region_nombre,
            "spots": {}
        }

        for spot_key, spot_data in region_data.get("spots", {}).items():
            try:
                cfg = _build_spot_config(spot_key, spot_data, pais_key, region_key)
                _registry[spot_key] = cfg
                _raw_tree[pais_key]["regiones"][region_key]["spots"][spot_key] = {
                    "nombre": cfg.nombre,
                    "ciudad": cfg.ciudad,
                    "tipo_break": cfg.tipo_break,
                    "notas": cfg.notas,
                }
            except Exception as e:
                logger.warning("Error procesando spot %s: %s", spot_key, e)


def _resolve_tz(spot_data: dict, pais_upper: str) -> str:
    """
    Resuelve la timezone del spot con la siguiente prioridad:
      1. Campo 'tz' en el JSON del spot.
      2. Fallback por código de país en _TZ_FALLBACK_POR_PAIS.
      3. Fallback final: UTC (con warning).
    """
    tz = spot_data.get("tz", "").strip()
    if tz:
        # Validar que la tz es reconocida
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(tz)
            return tz
        except Exception:
            logger.warning("Timezone inválida '%s' en spot, usando fallback por país.", tz)

    fallback = _TZ_FALLBACK_POR_PAIS.get(pais_upper)
    if fallback:
        return fallback

    logger.warning(
        "No se encontró tz para país '%s'. Usando UTC. "
        "Agregar 'tz' al JSON del spot o al diccionario _TZ_FALLBACK_POR_PAIS.",
        pais_upper,
    )
    return "UTC"


def _build_spot_config(key: str, d: dict, pais: str, region: str) -> SpotConfig:
    marea = d.get("marea", {})
    swell = d.get("swell", {})
    viento = d.get("viento", {})
    ajustes = d.get("ajustes_locales", {})
    pais_upper = pais.upper()

    return SpotConfig(
        key=key,
        nombre=d["nombre"],
        ciudad=d.get("ciudad", ""),
        pais=pais_upper,
        region=region,
        lat=d["lat"],
        lon=d["lon"],
        orientacion_costa_deg=d["orientacion_costa_deg"],
        tolerancia_swell_deg=d.get("tolerancia_swell_deg", 45.0),
        tipo_break=d.get("tipo_break", "beach"),
        fondo=d.get("fondo", "arena"),
        marea_min_m=marea.get("rango_optimo_min_m", 0.0),
        marea_max_m=marea.get("rango_optimo_max_m", 2.0),
        marea_tipo_efecto=marea.get("tipo_efecto", "mid_better"),
        swell_altura_min=swell.get("altura_min_m", 0.3),
        swell_altura_max=swell.get("altura_max_m", 4.0),
        swell_periodo_min=swell.get("periodo_min_s", 6.0),
        viento_max_offshore=viento.get("vel_max_offshore_kmh", 40.0),
        viento_max_onshore=viento.get("vel_max_onshore_kmh", 15.0),
        direcciones_ideales=swell.get("direcciones_ideales", []),
        delta_altura=ajustes.get("delta_altura", 0.0),
        delta_marea=ajustes.get("delta_marea", 0.0),
        factor_periodo=ajustes.get("factor_periodo", 1.0),
        fuente_datos=d.get("fuente_datos", "open-meteo"),
        notas=d.get("notas", ""),
        tz=_resolve_tz(d, pais_upper),
    )


def _ensure_loaded():
    if _registry is None:
        _cargar_registry()


# ------------------------------------------------------------------
# API pública
# ------------------------------------------------------------------

def get_spot(key: str) -> SpotConfig:
    _ensure_loaded()
    spot = _registry.get(key)
    if spot is None:
        raise KeyError(f"Spot '{key}' no encontrado en el registry.")
    return spot


def get_all_spots() -> Dict[str, SpotConfig]:
    _ensure_loaded()
    return dict(_registry)


def listar_paises() -> List[Tuple[str, str]]:
    """Retorna lista de (key, nombre) de países disponibles."""
    _ensure_loaded()
    return [(k, v["nombre"]) for k, v in sorted(_raw_tree.items())]


def listar_regiones(pais_key: str) -> List[Tuple[str, str]]:
    """Retorna lista de (key, nombre) de regiones para un país."""
    _ensure_loaded()
    pais = _raw_tree.get(pais_key, {})
    return [
        (k, v["nombre"])
        for k, v in sorted(pais.get("regiones", {}).items())
    ]


def listar_spots_region(pais_key: str, region_key: str) -> List[Tuple[str, dict]]:
    """Retorna lista de (key, info) de spots para una región."""
    _ensure_loaded()
    pais = _raw_tree.get(pais_key, {})
    region = pais.get("regiones", {}).get(region_key, {})
    return [
        (k, v)
        for k, v in sorted(region.get("spots", {}).items())
    ]


def actualizar_ajuste(spot_key: str, param: str, valor: float):
    """Actualiza un ajuste de calibración en memoria (no persiste en JSON)."""
    _ensure_loaded()
    spot = _registry.get(spot_key)
    if spot is None:
        raise KeyError(f"Spot '{spot_key}' no encontrado.")
    if param == "delta_altura":
        spot.delta_altura = valor
    elif param == "delta_marea":
        spot.delta_marea = valor
    elif param == "factor_periodo":
        spot.factor_periodo = valor
    else:
        raise ValueError(f"Parámetro de ajuste desconocido: {param}")


def recargar():
    """Fuerza recarga de todos los JSONs (útil en desarrollo)."""
    global _registry, _raw_tree
    _registry = None
    _raw_tree = None
    _cargar_registry()


def reaplicar_ajustes(ajustes: Dict[str, Dict[str, float]]) -> None:
    """
    Reaplica sobre el registry ya cargado ajustes de /ajuste persistidos en
    una fuente externa (SQLite, ver
    persistence/session_store.py::get_all_spot_adjustments()). Pensado para
    llamarse una vez al arranque del proceso (ver main.py), antes de
    empezar a servir tráfico.

    Sin esto, un ajuste aplicado con /ajuste persistía en la tabla
    spot_adjustments (session_store.set_spot_adjustment() sí se llama
    desde el handler) pero nunca se releía al reiniciar el proceso — el
    ajuste funcionaba mientras el proceso seguía vivo, pero se perdía en
    cada restart (Render free tier reinicia con frecuencia: cold starts,
    deploys, sleep del tier gratuito) sin que el mensaje de confirmación
    de /ajuste dejara ver que la persistencia era parcial (#31).

    core/ no importa persistence/ directamente (capas externas no deben
    ser una dependencia de core/) — el caller (main.py) es responsable de
    leer los ajustes desde SQLite y pasarlos ya armados acá.

    Un spot_key o param inválido en los datos persistidos (ej. un spot
    renombrado/eliminado del JSON desde que se guardó el ajuste) se
    registra como warning y se saltea, no interrumpe el resto.

    Nota: esto arregla que el proceso vuelva a LEER la tabla al arrancar
    — no garantiza que el archivo SQLite en sí sobreviva el restart. Eso
    depende de que SESSION_DB_PATH apunte a disco realmente persistente
    en el entorno de deploy (no verificado acá contra la config real de
    Render); si el filesystem es efímero, la tabla puede estar vacía en
    cada arranque y no hay nada que reaplicar.
    """
    _ensure_loaded()
    for spot_key, params in ajustes.items():
        for param, valor in params.items():
            try:
                actualizar_ajuste(spot_key, param, valor)
            except (KeyError, ValueError) as e:
                logger.warning(
                    "No se pudo reaplicar ajuste persistido %s.%s=%s: %s",
                    spot_key, param, valor, e,
                )
