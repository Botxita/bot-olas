"""Formateadores de mensajes para Telegram.

Convierte datos de score y pronóstico en mensajes listos para enviar.
Completamente independiente de la lógica de scoring y del bot.
Testeable con cualquier ScoreBreakdown/ForecastHour de fixture.

Cambios V2:
  - Sin lógica Pro (todo habilitado para todos).
  - pytz reemplazado por zoneinfo (stdlib).
  - Timezone tomada del SpotConfig.tz en lugar de hardcodear AR.
  - Nuevas funciones: formato_mareas, formato_mejor_hora,
    formato_vista_horaria, formato_semana.
"""

from datetime import datetime, date
from typing import List, Optional
from zoneinfo import ZoneInfo

from core.scoring.models import ForecastHour, ScoreBreakdown, SpotConfig, VentanaOptima
from core.analysis.daylight import DaylightInfo
from core.analysis.tides import TideAnalysis, TideEvent
from core.analysis.best_hour import BestHourResult
from core.analysis.hourly_view import HourlyRow, HourlyViewResult
from core.analysis.weekly import DayScore, WeeklyAnalysis

SEPARADOR = "─" * 22


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _tz(spot: SpotConfig) -> ZoneInfo:
    return spot.get_zoneinfo()


def _ts_local(ts: datetime, spot: SpotConfig) -> str:
    """Convierte UTC a hora local del spot y formatea HH:MM."""
    return ts.astimezone(_tz(spot)).strftime("%H:%M")


def _dia_relativo(ts: datetime, spot: SpotConfig) -> str:
    """Retorna 'Hoy', 'Mañana', 'Ayer' o nombre corto del día."""
    tz = _tz(spot)
    now_local = datetime.now(tz)
    ts_local = ts.astimezone(tz)
    delta = (ts_local.date() - now_local.date()).days
    if delta == 0:  return "Hoy"
    if delta == 1:  return "Mañana"
    if delta == -1: return "Ayer"
    dias = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
    return dias[ts_local.weekday()]


def _dir_a_texto(deg: float) -> str:
    """Convierte grados a punto cardinal (16 puntos)."""
    dirs = ["N","NNE","NE","ENE","E","ESE","SE","SSE",
            "S","SSO","SO","OSO","O","ONO","NO","NNO"]
    return dirs[round(deg / 22.5) % 16]


def _estrellas(score: float) -> str:
    """Retorna emoji de estrellas para un score 0–1."""
    s = score * 5
    llenas = int(s)
    media = (s - llenas) >= 0.4
    vacias = 5 - llenas - (1 if media else 0)
    return "⭐" * llenas + ("✨" if media else "") + "·" * max(0, vacias)


def _emoji_score(score_100: int) -> str:
    if score_100 >= 70: return "🟢"
    if score_100 >= 45: return "🟡"
    return "🔴"


def _barra_score(score: float, ancho: int = 10) -> str:
    llenos = round(score * ancho)
    return "█" * llenos + "░" * (ancho - llenos)


# ---------------------------------------------------------------------------
# Funciones existentes (mantenidas, sin lógica Pro)
# ---------------------------------------------------------------------------

