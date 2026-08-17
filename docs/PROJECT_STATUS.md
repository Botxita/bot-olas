# PROJECT_STATUS.md — bot-olas

Documento vivo. Se actualiza a medida que avanza el proyecto — no es un historial estático, es el estado actual + decisiones + backlog.

## Estado actual (última auditoría: sesión de Claude Code, checkout de `main`)

Repo clonado desde `https://github.com/Botxita/bot-olas.git`, rama `main`, remote único `origin`.

Arquitectura real implementada = la V2 completa, no la plana:

```
main.py                          — entry point (webhook si hay WEBHOOK_URL, sino polling)
bot/
  formatters.py, keyboards.py, handlers/main.py
core/
  scoring/engine.py, models.py   — motor 5 capas (energía, período, dir. swell, viento, marea)
  analysis/best_hour.py, daylight.py, hourly_view.py, tides.py, weekly.py
  forecast/open_meteo.py (implementado), stormglass.py, worldtides.py (stubs), cache.py, provider_base.py
  spots/registry.py
  windows/detector.py
persistence/session_store.py     — SQLite (sessions, spot_adjustments, user_favorites)
config/scoring_weights.json, config/spots/{argentina,brasil,chile,costa_rica,peru,uruguay}.json
tests/ (7 archivos, 169 tests — 154 pasan, 15 fallan)
```

## ✅ Prioridad 1 — Seguridad (resuelta en `cf0f5fe`)

`.env` estaba trackeado en git (commit `cbc4472`) con `TELEGRAM_BOT_TOKEN` real en texto plano, y seguía trackeado en HEAD (no solo en historia vieja). Resuelto: token rotado vía @BotFather, env var actualizada en Render, `.env` destrackeado (`git rm --cached .env`, commit `cf0f5fe`), revisado por Codex antes de commitear.

**Riesgo residual aceptado:** el token viejo (ya inválido) sigue visible en commits anteriores a `cf0f5fe`. No se purgó el historial porque el repo es privado — decisión explícita de Ivan, revisable si el repo pasa a público.

## Datos confirmados vs. lo narrado en migraciones anteriores

| Punto | Confirmado en código |
|---|---|
| Modo Telegram | Webhook en producción (`use_webhook = bool(WEBHOOK_URL)`), fallback a polling en dev local |
| Health check | `/health` parchado sobre `webhook_server.http_server.request_callback`, soporta GET y HEAD |
| Forecast | Real, Open-Meteo únicamente. `wind_speed_unit="kmh"` explícito. Retry backoff 2/5/10s. Stormglass/WorldTides son stubs sin llaves |
| Config de spots | Única fuente: `config/spots/*.json` (6 países). No hay `ajustes_spots.py` — eso vive en tabla `spot_adjustments` de SQLite |
| Spots reales | **59 spots en 6 países** (AR 11, BR 15, CL 7, CR 8, PE 12, UY 6) — no los "17 en 5 países" de documentación previa. Uruguay existe en código y no estaba documentado en ningún lado |
| Motor de scoring | 5 componentes activo. Pesos reales en `config/scoring_weights.json` parecidos pero no idénticos a lo narrado en sesiones anteriores |
| Umbral de ventana óptima | `0.60` en escala 0–1 (`umbral_ventana_optima`) — no el histórico "score ≥ 65" de V1 |
| Persistencia | SQLite activo, 3 tablas. Favoritos SÍ tienen persistencia real a nivel de datos (contradice lo que se creía pendiente) — falta confirmar que el handler la usa bien en vivo |
| Tests | 169 tests, 15 fallan por fixtures con fecha fija 2025 vs. reloj real — no es bug funcional |
| Restos "Pro" | `_es_pro()` no existe. `es_pro: bool = True` queda como parámetro de compatibilidad intencional |
| keep_alive.py / Flask Replit | No existe. `Flask==3.0.3` era deuda muerta (0 usos; webhook y `/health` corren sobre Tornado vía PTB v13) — eliminado de `requirements.txt` |

## Backlog (por prioridad sugerida)

1. ~~Rotar `TELEGRAM_BOT_TOKEN` y limpiar `.env` del historial de git.~~ Resuelto en `cf0f5fe` (ver Prioridad 1 arriba).
2. Actualizar los 15 tests con fechas fijas para que no dependan del reloj real.
3. Verificar en vivo la navegación "atrás" desde favoritos (`bot/handlers/main.py`).
4. ~~Confirmar si Flask en `requirements.txt` está realmente usado en algún lado o es deuda muerta.~~ Resuelto: era deuda muerta, eliminado.
5. Onboarding de nivel de surfista (principiante/intermedio/avanzado) para ajustar umbrales de "bueno/regular/malo" — pedido original de Ivan, no implementado.
6. Resolución horaria de marea con minutos exactos — limitación de datos de Open-Meteo, no bug de formato; evaluar si vale la pena un proveedor de mareas dedicado (Stormglass/WorldTides) a futuro.
7. Borrar `power.txt` (archivo suelto con ruta local vieja, aparenta ser basura de un `cd`/`activate` pegado por error).

## Decisiones de producto ya tomadas (no volver a discutir sin razón)

- Sin versión Pro: todo gratis (ventanas 48h, semana, breakdown, hora a hora, por fecha).
- Timezone local del spot en toda la UX (IANA, vía `zoneinfo`), nunca UTC implícito.
- Marea de Open-Meteo se presenta como proxy MSL, nunca como predicción náutica de precisión.
- Open-Meteo es el proveedor primario; Stormglass/WorldTides quedan como stubs, no se activan sin necesidad real.

## Convenciones de trabajo con Ivan

- Comunicación en español.
- Deploys a Render después de cada cambio, reporta errores con capturas.
- Prefiere pensar la solución "en frío" antes de implementar.
- Espera comandos git al final en formato exacto: `git add` / `git commit -m "..."` / `git push origin HEAD:main`.
- Requiere ver el archivo completo actual antes de cualquier cambio — parcheo sin contexto completo generó pérdida de trabajo en el pasado.
