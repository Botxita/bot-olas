# pronostico_api.py
"""
Lógica de obtención y formateo de pronósticos de olas.

- Usa la API de Open-Meteo Marine para olas (sin API key).
- Usa la API de Open-Meteo Forecast para viento y salida/puesta del sol.
- Devuelve un texto pensado para chat (Telegram o simulador) con:
    1) Resumen general del día
    2) Mejores ventanas (bloques horarios)
    3) Detalle hora por hora con una calificación simple

IMPORTANTE:
- Open-Meteo devuelve el viento en km/h por defecto -> NO convertir m/s->km/h.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import List, Optional, Tuple

import requests

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


from spots_config import SPOTS
from ajustes_spots import cargar_ajustes_para, aplicar_ajustes


# ---------------------------------------------------------------------------
# Modelo de datos interno
# ---------------------------------------------------------------------------

@dataclass
class PuntoOla:
    dt: datetime
    altura_m: float
    periodo_s: float
    viento_vel_kmh: Optional[float]      # puede ser None si no tenemos dato
    viento_dir_grados: float             # dirección del viento (desde dónde sopla)
    swell_dir_grados: float              # dirección del swell (desde dónde viene)
    score: float = 0.0


# ---------------------------------------------------------------------------
# Helpers de API
# ---------------------------------------------------------------------------
def format_hour_line(hora: str,
                     altura: str,
                     periodo: str,
                     txt_viento: str,
                     txt_swell: str,
                     estrellas: str) -> str:
    # Formato final:
    # 06:00 0.7m/6s 💨12NW 🌊E ★★
    return f"{hora} {altura}m/{periodo}s {txt_viento} {txt_swell} {estrellas}"




def _pedir_marine(lat: float, lon: float, fecha: date, timezone: str):
    """Pide datos a la API marine (olas + viento si hay)."""
    url = "https://marine-api.open-meteo.com/v1/marine"
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "wave_height,wave_period,wave_direction,wind_speed_10m,wind_direction_10m",
        "start_date": fecha.isoformat(),
        "end_date": fecha.isoformat(),
        "timezone": timezone,
    }
    resp = requests.get(url, params=params, timeout=10, verify=False)
    resp.raise_for_status()
    data = resp.json()
    hourly = data.get("hourly", {})
    tiempos = hourly.get("time", []) or []
    alturas = hourly.get("wave_height", []) or []
    periodos = hourly.get("wave_period", []) or []
    olas_dir = hourly.get("wave_direction", []) or []
    viento_vel = hourly.get("wind_speed_10m", []) or []
    viento_dir = hourly.get("wind_direction_10m", []) or []
    return tiempos, alturas, periodos, olas_dir, viento_vel, viento_dir


def _pedir_forecast_viento(lat: float, lon: float, fecha: date, timezone: str):
    """Pide solo viento a la API de pronóstico general de Open-Meteo."""
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "wind_speed_10m,wind_direction_10m",
        "start_date": fecha.isoformat(),
        "end_date": fecha.isoformat(),
        "timezone": timezone,
    }
    resp = requests.get(url, params=params, timeout=10, verify=False)
    resp.raise_for_status()
    data = resp.json()
    hourly = data.get("hourly", {})
    tiempos = hourly.get("time", []) or []
    viento_vel = hourly.get("wind_speed_10m", []) or []
    viento_dir = hourly.get("wind_direction_10m", []) or []
    return tiempos, viento_vel, viento_dir


def _obtener_sunrise_sunset(
    lat: float,
    lon: float,
    fecha: date,
    timezone: str = "America/Argentina/Buenos_Aires",
) -> Tuple[Optional[datetime], Optional[datetime]]:
    """
    Pide hora de amanecer y atardecer para esa fecha y spot.
    """
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "sunrise,sunset",
        "start_date": fecha.isoformat(),
        "end_date": fecha.isoformat(),
        "timezone": timezone,
    }
    try:
        resp = requests.get(url, params=params, timeout=10, verify=False)
        resp.raise_for_status()
        data = resp.json()
        daily = data.get("daily", {})
        amaneceres = daily.get("sunrise", []) or []
        atardeceres = daily.get("sunset", []) or []
        if not amaneceres or not atardeceres:
            return None, None
        amanecer = datetime.fromisoformat(amaneceres[0])
        atardecer = datetime.fromisoformat(atardeceres[0])
        return amanecer, atardecer
    except Exception:
        return None, None


# ---------------------------------------------------------------------------
# Direcciones -> flechas/compás
# ---------------------------------------------------------------------------

# 0° = N, 90° = E, 180° = S, 270° = W (dirección DESDE donde viene)
_DIRS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
# Usamos una flecha genérica que se ve bien en todos lados
_ARROW = "→"


def _sector_index(grados: float) -> int:
    """Devuelve el índice 0–7 del sector de 45° correspondiente."""
    return int(((grados % 360) / 45.0) + 0.5) % 8

def _formatear_dir_compas(grados: float) -> str:
    """
    Versión “larga” si en algún momento la querés usar:
    '→ N', '→ NE', etc.
    """
    idx = _sector_index(grados)
    return f"{_ARROW} {_DIRS[idx]}"

def _dir_compacto(grados: float) -> str:
    idx = _sector_index(grados)
    # Solo devuelve la sigla: N, NE, E, SE, S, SW, W, NW
    return _DIRS[idx]


# ---------------------------------------------------------------------------
# API real (olas + viento con fallback)
# ---------------------------------------------------------------------------

def _obtener_datos_olas_reales(
    lat: float,
    lon: float,
    fecha: date,
    timezone: str = "America/Argentina/Buenos_Aires",
) -> List[PuntoOla]:
    """
    Devuelve una lista de PuntoOla hora a hora (24 hs UTC local):

    - Olas: siempre de la API marine.
    - Viento: marine si está disponible; si no, fallback a API forecast.
    """
    (
        tiempos,
        alturas,
        periodos,
        olas_dir,
        viento_vel,
        viento_dir,
    ) = _pedir_marine(lat, lon, fecha, timezone)

    n = min(len(tiempos), len(alturas), len(periodos), len(olas_dir))
    tiempos = tiempos[:n]
    alturas = alturas[:n]
    periodos = periodos[:n]
    olas_dir = olas_dir[:n]
    viento_vel = viento_vel[:n]
    viento_dir = viento_dir[:n]

    tiene_viento_marine = any(v is not None for v in viento_vel)

    if not tiene_viento_marine and tiempos:
        try:
            tiempos_v, viento_vel_v, viento_dir_v = _pedir_forecast_viento(
                lat, lon, fecha, timezone
            )
            m = min(len(tiempos), len(tiempos_v), len(viento_vel_v), len(viento_dir_v))
            if m > 0:
                viento_vel = viento_vel_v[:m]
                viento_dir = viento_dir_v[:m]
                tiempos = tiempos[:m]
                alturas = alturas[:m]
                periodos = periodos[:m]
                olas_dir = olas_dir[:m]
        except Exception:
            viento_vel = [None] * len(tiempos)
            viento_dir = [0.0] * len(tiempos)

    puntos: List[PuntoOla] = []

    for t, h, p, sd, vv, vd in zip(
        tiempos, alturas, periodos, olas_dir, viento_vel, viento_dir
    ):
        dt = datetime.fromisoformat(t)

        h_val = float(h) if h is not None else 0.0
        p_val = float(p) if p is not None else 0.0

        if vv is None:
            kmh: Optional[float] = None
        else:
            kmh = float(vv)

        vd_val = float(vd) if vd is not None else 0.0
        sd_val = float(sd) if sd is not None else 0.0

        puntos.append(
            PuntoOla(
                dt=dt,
                altura_m=h_val,
                periodo_s=p_val,
                viento_vel_kmh=kmh,
                viento_dir_grados=vd_val,
                swell_dir_grados=sd_val,
            )
        )

    return puntos


def _filtrar_horas_luz(
    puntos: List[PuntoOla],
    lat: float,
    lon: float,
    fecha: date,
    timezone: str = "America/Argentina/Buenos_Aires",
) -> Tuple[List[PuntoOla], Optional[datetime], Optional[datetime]]:
    """
    Devuelve solo los puntos entre amanecer y atardecer.
    Si no se puede obtener esa info, devuelve los puntos originales.
    """
    amanecer, atardecer = _obtener_sunrise_sunset(lat, lon, fecha, timezone)
    if amanecer is None or atardecer is None:
        return puntos, None, None

    filtrados = [p for p in puntos if amanecer <= p.dt < atardecer]
    if not filtrados:
        return puntos, amanecer, atardecer

    return filtrados, amanecer, atardecer


# ---------------------------------------------------------------------------
# Scoring y análisis de “calidad de sesión”
# ---------------------------------------------------------------------------

def _score_punto(p: PuntoOla) -> float:
    h = p.altura_m
    T = p.periodo_s
    v = p.viento_vel_kmh

    score = 0.0

    if h <= 0 or T <= 0:
        return 0.0

    ideal_h = 1.2
    diff_h = abs(h - ideal_h)
    factor_h = max(0.0, 1.0 - diff_h / 1.2)
    score += factor_h * 40.0

    if T < 6:
        factor_T = 0.0
    elif 6 <= T < 9:
        factor_T = (T - 6) / 3.0 * 0.6
    else:
        factor_T = 0.6 + min((T - 9) / 5.0 * 0.4, 0.4)
    score += factor_T * 40.0

    if v is not None:
        if v <= 5:
            score += 20.0
        elif v <= 10:
            score += 10.0

    return max(0.0, min(100.0, score))


def _calcular_scores(puntos: List[PuntoOla]) -> None:
    for p in puntos:
        p.score = _score_punto(p)


def _score_a_estrellas(score: float) -> str:
    if score < 15:
        return "-"
    estrellas = max(1, min(5, int(round(score / 20.0))))
    return "★" * estrellas


def _texto_calidad(score: float) -> str:
    if score < 25:
        return "baja"
    if score < 45:
        return "media-baja"
    if score < 65:
        return "media"
    if score < 80:
        return "buena"
    return "muy buena"


def _agrupar_mejores_ventanas(
    puntos: List[PuntoOla],
    umbral_score: float = 65.0,
    max_ventanas: int = 3,
) -> List[Tuple[datetime, datetime, float]]:
    ventanas = []
    actual_inicio = None
    scores_actuales: List[float] = []

    for p in puntos:
        if p.score >= umbral_score:
            if actual_inicio is None:
                actual_inicio = p.dt
                scores_actuales = [p.score]
            else:
                scores_actuales.append(p.score)
        else:
            if actual_inicio is not None:
                fin = p.dt
                promedio = sum(scores_actuales) / len(scores_actuales)
                ventanas.append((actual_inicio, fin, promedio))
                actual_inicio = None
                scores_actuales = []

    if actual_inicio is not None and scores_actuales:
        fin = puntos[-1].dt
        promedio = sum(scores_actuales) / len(scores_actuales)
        ventanas.append((actual_inicio, fin, promedio))

    ventanas.sort(key=lambda v: v[2], reverse=True)
    return ventanas[:max_ventanas]


# ---------------------------------------------------------------------------
# Traducción “surf-report” del resumen
# ---------------------------------------------------------------------------

def _descripcion_resumen(h_max: float, T_max: float, score_prom: float) -> str:
    if h_max < 0.4:
        return "Flat a muy chico. Solo para remar o chapotear con tabla grande."

    if h_max < 0.8:
        if score_prom < 30:
            return "Chico y bastante tocado por viento/mar de fondo. Sesión floja, solo long/foamy."
        elif score_prom < 55:
            return "Chico pero relativamente ordenado. Bien para longboard, SUP o tabla ancha, sin exigirse."
        else:
            return "Chico y prolijo casi todo el día. Buenas opciones para tablas grandes y entrenamiento suave."

    if h_max < 1.5:
        if score_prom < 40:
            return "Tamaño medio con mar mezclado. Se surfea algo, pero con secciones cortas e inestables."
        elif score_prom < 55:
            return "Tamaño medio, condiciones irregulares. Se rascan olas y puede haber momentos buenos aislados."
        elif score_prom < 65:
            return "Tamaño medio con paredes razonables. No es épico, pero se puede surfear bien si elegís la hora."
        else:
            return "Tamaño medio con buenas condiciones. Día sólido para sacar el shortboard y moverse."

    if score_prom < 40:
        return "Tamaño grande con mar desordenado. Solo recomendable con buena experiencia y ganas de remar."
    elif score_prom < 65:
        return "Tamaño grande con condiciones mixtas. Hay secciones muy buenas si se elige bien la ventana."
    else:
        return "Tamaño grande y condiciones consistentes. Sesión potente para entrar descansado y concentrado."


# ---------------------------------------------------------------------------
# Texto formateado para el bot
# ---------------------------------------------------------------------------

def obtener_pronostico_formateado(
    spot_key: str,
    playa_key: str,
    fecha: Optional[date] = None,
) -> str:
    from datetime import date as _date

    if fecha is None:
        fecha = _date.today()

    spot = SPOTS[spot_key]
    playa = spot["playas"][playa_key]

    lat = playa["lat"]
    lon = playa["lon"]

    try:
        puntos = _obtener_datos_olas_reales(lat, lon, fecha)
    except Exception as e:
        return (
            f"No pude obtener el pronóstico para {playa['nombre']} ({spot['nombre']}).\n"
            f"Detalle técnico: {e}"
        )

    if not puntos:
        return f"No hay datos de olas para {playa['nombre']} en la fecha indicada."

    puntos, amanecer, atardecer = _filtrar_horas_luz(puntos, lat, lon, fecha)

    ajustes = cargar_ajustes_para(spot_key, playa_key)
    for p in puntos:
        p.altura_m, p.periodo_s = aplicar_ajustes(
            p.altura_m,
            p.periodo_s,
            ajustes,
        )

    _calcular_scores(puntos)

    alturas = [p.altura_m for p in puntos if p.altura_m > 0]
    periodos = [p.periodo_s for p in puntos if p.periodo_s > 0]
    scores = [p.score for p in puntos]

    h_min = min(alturas) if alturas else 0.0
    h_max = max(alturas) if alturas else 0.0
    T_min = min(periodos) if periodos else 0.0
    T_max = max(periodos) if periodos else 0.0
    score_prom = sum(scores) / len(scores) if scores else 0.0

    ventanas = _agrupar_mejores_ventanas(puntos)
    etiqueta_calidad = _texto_calidad(score_prom)

    txt_fecha = fecha.strftime("%d/%m/%Y")
    lineas: List[str] = []

    lineas.append(f"🌊 Pronóstico de olas para {playa['nombre']} ({spot['nombre']})")
    lineas.append(f"📅 Fecha: {txt_fecha}")
    if amanecer and atardecer:
        lineas.append(
            f"☀️ Horas consideradas: {amanecer.strftime('%H:%M')} – {atardecer.strftime('%H:%M')} hs"
        )
    lineas.append("")

    lineas.append("📌 Resumen del día:")
    if alturas and periodos:
        lineas.append(f"- Altura aprox: {h_min:.1f} – {h_max:.1f} m")
        lineas.append(f"- Período aprox: {T_min:.0f} – {T_max:.0f} s")
        lineas.append(
            f"- Calidad de las olas: {etiqueta_calidad} · "
            f"{_score_a_estrellas(score_prom)} (score ~ {score_prom:.0f}/100)"
        )
        descripcion = _descripcion_resumen(h_max, T_max, score_prom)
        lineas.append(f"- Lectura surf: {descripcion}")
    else:
        lineas.append("- No hay datos suficientes de altura/período.")
    lineas.append("")

    lineas.append("⭐ Mejores ventanas del día:")
    if ventanas:
        for i, (inicio, fin, prom) in enumerate(ventanas, start=1):
            h_ini = inicio.strftime("%H:%M")
            h_fin = fin.strftime("%H:%M")
            estrellas = _score_a_estrellas(prom)
            lineas.append(
                f"{i}) {h_ini} – {h_fin} hs · {estrellas} (score ~ {prom:.0f}/100)"
            )
    else:
        if score_prom < 35:
            lineas.append("No se ve una ventana muy prolija; el mar se mantiene bastante cruzado/desordenado.")
        elif score_prom < 55:
            lineas.append("No hay una ventana claramente mejor; el día se mantiene parejo, sin momentos muy destacados.")
        else:
            lineas.append("Hay condiciones aceptables, pero sin bloques largos que superen el umbral de calidad marcado.")
    lineas.append("")

    # Detalle compacto
    lineas.append("🕒 Detalle hora a hora:")
    for p in puntos:
        hora = p.dt.strftime("%H:%M")
        estrellas = _score_a_estrellas(p.score)

        altura_str = f"{p.altura_m:.1f}"
        periodo_str = f"{p.periodo_s:.0f}"

        if p.viento_vel_kmh is None:
            txt_viento = "💨--"
        else:
            txt_viento = f"💨{p.viento_vel_kmh:.0f}{_dir_compacto(p.viento_dir_grados)}"

        txt_swell = f"🌊{_dir_compacto(p.swell_dir_grados)}"

        linea = format_hour_line(
            hora=hora,
            altura=altura_str,
            periodo=periodo_str,
            txt_viento=txt_viento,
            txt_swell=txt_swell,
            estrellas=estrellas,
        )
        lineas.append(linea)


    if ajustes:
        lineas.append("")
        lineas.append("🔧 Ajustes aplicados a esta playa:")
        for k, v in ajustes.items():
            lineas.append(f"  • {k} = {v}")

    lineas.append("")
    lineas.append("Escribí 'v' para volver al menú anterior.")
    return "\n".join(lineas)
