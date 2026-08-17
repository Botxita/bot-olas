"""Tests unitarios para persistence/session_store.py (foco: nivel de surf, #A2).

Cada test usa una instancia propia de SQLiteSessionStore sobre un archivo
temporal (fixture `store`), para no compartir estado entre tests ni tocar
data/sessions.db real.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from persistence.session_store import SQLiteSessionStore, NIVEL_DEFAULT, NIVELES_VALIDOS


@pytest.fixture
def store(tmp_path):
    db_path = str(tmp_path / "sessions_test.db")
    return SQLiteSessionStore(db_path)


USER_ID = 12345


def test_tiene_nivel_false_antes_de_elegir(store):
    assert store.tiene_nivel(USER_ID) is False


def test_get_nivel_default_es_intermedio(store):
    assert store.get_nivel(USER_ID) == NIVEL_DEFAULT == "intermedio"


def test_set_nivel_lo_guarda(store):
    store.set_nivel(USER_ID, "principiante")
    assert store.get_nivel(USER_ID) == "principiante"


def test_set_nivel_hace_que_tiene_nivel_pase_a_true(store):
    assert store.tiene_nivel(USER_ID) is False
    store.set_nivel(USER_ID, "avanzado")
    assert store.tiene_nivel(USER_ID) is True


def test_set_nivel_invalido_lanza_valueerror(store):
    with pytest.raises(ValueError):
        store.set_nivel(USER_ID, "experto")


@pytest.mark.parametrize("nivel", NIVELES_VALIDOS)
def test_set_nivel_acepta_los_3_valores_validos(store, nivel):
    store.set_nivel(USER_ID, nivel)
    assert store.get_nivel(USER_ID) == nivel


def test_set_nivel_preserva_otras_claves_del_estado(store):
    """update_session() (usado internamente por set_nivel) no debe pisar
    otras claves ya guardadas en el mismo estado por user_id."""
    store.update_session(USER_ID, step="menu_spot", spot_key="mdq_varese")
    store.set_nivel(USER_ID, "principiante")
    estado = store.get_session(USER_ID)
    assert estado["step"] == "menu_spot"
    assert estado["spot_key"] == "mdq_varese"
    assert estado["nivel"] == "principiante"


def test_set_nivel_no_afecta_a_otro_usuario(store):
    store.set_nivel(USER_ID, "avanzado")
    otro_user = USER_ID + 1
    assert store.tiene_nivel(otro_user) is False
    assert store.get_nivel(otro_user) == NIVEL_DEFAULT


def test_get_nivel_refleja_el_ultimo_valor_seteado(store):
    store.set_nivel(USER_ID, "principiante")
    store.set_nivel(USER_ID, "avanzado")
    assert store.get_nivel(USER_ID) == "avanzado"