def formato_condiciones_actuales(
    hour: ForecastHour,
    breakdown: ScoreBreakdown,
    spot: SpotConfig,
    es_pro: bool = True,   # ignorado — todo habilitado
    tide_analysis=None,    # TideAnalysis opcional para mostrar tendencia de marea
) -> str:
    """Mensaje completo de condiciones actuales."""
    lineas = []

    lineas.append(f"🌊 *{spot.ciudad} · {spot.nombre}*")
    dia = _dia_relativo(hour.timestamp, spot)
    hora = _ts_local(hour.timestamp, spot)
    lineas.append(f"📅 {dia} · {hora} hs")
    lineas.append("")
    lineas.append(SEPARADOR)

    lineas.append("*CONDICIONES*")
    s = hour.swell
    w = hour.wind
    t = hour.tide
    lineas.append(f"🌊  Swell:   `{s.altura_m:.1f}m · {s.periodo_s:.0f}s · {_dir_a_texto(s.direccion_deg)}`")
    lineas.append(f"💨  Viento:  `{w.velocidad_kmh:.0f} km/h · {_dir_a_texto(w.direccion_deg)}`")
    if w.rafaga_kmh > w.velocidad_kmh * 1.3:
        lineas.append(f"       ↪ Ráfagas: `{w.rafaga_kmh:.0f} km/h`")
    if hour.temp_agua_c is not None:
        lineas.append(f"🌡️  Agua:    `{hour.temp_agua_c:.1f}°C`")

    # Marea: tendencia + horarios de alta/baja del día
    lineas.append(_formato_marea_inline(t, tide_analysis, spot))
    if tide_analysis is not None and tide_analysis.tiene_extremos_claros and tide_analysis.eventos:
        tz = _tz(spot)
        partes = []
        for ev in tide_analysis.eventos[:4]:
            hora_ev = ev.timestamp.astimezone(tz).strftime("%H:%M")
            simbolo = "▲" if ev.tipo == "alta" else "▽"
            partes.append(f"{simbolo}{hora_ev}")
        lineas.append(f"       ↪ `{'  '.join(partes)}`")
    lineas.append("")
    lineas.append(SEPARADOR)

    lineas.append("*CALIDAD*")
    lineas.append(f"{_estrellas(breakdown.score_total)}  *({breakdown.score_100}/100) · {breakdown.etiqueta}*")
    lineas.append("")

    for f_pos in breakdown.flags_positivos:
        lineas.append(f"✅ {f_pos}")
    for f_neg in breakdown.flags_negativos:
        lineas.append(f"❌ {f_neg}")
    for f_neu in breakdown.flags_neutros:
        # Omitir el flag técnico de proxy MSL — ya está en el inline de marea
        if "proxy MSL" not in f_neu and "proxy_msl" not in f_neu.lower():
            lineas.append(f"ℹ️  {f_neu}")

    return "\n".join(lineas)


def formato_ventanas(
    ventanas: List[VentanaOptima],
    spot: SpotConfig,
    forecast: List[ForecastHour] = None,
) -> str:
    """Mensaje con las mejores ventanas de surf de las próximas 48h."""
    lineas = []
    lineas.append(f"⏱ *Ventanas óptimas · {spot.nombre}*")
    lineas.append(f"📍 {spot.ciudad} — próximas 48h")
    lineas.append("")
    lineas.append(SEPARADOR)

    if not ventanas:
        lineas.append("😔 No se encontraron ventanas buenas en las próximas 48h.")
        lineas.append("Intentá consultar de nuevo mañana o revisá otro spot.")
        return "\n".join(lineas)

    for i, v in enumerate(ventanas, 1):
        from datetime import timedelta
        hora_ini = _ts_local(v.inicio, spot)
        fin_real = v.fin if v.fin > v.inicio else v.inicio + timedelta(hours=1)
        hora_fin = _ts_local(fin_real, spot)
        dia = _dia_relativo(v.inicio, spot)
        emoji = _emoji_score(v.score_100)
        lineas.append(f"{emoji} *{dia} {hora_ini}–{hora_fin}*")
        lineas.append(f"   {_estrellas(v.score_promedio)} ({v.score_100}/100)")
        lineas.append(f"   📝 {v.descripcion}")
        lineas.append(f"   ⏱ Pico: {_ts_local(v.hora_pico, spot)} hs · {max(v.horas_count, 1)}h de buenas condiciones")
        if i < len(ventanas):
            lineas.append("")

    return "\n".join(lineas)


