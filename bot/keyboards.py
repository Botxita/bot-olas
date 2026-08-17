"""Teclados inline para Telegram.

Convenciones de callback_data:
  pais:<pais_key>
  region:<pais_key>:<region_key>
  spot:<spot_key>
  spot_from_home:<spot_key>             ← spot abierto desde favoritos (home o pantalla dedicada)
  action:<accion>:<spot_key>:<origen>   ← origen: "" (navegación normal) | "fav" (contexto favoritos)
  fecha:<spot_key>:<fecha_iso>:<origen>
  fav_add:<spot_key>:<origen>
  fav_del:<spot_key>:<origen>
  back:<destino>
  back:spot:<spot_key>:<origen>
  back:favoritos                        ← pantalla "Mis favoritos"
  back:fecha:<spot_key>:<origen>        ← selector de 7 días (#A3.2)
  horaria:<spot_key>:<fecha_iso>:<origen>  ← hora a hora de un día específico (#A3.3)
  nivel:<principiante|intermedio|avanzado>  ← onboarding / comando /nivel (#A2)

<origen> viaja en el propio callback_data (no en la sesión) — el país/región
de un spot para el botón "Volver" del menú se derivan de spot.pais/spot.region
(SpotConfig), no de estado de sesión mutable. Esto es intencional: antes, el
botón "Volver" leía país/región desde la sesión global del usuario, lo que
rompía la navegación al abrir un spot desde favoritos (sin país/región propios)
y además podía pisarse si el usuario tocaba un botón de un mensaje viejo en
paralelo. Con el contexto viajando en cada callback, cada botón es autónomo.

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
    "pa": "🇵🇦",
    "sv": "🇸🇻",
}


def kb_seleccion_pais(
    paises: List[Tuple[str, str]],
    favoritos: List[Tuple[str, str]] = None,  # lista de (spot_key, nombre)
) -> InlineKeyboardMarkup:
    """Países disponibles, dos por fila. Si hay favoritos, se muestran arriba."""
    botones = []

    # --- Favoritos (si existen): acceso directo a cada uno + pantalla dedicada ---
    if favoritos:
        for spot_key, nombre in favoritos:
            botones.append([InlineKeyboardButton(
                f"⭐ {nombre}", callback_data=f"spot_from_home:{spot_key}"
            )])
        botones.append([InlineKeyboardButton(
            "⭐ Mis favoritos", callback_data="back:favoritos"
        )])

    # --- Países ---
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
    desde_favoritos: bool = False,
) -> InlineKeyboardMarkup:
    """
    Menú de acciones para un spot seleccionado.

    desde_favoritos indica si el spot se abrió desde el contexto de
    favoritos (home o pantalla "Mis favoritos") — determina a dónde
    lleva "⬅️ Volver": a la pantalla de favoritos, no a la lista de
    spots de una región (el spot puede no pertenecer a ninguna región
    que el usuario haya navegado en esta sesión). pais_key/region_key
    los calcula el caller a partir de spot.pais/spot.region (no de
    sesión) — solo se usan si desde_favoritos es False.
    """
    origen = "fav" if desde_favoritos else ""
    fav_label = "💛 Quitar favorito" if es_favorito else "⭐ Favorito"
    fav_data  = f"fav_del:{spot_key}:{origen}" if es_favorito else f"fav_add:{spot_key}:{origen}"

    if desde_favoritos:
        volver_data = "back:favoritos"
    elif pais_key and region_key:
        volver_data = f"back:spots:{pais_key}:{region_key}"
    else:
        volver_data = "back:paises"  # fallback defensivo, no debería alcanzarse en la práctica

    botones = [
        # Fila 1: acciones principales
        [
            InlineKeyboardButton("🌊 Ahora",        callback_data=f"action:ahora:{spot_key}:{origen}"),
            InlineKeyboardButton("📅 Por fecha",    callback_data=f"action:fecha:{spot_key}:{origen}"),
        ],
        # Fila 2: vistas avanzadas
        [
            InlineKeyboardButton("🏄 Próximas olas", callback_data=f"action:ventanas:{spot_key}:{origen}"),
            InlineKeyboardButton("📊 Hora a hora",  callback_data=f"action:horaria:{spot_key}:{origen}"),
        ],
        # Fila 3: semana + breakdown
        [
            InlineKeyboardButton("🗓️ Esta semana",  callback_data=f"action:semana:{spot_key}:{origen}"),
            InlineKeyboardButton("🔬 Breakdown",    callback_data=f"action:breakdown:{spot_key}:{origen}"),
        ],
        # Fila 4: favorito (sola, no compite en peso visual con Volver — #A1)
        [
            InlineKeyboardButton(fav_label,         callback_data=fav_data),
        ],
        # Fila 5: navegación
        [
            InlineKeyboardButton("⬅️ Volver",        callback_data=volver_data),
        ],
    ]
    return InlineKeyboardMarkup(botones)


# ------------------------------------------------------------------
# Pantalla de selección de fecha (7 días por botones)
# ------------------------------------------------------------------

def kb_seleccion_fecha(spot_key: str, fecha_base: date = None, desde_favoritos: bool = False) -> InlineKeyboardMarkup:
    """
    7 botones con fechas relativas para consulta por fecha.
    Hoy / Mañana / Pasado / +3 / +4 / +5 / +6

    callback_data: fecha:<spot_key>:<YYYY-MM-DD>:<origen>
    """
    if fecha_base is None:
        fecha_base = date.today()

    origen = "fav" if desde_favoritos else ""
    etiquetas = ["Hoy", "Mañana", "Pasado", "+3", "+4", "+5", "+6"]
    botones = []
    fila = []

    for i, label in enumerate(etiquetas):
        fecha = fecha_base + timedelta(days=i)
        fecha_iso = fecha.isoformat()
        # Añadir fecha corta al label para contexto
        fecha_corta = fecha.strftime("%d/%m")
        texto = f"{label}\n{fecha_corta}" if i >= 2 else label
        btn = InlineKeyboardButton(texto, callback_data=f"fecha:{spot_key}:{fecha_iso}:{origen}")
        fila.append(btn)
        if len(fila) == 2:
            botones.append(fila)
            fila = []

    if fila:
        botones.append(fila)

    volver_data = f"spot_from_home:{spot_key}" if desde_favoritos else f"spot:{spot_key}"
    botones.append([InlineKeyboardButton(
        "⬅️ Volver", callback_data=volver_data
    )])
    return InlineKeyboardMarkup(botones)


# ------------------------------------------------------------------
# Pantalla post-forecast (botones de acción rápida)
# ------------------------------------------------------------------

def kb_post_forecast(
    spot_key: str,
    es_pro: bool = True,    # ignorado, mantenido por compatibilidad
    desde_favoritos: bool = False,
    volver_data: str = None,
    fecha_horaria: date = None,
) -> InlineKeyboardMarkup:
    """
    Botones que aparecen después de mostrar cualquier resultado.

    volver_data: destino de "Volver" (callback_data completo). None = el
    default de siempre, back:spot:<spot_key>:<origen> (menú del spot).
    Lo usa _mostrar_dia() (#A3.2) para que "Volver" desde el detalle de
    un día vuelva al selector de 7 días en vez de saltar al menú del
    spot, perdiendo la fecha que se estaba mirando.

    fecha_horaria: si se pasa, el botón "Hora a hora" lleva esa fecha
    específica en vez de asumir "hoy" (#A3.3) — lo usa _mostrar_dia()
    para que "Hora a hora" respete el día que se estaba mirando.
    """
    origen = "fav" if desde_favoritos else ""
    if fecha_horaria is not None:
        cb_horaria = f"horaria:{spot_key}:{fecha_horaria.isoformat()}:{origen}"
    else:
        cb_horaria = f"action:horaria:{spot_key}:{origen}"
    volver_data = volver_data or f"back:spot:{spot_key}:{origen}"
    botones = [
        [
            InlineKeyboardButton("🌊 Ahora",   callback_data=f"action:ahora:{spot_key}:{origen}"),
            InlineKeyboardButton("📅 Por fecha",    callback_data=f"action:fecha:{spot_key}:{origen}"),
        ],
        [
            InlineKeyboardButton("🏄 Próximas olas", callback_data=f"action:ventanas:{spot_key}:{origen}"),
            InlineKeyboardButton("📊 Hora a hora",  callback_data=cb_horaria),
        ],
        [
            InlineKeyboardButton("🗓️ Esta semana",  callback_data=f"action:semana:{spot_key}:{origen}"),
            InlineKeyboardButton("🔬 Breakdown",    callback_data=f"action:breakdown:{spot_key}:{origen}"),
        ],
        [
            InlineKeyboardButton("⬅️ Volver", callback_data=volver_data),
        ],
    ]
    return InlineKeyboardMarkup(botones)


# ------------------------------------------------------------------
# Favoritos
# ------------------------------------------------------------------

def kb_favoritos(favoritos: List[Tuple[str, str]]) -> InlineKeyboardMarkup:
    """
    Lista de spots favoritos del usuario. Cada botón usa spot_from_home
    (no spot:), para que el menú del spot sepa que "Volver" debe traer
    de regreso a esta pantalla, no a la lista de spots de una región.
    """
    botones = []
    for spot_key, nombre in favoritos:
        botones.append([InlineKeyboardButton(
            f"⭐ {nombre}", callback_data=f"spot_from_home:{spot_key}"
        )])
    botones.append([InlineKeyboardButton("📍 Explorar spots", callback_data="back:paises")])
    return InlineKeyboardMarkup(botones)


# ------------------------------------------------------------------
# Nivel de surf (#A2) — onboarding en /start y comando /nivel
# ------------------------------------------------------------------

def kb_nivel() -> InlineKeyboardMarkup:
    """Selector de nivel de surf (principiante/intermedio/avanzado)."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🐣 Principiante", callback_data="nivel:principiante")],
        [InlineKeyboardButton("🏄 Intermedio", callback_data="nivel:intermedio")],
        [InlineKeyboardButton("🔥 Avanzado", callback_data="nivel:avanzado")],
    ])


# ------------------------------------------------------------------
# kb_volver: pantallas de error sin navegación propia (#A3.1)
# ------------------------------------------------------------------

def kb_volver(destino: str) -> InlineKeyboardMarkup:
    """
    Teclado de un solo botón "Volver" hacia `destino` (callback_data
    completo, ej. "back:paises"). Antes, varios estados de error
    (país/región sin datos, spot no encontrado, callback malformado)
    respondían con query.message.reply_text() — un mensaje nuevo sin
    ningún botón, dejando el mensaje anterior (con botones desactualizados)
    todavía en pantalla. Con esto, esos errores editan el mensaje activo
    y siempre dejan una salida.
    """
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⬅️ Volver", callback_data=destino),
    ]])


# ------------------------------------------------------------------
# kb_upgrade: mantenida vacía por compatibilidad (ya no se usa en handlers)
# ------------------------------------------------------------------

def kb_upgrade() -> InlineKeyboardMarkup:
    """Deprecated — mantenida para no romper imports existentes."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⬅️ Volver", callback_data="back:paises"),
    ]])
