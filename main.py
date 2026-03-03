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
"""

import logging
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

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
    """Servidor HTTP mínimo que responde 200 OK en /health."""

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # Silenciar logs de acceso HTTP


def _start_health_server(port: int):
    """Arranca el servidor de health en un thread separado."""
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info("Health server escuchando en :%d/health", port)


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

        # Health endpoint en puerto 8080 para UptimeRobot
        _start_health_server(8080)

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
