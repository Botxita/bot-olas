"""Motor de scoring por capas.

Arquitectura:
  1. Energía real del swell (H² × T)
  2. Score de período (groundswell vs windchop)
  3. Score de dirección swell–costa (ángulo relativo)
  4. Score de viento (offshore/onshore/cross + intensidad)
  5. Score de marea (rango óptimo por spot)
  → Score total ponderado por tipo de break

Completamente desacoplado de Telegram. Testeable con fixtures JSON.
"""

import json
import math
import os
from typing import Dict

from .models import (
    ForecastHour,
    ScoreBreakdown,
    SpotConfig,
    SwellData,
    WindData,
    TideData,
)

# Ruta config de pesos
_WEIGHTS_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "config", "scoring_weights.json"
)
_weights_cache: Dict = {}


def _get_weights() -> Dict:
    global _weights_cache
    if not _weights_cache:
        with open(_WEIGHTS_PATH, "r", encoding="utf-8") as f:
            _weights_cache = json.load(f)
    return _weights_cache


# ---------------------------------------------------------------------------
# Capa 1 — Energía del swell
# ---------------------------------------------------------------------------

def _energia_proxy(swell: SwellData) -> float:
    """Proxy de energía: H² × T. No es energía física exacta, es índice surfer."""
    return (swell.altura_m ** 2) * swell.periodo_s


def _score_energia(energia: float, escala: float = 50.0) -> float:
    """Normaliza la energía al rango [0, 1] con una curva suave (sigmoid-like)."""
    # Usamos tanh para comprimir valores extremos sin recortarlos abruptamente
    ratio = energia / escala
    return float(math.tanh(ratio * 1.2))


# ---------------------------------------------------------------------------
# Capa 2 — Período
# ---------------------------------------------------------------------------

def _score_periodo(T: float) -> float:
    """
    Curve de calidad basada en período.
    < 7s  → windchop desordenado
    7–10s → corto, usable
    10–14s → bueno
    > 14s → groundswell puro
    """
    if T < 5:   return 0.10
    if T < 7:   return 0.30
    if T < 9:   return 0.55
    if T < 11:  return 0.72
    if T < 14:  return 0.88
    if T < 17:  return 0.97
    return 1.0


# ---------------------------------------------------------------------------
# Capa 3 — Dirección relativa swell–costa
# ---------------------------------------------------------------------------

def _angulo_relativo(dir_swell: float, orientacion_costa: float) -> float:
    """
    Calcula cuánto se desvía el swell del ángulo ideal (perpendicular a la costa).

    dir_swell: de dónde viene el swell (0=N, 90=E, 180=S, 270=W)
    orientacion_costa: hacia dónde "mira" la playa (la cara de la ola)

    Ejemplo: costa orientada al E (90°) → swell ideal viene del E (90°)
    """
    angulo_ideal = orientacion_costa  # swell perpendicular = viene directo a la cara
    diff = abs(dir_swell - angulo_ideal) % 360
    if diff > 180:
        diff = 360 - diff
    return diff  # 0 = perfecto, 90 = paralelo, 180 = de espaldas


def _mejor_diff_direccion(dir_swell: float, spot: SpotConfig) -> float:
    """
    Diferencia angular entre el swell y el ideal más cercano del spot.

    Si el spot tiene `direcciones_ideales` configuradas (config/spots/*.json,
    swell.direcciones_ideales), se usa la mínima diferencia contra cualquiera
    de ellas — un point/reef break puede funcionar bien con más de un swell.
    Si la lista está vacía, se usa orientacion_costa_deg como único ideal
    (comportamiento previo, perpendicular a la costa).
    """
    ideales = spot.direcciones_ideales or [spot.orientacion_costa_deg]
    return min(_angulo_relativo(dir_swell, ideal) for ideal in ideales)


def _score_dir_swell(diff: float, tolerancia: float) -> float:
    """
    Score de dirección swell con tolerancia configurable por spot.
    Los beach breaks toleran más variación que los reef y point breaks.

    `diff` es la diferencia angular ya calculada contra el ideal más cercano
    (ver `_mejor_diff_direccion`).
    """
    if diff <= tolerancia:
        # Dentro de la ventana óptima: bonus por ser más centrado
        return 1.0 - 0.15 * (diff / tolerancia)

    elif diff <= tolerancia * 2.5:
        # Zona de transición: decae linealmente
        exceso = diff - tolerancia
        rango = tolerancia * 1.5
        return 0.85 - 0.65 * (exceso / rango)

    else:
        # Swell muy oblicuo o de espaldas
        return max(0.05, 0.20 - 0.003 * diff)


# ---------------------------------------------------------------------------
# Capa 4 — Viento
# ---------------------------------------------------------------------------