def formato_breakdown_pro(
    hour: ForecastHour,
    breakdown: ScoreBreakdown,
    spot: SpotConfig,
) -> str:
    """Breakdown detallado de sub-scores."""
    lineas = []
    lineas.append(f"🔬 *Breakdown técnico · {spot.nombre}*")
    lineas.append(SEPARADOR)
    lineas.append(f"⚡ Energía:   `{_barra_score(breakdown.score_energia)}` {int(breakdown.score_energia*100):3d}/100")
    lineas.append(f"   ↪ H²×T = `{breakdown.energia_proxy:.1f}`")
    lineas.append(f"⏱ Período:   `{_barra_score(breakdown.score_periodo)}` {int(breakdown.score_periodo*100):3d}/100")
    lineas.append(f"🧭 Dirección: `{_barra_score(breakdown.score_dir_swell)}` {int(breakdown.score_dir_swell*100):3d}/100")
    lineas.append(f"💨 Viento:    `{_barra_score(breakdown.score_viento)}` {int(breakdown.score_viento*100):3d}/100")
    lineas.append(f"🌊 Marea:     `{_barra_score(breakdown.score_marea)}` {int(breakdown.score_marea*100):3d}/100")
    lineas.append("")
    lineas.append(f"*TOTAL: {breakdown.score_100}/100 · {breakdown.etiqueta}*")
    lineas.append("")
    lineas.append(f"🏄 Break: `{spot.tipo_break}` · Fondo: `{spot.fondo}`")
    return "\n".join(lineas)


def formato_lista_ventanas_corta(ventanas: List[VentanaOptima], spot: SpotConfig = None) -> str:
    """Versión compacta de ventanas para incluir al final de condiciones.
    Filtra ventanas donde inicio == fin (datos rotos del detector).
    Si todas son de 1h, toma la mejor y muestra fin = inicio + 1h."""
    from datetime import timedelta

    if not ventanas:
        return "Sin ventanas buenas en las próximas horas."

    # Preferir ventanas con rango real (fin > inicio)
    con_rango = [v for v in ventanas if v.fin > v.inicio]
    v = con_rango[0] if con_rango else ventanas[0]

    if spot:
        ini_str = _ts_local(v.inicio, spot)
        fin_real = v.fin if v.fin > v.inicio else v.inicio + timedelta(hours=1)
        fin_str = _ts_local(fin_real, spot)
        dia = _dia_relativo(v.inicio, spot)
    else:
        ini_str = v.inicio.strftime("%H:%M")
        fin_real = v.fin if v.fin > v.inicio else v.inicio + timedelta(hours=1)
        fin_str = fin_real.strftime("%H:%M")
        dia = "Próxima"

    horas = max(v.horas_count, 1)
    return (
        f"⏰ Mejor ventana: *{dia} {ini_str}–{fin_str}*  _({horas}h)_\n"
        f"   📝 {v.descripcion}"
    )


def formato_no_disponible(spot: SpotConfig, error: str = "") -> str:
    """Mensaje cuando no se pueden obtener datos."""
    msg = f"⚠️ No pude obtener el pronóstico para *{spot.nombre}*."
    if error:
        msg += f"\n_Error: {error}_"
    msg += "\nIntentá de nuevo en unos minutos."
    return msg


# ---------------------------------------------------------------------------
# Funciones NUEVAS V2
# ---------------------------------------------------------------------------

def formato_luz_solar(daylight: DaylightInfo, spot: SpotConfig) -> str:
    """
    Línea compacta con amanecer, atardecer y horas de luz.
    Ejemplo: 🌅 06:42  🌇 20:15  (13.5h de luz)
    """
    sr = daylight.sunrise_local.strftime("%H:%M")
    ss = daylight.sunset_local.strftime("%H:%M")
    return f"🌅 {sr}  🌇 {ss}  _({daylight.duration_h:.1f}h de luz)_"


