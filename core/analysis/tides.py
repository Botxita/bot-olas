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
        spot: Opcional — solo se usa para ajuste de delta_marea.
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

    # Extraer serie de niveles con ajuste de spot si corresponde. Usa
    # delta_marea (no delta_altura) — son calibraciones independientes,
    # una para altura de swell y otra para nivel de marea (regresión #13:
    # antes reutilizaba delta_altura para ambas cosas).
    delta = spot.delta_marea if spot else 0.0
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

    indices_del_dia = [
        i for i, h in enumerate(forecast)
        if h.timestamp.astimezone(tz).date() == fecha
    ]

    if not indices_del_dia:
        return TideAnalysis(
            eventos=[],
            tendencia_actual="sin datos",
            proximo_cambio=None,
            nivel_actual=None,
            tiene_extremos_claros=False,
        )

    horas_del_dia = [forecast[i] for i in indices_del_dia]
    resultado = detectar_mareas(horas_del_dia, spot=spot)

    # `_detectar_extremos()` ignora el primer y último índice de la serie
    # que recibe (necesita ambos vecinos para comparar). Al recortar por
    # fecha, una marea real en el borde del día (ej. pleamar justo a las
    # 00:00 local) queda en ese índice 0/n-1 y nunca se evalúa (#14).
    # Se re-detecta con 2 horas de contexto real a cada lado (si existen en
    # el forecast completo) y se filtran los eventos resultantes de vuelta
    # a los que caen dentro de la fecha pedida — nivel_actual, tendencia_actual
    # y proximo_cambio siguen viniendo de horas_del_dia sin padding, sin
    # cambios (solo eventos y tiene_extremos_claros se recalculan). 2 horas
    # (no 1) porque _suavizar() usa ventana=3: con solo 1 hora de contexto,
    # esa hora de borde queda como índice 0/n-1 de la serie con padding y
    # recibe su propio ventana parcial (2 puntos en vez de 3) — pudiendo
    # todavía enmascarar el extremo real que está justo al lado.
    idx_inicio = max(0, indices_del_dia[0] - 2)
    idx_fin = min(len(forecast), indices_del_dia[-1] + 3)
    horas_con_contexto = forecast[idx_inicio:idx_fin]
    eventos_con_contexto = detectar_mareas(horas_con_contexto, spot=spot).eventos
    eventos_del_dia = [
        e for e in eventos_con_contexto
        if e.timestamp.astimezone(tz).date() == fecha
    ]

    delta = spot.delta_marea if spot else 0.0
    niveles_suavizados_dia = _suavizar([h.tide.nivel_m + delta for h in horas_del_dia], ventana=3)

    resultado.eventos = eventos_del_dia
    resultado.tiene_extremos_claros = (
        len(eventos_del_dia) >= 1
        and _amplitud_serie(niveles_suavizados_dia) >= _AMPLITUD_MINIMA_M
    )

    return resultado


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
    Calcula la pendiente desde el índice más cercano al momento actual
    para que sea correcto cuando el array incluye horas pasadas del día.
    """
    if len(niveles) < 2:
        return "desconocida", None

    # Encontrar índice más cercano a ahora
    from datetime import datetime as _dt, timezone as _tz
    now = _dt.now(_tz.utc)
    idx_now = min(range(len(timestamps)), key=lambda i: abs((timestamps[i] - now).total_seconds()))

    # Pendiente: diferencia entre hora actual y 2 horas más tarde (o lo que quede)
    idx_end = min(idx_now + 2, len(niveles) - 1)
    if idx_end == idx_now:
        # Solo queda una hora — usar diferencia con la anterior
        idx_start = max(0, idx_now - 1)
        pendiente = niveles[idx_now] - niveles[idx_start]
    else:
        pendiente = niveles[idx_end] - niveles[idx_now]

    if pendiente > 0.02:
        tendencia = "subiendo"
    elif pendiente < -0.02:
        tendencia = "bajando"
    else:
        tendencia = "estable"

    # Estimar próximo cambio buscando el primer extremo DESDE el índice actual
    proximo_cambio = None
    for i in range(max(1, idx_now), len(niveles) - 1):
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
