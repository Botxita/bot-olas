"""Handlers principales del bot de Telegram — V2.

Flujo de conversación:
  /start → selección país → región → spot → menú spot → acción

Acciones disponibles (todas sin restricción de plan):
  ahora     → condiciones actuales + ventanas + mejor hora
  fecha     → selección de fecha (7 días) → día completo
  ventanas  → ventanas óptimas 48h
  horaria   → vista hora a hora (hoy por defecto)
  semana    → ranking de los próximos 7 días
  breakdown → breakdown técnico de sub-scores

Regla de arquitectura:
  - Cero lógica de negocio en handlers.
  - Todo el cálculo en core/.
  - Todo el texto en bot/formatters.py.
  - Handlers solo orquestan: obtienen datos → llaman core → llaman formatter → envían.
"""

import logging
import os
from datetime import date, datetime, timezone

from telegram import Update, ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    CallbackContext,
    CallbackQueryHandler,
    CommandHandler,
    Dispatcher,
    MessageHandler,
    Filters,
)
from telegram.utils.helpers import escape_markdown

from core.forecast import get_provider
from core.forecast.cache import forecast_cache
from core.spots.registry import (
    get_spot,
    listar_paises,
    listar_regiones,
    listar_spots_region,
    actualizar_ajuste,
)
from core.windows.detector import detectar_ventanas, calcular_score_actual
from core.analysis.daylight import get_daylight
from core.analysis.tides import detectar_mareas_del_dia
from core.analysis.best_hour import calcular_mejor_hora
from core.analysis.hourly_view import generar_vista_horaria
from core.analysis.weekly import analizar_semana
from persistence.session_store import session_store

from bot.formatters import (
    formato_condiciones_actuales,
    formato_ventanas,
    formato_breakdown_pro,
    formato_lista_ventanas_corta,
    formato_no_disponible,
    formato_dia_completo,
    formato_vista_horaria,
    formato_semana,
    formato_proximas_olas,
)
from bot.keyboards import (
    kb_seleccion_pais,
    kb_seleccion_region,
    kb_seleccion_spot,
    kb_menu_spot,
    kb_post_forecast,
    kb_seleccion_fecha,
    kb_favoritos,
    kb_nivel,
    kb_volver,
)

logger = logging.getLogger(__name__)
SEPARADOR_MSG = "─" * 22

# Umbrales de recomendación por nivel de surf (#A2). El score 0-100 en sí
# no cambia con el nivel — solo qué tan exigente es el bot para marcar
# algo como "vale la pena": un principiante necesita condiciones más
# prolijas para que se arme una ventana óptima o cuente como día bueno.
UMBRAL_POR_NIVEL = {
    "principiante": {"ventana": 0.70, "dia_bueno": 65},
    "intermedio":   {"ventana": 0.60, "dia_bueno": 55},
    "avanzado":     {"ventana": 0.50, "dia_bueno": 45},
}

NOMBRE_NIVEL = {
    "principiante": "Principiante 🐣",
    "intermedio": "Intermedio 🏄",
    "avanzado": "Avanzado 🔥",
}


# ------------------------------------------------------------------
# Safe edit (evita crash "Message is not modified")
# ------------------------------------------------------------------

def _safe_edit(query, text, **kwargs):
    try:
        query.message.edit_text(text, **kwargs)
    except BadRequest as e:
        if "Message is not modified" in str(e):
            return
        raise


# ------------------------------------------------------------------
# Helpers internos
# ------------------------------------------------------------------

def _es_admin(user_id: int) -> bool:
    """True si el user_id está en ADMIN_USER_IDS (solo para /ajuste)."""
    admins = os.getenv("ADMIN_USER_IDS", "")
    return str(user_id) in [a.strip() for a in admins.split(",") if a.strip()]


def _get_forecast_cached(spot_key: str):
    """Obtiene forecast con caché. Lanza excepción si falla."""
    cached = forecast_cache.get(spot_key)
    if cached is not None:
        return cached
    spot = get_spot(spot_key)
    provider = get_provider(spot.fuente_datos)
    forecast = provider.get_forecast_48h(spot)
    forecast_cache.set(spot_key, forecast)
    return forecast


def _fecha_local_hoy(spot) -> date:
    """Retorna la fecha actual en la timezone del spot."""
    return datetime.now(spot.get_zoneinfo()).date()


