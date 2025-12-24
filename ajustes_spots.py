"""Manejo de ajustes finos por spot/playa.

Los ajustes se guardan en un archivo JSON: ajustes_spots.json

Estructura aproximada:
{
    "miramar/centro": {
        "delta_altura": 0.2,
        "factor_periodo": 1.0
    },
    "miramar/vivero": {
        "delta_altura": -0.1
    }
}
"""

import json
import os
from typing import Dict, Any

RUTA_ARCHIVO = os.path.join(os.path.dirname(__file__), "ajustes_spots.json")


def _clave(spot_key: str, playa_key: str) -> str:
    return f"{spot_key}/{playa_key}"


def _cargar_todo() -> Dict[str, Any]:
    if not os.path.exists(RUTA_ARCHIVO):
        return {}
    try:
        with open(RUTA_ARCHIVO, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _guardar_todo(datos: Dict[str, Any]) -> None:
    with open(RUTA_ARCHIVO, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=2)


def cargar_ajustes_para(spot_key: str, playa_key: str) -> Dict[str, float]:
    """Devuelve los ajustes para un spot/playa, o {} si no hay."""
    datos = _cargar_todo()
    return datos.get(_clave(spot_key, playa_key), {})


def actualizar_ajuste_param(spot_key: str, playa_key: str, param: str, valor: float):
    """Actualiza (o crea) un parámetro de ajuste para un spot/playa."""
    datos = _cargar_todo()
    key = _clave(spot_key, playa_key)
    if key not in datos:
        datos[key] = {}
    datos[key][param] = valor
    _guardar_todo(datos)


def aplicar_ajustes(altura_base: float, periodo_base: float, ajustes: Dict[str, float]):
    """Aplica delta_altura y factor_periodo si existen y devuelve (altura, periodo)."""
    delta_altura = ajustes.get("delta_altura", 0.0)
    factor_periodo = ajustes.get("factor_periodo", 1.0)

    altura = altura_base + delta_altura
    periodo = periodo_base * factor_periodo

    return altura, periodo
