Olas Surfer Bot V2 — Documentación completa
============================================

ARQUITECTURA
============

olas_surfer_bot_v2/
│
├── config/
│   ├── spots/
│   │   ├── argentina.json     ← 8 spots activos
│   │   ├── brasil.json        ← 3 spots (Floripa + SP)
│   │   ├── chile.json         ← 2 spots (Reñaca + Pichilemu)
│   │   ├── peru.json          ← 2 spots (Lima + Trujillo)
│   │   └── costa_rica.json    ← 2 spots (Tamarindo + Jacó)
│   └── scoring_weights.json   ← pesos configurables por tipo de break
│
├── core/
│   ├── scoring/
│   │   ├── engine.py          ← Motor de scoring (5 capas)
│   │   └── models.py          ← Dataclasses: ForecastHour, ScoreBreakdown, etc.
│   ├── forecast/
│   │   ├── provider_base.py   ← ABC del provider pattern
│   │   ├── open_meteo.py      ← IMPLEMENTADO (gratis, sin API key)
│   │   ├── stormglass.py      ← STUB (pago, alta precisión + mareas reales)
│   │   ├── worldtides.py      ← STUB (pago, mareas estaciones globales)
│   │   └── cache.py           ← In-memory (TTL 30min) + stub Redis
│   ├── spots/
│   │   └── registry.py        ← Carga y valida JSONs, navegación país/región/spot
│   └── windows/
│       └── detector.py        ← Detector de ventanas óptimas 48h
│
├── bot/
│   ├── formatters.py          ← Texto Telegram (independiente del bot)
│   ├── keyboards.py           ← Todos los InlineKeyboards
│   └── handlers/
│       └── main.py            ← Handlers: /start, /ajuste, callbacks
│
├── persistence/
│   └── session_store.py       ← SQLite: sesiones + ajustes + favoritos
│
├── tests/
│   ├── test_scoring.py        ← Tests del motor (sin Telegram)
│   └── fixtures/
│       └── sample_forecast.json
│
├── main.py                    ← Entry point (polling o webhook)
├── .env.example
└── requirements.txt


INSTALACIÓN
===========

1. Clonar el repositorio
2. Crear y activar entorno virtual:
     python -m venv venv
     venv\Scripts\activate    (Windows)
     source venv/bin/activate (Linux/macOS)

3. Instalar dependencias:
     pip install -r requirements.txt

4. Configurar entorno:
     cp .env.example .env
     # Editar .env con TELEGRAM_BOT_TOKEN

5. Crear directorio de datos:
     mkdir data

6. Ejecutar:
     python main.py


TESTS (sin Telegram)
====================

# Correr tests del motor de scoring:
python -m pytest tests/ -v

# O sin pytest:
python tests/test_scoring.py

# El motor también se puede probar directamente:
python -c "
from core.scoring.engine import calcular_score
from core.scoring.models import *
from datetime import datetime, timezone

spot = SpotConfig(
    key='test', nombre='Test', ciudad='MDP', pais='AR', region='ba',
    lat=-38.0, lon=-57.5, orientacion_costa_deg=95, tolerancia_swell_deg=45,
    tipo_break='beach', fondo='arena', marea_min_m=0.4, marea_max_m=1.6,
    marea_tipo_efecto='mid_better', swell_altura_min=0.5, swell_altura_max=3.0,
    swell_periodo_min=7.0, viento_max_offshore=35.0, viento_max_onshore=15.0,
)
hour = ForecastHour(
    timestamp=datetime.now(timezone.utc),
    swell=SwellData(altura_m=1.4, periodo_s=14, direccion_deg=95),
    wind=WindData(velocidad_kmh=8, rafaga_kmh=12, direccion_deg=275),
    tide=TideData(nivel_m=1.0)
)
bd = calcular_score(hour, spot)
print(f'{bd.score_100}/100 — {bd.etiqueta}')
print(bd.flags_positivos)
"


MOTOR DE SCORING — 5 CAPAS
===========================

Capa 1 — Energía del swell (H² × T)
  Una ola de 1m/14s tiene más calidad que 1.5m/7s.
  Normalizada con tanh para no recompensar extremos irreales.

Capa 2 — Período
  < 7s  → windchop (0.30)
  7–10s → corto pero surfeable (0.55)
  10–14s → bueno (0.88)
  > 14s → groundswell puro (1.0)

Capa 3 — Dirección relativa swell–costa
  Calcula el ángulo de incidencia del swell respecto a la orientación de la costa.
  Tolerancia configurable por spot (reef < point < beach).

Capa 4 — Viento
  offshore (viento de tierra) → ideal → hasta 0.98
  cross → neutro → 0.65–0.80
  onshore (viento del mar) → destruye la ola → 0.05–0.55

Capa 5 — Marea (proxy MSL)
  Rango óptimo configurable por spot.
  Penalización gradual al salir del rango.

NOTA IMPORTANTE SOBRE MAREAS:
  Open-Meteo usa sea_level_height_msl que incorpora componentes
  mareales pero NO está referenciado a LAT/MLLW náutico estándar.
  La precisión costera es limitada. Se usa como indicador relativo.
  Para mareas más precisas, activar Stormglass o WorldTides (pago).

