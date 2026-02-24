"""Tests del motor de scoring.

Completamente independiente de Telegram.
Ejecutar con: python -m pytest tests/ -v
O sin pytest:  python tests/test_scoring.py
"""

import json
import os
import sys
import unittest
from datetime import datetime, timezone

# Agregar el root al path para importar módulos
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.scoring.engine import (
    _energia_proxy,
    _score_periodo,
    _score_dir_swell,
    _score_viento,
    _score_marea,
    calcular_score,
    _angulo_relativo,
    _tipo_viento,
)
from core.scoring.models import (
    ForecastHour,
    SpotConfig,
    SwellData,
    TideData,
    WindData,
)
from core.windows.detector import detectar_ventanas, calcular_score_actual


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

def make_spot(
    tipo_break="beach",
    orientacion=95,
    tolerancia=45,
    marea_min=0.4,
    marea_max=1.6,
) -> SpotConfig:
    return SpotConfig(
        key="test_varese",
        nombre="Varese (test)",
        ciudad="Mar del Plata",
        pais="AR",
        region="buenos_aires",
        lat=-38.0088,
        lon=-57.5328,
        orientacion_costa_deg=orientacion,
        tolerancia_swell_deg=tolerancia,
        tipo_break=tipo_break,
        fondo="arena",
        marea_min_m=marea_min,
        marea_max_m=marea_max,
        marea_tipo_efecto="mid_better",
        swell_altura_min=0.5,
        swell_altura_max=3.0,
        swell_periodo_min=7.0,
        viento_max_offshore=35.0,
        viento_max_onshore=15.0,
    )


def make_hour(
    altura=1.2, periodo=13, dir_swell=100,
    vel_viento=10, dir_viento=270,
    nivel_marea=1.0,
    ts=None,
) -> ForecastHour:
    if ts is None:
        ts = datetime(2025, 2, 1, 8, 0, tzinfo=timezone.utc)
    return ForecastHour(
        timestamp=ts,
        swell=SwellData(altura_m=altura, periodo_s=periodo, direccion_deg=dir_swell),
        wind=WindData(velocidad_kmh=vel_viento, rafaga_kmh=vel_viento*1.3, direccion_deg=dir_viento),
        tide=TideData(nivel_m=nivel_marea),
    )


def load_fixture_forecast(spot: SpotConfig):
    """Carga el forecast de fixture y lo convierte a ForecastHour."""
    fixture_path = os.path.join(os.path.dirname(__file__), "fixtures", "sample_forecast.json")
    with open(fixture_path) as f:
        data = json.load(f)
    hours = []
    for h in data["horas"]:
        ts = datetime.fromisoformat(h["timestamp"]).replace(tzinfo=timezone.utc)
        hours.append(ForecastHour(
            timestamp=ts,
            swell=SwellData(**h["swell"]),
            wind=WindData(**h["wind"]),
            tide=TideData(**h["tide"]),
        ))
    return hours


# ------------------------------------------------------------------
# Tests de sub-funciones
# ------------------------------------------------------------------

class TestEnergia(unittest.TestCase):
    def test_energia_basica(self):
        swell = SwellData(altura_m=1.5, periodo_s=12, direccion_deg=90)
        e = _energia_proxy(swell)
        self.assertAlmostEqual(e, 1.5**2 * 12, places=5)  # 27.0

    def test_flat_tiene_energia_cero(self):
        swell = SwellData(altura_m=0.0, periodo_s=10, direccion_deg=90)
        e = _energia_proxy(swell)
        self.assertEqual(e, 0.0)

    def test_score_energia_rango(self):
        from core.scoring.engine import _score_energia
        self.assertGreaterEqual(_score_energia(0), 0.0)
        self.assertLessEqual(_score_energia(1000), 1.0)  # no supera 1
        self.assertGreater(_score_energia(50), _score_energia(10))  # monótona


class TestPeriodo(unittest.TestCase):
    def test_windchop_bajo(self):
        self.assertLess(_score_periodo(5), 0.35)

    def test_groundswell_alto(self):
        self.assertGreater(_score_periodo(16), 0.95)

    def test_monotono(self):
        scores = [_score_periodo(t) for t in [4, 7, 10, 13, 16]]
        for i in range(len(scores) - 1):
            self.assertLessEqual(scores[i], scores[i+1])


class TestDireccionSwell(unittest.TestCase):
    def test_perfecto_central(self):
        """Swell que viene exactamente de frente → score máximo."""
        s = _score_dir_swell(dir_swell=95, orientacion_costa=95, tolerancia=45)
        self.assertGreater(s, 0.9)

    def test_oblicuo(self):
        """Swell muy oblicuo a la costa → score bajo."""
        s = _score_dir_swell(dir_swell=5, orientacion_costa=95, tolerancia=45)
        self.assertLess(s, 0.50)  # Al menos peor que las condiciones aceptables

    def test_angulo_relativo_180(self):
        """Swell de espaldas → diferencia = 180."""
        diff = _angulo_relativo(275, 95)
        self.assertAlmostEqual(diff, 180.0, delta=1.0)


