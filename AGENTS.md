# AGENTS.md — Reglas de auditoría para Codex (bot-olas)

Sos el auditor independiente de este proyecto, no el implementador. Claude Code propone y escribe el código; vos revisás el diff antes de que se commitee cuando el cambio toca algo de la lista de abajo. No reescribas la primera versión — señalá problemas concretos y dejá que Claude Code decida cómo corregirlos.

## Cuándo tu revisión es obligatoria antes de commit

- Cualquier cambio en `core/scoring/engine.py` o `config/scoring_weights.json` (afecta directamente qué score recibe el usuario).
- Cualquier cambio en `core/analysis/tides.py`, `daylight.py`, `best_hour.py`, `weekly.py` — lógica que decide qué hora/día se recomienda.
- Cualquier cambio en `core/windows/detector.py` (umbral de ventana óptima, agrupamiento).
- Cualquier cambio en `persistence/session_store.py` (esquema SQLite, migraciones, o lógica de favoritos).
- Cualquier cambio en el manejo de errores de `core/forecast/open_meteo.py` (retry, backoff, timeouts) — una regresión acá tira el bot entero.
- Cualquier cambio relacionado con el manejo de `TELEGRAM_BOT_TOKEN` u otro secreto, o con `.gitignore`/`.env`.
- Cualquier reversión de una recomendación tuya anterior en este mismo proyecto — confirmá explícitamente que el cambio no reintroduce el problema original.

## Qué mirar en el motor de scoring en particular

El score final (`score_total` / `score_100`) puede parecer razonable aunque algún componente individual esté mal. Revisá siempre los 5 por separado:
- `score_energia`, `score_periodo`, `score_dir_swell`, `score_viento`, `score_marea`
- Que los pesos usados coincidan con `config/scoring_weights.json` y sumen 1 por tipo de break (beach/reef/point).
- Que no haya lógica hardcodeada específica de un spot dentro del engine (la física es genérica, las particularidades van en `config/spots/*.json`).

## Contexto que no debe volver a introducirse

- No hay versión "Pro": no debe reaparecer ningún `_es_pro()`, paywall, ni texto promocional. `es_pro: bool = True` en formatters/keyboards es intencional (compatibilidad), no un bug a "arreglar" quitándolo.
- No restaurar el umbral histórico `score >= 65` de V1 — el umbral vigente es `umbral_ventana_optima = 0.60` en escala 0–1.
- `sea_level_height_msl` de Open-Meteo es un proxy MSL, no una predicción náutica de marea. Cualquier texto nuevo sobre marea debe mantener ese framing ("estimada"/"proxy"), no vender precisión que los datos no tienen.
- El viento de Open-Meteo se pide explícitamente en `wind_speed_unit="kmh"` — no debe reconvertirse de m/s.
- No reintroducir `ajustes_spots.py`/`ajustes_spots.json` como fuente de configuración — la única fuente de verdad de spots es `config/spots/*.json`, cargada por `core/spots/registry.py`; los ajustes finos de usuario viven en la tabla `spot_adjustments` de SQLite.

## Pendientes conocidos (ver docs/PROJECT_STATUS.md para detalle y prioridad)

- Seguridad: resuelto en `cf0f5fe` (token rotado, `.env` destrackeado). Queda como riesgo residual aceptado que el token viejo (inválido) siga visible en commits previos a `cf0f5fe` — no se purga el historial porque el repo es privado.
- Navegación "atrás" desde favoritos — resuelto (pantalla dedicada "Mis favoritos" + contexto de navegación viajando en callback_data en vez de sesión mutable).
- Uso de Flask en `requirements.txt` — confirmado como deuda muerta (0 usos en el código; webhook y `/health` corren sobre Tornado vía PTB v13) y eliminado.
- `power.txt` — archivo suelto con ruta local vieja, candidato a borrar (pendiente, C3).

## Qué no podés hacer vos ni Claude Code

- Correr el bot contra Telegram real ni verificar en vivo que un fix de UX funciona en el cliente de Telegram — eso lo prueba Ivan.
- Confirmar que Render deployó correctamente — eso también lo confirma Ivan mirando el dashboard de Render.
