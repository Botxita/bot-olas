# Olas Surfer Bot — Contexto de Proyecto (Handoff para Claude Code)

> **Instrucción de operación:** Olas Surfer Bot es un bot Python de pronóstico de surf en Telegram desplegado en Render. Claude Code actúa como desarrollador principal y tiene la decisión final sobre la implementación; Codex actúa como auditor independiente, sin introducir cambios paralelos sin coordinación. **Antes de modificar código, inspeccionar el repositorio completo y reconciliar el estado real con este documento.** No asumir que los componentes descritos abajo (ya sea de la narrativa "V1" o "V2") siguen vigentes solo porque están documentados aquí — este archivo mezcla historia narrada (de sesiones de chat) con estructura real de código, y ambas fuentes tienen huecos y posibles contradicciones entre sí. Cuando haya discrepancia entre este documento y el código, **informar la discrepancia antes de decidir qué conservar.**

---

## ⚠️ ALERTA DE RECONCILIACIÓN — leer primero

Este proyecto tiene **dos relatos de arquitectura que no coinciden entre sí** y deben reconciliarse contra el repo real antes de tocar nada:

**Relato A (sesiones de trabajo directo, más reciente/operativo):**
Estructura simple y plana — `detector.py`, `tides.py`, `open_meteo.py`, favoritos por usuario con SQLite, bot ya deployado y funcionando en producción sobre Render con webhook confirmado, UptimeRobot pegándole a `/health`. Bugs recientes resueltos: agrupamiento de ventanas que cruzan medianoche, dirección de marea. Pendientes conocidos: persistencia de favoritos entre sesiones, navegación "atrás" rota, onboarding de nivel de surfista, resolución horaria de marea (limitación de la API, no bug).

**Relato B (documento del usuario, aparentemente trabajado en ChatGPT, describe un rediseño "V2"):**
Arquitectura mucho más elaborada — `core/scoring/engine.py` con 5 componentes de score (energía, período, dirección de swell, viento, marea), pesos distintos por tipo de rompiente (beach/reef/point), `SpotConfig` con `tolerancia_swell_deg`, `core/analysis/` con módulos separados (daylight, tides, best_hour, weekly, hourly_view), 17 spots en 5 países (Argentina, Brasil, Chile, Costa Rica, Perú), timezone IANA por spot, providers pattern con Stormglass/WorldTides como stubs, tests unitarios por módulo, sin funciones Pro.

**Estas dos narrativas podrían describir el mismo proyecto en distintos momentos, o podrían haber divergido (uno implementado, el otro solo planificado en el chat).** La primera tarea de Claude Code, según pide el propio documento fuente, es **auditar el checkout real** y producir un mapa de qué de todo esto existe efectivamente en código, no asumir ninguna de las dos versiones.

---

## 1. Qué debe hacer Claude Code primero (auditoría de estado, no código)

Antes de programar, generar un inventario real del repositorio:

- Commit actual, branch activa, remote de GitHub
- Entry point real (`main.py` u otro)
- Versión de Python (`.python-version`) y de `python-telegram-bot`
- **Modo Telegram: webhook vs polling** (punto de máxima incertidumbre — hay evidencia histórica de ambos)
- Modo Render: comando de inicio, puerto, endpoint de salud (`/health`)
- Fuente del forecast: ¿real (Open-Meteo) o hay restos de datos simulados/mock fuera de tests?
- Configuración de spots: ¿existe `spots_config.json` y/o restos de `ajustes_spots.py` / `ajustes_spots.json`? ¿Hay doble fuente de verdad?
- Cantidad real de spots configurados, países, timezones
- Motor de scoring activo: ¿fórmula simple (altura/período/viento, umbral 65) o engine de 5 componentes con pesos por tipo de break?
- Módulos de análisis presentes: daylight, tides, best_hour, hourly_view, weekly, ventanas 48h
- Persistencia: ¿SQLite activo?, ¿qué se guarda (favoritos, estado de sesión)?
- Tests: cuántos hay, cuáles pasan
- Restos de funcionalidad "Pro" (`_es_pro()` u otros) — deben eliminarse o quedar siempre en `True`, el producto es 100% gratuito
- Restos de `keep_alive.py` / Flask tipo Replit — determinar si siguen siendo necesarios dado el modo Telegram real

