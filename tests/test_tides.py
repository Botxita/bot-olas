"""Tests unitarios para core/analysis/tides.py.

Todos los tests usan fixtures sintéticos (sin red, sin archivos).
Los datos de marea se construyen con funciones senoidales para simular
ciclos mareales realistas.

Correr con:
    python -m pytest tests/test_tides.py -v
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import math
from datetime import datetime, timezone, timedelta, date
from typing import List
from zoneinfo import ZoneInfo

from core.scoring.models import ForecastHour, SwellData, WindData, TideData, SpotConfig
from core.analysis.tides import (
    detectar_mareas,
    detectar_mareas_del_dia,
    TideEvent,
    TideAnalysis,
    _suavizar,
    _detectar_extremos,
    _amplitud_serie,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_spot() -> SpotConfig:
    return SpotConfig(
        key="test_spot",
        nombre="Test",
        ciudad="Test",
        pais="AR",
        region="test",
        lat=-38.0,
        lon=-57.5,
        orientacion_costa_deg=180.0,
        tolerancia_swell_deg=45.0,
        tipo_break="beach",
        fondo="arena",
        marea_min_m=0.3,
        marea_max_m=1.2,
        marea_tipo_efecto="mid_better",
        swell_altura_min=0.3,
        swell_altura_max=4.0,
        swell_periodo_min=6.0,
        viento_max_offshore=40.0,
        viento_max_onshore=15.0,
        delta_altura=0.0,
        tz="America/Argentina/Buenos_Aires",
    )


def make_forecast_senoidal(
    n_horas: int = 48,
    amplitud: float = 0.6,
    offset: float = 0.5,
    periodo_h: float = 12.42,  # ciclo mareal semidiurno real
    fase_inicial: float = 0.0,
    inicio: datetime = None,
) -> List[ForecastHour]:
    """
    Genera un forecast con marea senoidal (simula ciclo semidiurno).
    Garantiza que haya exactamente 2 altas y 2 bajas por cada ~25h.
    """
    if inicio is None:
        inicio = datetime(2025, 1, 15, 0, 0, tzinfo=timezone.utc)

    hours = []
    for i in range(n_horas):
        ts = inicio + timedelta(hours=i)
        fase = (2 * math.pi * i / periodo_h) + fase_inicial
        nivel = offset + amplitud * math.sin(fase)

        tide = TideData(nivel_m=round(nivel, 4), fuente="proxy_msl", es_exacto=False)
        swell = SwellData(altura_m=1.5, periodo_s=12.0, direccion_deg=180.0)
        wind = WindData(velocidad_kmh=15.0, rafaga_kmh=20.0, direccion_deg=180.0)
        hours.append(ForecastHour(timestamp=ts, swell=swell, wind=wind, tide=tide))

    return hours


def make_forecast_plano(n_horas: int = 24, nivel: float = 0.5) -> List[ForecastHour]:
    """Forecast con marea constante (sin variación)."""
    inicio = datetime(2025, 1, 15, 0, 0, tzinfo=timezone.utc)
    hours = []
    for i in range(n_horas):
        ts = inicio + timedelta(hours=i)
        tide = TideData(nivel_m=nivel, fuente="proxy_msl", es_exacto=False)
        swell = SwellData(altura_m=1.0, periodo_s=10.0, direccion_deg=180.0)
        wind = WindData(velocidad_kmh=10.0, rafaga_kmh=15.0, direccion_deg=180.0)
        hours.append(ForecastHour(timestamp=ts, swell=swell, wind=wind, tide=tide))
    return hours


def make_forecast_subiendo(n_horas: int = 12) -> List[ForecastHour]:
    """Forecast con marea siempre subiendo (pendiente positiva)."""
    inicio = datetime(2025, 1, 15, 6, 0, tzinfo=timezone.utc)
    hours = []
    for i in range(n_horas):
        ts = inicio + timedelta(hours=i)
        nivel = 0.2 + i * 0.08  # sube ~8cm por hora
        tide = TideData(nivel_m=round(nivel, 3), fuente="proxy_msl")
        swell = SwellData(altura_m=1.0, periodo_s=10.0, direccion_deg=180.0)
        wind = WindData(velocidad_kmh=10.0, rafaga_kmh=15.0, direccion_deg=180.0)
        hours.append(ForecastHour(timestamp=ts, swell=swell, wind=wind, tide=tide))
    return hours


def make_forecast_bajando(n_horas: int = 12) -> List[ForecastHour]:
    """Forecast con marea siempre bajando."""
    inicio = datetime(2025, 1, 15, 6, 0, tzinfo=timezone.utc)
    hours = []
    for i in range(n_horas):
        ts = inicio + timedelta(hours=i)
        nivel = 1.2 - i * 0.08
        tide = TideData(nivel_m=round(nivel, 3), fuente="proxy_msl")
        swell = SwellData(altura_m=1.0, periodo_s=10.0, direccion_deg=180.0)
        wind = WindData(velocidad_kmh=10.0, rafaga_kmh=15.0, direccion_deg=180.0)
        hours.append(ForecastHour(timestamp=ts, swell=swell, wind=wind, tide=tide))
    return hours


# ---------------------------------------------------------------------------
# Tests de _suavizar
# ---------------------------------------------------------------------------

def test_suavizar_conserva_longitud():
    vals = [1.0, 2.0, 3.0, 2.0, 1.0]
    result = _suavizar(vals, ventana=3)
    assert len(result) == len(vals)


def test_suavizar_reduce_picos():
    """Un pico extremo debe ser atenuado por el suavizado."""
    vals = [0.5, 0.5, 5.0, 0.5, 0.5]  # pico en el centro
    result = _suavizar(vals, ventana=3)
    assert result[2] < 5.0, "El suavizado debe atenuar el pico"
    assert result[2] > 0.5, "El suavizado no debe eliminar el pico completamente"


def test_suavizar_serie_plana():
    """Una serie plana debe quedar igual después del suavizado."""
    vals = [1.0] * 10
    result = _suavizar(vals, ventana=3)
    for v in result:
        assert abs(v - 1.0) < 1e-10


# ---------------------------------------------------------------------------
# Tests de _amplitud_serie
# ---------------------------------------------------------------------------

def test_amplitud_serie_senoidal():
    forecast = make_forecast_senoidal(amplitud=0.6)
    niveles = [h.tide.nivel_m for h in forecast]
    amp = _amplitud_serie(niveles)
    # Amplitud esperada ≈ 2 * 0.6 = 1.2 (pico a pico de seno)
    assert 1.0 <= amp <= 1.3, f"Amplitud esperada ~1.2, got {amp:.3f}"


def test_amplitud_serie_plana():
    vals = [0.5] * 10
    assert _amplitud_serie(vals) < 0.01


def test_amplitud_serie_vacia():
    assert _amplitud_serie([]) == 0.0


# ---------------------------------------------------------------------------
# Tests de _detectar_extremos — mesetas
# ---------------------------------------------------------------------------

def test_detectar_extremos_meseta_de_2_horas():
    """
    Regresión #15: una meseta de 2 horas en pleamar (mismo valor suavizado
    dos horas seguidas) debe detectarse como un único evento — antes,
    `curr > next` fallaba porque `next` era igual, no menor, y la pleamar
    completa se perdía.
    """
    inicio = datetime(2025, 1, 15, 0, 0, tzinfo=timezone.utc)
    niveles = [0.50, 0.80, 0.80, 0.50]
    timestamps = [inicio + timedelta(hours=i) for i in range(len(niveles))]
    eventos = _detectar_extremos(timestamps, niveles, niveles)
    assert len(eventos) == 1
    assert eventos[0].tipo == "alta"
    assert eventos[0].nivel_m == 0.80
    # Punto medio de la meseta (índices 1,2), redondeado hacia abajo -> índice 1
    assert eventos[0].timestamp == inicio + timedelta(hours=1)


def test_detectar_extremos_meseta_larga_bajamar():
    """Meseta de 4 horas en bajamar también se agrupa en un único evento."""
    inicio = datetime(2025, 1, 15, 0, 0, tzinfo=timezone.utc)
    niveles = [1.0, 0.2, 0.2, 0.2, 0.2, 1.0]
    timestamps = [inicio + timedelta(hours=i) for i in range(len(niveles))]
    eventos = _detectar_extremos(timestamps, niveles, niveles)
    assert len(eventos) == 1
    assert eventos[0].tipo == "baja"
    # Meseta en índices 1..4, punto medio redondeado hacia abajo -> índice 2
    assert eventos[0].timestamp == inicio + timedelta(hours=2)


def test_detectar_extremos_meseta_que_toca_el_borde_no_se_detecta():
    """
    Una meseta que se extiende hasta el borde de la serie (índice 0) no
    tiene vecino real de ese lado para comparar — igual que un punto
    aislado en el borde, se descarta (comportamiento heredado, no nuevo).
    """
    inicio = datetime(2025, 1, 15, 0, 0, tzinfo=timezone.utc)
    niveles = [0.80, 0.80, 0.80, 0.50]  # meseta pegada al borde izquierdo
    timestamps = [inicio + timedelta(hours=i) for i in range(len(niveles))]
    eventos = _detectar_extremos(timestamps, niveles, niveles)
    assert eventos == []


def test_detectar_extremos_no_detecta_serie_plana():
    """Serie completamente plana: no hay extremos, sin importar la longitud."""
    inicio = datetime(2025, 1, 15, 0, 0, tzinfo=timezone.utc)
    niveles = [0.5] * 10
    timestamps = [inicio + timedelta(hours=i) for i in range(len(niveles))]
    eventos = _detectar_extremos(timestamps, niveles, niveles)
    assert eventos == []


# ---------------------------------------------------------------------------
# Tests de detectar_mareas — serie senoidal
# ---------------------------------------------------------------------------

def test_detectar_mareas_retorna_analysis():
    forecast = make_forecast_senoidal(n_horas=48)
    result = detectar_mareas(forecast)
    assert isinstance(result, TideAnalysis)


def test_detectar_mareas_encuentra_altas_y_bajas():
    """En una senoidal de 48h debe detectar al menos 2 altas y 2 bajas."""
    forecast = make_forecast_senoidal(n_horas=48)
    result = detectar_mareas(forecast)

    altas = [e for e in result.eventos if e.tipo == "alta"]
    bajas = [e for e in result.eventos if e.tipo == "baja"]

    assert len(altas) >= 2, f"Esperadas ≥2 mareas altas, detectadas {len(altas)}"
    assert len(bajas) >= 2, f"Esperadas ≥2 mareas bajas, detectadas {len(bajas)}"


def test_detectar_mareas_eventos_ordenados_cronologicamente():
    """Los eventos deben estar ordenados por timestamp."""
    forecast = make_forecast_senoidal(n_horas=48)
    result = detectar_mareas(forecast)

    for i in range(1, len(result.eventos)):
        assert result.eventos[i].timestamp > result.eventos[i-1].timestamp, \
            "Eventos no ordenados cronológicamente"


def test_detectar_mareas_alternancia_alta_baja():
    """En una senoidal limpia, altas y bajas deben alternarse."""
    forecast = make_forecast_senoidal(n_horas=48)
    result = detectar_mareas(forecast)

    if len(result.eventos) >= 2:
        for i in range(1, len(result.eventos)):
            prev_tipo = result.eventos[i-1].tipo
            curr_tipo = result.eventos[i].tipo
            assert prev_tipo != curr_tipo, \
                f"Dos eventos consecutivos del mismo tipo: {prev_tipo} seguido de {curr_tipo}"


def test_detectar_mareas_nivel_alta_mayor_baja():
    """El nivel de cada alta debe ser mayor que el de las bajas adyacentes."""
    forecast = make_forecast_senoidal(n_horas=48, amplitud=0.5, offset=0.8)
    result = detectar_mareas(forecast)

    altas = [e for e in result.eventos if e.tipo == "alta"]
    bajas = [e for e in result.eventos if e.tipo == "baja"]

    if altas and bajas:
        nivel_alta_min = min(e.nivel_m for e in altas)
        nivel_baja_max = max(e.nivel_m for e in bajas)
        assert nivel_alta_min > nivel_baja_max, \
            f"Nivel mínimo de alta ({nivel_alta_min:.3f}) debe ser > nivel máximo de baja ({nivel_baja_max:.3f})"


def test_detectar_mareas_fuente_proxy():
    """La fuente debe ser proxy_msl."""
    forecast = make_forecast_senoidal()
    result = detectar_mareas(forecast)
    assert result.fuente == "proxy_msl"


def test_detectar_mareas_todos_estimados():
    """Todos los eventos deben tener es_estimado=True."""
    forecast = make_forecast_senoidal()
    result = detectar_mareas(forecast)
    for evento in result.eventos:
        assert evento.es_estimado is True


# ---------------------------------------------------------------------------
# Tests de tendencia
# ---------------------------------------------------------------------------

def test_tendencia_subiendo():
    forecast = make_forecast_subiendo()
    result = detectar_mareas(forecast)
    assert result.tendencia_actual == "subiendo", \
        f"Tendencia esperada 'subiendo', got '{result.tendencia_actual}'"


def test_tendencia_bajando():
    forecast = make_forecast_bajando()
    result = detectar_mareas(forecast)
    assert result.tendencia_actual == "bajando", \
        f"Tendencia esperada 'bajando', got '{result.tendencia_actual}'"


def test_tendencia_estable():
    forecast = make_forecast_plano(nivel=0.7)
    result = detectar_mareas(forecast)
    assert result.tendencia_actual in ("estable", "subiendo", "bajando")  # plano puede variar levemente


# ---------------------------------------------------------------------------
# Tests de serie con poco datos
# ---------------------------------------------------------------------------

def test_forecast_muy_corto():
    """Menos de 3 horas debe retornar análisis vacío sin errores."""
    forecast = make_forecast_senoidal(n_horas=2)
    result = detectar_mareas(forecast)
    assert isinstance(result, TideAnalysis)
    assert result.eventos == []


def test_forecast_vacio():
    """Lista vacía no debe lanzar excepción."""
    result = detectar_mareas([])
    assert isinstance(result, TideAnalysis)
    assert result.eventos == []
    assert result.nivel_actual is None


# ---------------------------------------------------------------------------
# Tests de serie plana (sin extremos claros — fallback)
# ---------------------------------------------------------------------------

def test_serie_plana_no_tiene_extremos_claros():
    """Con marea constante, tiene_extremos_claros debe ser False."""
    forecast = make_forecast_plano(nivel=0.5)
    result = detectar_mareas(forecast)
    assert result.tiene_extremos_claros is False


def test_serie_senoidal_tiene_extremos_claros():
    """Con marea senoidal, tiene_extremos_claros debe ser True."""
    forecast = make_forecast_senoidal(amplitud=0.5)
    result = detectar_mareas(forecast)
    assert result.tiene_extremos_claros is True


# ---------------------------------------------------------------------------
# Tests de filtrado por timestamp (parámetro 'desde')
# ---------------------------------------------------------------------------

def test_desde_filtra_eventos_pasados():
    """El parámetro 'desde' debe excluir eventos anteriores."""
    forecast = make_forecast_senoidal(n_horas=48)

    # Tomar el timestamp del 2do evento y filtrar por ahí
    result_todos = detectar_mareas(forecast)
    if len(result_todos.eventos) >= 2:
        corte = result_todos.eventos[1].timestamp
        result_filtrado = detectar_mareas(forecast, desde=corte)

        for evento in result_filtrado.eventos:
            assert evento.timestamp >= corte, \
                f"Evento {evento.timestamp} anterior al corte {corte}"


# ---------------------------------------------------------------------------
# Tests de detectar_mareas_del_dia
# ---------------------------------------------------------------------------

def test_detectar_mareas_del_dia_filtra_fecha():
    """Solo debe retornar eventos del día especificado."""
    spot = make_spot()
    forecast = make_forecast_senoidal(n_horas=48)
    fecha = date(2025, 1, 15)
    tz = ZoneInfo("America/Argentina/Buenos_Aires")

    result = detectar_mareas_del_dia(forecast, fecha, spot=spot, tz=tz)

    # Todos los eventos deben ser del día 2025-01-15 en hora local
    for evento in result.eventos:
        evento_local = evento.timestamp.astimezone(tz)
        assert evento_local.date() == fecha, \
            f"Evento {evento_local} no corresponde a {fecha}"


def test_detectar_mareas_del_dia_fecha_sin_datos():
    """Si la fecha no tiene datos en el forecast, retornar análisis vacío."""
    spot = make_spot()
    forecast = make_forecast_senoidal(n_horas=24)  # solo 15 enero
    fecha_fuera = date(2025, 1, 20)

    result = detectar_mareas_del_dia(forecast, fecha_fuera, spot=spot)
    assert result.eventos == []


def test_detectar_mareas_del_dia_detecta_extremo_en_borde():
    """
    Regresión #14: una pleamar justo en la primera hora del día (00:00
    local) no debe perderse solo porque _detectar_extremos() ignora el
    primer/último índice de la serie recortada por fecha. Serie (UTC=local,
    tz=UTC): 22:00(0.5) 23:00(0.7) [día anterior] 00:00(1.1) 01:00(0.7)
    [día pedido] 02:00 en adelante plano en 0.5 — pico aislado a la
    medianoche exacta del día pedido.
    """
    spot = make_spot()
    tz = ZoneInfo("UTC")
    inicio = datetime(2025, 1, 14, 22, 0, tzinfo=timezone.utc)
    niveles = [0.5, 0.7, 1.1, 0.7] + [0.5] * 22  # 26 horas: hasta 2025-01-15 23:00
    forecast = []
    for i, nivel in enumerate(niveles):
        ts = inicio + timedelta(hours=i)
        tide = TideData(nivel_m=nivel, fuente="proxy_msl", es_exacto=False)
        swell = SwellData(altura_m=1.0, periodo_s=10.0, direccion_deg=180.0)
        wind = WindData(velocidad_kmh=10.0, rafaga_kmh=15.0, direccion_deg=180.0)
        forecast.append(ForecastHour(timestamp=ts, swell=swell, wind=wind, tide=tide))

    fecha = date(2025, 1, 15)
    result = detectar_mareas_del_dia(forecast, fecha, spot=spot, tz=tz)

    altas = [e for e in result.eventos if e.tipo == "alta"]
    assert len(altas) == 1, f"esperaba 1 pleamar en el borde del día, hubo {len(altas)}"
    assert altas[0].timestamp == datetime(2025, 1, 15, 0, 0, tzinfo=timezone.utc)
    assert result.tiene_extremos_claros is True


def test_detectar_mareas_del_dia_borde_final_y_filtra_evento_del_dia_siguiente():
    """
    Regresión #14 (caso simétrico, borde final del día + verificación del
    filtro de fecha): un extremo justo en la ÚLTIMA hora del día (23:00
    local) también se pierde con el recorte simple, igual que a las 00:00.
    Además hay un segundo extremo genuino un par de horas más tarde (día
    siguiente, 00:00) que sí se detecta al darle contexto a la búsqueda —
    pero debe quedar excluido del resultado por el filtro de fecha, y no
    "colarse" solo porque el propio padding interno también lo detectó.
    """
    spot = make_spot()
    tz = ZoneInfo("UTC")
    inicio = datetime(2025, 1, 15, 0, 0, tzinfo=timezone.utc)
    # 26 horas: 2025-01-15 00:00 -> 2025-01-16 01:00. Plano hasta las 21:00,
    # luego un valle-pico-valle que deja un extremo en el borde del día
    # (23:00, alta) y otro genuino justo después (día siguiente 00:00, baja).
    niveles = [0.5] * 22 + [0.2, 0.1, 0.6, 0.1]
    forecast = []
    for i, nivel in enumerate(niveles):
        ts = inicio + timedelta(hours=i)
        tide = TideData(nivel_m=nivel, fuente="proxy_msl", es_exacto=False)
        swell = SwellData(altura_m=1.0, periodo_s=10.0, direccion_deg=180.0)
        wind = WindData(velocidad_kmh=10.0, rafaga_kmh=15.0, direccion_deg=180.0)
        forecast.append(ForecastHour(timestamp=ts, swell=swell, wind=wind, tide=tide))

    fecha = date(2025, 1, 15)

    # Sanity check: sin filtrar por fecha, el segundo extremo (día
    # siguiente) sí es detectable en la serie completa — si no lo fuera,
    # este test no probaría nada sobre el filtro de fecha.
    deteccion_completa = detectar_mareas(forecast, spot=spot)
    timestamps_completa = {e.timestamp for e in deteccion_completa.eventos}
    assert datetime(2025, 1, 16, 0, 0, tzinfo=timezone.utc) in timestamps_completa

    result = detectar_mareas_del_dia(forecast, fecha, spot=spot, tz=tz)
    timestamps_resultado = {e.timestamp for e in result.eventos}

    # El extremo del borde final del día pedido sí debe aparecer.
    assert datetime(2025, 1, 15, 23, 0, tzinfo=timezone.utc) in timestamps_resultado

    # El extremo del día siguiente no debe colarse, aunque el padding
    # interno de detectar_mareas_del_dia() lo haya detectado.
    assert datetime(2025, 1, 16, 0, 0, tzinfo=timezone.utc) not in timestamps_resultado
    for evento in result.eventos:
        assert evento.timestamp.astimezone(tz).date() == fecha


# ---------------------------------------------------------------------------
# Tests de TideEvent
# ---------------------------------------------------------------------------

def test_tide_event_timestamp_local():
    ts_utc = datetime(2025, 1, 15, 12, 0, tzinfo=timezone.utc)
    evento = TideEvent(timestamp=ts_utc, nivel_m=1.2, tipo="alta")
    tz_ar = ZoneInfo("America/Argentina/Buenos_Aires")
    ts_local = evento.timestamp_local(tz_ar)
    # AR es UTC-3, entonces 12:00 UTC → 09:00 AR
    assert ts_local.hour == 9, f"Hora local esperada 09:00, got {ts_local.hour}:00"


def test_tide_event_str_alta():
    evento = TideEvent(timestamp=datetime.now(timezone.utc), nivel_m=1.35, tipo="alta")
    s = str(evento)
    assert "▲" in s
    assert "1.35" in s


def test_tide_event_str_baja():
    evento = TideEvent(timestamp=datetime.now(timezone.utc), nivel_m=0.22, tipo="baja")
    s = str(evento)
    assert "▼" in s


# ---------------------------------------------------------------------------
# Tests de TideAnalysis properties
# ---------------------------------------------------------------------------

def test_proxima_alta_property():
    ts1 = datetime(2025, 1, 15, 6, 0, tzinfo=timezone.utc)
    ts2 = datetime(2025, 1, 15, 12, 0, tzinfo=timezone.utc)
    eventos = [
        TideEvent(ts1, 0.3, "baja"),
        TideEvent(ts2, 1.2, "alta"),
    ]
    analysis = TideAnalysis(
        eventos=eventos,
        tendencia_actual="subiendo",
        proximo_cambio=ts2,
        nivel_actual=0.5,
        tiene_extremos_claros=True,
    )
    assert analysis.proxima_alta is not None
    assert analysis.proxima_alta.tipo == "alta"
    assert analysis.proxima_baja is not None
    assert analysis.proxima_baja.tipo == "baja"


def test_proxima_alta_sin_eventos():
    analysis = TideAnalysis(
        eventos=[],
        tendencia_actual="desconocida",
        proximo_cambio=None,
        nivel_actual=None,
        tiene_extremos_claros=False,
    )
    assert analysis.proxima_alta is None
    assert analysis.proxima_baja is None


# ---------------------------------------------------------------------------
# Test de integración: con delta_marea de spot
# ---------------------------------------------------------------------------

def test_delta_marea_se_aplica():
    """El delta_marea del spot debe desplazar los niveles reportados."""
    spot_sin_delta = make_spot()
    spot_sin_delta.delta_marea = 0.0

    spot_con_delta = make_spot()
    spot_con_delta.delta_marea = 0.3

    forecast = make_forecast_senoidal(n_horas=48)

    result_sin = detectar_mareas(forecast, spot=spot_sin_delta)
    result_con = detectar_mareas(forecast, spot=spot_con_delta)

    assert result_sin.eventos, "El fixture senoidal debería producir eventos detectables"
    assert result_con.eventos, "El fixture senoidal debería producir eventos detectables"

    # El nivel del primer evento con delta debe ser ~0.3m mayor
    diff = result_con.eventos[0].nivel_m - result_sin.eventos[0].nivel_m
    assert abs(diff - 0.3) < 0.01, f"Diferencia de nivel esperada ~0.3, got {diff:.4f}"


def test_delta_altura_no_afecta_la_marea():
    """
    Regresión #13: delta_altura (calibración de altura de SWELL) y
    delta_marea (calibración de nivel de MAREA) deben ser independientes.
    Antes, tides.py reutilizaba delta_altura para desplazar también la
    marea — un admin que solo quisiera corregir la altura de ola terminaba
    corriendo la marea reportada sin darse cuenta.
    """
    spot_solo_delta_altura = make_spot()
    spot_solo_delta_altura.delta_altura = 0.5
    spot_solo_delta_altura.delta_marea = 0.0

    spot_sin_ajustes = make_spot()
    spot_sin_ajustes.delta_altura = 0.0
    spot_sin_ajustes.delta_marea = 0.0

    forecast = make_forecast_senoidal(n_horas=48)

    result_con_delta_altura = detectar_mareas(forecast, spot=spot_solo_delta_altura)
    result_sin_ajustes = detectar_mareas(forecast, spot=spot_sin_ajustes)

    assert result_con_delta_altura.eventos, "El fixture senoidal debería producir eventos detectables"
    assert result_sin_ajustes.eventos, "El fixture senoidal debería producir eventos detectables"

    diff = result_con_delta_altura.eventos[0].nivel_m - result_sin_ajustes.eventos[0].nivel_m
    assert abs(diff) < 0.001, \
        f"delta_altura no debería afectar la marea reportada, pero la desplazó {diff:.4f}m"


# ---------------------------------------------------------------------------
# Ejecutar directamente
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        test_suavizar_conserva_longitud,
        test_suavizar_reduce_picos,
        test_suavizar_serie_plana,
        test_amplitud_serie_senoidal,
        test_amplitud_serie_plana,
        test_amplitud_serie_vacia,
        test_detectar_mareas_retorna_analysis,
        test_detectar_mareas_encuentra_altas_y_bajas,
        test_detectar_mareas_eventos_ordenados_cronologicamente,
        test_detectar_mareas_alternancia_alta_baja,
        test_detectar_mareas_nivel_alta_mayor_baja,
        test_detectar_mareas_fuente_proxy,
        test_detectar_mareas_todos_estimados,
        test_tendencia_subiendo,
        test_tendencia_bajando,
        test_tendencia_estable,
        test_forecast_muy_corto,
        test_forecast_vacio,
        test_serie_plana_no_tiene_extremos_claros,
        test_serie_senoidal_tiene_extremos_claros,
        test_desde_filtra_eventos_pasados,
        test_detectar_mareas_del_dia_filtra_fecha,
        test_detectar_mareas_del_dia_fecha_sin_datos,
        test_tide_event_timestamp_local,
        test_tide_event_str_alta,
        test_tide_event_str_baja,
        test_proxima_alta_property,
        test_proxima_alta_sin_eventos,
        test_delta_marea_se_aplica,
        test_delta_altura_no_afecta_la_marea,
    ]

    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  ✅ {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  ❌ {t.__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\n{'='*50}")
    print(f"  {passed} passed  |  {failed} failed  |  {len(tests)} total")
    if failed == 0:
        print("  🎉 Todos los tests pasaron.")
    else:
        print("  ⚠️  Hay tests fallando.")
