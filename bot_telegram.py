"""Bot de Telegram para el proyecto de olas.

Usa la misma lógica central que el simulador (core_bot_olas).
"""

import os
from typing import Dict, Any

from threading import Thread
from keep_alive import run as run_keep_alive


from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    Filters,
    CallbackContext,
)

from core_bot_olas import responder_mensaje
from ajustes_spots import actualizar_ajuste_param

# Cargar variables de entorno (.env)
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

if not TELEGRAM_TOKEN:
    raise RuntimeError(
        "No se encontró TELEGRAM_BOT_TOKEN en el entorno. "
        "Creá un archivo .env basado en .env.example."
    )

# Estado por usuario (solo en memoria)
estados_usuarios: Dict[int, Dict[str, Any]] = {}

def format_hour_line(hora, altura, periodo, viento, direccion_viento, oleaje, direccion_oleaje, estrellas):
    # Línea base sin estrellas
    base = f"{hora} · {altura}m/{periodo}s · {viento}→{direccion_viento} · {oleaje}→{direccion_oleaje} · "

    # ANCHO FIJO antes de las estrellas — ajustá si hace falta
    TARGET = 34

    # Calculamos cuántos espacios faltan para que todas las líneas midan lo mismo
    padding = max(0, TARGET - len(base))
    base = base + (" " * padding)

    # Agregamos las estrellas
    return base + estrellas


def _obtener_estado_usuario(user_id: int) -> Dict[str, Any]:
    return estados_usuarios.get(user_id, {})


def _guardar_estado_usuario(user_id: int, estado: Dict[str, Any]):
    estados_usuarios[user_id] = estado


# Handlers
def start(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    texto_usuario = "/start"
    estado_actual = _obtener_estado_usuario(user_id)

    respuesta, nuevo_estado = responder_mensaje(texto_usuario, estado_actual)
    _guardar_estado_usuario(user_id, nuevo_estado)

    update.message.reply_text(respuesta)


def comando_ajuste(update: Update, context: CallbackContext):
    """/ajuste <spot> <playa> <param> <valor>"""
    args = context.args
    if len(args) != 4:
        update.message.reply_text(
            "Uso: /ajuste <spot> <playa> <param> <valor>\n"
            "Ejemplo: /ajuste miramar centro delta_altura 0.3"
        )
        return

    spot, playa, param, valor_str = args
    try:
        valor = float(valor_str)
    except ValueError:
        update.message.reply_text("El valor debe ser numérico (ej: 0.3).")
        return

    try:
        actualizar_ajuste_param(spot, playa, param, valor)
        update.message.reply_text(
            f"Ajuste aplicado: {spot}/{playa} {param} = {valor}"
        )
    except Exception as e:
        update.message.reply_text(f"Error al aplicar ajuste: {e}")


def manejar_mensaje(update: Update, context: CallbackContext):
    if not update.message:
        return

    user_id = update.effective_user.id
    texto_usuario = update.message.text or ""

    estado_actual = _obtener_estado_usuario(user_id)
    respuesta, nuevo_estado = responder_mensaje(texto_usuario, estado_actual)
    _guardar_estado_usuario(user_id, nuevo_estado)

    update.message.reply_text(respuesta)


def main():
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("ajuste", comando_ajuste))

    # Todos los demás mensajes de texto se manejan con la misma lógica
    dp.add_handler(
        MessageHandler(Filters.text & ~Filters.command, manejar_mensaje)
    )

    Thread(target=run_keep_alive, daemon=True).start()
    print("Bot de olas iniciado. Esperando mensajes en Telegram...")
    updater.start_polling()
    updater.idle()


if __name__ == "__main__":
    main()
