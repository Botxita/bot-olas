"""Tests del motor de scoring.

Completamente independiente de Telegram.
Ejecutar con: python -m pytest tests/ -v
O sin pytest:  python tests/test_scoring.py
"""

import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

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
    _mejor_diff_direccion,
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
from core.spots.registry import get_spot


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

def make_spot(
    tipo_break="beach",
    orientacion=95,
    tolerancia=45,
    marea_min=0.4,
    marea_max=1.6,
    marea_tipo_efecto="mid_better",
    direcciones_ideales=None,
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
        marea_tipo_efecto=marea_tipo_efecto,
        swell_altura_min=0.5,
        swell_altura_max=3.0,
        swell_periodo_min=7.0,
        viento_max_offshore=35.0,
        viento_max_onshore=15.0,
        direcciones_ideales=direcciones_ideales or [],
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
        diff = _angulo_relativo(95, 95)
        s = _score_dir_swell(diff=diff, tolerancia=45)
        self.assertGreater(s, 0.9)

    def test_oblicuo(self):
        """Swell muy oblicuo a la costa → score bajo."""
        diff = _angulo_relativo(5, 95)
        s = _score_dir_swell(diff=diff, tolerancia=45)
        self.assertLess(s, 0.50)  # Al menos peor que las condiciones aceptables

    def test_angulo_relativo_180(self):
        """Swell de espaldas → diferencia = 180."""
        diff = _angulo_relativo(275, 95)
        self.assertAlmostEqual(diff, 180.0, delta=1.0)

    def test_direccion_ideal_de_la_lista_distinta_de_orientacion(self):
        """Con direcciones_ideales configuradas, un swell que coincide con
        una de ellas (no con orientacion_costa_deg) debe dar diff=0."""
        spot = make_spot(orientacion=95, direcciones_ideales=[90, 135, 180])
        diff = _mejor_diff_direccion(180, spot)
        self.assertEqual(diff, 0.0)

    def test_selecciona_la_menor_diferencia_entre_varios_ideales(self):
        """Debe usar el ideal más cercano, no el primero ni el promedio."""
        spot = make_spot(orientacion=95, direcciones_ideales=[0, 180])
        # 170 está a 10° de 180 y a 170° de 0 → debe ganar el de 180
        diff = _mejor_diff_direccion(170, spot)
        self.assertAlmostEqual(diff, 10.0, delta=0.01)

    def test_lista_vacia_cae_a_orientacion_costa(self):
        """Sin direcciones_ideales configuradas, el fallback es
        orientacion_costa_deg — mismo resultado que el comportamiento previo."""
        spot = make_spot(orientacion=95, direcciones_ideales=[])
        diff_fallback = _mejor_diff_direccion(20, spot)
        diff_manual = _angulo_relativo(20, 95)
        self.assertAlmostEqual(diff_fallback, diff_manual, delta=0.01)

    def test_ideal_unico_igual_a_orientacion_preserva_comportamiento_viejo(self):
        """Una lista de un solo elemento igual a orientacion_costa_deg
        debe comportarse exactamente igual que el fallback."""
        spot_con_lista = make_spot(orientacion=95, direcciones_ideales=[95])
        spot_sin_lista = make_spot(orientacion=95, direcciones_ideales=[])
        for swell_dir in (30, 95, 200, 350):
            self.assertAlmostEqual(
                _mejor_diff_direccion(swell_dir, spot_con_lista),
                _mejor_diff_direccion(swell_dir, spot_sin_lista),
                delta=0.01,
            )

    def test_registry_carga_direcciones_ideales_reales(self):
        """Confirma que el fix realmente conecta con la config real de
        producción, no solo con spots de test armados a mano."""
        spot = get_spot("mdq_playa_grande")
        self.assertEqual(spot.direcciones_ideales, [90, 120, 150])


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

    def test_mid_better_simetrico_alrededor_del_centro(self):
        """Comportamiento por default (sin cambios): mejor en el centro."""
        spot = make_spot(marea_min=0.4, marea_max=1.6, marea_tipo_efecto="mid_better")
        centro = _score_marea(TideData(nivel_m=1.0), spot)
        borde_bajo = _score_marea(TideData(nivel_m=0.4), spot)
        borde_alto = _score_marea(TideData(nivel_m=1.6), spot)
        self.assertGreater(centro, borde_bajo)
        self.assertGreater(centro, borde_alto)
        self.assertAlmostEqual(borde_bajo, borde_alto, delta=0.001)

    def test_low_better_mejor_en_marea_baja(self):
        """
        Regresión #1: un spot low_better debe puntuar mejor con marea baja
        que con marea alta dentro del rango — hoy (antes del fix) puntuaban
        igual porque el score era simétrico al centro sin importar el tipo.
        """
        spot = make_spot(marea_min=0.3, marea_max=1.4, marea_tipo_efecto="low_better")
        s_baja = _score_marea(TideData(nivel_m=0.3), spot)   # marea_min_m
        s_centro = _score_marea(TideData(nivel_m=0.85), spot)
        s_alta = _score_marea(TideData(nivel_m=1.4), spot)   # marea_max_m
        self.assertGreater(s_baja, s_centro)
        self.assertGreater(s_centro, s_alta)
        self.assertAlmostEqual(s_baja, 1.0, delta=0.001)

    def test_high_better_mejor_en_marea_alta(self):
        """Simétrico al caso low_better, pero favoreciendo marea alta."""
        spot = make_spot(marea_min=0.3, marea_max=1.4, marea_tipo_efecto="high_better")
        s_baja = _score_marea(TideData(nivel_m=0.3), spot)
        s_centro = _score_marea(TideData(nivel_m=0.85), spot)
        s_alta = _score_marea(TideData(nivel_m=1.4), spot)
        self.assertGreater(s_alta, s_centro)
        self.assertGreater(s_centro, s_baja)
        self.assertAlmostEqual(s_alta, 1.0, delta=0.001)

    def test_low_better_fuera_de_rango_hacia_abajo_no_penaliza(self):
        """
        Regresión #1 (parte 2): _generar_flags() marca una marea por debajo
        de marea_min_m en un spot low_better como flag POSITIVO ("bueno
        aquí"), no neutro/negativo. El score debe ser consistente con eso:
        no penalizar esa dirección, igual que en el borde marea_min_m.
        """
        spot = make_spot(marea_min=0.4, marea_max=1.6, marea_tipo_efecto="low_better")
        s_en_el_borde = _score_marea(TideData(nivel_m=0.4), spot)
        s_mas_abajo = _score_marea(TideData(nivel_m=0.1), spot)
        s_mucho_mas_abajo = _score_marea(TideData(nivel_m=-0.5), spot)
        self.assertAlmostEqual(s_en_el_borde, 1.0, delta=0.001)
        self.assertAlmostEqual(s_mas_abajo, 1.0, delta=0.001)
        self.assertAlmostEqual(s_mucho_mas_abajo, 1.0, delta=0.001)

    def test_low_better_fuera_de_rango_hacia_arriba_si_penaliza(self):
        """La dirección opuesta (marea alta) en un spot low_better sigue
        penalizada — solo se exime la dirección que el flag marca como buena."""
        spot = make_spot(marea_min=0.4, marea_max=1.6, marea_tipo_efecto="low_better")
        s_en_el_borde = _score_marea(TideData(nivel_m=1.6), spot)
        s_mas_arriba = _score_marea(TideData(nivel_m=3.0), spot)
        self.assertLess(s_mas_arriba, s_en_el_borde)

    def test_high_better_fuera_de_rango_hacia_arriba_no_penaliza(self):
        """Simétrico: high_better no penaliza marea por encima de marea_max_m."""
        spot = make_spot(marea_min=0.4, marea_max=1.6, marea_tipo_efecto="high_better")
        s_en_el_borde = _score_marea(TideData(nivel_m=1.6), spot)
        s_mas_arriba = _score_marea(TideData(nivel_m=3.0), spot)
        self.assertAlmostEqual(s_en_el_borde, 1.0, delta=0.001)
        self.assertAlmostEqual(s_mas_arriba, 1.0, delta=0.001)

    def test_high_better_fuera_de_rango_hacia_abajo_si_penaliza(self):
        spot = make_spot(marea_min=0.4, marea_max=1.6, marea_tipo_efecto="high_better")
        s_en_el_borde = _score_marea(TideData(nivel_m=0.4), spot)
        s_mas_abajo = _score_marea(TideData(nivel_m=-1.0), spot)
        self.assertLess(s_mas_abajo, s_en_el_borde)

    def test_mid_better_fuera_de_rango_penaliza_ambos_lados(self):
        """mid_better (default, sin cambios): ninguna dirección se exime."""
        spot = make_spot(marea_min=0.4, marea_max=1.6, marea_tipo_efecto="mid_better")
        s_abajo = _score_marea(TideData(nivel_m=0.0), spot)
        s_arriba = _score_marea(TideData(nivel_m=2.0), spot)
        self.assertLess(s_abajo, 1.0)
        self.assertLess(s_arriba, 1.0)


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

    def test_horizonte_48h_excluye_ventanas_lejanas(self):
        """
        Regresión #22: aunque el forecast recibido tenga más de 48h de datos
        (Open-Meteo puede devolver hasta 7 días y los handlers no recortan),
        el detector no debe generar ventanas más allá del horizonte declarado.
        """
        spot = make_spot()
        ahora = datetime.now(timezone.utc)
        # Hora "cercana": mañana a las 15:00 UTC (~mediodía en AR), bien dentro de 48h.
        ts_cercana = (ahora + timedelta(days=1)).replace(hour=15, minute=0, second=0, microsecond=0)
        # Hora "lejana": una semana después a la misma hora UTC — fuera del horizonte de 48h.
        ts_lejana = ts_cercana + timedelta(days=6)

        condiciones_ideales = dict(
            altura=1.4, periodo=14, dir_swell=95, vel_viento=8, dir_viento=275, nivel_marea=1.0
        )
        forecast = [
            make_hour(ts=ts_cercana, **condiciones_ideales),
            make_hour(ts=ts_lejana, **condiciones_ideales),
        ]

        ventanas = detectar_ventanas(forecast, spot, umbral=0.60)

        limite_48h = ahora + timedelta(hours=48)
        self.assertTrue(
            all(v.fin <= limite_48h for v in ventanas),
            "No debería haber ventanas más allá del horizonte de 48h",
        )
        # La hora cercana, con condiciones ideales, sí debe generar ventana.
        self.assertTrue(any(v.inicio == ts_cercana for v in ventanas))

    def test_horizonte_configurable(self):
        """
        horizonte_horas debe ser ajustable por el caller, no fijo en 48.

        Usa un margen grande (~7 días vs. 10 días de horizonte) a propósito:
        con un margen chico (ej. 72h) el offset real entre "ahora" y un
        timestamp construido con día+hora fija puede variar hasta ~24h según
        la hora UTC en que corra el test, generando un test flaky en el borde.
        Con ~168h de por medio esa variación es irrelevante.
        """
        spot = make_spot()
        ahora = datetime.now(timezone.utc)
        # ~7 días en el futuro, a las 15:00 UTC (mediodía AR, daylight seguro).
        ts_lejos = (ahora + timedelta(days=7)).replace(hour=15, minute=0, second=0, microsecond=0)

        condiciones_ideales = dict(
            altura=1.4, periodo=14, dir_swell=95, vel_viento=8, dir_viento=275, nivel_marea=1.0
        )
        forecast = [make_hour(ts=ts_lejos, **condiciones_ideales)]

        # Con horizonte default (48h) esta hora queda afuera.
        self.assertEqual(detectar_ventanas(forecast, spot, umbral=0.60), [])
        # Con horizonte extendido a 10 días, debe aparecer.
        ventanas_extendidas = detectar_ventanas(forecast, spot, umbral=0.60, horizonte_horas=24 * 10)
        self.assertTrue(any(v.inicio == ts_lejos for v in ventanas_extendidas))

    def test_calcular_score_actual(self):
        spot = make_spot()
        forecast = load_fixture_forecast(spot)
        hour, bd = calcular_score_actual(forecast, spot)
        # Con el fixture no hay "ahora real", pero debe retornar algo
        self.assertIsNotNone(hour)
        self.assertIsNotNone(bd)


if __name__ == "__main__":
    unittest.main(verbosity=2)
