"""Handlers principales del bot de Telegram.

Flujo de conversación:
  /start → selección país → región → spot → menú spot → acción

Todo el flujo usa InlineKeyboard callbacks.
El estado se persiste en SQLite via session_store.
"""

import logging
from datetime import datetime

from telegram import Update, ParseMode
from telegram.ext import (
    CallbackContext,
    CallbackQueryHandler,
    CommandHandler,
    Dispatcher,
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
from persistence.session_store import session_store
from bot.formatters import (
    formato_condiciones_actuales,
    formato_ventanas,
    formato_breakdown_pro,
    formato_lista_ventanas_corta,
    formato_no_disponible,
)

from bot.keyboards import (
    kb_seleccion_pais,
    kb_seleccion_region,
    kb_seleccion_spot,
    kb_menu_spot,
    kb_post_forecast,
    kb_upgrade,
    kb_favoritos,
)

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _es_pro(user_id: int) -> bool:
    """Placeholder de verificación de plan Pro."""
    # TODO: conectar con sistema de pagos (Stripe, etc.)
    # Por ahora devuelve False para todos (free).
    # Para testing: poner user_id en una lista de admins en .env
    import os
    admins = os.getenv("ADMIN_USER_IDS", "")
    if str(user_id) in admins.split(","):
        return True
    estado = session_store.get_session(user_id)
    return estado.get("plan") == "pro"


def _get_forecast_cached(spot_key: str):
    """Obtiene forecast con caché."""
    cached = forecast_cache.get(spot_key)
    if cached is not None:
        return cached
    spot = get_spot(spot_key)
    provider = get_provider(spot.fuente_datos)
    forecast = provider.get_forecast_48h(spot)
    forecast_cache.set(spot_key, forecast)
    return forecast


def _reply_error(update: Update, msg: str):
    if update.callback_query:
        update.callback_query.message.reply_text(msg)
    elif update.message:
        update.message.reply_text(msg)


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
        "Consultá las condiciones de surf en cualquier spot de Latinoamérica\\.\n\n"
        "¿En qué país vas a surfear?"
    )
    update.message.reply_text(
        texto,
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=kb_seleccion_pais(paises),
    )


# ------------------------------------------------------------------
# /ajuste (admin)
# ------------------------------------------------------------------

