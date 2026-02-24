"""Teclados inline para Telegram.

Convenciones de callback_data:
  pais:<pais_key>
  region:<pais_key>:<region_key>
  spot:<spot_key>
  action:<accion>:<spot_key>
  fav_add:<spot_key>
  fav_del:<spot_key>
  back:<destino>

Todo el flujo de navegación ocurre por botones inline.
El usuario nunca tiene que tipear nada.
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from typing import List, Tuple


# ------------------------------------------------------------------
# Pantalla 1: Selección de país
# ------------------------------------------------------------------

PAISES_FLAGS = {
    "ar": "🇦🇷",
    "br": "🇧🇷",
    "cl": "🇨🇱",
    "pe": "🇵🇪",
    "cr": "🇨🇷",
}


def kb_seleccion_pais(paises: List[Tuple[str, str]]) -> InlineKeyboardMarkup:
    """Paises disponibles, dos por fila."""
    botones = []
    fila = []
    for i, (key, nombre) in enumerate(paises):
        flag = PAISES_FLAGS.get(key, "🌍")
        btn = InlineKeyboardButton(f"{flag} {nombre}", callback_data=f"pais:{key}")
        fila.append(btn)
        if len(fila) == 2:
            botones.append(fila)
            fila = []
    if fila:
        botones.append(fila)
    return InlineKeyboardMarkup(botones)


# ------------------------------------------------------------------
# Pantalla 2: Selección de región
# ------------------------------------------------------------------

def kb_seleccion_region(
    pais_key: str, regiones: List[Tuple[str, str]]
) -> InlineKeyboardMarkup:
    botones = []
    for key, nombre in regiones:
        botones.append([InlineKeyboardButton(
            f"📍 {nombre}", callback_data=f"region:{pais_key}:{key}"
        )])
    botones.append([InlineKeyboardButton("⬅️ Volver", callback_data="back:paises")])
    return InlineKeyboardMarkup(botones)


# ------------------------------------------------------------------
# Pantalla 3: Selección de spot
# ------------------------------------------------------------------

def kb_seleccion_spot(
    pais_key: str,
    region_key: str,
    spots: List[Tuple[str, dict]],
) -> InlineKeyboardMarkup:
    BREAK_ICONS = {"reef": "🪸", "point": "↪️", "beach": "🏖️"}
    botones = []
    for key, info in spots:
        icon = BREAK_ICONS.get(info.get("tipo_break", "beach"), "🏄")
        nombre = info.get("nombre", key)
        ciudad = info.get("ciudad", "")
        label = f"{icon} {nombre}"
        if ciudad and ciudad != nombre:
            label = f"{icon} {ciudad} · {nombre}"
        botones.append([InlineKeyboardButton(label, callback_data=f"spot:{key}")])
    botones.append([InlineKeyboardButton(
        "⬅️ Volver", callback_data=f"back:regiones:{pais_key}"
    )])
    return InlineKeyboardMarkup(botones)


# ------------------------------------------------------------------
# Pantalla 4: Menú del spot (acciones)
# ------------------------------------------------------------------

def kb_menu_spot(spot_key: str, es_pro: bool = False, es_favorito: bool = False) -> InlineKeyboardMarkup:
    """Menú de acciones para un spot seleccionado."""
    fav_label = "💛 Quitar favorito" if es_favorito else "⭐ Guardar favorito"
    fav_data = f"fav_del:{spot_key}" if es_favorito else f"fav_add:{spot_key}"

    botones = [
        [InlineKeyboardButton("🌊 Ver ahora", callback_data=f"action:ahora:{spot_key}")],
    ]

    if es_pro:
        botones.append([
            InlineKeyboardButton("⏱ Ventanas 48h", callback_data=f"action:ventanas:{spot_key}"),
            InlineKeyboardButton("🔬 Breakdown", callback_data=f"action:breakdown:{spot_key}"),
        ])
        botones.append([InlineKeyboardButton(fav_label, callback_data=fav_data)])
    else:
        botones.append([
            InlineKeyboardButton(
                "🔒 Ventanas 48h (Pro)", callback_data="action:upgrade"
            )
        ])

    botones.append([InlineKeyboardButton("🗺️ Cambiar spot", callback_data="back:paises")])
    return InlineKeyboardMarkup(botones)


# ------------------------------------------------------------------
# Pantalla post-forecast (botones de acción rápida)
# ------------------------------------------------------------------

def kb_post_forecast(spot_key: str, es_pro: bool = False) -> InlineKeyboardMarkup:
    botones = [
        [
            InlineKeyboardButton("🔄 Actualizar", callback_data=f"action:ahora:{spot_key}"),
            InlineKeyboardButton("🗺️ Cambiar spot", callback_data="back:paises"),
        ]
    ]
    if es_pro:
        botones.append([
            InlineKeyboardButton("⏱ Ver 48h", callback_data=f"action:ventanas:{spot_key}"),
            InlineKeyboardButton("🔬 Breakdown", callback_data=f"action:breakdown:{spot_key}"),
        ])
    else:
        botones.append([
            InlineKeyboardButton("🔒 Ver 48h (Pro)", callback_data="action:upgrade")
        ])
    return InlineKeyboardMarkup(botones)


# ------------------------------------------------------------------
# Pantalla Pro upgrade
# ------------------------------------------------------------------

def kb_upgrade() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🚀 Ver planes Pro", callback_data="action:ver_pro"),
        InlineKeyboardButton("⬅️ Volver", callback_data="back:paises"),
    ]])


# ------------------------------------------------------------------
# Favoritos (Pro)
# ------------------------------------------------------------------

def kb_favoritos(favoritos: List[Tuple[str, str]]) -> InlineKeyboardMarkup:
    """Lista de spots favoritos del usuario."""
    botones = []
    for spot_key, nombre in favoritos:
        botones.append([InlineKeyboardButton(
            f"⭐ {nombre}", callback_data=f"spot:{spot_key}"
        )])
    botones.append([InlineKeyboardButton("📍 Explorar spots", callback_data="back:paises")])
    return InlineKeyboardMarkup(botones)
