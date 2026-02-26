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
)
from bot.keyboards import (
    kb_seleccion_pais,
    kb_seleccion_region,
    kb_seleccion_spot,
    kb_menu_spot,
    kb_post_forecast,
    kb_seleccion_fecha,
    kb_favoritos,
)

logger = logging.getLogger(__name__)
SEPARADOR_MSG = "─" * 22


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


# ------------------------------------------------------------------
# /start
# ------------------------------------------------------------------

def handle_start(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    first_name = update.effective_user.first_name or "surfer"

    session_store.update_session(user_id, step="seleccion_pais")

    paises = listar_paises()
    texto = (
        f"🌊 Hola *{first_name}*\\! Bienvenido al *Olas Surfer Bot*\\.\n\n"
        "Consultá condiciones, ventanas, mareas y el mejor día de la semana "
        "para cualquier spot de Latinoamérica\\.\n\n"
        "¿En qué país vas a surfear?"
    )
    update.message.reply_text(
        texto,
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=kb_seleccion_pais(paises),
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
    query.answer()

    user_id = query.from_user.id
    data = query.data or ""

    if data.startswith("pais:"):
        _cb_pais(query, user_id, data[5:])

    elif data.startswith("region:"):
        parts = data[7:].split(":", 1)
        if len(parts) == 2:
            _cb_region(query, user_id, parts[0], parts[1])

    elif data.startswith("spot:"):
        _cb_spot(query, user_id, data[5:])

    elif data.startswith("action:"):
        parts = data[7:].split(":", 1)
        accion = parts[0]
        arg = parts[1] if len(parts) > 1 else ""
        _cb_action(query, user_id, accion, arg)

    elif data.startswith("fecha:"):
        # fecha:<spot_key>:<YYYY-MM-DD>
        parts = data[6:].split(":", 1)
        if len(parts) == 2:
            spot_key, fecha_iso = parts
            try:
                fecha = date.fromisoformat(fecha_iso)
                _mostrar_dia(query, spot_key, fecha)
            except ValueError:
                query.message.reply_text("❌ Fecha inválida.")
        else:
            # Sin fecha → mostrar selector
            _cb_mostrar_selector_fecha(query, data[6:])

    elif data.startswith("back:"):
        _cb_back(query, user_id, data[5:])

    elif data.startswith("fav_add:"):
        _cb_fav_add(query, user_id, data[8:])

    elif data.startswith("fav_del:"):
        _cb_fav_del(query, user_id, data[8:])

    else:
        logger.warning("Callback desconocido: %s", data)


# ------------------------------------------------------------------
# Navegación: país → región → spot → menú
# ------------------------------------------------------------------

def _cb_pais(query, user_id: int, pais_key: str):
    session_store.update_session(user_id, step="seleccion_region", pais=pais_key)
    regiones = listar_regiones(pais_key)

    if not regiones:
        query.message.reply_text("No hay regiones configuradas para este país todavía.")
        return

    if len(regiones) == 1:
        _cb_region(query, user_id, pais_key, regiones[0][0])
        return

    _safe_edit(query, 
        "📍 Seleccioná la región:",
        reply_markup=kb_seleccion_region(pais_key, regiones),
    )


def _cb_region(query, user_id: int, pais_key: str, region_key: str):
    session_store.update_session(user_id, step="seleccion_spot", region=region_key)
    spots = listar_spots_region(pais_key, region_key)

    if not spots:
        query.message.reply_text("No hay spots configurados en esta región.")
        return

    _safe_edit(query, 
        "🏄 Elegí tu spot:",
        reply_markup=kb_seleccion_spot(pais_key, region_key, spots),
    )


def _cb_spot(query, user_id: int, spot_key: str):
    try:
        spot = get_spot(spot_key)
    except KeyError:
        query.message.reply_text("❌ Spot no encontrado.")
        return

    session_store.update_session(user_id, step="menu_spot", spot_key=spot_key)
    favs = session_store.get_favoritos(user_id)
    es_fav = spot_key in favs

    tipo_icons = {"reef": "🪸", "point": "↪️", "beach": "🏖️"}
    icon = tipo_icons.get(spot.tipo_break, "🏄")

    texto = (
        f"{icon} *{spot.nombre}*\n"
        f"📍 {spot.ciudad} · {spot.pais}\n"
        f"🏄 {spot.tipo_break.capitalize()} break — {spot.fondo}\n"
    )
    if spot.notas:
        texto += f"\n_{spot.notas}_"

    _safe_edit(query, 
        texto,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_menu_spot(spot_key, es_favorito=es_fav),
    )


# ------------------------------------------------------------------
# Router de acciones del menú del spot
# ------------------------------------------------------------------

def _cb_action(query, user_id: int, accion: str, spot_key: str):
    if not spot_key:
        query.message.reply_text("❌ No hay spot seleccionado.")
        return

    try:
        spot = get_spot(spot_key)
    except KeyError:
        query.message.reply_text("❌ Spot no encontrado.")
        return

    if accion == "ahora":
        _mostrar_ahora(query, spot_key, spot)

    elif accion == "fecha":
        _cb_mostrar_selector_fecha(query, spot_key)

    elif accion == "ventanas":
        _mostrar_ventanas(query, spot_key, spot)

    elif accion == "horaria":
        # Hora a hora: usa el día de hoy en la tz del spot
        fecha_hoy = _fecha_local_hoy(spot)
        _mostrar_horaria(query, spot_key, spot, fecha_hoy)

    elif accion == "semana":
        _mostrar_semana(query, spot_key, spot)

    elif accion == "breakdown":
        _mostrar_breakdown(query, spot_key, spot)

    else:
        logger.warning("Acción desconocida: %s para spot %s", accion, spot_key)


# ------------------------------------------------------------------
# Acciones concretas
# ------------------------------------------------------------------

def _mostrar_ahora(query, spot_key: str, spot):
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

        # Ventana más cercana (mínimo 2h)
        ventanas = detectar_ventanas(forecast, spot)
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
            reply_markup=kb_post_forecast(spot_key),
        )
    except Exception as e:
        logger.exception("Error en _mostrar_ahora: %s", e)
        _safe_edit(query, formato_no_disponible(spot, str(e)))


def _cb_mostrar_selector_fecha(query, spot_key: str):
    """Muestra el selector de 7 días para consulta por fecha."""
    try:
        spot = get_spot(spot_key)
        fecha_base = _fecha_local_hoy(spot)
    except KeyError:
        fecha_base = date.today()

    _safe_edit(query, 
        "📅 ¿Para qué día querés ver las condiciones?",
        reply_markup=kb_seleccion_fecha(spot_key, fecha_base),
    )


def _mostrar_dia(query, spot_key: str, fecha: date):
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
                        "El pronóstico cubre hasta 48h desde ahora.",
                        reply_markup=kb_seleccion_fecha(spot_key, fecha_hoy),
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
        )

        _safe_edit(query, 
            texto,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_post_forecast(spot_key),
        )
    except Exception as e:
        logger.exception("Error en _mostrar_dia: %s", e)
        try:
            spot = get_spot(spot_key)
            _safe_edit(query, formato_no_disponible(spot, str(e)))
        except Exception:
            _safe_edit(query, "⚠️ Error al obtener el pronóstico.")