class TestViento(unittest.TestCase):
    def test_offshore_calmo(self):
        """Offshore suave → score casi perfecto."""
        wind = WindData(velocidad_kmh=10, rafaga_kmh=14, direccion_deg=275)
        s = _score_viento(wind, orientacion_costa=95)
        self.assertGreater(s, 0.90)

    def test_onshore_fuerte(self):
        """Onshore fuerte → score muy bajo."""
        wind = WindData(velocidad_kmh=30, rafaga_kmh=40, direccion_deg=90)
        s = _score_viento(wind, orientacion_costa=95)
        self.assertLess(s, 0.20)

    def test_tipo_viento_offshore(self):
        tipo = _tipo_viento(dir_viento=280, orientacion_costa=90)
        self.assertEqual(tipo, "offshore")

    def test_tipo_viento_onshore(self):
        tipo = _tipo_viento(dir_viento=90, orientacion_costa=90)
        self.assertEqual(tipo, "onshore")

    def test_calmo_siempre_bueno(self):
        for dir_v in [0, 90, 180, 270]:
            wind = WindData(velocidad_kmh=3, rafaga_kmh=5, direccion_deg=dir_v)
            self.assertEqual(_score_viento(wind, 90), 1.0)


class TestMarea(unittest.TestCase):
    def test_en_rango_optimo(self):
        spot = make_spot(marea_min=0.4, marea_max=1.6)
        tide = TideData(nivel_m=1.0)
        s = _score_marea(tide, spot)
        self.assertGreater(s, 0.85)

    def test_fuera_rango_bajo(self):
        spot = make_spot(marea_min=0.4, marea_max=1.6)
        tide = TideData(nivel_m=0.0)
        s = _score_marea(tide, spot)
        self.assertLess(s, 0.85)  # Fuera del rango → penalizado

    def test_fuera_rango_alto(self):
        spot = make_spot(marea_min=0.4, marea_max=1.6)
        tide = TideData(nivel_m=3.0)
        s = _score_marea(tide, spot)
        self.assertLess(s, 0.50)

    def test_no_negativo(self):
        spot = make_spot(marea_min=0.4, marea_max=1.6)
        tide = TideData(nivel_m=10.0)
        self.assertGreaterEqual(_score_marea(tide, spot), 0.0)


# ------------------------------------------------------------------
# Tests del score total
# ------------------------------------------------------------------

class TestScoreTotal(unittest.TestCase):
    def test_condiciones_ideales(self):
        """Offshore + groundswell + marea ok → score alto."""
        spot = make_spot()
        hour = make_hour(altura=1.4, periodo=14, dir_swell=95, vel_viento=8, dir_viento=275, nivel_marea=1.0)
        bd = calcular_score(hour, spot)
        self.assertGreater(bd.score_100, 70)

    def test_condiciones_malas(self):
        """Onshore fuerte + windchop + flat → score bajo."""
        spot = make_spot()
        hour = make_hour(altura=0.3, periodo=5, dir_swell=90, vel_viento=35, dir_viento=90, nivel_marea=2.5)
        bd = calcular_score(hour, spot)
        self.assertLess(bd.score_100, 35)

    def test_score_rango(self):
        """El score siempre debe estar entre 0 y 100."""
        spot = make_spot()
        for altura in [0.1, 0.5, 1.0, 2.0, 4.0]:
            for periodo in [4, 8, 12, 16]:
                hour = make_hour(altura=altura, periodo=periodo)
                bd = calcular_score(hour, spot)
                self.assertGreaterEqual(bd.score_100, 0)
                self.assertLessEqual(bd.score_100, 100)

    def test_breakdown_tiene_flags(self):
        spot = make_spot()
        hour = make_hour()
        bd = calcular_score(hour, spot)
        total_flags = len(bd.flags_positivos) + len(bd.flags_negativos) + len(bd.flags_neutros)
        self.assertGreater(total_flags, 0)

    def test_pesos_por_tipo_break(self):
        """Reef debe penalizar más el período que beach."""
        beach = make_spot(tipo_break="beach")
        reef = make_spot(tipo_break="reef")
        hour_windchop = make_hour(periodo=6)

        s_beach = calcular_score(hour_windchop, beach)
        s_reef = calcular_score(hour_windchop, reef)
        # Reef da más peso al período → score más bajo con windchop
        self.assertLessEqual(s_reef.score_100, s_beach.score_100 + 5)


# ------------------------------------------------------------------
# Tests del detector de ventanas
# ------------------------------------------------------------------

class TestDetectorVentanas(unittest.TestCase):
    def test_ventanas_desde_fixture(self):
        spot = make_spot()
        forecast = load_fixture_forecast(spot)
        ventanas = detectar_ventanas(forecast, spot, umbral=0.50)
        # Al menos una ventana con las condiciones del fixture (mañana offshore + 14s)
        self.assertGreater(len(ventanas), 0)

    def test_ventana_tiene_descripcion(self):
        spot = make_spot()
        forecast = load_fixture_forecast(spot)
        ventanas = detectar_ventanas(forecast, spot, umbral=0.50)
        if ventanas:
            self.assertIsInstance(ventanas[0].descripcion, str)
            self.assertGreater(len(ventanas[0].descripcion), 5)

    def test_ventanas_ordenadas_por_score(self):
        spot = make_spot()
        forecast = load_fixture_forecast(spot)
        ventanas = detectar_ventanas(forecast, spot, umbral=0.40)
        scores = [v.score_promedio for v in ventanas]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_sin_datos_retorna_lista_vacia(self):
        spot = make_spot()
        ventanas = detectar_ventanas([], spot)
        self.assertEqual(ventanas, [])

    def test_calcular_score_actual(self):
        spot = make_spot()
        forecast = load_fixture_forecast(spot)
        hour, bd = calcular_score_actual(forecast, spot)
        # Con el fixture no hay "ahora real", pero debe retornar algo
        self.assertIsNotNone(hour)
        self.assertIsNotNone(bd)


if __name__ == "__main__":
    unittest.main(verbosity=2)
