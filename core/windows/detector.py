"""Detector de ventanas óptimas de surf en las próximas 48 horas.

Algoritmo:
  1. Filtra horas nocturnas (fuera del rango amanecer–atardecer del spot)
  2. Calcula score hora por hora usando el motor de scoring
  3. Identifica horas consecutivas que superan el umbral
  4. Agrupa en "ventanas" (intervalos contiguos)
  5. Para cada ventana: score promedio, score máximo, hora pico
  6. Genera descripción legible en español
  7. Retorna Top N ventanas ordenadas por score promedio
"""

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import List

from ..scoring.engine import calcular_score
from ..scoring.models import ForecastHour, ScoreBreakdown, SpotConfig, VentanaOptima
from ..analysis.daylight import get_daylight_for_forecast_hour, is_daylight

logger = logging.getLogger(__name__)

_WEIGHTS_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "config", "scoring_weights.json"
)


def _get_config() -> dict:
    with open(_WEIGHTS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def detectar_ventanas(
    forecast: List[ForecastHour],
    spot: SpotConfig,
    umbral: float = None,
    top_n: int = None,
    horizonte_horas: float = 48,
) -> List[VentanaOptima]:
    """
    Detecta ventanas óptimas de surf en un forecast de 48h.

    Args:
        forecast: lista de ForecastHour. El proveedor real (Open-Meteo) puede
                  devolver hasta 7 días — esta función recorta al horizonte
                  declarado antes de procesar, no asume que el caller ya lo hizo.
        spot: configuración del spot
        umbral: score mínimo (0-1) para considerar una hora como buena.
                None = leer de scoring_weights.json
        top_n: máximo de ventanas a retornar.
                None = leer de scoring_weights.json
        horizonte_horas: cuántas horas hacia adelante considerar desde ahora.
                Default 48, acorde al contrato de "Próximas olas — 48h".

    Returns:
        Lista de VentanaOptima ordenada por score_promedio descendente.
    """
    config = _get_config()
    umbral = umbral if umbral is not None else config.get("umbral_ventana_optima", 0.60)
    top_n = top_n if top_n is not None else config.get("top_ventanas", 3)

    if not forecast:
        return []

    # Recortar al horizonte declarado (48h por defecto). No filtramos el piso
    # (horas ya pasadas) acá: una ventana en curso puede haber empezado antes
    # de "ahora" y necesita esas horas para calcularse correctamente.
    # Cada ForecastHour representa un bloque de 1h (ver _construir_ventana:
    # fin = última hora + 1h) — exigimos que el FIN del bloque quede dentro
    # del horizonte, no solo su inicio, para que ninguna ventana resultante
    # pueda terminar después del límite declarado.
    limite = datetime.now(timezone.utc) + timedelta(hours=horizonte_horas)
    forecast = [h for h in forecast if h.timestamp + timedelta(hours=1) <= limite]
    if not forecast:
        return []

    # Filtrar horas nocturnas: solo evaluar horas dentro del horario diurno
    forecast_diurno = []
    for hour in forecast:
        try:
            daylight = get_daylight_for_forecast_hour(spot, hour.timestamp)
            if is_daylight(hour.timestamp, daylight):
                forecast_diurno.append(hour)
        except Exception as e:
            logger.warning("Error calculando luz solar para %s: %s", hour.timestamp, e)
            # En caso de error, incluir la hora (fail-safe)
            forecast_diurno.append(hour)

    if not forecast_diurno:
        return []

    # Calcular score para cada hora diurna
    scored: List[tuple] = []  # (ForecastHour, ScoreBreakdown)
    for hour in forecast_diurno:
        try:
            breakdown = calcular_score(hour, spot)
            scored.append((hour, breakdown))
        except Exception as e:
            logger.warning("Error calculando score para %s: %s", hour.timestamp, e)
            continue

    # Agrupar en ventanas contiguas por encima del umbral.
    # Corte adicional cuando cambia el día local (evita ventanas que cruzan medianoche).
    ventanas_raw: List[List[tuple]] = []
    current_window: List[tuple] = []
    tz = spot.get_zoneinfo()

    for hour, bd in scored:
        if bd.score_total >= umbral:
            # Cortar si el día local cambió respecto a la hora anterior en la ventana
            if current_window:
                ultimo_ts = current_window[-1][0].timestamp
                if hour.timestamp.astimezone(tz).date() != ultimo_ts.astimezone(tz).date():
                    ventanas_raw.append(current_window)
                    current_window = []
            current_window.append((hour, bd))
        else:
            if current_window:
                ventanas_raw.append(current_window)
                current_window = []

    if current_window:
        ventanas_raw.append(current_window)

    # Construir VentanaOptima para cada grupo
    ventanas = []
    for group in ventanas_raw:
        ventana = _construir_ventana(group, spot)
        if ventana:
            ventanas.append(ventana)

    # Filtrar ventanas que ya terminaron
    now = datetime.now(timezone.utc)
    ventanas = [v for v in ventanas if v.fin > now]

    # Ordenar por score promedio descendente y retornar top N
    ventanas.sort(key=lambda v: v.score_promedio, reverse=True)
    return ventanas[:top_n]


def _construir_ventana(
    group: List[tuple], spot: SpotConfig
) -> VentanaOptima:
    """Construye un VentanaOptima a partir de un grupo de horas contiguas."""
    if not group:
        return None

    horas = [h for h, _ in group]
    scores = [bd.score_total for _, bd in group]

    inicio = horas[0].timestamp
    fin = horas[-1].timestamp + timedelta(hours=1)  # fin = inicio de la última hora + 1h
    score_prom = sum(scores) / len(scores)
    score_max = max(scores)

    # Hora pico = hora con mayor score
    idx_pico = scores.index(score_max)
    hora_pico = horas[idx_pico].timestamp

    # Descripción legible
    desc = _generar_descripcion(horas, group, inicio, fin, hora_pico, score_prom, spot)

    return VentanaOptima(
        inicio=inicio,
        fin=fin,
        score_promedio=round(score_prom, 3),
        score_max=round(score_max, 3),
        hora_pico=hora_pico,
        descripcion=desc,
        horas_count=len(group),
    )


def _generar_descripcion(
    horas: List[ForecastHour],
    group: List[tuple],
    inicio: datetime,
    fin: datetime,
    hora_pico: datetime,
    score_prom: float,
    spot: SpotConfig,
) -> str:
    """Genera descripción en lenguaje surfer usando la timezone local del spot."""
    from zoneinfo import ZoneInfo
    tz = spot.get_zoneinfo()

    inicio_local = inicio.astimezone(tz)
    fin_local = fin.astimezone(tz)
    now_local = datetime.now(tz)

    # Día relativo en timezone local
    delta_dias = (inicio_local.date() - now_local.date()).days
    if delta_dias == 0:
        dia = "Hoy"
    elif delta_dias == 1:
        dia = "Mañana"
    else:
        dias_es = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
        dia = dias_es[inicio_local.weekday()]

    hora_inicio_str = inicio_local.strftime("%H:%M")
    hora_fin_str = fin_local.strftime("%H:%M")

    # Características predominantes de la ventana (hora pico)
    pico_hour = next((h for h in horas if h.timestamp == hora_pico), horas[0])
    _, pico_bd = next(
        ((h, bd) for h, bd in group if h.timestamp == hora_pico), group[0]
    )

    # Highlights
    highlights = []

    # Viento
    if pico_bd.score_viento >= 0.85:
        highlights.append("offshore")
    elif pico_bd.score_viento >= 0.65:
        highlights.append("viento ok")
    else:
        highlights.append("viento regular")

    # Período
    T = pico_hour.swell.periodo_s
    if T >= 14:
        highlights.append(f"groundswell {T:.0f}s")
    elif T >= 10:
        highlights.append(f"{T:.0f}s")

    # Altura
    H = pico_hour.swell.altura_m
    highlights.append(f"{H:.1f}m")

    # Marea (si es relevante)
    if pico_bd.score_marea >= 0.85:
        highlights.append("marea ideal")
    elif pico_bd.score_marea < 0.4:
        highlights.append("marea no ideal")

    highlights_str = " · ".join(highlights)
    return highlights_str


def calcular_score_actual(
    forecast: List[ForecastHour], spot: SpotConfig
) -> tuple:
    """
    Retorna (ForecastHour, ScoreBreakdown) para el momento más cercano a ahora.
    Útil para el comando "condiciones actuales".
    """
    if not forecast:
        return None, None

    now = datetime.now(timezone.utc)
    closest = min(forecast, key=lambda h: abs((h.timestamp - now).total_seconds()))
    breakdown = calcular_score(closest, spot)
    return closest, breakdown