def formato_mareas(
    analysis: TideAnalysis,
    spot: SpotConfig,
    max_eventos: int = 4,
) -> str:
    """
    Sección de mareas para incluir en el mensaje del día.

    Muestra eventos alta/baja con horario local.
    Si no hay extremos claros, muestra solo la tendencia.
    Siempre aclara que es marea estimada (proxy MSL).
    """
    lineas = []
    lineas.append(SEPARADOR)
    lineas.append("*MAREAS* _(estimadas, proxy MSL)_")

    tz = _tz(spot)

    if not analysis.tiene_extremos_claros:
        # Fallback: solo tendencia
        flecha = {"subiendo": "↑", "bajando": "↓", "estable": "→"}.get(
            analysis.tendencia_actual, "~"
        )
        lineas.append(f"🌊 Tendencia: *{analysis.tendencia_actual}* {flecha}")
        if analysis.proximo_cambio:
            hora_cambio = analysis.proximo_cambio.astimezone(tz).strftime("%H:%M")
            lineas.append(f"   Próximo cambio estimado: ~{hora_cambio} hs")
        lineas.append("_ℹ️ No se detectaron extremos claros en el período_")
        return "\n".join(lineas)

    # Mostrar eventos ordenados
    eventos = analysis.eventos[:max_eventos]
    for evento in eventos:
        hora_local = evento.timestamp.astimezone(tz).strftime("%H:%M")
        simbolo = "▲" if evento.tipo == "alta" else "▼"
        tipo_fmt = "*Alta*" if evento.tipo == "alta" else "Baja"
        lineas.append(f"{simbolo} {hora_local} hs — {tipo_fmt} `{evento.nivel_m:.2f}m`")

    # Tendencia actual como info adicional
    if analysis.nivel_actual is not None:
        flecha = {"subiendo": "↑", "bajando": "↓", "estable": "→"}.get(
            analysis.tendencia_actual, ""
        )
        lineas.append(f"_Ahora: {analysis.nivel_actual:.2f}m {flecha}_")

    return "\n".join(lineas)


def formato_mejor_hora(
    result: BestHourResult,
    spot: SpotConfig,
) -> str:
    """
    Sección 'Mejor hora del día' para incluir en mensajes de fecha.

    Ejemplo:
      ──────────────────────
      MEJOR HORA
      ⭐⭐⭐⭐ 10:00 hs  (82/100 · Excelente)
      🌊 1.8m · 13s · SSE  💨 12 km/h offshore
    """
    lineas = []
    lineas.append(SEPARADOR)
    lineas.append("*MEJOR HORA DEL DÍA*")

    h = result.hour
    hora_local = _ts_local(h.timestamp, spot)
    estrellas = _estrellas(result.breakdown.score_total)

    lineas.append(f"{estrellas} *{hora_local} hs*  _({result.score_100}/100 · {result.breakdown.etiqueta})_")
    lineas.append(
        f"🌊 `{h.swell.altura_m:.1f}m · {h.swell.periodo_s:.0f}s · {_dir_a_texto(h.swell.direccion_deg)}`"
        f"  💨 `{h.wind.velocidad_kmh:.0f} km/h {_dir_a_texto(h.wind.direccion_deg)}`"
    )

    # Info de luz: cuántas horas se evaluaron
    sr = result.daylight.sunrise_local.strftime("%H:%M")
    ss = result.daylight.sunset_local.strftime("%H:%M")
    lineas.append(f"_Evaluadas {result.horas_evaluadas}h de luz ({sr}–{ss})_")

    return "\n".join(lineas)


