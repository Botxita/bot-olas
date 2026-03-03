"""Análisis semanal: mejor día de la semana para surfear.

Itera sobre los días disponibles en el forecast (hasta 7),
calcula score promedio por día filtrando por luz solar,
y retorna el mejor día con su mejor ventana.

Depende de: core.analysis.best_hour, core.analysis.daylight
No depende de: bot/, telegram, IO, red.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import List, Optional, Tuple

from core.scoring.models import ForecastHour, SpotConfig
from core.analysis.daylight import get_daylight, is_daylight
from core.analysis.best_hour import BestHourResult, calcular_mejor_hora


@dataclass
class DayScore:
    """Score consolidado para un día."""
    fecha: date
    score_promedio: float      # promedio de scores diurnos (0.0–1.0)
    score_max: float           # score de la mejor hora diurna
    score_100: int             # score_promedio * 100
    mejor_hora: Optional[BestHourResult]
    horas_con_luz: int         # cantidad de horas diurnas evaluadas
    tiene_datos: bool          # False si no había forecast para ese día

    @property
    def es_bueno(self) -> bool:
        return self.score_100 >= 55

    @property
    def nombre_dia(self) -> str:
        dias = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
        return dias[self.fecha.weekday()]

    @property
    def nombre_corto(self) -> str:
        dias = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
        return dias[self.fecha.weekday()]


@dataclass
class WeeklyAnalysis:
    """Análisis completo de la semana."""
    scores_por_dia: List[DayScore]      # todos los días, ordenados cronológicamente
    mejor_dia: DayScore                 # día con mayor score_promedio
    peor_dia: DayScore                  # día con menor score_promedio
    dias_buenos: List[DayScore]         # días con score >= 55
    spot: SpotConfig

    @property
    def hay_dias_buenos(self) -> bool:
        return len(self.dias_buenos) > 0

    @property
    def mejor_ventana_semana(self) -> Optional[BestHourResult]:
        """La mejor hora individual de toda la semana."""
        if not self.scores_por_dia:
            return None
        candidatos = [d.mejor_hora for d in self.scores_por_dia if d.mejor_hora is not None]
        if not candidatos:
            return None
        return max(candidatos, key=lambda r: r.score_100)


def analizar_semana(
    forecast: List[ForecastHour],
    spot: SpotConfig,
    score_fn=None,
    max_dias: int = 7,
) -> Optional[WeeklyAnalysis]:
    """
    Analiza hasta 7 días de forecast desde HOY y retorna el ranking semanal.

    Args:
        forecast: Lista completa de ForecastHour (UTC-aware, hasta 7 días).
        spot: SpotConfig con lat, lon, tz.
        score_fn: Función de scoring inyectable.
        max_dias: Cuántos días analizar (default 7).

    Returns:
        WeeklyAnalysis con scores por día y mejor día, o None si no hay datos.
    """
    score_fn = score_fn or _default_score_fn()
    if score_fn is None or not forecast:
        return None

    tz = spot.get_zoneinfo()

    # Solo fechas desde hoy en adelante (sin días pasados)
    hoy = datetime.now(tz).date()
    fechas_disponibles = sorted({
        h.timestamp.astimezone(tz).date()
        for h in forecast
        if h.timestamp.astimezone(tz).date() >= hoy
    })[:max_dias]

    if not fechas_disponibles:
        return None

    scores_por_dia: List[DayScore] = []

    for fecha in fechas_disponibles:
        day_score = _analizar_dia(forecast, spot, fecha, score_fn, tz)
        scores_por_dia.append(day_score)

    # Filtrar días con datos para calcular mejor/peor
    dias_con_datos = [d for d in scores_por_dia if d.tiene_datos]
    if not dias_con_datos:
        return None

    mejor_dia = max(dias_con_datos, key=lambda d: d.score_promedio)
    peor_dia = min(dias_con_datos, key=lambda d: d.score_promedio)
    dias_buenos = [d for d in dias_con_datos if d.es_bueno]

    return WeeklyAnalysis(
        scores_por_dia=scores_por_dia,
        mejor_dia=mejor_dia,
        peor_dia=peor_dia,
        dias_buenos=dias_buenos,
        spot=spot,
    )


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _analizar_dia(
    forecast: List[ForecastHour],
    spot: SpotConfig,
    fecha: date,
    score_fn,
    tz,
) -> DayScore:
    """Calcula el DayScore para un día específico."""

    # Calcular mejor hora (que internamente filtra por luz y calcula scores)
    mejor_hora = calcular_mejor_hora(forecast, spot, fecha, score_fn=score_fn)

    if mejor_hora is None:
        return DayScore(
            fecha=fecha,
            score_promedio=0.0,
            score_max=0.0,
            score_100=0,
            mejor_hora=None,
            horas_con_luz=0,
            tiene_datos=False,
        )

    # Calcular promedio usando todas las horas diurnas del ranking
    scores_diurnos = [
        rh.breakdown.score_total
        for rh in mejor_hora.todas_las_horas
        if rh.es_dia
    ]

    score_promedio = sum(scores_diurnos) / len(scores_diurnos) if scores_diurnos else 0.0
    score_max = mejor_hora.breakdown.score_total

    return DayScore(
        fecha=fecha,
        score_promedio=score_promedio,
        score_max=score_max,
        score_100=round(score_promedio * 100),
        mejor_hora=mejor_hora,
        horas_con_luz=mejor_hora.horas_evaluadas,
        tiene_datos=True,
    )


def _default_score_fn():
    try:
        from core.scoring.engine import calcular_score
        return calcular_score
    except ImportError:
        return None
