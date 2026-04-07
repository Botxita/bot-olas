"""Entry point principal — Olas Surfer Bot V2.

Soporta dos modos:
  - polling:  desarrollo local (sin webhook)
  - webhook:  producción en Render

Variables de entorno requeridas:
  TELEGRAM_BOT_TOKEN   → obligatorio
  WEBHOOK_URL          → obligatorio en modo webhook (ej: https://mi-app.onrender.com)
  PORT                 → puerto para el webhook (Render lo asigna automáticamente)

Variables opcionales:
  SESSION_DB_PATH      → ruta del SQLite (default: data/sessions.db)
  FORECAST_CACHE_TTL_SECONDS → TTL del caché (default: 1800)
  REDIS_URL            → si existe, usa Redis en lugar de in-memory cache
  ADMIN_USER_IDS       → IDs separados por coma con acceso a /ajuste
  STORMGLASS_API_KEY   → para usar proveedor Stormglass (futuro)
  WORLDTIDES_API_KEY   → para usar WorldTides (futuro)

Health check para UptimeRobot:
  PTB v13 start_webhook responde 200 en cualquier ruta que no sea el token.
  Apuntar UptimeRobot a: https://<WEBHOOK_URL>/health  (método GET, esperar 200)
  No se necesita servidor separado.
"""

import logging
import os
import sys

from dotenv import load_dotenv
from telegram.ext import Updater

from bot.handlers.main import register_handlers

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# Cargar .env
load_dotenv()


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN no configurado. Crear .env basado en .env.example")
        sys.exit(1)

    webhook_url = os.getenv("WEBHOOK_URL", "")
    port = int(os.getenv("PORT", "10000"))
    use_webhook = bool(webhook_url)

    updater = Updater(token, use_context=True)
    dp = updater.dispatcher
    register_handlers(dp)

    if use_webhook:
        logger.info("Iniciando en modo WEBHOOK → %s (port %d)", webhook_url, port)
        logger.info(
            "Health check para UptimeRobot: %s/health (GET → 200 OK)", webhook_url
        )

        # PTB v13 start_webhook levanta un servidor tornado que responde 200
        # en cualquier ruta que no sea /<token>. No se necesita servidor auxiliar.
        # UptimeRobot debe apuntar a: https://<WEBHOOK_URL>/health
        updater.start_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=token,
            webhook_url=f"{webhook_url}/{token}",
        )
        updater.idle()
    else:
        logger.info("Iniciando en modo POLLING (desarrollo local)")
        updater.start_polling()
        updater.idle()


if __name__ == "__main__":
    main()