def formato_vista_horaria(
    view: HourlyViewResult,
    spot: SpotConfig,
) -> str:
    """
    Vista hora a hora compacta para un día.

    Formato de cada fila:
      ☀️ 09:00  ⭐⭐⭐·· 58  1.5m/12s  15km/h S
      🌟 10:00  ⭐⭐⭐⭐· 72  1.8m/13s  12km/h S  ← mejor hora
      ☀️ 11:00  ⭐⭐⭐·· 61  1.7m/12s  14km/h SO
    """
    tz = _tz(spot)
    lineas = []

    dia = _dia_relativo(view.filas[0].hour.timestamp, spot) if view.filas else "?"
    sr = view.daylight.sunrise_local.strftime("%H:%M")
    ss = view.daylight.sunset_local.strftime("%H:%M")

    lineas.append(f"📊 *Vista hora a hora · {spot.nombre}*")
    lineas.append(f"📅 {dia}  {formato_luz_solar(view.daylight, spot)}")
    lineas.append("")
    lineas.append("`hora  score  swell        viento`")
    lineas.append(SEPARADOR)

    for fila in view.filas:
        hora_str = fila.hour.timestamp.astimezone(tz).strftime("%H:%M")
        score_str = f"{fila.breakdown.score_100:3d}"
        swell_str = f"{fila.hour.swell.altura_m:.1f}m/{fila.hour.swell.periodo_s:.0f}s"
        viento_str = f"{fila.hour.wind.velocidad_kmh:.0f}km/h {_dir_a_texto(fila.hour.wind.direccion_deg)}"

        if fila.es_mejor:
            icono = "🌟"
            linea = f"{icono} `{hora_str}  {score_str}  {swell_str:<8}  {viento_str}` ◀"
        elif fila.es_dia:
            icono = "☀️"
            linea = f"{icono} `{hora_str}  {score_str}  {swell_str:<8}  {viento_str}`"
        else:
            icono = "🌙"
            linea = f"{icono} `{hora_str}  {score_str}  {swell_str:<8}  {viento_str}`"

        lineas.append(linea)

    return "\n".join(lineas)


def formato_semana(
    analysis: WeeklyAnalysis,
    spot: SpotConfig,
) -> str:
    """
    Vista semanal: mejor día + ranking de los próximos 7 días.

    Ejemplo:
      📅 Semana · Varese
      ──────────────────────
      🏆 MEJOR DÍA: Miércoles 22/01
         ⭐⭐⭐⭐ (74/100) · Excelente
         ⏰ Mejor hora: 10:00 hs

      RANKING SEMANAL
      🟢 Mié 22  ████████░░  74  ← mejor
      🟡 Jue 23  ██████░░░░  58
      🟡 Vie 24  █████░░░░░  52
      🔴 Sáb 25  ████░░░░░░  41
      🔴 Dom 26  ███░░░░░░░  32
    """
    lineas = []
    lineas.append(f"📅 *Semana · {spot.nombre}*")
    lineas.append(f"📍 {spot.ciudad}")
    lineas.append("")
    lineas.append(SEPARADOR)

    # Mejor día destacado
    md = analysis.mejor_dia
    lineas.append(f"🏆 *MEJOR DÍA: {md.nombre_dia} {md.fecha.strftime('%d/%m')}*")
    lineas.append(f"   {_estrellas(md.score_promedio)} ({md.score_100}/100) · _{_etiqueta(md.score_100)}_")
    if md.mejor_hora:
        hora_pico = _ts_local(md.mejor_hora.hour.timestamp, spot)
        lineas.append(f"   ⏰ Mejor hora: *{hora_pico} hs*")
    lineas.append("")

    # Ranking semanal
    lineas.append("*RANKING SEMANAL*")
    for d in analysis.scores_por_dia:
        if not d.tiene_datos:
            lineas.append(f"⚫ {d.nombre_corto} {d.fecha.strftime('%d/%m')}  _sin datos_")
            continue

        emoji = _emoji_score(d.score_100)
        barra = _barra_score(d.score_promedio, ancho=8)
        es_mejor = " ◀" if d.fecha == md.fecha else ""
        lineas.append(f"{emoji} `{d.nombre_corto} {d.fecha.strftime('%d/%m')}  {barra}  {d.score_100:3d}`{es_mejor}")

    # Días buenos
    if analysis.hay_dias_buenos:
        nombres = ", ".join(d.nombre_corto for d in analysis.dias_buenos)
        lineas.append("")
        lineas.append(f"✅ _Días recomendados: {nombres}_")
    else:
        lineas.append("")
        lineas.append("_😔 No hay días con condiciones buenas esta semana._")

    return "\n".join(lineas)