def handle_ajuste(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if not _es_pro(user_id):
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
        forecast_cache.invalidate(spot_key)  # invalidar caché tras ajuste
        update.message.reply_text(
            f"✅ Ajuste aplicado: `{spot_key}` · {param} = {valor}\n"
            "El caché del spot fue invalidado.",
            parse_mode=ParseMode.MARKDOWN,
        )
    except (KeyError, ValueError) as e:
        update.message.reply_text(f"❌ Error: {e}")


# ------------------------------------------------------------------
# Callbacks inline
# ------------------------------------------------------------------

def handle_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()

    user_id = query.from_user.id
    data = query.data or ""

    # Routing por prefijo
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

    elif data.startswith("back:"):
        _cb_back(query, user_id, data[5:])

    elif data.startswith("fav_add:"):
        _cb_fav_add(query, user_id, data[8:])

    elif data.startswith("fav_del:"):
        _cb_fav_del(query, user_id, data[8:])

    else:
        logger.warning("Callback desconocido: %s", data)


def _cb_pais(query, user_id: int, pais_key: str):
    session_store.update_session(user_id, step="seleccion_region", pais=pais_key)
    regiones = listar_regiones(pais_key)

    if not regiones:
        query.message.reply_text("No hay regiones configuradas para este país todavía.")
        return

    if len(regiones) == 1:
        # Saltar selección de región si solo hay una
        _cb_region(query, user_id, pais_key, regiones[0][0])
        return

    query.message.edit_text(
        "📍 Seleccioná la región:",
        reply_markup=kb_seleccion_region(pais_key, regiones),
    )


def _cb_region(query, user_id: int, pais_key: str, region_key: str):
    session_store.update_session(user_id, step="seleccion_spot", region=region_key)
    spots = listar_spots_region(pais_key, region_key)

    if not spots:
        query.message.reply_text("No hay spots configurados en esta región.")
        return

    query.message.edit_text(
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
    pro = _es_pro(user_id)
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

    query.message.edit_text(
        texto,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_menu_spot(spot_key, es_pro=pro, es_favorito=es_fav),
    )


def _cb_action(query, user_id: int, accion: str, spot_key: str):
    if accion == "upgrade":
        query.message.edit_text(
            "🚀 *Olas Surfer Bot Pro*\n\n"
            "Con el plan Pro accedés a:\n"
            "• Ventanas óptimas 48h\n"
            "• Breakdown técnico de score\n"
            "• Favoritos y alertas\n"
            "• Sin publicidad\n\n"
            "_(Próximamente — mandá /pro para más info)_",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_upgrade(),
        )
        return

    if accion == "ver_pro":
        query.message.reply_text("📧 Funcionalidad Pro próximamente. Escribinos a @olas_surfer_bot")
        return

    if not spot_key:
        query.message.reply_text("❌ No hay spot seleccionado.")
        return

    try:
        spot = get_spot(spot_key)
    except KeyError:
        query.message.reply_text("❌ Spot no encontrado.")
        return

    pro = _es_pro(user_id)

    if accion == "ahora":
        _mostrar_ahora(query, spot_key, spot, pro)

    elif accion == "ventanas":
        if not pro:
            query.message.reply_text(
                "🔒 Las ventanas 48h son una función Pro.",
                reply_markup=kb_upgrade(),
            )
            return
        _mostrar_ventanas(query, spot_key, spot)

    elif accion == "breakdown":
        if not pro:
            query.message.reply_text(
                "🔒 El breakdown técnico es una función Pro.",
                reply_markup=kb_upgrade(),
            )
            return
        _mostrar_breakdown(query, spot_key, spot)


def _mostrar_ahora(query, spot_key: str, spot, pro: bool):
    try:
        query.message.edit_text("⏳ Consultando pronóstico...")
        forecast = _get_forecast_cached(spot_key)
        hour, breakdown = calcular_score_actual(forecast, spot)
        if hour is None:
            query.message.edit_text(formato_no_disponible(spot))
            return

        texto = formato_condiciones_actuales(hour, breakdown, spot, es_pro=pro)

        # Si es Pro, agregar ventana más cercana
        if pro:
            ventanas = detectar_ventanas(forecast, spot)
            if ventanas:
                texto += "\n\n" + SEPARADOR_MSG + "\n*PRÓXIMA VENTANA ÓPTIMA*\n"
                texto += formato_lista_ventanas_corta(ventanas)

        query.message.edit_text(
            texto,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_post_forecast(spot_key, es_pro=pro),
        )
    except Exception as e:
        logger.exception("Error en _mostrar_ahora: %s", e)
        query.message.edit_text(formato_no_disponible(spot, str(e)))


def _mostrar_ventanas(query, spot_key: str, spot):
    try:
        query.message.edit_text("⏳ Calculando ventanas...")
        forecast = _get_forecast_cached(spot_key)
        ventanas = detectar_ventanas(forecast, spot)
        texto = formato_ventanas(ventanas, spot, forecast)
        query.message.edit_text(
            texto,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_post_forecast(spot_key, es_pro=True),
        )
    except Exception as e:
        logger.exception("Error en _mostrar_ventanas: %s", e)
        query.message.edit_text(formato_no_disponible(spot, str(e)))


def _mostrar_breakdown(query, spot_key: str, spot):
    try:
        forecast = _get_forecast_cached(spot_key)
        hour, breakdown = calcular_score_actual(forecast, spot)
        if hour is None:
            query.message.edit_text(formato_no_disponible(spot))
            return
        texto = formato_breakdown_pro(hour, breakdown, spot)
        query.message.edit_text(
            texto,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_post_forecast(spot_key, es_pro=True),
        )
    except Exception as e:
        logger.exception("Error en _mostrar_breakdown: %s", e)
        query.message.edit_text(formato_no_disponible(spot, str(e)))


def _cb_back(query, user_id: int, destino: str):
    if destino == "paises":
        session_store.update_session(user_id, step="seleccion_pais")
        paises = listar_paises()
        query.message.edit_text(
            "🌍 ¿En qué país vas a surfear?",
            reply_markup=kb_seleccion_pais(paises),
        )
    elif destino.startswith("regiones:"):
        pais_key = destino.split(":")[1]
        _cb_pais(query, user_id, pais_key)


def _cb_fav_add(query, user_id: int, spot_key: str):
    session_store.add_favorito(user_id, spot_key)
    query.answer("⭐ Agregado a favoritos")
    try:
        spot = get_spot(spot_key)
        favs = session_store.get_favoritos(user_id)
        query.message.edit_reply_markup(
            kb_menu_spot(spot_key, es_pro=_es_pro(user_id), es_favorito=True)
        )
    except Exception:
        pass


def _cb_fav_del(query, user_id: int, spot_key: str):
    session_store.remove_favorito(user_id, spot_key)
    query.answer("💔 Quitado de favoritos")
    try:
        query.message.edit_reply_markup(
            kb_menu_spot(spot_key, es_pro=_es_pro(user_id), es_favorito=False)
        )
    except Exception:
        pass


SEPARADOR_MSG = "─" * 22


# ------------------------------------------------------------------
# Registro de handlers en el dispatcher
# ------------------------------------------------------------------

def register_handlers(dp: Dispatcher):
    dp.add_handler(CommandHandler("start", handle_start))
    dp.add_handler(CommandHandler("ajuste", handle_ajuste))
    dp.add_handler(CallbackQueryHandler(handle_callback))