Pesos por tipo de break (configurables en scoring_weights.json):
              beach  reef   point
  energia:    0.28   0.24   0.26
  periodo:    0.14   0.20   0.18
  dir_swell:  0.16   0.22   0.24
  viento:     0.27   0.20   0.20
  marea:      0.15   0.14   0.12


VENTANAS ÓPTIMAS 48H
====================

El detector analiza hora por hora, agrupa bloques contiguos sobre
el umbral de score (default: 0.60), y retorna las Top 3 ventanas
ordenadas por score promedio con:
  - Inicio y fin del bloque
  - Hora pico
  - Score promedio y máximo
  - Descripción legible: "Mañana 07–10h: offshore · groundswell 14s · 1.4m"


AGREGAR UN SPOT NUEVO
=====================

Solo hay que agregar un JSON en config/spots/<pais>.json:

{
  "nombre": "Nombre del Spot",
  "ciudad": "Ciudad",
  "lat": -38.0,
  "lon": -57.5,
  "orientacion_costa_deg": 95,     ← hacia donde mira la playa (0=N, 90=E...)
  "tolerancia_swell_deg": 45,       ← cuánto puede desviarse el swell (reef <30, beach 50+)
  "tipo_break": "beach",            ← "beach" | "reef" | "point"
  "fondo": "arena",                 ← "arena" | "roca" | "coral"
  "marea": {
    "rango_optimo_min_m": 0.4,
    "rango_optimo_max_m": 1.6,
    "tipo_efecto": "mid_better"     ← "low_better" | "high_better" | "mid_better"
  },
  "swell": {
    "altura_min_m": 0.5,
    "altura_max_m": 3.0,
    "periodo_min_s": 7
  },
  "viento": {
    "vel_max_offshore_kmh": 35,
    "vel_max_onshore_kmh": 15
  },
  "fuente_datos": "open-meteo",
  "notas": "Descripción del spot para el usuario."
}

Reiniciar el bot y el spot ya está disponible sin cambios de código.


ACTIVAR STORMGLASS (mareas reales)
===================================

1. Registrar en stormglass.io y obtener API key
2. Agregar al .env: STORMGLASS_API_KEY=tu_key
3. Implementar core/forecast/stormglass.py (ver el stub incluido)
4. Cambiar "fuente_datos": "stormglass" en los spots deseados


DEPLOY EN RENDER
================

1. Subir código a GitHub
2. Crear nuevo Web Service en Render
3. Variables de entorno:
     TELEGRAM_BOT_TOKEN = tu_token
     WEBHOOK_URL = https://tu-app.onrender.com
     SESSION_DB_PATH = /data/sessions.db
4. Crear Persistent Disk en Render y montarlo en /data
5. Build command: pip install -r requirements.txt
6. Start command: python main.py


PLAN DE MONETIZACIÓN — FREE vs PRO
====================================

FREE (siempre gratuito):
  - Condiciones actuales (ahora mismo)
  - Score con etiqueta (★★★☆☆)
  - Flags explicativos (offshore, período, etc.)
  - Navegación país → región → spot
  - Spots de todos los países

PRO ($4.99/mes o $39/año):
  - Ventanas óptimas 48h (detectar_ventanas)
  - Breakdown técnico completo (sub-scores + barras)
  - Favoritos (hasta 10 spots guardados)
  - Acceso directo a favoritos desde /start
  - Alertas configurables (próxima ventana > X estrellas)
  - Prioridad en soporte

PRO+ / BUSINESS (futuro, para escuelas de surf / balnearios):
  - API REST (webhook propio)
  - Branding personalizado
  - Spots privados configurables
  - Dashboard de métricas

DIFERENCIADORES REALES frente a Surfline/Magic Seaweed:
  - Especificidad local: cada spot tiene orientación, tolerancias y rango
    de marea propios. No es el mismo pronóstico para toda la costa.
  - Bot conversacional en Telegram: donde el usuario ya está. Sin app.
  - Descripción en lenguaje surfer (no meteorólogo).
  - Gratuito con features útiles (no solo vista de 3 días).
  - América Latina first: Surfline tiene poca cobertura de spots locales.
  - Código abierto / hackeable: escuelas de surf pueden deployer su propia
    instancia con sus spots privados.

IMPLEMENTACIÓN DE PAGOS (cuando tengas usuarios):
  1. Stripe Payment Links (más simple) o Stripe Checkout
  2. El user paga → Stripe webhook → actualizar sessions.db con plan="pro"
  3. La lógica _es_pro(user_id) ya está preparada para esto
  4. Alternativamente: Telegram Stars (pagos nativos de Telegram)


VARIABLES DE ENTORNO COMPLETAS
===============================

TELEGRAM_BOT_TOKEN     Obligatorio
WEBHOOK_URL            Obligatorio en producción (Render)
PORT                   Render lo asigna; default 10000
SESSION_DB_PATH        Default: data/sessions.db
FORECAST_CACHE_TTL_SECONDS  Default: 1800 (30 min)
REDIS_URL              Opcional; activa RedisForecastCache
ADMIN_USER_IDS         IDs Telegram con acceso a /ajuste, separados por coma
STORMGLASS_API_KEY     Opcional; proveedor de mareas de alta precisión
WORLDTIDES_API_KEY     Opcional; proveedor de mareas global
