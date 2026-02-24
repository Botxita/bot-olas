"""Detección de mareas altas y bajas a partir de la serie horaria.

FUENTE DE DATOS: sea_level_height_msl de Open-Meteo.
Esto es un proxy MSL — incorpora componentes mareales pero NO está
referenciado al datum náutico (LAT/MLLW). La detección de extremos
es válida como indicador relativo; los horarios pueden diferir
hasta ±30–60 min respecto a tablas de marea oficiales.

El texto visible al usuario siempre incluirá "marea estimada (proxy MSL)".

Algoritmo:
  - Suavizado opcional por media móvil (reduce ruido del proxy MSL).
  - Detección de máximos/mínimos locales por ventana de 3 puntos.
  - Fallback: si no se detectan extremos claros, retorna tendencia
    (subiendo/bajando) y estimación del próximo cambio de pendiente.
"""

from dataclasses import dataclass
from datetime import datetime, date
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

from core.scoring.models import ForecastHour, SpotConfig


# Umbral mínimo de amplitud para considerar un extremo "claro"
# (evita detectar micro-oscilaciones del proxy MSL)
_AMPLITUD_MINIMA_M = 0.05


@dataclass
class TideEvent:
    """Un evento de marea detectado (alta o baja)."""
    timestamp: datetime       # UTC-aware
    nivel_m: float
    tipo: str                 # "alta" | "baja"
    es_estimado: bool = True  # Siempre True para proxy MSL

    def timestamp_local(self, tz: ZoneInfo) -> datetime:
        return self.timestamp.astimezone(tz)

    def __str__(self) -> str:
        simbolo = "▲" if self.tipo == "alta" else "▼"
        return f"{simbolo} {self.tipo.capitalize()}: {self.nivel_m:.2f}m"


@dataclass
class TideAnalysis:
    """Resultado completo del análisis de mareas para un período."""
    eventos: List[TideEvent]          # Extremos detectados (ordenados por tiempo)
    tendencia_actual: str             # "subiendo" | "bajando" | "estable"
    proximo_cambio: Optional[datetime]  # UTC — cuándo cambia la tendencia
    nivel_actual: Optional[float]     # nivel en el momento más reciente disponible
    tiene_extremos_claros: bool       # False = solo tendencia disponible
    fuente: str = "proxy_msl"

    @property
    def proxima_alta(self) -> Optional[TideEvent]:
        for e in self.eventos:
            if e.tipo == "alta":
                return e
        return None

    @property
    def proxima_baja(self) -> Optional[TideEvent]:
        for e in self.eventos:
            if e.tipo == "baja":
                return e
        return None


def detectar_mareas(
    forecast: List[ForecastHour],
    spot: Optional[SpotConfig] = None,
    desde: Optional[datetime] = None,
) -> TideAnalysis:
    """
    Detecta eventos de marea alta y baja en la serie horaria.

    Args:
        forecast: Lista de ForecastHour ordenada cronológicamente.
        spot: Opcional — solo se usa para ajuste de delta_altura.
        desde: Si se indica, solo considera eventos a partir de esta hora (UTC).
               Útil para filtrar el pasado y mostrar solo eventos futuros.

    Returns:
        TideAnalysis con eventos detectados y tendencia.
    """
    if len(forecast) < 3:
        return TideAnalysis(
            eventos=[],
            tendencia_actual="desconocida",
            proximo_cambio=None,
            nivel_actual=None,
            tiene_extremos_claros=False,
        )

    # Extraer serie de niveles con ajuste de spot si corresponde
    delta = spot.delta_altura if spot else 0.0
    niveles = [h.tide.nivel_m + delta for h in forecast]
    timestamps = [h.timestamp for h in forecast]

    # Suavizado por media móvil de 3 puntos para reducir ruido proxy
    niveles_suavizados = _suavizar(niveles, ventana=3)

    # Detectar extremos locales
    eventos = _detectar_extremos(timestamps, niveles_suavizados, niveles)

    # Filtrar por 'desde' si se indica
    if desde is not None:
        eventos = [e for e in eventos if e.timestamp >= desde]

    # Calcular tendencia actual (últimas 3 horas disponibles)
    tendencia, proximo_cambio = _calcular_tendencia(timestamps, niveles_suavizados)

    nivel_actual = niveles[0] if niveles else None

    tiene_claros = (
        len(eventos) >= 1
        and _amplitud_serie(niveles_suavizados) >= _AMPLITUD_MINIMA_M
    )

    return TideAnalysis(
        eventos=eventos,
        tendencia_actual=tendencia,
        proximo_cambio=proximo_cambio,
        nivel_actual=nivel_actual,
        tiene_extremos_claros=tiene_claros,
        fuente=forecast[0].tide.fuente if forecast else "proxy_msl",
    )