## 2. Objetivo del producto (esto sí es estable en ambos relatos)

Un asistente de surf que convierte un forecast marino genérico en una recomendación local específica por spot: `datos crudos → interpretación según el spot → score → mejores horarios → recomendación comprensible`. No es un wrapper de Open-Meteo. Debe responder: ¿vale la pena hoy?, ¿cuál es la mejor hora?, ¿qué ventanas hay en 48h?, ¿qué día pinta mejor esta semana?, ¿por qué ese score?

Separación de responsabilidades que debe preservarse conceptualmente (nivel de implementación real: verificar): proveedor meteorológico ≠ scoring ≠ configuración del spot ≠ presentación Telegram.

## 3. Estado operativo conocido (de sesiones directas — más confiable para producción actual)

- **Deploy:** Render, tier gratuito, deploy automático vía GitHub
- **Uptime:** UptimeRobot pegándole a `/health` (soporta GET y HEAD) para evitar sleep del tier gratuito
- **Comunicación:** en español
- **Bugs resueltos recientemente:** agrupamiento de ventanas que cruzan medianoche (`detector.py`), error de cálculo de dirección de marea (`tides.py`)
- **Features implementadas:** bloques de ventana de surf ("Próximas olas"), temperatura superficial del mar, forecast a 7 días, tendencia de marea, reintentos de red en `open_meteo.py`, favoritos por usuario en SQLite, horas locales timezone-aware, filtrado por horas de luz diurna
- **Pendientes diferidos:** navegación "atrás" rota desde favoritos, onboarding de nivel de surfista, resolución horaria de marea (confirmado como limitación de datos de la API, no bug de formato). *Corrección tras reconciliación (ver `docs/PROJECT_STATUS.md`): la persistencia de favoritos entre sesiones SÍ existe a nivel de datos en SQLite — lo que queda pendiente es solo verificar en vivo que el handler la usa bien, no la persistencia en sí.*

## 4. Lecciones técnicas ya validadas (no re-descubrir)

- PTB v13 con Tornado: la estructura interna de `WebhookServer` requiere inspeccionar `webhook_server.http_server.request_callback` — el patching directo de `request_callback` sobre el objeto `application` falla
- Render tier gratuito expone un solo puerto — configurar UptimeRobot en consecuencia
- Errores 429 de Open-Meteo durante redeploys rápidos son por agotamiento de cache en memoria, no un bug estructural
- Viento de Open-Meteo en esta configuración viene directo en km/h — **no reconvertir** de m/s
- `sea_level_height_msl` de Open-Meteo **no equivale** a una predicción de marea náutica referenciada a datum local — tratar como proxy de nivel del mar / MSL, no vender precisión náutica inexistente
- Un bug recurrente entre sesiones: aplicar fixes siempre sobre el archivo actual real — subir versiones desactualizadas generó retrabajo varias veces

## 5. Arquitectura V2 propuesta/narrada (verificar cuánto de esto está realmente implementado)

```
Provider/API (Open-Meteo primario; Stormglass/WorldTides como stubs opcionales, no activos)
    ↓
Forecast normalizado
    ↓
SpotConfig (lat, lon, tz, orientacion_costa_deg, tolerancia_swell_deg, tipo_break, fondo, swell, viento, marea)
    ↓
Scoring engine (5 componentes: energía, período, dirección swell, viento, marea — pesos distintos por tipo de break)
    ↓
Análisis temporal (daylight, tides, best_hour, hourly_view, weekly, ventanas)
    ↓
Formatter
    ↓
Telegram
```

Pesos por tipo de rompiente (narrados, verificar si están en código):