def _resolver_nombres_favoritos(favs_keys: list) -> list:
    """Convierte lista de spot_keys en lista de (spot_key, nombre) para el teclado."""
    resultado = []
    for key in favs_keys:
        try:
            spot = get_spot(key)
            nombre = spot.nombre
            if spot.ciudad and spot.ciudad != spot.nombre:
                nombre = f"{spot.ciudad} · {spot.nombre}"
            resultado.append((key, nombre))
        except KeyError:
            pass  # spot eliminado del registry, ignorar silenciosamente
    return resultado


# ------------------------------------------------------------------
# /start
# ------------------------------------------------------------------

def _texto_menu_principal(user_id: int, first_name: str):
    """Texto + teclado del menú de selección de país (pantalla de inicio).

    first_name debe venir ya escapado con escape_markdown(version=2) —
    esta función lo interpola directo en texto MarkdownV2.
    """
    paises = listar_paises()
    favs_keys = session_store.get_favoritos(user_id)
    favs = _resolver_nombres_favoritos(favs_keys)

    if favs:
        texto = (
            f"🌊 Hola *{first_name}*\\! Bienvenido al *Olas Surfer Bot*\\.\n\n"
            "¿Dónde vas a surfear hoy? 🏄‍♂️🌍"
        )
    else:
        texto = (
            f"🌊 Hola *{first_name}*\\! Bienvenido al *Olas Surfer Bot*\\.\n\n"
            "Consultá condiciones, ventanas, mareas y el mejor día de la semana "
            "para cualquier spot de Latinoamérica\\.\n\n"
            "¿Dónde vas a surfear? 🏄‍♂️🌍"
        )
    return texto, kb_seleccion_pais(paises, favoritos=favs)