def _tipo_viento(dir_viento: float, orientacion_costa: float) -> str:
    """
    Clasifica el viento respecto a la costa.
    offshore: viento viene desde tierra hacia el mar (pula la ola)
    onshore:  viento viene del mar hacia tierra (destruye la ola)
    cross:    paralelo a la costa
    """
    # El viento "viene desde" dir_viento.
    # Offshore significa que sopla hacia el mar = viene desde tierra.
    # La tierra está "detrás" de la orientación de la costa.
    dir_tierra = (orientacion_costa + 180) % 360
    diff = abs(dir_viento - dir_tierra) % 360
    if diff > 180:
        diff = 360 - diff

    if diff < 60:
        return "offshore"
    elif diff < 120:
        return "cross"
    else:
        return "onshore"


def _score_viento(wind: WindData, orientacion_costa: float) -> float:
    tipo = _tipo_viento(wind.direccion_deg, orientacion_costa)
    vel = wind.velocidad_kmh

    if vel < 5:
        return 1.0   # Calmo: siempre perfecto

    if tipo == "offshore":
        if vel < 15: return 0.98
        if vel < 25: return 0.90
        if vel < 35: return 0.78
        if vel < 50: return 0.60
        return 0.40  # Offshore muy fuerte: dificultad para remar

    if tipo == "onshore":
        if vel < 10: return 0.55
        if vel < 20: return 0.30
        if vel < 30: return 0.15
        return 0.05  # Onshore fuerte: olas destruidas

    # cross
    if vel < 10: return 0.80
    if vel < 20: return 0.65
    if vel < 30: return 0.48
    return 0.30


# ---------------------------------------------------------------------------
# Capa 5 — Marea
# ---------------------------------------------------------------------------

def _score_marea(tide: TideData, spot: SpotConfig) -> float:
    """
    Penalización gradual cuando la marea se aleja del rango óptimo del spot.

    Dentro del rango [marea_min_m, marea_max_m], la forma de la curva depende
    de spot.marea_tipo_efecto:
      - "mid_better" (default): mejor en el centro del rango, decae hacia
        ambos bordes por igual.
      - "low_better": mejor en marea_min_m, decae monótonamente hacia
        marea_max_m (spots que solo funcionan con marea baja).
      - "high_better": mejor en marea_max_m, decae monótonamente hacia
        marea_min_m.
    Fuera del rango, la desviación en la dirección PREFERIDA por tipo_efecto
    (más baja para low_better, más alta para high_better) no se penaliza —
    consistente con _generar_flags(), que ya marca esos casos como flag
    positivo ("Marea baja/alta, bueno aquí") en vez de neutro. La desviación
    en la dirección opuesta usa la misma penalización por distancia absoluta
    de siempre (la escala de esa penalización es el hallazgo #7, no este fix).

    Nota: sea_level_height_msl es proxy, no datum náutico.
    La calibración por spot (delta_altura en ajustes) permite ajustar empíricamente.
    """
    nivel = tide.nivel_m
    mn = spot.marea_min_m
    mx = spot.marea_max_m
    centro = (mn + mx) / 2
    amplitud = (mx - mn) / 2

    if amplitud == 0:
        return 0.7  # Configuración incompleta, score neutro

    if mn <= nivel <= mx:
        tipo = spot.marea_tipo_efecto
        if tipo == "low_better":
            # Máximo en marea_min_m, decae linealmente hacia marea_max_m.
            progreso = (nivel - mn) / (mx - mn)
            return 1.0 - 0.20 * progreso
        elif tipo == "high_better":
            # Máximo en marea_max_m, decae linealmente hacia marea_min_m.
            progreso = (mx - nivel) / (mx - mn)
            return 1.0 - 0.20 * progreso
        else:
            # mid_better: bonus por estar cerca del centro
            distancia_centro = abs(nivel - centro)
            return 1.0 - 0.20 * (distancia_centro / amplitud)
    else:
        # Fuera del rango: penalización gradual, salvo que la desviación
        # vaya en la dirección que tipo_efecto ya marca como buena.
        tipo = spot.marea_tipo_efecto
        if nivel < mn:
            if tipo == "low_better":
                return 1.0  # Sigue siendo "bueno aquí" (ver _generar_flags)
            desvio = mn - nivel
        else:
            if tipo == "high_better":
                return 1.0  # Sigue siendo "bueno aquí" (ver _generar_flags)
            desvio = nivel - mx
        # Penalización capped: no llega a 0 (la marea sigue siendo surfeeable)
        return max(0.10, 1.0 - desvio * 0.50)


# ---------------------------------------------------------------------------
# Generación de flags explicativos
# ---------------------------------------------------------------------------

