"""Handlers principales del bot de Telegram — V2."""

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


# ==========================================================
# SAFE EDIT (evita crash Message is not modified)
# ==========================================================

def _safe_edit_message(query, text, **kwargs):
    try:
        query.message.edit_text(text, **kwargs)
    except BadRequest as e:
        if "Message is not modified" in str(e):
            return
        raise


# ==========================================================
# HELPERS
# ==========================================================

def _es_admin(user_id: int) -> bool:
    admins = os.getenv("ADMIN_USER_IDS", "")
    return str(user_id) in [a.strip() for a in admins.split(",") if a.strip()]


def _get_forecast_cached(spot_key: str):
    cached = forecast_cache.get(spot_key)
    if cached is not None:
        return cached
    spot = get_spot(spot_key)
    provider = get_provider(spot.fuente_datos)
    forecast = provider.get_forecast_48h(spot)
    forecast_cache.set(spot_key, forecast)
    return forecast


def _fecha_local_hoy(spot) -> date:
    return datetime.now(spot.get_zoneinfo()).date()


# ==========================================================
# /start
# ==========================================================

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


# ==========================================================
# /ajuste (admins)
# ==========================================================

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
            f"✅ Ajuste aplicado: {spot_key} · {param} = {valor}\n"
            "El caché del spot fue invalidado.",
            parse_mode=ParseMode.MARKDOWN,
        )
    except (KeyError, ValueError) as e:
        update.message.reply_text(f"❌ Error: {e}")


# ==========================================================
# CALLBACK ROUTER
# ==========================================================

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
        parts = data[6:].split(":", 1)
        if len(parts) == 2:
            spot_key, fecha_iso = parts
            try:
                fecha = date.fromisoformat(fecha_iso)
                _mostrar_dia(query, spot_key, fecha)
            except ValueError:
                query.message.reply_text("❌ Fecha inválida.")
        else:
            _cb_mostrar_selector_fecha(query, data[6:])
    elif data.startswith("back:"):
        _cb_back(query, user_id, data[5:])
    elif data.startswith("fav_add:"):
        _cb_fav_add(query, user_id, data[8:])
    elif data.startswith("fav_del:"):
        _cb_fav_del(query, user_id, data[8:])
    else:
        logger.warning("Callback desconocido: %s", data)


# ==========================================================
# NAVEGACIÓN
# ==========================================================

def _cb_pais(query, user_id: int, pais_key: str):
    session_store.update_session(user_id, step="seleccion_region", pais=pais_key)
    regiones = listar_regiones(pais_key)

    if not regiones:
        query.message.reply_text("No hay regiones configuradas.")
        return

    _safe_edit_message(
        query,
        "📍 Seleccioná la región:",
        reply_markup=kb_seleccion_region(pais_key, regiones),
    )


def _cb_region(query, user_id: int, pais_key: str, region_key: str):
    session_store.update_session(user_id, step="seleccion_spot", region=region_key)
    spots = listar_spots_region(pais_key, region_key)

    if not spots:
        query.message.reply_text("No hay spots configurados.")
        return

    _safe_edit_message(
        query,
        "🏄 Elegí tu spot:",
        reply_markup=kb_seleccion_spot(pais_key, region_key, spots),
    )


def _cb_spot(query, user_id: int, spot_key: str):
    spot = get_spot(spot_key)

    session_store.update_session(user_id, step="menu_spot", spot_key=spot_key)
    favs = session_store.get_favoritos(user_id)
    es_fav = spot_key in favs

    texto = (
        f"🏄 *{spot.nombre}*\n"
        f"📍 {spot.ciudad} · {spot.pais}\n"
        f"🏄 {spot.tipo_break.capitalize()} — {spot.fondo}"
    )

    _safe_edit_message(
        query,
        texto,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_menu_spot(spot_key, es_favorito=es_fav),
    )


# ==========================================================
# REGISTRO
# ==========================================================

def register_handlers(dp: Dispatcher):
    dp.add_handler(CommandHandler("start", handle_start))
    dp.add_handler(CommandHandler("ajuste", handle_ajuste))
    dp.add_handler(CallbackQueryHandler(handle_callback))