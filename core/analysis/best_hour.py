"""Cálculo de la mejor hora del día para surfear.

Filtra horas por luz solar y retorna la de mayor score,
junto con su posición relativa en el ranking del día.

Completamente independiente de Telegram. Pure functions.
Depende de: core.scoring (modelos + motor), core.analysis.daylight.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import List, Optional, Tuple

from core.scoring.models import ForecastHour, ScoreBreakdown, SpotConfig
from core.analysis.daylight import DaylightInfo, get_daylight, is_daylight


@dataclass
class RankedHour:
    """Una hora del día con su score y posición en el ranking."""
    hour: ForecastHour
    breakdown: ScoreBreakdown
    rank: int          # 1 = mejor del día
    es_dia: bool       # True si hay luz solar


@dataclass
class BestHourResult:
    """Resultado de calcular_mejor_hora."""
    hour: ForecastHour
    breakdown: ScoreBreakdown
    score_100: int
    rank_en_dia: int              # siempre 1 (es la mejor)
    horas_evaluadas: int          # cuántas horas con luz se analizaron
    daylight: DaylightInfo        # info solar del día
    todas_las_horas: List[RankedHour]   # ranking completo del día (con luz)


def calcular_mejor_hora(
    forecast: List[ForecastHour],
    spot: SpotConfig,
    fecha: date,
    score_fn=None,
) -> Optional[BestHourResult]:
    """
    Encuentra la mejor hora del día 'fecha' para surfear en el spot.

    Filtra:
      - Solo horas que correspondan a 'fecha' en la tz del spot.
      - Solo horas dentro del período de luz solar.

    Args:
        forecast: Lista completa de ForecastHour (UTC-aware).
        spot: SpotConfig con lat, lon, tz.
        fecha: Fecha local del spot (date).
        score_fn: Función de scoring. Si es None, usa el motor por defecto.
                  Firma esperada: (hour: ForecastHour, spot: SpotConfig) -> ScoreBreakdown

    Returns:
        BestHourResult con la mejor hora, o None si no hay datos.
    """
    score_fn = score_fn or _default_score_fn()
    if score_fn is None:
        return None

    tz = spot.get_zoneinfo()

    # Calcular luz solar del día
    try:
        daylight = get_daylight(spot, fecha)
    except ValueError:
        # Fenómeno polar extremo — no debería ocurrir para LATAM
        return None

    # Filtrar horas del día con luz solar
    horas_del_dia = [
        h for h in forecast
        if h.timestamp.astimezone(tz).date() == fecha
        and is_daylight(h.timestamp, daylight)
    ]

    if not horas_del_dia:
        return None

    # Calcular score para cada hora
    scored: List[Tuple[ForecastHour, ScoreBreakdown]] = []
    for h in horas_del_dia:
        try:
            bd = score_fn(h, spot)
            scored.append((h, bd))
        except Exception:
            continue

    if not scored:
        return None

    # Ordenar por score descendente
    scored.sort(key=lambda x: x[1].score_total, reverse=True)

    # Construir ranking completo
    ranked = [
        RankedHour(hour=h, breakdown=bd, rank=i + 1, es_dia=True)
        for i, (h, bd) in enumerate(scored)
    ]

    best_hour, best_breakdown = scored[0]

    return BestHourResult(
        hour=best_hour,
        breakdown=best_breakdown,
        score_100=best_breakdown.score_100,
        rank_en_dia=1,
        horas_evaluadas=len(scored),
        daylight=daylight,
        todas_las_horas=ranked,
    )


def calcular_ranking_dia(
    forecast: List[ForecastHour],
    spot: SpotConfig,
    fecha: date,
    score_fn=None,
    incluir_noche: bool = False,
) -> List[RankedHour]:
    """
    Ranking completo de horas de un día, con flag es_dia en cada una.
    Útil para vista hora a hora.

    Args:
        incluir_noche: Si True, incluye horas nocturnas con es_dia=False.
                       Si False (default), solo horas con luz.
    """
    score_fn = score_fn or _default_score_fn()
    if score_fn is None:
        return []

    tz = spot.get_zoneinfo()

    try:
        daylight = get_daylight(spot, fecha)
    except ValueError:
        return []

    # Filtrar horas del día (todas o solo con luz)
    horas_del_dia = [
        h for h in forecast
        if h.timestamp.astimezone(tz).date() == fecha
    ]

    if not horas_del_dia:
        return []

    # Calcular score + flag es_dia para cada hora
    scored_con_flag: List[Tuple[ForecastHour, ScoreBreakdown, bool]] = []
    for h in horas_del_dia:
        es_dia = is_daylight(h.timestamp, daylight)
        if not incluir_noche and not es_dia:
            continue
        try:
            bd = score_fn(h, spot)
            scored_con_flag.append((h, bd, es_dia))
        except Exception:
            continue

    if not scored_con_flag:
        return []

    # Ordenar cronológicamente para vista hora a hora
    scored_con_flag.sort(key=lambda x: x[0].timestamp)

    # Determinar cuál es la mejor hora del día (para marcar is_best)
    mejor_score = max(x[1].score_total for x in scored_con_flag if x[2])  # solo entre horas con luz
    mejor_ts = next(
        x[0].timestamp for x in scored_con_flag
        if x[2] and x[1].score_total == mejor_score
    )

    # Asignar ranks solo entre horas con luz (orden cronológico preservado)
    horas_dia_sorted_score = sorted(
        [(i, x) for i, x in enumerate(scored_con_flag) if x[2]],
        key=lambda ix: ix[1][1].score_total,
        reverse=True,
    )
    rank_map = {i: rank + 1 for rank, (i, _) in enumerate(horas_dia_sorted_score)}

    result = []
    for i, (h, bd, es_dia) in enumerate(scored_con_flag):
        rank = rank_map.get(i, 999)
        result.append(RankedHour(
            hour=h,
            breakdown=bd,
            rank=rank,
            es_dia=es_dia,
        ))

    return result


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _default_score_fn():
    """
    Carga el motor de scoring real del proyecto.
    Retorna None si no está disponible (tests con mock).
    """
    try:
        from core.scoring.engine import calcular_score
        return calcular_score
    except ImportError:
        return None