def detectar_mareas_del_dia(
    forecast: List[ForecastHour],
    fecha: date,
    spot: Optional[SpotConfig] = None,
    tz: Optional[ZoneInfo] = None,
) -> TideAnalysis:
    """
    Detecta mareas solo para las horas que correspondan a 'fecha' en la tz del spot.
    Útil para mostrar tabla de un día específico.

    Args:
        forecast: Lista completa de ForecastHour.
        fecha: Fecha local (date) del spot.
        spot: SpotConfig (para tz y delta).
        tz: ZoneInfo override (si spot es None).
    """
    if tz is None and spot is not None:
        tz = spot.get_zoneinfo()
    if tz is None:
        from zoneinfo import ZoneInfo as _ZI
        tz = _ZI("UTC")

    horas_del_dia = [
        h for h in forecast
        if h.timestamp.astimezone(tz).date() == fecha
    ]

    if not horas_del_dia:
        return TideAnalysis(
            eventos=[],
            tendencia_actual="sin datos",
            proximo_cambio=None,
            nivel_actual=None,
            tiene_extremos_claros=False,
        )

    return detectar_mareas(horas_del_dia, spot=spot)


# ---------------------------------------------------------------------------
# Funciones internas
# ---------------------------------------------------------------------------

def _suavizar(valores: List[float], ventana: int = 3) -> List[float]:
    """Media móvil centrada. Los extremos usan ventana parcial."""
    n = len(valores)
    half = ventana // 2
    result = []
    for i in range(n):
        inicio = max(0, i - half)
        fin = min(n, i + half + 1)
        result.append(sum(valores[inicio:fin]) / (fin - inicio))
    return result


def _detectar_extremos(
    timestamps: List[datetime],
    niveles_suavizados: List[float],
    niveles_originales: List[float],
) -> List[TideEvent]:
    """
    Detecta máximos y mínimos locales usando ventana de 3 puntos.
    Usa el nivel suavizado para detectar, pero reporta el nivel original.
    """
    eventos = []
    n = len(niveles_suavizados)

    for i in range(1, n - 1):
        prev_ = niveles_suavizados[i - 1]
        curr = niveles_suavizados[i]
        next_ = niveles_suavizados[i + 1]

        if curr > prev_ and curr > next_:
            eventos.append(TideEvent(
                timestamp=timestamps[i],
                nivel_m=round(niveles_originales[i], 3),
                tipo="alta",
                es_estimado=True,
            ))
        elif curr < prev_ and curr < next_:
            eventos.append(TideEvent(
                timestamp=timestamps[i],
                nivel_m=round(niveles_originales[i], 3),
                tipo="baja",
                es_estimado=True,
            ))

    return eventos


def _calcular_tendencia(
    timestamps: List[datetime],
    niveles: List[float],
) -> Tuple[str, Optional[datetime]]:
    """
    Determina tendencia actual y estima próximo cambio de tendencia.
    Usa las primeras N horas para calcular pendiente reciente.
    """
    if len(niveles) < 2:
        return "desconocida", None

    # Pendiente reciente: diferencia entre hora 0 y hora 2 (o siguiente disponible)
    n = min(3, len(niveles))
    pendiente = niveles[n - 1] - niveles[0]

    if pendiente > 0.02:
        tendencia = "subiendo"
    elif pendiente < -0.02:
        tendencia = "bajando"
    else:
        tendencia = "estable"

    # Estimar próximo cambio buscando el primer extremo en la serie completa
    proximo_cambio = None
    for i in range(1, len(niveles) - 1):
        prev_ = niveles[i - 1]
        curr = niveles[i]
        next_ = niveles[i + 1]
        if (curr > prev_ and curr > next_) or (curr < prev_ and curr < next_):
            proximo_cambio = timestamps[i]
            break

    return tendencia, proximo_cambio


def _amplitud_serie(niveles: List[float]) -> float:
    """Diferencia entre máximo y mínimo de la serie."""
    if not niveles:
        return 0.0
    return max(niveles) - min(niveles)
