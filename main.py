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
    """
    Inyecta /health en el servidor tornado de PTB v13.
    PTB v13 usa telegram.utils.webhookhandler.WebhookServer,
    que internamente tiene un atributo `application` (tornado.web.Application).
    """
    try:
        import tornado.web

        class HealthHandler(tornado.web.RequestHandler):
            def get(self):
                self.set_status(200)
                self.finish("OK")

        # WebhookServer hereda de tornado.httpserver.HTTPServer
        # Su aplicación tornado está en ._impl o en .request_callback
        # Probamos los atributos conocidos de PTB v13
        webhook_server = updater.httpd  # es un WebhookServer

        # Buscar la tornado.web.Application en el objeto
        app = None
        for attr in ["application", "request_callback", "_impl"]:
            candidate = getattr(webhook_server, attr, None)
            if candidate is not None and hasattr(candidate, "add_handlers"):
                app = candidate
                break
            # A veces está un nivel más adentro
            if candidate is not None and hasattr(candidate, "application"):
                inner = getattr(candidate, "application", None)
                if inner is not None and hasattr(inner, "add_handlers"):
                    app = inner
                    break

        if app is None:
            # Último recurso: buscar en __dict__ recursivamente
            def find_app(obj, depth=0):
                if depth > 3:
                    return None
                for v in vars(obj).values():
                    if hasattr(v, "add_handlers"):
                        return v
                    if hasattr(v, "__dict__"):
                        result = find_app(v, depth + 1)
                        if result:
                            return result
                return None
            app = find_app(webhook_server)

        if app is None:
            logger.warning("No se encontró tornado.web.Application en WebhookServer")
            return False

        app.add_handlers(r".*", [(r"/health", HealthHandler)])
        logger.info("✅ Ruta /health registrada correctamente")
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

        # Log estructura interna para debug
        webhook_server = updater.httpd
        logger.info("WebhookServer type: %s", type(webhook_server))
        logger.info("WebhookServer attrs: %s", [a for a in dir(webhook_server) if not a.startswith("__")])

        patched = _patch_health(updater)
        if not patched:
            logger.warning("⚠️ /health no disponible")

        updater.idle()
    else:
        logger.info("Iniciando en modo POLLING (desarrollo local)")
        updater.start_polling()
        updater.idle()


if __name__ == "__main__":
    main()
