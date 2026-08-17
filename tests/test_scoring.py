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
from unittest.mock import patch
from zoneinfo import ZoneInfo

# Agregar el root al path para importar módulos
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.scoring.engine import (
    _energia_proxy,
    _factor_tamano,
    _score_periodo,
    _score_dir_swell,
    _score_viento,
    _factor_rafaga,
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
    swell_periodo_min=7.0,
    viento_max_offshore=35.0,
    viento_max_onshore=15.0,
    swell_altura_max=3.0,
    delta_altura=0.0,
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
        swell_altura_max=swell_altura_max,
        swell_periodo_min=swell_periodo_min,
        viento_max_offshore=viento_max_offshore,
        viento_max_onshore=viento_max_onshore,
        direcciones_ideales=direcciones_ideales or [],
        delta_altura=delta_altura,
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

    def test_factor_tamano_no_penaliza_dentro_del_maximo(self):
        self.assertEqual(_factor_tamano(1.0, altura_max=2.0), 1.0)
        self.assertEqual(_factor_tamano(2.0, altura_max=2.0), 1.0)  # justo en el máximo

    def test_factor_tamano_penaliza_gradualmente_por_encima(self):
        """Regresión #4: a más exceso sobre el máximo del spot, más penalización."""
        f_poco = _factor_tamano(2.5, altura_max=2.0)   # 25% de exceso
        f_mucho = _factor_tamano(4.0, altura_max=2.0)  # 100% de exceso
        self.assertLess(f_mucho, f_poco)
        self.assertLess(f_poco, 1.0)

    def test_factor_tamano_no_llega_a_cero(self):
        f = _factor_tamano(20.0, altura_max=2.0)  # exceso extremo
        self.assertGreaterEqual(f, 0.3)

    def test_factor_tamano_maneja_maximo_cero_sin_crashear(self):
        """Config incompleta (altura_max=0) no debe dividir por cero."""
        self.assertEqual(_factor_tamano(1.0, altura_max=0.0), 1.0)


class TestPeriodo(unittest.TestCase):
    def test_windchop_bajo(self):
        self.assertLess(_score_periodo(5), 0.35)

    def test_groundswell_alto(self):
        self.assertGreater(_score_periodo(16), 0.95)

    def test_monotono(self):
        scores = [_score_periodo(t) for t in [4, 7, 10, 13, 16]]
        for i in range(len(scores) - 1):
            self.assertLessEqual(scores[i], scores[i+1])

    def test_periodo_min_default_preserva_curva_original(self):
        """periodo_min=7.0 (default de la función, ancla de la curva) debe
        dar exactamente el mismo resultado que no pasar el parámetro."""
        for T in [4, 6, 8, 10, 13, 16, 18]:
            self.assertEqual(_score_periodo(T), _score_periodo(T, periodo_min=7.0))

    def test_periodo_igual_a_periodo_min_entra_en_usable(self):
        """
        Regresión #3 (parte del hallazgo de Codex): T == periodo_min debe
        caer en el balde "usable" (0.55), no en "windchop" (0.30) —
        independientemente de qué tan alto sea periodo_min. Un ancla mal
        elegida (ej. en el default del registry, 6.0) hacía que T==periodo_min
        cayera siempre en 0.30 para cualquier spot que sí especifica el campo.
        """
        for periodo_min in (5.0, 7.0, 9.0, 12.0, 15.0):
            self.assertAlmostEqual(
                _score_periodo(periodo_min, periodo_min=periodo_min), 0.55, delta=0.001,
                msg=f"periodo_min={periodo_min}",
            )

    def test_periodo_min_mas_alto_penaliza_el_mismo_periodo(self):
        """
        Regresión #3: un reef con periodo_min más alto que el estándar debe
        considerar windchop un período que un spot estándar consideraría usable.
        """
        T = 8.0
        s_estandar = _score_periodo(T, periodo_min=7.0)
        s_reef_exigente = _score_periodo(T, periodo_min=11.0)
        self.assertLess(s_reef_exigente, s_estandar)

    def test_periodo_min_mas_bajo_favorece_el_mismo_periodo(self):
        T = 8.0
        s_estandar = _score_periodo(T, periodo_min=7.0)
        s_tolerante = _score_periodo(T, periodo_min=4.0)
        self.assertGreater(s_tolerante, s_estandar)


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

    def test_tolerancia_cero_alineacion_perfecta_no_crashea(self):
        """
        Regresión #10: tolerancia_swell_deg=0 con diff=0 calculaba 0/0
        (ZeroDivisionError) en la rama de bonus. Ningún spot real usa esto
        (rango real 30°-60°), pero el registry no valida positividad.
        """
        s = _score_dir_swell(diff=0, tolerancia=0)
        self.assertEqual(s, 1.0)

    def test_tolerancia_cero_no_alineado_no_crashea(self):
        """Con tolerancia=0 y diff>0, debe usar la curva de 'muy oblicuo'
        directamente (no hay ventana de bonus posible con tolerancia 0)."""
        s = _score_dir_swell(diff=30, tolerancia=0)
        self.assertAlmostEqual(s, 0.11, delta=0.001)  # 0.20 - 0.003*30

    def test_tolerancia_negativa_no_crashea(self):
        """Tolerancia negativa (config claramente rota) tampoco debe crashear."""
        s = _score_dir_swell(diff=10, tolerancia=-5)
        self.assertGreaterEqual(s, 0.0)
        self.assertLessEqual(s, 1.0)

    def test_continuidad_en_el_viejo_borde_2_5x_tolerancia(self):
        """
        Regresión #6: antes había un salto de 0.20 a 0.05 justo en
        diff == 2.5*tolerancia (dos ramas con fórmulas sin relación entre
        sí). Ahora es una sola curva continua — un desvío mínimo cerca de
        ese punto no debe cambiar el score de forma abrupta.
        """
        tolerancia = 45
        borde = tolerancia * 2.5
        s_en_el_borde = _score_dir_swell(diff=borde, tolerancia=tolerancia)
        s_justo_despues = _score_dir_swell(diff=borde + 0.1, tolerancia=tolerancia)
        self.assertAlmostEqual(s_en_el_borde, 0.20, delta=0.01)
        self.assertAlmostEqual(s_en_el_borde, s_justo_despues, delta=0.01)

    def test_piso_005_en_diff_extremo(self):
        """Un swell de espaldas (diff=180) sigue tocando el piso 0.05."""
        s = _score_dir_swell(diff=180, tolerancia=45)
        self.assertAlmostEqual(s, 0.05, delta=0.001)

    def test_monotono_decreciente_fuera_de_tolerancia(self):
        """
        Invariante de la curva nueva: el score no debe subir en ningún punto
        a medida que diff crece más allá de tolerancia. No es una regresión
        de la implementación vieja (esa también era decreciente, solo con
        un salto abrupto en 2.5*tolerancia en vez de una curva continua) —
        es una propiedad que la fórmula fusionada debe seguir cumpliendo.
        """
        tolerancia = 45
        diffs = [tolerancia, tolerancia * 1.5, tolerancia * 2, tolerancia * 2.5, tolerancia * 3, 180]
        scores = [_score_dir_swell(diff=d, tolerancia=tolerancia) for d in diffs]
        for i in range(1, len(scores)):
            self.assertLessEqual(
                scores[i], scores[i - 1],
                f"score subió de {scores[i-1]} (diff={diffs[i-1]}) a {scores[i]} (diff={diffs[i]})",
            )

    def test_tolerancia_cero_diff_cero_coherente_con_flag(self):
        """Integración vía calcular_score(): tolerancia=0 con alineación
        perfecta debe dar score 1.0 Y flag positivo — coherentes entre sí."""
        spot = make_spot(orientacion=95, tolerancia=0)
        hour = make_hour(altura=1.2, periodo=12, dir_swell=95, vel_viento=8, dir_viento=275, nivel_marea=1.0)
        bd = calcular_score(hour, spot)
        self.assertEqual(bd.score_dir_swell, 1.0)
        self.assertIn("Dirección swell ideal", bd.flags_positivos)

    def test_tolerancia_negativa_coherente_con_flag(self):
        """
        Regresión #10 (parte 2, hallazgo de Codex): con tolerancia negativa,
        ni siquiera una alineación perfecta (diff=0) debe dar score 1.0 con
        un flag negativo al mismo tiempo. _generar_flags() ya trata TODO
        diff como oblicuo cuando la tolerancia es negativa (diff <=
        tolerancia nunca es cierto para un diff real, que siempre es >=0),
        así que el score debe coincidir con esa misma lectura.
        """
        spot = make_spot(orientacion=95, tolerancia=-5)
        hour = make_hour(altura=1.2, periodo=12, dir_swell=95, vel_viento=8, dir_viento=275, nivel_marea=1.0)
        bd = calcular_score(hour, spot)
        self.assertLess(bd.score_dir_swell, 0.5)
        self.assertIn("Swell oblicuo al spot", bd.flags_negativos)

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

    def test_registry_carga_delta_marea_default(self):
        """
        Regresión #13: delta_marea es un campo nuevo e independiente de
        delta_altura. Ningún spot real lo especifica en config/spots/*.json
        todavía, así que debe caer al default 0.0 sin romper la carga.
        """
        spot = get_spot("mdq_playa_grande")
        self.assertEqual(spot.delta_marea, 0.0)

    def test_actualizar_ajuste_acepta_delta_marea(self):
        """El comando /ajuste (vía actualizar_ajuste) debe poder calibrar
        delta_marea igual que delta_altura y factor_periodo."""
        from core.spots.registry import actualizar_ajuste, get_spot

        spot = get_spot("mdq_playa_grande")
        valor_original = spot.delta_marea
        try:
            actualizar_ajuste("mdq_playa_grande", "delta_marea", 0.15)
            self.assertEqual(get_spot("mdq_playa_grande").delta_marea, 0.15)
        finally:
            actualizar_ajuste("mdq_playa_grande", "delta_marea", valor_original)


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

    def test_defaults_preservan_curva_original(self):
        """viento_max_offshore=40.0/viento_max_onshore=15.0 (defaults del
        registry) deben dar exactamente el mismo resultado que no pasar
        los parámetros."""
        wind_off = WindData(velocidad_kmh=20, rafaga_kmh=25, direccion_deg=275)
        wind_on = WindData(velocidad_kmh=12, rafaga_kmh=16, direccion_deg=90)
        self.assertEqual(
            _score_viento(wind_off, orientacion_costa=95),
            _score_viento(wind_off, orientacion_costa=95, viento_max_offshore=40.0),
        )
        self.assertEqual(
            _score_viento(wind_on, orientacion_costa=95),
            _score_viento(wind_on, orientacion_costa=95, viento_max_onshore=15.0),
        )

    def test_viento_max_onshore_mas_bajo_penaliza_mas(self):
        """
        Regresión #3: un spot expuesto (viento_max_onshore bajo, ej.
        Necochea con 10 km/h) debe penalizar el mismo onshore más que un
        spot tolerante (ej. 18 km/h) — hoy ambos recibían el mismo score.
        """
        wind = WindData(velocidad_kmh=9, rafaga_kmh=13, direccion_deg=90)
        s_expuesto = _score_viento(wind, orientacion_costa=95, viento_max_onshore=10.0)
        s_tolerante = _score_viento(wind, orientacion_costa=95, viento_max_onshore=18.0)
        self.assertLess(s_expuesto, s_tolerante)

    def test_viento_max_offshore_mas_bajo_penaliza_mas(self):
        wind = WindData(velocidad_kmh=20, rafaga_kmh=25, direccion_deg=275)
        s_bajo = _score_viento(wind, orientacion_costa=95, viento_max_offshore=25.0)
        s_alto = _score_viento(wind, orientacion_costa=95, viento_max_offshore=50.0)
        self.assertLess(s_bajo, s_alto)

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

    def test_rafaga_dentro_de_margen_no_penaliza(self):
        """Regresión #8: exceso <= 10 km/h es variabilidad normal, no penaliza."""
        self.assertEqual(_factor_rafaga(velocidad_kmh=10, rafaga_kmh=20), 1.0)  # exceso exacto = 10

    def test_rafaga_extrema_penaliza_viento_sostenido_calmo(self):
        """
        Regresión #8: WindData.rafaga_kmh se cargaba pero nunca se leía —
        un sostenido calmo con ráfagas extremas puntuaba igual que uno con
        ráfagas normales. Ejemplo del hallazgo original: sostenido 3 km/h,
        ráfaga 100 km/h ya no debe dar el mismo score que ráfaga 5 km/h.
        """
        wind_normal = WindData(velocidad_kmh=3, rafaga_kmh=5, direccion_deg=90)
        wind_rafagoso = WindData(velocidad_kmh=3, rafaga_kmh=100, direccion_deg=90)
        s_normal = _score_viento(wind_normal, orientacion_costa=90)
        s_rafagoso = _score_viento(wind_rafagoso, orientacion_costa=90)
        self.assertEqual(s_normal, 1.0)
        self.assertLess(s_rafagoso, s_normal)

    def test_piso_05_en_rafaga_extrema(self):
        """El factor de ráfaga nunca anula el score, tiene piso 0.5."""
        f = _factor_rafaga(velocidad_kmh=3, rafaga_kmh=200)
        self.assertAlmostEqual(f, 0.5, delta=0.001)

    def test_rafaga_penaliza_tambien_con_viento_fuerte(self):
        """El factor de ráfaga se aplica sin importar la rama de velocidad
        sostenida — no es exclusivo del viento calmo."""
        wind_normal = WindData(velocidad_kmh=25, rafaga_kmh=30, direccion_deg=275)
        wind_rafagoso = WindData(velocidad_kmh=25, rafaga_kmh=70, direccion_deg=275)
        s_normal = _score_viento(wind_normal, orientacion_costa=95)
        s_rafagoso = _score_viento(wind_rafagoso, orientacion_costa=95)
        self.assertLess(s_rafagoso, s_normal)

    def test_rafaga_fuerte_no_queda_sin_advertencia_en_offshore_limpio(self):
        """
        Regresión #8 (hallazgo de Codex): un offshore sostenido limpio con
        ráfagas extremas penaliza score_viento, pero _generar_flags() solo
        miraba wind.velocidad_kmh — el usuario veía únicamente "Offshore
        limpio" sin ninguna indicación de por qué el score bajó. Vía
        calcular_score(), el flag de ráfagas debe aparecer junto al de
        offshore limpio, no reemplazarlo.
        """
        spot = make_spot(orientacion=95, tolerancia=45)
        hour = ForecastHour(
            timestamp=datetime(2025, 2, 1, 8, 0, tzinfo=timezone.utc),
            swell=SwellData(altura_m=1.2, periodo_s=12, direccion_deg=95),
            wind=WindData(velocidad_kmh=3, rafaga_kmh=100, direccion_deg=275),
            tide=TideData(nivel_m=1.0),
        )
        bd = calcular_score(hour, spot)
        self.assertLess(bd.score_viento, 1.0)
        self.assertTrue(any("Offshore limpio" in f for f in bd.flags_positivos))
        self.assertTrue(any("Ráfagas fuertes" in f for f in bd.flags_negativos))


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

    def test_continuidad_en_el_borde_mid_better(self):
        """
        Regresión #2: justo en marea_min/marea_max el score interior da
        0.80 (borde), y un desvío infinitesimal hacia afuera NO debe saltar
        a ~1.00 — debe seguir cerca de 0.80, decayendo suavemente desde ahí.
        """
        spot = make_spot(marea_min=0.4, marea_max=1.6, marea_tipo_efecto="mid_better")
        epsilon = 0.001

        s_borde_bajo = _score_marea(TideData(nivel_m=0.4), spot)
        s_justo_afuera_bajo = _score_marea(TideData(nivel_m=0.4 - epsilon), spot)
        self.assertAlmostEqual(s_borde_bajo, 0.80, delta=0.001)
        self.assertAlmostEqual(s_borde_bajo, s_justo_afuera_bajo, delta=0.01)

        s_borde_alto = _score_marea(TideData(nivel_m=1.6), spot)
        s_justo_afuera_alto = _score_marea(TideData(nivel_m=1.6 + epsilon), spot)
        self.assertAlmostEqual(s_borde_alto, 0.80, delta=0.001)
        self.assertAlmostEqual(s_borde_alto, s_justo_afuera_alto, delta=0.01)

    def test_continuidad_en_el_borde_low_better_direccion_penalizada(self):
        """Misma continuidad, pero en la dirección que sí se penaliza
        (hacia marea_max_m) para un spot low_better."""
        spot = make_spot(marea_min=0.4, marea_max=1.6, marea_tipo_efecto="low_better")
        epsilon = 0.001
        s_borde = _score_marea(TideData(nivel_m=1.6), spot)
        s_justo_afuera = _score_marea(TideData(nivel_m=1.6 + epsilon), spot)
        self.assertAlmostEqual(s_borde, 0.80, delta=0.001)
        self.assertAlmostEqual(s_borde, s_justo_afuera, delta=0.01)

    def test_continuidad_en_el_borde_high_better_direccion_penalizada(self):
        """Misma continuidad, dirección penalizada (hacia marea_min_m) para
        un spot high_better."""
        spot = make_spot(marea_min=0.4, marea_max=1.6, marea_tipo_efecto="high_better")
        epsilon = 0.001
        s_borde = _score_marea(TideData(nivel_m=0.4), spot)
        s_justo_afuera = _score_marea(TideData(nivel_m=0.4 - epsilon), spot)
        self.assertAlmostEqual(s_borde, 0.80, delta=0.001)
        self.assertAlmostEqual(s_borde, s_justo_afuera, delta=0.01)

    def test_mid_better_fuera_de_rango_penaliza_ambos_lados(self):
        """mid_better (default, sin cambios): ninguna dirección se exime."""
        spot = make_spot(marea_min=0.4, marea_max=1.6, marea_tipo_efecto="mid_better")
        s_abajo = _score_marea(TideData(nivel_m=0.0), spot)
        s_arriba = _score_marea(TideData(nivel_m=2.0), spot)
        self.assertLess(s_abajo, 1.0)
        self.assertLess(s_arriba, 1.0)

    def test_penalizacion_fuera_de_rango_es_relativa_a_amplitud(self):
        """
        Regresión #7: el mismo desvío ABSOLUTO (0.2m) debe penalizar más a
        un spot con rango angosto que a uno con rango ancho — antes ambos
        perdían exactamente lo mismo porque la penalización era en metros
        absolutos, sin relación al ancho del rango de cada spot.
        """
        spot_angosto = make_spot(marea_min=0.2, marea_max=0.8, marea_tipo_efecto="mid_better")  # amplitud 0.3
        spot_ancho = make_spot(marea_min=0.5, marea_max=2.0, marea_tipo_efecto="mid_better")     # amplitud 0.75

        s_angosto = _score_marea(TideData(nivel_m=0.0), spot_angosto)  # 0.2m por debajo de marea_min
        s_ancho = _score_marea(TideData(nivel_m=0.3), spot_ancho)      # también 0.2m por debajo de marea_min

        self.assertLess(s_angosto, s_ancho, "El mismo desvío absoluto debería penalizar más al rango angosto")

    def test_desvio_igual_a_amplitud_llega_justo_al_piso(self):
        """Congela el significado del coeficiente 0.70: un desvío de exactamente
        un semi-ancho de rango (desvio == amplitud) debe dar score == 0.10."""
        spot = make_spot(marea_min=0.4, marea_max=1.6, marea_tipo_efecto="mid_better")  # amplitud 0.6
        s = _score_marea(TideData(nivel_m=0.4 - 0.6), spot)  # desvio == amplitud == 0.6
        self.assertAlmostEqual(s, 0.10, delta=0.001)

    def test_penalizacion_fuera_de_rango_misma_para_desvio_relativo_igual(self):
        """Contraparte: si el desvío es proporcionalmente igual (mismo
        desvio/amplitud), el score debe ser el mismo sin importar el ancho
        real del rango del spot."""
        spot_angosto = make_spot(marea_min=0.2, marea_max=0.8, marea_tipo_efecto="mid_better")  # amplitud 0.3
        spot_ancho = make_spot(marea_min=0.5, marea_max=2.0, marea_tipo_efecto="mid_better")     # amplitud 0.75

        # Mismo desvío relativo: 1/3 de la amplitud, por debajo de marea_min.
        s_angosto = _score_marea(TideData(nivel_m=0.2 - 0.3 / 3), spot_angosto)
        s_ancho = _score_marea(TideData(nivel_m=0.5 - 0.75 / 3), spot_ancho)

        self.assertAlmostEqual(s_angosto, s_ancho, delta=0.001)


# ------------------------------------------------------------------
# Tests del score total
# ------------------------------------------------------------------

class TestScoreTotal(unittest.TestCase):
    def test_delta_marea_afecta_score_y_flags(self):
        """
        Regresión #13 (parte 2, hallazgo de Codex): delta_marea debe
        aplicarse también al score de marea y sus flags dentro de
        calcular_score(), no solo a la vista de core/analysis/tides.py —
        si no, la vista podía mostrar "marea en rango óptimo" mientras el
        motor seguía puntuando contra el nivel crudo, fuera de rango.
        """
        spot = make_spot(marea_min=0.8, marea_max=1.2)
        spot.delta_marea = 0.9
        hour = make_hour(altura=1.2, periodo=12, dir_swell=95, vel_viento=8, dir_viento=275, nivel_marea=0.0)
        bd = calcular_score(hour, spot)

        # 0.0 + 0.9 = 0.9, dentro de [0.8, 1.2] → score alto, flag positivo.
        self.assertGreater(bd.score_marea, 0.85)
        self.assertIn("Marea en rango óptimo", bd.flags_positivos)

    def test_delta_altura_no_afecta_score_ni_flags_de_marea(self):
        """delta_altura (calibración de swell) no debe filtrarse al score
        de marea — son campos independientes desde el fix #13."""
        spot = make_spot(marea_min=0.8, marea_max=1.2, delta_altura=0.9)
        hour = make_hour(altura=1.2, periodo=12, dir_swell=95, vel_viento=8, dir_viento=275, nivel_marea=0.0)
        bd = calcular_score(hour, spot)

        # Sigue en 0.0 (sin ajuste de marea) → fuera de rango, score bajo.
        self.assertLess(bd.score_marea, 0.70)
        self.assertNotIn("Marea en rango óptimo", bd.flags_positivos)

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

    def test_altura_ajustada_negativa_no_da_energia_falsa(self):
        """
        Regresión #9: delta_altura negativo mayor que la altura real no
        debe convertirse en energía positiva falsa al elevar al cuadrado
        (H²) — debe clampearse a piso 0.0 (ola inexistente), no
        comportarse como si fuera una ola grande real.
        """
        spot = make_spot(delta_altura=-1.0)
        hour = make_hour(altura=0.2, periodo=10, dir_swell=95, vel_viento=8, dir_viento=275, nivel_marea=1.0)
        bd = calcular_score(hour, spot)

        self.assertEqual(bd.energia_proxy, 0.0)
        self.assertAlmostEqual(bd.score_energia, 0.0, delta=0.01)

    def test_delta_altura_positivo_normal_sigue_funcionando(self):
        """Un delta_altura positivo normal (calibración típica) no debe
        verse afectado por el clamp — solo el caso negativo-y-mayor cambia."""
        spot_ajustado = make_spot(delta_altura=0.3)
        spot_base = make_spot(delta_altura=0.0)
        hour = make_hour(altura=1.0, periodo=12, dir_swell=95, vel_viento=8, dir_viento=275, nivel_marea=1.0)

        bd_ajustado = calcular_score(hour, spot_ajustado)
        bd_base = calcular_score(hour, spot_base)

        # Con delta_altura=+0.3, la altura efectiva es mayor → más energía, no menos.
        self.assertGreater(bd_ajustado.energia_proxy, bd_base.energia_proxy)

    def test_periodo_min_alto_coherente_entre_score_y_flags(self):
        """
        Regresión #3 (coherencia score/flags): un reef exigente
        (periodo_min=15, como pidió Codex de ejemplo) con un período de 14s
        —que en un spot estándar sería "groundswell largo"— debe quedar con
        score bajo (windchop para ESTE spot) y el flag debe decir eso, no
        "Groundswell largo".
        """
        reef_exigente = make_spot(tipo_break="reef", swell_periodo_min=15.0)
        hour = make_hour(altura=1.2, periodo=14, dir_swell=95, vel_viento=8, dir_viento=275, nivel_marea=1.0)
        bd = calcular_score(hour, reef_exigente)

        self.assertLess(bd.score_periodo, 0.40)
        flags_texto = " ".join(bd.flags_positivos + bd.flags_negativos + bd.flags_neutros)
        self.assertNotIn("Groundswell largo", flags_texto)
        self.assertTrue(
            any("corto" in f or "windchop" in f for f in bd.flags_negativos),
            f"Esperaba un flag negativo de período corto/windchop, flags: {bd.flags_negativos}",
        )

    def test_ola_por_encima_del_maximo_no_sigue_subiendo_el_score(self):
        """
        Regresión #4: antes, una ola más grande que swell_altura_max seguía
        aumentando el score de energía sin límite (solo se avisaba con un
        flag). Reproduce el ejemplo de KNOWN_ISSUES.md: spot con máximo 2m,
        período 12s — 4m (el doble del máximo) debe puntuar peor que 2m
        (justo en el máximo), no mejor.
        """
        spot = make_spot(swell_altura_max=2.0)
        hour_en_el_maximo = make_hour(altura=2.0, periodo=12, dir_swell=95, vel_viento=8, dir_viento=275, nivel_marea=1.0)
        hour_muy_grande = make_hour(altura=4.0, periodo=12, dir_swell=95, vel_viento=8, dir_viento=275, nivel_marea=1.0)

        bd_en_el_maximo = calcular_score(hour_en_el_maximo, spot)
        bd_muy_grande = calcular_score(hour_muy_grande, spot)

        self.assertLess(bd_muy_grande.score_energia, bd_en_el_maximo.score_energia)

    def test_borde_exacto_vs_apenas_por_encima(self):
        """
        Regresión #4 (hallazgo de Codex): justo en el máximo (2.0m) vs.
        apenas por encima (2.1m) — el score no debe subir. Con solo el
        factor multiplicativo sin recortar la base, tanh todavía crecía más
        rápido que lo que decaía el factor lineal en esta zona.
        """
        spot = make_spot(swell_altura_max=2.0)
        hour_en_el_maximo = make_hour(altura=2.0, periodo=6, dir_swell=95, vel_viento=8, dir_viento=275, nivel_marea=1.0)
        hour_apenas_encima = make_hour(altura=2.1, periodo=6, dir_swell=95, vel_viento=8, dir_viento=275, nivel_marea=1.0)

        bd_en_el_maximo = calcular_score(hour_en_el_maximo, spot)
        bd_apenas_encima = calcular_score(hour_apenas_encima, spot)

        self.assertLessEqual(bd_apenas_encima.score_energia, bd_en_el_maximo.score_energia)

    def test_periodo_corto_sin_saturacion_de_tanh(self):
        """
        Caso real señalado por Codex: máximo 2m, período 6s (periodo_min
        bajo real en config/spots) — con T corto el tanh está lejos de
        saturar, la zona donde el bug original era más severo.
        """
        spot = make_spot(swell_altura_max=2.0)
        hour_en_el_maximo = make_hour(altura=2.0, periodo=6, dir_swell=95, vel_viento=8, dir_viento=275, nivel_marea=1.0)
        hour_50pct_exceso = make_hour(altura=3.0, periodo=6, dir_swell=95, vel_viento=8, dir_viento=275, nivel_marea=1.0)

        bd_en_el_maximo = calcular_score(hour_en_el_maximo, spot)
        bd_50pct_exceso = calcular_score(hour_50pct_exceso, spot)

        self.assertLess(bd_50pct_exceso.score_energia, bd_en_el_maximo.score_energia)

    def test_monotonicidad_no_creciente_por_encima_del_maximo(self):
        """El score de energía no debe subir en ningún punto a medida que
        la altura crece más allá del máximo del spot."""
        spot = make_spot(swell_altura_max=2.0)
        alturas = [2.0, 2.1, 2.3, 2.5, 3.0, 4.0, 6.0]
        scores = []
        for altura in alturas:
            hour = make_hour(altura=altura, periodo=6, dir_swell=95, vel_viento=8, dir_viento=275, nivel_marea=1.0)
            scores.append(calcular_score(hour, spot).score_energia)

        for i in range(1, len(scores)):
            self.assertLessEqual(
                scores[i], scores[i - 1],
                f"score_energia subió de {scores[i-1]} (altura={alturas[i-1]}) a "
                f"{scores[i]} (altura={alturas[i]})",
            )

    def test_viento_max_offshore_bajo_coherente_entre_score_y_flags(self):
        """Un spot expuesto (viento_max_offshore=20) con offshore de 18 km/h
        —"limpio" en un spot estándar— debe quedar con score intermedio
        (no el tope de 0.98) y el flag debe decir "fuerte", no "limpio"."""
        spot_expuesto = make_spot(viento_max_offshore=20.0)
        hour = make_hour(altura=1.2, periodo=12, dir_swell=95, vel_viento=18, dir_viento=275, nivel_marea=1.0)
        bd = calcular_score(hour, spot_expuesto)

        self.assertLess(bd.score_viento, 0.90)
        flags_texto = " ".join(bd.flags_positivos + bd.flags_negativos + bd.flags_neutros)
        self.assertNotIn("Offshore limpio", flags_texto)
        self.assertIn("fuerte", flags_texto)


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

    def test_descripcion_no_dice_offshore_con_viento_calmo_onshore(self):
        """
        Regresión #27: score_viento >= 0.85 no implica dirección offshore —
        el engine da score=1.0 a cualquier viento < 5 km/h sin importar su
        dirección (ver _score_viento). Antes, esa condición sola bastaba
        para etiquetar la ventana como "offshore", una afirmación de
        dirección meteorológica falsa para un viento simplemente calmo con
        dirección onshore.
        """
        spot = make_spot()
        ahora = datetime.now(timezone.utc)
        base = (ahora + timedelta(days=1)).replace(hour=15, minute=0, second=0, microsecond=0)
        # Viento calmo (3 km/h) con dirección onshore (95° == orientacion_costa
        # del spot de test) -- score_viento=1.0 por ser calmo, no por offshore.
        cond = dict(altura=1.4, periodo=14, dir_swell=95, vel_viento=3, dir_viento=95, nivel_marea=1.0)
        forecast = [
            make_hour(ts=base, **cond),
            make_hour(ts=base + timedelta(hours=1), **cond),
        ]

        ventanas = detectar_ventanas(forecast, spot, umbral=0.60)
        self.assertEqual(len(ventanas), 1, f"Esperaba 1 ventana: {ventanas}")
        self.assertNotIn("offshore", ventanas[0].descripcion)
        self.assertIn("viento calmo", ventanas[0].descripcion)

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

    def test_umbral_negativo_lanza_error(self):
        """
        Regresión #30: umbral fuera de [0,1] es config imposible de
        interpretar, no un caso silencioso a normalizar — mismo criterio
        que el fix #23 (fallar visible, no degradar en silencio).
        """
        spot = make_spot()
        forecast = [make_hour()]
        with self.assertRaises(ValueError):
            detectar_ventanas(forecast, spot, umbral=-5.0)

    def test_umbral_mayor_a_uno_lanza_error(self):
        spot = make_spot()
        forecast = [make_hour()]
        with self.assertRaises(ValueError):
            detectar_ventanas(forecast, spot, umbral=5.0)

    def test_umbral_nan_lanza_error(self):
        spot = make_spot()
        forecast = [make_hour()]
        with self.assertRaises(ValueError):
            detectar_ventanas(forecast, spot, umbral=float("nan"))

    def test_umbral_infinito_lanza_error(self):
        spot = make_spot()
        forecast = [make_hour()]
        with self.assertRaises(ValueError):
            detectar_ventanas(forecast, spot, umbral=float("inf"))

    def test_umbral_en_los_bordes_no_lanza_error(self):
        """0.0 y 1.0 son válidos (bordes inclusive del rango)."""
        spot = make_spot()
        forecast = [make_hour()]
        detectar_ventanas(forecast, spot, umbral=0.0)  # no debe lanzar
        detectar_ventanas(forecast, spot, umbral=1.0)  # no debe lanzar

    def test_umbral_bool_lanza_error(self):
        """bool es subclase de int en Python — debe rechazarse explícitamente
        (mismo motivo que test_top_n_bool_lanza_error)."""
        spot = make_spot()
        forecast = [make_hour()]
        with self.assertRaises(ValueError):
            detectar_ventanas(forecast, spot, umbral=True)

    def test_umbral_string_lanza_error(self):
        spot = make_spot()
        forecast = [make_hour()]
        with self.assertRaises(ValueError):
            detectar_ventanas(forecast, spot, umbral="0.6")

    def test_top_n_negativo_lanza_error(self):
        """
        Regresión #30: top_n=-1 hacía slicing `ventanas[:-1]` ("todas
        menos la última") en vez de fallar visiblemente por config inválida.
        """
        spot = make_spot()
        forecast = [make_hour()]
        with self.assertRaises(ValueError):
            detectar_ventanas(forecast, spot, umbral=0.5, top_n=-1)

    def test_top_n_float_lanza_error(self):
        """top_n no entero (ej. 3.7) debe fallar explícitamente, no
        truncarse en silencio a un valor distinto del pedido."""
        spot = make_spot()
        forecast = [make_hour()]
        with self.assertRaises(ValueError):
            detectar_ventanas(forecast, spot, umbral=0.5, top_n=3.7)

    def test_top_n_bool_lanza_error(self):
        """bool es subclase de int en Python (isinstance(True, int) es
        True) — debe rechazarse explícitamente, no colarse como top_n=1/0."""
        spot = make_spot()
        forecast = [make_hour()]
        with self.assertRaises(ValueError):
            detectar_ventanas(forecast, spot, umbral=0.5, top_n=True)

    def test_top_n_string_lanza_error(self):
        spot = make_spot()
        forecast = [make_hour()]
        with self.assertRaises(ValueError):
            detectar_ventanas(forecast, spot, umbral=0.5, top_n="3")

    def test_top_n_cero_no_lanza_error(self):
        """top_n=0 es válido (0 es un entero >= 0 legítimo) — retorna []."""
        spot = make_spot()
        forecast = [make_hour()]
        ventanas = detectar_ventanas(forecast, spot, umbral=0.5, top_n=0)
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
        # Dos horas contiguas para la ventana cercana — una sola hora no
        # forma ventana válida desde el fix #26 (mínimo 2h).
        forecast = [
            make_hour(ts=ts_cercana, **condiciones_ideales),
            make_hour(ts=ts_cercana + timedelta(hours=1), **condiciones_ideales),
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
        # Dos horas contiguas — una sola hora no forma ventana válida desde
        # el fix #26 (mínimo 2h).
        forecast = [
            make_hour(ts=ts_lejos, **condiciones_ideales),
            make_hour(ts=ts_lejos + timedelta(hours=1), **condiciones_ideales),
        ]

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

    def test_todas_las_horas_fallan_score_lanza_excepcion(self):
        """
        Regresión #23: si calcular_score() falla para TODAS las horas
        evaluadas (ej. config de spot rota), detectar_ventanas() no debe
        devolver [] silenciosamente —indistinguible de "no hay condiciones
        buenas"— sino propagar el error. Los handlers de bot/handlers/main.py
        ya envuelven detectar_ventanas() en un try/except genérico que
        muestra un mensaje de error legible en ese caso.
        """
        spot = make_spot()
        ahora = datetime.now(timezone.utc)
        ts = (ahora + timedelta(days=1)).replace(hour=15, minute=0, second=0, microsecond=0)
        forecast = [make_hour(ts=ts)]

        with patch(
            "core.windows.detector.calcular_score",
            side_effect=ValueError("config de spot rota (simulado)"),
        ):
            with self.assertRaises(RuntimeError):
                detectar_ventanas(forecast, spot, umbral=0.50)

    def test_fallo_parcial_no_lanza_solo_descarta_esas_horas(self):
        """Un fallo aislado (no todas las horas) debe seguir descartándose
        en silencio, como antes — solo el fallo sistémico (100%) propaga."""
        spot = make_spot()
        ahora = datetime.now(timezone.utc)
        ts_falla = (ahora + timedelta(days=1)).replace(hour=15, minute=0, second=0, microsecond=0)
        ts_ok = ts_falla + timedelta(hours=1)
        # Segunda hora "ok" contigua — una sola hora sobreviviente no forma
        # ventana válida desde el fix #26 (mínimo 2h).
        ts_ok2 = ts_ok + timedelta(hours=1)
        forecast = [make_hour(ts=ts_falla), make_hour(ts=ts_ok), make_hour(ts=ts_ok2)]

        from core.scoring.engine import calcular_score as real_calcular_score

        def falla_una_hora(hour, spot):
            if hour.timestamp == ts_falla:
                raise ValueError("simulado")
            return real_calcular_score(hour, spot)

        with patch("core.windows.detector.calcular_score", side_effect=falla_una_hora):
            # No debe lanzar, y las horas que sí calculan score deben seguir
            # disponibles (con umbral=0.0 cualquier score válido forma ventana).
            ventanas = detectar_ventanas(forecast, spot, umbral=0.0)
            self.assertIsInstance(ventanas, list)
            self.assertTrue(
                any(v.inicio == ts_ok for v in ventanas),
                f"Las horas que sí calcularon score ({ts_ok}) deberían formar una ventana: {ventanas}",
            )

    def test_hueco_de_datos_corta_la_ventana(self):
        """
        Regresión #25: dos bloques de horas buenas (sobre el umbral) del
        MISMO día local, separados por un hueco real de datos del proveedor
        (una hora intermedia directamente ausente del forecast, no solo con
        score bajo), deben quedar como DOS ventanas separadas — antes, el
        corte solo chequeaba cambio de día local, así que un hueco dentro
        del mismo día no se detectaba y ambos bloques se fusionaban en una
        sola ventana continua.
        """
        class _RelojFijo(datetime):
            _ahora = None

            @classmethod
            def now(cls, tz=None):
                return cls._ahora.astimezone(tz) if tz else cls._ahora

        tz = ZoneInfo("America/Argentina/Buenos_Aires")
        _RelojFijo._ahora = datetime(2025, 1, 15, 6, 0, tzinfo=tz).astimezone(timezone.utc)

        spot = make_spot()
        cond_buena = dict(altura=1.4, periodo=14, dir_swell=95, vel_viento=8, dir_viento=275, nivel_marea=1.0)
        ts_08 = datetime(2025, 1, 15, 8, 0, tzinfo=tz).astimezone(timezone.utc)
        ts_09 = ts_08 + timedelta(hours=1)
        # 10:00 deliberadamente ausente del forecast -- simula un hueco real del proveedor.
        ts_11 = ts_08 + timedelta(hours=3)
        ts_12 = ts_08 + timedelta(hours=4)
        forecast = [
            make_hour(ts=ts_08, **cond_buena),
            make_hour(ts=ts_09, **cond_buena),
            make_hour(ts=ts_11, **cond_buena),
            make_hour(ts=ts_12, **cond_buena),
        ]

        with patch("core.windows.detector.datetime", _RelojFijo):
            ventanas = detectar_ventanas(forecast, spot, umbral=0.60)

        self.assertEqual(len(ventanas), 2, f"Esperaba 2 ventanas separadas por el hueco: {ventanas}")
        inicios = sorted(v.inicio for v in ventanas)
        self.assertEqual(inicios, [ts_08, ts_11])
        for v in ventanas:
            self.assertEqual(v.horas_count, 2)

    def test_medianoche_corta_la_ventana_aunque_sea_horariamente_contigua(self):
        """
        No-regresión pedida por Codex al revisar #25: el corte de ventana al
        cambiar el día local debe conservarse — horas horariamente contiguas
        que cruzan medianoche (22:00→23:00→00:00→01:00, cada una separada
        por exactamente 1h, sin ningún hueco) NO deben fusionarse en una
        sola ventana. Se fuerza is_daylight=True porque en la práctica
        ningún spot real tiene luz solar a esa hora — el filtro de luz
        diurna ya evita el escenario en producción, pero la regla en sí
        (nunca cruzar medianoche) debe seguir vigente en el código, no
        depender solo de ese filtro externo.
        """
        class _RelojFijo(datetime):
            _ahora = None

            @classmethod
            def now(cls, tz=None):
                return cls._ahora.astimezone(tz) if tz else cls._ahora

        tz = ZoneInfo("America/Argentina/Buenos_Aires")
        _RelojFijo._ahora = datetime(2025, 1, 15, 20, 0, tzinfo=tz).astimezone(timezone.utc)

        spot = make_spot()
        cond_buena = dict(altura=1.4, periodo=14, dir_swell=95, vel_viento=8, dir_viento=275, nivel_marea=1.0)
        ts_22 = datetime(2025, 1, 15, 22, 0, tzinfo=tz).astimezone(timezone.utc)
        ts_23 = ts_22 + timedelta(hours=1)
        ts_00 = ts_22 + timedelta(hours=2)  # 2025-01-16 00:00 local
        ts_01 = ts_22 + timedelta(hours=3)
        forecast = [
            make_hour(ts=ts_22, **cond_buena),
            make_hour(ts=ts_23, **cond_buena),
            make_hour(ts=ts_00, **cond_buena),
            make_hour(ts=ts_01, **cond_buena),
        ]

        with patch("core.windows.detector.datetime", _RelojFijo), \
                patch("core.windows.detector.is_daylight", return_value=True):
            ventanas = detectar_ventanas(forecast, spot, umbral=0.60)

        self.assertEqual(len(ventanas), 2, f"Esperaba 2 ventanas separadas por el cambio de día: {ventanas}")
        inicios = sorted(v.inicio for v in ventanas)
        self.assertEqual(inicios, [ts_22, ts_00])
        for v in ventanas:
            self.assertEqual(v.horas_count, 2)

    def test_ventana_de_una_hora_no_se_genera(self):
        """
        Regresión #26: una hora buena aislada entre dos malas (contrato
        documentado en bot/handlers/main.py: "ventana más cercana, mínimo
        2h") no debe generar una ventana de horas_count=1. Ejemplo exacto
        del hallazgo: 09:00=0.40, 10:00=0.61, 11:00=0.40.
        """
        spot = make_spot()
        ahora = datetime.now(timezone.utc)
        # hour=15 UTC ~ mediodía AR (UTC-3), daylight seguro en cualquier
        # época del año — igual criterio que otros tests de esta clase.
        base = (ahora + timedelta(days=1)).replace(hour=15, minute=0, second=0, microsecond=0)

        cond_mala = dict(altura=0.2, periodo=5, dir_swell=0, vel_viento=30, dir_viento=90, nivel_marea=2.5)
        cond_buena = dict(altura=1.4, periodo=14, dir_swell=95, vel_viento=8, dir_viento=275, nivel_marea=1.0)
        forecast = [
            make_hour(ts=base, **cond_mala),
            make_hour(ts=base + timedelta(hours=1), **cond_buena),
            make_hour(ts=base + timedelta(hours=2), **cond_mala),
        ]

        ventanas = detectar_ventanas(forecast, spot, umbral=0.55)
        self.assertEqual(ventanas, [], f"Una sola hora buena no debería formar ventana: {ventanas}")

    def test_ventana_en_curso_recorta_horas_ya_pasadas(self):
        """
        Regresión #24: una ventana que ya empezó pero todavía no terminó
        debe recalcular promedio/hora_pico/inicio solo con las horas
        vigentes, no con las que ya pasaron. Antes, una ventana 10:00-14:00
        consultada a las 12:30 seguía mostrando estadísticas calculadas con
        las horas 10:00 y 11:00, ya terminadas.

        Usa un reloj fijo (mock sobre core.windows.detector.datetime) para
        poder construir horas "pasadas" y "futuras" de forma determinista,
        sin depender de la hora real de ejecución del test.
        """
        class _RelojFijo(datetime):
            _ahora = None

            @classmethod
            def now(cls, tz=None):
                return cls._ahora.astimezone(tz) if tz else cls._ahora

        tz = ZoneInfo("America/Argentina/Buenos_Aires")
        # 2025-06-15 es invierno en AR pero 10-13h local sigue siendo de día
        # (sunrise invierno MDP ~08:00-08:30).
        base_local = datetime(2025, 6, 15, 10, 0, tzinfo=tz)  # 10:00 local
        _RelojFijo._ahora = datetime(2025, 6, 15, 12, 30, tzinfo=tz).astimezone(timezone.utc)  # ahora = 12:30 local

        spot = make_spot()
        # 10:00 tiene las mejores condiciones (dirección de swell perfecta);
        # 11:00-13:00 son buenas pero no óptimas — si el pico quedara mal
        # calculado, elegiría 10:00 (ya pasada) en vez de una hora vigente.
        cond_pico = dict(altura=1.4, periodo=14, dir_swell=95, vel_viento=8, dir_viento=275, nivel_marea=1.0)
        cond_buena = dict(altura=1.2, periodo=12, dir_swell=115, vel_viento=10, dir_viento=275, nivel_marea=1.0)
        horas_ts = [base_local.astimezone(timezone.utc) + timedelta(hours=h) for h in range(4)]
        forecast = [
            make_hour(ts=horas_ts[0], **cond_pico),
            make_hour(ts=horas_ts[1], **cond_buena),
            make_hour(ts=horas_ts[2], **cond_buena),
            make_hour(ts=horas_ts[3], **cond_buena),
        ]

        with patch("core.windows.detector.datetime", _RelojFijo):
            ventanas = detectar_ventanas(forecast, spot, umbral=0.30)

        self.assertEqual(len(ventanas), 1, f"Esperaba una sola ventana: {ventanas}")
        ventana = ventanas[0]

        # Las horas 10:00 y 11:00 (bloque termina a las 11:00 y 12:00,
        # ambos <= ahora=12:30) ya pasaron — no deben contarse.
        self.assertEqual(ventana.horas_count, 2, "Deberían quedar solo las 2 horas vigentes (12:00 y 13:00)")
        self.assertEqual(ventana.inicio, horas_ts[2], "inicio debería recalcularse a la primera hora vigente (12:00)")
        self.assertNotEqual(
            ventana.hora_pico, horas_ts[0],
            "hora_pico no debería ser la hora ya pasada (10:00), aunque tenga el mejor score",
        )
        self.assertIn(ventana.hora_pico, (horas_ts[2], horas_ts[3]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