def _mostrar_ventanas(query, spot_key: str, spot):
    """Ventanas óptimas de las próximas 48h."""
    try:
        _safe_edit(query, "⏳ Calculando ventanas...")
        forecast = _get_forecast_cached(spot_key)
        ventanas = detectar_ventanas(forecast, spot)
        texto = formato_ventanas(ventanas, spot, forecast)
        _safe_edit(query, 
            texto,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_post_forecast(spot_key),
        )
    except Exception as e:
        logger.exception("Error en _mostrar_ventanas: %s", e)
        _safe_edit(query, formato_no_disponible(spot, str(e)))


def _mostrar_horaria(query, spot_key: str, spot, fecha: date):
    """Vista hora a hora para el día indicado."""
    try:
        _safe_edit(query, "⏳ Generando vista hora a hora...")
        forecast = _get_forecast_cached(spot_key)
        view = generar_vista_horaria(forecast, spot, fecha, incluir_noche=True)

        if view is None:
            _safe_edit(query, 
                f"⚠️ No hay datos para {fecha.strftime('%d/%m')}.",
                reply_markup=kb_post_forecast(spot_key),
            )
            return

        texto = formato_vista_horaria(view, spot)
        _safe_edit(query, 
            texto,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_post_forecast(spot_key),
        )
    except Exception as e:
        logger.exception("Error en _mostrar_horaria: %s", e)
        _safe_edit(query, formato_no_disponible(spot, str(e)))


def _mostrar_semana(query, spot_key: str, spot):
    """Ranking de los próximos 7 días."""
    try:
        _safe_edit(query, "⏳ Analizando la semana...")
        forecast = _get_forecast_cached(spot_key)
        analysis = analizar_semana(forecast, spot)

        if analysis is None:
            _safe_edit(query, 
                "⚠️ No hay suficientes datos para el análisis semanal.",
                reply_markup=kb_post_forecast(spot_key),
            )
            return

        texto = formato_semana(analysis, spot)
        _safe_edit(query, 
            texto,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_post_forecast(spot_key),
        )
    except Exception as e:
        logger.exception("Error en _mostrar_semana: %s", e)
        _safe_edit(query, formato_no_disponible(spot, str(e)))


def _mostrar_breakdown(query, spot_key: str, spot):
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
            reply_markup=kb_post_forecast(spot_key),
        )
    except Exception as e:
        logger.exception("Error en _mostrar_breakdown: %s", e)
        _safe_edit(query, formato_no_disponible(spot, str(e)))


# ------------------------------------------------------------------
# Navegación: back
# ------------------------------------------------------------------

def _cb_back(query, user_id: int, destino: str):
    if destino == "paises":
        session_store.update_session(user_id, step="seleccion_pais")
        paises = listar_paises()
        _safe_edit(query, 
            "🌍 ¿En qué país vas a surfear?",
            reply_markup=kb_seleccion_pais(paises),
        )
    elif destino.startswith("regiones:"):
        pais_key = destino.split(":")[1]
        _cb_pais(query, user_id, pais_key)


# ------------------------------------------------------------------
# Favoritos
# ------------------------------------------------------------------

def _cb_fav_add(query, user_id: int, spot_key: str):
    session_store.add_favorito(user_id, spot_key)
    query.answer("⭐ Agregado a favoritos")
    try:
        favs = session_store.get_favoritos(user_id)
        query.message.edit_reply_markup(
            kb_menu_spot(spot_key, es_favorito=True)
        )
    except Exception:
        pass


def _cb_fav_del(query, user_id: int, spot_key: str):
    session_store.remove_favorito(user_id, spot_key)
    query.answer("💔 Quitado de favoritos")
    try:
        query.message.edit_reply_markup(
            kb_menu_spot(spot_key, es_favorito=False)
        )
    except Exception:
        pass


# ------------------------------------------------------------------
# Registro en el dispatcher
# ------------------------------------------------------------------

def register_handlers(dp: Dispatcher):
    dp.add_handler(CommandHandler("start", handle_start))
    dp.add_handler(CommandHandler("ajuste", handle_ajuste))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_text))
    dp.add_handler(CallbackQueryHandler(handle_callback))