def _generar_flags(
    swell: SwellData,
    wind: WindData,
    tide: TideData,
    spot: SpotConfig,
    energia: float,
) -> tuple:
    positivos = []
    negativos = []
    neutros = []

    # Período
    if swell.periodo_s >= 14:
        positivos.append(f"Groundswell largo ({swell.periodo_s:.0f}s)")
    elif swell.periodo_s >= 10:
        positivos.append(f"Período decente ({swell.periodo_s:.0f}s)")
    elif swell.periodo_s < 7:
        negativos.append(f"Período muy corto ({swell.periodo_s:.0f}s) — windchop")

    # Viento
    tipo_v = _tipo_viento(wind.direccion_deg, spot.orientacion_costa_deg)
    if tipo_v == "offshore":
        if wind.velocidad_kmh < 20:
            positivos.append(f"Offshore limpio ({wind.velocidad_kmh:.0f} km/h)")
        else:
            positivos.append(f"Offshore (fuerte, {wind.velocidad_kmh:.0f} km/h)")
    elif tipo_v == "onshore":
        if wind.velocidad_kmh > 20:
            negativos.append(f"Onshore fuerte ({wind.velocidad_kmh:.0f} km/h)")
        else:
            negativos.append(f"Onshore ({wind.velocidad_kmh:.0f} km/h)")
    else:
        if wind.velocidad_kmh < 15:
            neutros.append(f"Viento lateral suave ({wind.velocidad_kmh:.0f} km/h)")
        else:
            negativos.append(f"Viento lateral ({wind.velocidad_kmh:.0f} km/h)")

    # Altura
    if swell.altura_m < spot.swell_altura_min:
        negativos.append(f"Ola pequeña ({swell.altura_m:.1f}m)")
    elif swell.altura_m > spot.swell_altura_max:
        negativos.append(f"Ola grande para el spot ({swell.altura_m:.1f}m)")
    else:
        if swell.altura_m >= 1.5:
            positivos.append(f"Tamaño sólido ({swell.altura_m:.1f}m)")
        else:
            neutros.append(f"Tamaño manejable ({swell.altura_m:.1f}m)")

    # Dirección swell
    diff = _mejor_diff_direccion(swell.direccion_deg, spot)
    if diff <= spot.tolerancia_swell_deg:
        positivos.append("Dirección swell ideal")
    elif diff <= spot.tolerancia_swell_deg * 2:
        neutros.append("Dirección swell aceptable")
    else:
        negativos.append("Swell oblicuo al spot")

    # Marea
    if spot.marea_min_m <= tide.nivel_m <= spot.marea_max_m:
        positivos.append("Marea en rango óptimo")
    elif tide.nivel_m < spot.marea_min_m:
        if spot.marea_tipo_efecto == "low_better":
            positivos.append("Marea baja (bueno aquí)")
        else:
            neutros.append(f"Marea baja ({tide.nivel_m:.1f}m)")
    else:
        if spot.marea_tipo_efecto == "high_better":
            positivos.append("Marea alta (bueno aquí)")
        else:
            neutros.append(f"Marea alta ({tide.nivel_m:.1f}m)")

    # Nota proxy marea
    neutros.append("⚠️ Marea: proxy MSL (orientativo)")

    return positivos, negativos, neutros


# ---------------------------------------------------------------------------
# Función principal
# ---------------------------------------------------------------------------

def calcular_score(hour: ForecastHour, spot: SpotConfig) -> ScoreBreakdown:
    """
    Calcula el score completo para una hora de pronóstico en un spot dado.

    Args:
        hour: datos de pronóstico (swell + viento + marea)
        spot: configuración del spot

    Returns:
        ScoreBreakdown con score total, sub-scores, y flags explicativos
    """
    weights = _get_weights()
    pesos = weights["pesos_por_break"].get(spot.tipo_break, weights["pesos_por_break"]["beach"])
    escala_energia = weights["energia"]["escala_normalizacion"]

    # Aplicar ajustes locales del spot
    swell_ajustado = SwellData(
        altura_m=hour.swell.altura_m + spot.delta_altura,
        periodo_s=hour.swell.periodo_s * spot.factor_periodo,
        direccion_deg=hour.swell.direccion_deg,
        altura_viento_m=hour.swell.altura_viento_m,
    )

    # Calcular sub-scores
    energia = _energia_proxy(swell_ajustado)
    s_energia = _score_energia(energia, escala_energia)
    s_periodo = _score_periodo(swell_ajustado.periodo_s)
    diff_dir = _mejor_diff_direccion(swell_ajustado.direccion_deg, spot)
    s_dir = _score_dir_swell(diff_dir, spot.tolerancia_swell_deg)
    s_viento = _score_viento(hour.wind, spot.orientacion_costa_deg)
    s_marea = _score_marea(hour.tide, spot)

    # Score total ponderado
    score_total = (
        pesos["energia"]   * s_energia +
        pesos["periodo"]   * s_periodo +
        pesos["dir_swell"] * s_dir +
        pesos["viento"]    * s_viento +
        pesos["marea"]     * s_marea
    )
    score_total = min(1.0, max(0.0, score_total))

    # Flags
    pos, neg, neu = _generar_flags(swell_ajustado, hour.wind, hour.tide, spot, energia)

    return ScoreBreakdown(
        score_energia=round(s_energia, 3),
        score_periodo=round(s_periodo, 3),
        score_dir_swell=round(s_dir, 3),
        score_viento=round(s_viento, 3),
        score_marea=round(s_marea, 3),
        score_total=round(score_total, 3),
        energia_proxy=round(energia, 2),
        flags_positivos=pos,
        flags_negativos=neg,
        flags_neutros=neu,
    )
