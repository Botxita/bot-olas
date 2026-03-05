"""Teclados inline para Telegram.

Convenciones de callback_data:
  pais:<pais_key>
  region:<pais_key>:<region_key>
  spot:<spot_key>
  action:<accion>:<spot_key>
  fecha:<spot_key>:<fecha_iso>          ← NUEVO (ej: fecha:mdq_varese:2025-01-15)
  fav_add:<spot_key>
  fav_del:<spot_key>
  back:<destino>

Todo el flujo de navegación ocurre por botones inline.
El usuario nunca tiene que tipear nada.
"""

from datetime import date, timedelta
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
    "uy": "🇺🇾",
}


def kb_seleccion_pais(paises: List[Tuple[str, str]]) -> InlineKeyboardMarkup:
    """Países disponibles, dos por fila."""
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
# Sin lógica Pro — todo habilitado para todos.
# ------------------------------------------------------------------

def kb_menu_spot(
    spot_key: str,
    es_pro: bool = True,       # ignorado, mantenido por compatibilidad
    es_favorito: bool = False,
    pais_key: str = "",
    region_key: str = "",
) -> InlineKeyboardMarkup:
    """Menú de acciones para un spot seleccionado."""
    fav_label = "💛 Quitar favorito" if es_favorito else "⭐ Favorito"
    fav_data  = f"fav_del:{spot_key}" if es_favorito else f"fav_add:{spot_key}"

    botones = [
        # Fila 1: acciones principales
        [
            InlineKeyboardButton("🌊 Ahora",        callback_data=f"action:ahora:{spot_key}"),
            InlineKeyboardButton("📅 Por fecha",    callback_data=f"action:fecha:{spot_key}"),
        ],
        # Fila 2: vistas avanzadas
        [
            InlineKeyboardButton("🏄 Próximas olas", callback_data=f"action:ventanas:{spot_key}"),
            InlineKeyboardButton("📊 Hora a hora",  callback_data=f"action:horaria:{spot_key}"),
        ],
        # Fila 3: semana + breakdown
        [
            InlineKeyboardButton("📅 Esta semana",  callback_data=f"action:semana:{spot_key}"),
            InlineKeyboardButton("🔬 Breakdown",    callback_data=f"action:breakdown:{spot_key}"),
        ],
        # Fila 4: favorito + navegación
        [
            InlineKeyboardButton(fav_label,         callback_data=fav_data),
            InlineKeyboardButton("⬅️ Volver",        callback_data=f"back:spots:{pais_key}:{region_key}" if pais_key and region_key else "back:paises"),
        ],
    ]
    return InlineKeyboardMarkup(botones)


# ------------------------------------------------------------------
# Pantalla de selección de fecha (7 días por botones)
# ------------------------------------------------------------------

def kb_seleccion_fecha(spot_key: str, fecha_base: date = None) -> InlineKeyboardMarkup:
    """
    7 botones con fechas relativas para consulta por fecha.
    Hoy / Mañana / Pasado / +3 / +4 / +5 / +6

    callback_data: fecha:<spot_key>:<YYYY-MM-DD>
    """
    if fecha_base is None:
        fecha_base = date.today()

    etiquetas = ["Hoy", "Mañana", "Pasado", "+3", "+4", "+5", "+6"]
    botones = []
    fila = []

    for i, label in enumerate(etiquetas):
        fecha = fecha_base + timedelta(days=i)
        fecha_iso = fecha.isoformat()
        # Añadir fecha corta al label para contexto
        fecha_corta = fecha.strftime("%d/%m")
        texto = f"{label}\n{fecha_corta}" if i >= 2 else label
        btn = InlineKeyboardButton(texto, callback_data=f"fecha:{spot_key}:{fecha_iso}")
        fila.append(btn)
        if len(fila) == 2:
            botones.append(fila)
            fila = []

    if fila:
        botones.append(fila)

    botones.append([InlineKeyboardButton(
        "⬅️ Volver al spot", callback_data=f"spot:{spot_key}"
    )])
    return InlineKeyboardMarkup(botones)


# ------------------------------------------------------------------
# Pantalla post-forecast (botones de acción rápida)
# ------------------------------------------------------------------

def kb_post_forecast(
    spot_key: str,
    es_pro: bool = True,    # ignorado, mantenido por compatibilidad
) -> InlineKeyboardMarkup:
    """Botones que aparecen después de mostrar cualquier resultado."""
    botones = [
        [
            InlineKeyboardButton("🔄 Actualizar",   callback_data=f"action:ahora:{spot_key}"),
            InlineKeyboardButton("📅 Por fecha",    callback_data=f"action:fecha:{spot_key}"),
        ],
        [
            InlineKeyboardButton("🏄 Próximas olas", callback_data=f"action:ventanas:{spot_key}"),
            InlineKeyboardButton("📊 Hora a hora",  callback_data=f"action:horaria:{spot_key}"),
        ],
        [
            InlineKeyboardButton("📅 Esta semana",  callback_data=f"action:semana:{spot_key}"),
            InlineKeyboardButton("🔬 Breakdown",    callback_data=f"action:breakdown:{spot_key}"),
        ],
        [
            InlineKeyboardButton("⬅️ Volver al spot", callback_data=f"back:spot:{spot_key}"),
        ],
    ]
    return InlineKeyboardMarkup(botones)


# ------------------------------------------------------------------
# Favoritos
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


# ------------------------------------------------------------------
# kb_upgrade: mantenida vacía por compatibilidad (ya no se usa en handlers)
# ------------------------------------------------------------------

def kb_upgrade() -> InlineKeyboardMarkup:
    """Deprecated — mantenida para no romper imports existentes."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⬅️ Volver", callback_data="back:paises"),
    ]])