def formato_dia_completo(
    hour: ForecastHour,
    breakdown: ScoreBreakdown,
    spot: SpotConfig,
    daylight: DaylightInfo,
    tide_analysis: TideAnalysis,
    mejor_hora: Optional[BestHourResult],
    es_hoy: bool = False,
) -> str:
    """
    Mensaje para un día específico consultado por fecha.

    Si es_hoy=True: muestra condiciones actuales + mejor hora al final.
    Si es_hoy=False: muestra directamente la mejor hora del día como protagonista,
                     con condiciones completas de esa hora.
    """
    lineas = []

    # Usar mejor_hora como fuente principal si existe y no es hoy
    h_display = hour
    bd_display = breakdown
    if not es_hoy and mejor_hora is not None:
        h_display = mejor_hora.hour
        bd_display = mejor_hora.breakdown

    tz_spot = _tz(spot)
    fecha_display = h_display.timestamp.astimezone(tz_spot)
    dia = _dia_relativo(h_display.timestamp, spot)
    fecha_str = fecha_display.strftime("%d/%m")

    # Header
    lineas.append(f"🌊 *{spot.ciudad} · {spot.nombre}*")
    lineas.append(f"📅 {dia} {fecha_str}")
    lineas.append("")

    # Luz solar
    lineas.append(formato_luz_solar(daylight, spot))
    lineas.append("")
    lineas.append(SEPARADOR)

    if es_hoy:
        lineas.append("*CONDICIONES AHORA*")
    else:
        mejor_hora_str = _ts_local(h_display.timestamp, spot)
        lineas.append(f"*MEJOR HORA DEL DÍA · {mejor_hora_str} hs*")
        lineas.append(f"{_estrellas(bd_display.score_total)}  *({bd_display.score_100}/100) · {bd_display.etiqueta}*")
        lineas.append("")

    s = h_display.swell
    w = h_display.wind
    t = h_display.tide
    lineas.append(f"🌊  Swell:   `{s.altura_m:.1f}m · {s.periodo_s:.0f}s · {_dir_a_texto(s.direccion_deg)}`")
    lineas.append(f"💨  Viento:  `{w.velocidad_kmh:.0f} km/h · {_dir_a_texto(w.direccion_deg)}`")
    if w.rafaga_kmh > w.velocidad_kmh * 1.3:
        lineas.append(f"       ↪ Ráfagas: `{w.rafaga_kmh:.0f} km/h`")
    if h_display.temp_agua_c is not None:
        lineas.append(f"🌡️  Agua:    `{h_display.temp_agua_c:.1f}°C`")
    lineas.append(_formato_marea_inline(t, tide_analysis, spot))
    if tide_analysis is not None and tide_analysis.tiene_extremos_claros and tide_analysis.eventos:
        partes = []
        for ev in tide_analysis.eventos[:4]:
            hora_ev = ev.timestamp.astimezone(tz_spot).strftime("%H:%M")
            simbolo = "▲" if ev.tipo == "alta" else "▽"
            partes.append(f"{simbolo}{hora_ev}")
        lineas.append(f"       ↪ `{'  '.join(partes)}`")
    lineas.append("")
    lineas.append(SEPARADOR)

    # Score + flags
    if es_hoy:
        lineas.append("*CALIDAD*")
        lineas.append(f"{_estrellas(bd_display.score_total)}  *({bd_display.score_100}/100) · {bd_display.etiqueta}*")
        lineas.append("")
    for f_pos in bd_display.flags_positivos:
        lineas.append(f"✅ {f_pos}")
    for f_neg in bd_display.flags_negativos:
        lineas.append(f"❌ {f_neg}")
    for f_neu in bd_display.flags_neutros:
        if "proxy MSL" not in f_neu and "proxy_msl" not in f_neu.lower():
            lineas.append(f"ℹ️  {f_neu}")

    # Mareas del día
    if tide_analysis:
        lineas.append("")
        lineas.append(formato_mareas(tide_analysis, spot))

    # Si es hoy, mostrar mejor hora al final como bonus
    if es_hoy and mejor_hora is not None:
        lineas.append("")
        lineas.append(formato_mejor_hora(mejor_hora, spot))

    return "\n".join(lineas)