| Tipo | Energía | Período | Dirección | Viento | Marea |
|------|---------|---------|-----------|--------|-------|
| beach | .30 | .15 | .15 | .25 | .15 |
| reef | .25 | .20 | .20 | .20 | .15 |
| point | .28 | .18 | .22 | .20 | .12 |

## 6. Spots (narrado — confirmar contra `spots_config.json` real, no inventar)

17 spots en 5 países: Argentina (8), Brasil (3), Chile (2), Costa Rica (2), Perú (2).

Confirmados por nombre en el historial: Quequén, Miramar, Chapadmalal, Mar del Plata, Varese (Mar del Plata) en Argentina; Barra de Ibiraquera, Praia da Ferrugem, Praia do Rosa en Brasil; Punta Hermosa, Chicama en Perú. Los demás nombres (resto de Argentina, Chile, Costa Rica) **no están confirmados** — no reconstruir de memoria.

Timezones IANA narradas por país:
```
Argentina    America/Argentina/Buenos_Aires
Brasil       America/Sao_Paulo
Chile        America/Santiago
Costa Rica   America/Costa_Rica
Perú         America/Lima
```

## 7. Decisiones de producto que deben preservarse

- **Sin versión Pro.** Todo gratuito: ventanas 48h, esta semana, breakdown, hora a hora, por fecha. `_es_pro()` no debe existir como función (confirmado que no existe en el código real — ver `docs/PROJECT_STATUS.md`); un parámetro `es_pro: bool = True` como flag de compatibilidad sí es intencional. No reintroducir paywalls.
- Selector de fecha de 7 días (Hoy, Mañana, Pasado, +3...+6) no es funcionalidad premium.
- Breakdown del score (mostrar qué componente perjudica/beneficia) es valioso para poder auditar el motor, no solo el número final.

## 8. Convenciones de trabajo con el usuario (Ivan)

- Trabaja iterativamente: deploya a Render después de cada cambio y reporta errores con capturas
- Prefiere pensar la solución "en frío" antes de implementar
- A veces trabaja desde el celular, vuelve a la compu para compartir archivos de código
- Espera comandos git al final de cada tarea en formato exacto:
  ```
  git add [archivos]
  git commit -m "..."
  git push origin HEAD:main
  ```
- Espera reportes de bugs numerados y soluciones directas a nivel de código, sin explicaciones extensas
- Requiere el archivo completo actual como referencia antes de cualquier cambio — parcheo sin ver el estado actual causó pérdida de modificaciones en el pasado

## 9. Reparto de responsabilidades Claude Code / Codex (según lo pedido por el usuario)

1. Claude Code inspecciona el repo, entiende el problema, propone/implementa
2. Se corren los tests existentes
3. Codex audita el diff contra requerimientos, arquitectura y regresiones — no reemplaza la implementación de Claude
4. Codex informa errores concretos, casos límite, deuda técnica introducida, discrepancias
5. Claude Code evalúa los hallazgos y decide la versión final
6. Se corren tests de nuevo antes del commit

Para el scoring en particular, Codex debería auditar tanto el `score_total` como cada componente individual (`score_energia`, `score_periodo`, `score_dir_swell`, `score_viento`, `score_marea`) — un total razonable puede esconder una implementación incorrecta en los componentes.

## 10. Reglas explícitas para esta migración

- No refactorizar ni reemplazar componentes funcionales solo por preferencia estilística
- No restaurar automáticamente el umbral histórico de ventana `score >= 65` (V1) si el engine V2 ya tiene otro criterio implementado
- No introducir dependencias pagas (Stormglass, WorldTides) solo porque están contempladas arquitectónicamente
- No asumir que la estructura V2 (`core/scoring`, `core/analysis`) reemplazó efectivamente a los archivos planos (`detector.py`, `tides.py`, `open_meteo.py`) sin confirmarlo en el repo
- Ejecutar tests antes y después de cada cambio
