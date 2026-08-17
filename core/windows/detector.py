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
import math
import os
from datetime import datetime, timezone, timedelta
from typing import List

from ..scoring.engine import calcular_score, _tipo_viento, ajustar_swell
from ..scoring.models import ForecastHour, ScoreBreakdown, SpotConfig, VentanaOptima
from ..analysis.daylight import get_daylight_for_forecast_hour, is_daylight, PolarDaylightError

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

    # Validar rangos. umbral/top_n vienen de scoring_weights.json o de un
    # caller — un valor fuera de rango no es un error de datos del
    # forecast, es config imposible de interpretar de forma útil. Igual
    # que el fix #23: fallar temprano y visible en vez de degradar en
    # silencio (clampear "arreglaría" el síntoma pero produciría otro
    # comportamiento silencioso — ej. top_n=0.9 truncado a 0 se vería
    # idéntico a "no hay ventanas buenas"). Los handlers de
    # bot/handlers/main.py ya envuelven detectar_ventanas() en un
    # try/except genérico que muestra un mensaje de error legible.
    if isinstance(umbral, bool) or not isinstance(umbral, (int, float)) \
            or not math.isfinite(umbral) or not (0.0 <= umbral <= 1.0):
        raise ValueError(f"umbral debe ser un número finito entre 0 y 1, recibido: {umbral!r}")
    if isinstance(top_n, bool) or not isinstance(top_n, int) or top_n < 0:
        raise ValueError(f"top_n debe ser un entero >= 0, recibido: {top_n!r}")

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

    # Filtrar horas nocturnas: solo evaluar horas dentro del horario diurno.
    # Solo PolarDaylightError (el caso documentado, un tipo específico —
    # no ValueError genérico) activa el fail-safe de incluir la hora igual.
    # Antes, except Exception amplio atrapaba (y escondía, fail-open)
    # cualquier otro error no relacionado (ej. un bug futuro en
    # daylight.py, o un SpotConfig con lat/lon corruptos) tratándolo
    # silenciosamente como si fuera el caso polar. Angostar a `ValueError`
    # a secas no alcanza — cualquier otro ValueError no documentado
    # (ej. de is_daylight() con un dt naive, o un bug futuro) seguiría
    # cayendo en el fail-safe; PolarDaylightError es una subclase
    # específica de ValueError solo para el fenómeno polar (#29).
    forecast_diurno = []
    for hour in forecast:
        try:
            daylight = get_daylight_for_forecast_hour(spot, hour.timestamp)
            if is_daylight(hour.timestamp, daylight):
                forecast_diurno.append(hour)
        except PolarDaylightError as e:
            logger.warning("Error calculando luz solar para %s: %s", hour.timestamp, e)
            # En caso de error, incluir la hora (fail-safe)
            forecast_diurno.append(hour)

    if not forecast_diurno:
        return []

    # Calcular score para cada hora diurna
    scored: List[tuple] = []  # (ForecastHour, ScoreBreakdown)
    errores_score = 0
    for hour in forecast_diurno:
        try:
            breakdown = calcular_score(hour, spot)
            scored.append((hour, breakdown))
        except Exception as e:
            logger.warning("Error calculando score para %s: %s", hour.timestamp, e)
            errores_score += 1
            continue

    # Si TODAS las horas fallaron al calcular score (ej. config de spot
    # inválida — tolerancia_swell_deg=0, marea_max_m <= marea_min_m, etc.),
    # devolver [] es indistinguible de "no hay condiciones buenas" para el
    # caller. Se propaga como excepción: los handlers de bot/handlers/main.py
    # ya envuelven detectar_ventanas() en un try/except genérico que muestra
    # un mensaje de error legible (formato_no_disponible) en vez de la
    # sección de ventanas vacía — no hace falta tocar el handler.
    # Fallos aislados de horas puntuales (no todas) se siguen ignorando
    # en silencio, como antes: son datos parciales, no un error sistémico.
    if forecast_diurno and errores_score == len(forecast_diurno):
        raise RuntimeError(
            f"No se pudo calcular el score en ninguna de las {errores_score} horas "
            f"evaluadas para el spot '{spot.key}' — posible config de spot inválida."
        )

    # Agrupar en ventanas contiguas por encima del umbral.
    # Corte adicional cuando cambia el día local (evita ventanas que cruzan medianoche).
    ventanas_raw: List[List[tuple]] = []
    current_window: List[tuple] = []
    tz = spot.get_zoneinfo()

    for hour, bd in scored:
        if bd.score_total >= umbral:
            # Cortar si la hora no es exactamente contigua a la anterior de
            # la ventana (hueco real de datos — hora faltante del proveedor,
            # u hora descartada por #23 — entre dos horas buenas del mismo
            # día, antes las fusionaba en una sola ventana continua, #25) O
            # si cambió el día local (regla de producto ya existente, evita
            # ventanas que cruzan medianoche aunque sean horariamente
            # contiguas — ej. 23:00→00:00). Son dos reglas distintas, no
            # una reemplaza a la otra: dos horas de días distintos SIEMPRE
            # cortan, aunque estén separadas por exactamente 1h.
            if current_window:
                ultimo_ts = current_window[-1][0].timestamp
                hay_hueco = hour.timestamp - ultimo_ts != timedelta(hours=1)
                cambia_dia = hour.timestamp.astimezone(tz).date() != ultimo_ts.astimezone(tz).date()
                if hay_hueco or cambia_dia:
                    ventanas_raw.append(current_window)
                    current_window = []
            current_window.append((hour, bd))
        else:
            if current_window:
                ventanas_raw.append(current_window)
                current_window = []

    if current_window:
        ventanas_raw.append(current_window)

    # Construir VentanaOptima para cada grupo, recortando primero las horas
    # cuyo bloque ya terminó. Una ventana "en curso" (empezó antes de ahora,
    # termina después) debe reflejar solo la porción vigente — antes, una
    # ventana 10:00-14:00 consultada a las 12:30 seguía mostrando
    # score_promedio/score_max/hora_pico calculados con las horas 10:00 y
    # 11:00 ya pasadas, como si todavía formaran parte de la recomendación.
    # Recortar el grupo ANTES de construir la ventana (en vez de filtrar
    # después por v.fin > ahora) resuelve ambos problemas con un solo
    # cambio: una ventana totalmente pasada queda con group_vigente vacío
    # y se descarta (mismo efecto que el filtro viejo), y una ventana
    # parcialmente pasada recalcula todos sus campos solo con las horas
    # que quedan.
    ahora = datetime.now(timezone.utc)
    ventanas = []
    for group in ventanas_raw:
        group_vigente = [
            (h, bd) for h, bd in group
            if h.timestamp + timedelta(hours=1) > ahora
        ]
        if not group_vigente:
            continue
        # Contrato documentado en bot/handlers/main.py ("ventana más
        # cercana, mínimo 2h"): una ventana de una sola hora no es una
        # recomendación útil — antes, un grupo de tamaño 1 (ej. una única
        # hora sobre el umbral entre dos que no lo superan, o una ventana
        # en curso recortada por #24 hasta quedar con 1h vigente) se
        # construía igual y se mostraba como ventana válida (#26).
        if len(group_vigente) < 2:
            continue
        ventana = _construir_ventana(group_vigente, spot)
        if ventana:
            ventanas.append(ventana)

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

    # Viento — score_viento >= 0.85 no implica dirección offshore: el
    # engine da score=1.0 a cualquier viento < 5 km/h sin importar su
    # dirección (ver _score_viento, engine.py). Antes, esa condición sola
    # bastaba para etiquetar la ventana como "offshore" — una afirmación de
    # dirección meteorológica falsa para un viento simplemente calmo (#27).
    tipo_viento = _tipo_viento(pico_hour.wind.direccion_deg, spot.orientacion_costa_deg)
    if pico_bd.score_viento >= 0.85:
        if tipo_viento == "offshore":
            highlights.append("offshore")
        else:
            highlights.append("viento calmo")
    elif pico_bd.score_viento >= 0.65:
        highlights.append("viento ok")
    else:
        highlights.append("viento regular")

    # Período y altura — usa los valores AJUSTADOS por delta_altura/
    # factor_periodo del spot, los mismos que calcular_score() usó para
    # puntuar (ver ajustar_swell en engine.py). Antes se leían los crudos
    # de ForecastHour: un período crudo de 13s con factor_periodo=1.1 se
    # mostraba como "13s" aunque el score usó 14.3s y calificó como
    # groundswell — la descripción contradecía el score (#28).
    swell_ajustado = ajustar_swell(pico_hour.swell, spot)
    T = swell_ajustado.periodo_s
    if T >= 14:
        highlights.append(f"groundswell {T:.0f}s")
    elif T >= 10:
        highlights.append(f"{T:.0f}s")

    # Altura
    H = swell_ajustado.altura_m
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
