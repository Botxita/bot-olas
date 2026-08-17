"""Persistencia de sesiones de usuario con SQLite.

Almacena el estado de conversación por user_id de Telegram.
Sobrevive reinicios del proceso (disco persistente en Render).

Estructura del estado:
{
    "step": "seleccion_pais" | "seleccion_region" | "seleccion_spot" | "menu_spot",
    "pais": "ar",
    "region": "buenos_aires",
    "spot_key": "mdq_varese",
    "plan": "free" | "pro",
    "idioma": "es" | "en" | "pt",
    "favoritos": ["mdq_varese", "chapa_general"],
    "nivel": "principiante" | "intermedio" | "avanzado",
}

Para Postgres en producción:
  Cambiar la clase por PostgresSessionStore e inyectarla.
  La interface es idéntica.
"""

import json
import logging
import os
import sqlite3
import threading
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DB_PATH = os.getenv("SESSION_DB_PATH", "data/sessions.db")

NIVEL_DEFAULT = "intermedio"
NIVELES_VALIDOS = ("principiante", "intermedio", "avanzado")


class SQLiteSessionStore:
    """
    Persistencia de sesiones en SQLite.
    Thread-safe gracias a threading.local() para conexiones.
    """

    def __init__(self, db_path: str = _DB_PATH):
        self.db_path = db_path
        self._local = threading.local()
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(
                self.db_path,
                check_same_thread=False,
                isolation_level=None,  # autocommit
            )
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                user_id     INTEGER PRIMARY KEY,
                estado      TEXT    NOT NULL DEFAULT '{}',
                created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
                updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS spot_adjustments (
                spot_key    TEXT    NOT NULL,
                param       TEXT    NOT NULL,
                valor       REAL    NOT NULL,
                updated_at  TEXT    NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (spot_key, param)
            );
            CREATE TABLE IF NOT EXISTS user_favorites (
                user_id     INTEGER NOT NULL,
                spot_key    TEXT    NOT NULL,
                PRIMARY KEY (user_id, spot_key)
            );
        """)
        conn.commit()
        conn.close()

    # ------------------------------------------------------------------
    # Sesiones
    # ------------------------------------------------------------------

    def get_session(self, user_id: int) -> Dict[str, Any]:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT estado FROM sessions WHERE user_id = ?", (user_id,)
        ).fetchone()
        if row is None:
            return {}
        try:
            return json.loads(row["estado"])
        except json.JSONDecodeError:
            return {}

    def set_session(self, user_id: int, estado: Dict[str, Any]):
        conn = self._get_conn()
        conn.execute("""
            INSERT INTO sessions (user_id, estado, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(user_id) DO UPDATE SET
                estado = excluded.estado,
                updated_at = excluded.updated_at
        """, (user_id, json.dumps(estado, ensure_ascii=False)))

    def update_session(self, user_id: int, **kwargs):
        """Actualiza campos individuales del estado sin sobreescribir el resto."""
        estado = self.get_session(user_id)
        estado.update(kwargs)
        self.set_session(user_id, estado)

    def clear_session(self, user_id: int):
        conn = self._get_conn()
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))

    # ------------------------------------------------------------------
    # Ajustes de spot (reemplaza ajustes_spots.py)
    # ------------------------------------------------------------------

    def get_spot_adjustment(self, spot_key: str, param: str) -> Optional[float]:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT valor FROM spot_adjustments WHERE spot_key = ? AND param = ?",
            (spot_key, param)
        ).fetchone()
        return float(row["valor"]) if row else None

    def set_spot_adjustment(self, spot_key: str, param: str, valor: float):
        conn = self._get_conn()
        conn.execute("""
            INSERT INTO spot_adjustments (spot_key, param, valor, updated_at)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(spot_key, param) DO UPDATE SET
                valor = excluded.valor,
                updated_at = excluded.updated_at
        """, (spot_key, param, valor))

    def get_all_spot_adjustments(self) -> Dict[str, Dict[str, float]]:
        conn = self._get_conn()
        rows = conn.execute("SELECT spot_key, param, valor FROM spot_adjustments").fetchall()
        result: Dict[str, Dict[str, float]] = {}
        for row in rows:
            if row["spot_key"] not in result:
                result[row["spot_key"]] = {}
            result[row["spot_key"]][row["param"]] = float(row["valor"])
        return result

    # ------------------------------------------------------------------
    # Favoritos (feature Pro)
    # ------------------------------------------------------------------

    def get_favoritos(self, user_id: int) -> List[str]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT spot_key FROM user_favorites WHERE user_id = ?", (user_id,)
        ).fetchall()
        return [r["spot_key"] for r in rows]

    def add_favorito(self, user_id: int, spot_key: str):
        conn = self._get_conn()
        conn.execute(
            "INSERT OR IGNORE INTO user_favorites (user_id, spot_key) VALUES (?, ?)",
            (user_id, spot_key)
        )

    def remove_favorito(self, user_id: int, spot_key: str):
        conn = self._get_conn()
        conn.execute(
            "DELETE FROM user_favorites WHERE user_id = ? AND spot_key = ?",
            (user_id, spot_key)
        )

    # ------------------------------------------------------------------
    # Nivel de surf (#A2) — afecta umbrales de recomendación, no el score
    # ------------------------------------------------------------------

    def tiene_nivel(self, user_id: int) -> bool:
        """True si el usuario ya eligió nivel alguna vez (gate del onboarding)."""
        return "nivel" in self.get_session(user_id)

    def get_nivel(self, user_id: int) -> str:
        """Nivel guardado, o NIVEL_DEFAULT si nunca se eligió."""
        return self.get_session(user_id).get("nivel", NIVEL_DEFAULT)

    def set_nivel(self, user_id: int, nivel: str):
        if nivel not in NIVELES_VALIDOS:
            raise ValueError(f"nivel inválido: {nivel!r} (válidos: {NIVELES_VALIDOS})")
        self.update_session(user_id, nivel=nivel)


# Instancia global
session_store = SQLiteSessionStore()