def handle_start(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    # Escapado porque va interpolado en texto MarkdownV2 más abajo — un
    # first_name real con "_", "*", "(", etc. (ej. "Ivan_Test") rompía el
    # parseo con BadRequest si no se escapaba (encontrado en revisión #A2).
    first_name = escape_markdown(update.effective_user.first_name or "surfer", version=2)

    # Onboarding de nivel de surf (#A2): primer uso real, sin nivel guardado
    # todavía. Gate único — una vez elegido, /start nunca vuelve a mostrarlo.
    if not session_store.tiene_nivel(user_id):
        update.message.reply_text(
            f"🌊 Hola *{first_name}*\\! Bienvenido al *Olas Surfer Bot*\\.\n\n"
            "Antes de arrancar, contame tu nivel de surf: así el bot ajusta "
            "qué tan exigente es para avisarte que vale la pena salir\\. "
            "Lo podés cambiar cuando quieras con /nivel\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=kb_nivel(),
        )
        return

    session_store.update_session(user_id, step="seleccion_pais")
    texto, keyboard = _texto_menu_principal(user_id, first_name)
    update.message.reply_text(
        texto,
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=keyboard,
    )


# ------------------------------------------------------------------
# /nivel — cambiar el nivel de surf en cualquier momento
# ------------------------------------------------------------------

def handle_nivel(update: Update, context: CallbackContext):
    update.message.reply_text(
        "🏄 ¿Cuál es tu nivel de surf?",
        reply_markup=kb_nivel(),
    )


# ------------------------------------------------------------------
# Texto libre ("hola", "buen día", etc.)
# ------------------------------------------------------------------

def handle_text(update: Update, context: CallbackContext):
    """Cualquier mensaje de texto dispara el flujo inicial."""
    handle_start(update, context)


# ------------------------------------------------------------------
# /ajuste (solo admins)
# ------------------------------------------------------------------

def handle_ajuste(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if not _es_admin(user_id):
        update.message.reply_text("⛔ Este comando es solo para administradores.")
        return

    args = context.args
    if len(args) != 3:
        update.message.reply_text(
            "Uso: /ajuste <spot_key> <param> <valor>\n"
            "Parámetros válidos: delta_altura, delta_marea, factor_periodo\n"
            "Ejemplo: /ajuste mdq_varese delta_altura 0.2"
        )
        return

    spot_key, param, valor_str = args
    try:
        valor = float(valor_str)
    except ValueError:
        update.message.reply_text("El valor debe ser numérico.")
        return

    try:
        actualizar_ajuste(spot_key, param, valor)
        session_store.set_spot_adjustment(spot_key, param, valor)
        forecast_cache.invalidate(spot_key)
        update.message.reply_text(
            f"✅ Ajuste aplicado: `{spot_key}` · {param} = {valor}\n"
            "El caché del spot fue invalidado.",
            parse_mode=ParseMode.MARKDOWN,
        )
    except (KeyError, ValueError) as e:
        update.message.reply_text(f"❌ Error: {e}")


# ------------------------------------------------------------------
# Router principal de callbacks inline
# ------------------------------------------------------------------

def handle_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    try:
        query.answer()
    except BadRequest:
        pass  # Query expirada

    user_id = query.from_user.id
    data = query.data or ""

    if data == "noop":
        return  # Botones decorativos

    if data.startswith("spot_from_home:"):
        # Spot abierto desde el contexto de favoritos (home o pantalla
        # "Mis favoritos") — el país/región del spot se derivan de
        # spot.pais/spot.region dentro de _cb_spot, no de la sesión.
        spot_key = data[15:]
        _cb_spot(query, user_id, spot_key, desde_favoritos=True)

    elif data.startswith("pais:"):
        _cb_pais(query, user_id, data[5:])

    elif data.startswith("region:"):
        parts = data[7:].split(":", 1)
        if len(parts) == 2:
            _cb_region(query, user_id, parts[0], parts[1])

    elif data.startswith("spot:"):
        _cb_spot(query, user_id, data[5:])

    elif data.startswith("action:"):
        # action:<accion>:<spot_key>:<origen>
        parts = data[7:].split(":")
        accion = parts[0]
        spot_key = parts[1] if len(parts) > 1 else ""
        desde_favoritos = len(parts) > 2 and parts[2] == "fav"
        _cb_action(query, user_id, accion, spot_key, desde_favoritos=desde_favoritos)

    elif data.startswith("fecha:"):
        # fecha:<spot_key>:<YYYY-MM-DD>:<origen>
        parts = data[6:].split(":")
        if len(parts) >= 2:
            spot_key, fecha_iso = parts[0], parts[1]
            desde_favoritos = len(parts) > 2 and parts[2] == "fav"
            try:
                fecha = date.fromisoformat(fecha_iso)
                _mostrar_dia(query, spot_key, fecha, desde_favoritos=desde_favoritos)
            except ValueError:
                # Ídem #A3.1
                origen_cb = "fav" if desde_favoritos else ""
                _safe_edit(query, "❌ Fecha inválida.",
                    reply_markup=kb_volver(f"back:spot:{spot_key}:{origen_cb}"))
        else:
            # Sin fecha → mostrar selector
            _cb_mostrar_selector_fecha(query, data[6:])

    elif data.startswith("horaria:"):
        # horaria:<spot_key>:<fecha_iso>:<origen> — hora a hora de un día
        # específico (#A3.3), distinto de action:horaria: que siempre usa
        # hoy. Lo genera kb_post_forecast(fecha_horaria=...) desde la
        # vista de un día por fecha.
        parts = data[8:].split(":")
        if len(parts) >= 2:
            spot_key, fecha_iso = parts[0], parts[1]
            desde_favoritos = len(parts) > 2 and parts[2] == "fav"
            try:
                fecha = date.fromisoformat(fecha_iso)
                spot = get_spot(spot_key)
                _mostrar_horaria(query, spot_key, spot, fecha, desde_favoritos=desde_favoritos)
            except ValueError:
                origen_cb = "fav" if desde_favoritos else ""
                _safe_edit(query, "❌ Fecha inválida.",
                    reply_markup=kb_volver(f"back:spot:{spot_key}:{origen_cb}"))
            except KeyError:
                destino = "back:favoritos" if desde_favoritos else "back:paises"
                _safe_edit(query, "❌ Spot no encontrado.", reply_markup=kb_volver(destino))

    elif data.startswith("back:"):
        _cb_back(query, user_id, data[5:])

    elif data.startswith("fav_add:"):
        parts = data[8:].split(":")
        desde_favoritos = len(parts) > 1 and parts[1] == "fav"
        _cb_fav_add(query, user_id, parts[0], desde_favoritos=desde_favoritos)

    elif data.startswith("fav_del:"):
        parts = data[8:].split(":")
        desde_favoritos = len(parts) > 1 and parts[1] == "fav"
        _cb_fav_del(query, user_id, parts[0], desde_favoritos=desde_favoritos)

    elif data.startswith("nivel:"):
        _cb_nivel(query, user_id, data[6:])

    else:
        logger.warning("Callback desconocido: %s", data)


# ------------------------------------------------------------------
# Navegación: país → región → spot → menú
# ------------------------------------------------------------------

def _cb_pais(query, user_id: int, pais_key: str):
    session_store.update_session(user_id, step="seleccion_region")
    regiones = listar_regiones(pais_key)

    if not regiones:
        # Edita el mensaje activo en vez de mandar uno nuevo sin botones
        # (#A3.1) — antes esto dejaba 2 mensajes en pantalla: el viejo
        # (con botones desactualizados) y este, sin ninguna salida.
        _safe_edit(query,
            "No hay regiones configuradas para este país todavía.",
            reply_markup=kb_volver("back:paises"),
        )
        return

    _safe_edit(query,
        "📍 Seleccioná la región:",
        reply_markup=kb_seleccion_region(pais_key, regiones),
    )


def _cb_region(query, user_id: int, pais_key: str, region_key: str):
    session_store.update_session(user_id, step="seleccion_spot")
    spots = listar_spots_region(pais_key, region_key)

    if not spots:
        # Ídem #A3.1
        _safe_edit(query,
            "No hay spots configurados en esta región.",
            reply_markup=kb_volver(f"back:regiones:{pais_key}"),
        )
        return

    _safe_edit(query,
        "🏄 Elegí tu spot:",
        reply_markup=kb_seleccion_spot(pais_key, region_key, spots),
    )


def _cb_spot(query, user_id: int, spot_key: str, desde_favoritos: bool = False):
    try:
        spot = get_spot(spot_key)
    except KeyError:
        # Ídem #A3.1. Si venía de favoritos, "Volver" va a Mis favoritos
        # (de donde salió el spot ahora inexistente); si no, a países.
        destino = "back:favoritos" if desde_favoritos else "back:paises"
        _safe_edit(query, "❌ Spot no encontrado.", reply_markup=kb_volver(destino))
        return

    session_store.update_session(user_id, step="menu_spot", spot_key=spot_key)
    favs = session_store.get_favoritos(user_id)
    es_fav = spot_key in favs

    tipo_icons = {"reef": "🪸", "point": "🌀", "beach": "🏖️"}
    icon = tipo_icons.get(spot.tipo_break, "🏄")

    texto = (
        f"{icon} *{spot.nombre}*\n"
        f"📍 {spot.ciudad} · {spot.pais}\n"
        f"🏄 {spot.tipo_break.capitalize()} break — {spot.fondo}\n"
    )
    if spot.notas:
        texto += f"\n_{spot.notas}_"

    # País/región para el botón Volver: derivados del propio spot
    # (SpotConfig), no de la sesión — la sesión es estado global mutable
    # por usuario y podía pisarse entre mensajes/pantallas (#B1). Solo se
    # usan cuando NO viene de favoritos (ver kb_menu_spot).
    pais_key = spot.pais.lower()
    region_key = spot.region

    _safe_edit(query,
        texto,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_menu_spot(
            spot_key, es_favorito=es_fav, pais_key=pais_key, region_key=region_key,
            desde_favoritos=desde_favoritos,
        ),
    )


# ------------------------------------------------------------------
# Router de acciones del menú del spot
# ------------------------------------------------------------------

def _cb_action(query, user_id: int, accion: str, spot_key: str, desde_favoritos: bool = False):
    if not spot_key:
        # Ídem #A3.1. Mismo criterio que "spot no encontrado" más abajo:
        # el origen (favoritos vs. navegación normal) se conoce igual,
        # aunque no haya spot_key.
        destino = "back:favoritos" if desde_favoritos else "back:paises"
        _safe_edit(query, "❌ No hay spot seleccionado.", reply_markup=kb_volver(destino))
        return

    try:
        spot = get_spot(spot_key)
    except KeyError:
        destino = "back:favoritos" if desde_favoritos else "back:paises"
        _safe_edit(query, "❌ Spot no encontrado.", reply_markup=kb_volver(destino))
        return

    if accion == "ahora":
        _mostrar_ahora(query, user_id, spot_key, spot, desde_favoritos=desde_favoritos)

    elif accion == "fecha":
        _cb_mostrar_selector_fecha(query, spot_key, desde_favoritos=desde_favoritos)

    elif accion == "ventanas":
        _mostrar_ventanas(query, user_id, spot_key, spot, desde_favoritos=desde_favoritos)

    elif accion == "horaria":
        # Hora a hora: usa el día de hoy en la tz del spot
        fecha_hoy = _fecha_local_hoy(spot)
        _mostrar_horaria(query, spot_key, spot, fecha_hoy, desde_favoritos=desde_favoritos)

    elif accion == "semana":
        _mostrar_semana(query, user_id, spot_key, spot, desde_favoritos=desde_favoritos)

    elif accion == "breakdown":
        _mostrar_breakdown(query, spot_key, spot, desde_favoritos=desde_favoritos)

    else:
        logger.warning("Acción desconocida: %s para spot %s", accion, spot_key)


# ------------------------------------------------------------------
# Acciones concretas
# ------------------------------------------------------------------

def _mostrar_ahora(query, user_id: int, spot_key: str, spot, desde_favoritos: bool = False):
    """Condiciones actuales + tendencia de marea + ventanas próximas + mejor hora de hoy."""
    try:
        _safe_edit(query, "⏳ Consultando pronóstico...")
        forecast = _get_forecast_cached(spot_key)
        hour, breakdown = calcular_score_actual(forecast, spot)

        if hour is None:
            _safe_edit(query, formato_no_disponible(spot))
            return

        # Análisis de marea para tendencia legible
        fecha_hoy = _fecha_local_hoy(spot)
        tide_analysis = detectar_mareas_del_dia(forecast, fecha_hoy, spot=spot)

        texto = formato_condiciones_actuales(hour, breakdown, spot, tide_analysis=tide_analysis)

        # Ventana más cercana (mínimo 2h) — umbral según nivel de surf (#A2)
        nivel = session_store.get_nivel(user_id)
        ventanas = detectar_ventanas(forecast, spot, umbral=UMBRAL_POR_NIVEL[nivel]["ventana"])
        if ventanas:
            texto += f"\n\n{SEPARADOR_MSG}\n*PRÓXIMA VENTANA ÓPTIMA*\n"
            texto += formato_lista_ventanas_corta(ventanas, spot)

        # Mejor hora de hoy
        mejor = calcular_mejor_hora(forecast, spot, fecha_hoy)
        if mejor:
            from bot.formatters import formato_mejor_hora
            texto += f"\n\n{formato_mejor_hora(mejor, spot)}"

        _safe_edit(query,
            texto,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_post_forecast(spot_key, desde_favoritos=desde_favoritos),
        )
    except Exception as e:
        logger.exception("Error en _mostrar_ahora: %s", e)
        _safe_edit(query, formato_no_disponible(spot, str(e)))


def _cb_mostrar_selector_fecha(query, spot_key: str, desde_favoritos: bool = False):
    """Muestra el selector de 7 días para consulta por fecha."""
    try:
        spot = get_spot(spot_key)
        fecha_base = _fecha_local_hoy(spot)
    except KeyError:
        fecha_base = date.today()

    _safe_edit(query,
        "📅 ¿Para qué día querés ver las condiciones?",
        reply_markup=kb_seleccion_fecha(spot_key, fecha_base, desde_favoritos=desde_favoritos),
    )


def _mostrar_dia(query, spot_key: str, fecha: date, desde_favoritos: bool = False):
    """
    Vista completa para un día específico:
    condiciones de esa hora + luz solar + mareas + mejor hora.
    """
    try:
        _safe_edit(query, "⏳ Calculando condiciones del día...")
        spot = get_spot(spot_key)
        forecast = _get_forecast_cached(spot_key)
        tz = spot.get_zoneinfo()

        # Hora más representativa del día (mediodía local si es futuro,
        # hora actual si es hoy)
        fecha_hoy = _fecha_local_hoy(spot)
        if fecha == fecha_hoy:
            now_utc = datetime.now(timezone.utc)
            hour, breakdown = calcular_score_actual(forecast, spot)
        else:
            # Usar el mejor momento del día para mostrar condiciones
            mejor = calcular_mejor_hora(forecast, spot, fecha)
            if mejor:
                hour = mejor.hour
                breakdown = mejor.breakdown
            else:
                # Fallback: primera hora del día disponible
                horas_del_dia = [
                    h for h in forecast
                    if h.timestamp.astimezone(tz).date() == fecha
                ]
                if not horas_del_dia:
                    _safe_edit(query,
                        f"⚠️ No hay datos de pronóstico para {fecha.strftime('%d/%m')}.\n"
                        "El pronóstico cubre los próximos 7 días.",
                        reply_markup=kb_seleccion_fecha(spot_key, fecha_hoy, desde_favoritos=desde_favoritos),
                    )
                    return
                hour = horas_del_dia[len(horas_del_dia) // 2]  # hora del mediodía
                from core.windows.detector import calcular_score_hora
                breakdown = calcular_score_hora(hour, spot)

        if hour is None:
            _safe_edit(query, formato_no_disponible(spot))
            return

        # Luz solar del día
        daylight = get_daylight(spot, fecha)

        # Mareas del día
        tide_analysis = detectar_mareas_del_dia(forecast, fecha, spot=spot)

        # Mejor hora del día
        mejor_hora = calcular_mejor_hora(forecast, spot, fecha)

        texto = formato_dia_completo(
            hour, breakdown, spot,
            daylight, tide_analysis, mejor_hora,
            es_hoy=(fecha == fecha_hoy),
        )

        origen = "fav" if desde_favoritos else ""
        _safe_edit(query,
            texto,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_post_forecast(
                spot_key, desde_favoritos=desde_favoritos,
                # "Volver" va al selector de 7 días, no al menú del spot
                # (#A3.2) — y "Hora a hora" respeta la fecha que se está
                # viendo, no siempre "hoy" (#A3.3).
                volver_data=f"back:fecha:{spot_key}:{origen}",
                fecha_horaria=fecha,
            ),
        )
    except Exception as e:
        logger.exception("Error en _mostrar_dia: %s", e)
        try:
            spot = get_spot(spot_key)
            _safe_edit(query, formato_no_disponible(spot, str(e)))
        except Exception:
            _safe_edit(query, "⚠️ Error al obtener el pronóstico.")


def _mostrar_ventanas(query, user_id: int, spot_key: str, spot, desde_favoritos: bool = False):
    """Próximas olas — vista 48h hora a hora con horas buenas destacadas."""
    try:
        _safe_edit(query, "⏳ Calculando próximas olas...")
        forecast = _get_forecast_cached(spot_key)
        nivel = session_store.get_nivel(user_id)
        umbral_ventana = UMBRAL_POR_NIVEL[nivel]["ventana"]
        ventanas = detectar_ventanas(forecast, spot, umbral=umbral_ventana)
        # Mismo umbral, misma escala (0-1, score_total) para la búsqueda de
        # "próxima oportunidad" más allá de 48h — antes hardcodeado en 55,
        # inconsistente con el umbral real usado para armar ventanas (#A2).
        # Sin redondear a score_100: eso introducía falsos positivos en el
        # borde (0.699 redondea a 70, pasaría un umbral de 70 igual).
        texto = formato_proximas_olas(forecast, ventanas, spot, umbral_score=umbral_ventana)
        _safe_edit(query,
            texto,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_post_forecast(spot_key, desde_favoritos=desde_favoritos),
        )
    except Exception as e:
        logger.exception("Error en _mostrar_ventanas: %s", e)
        _safe_edit(query, formato_no_disponible(spot, str(e)))


def _mostrar_horaria(query, spot_key: str, spot, fecha: date, desde_favoritos: bool = False):
    """Vista hora a hora para el día indicado."""
    try:
        _safe_edit(query, "⏳ Generando vista hora a hora...")
        forecast = _get_forecast_cached(spot_key)
        view = generar_vista_horaria(forecast, spot, fecha, incluir_noche=True)

        # Si fecha no es hoy, preserva el contexto de fecha en el propio
        # teclado (#A3.2/#A3.3 completo): "Hora a hora" sigue apuntando al
        # mismo día en vez de saltar a hoy, y "Volver" va al selector de
        # 7 días en vez del menú del spot. Si es hoy, el comportamiento
        # observable es el mismo que antes de A3 (vuelve al menú del
        # spot) aunque el callback_data de "Hora a hora" cambia de
        # action:horaria:... a horaria:...:<fecha_iso>:... — equivalente
        # en efecto, no en el string.
        origen = "fav" if desde_favoritos else ""
        es_hoy = fecha == _fecha_local_hoy(spot)
        volver_data = None if es_hoy else f"back:fecha:{spot_key}:{origen}"
        kb = kb_post_forecast(
            spot_key, desde_favoritos=desde_favoritos,
            volver_data=volver_data, fecha_horaria=fecha,
        )

        if view is None:
            _safe_edit(query,
                f"⚠️ No hay datos para {fecha.strftime('%d/%m')}.",
                reply_markup=kb,
            )
            return

        texto = formato_vista_horaria(view, spot)
        _safe_edit(query,
            texto,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb,
        )
    except Exception as e:
        logger.exception("Error en _mostrar_horaria: %s", e)
        _safe_edit(query, formato_no_disponible(spot, str(e)))


def _mostrar_semana(query, user_id: int, spot_key: str, spot, desde_favoritos: bool = False):
    """Ranking de los próximos 7 días."""
    try:
        _safe_edit(query, "⏳ Analizando la semana...")
        forecast = _get_forecast_cached(spot_key)
        nivel = session_store.get_nivel(user_id)
        analysis = analizar_semana(forecast, spot, umbral_dia_bueno=UMBRAL_POR_NIVEL[nivel]["dia_bueno"])

        if analysis is None:
            _safe_edit(query,
                "⚠️ No hay suficientes datos para el análisis semanal.",
                reply_markup=kb_post_forecast(spot_key, desde_favoritos=desde_favoritos),
            )
            return

        texto = formato_semana(analysis, spot)
        _safe_edit(query,
            texto,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_post_forecast(spot_key, desde_favoritos=desde_favoritos),
        )
    except Exception as e:
        logger.exception("Error en _mostrar_semana: %s", e)
        _safe_edit(query, formato_no_disponible(spot, str(e)))


def _mostrar_breakdown(query, spot_key: str, spot, desde_favoritos: bool = False):
    """Breakdown técnico de sub-scores."""
    try:
        forecast = _get_forecast_cached(spot_key)
        hour, breakdown = calcular_score_actual(forecast, spot)
        if hour is None:
            _safe_edit(query, formato_no_disponible(spot))
            return
        from bot.formatters import formato_breakdown_pro
        texto = formato_breakdown_pro(hour, breakdown, spot)
        _safe_edit(query,
            texto,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_post_forecast(spot_key, desde_favoritos=desde_favoritos),
        )
    except Exception as e:
        logger.exception("Error en _mostrar_breakdown: %s", e)
        _safe_edit(query, formato_no_disponible(spot, str(e)))


# ------------------------------------------------------------------
# Navegación: back
# ------------------------------------------------------------------

def _mostrar_menu_paises(query, user_id: int):
    """Pantalla de selección de país — destino común de "Volver" desde
    varios puntos. Antes este texto+teclado estaba duplicado 3 veces
    dentro de _cb_back(), una fuente de verdad distinta por cada destino
    lógico (#A3.8)."""
    session_store.update_session(user_id, step="seleccion_pais")
    paises = listar_paises()
    favs = _resolver_nombres_favoritos(session_store.get_favoritos(user_id))
    _safe_edit(query,
        "¿Dónde vas a surfear? 🏄‍♂️🌍",
        reply_markup=kb_seleccion_pais(paises, favoritos=favs),
    )


def _cb_back(query, user_id: int, destino: str):
    if destino == "favoritos":
        _mostrar_favoritos(query, user_id)
    elif destino == "paises":
        _mostrar_menu_paises(query, user_id)
    elif destino.startswith("regiones:"):
        pais_key = destino.split(":")[1]
        _cb_pais(query, user_id, pais_key)
    elif destino.startswith("spots:"):
        parts = destino.split(":")
        if len(parts) == 3:
            pais_key, region_key = parts[1], parts[2]
            _cb_region(query, user_id, pais_key, region_key)
        else:
            _mostrar_menu_paises(query, user_id)
    elif destino.startswith("spot:"):
        # back:spot:<spot_key>:<origen>
        parts = destino.split(":")
        spot_key = parts[1]
        desde_favoritos = len(parts) > 2 and parts[2] == "fav"
        _cb_spot(query, user_id, spot_key, desde_favoritos=desde_favoritos)
    elif destino.startswith("fecha:"):
        # back:fecha:<spot_key>:<origen> — vuelve al selector de 7 días,
        # no al menú del spot (#A3.2): "Volver" desde el detalle de un
        # día debía saltar 2 pasos atrás para probar otra fecha.
        parts = destino.split(":")
        spot_key = parts[1]
        desde_favoritos = len(parts) > 2 and parts[2] == "fav"
        _cb_mostrar_selector_fecha(query, spot_key, desde_favoritos=desde_favoritos)


def _mostrar_favoritos(query, user_id: int):
    """Pantalla dedicada 'Mis favoritos' — destino de Volver para spots
    abiertos desde el contexto de favoritos (#B1)."""
    favs = _resolver_nombres_favoritos(session_store.get_favoritos(user_id))
    if not favs:
        paises = listar_paises()
        _safe_edit(query,
            "No tenés spots favoritos todavía. Explorá y agregá alguno con ⭐ desde el menú de un spot.",
            reply_markup=kb_seleccion_pais(paises, favoritos=[]),
        )
        return
    _safe_edit(query,
        "⭐ *Mis favoritos*",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_favoritos(favs),
    )


# ------------------------------------------------------------------
# Favoritos
# ------------------------------------------------------------------

def _cb_fav_add(query, user_id: int, spot_key: str, desde_favoritos: bool = False):
    session_store.add_favorito(user_id, spot_key)
    query.answer("⭐ Agregado a favoritos")
    try:
        spot = get_spot(spot_key)
        query.message.edit_reply_markup(
            kb_menu_spot(
                spot_key, es_favorito=True,
                pais_key=spot.pais.lower(), region_key=spot.region,
                desde_favoritos=desde_favoritos,
            )
        )
    except Exception:
        pass


def _cb_fav_del(query, user_id: int, spot_key: str, desde_favoritos: bool = False):
    session_store.remove_favorito(user_id, spot_key)
    query.answer("💔 Quitado de favoritos")
    try:
        spot = get_spot(spot_key)
        query.message.edit_reply_markup(
            kb_menu_spot(
                spot_key, es_favorito=False,
                pais_key=spot.pais.lower(), region_key=spot.region,
                desde_favoritos=desde_favoritos,
            )
        )
    except Exception:
        pass


# ------------------------------------------------------------------
# Nivel de surf (#A2)
# ------------------------------------------------------------------

def _cb_nivel(query, user_id: int, nivel: str):
    """Guarda el nivel elegido (onboarding o /nivel) y sigue la navegación."""
    if nivel not in NOMBRE_NIVEL:
        logger.warning("Nivel desconocido en callback: %s", nivel)
        return

    # Spot que se estaba mirando ANTES de tocar /nivel — se lee antes de
    # pisar el estado, para poder volver ahí en vez de reiniciar la
    # navegación desde países (#A3.7). Exige step == "menu_spot": spot_key
    # queda en el estado indefinidamente (nada lo borra al volver a
    # países/regiones), así que sin este chequeo un usuario que visitó un
    # spot, volvió a países y recién ahí tocó /nivel terminaría de vuelta
    # en ese spot viejo en vez del menú principal (bug real encontrado en
    # revisión de Codex). "menu_spot" también cubre estar en cualquier
    # pantalla de resultado del spot (Ahora/Ventanas/etc. no cambian
    # step), que es el caso que sí queremos preservar.
    estado = session_store.get_session(user_id)
    spot_key_previo = estado.get("spot_key") if estado.get("step") == "menu_spot" else None

    session_store.set_nivel(user_id, nivel)

    if spot_key_previo:
        try:
            get_spot(spot_key_previo)
            query.answer(f"✅ Nivel: {NOMBRE_NIVEL[nivel]}")
            _cb_spot(query, user_id, spot_key_previo)
            return
        except KeyError:
            pass  # el spot ya no existe — cae al menú principal de abajo

    session_store.update_session(user_id, step="seleccion_pais")

    first_name = escape_markdown(query.from_user.first_name or "surfer", version=2)
    texto_menu, keyboard = _texto_menu_principal(user_id, first_name)
    texto = f"✅ Nivel guardado: *{NOMBRE_NIVEL[nivel]}*\\.\n\n{texto_menu}"

    _safe_edit(query,
        texto,
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=keyboard,
    )


# ------------------------------------------------------------------
# Registro en el dispatcher
# ------------------------------------------------------------------

def register_handlers(dp: Dispatcher):
    dp.add_handler(CommandHandler("start", handle_start))
    dp.add_handler(CommandHandler("nivel", handle_nivel))
    dp.add_handler(CommandHandler("ajuste", handle_ajuste))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_text))
    dp.add_handler(CallbackQueryHandler(handle_callback))
