"""Vista hora a hora para un día específico.

Genera una lista de HourlyRow lista para ser formateada por bot/formatters.py.
Toda la lógica de presentación (emojis, texto) vive en formatters, no acá.

Depende de: core.analysis.best_hour (calcular_ranking_dia)
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import List, Optional

from core.scoring.models import ForecastHour, ScoreBreakdown, SpotConfig
from core.analysis.daylight import DaylightInfo, get_daylight
from core.analysis.best_hour import RankedHour, calcular_ranking_dia


@dataclass
class HourlyRow:
    """Una fila de la vista hora a hora."""
    hour: ForecastHour
    breakdown: ScoreBreakdown
    rank: int             # posición por score (1 = mejor del día)
    es_dia: bool          # True si hay luz solar
    es_mejor: bool        # True si es la hora con mayor score del día


@dataclass
class HourlyViewResult:
    """Resultado completo de la vista hora a hora para un día."""
    fecha: date
    spot: SpotConfig
    filas: List[HourlyRow]        # ordenadas cronológicamente
    daylight: DaylightInfo
    mejor_hora: Optional[HourlyRow]   # fila con es_mejor=True


def generar_vista_horaria(
    forecast: List[ForecastHour],
    spot: SpotConfig,
    fecha: date,
    score_fn=None,
    incluir_noche: bool = True,
) -> Optional[HourlyViewResult]:
    """
    Genera la vista hora a hora para el día 'fecha'.

    Args:
        forecast: Lista completa de ForecastHour (UTC-aware).
        spot: SpotConfig con lat, lon, tz.
        fecha: Fecha local del spot.
        score_fn: Función de scoring inyectable (útil para tests).
        incluir_noche: Si True (default), incluye horas nocturnas con es_dia=False
                       y el indicador 🌙 en el formatter.

    Returns:
        HourlyViewResult con filas cronológicas, o None si no hay datos.
    """
    score_fn = score_fn or _default_score_fn()
    if score_fn is None:
        return None

    # Obtener ranking del día (cronológico, con flag es_dia)
    ranked: List[RankedHour] = calcular_ranking_dia(
        forecast, spot, fecha,
        score_fn=score_fn,
        incluir_noche=incluir_noche,
    )

    if not ranked:
        return None

    # Calcular daylight del día
    try:
        daylight = get_daylight(spot, fecha)
    except ValueError:
        return None

    # Determinar la mejor hora (rank=1 entre las diurnas)
    mejor_rank1 = next((r for r in ranked if r.rank == 1 and r.es_dia), None)

    # Construir HourlyRow con flag es_mejor
    filas: List[HourlyRow] = []
    for r in ranked:
        es_mejor = (
            mejor_rank1 is not None
            and r.hour.timestamp == mejor_rank1.hour.timestamp
        )
        filas.append(HourlyRow(
            hour=r.hour,
            breakdown=r.breakdown,
            rank=r.rank,
            es_dia=r.es_dia,
            es_mejor=es_mejor,
        ))

    mejor_fila = next((f for f in filas if f.es_mejor), None)

    return HourlyViewResult(
        fecha=fecha,
        spot=spot,
        filas=filas,
        daylight=daylight,
        mejor_hora=mejor_fila,
    )


def _default_score_fn():
    try:
        from core.scoring.engine import calcular_score
        return calcular_score
    except ImportError:
        return None
