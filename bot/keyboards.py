from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from datetime import timedelta


# ==========================================================
# Países
# ==========================================================

def kb_seleccion_pais(paises):
    keyboard = []
    for key, nombre in paises:
        keyboard.append([
            InlineKeyboardButton(nombre, callback_data=f"pais:{key}")
        ])
    return InlineKeyboardMarkup(keyboard)


# ==========================================================
# Regiones
# ==========================================================

def kb_seleccion_region(pais_key, regiones):
    keyboard = []
    for key, nombre in regiones:
        keyboard.append([
            InlineKeyboardButton(
                nombre,
                callback_data=f"region:{pais_key}:{key}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton("🔙 Volver", callback_data="back:paises")
    ])

    return InlineKeyboardMarkup(keyboard)


# ==========================================================
# Spots
# ==========================================================

def kb_seleccion_spot(pais_key, region_key, spots):
    keyboard = []

    for key, nombre in spots:
        keyboard.append([
            InlineKeyboardButton(
                nombre,
                callback_data=f"spot:{key}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton(
            "🔙 Volver",
            callback_data=f"back:regiones:{pais_key}"
        )
    ])

    return InlineKeyboardMarkup(keyboard)


# ==========================================================
# Menú Spot
# ==========================================================

def kb_menu_spot(spot_key, es_favorito=False):
    fav_text = "💔 Quitar de favoritos" if es_favorito else "⭐ Agregar a favoritos"

    keyboard = [
        [InlineKeyboardButton("🌊 Ahora", callback_data=f"action:ahora:{spot_key}")],
        [InlineKeyboardButton("📅 Elegir fecha", callback_data=f"action:fecha:{spot_key}")],
        [InlineKeyboardButton("⏳ Ventanas 48h", callback_data=f"action:ventanas:{spot_key}")],
        [InlineKeyboardButton("🕒 Hora a hora", callback_data=f"action:horaria:{spot_key}")],
        [InlineKeyboardButton("📊 Semana", callback_data=f"action:semana:{spot_key}")],
        [InlineKeyboardButton("🧠 Breakdown técnico", callback_data=f"action:breakdown:{spot_key}")],
        [InlineKeyboardButton(fav_text, callback_data=f"{'fav_del' if es_favorito else 'fav_add'}:{spot_key}")],
    ]

    return InlineKeyboardMarkup(keyboard)


# ==========================================================
# Post forecast (después de mostrar datos)
# ==========================================================

def kb_post_forecast(spot_key):
    keyboard = [
        [InlineKeyboardButton("🔄 Volver al menú", callback_data=f"spot:{spot_key}")]
    ]
    return InlineKeyboardMarkup(keyboard)


# ==========================================================
# Selector de fecha (7 días)
# ==========================================================

def kb_seleccion_fecha(spot_key, fecha_base):
    keyboard = []

    for i in range(7):
        fecha = fecha_base + timedelta(days=i)
        label = fecha.strftime("%a %d/%m")
        keyboard.append([
            InlineKeyboardButton(
                label,
                callback_data=f"fecha:{spot_key}:{fecha.isoformat()}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton("🔙 Volver", callback_data=f"spot:{spot_key}")
    ])

    return InlineKeyboardMarkup(keyboard)


# ==========================================================
# Favoritos
# ==========================================================

def kb_favoritos(favoritos):
    keyboard = []

    for spot_key, nombre in favoritos:
        keyboard.append([
            InlineKeyboardButton(
                nombre,
                callback_data=f"spot:{spot_key}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton("🔙 Volver", callback_data="back:paises")
    ])

    return InlineKeyboardMarkup(keyboard)