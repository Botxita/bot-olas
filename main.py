"""Entry point principal — Olas Surfer Bot V2."""

import logging
import os
import sys

from dotenv import load_dotenv
from telegram.ext import Updater

from bot.handlers.main import register_handlers

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

load_dotenv()


def _patch_health(updater):
    try:
        import tornado.web

        class HealthHandler(tornado.web.RequestHandler):
            def get(self):
                self.set_status(200)
                self.finish("OK")

            def head(self):
                self.set_status(200)
                self.finish()

        webhook_server = updater.httpd
        http_server = webhook_server.http_server
        app = http_server.request_callback

        app.add_handlers(r".*", [(r"/health", HealthHandler)])
        logger.info("✅ Ruta /health registrada (GET + HEAD)")
        return True

    except Exception as e:
        logger.warning("No se pudo parchear /health: %s", e)
        return False


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN no configurado.")
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

        patched = _patch_health(updater)
        if patched:
            logger.info("Health check: %s/health", webhook_url)
        else:
            logger.warning("⚠️ /health no disponible")

        updater.idle()
    else:
        logger.info("Iniciando en modo POLLING (desarrollo local)")
        updater.start_polling()
        updater.idle()


if __name__ == "__main__":
    main()
