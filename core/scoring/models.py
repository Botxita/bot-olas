"""Modelos de datos para el motor de scoring.

Todos los modelos son dataclasses para facilitar serialización,
testing sin Telegram, y uso futuro como API REST.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class SwellData:
    """Datos de oleaje marino."""
    altura_m: float          # Significant wave height (m)
    periodo_s: float          # Peak wave period (s)
    direccion_deg: float      # Direction waves come FROM (meteorological convention, 0=N, 90=E)
    altura_viento_m: float = 0.0   # Wind wave component (m) — informativo


@dataclass
class WindData:
    """Datos de viento."""
    velocidad_kmh: float
    rafaga_kmh: float
    direccion_deg: float      # Direction wind comes FROM (0=N, 90=E)


@dataclass
class TideData:
    """
    Datos de marea.

    IMPORTANTE: open-meteo sea_level_height_msl es un proxy de nivel del mar
    que incorpora componente mareal pero NO está referenciado a LAT/MLLW
    estándar náutico. Precisión costera limitada.
    Usar como indicador relativo para scoring surfer, no para navegación.
    """
    nivel_m: float            # Nivel en metros (referencia MSL de open-meteo)
    fuente: str = "proxy_msl"  # "proxy_msl" | "stormglass" | "worldtides"
    es_exacto: bool = False    # False = proxy; True = dato de estación real


@dataclass
class ForecastHour:
    """Pronóstico para una hora específica."""
    timestamp: datetime
    swell: SwellData
    wind: WindData
    tide: TideData


@dataclass
class ScoreBreakdown:
    """Desglose completo del score para una hora."""
    # Sub-scores (0.0 a 1.0)
    score_energia: float
    score_periodo: float
    score_dir_swell: float
    score_viento: float
    score_marea: float

    # Score final ponderado (0.0 a 1.0 → se muestra como 0-100)
    score_total: float

    # Datos crudos (para debug y display)
    energia_proxy: float      # H² * T

    # Flags explicativos (texto corto para el usuario)
    flags_positivos: list = field(default_factory=list)
    flags_negativos: list = field(default_factory=list)
    flags_neutros: list = field(default_factory=list)

    @property
    def score_100(self) -> int:
        return round(self.score_total * 100)

    @property
    def estrellas(self) -> str:
        """Representación en estrellas (0–5, mitades incluidas)."""
        s = self.score_total * 5
        llenas = int(s)
        media = (s - llenas) >= 0.4
        vacias = 5 - llenas - (1 if media else 0)
        return "⭐" * llenas + ("✨" if media else "") + "·" * vacias

    @property
    def etiqueta(self) -> str:
        """Etiqueta cualitativa."""
        s = self.score_100
        if s >= 85: return "Épico"
        if s >= 70: return "Excelente"
        if s >= 55: return "Bueno"
        if s >= 40: return "Regular"
        if s >= 25: return "Pobre"
        return "Flat / No apto"


@dataclass
class VentanaOptima:
    """Ventana horaria con condiciones por encima del umbral."""
    inicio: datetime
    fin: datetime
    score_promedio: float
    score_max: float
    hora_pico: datetime
    descripcion: str          # "Mañana 07–10h: offshore + 13s + marea subiendo"
    horas_count: int

    @property
    def score_100(self) -> int:
        return round(self.score_promedio * 100)


# Zonas horarias IANA por país — fallback cuando no se especifica en JSON
_TZ_FALLBACK_POR_PAIS = {
    "AR": "America/Argentina/Buenos_Aires",
    "BR": "America/Sao_Paulo",
    "CL": "America/Santiago",
    "PE": "America/Lima",
    "CR": "America/Costa_Rica",
    "MX": "America/Mexico_City",
    "CO": "America/Bogota",
    "UY": "America/Montevideo",
    "EC": "America/Guayaquil",
}


@dataclass
class SpotConfig:
    """Configuración completa de un spot, cargada desde JSON."""
    key: str
    nombre: str
    ciudad: str
    pais: str
    region: str
    lat: float
    lon: float
    orientacion_costa_deg: float
    tolerancia_swell_deg: float
    tipo_break: str           # "beach" | "reef" | "point"
    fondo: str                # "arena" | "roca" | "coral"
    marea_min_m: float
    marea_max_m: float
    marea_tipo_efecto: str    # "low_better" | "high_better" | "mid_better"
    swell_altura_min: float
    swell_altura_max: float
    swell_periodo_min: float
    viento_max_offshore: float
    viento_max_onshore: float
    delta_altura: float = 0.0
    factor_periodo: float = 1.0
    fuente_datos: str = "open-meteo"
    notas: str = ""
    # Timezone IANA del spot (ej: "America/Argentina/Buenos_Aires")
    # Se usa para toda la presentación al usuario y cálculos de luz solar.
    # Si no se especifica en el JSON, se infiere del campo `pais`.
    tz: str = "America/Argentina/Buenos_Aires"

    def get_zoneinfo(self):
        """Retorna ZoneInfo para este spot. Lazy import para compatibilidad."""
        from zoneinfo import ZoneInfo
        try:
            return ZoneInfo(self.tz)
        except Exception:
            # Fallback seguro
            return ZoneInfo("America/Argentina/Buenos_Aires")