# ---------------------------------------------------------------------------
# Helper interno
# ---------------------------------------------------------------------------

def formato_proximas_olas(
    forecast: list,
    ventanas: list,
    spot: SpotConfig,
) -> str:
    """
    Muestra las próximas ventanas surfeables como bloques concretos.
    Si no hay ventanas, indica cuándo es la próxima oportunidad.
    """
    from datetime import timezone as _tz_mod
    from core.scoring.engine import calcular_score
    from core.analysis.daylight import get_daylight_for_forecast_hour, is_daylight

    tz = _tz(spot)
    now = datetime.now(_tz_mod.utc)
    lineas = []

    lineas.append(f"🏄 *Próximas olas · {spot.nombre}*")
    lineas.append(f"📍 {spot.ciudad}")

    if not ventanas:
        lineas.append("")
        lineas.append("_No hay ventanas buenas en las próximas 48hs._")

        # Buscar próxima hora decente más allá de las 48h
        proxima = None
        for h in forecast:
            if h.timestamp <= now:
                continue
            if (h.timestamp - now).total_seconds() / 3600 <= 48:
                continue
            try:
                daylight = get_daylight_for_forecast_hour(spot, h.timestamp)
                if not is_daylight(h.timestamp, daylight):
                    continue
                bd = calcular_score(h, spot)
                if bd.score_100 >= 55:
                    proxima = (h, bd)
                    break
            except Exception:
                pass

        if proxima:
            h, bd = proxima
            dia = _dia_relativo(h.timestamp, spot)
            hora_str = _ts_local(h.timestamp, spot)
            lineas.append(f"💡 _Próxima oportunidad: {dia} {hora_str} hs ({bd.score_100}/100)_")
        else:
            lineas.append("_😔 Sin condiciones buenas en los próximos 7 días._")

        return "\n".join(lineas)

    # Ordenar ventanas cronológicamente para mostrarlas
    ventanas_orden = sorted(ventanas, key=lambda v: v.inicio)

    for v in ventanas_orden:
        lineas.append("")
        lineas.append(SEPARADOR)

        # Encabezado: día + horario
        dia = _dia_relativo(v.inicio, spot)
        fecha_str = v.inicio.astimezone(tz).strftime("%d/%m")
        inicio_str = v.inicio.astimezone(tz).strftime("%H:%M")
        fin_str = v.fin.astimezone(tz).strftime("%H:%M")
        score = round(v.score_promedio * 100)
        emoji = _emoji_score(score)

        duracion_h = round((v.fin - v.inicio).total_seconds() / 3600)
        duracion_str = f"{duracion_h}h" if duracion_h > 1 else "~1h"

        lineas.append(f"{emoji} *{dia} {fecha_str} · {inicio_str}–{fin_str}* ({duracion_str})")
        lineas.append(f"📊 Score: *{score}/100*")

        # Datos de la hora pico
        hora_pico = next(
            (h for h in forecast if h.timestamp == v.hora_pico), None
        )
        if hora_pico:
            swell_str = f"{hora_pico.swell.altura_m:.1f}m / {hora_pico.swell.periodo_s:.0f}s"
            viento_str = f"{hora_pico.wind.velocidad_kmh:.0f} km/h {_dir_a_texto(hora_pico.wind.direccion_deg)}"
            lineas.append(f"🌊 Swell: {swell_str}")
            lineas.append(f"💨 Viento: {viento_str}")

        # Descripción del detector (offshore, groundswell, etc.)
        if v.descripcion:
            lineas.append(f"_{v.descripcion}_")

    lineas.append("")
    return "\n".join(lineas)


