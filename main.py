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
  GET https://<WEBHOOK_URL>/health → 200 OK
  El servidor tornado de PTB v13 se extiende con una ruta extra /health.
"""

import logging
import os
import sys
from threading import Thread
from http.server import BaseHTTPRequestHandler, HTTPServer

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


class HealthHandler(BaseHTTPRequestHandler):
    """Responde 200 OK en /health. Render solo expone PORT, así que
    este servidor corre en PORT+1 internamente — pero UptimeRobot
    apunta al puerto principal. Ver nota abajo."""

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        pass


def _patch_updater_with_health(updater, health_path: str = "/health"):
    """
    Agrega una ruta /health al servidor tornado interno de PTB v13.
    Debe llamarse DESPUÉS de start_webhook().
    """
    try:
        import tornado.web

        class HealthTornadoHandler(tornado.web.RequestHandler):
            def get(self):
                self.set_status(200)
                self.finish("OK")

        # El httpd interno del Updater es un tornado HTTPServer
        # Su application tiene una lista de handlers que podemos extender
        app = updater.httpd.request_callback  # tornado.web.Application
        app.add_handlers(r".*", [(r"/health", HealthTornadoHandler)])
        logger.info("Ruta /health registrada en servidor tornado de PTB")
        return True
    except Exception as e:
        logger.warning("No se pudo parchear tornado con /health: %s", e)
        return False


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

        updater.start_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=token,
            webhook_url=f"{webhook_url}/{token}",
        )

        # Parchear el servidor tornado para agregar /health
        patched = _patch_updater_with_health(updater)
        if patched:
            logger.info("Health check disponible en: %s/health", webhook_url)
        else:
            logger.warning("Health check NO disponible — UptimeRobot puede fallar")

        updater.idle()
    else:
        logger.info("Iniciando en modo POLLING (desarrollo local)")
        updater.start_polling()
        updater.idle()


if __name__ == "__main__":
    main()