def _formato_marea_inline(tide_data, tide_analysis, spot: SpotConfig) -> str:
    """
    Línea de marea legible para el bloque CONDICIONES.

    Sin tide_analysis: muestra solo tendencia básica.
    Con tide_analysis: muestra tendencia + próximo evento (alta/baja).

    Ejemplos:
      🌊  Marea:   subiendo ↑ · próxima alta ~14:30 hs
      🌊  Marea:   bajando ↓  · próxima baja ~17:00 hs
      🌊  Marea:   estable →
    """
    from core.analysis.tides import TideAnalysis

    flechas = {"subiendo": "↑", "bajando": "↓", "estable": "→"}

    if tide_analysis is None or not isinstance(tide_analysis, TideAnalysis):
        # Sin análisis — mostrar solo tendencia estimada por el nivel
        return "🌊  Marea:   _sin datos de tendencia_"

    tendencia = tide_analysis.tendencia_actual
    flecha = flechas.get(tendencia, "")
    tz = _tz(spot)

    # Buscar el próximo evento coherente con la tendencia:
    #   subiendo → próximo evento esperado es ALTA
    #   bajando  → próximo evento esperado es BAJA
    #   estable  → cualquier evento próximo
    proximo = None
    if tide_analysis.tiene_extremos_claros and tide_analysis.eventos:
        import datetime as _dt
        ahora = _dt.datetime.now(_dt.timezone.utc)
        futuros = [e for e in tide_analysis.eventos if e.timestamp > ahora]
        if futuros:
            if tendencia == "subiendo":
                # buscamos la próxima alta
                proximo = next((e for e in futuros if e.tipo == "alta"), futuros[0])
            elif tendencia == "bajando":
                # buscamos la próxima baja
                proximo = next((e for e in futuros if e.tipo == "baja"), futuros[0])
            else:
                proximo = futuros[0]

    if proximo:
        hora_evento = proximo.timestamp.astimezone(tz).strftime("%H:%M")
        tipo = "alta" if proximo.tipo == "alta" else "baja"
        # Solo mostrar si tiene sentido con la tendencia actual
        # subiendo → próxima alta | bajando → próxima baja
        coherente = (
            (tendencia == "subiendo" and proximo.tipo == "alta") or
            (tendencia == "bajando" and proximo.tipo == "baja") or
            tendencia == "estable"
        )
        if coherente:
            return f"🌊  Marea:   `{tendencia} {flecha}` · próxima {tipo} ~{hora_evento} hs"
        else:
            # Buscar el siguiente evento coherente
            if tide_analysis.tiene_extremos_claros and tide_analysis.eventos:
                from datetime import timezone as _tz_mod
                ahora = __import__('datetime').datetime.now(_tz_mod.utc)
                tipo_buscado = "alta" if tendencia == "subiendo" else "baja"
                coherentes = [e for e in tide_analysis.eventos if e.timestamp > ahora and e.tipo == tipo_buscado]
                if coherentes:
                    hora_evento = coherentes[0].timestamp.astimezone(tz).strftime("%H:%M")
                    return f"🌊  Marea:   `{tendencia} {flecha}` · próxima {tipo_buscado} ~{hora_evento} hs"
            return f"🌊  Marea:   `{tendencia} {flecha}`"
    elif tide_analysis.proximo_cambio:
        hora_cambio = tide_analysis.proximo_cambio.astimezone(tz).strftime("%H:%M")
        return f"🌊  Marea:   `{tendencia} {flecha}` · cambia ~{hora_cambio} hs"
    else:
        return f"🌊  Marea:   `{tendencia} {flecha}`"


def _etiqueta(score_100: int) -> str:
    if score_100 >= 85: return "Épico"
    if score_100 >= 70: return "Excelente"
    if score_100 >= 55: return "Bueno"
    if score_100 >= 40: return "Regular"
    if score_100 >= 25: return "Pobre"
    return "Flat / No apto"
