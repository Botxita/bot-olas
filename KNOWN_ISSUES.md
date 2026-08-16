# KNOWN_ISSUES.md — bot-olas

Documento vivo de auditoría de fondo sobre `core/` (scoring, analysis, windows) y, desde el hallazgo #32, sobre **calidad de datos geográficos** de `config/spots/*.json`. Generado a partir de una revisión conjunta Claude Code + Codex (auditor independiente, ver `AGENTS.md`).

**Estado (actualizado tras 3 rondas de fixes de código + 1 ronda de auditoría de datos + 2 rondas de fixes de datos):** de los **55 hallazgos documentados** (31 de código + 24 de datos geográficos), **30 fueron corregidos** — 13 de código en 3 grupos priorizados por Ivan, más 17 de datos geográficos:
- **10 verificados por Ivan en Google Maps** (la fuente más confiable): #33, #34, #41, #43, #44, #46, #47, #48, #49, #53.
- **7 del "grupo confiable"** aplicados a partir de estimaciones de Codex (punto medio de rango sugerido, o coordenada estimada), **sin verificación cartográfica** de Ivan: #32, #37, #38, #39, #40, #50, #51 — tratar con más cautela que los 10 anteriores.

#45 fue verificado y confirmado que **no** necesita corrección. #36 y #54 fueron investigados pero la evidencia encontrada no fue concluyente (marcados `🔍 VERIFICADO — EVIDENCIA INSUFICIENTE`, distinto de resuelto o sin tocar; para #36 la orientación sí se corrigió como parte de #40, solo las coordenadas quedaron sin resolver). El resto (#35, #42, #52, #55) sigue como auditoría sin aplicar, sin ninguna acción todavía — ver advertencia de confiabilidad en esa sección antes de actuar. Los demás hallazgos de código sin marcar (#2, #6-#8, #14-#21, #25-#29, #31) también siguen abiertos.

- **Grupo 1** (`fix-grupo1-scoring-critico`): #5, #12, #22.
- **Grupo 2** (`fix-grupo2-analisis-y-detector`): #1, #3, #4, #11, #23, #24.
- **Grupo 3** (`fix-grupo3-riesgos-configuracion`): #9, #10, #13, #30.

Cada fix pasó por revisión obligatoria de Codex antes de commitear (ver `AGENTS.md`) y sumó tests de regresión — el test suite completo sigue en 217 passed / 15 failed (los 15 son fixtures de fecha fija preexistentes, no relacionados a este documento).

Formato por hallazgo (#1-#31, código): módulo, severidad, líneas, descripción, escenario concreto, por qué el test suite no lo agarra. Formato por hallazgo (#32-#55, datos geográficos): spot afectado, qué está mal, estimación de corrección — ver la sección propia para su escala de severidad, distinta a la de código.

Severidad para hallazgos #1-#31 (código): **alta** = puede cambiar una recomendación real hoy con la config de spots actual (59 spots). **media** = afecta casos reales pero de forma más acotada, o es inconsistencia de diseño sin impacto dramático todavía. **baja** = riesgo latente de configuración futura, no disparable con los JSON actuales.

---

## core/scoring/engine.py

Auditado completo (355 líneas) contra `models.py`, `config/scoring_weights.json` y los 59 `config/spots/*.json` reales, y contrastado contra `tests/test_scoring.py`.

### 1. `marea_tipo_efecto` no afecta el score numérico — **alta**

**✅ RESUELTO** — commit `605a9e1` (`fix(#1): marea_tipo_efecto ahora afecta el score numérico de marea`).

- Código: [`_score_marea()`, engine.py:180-207](core/scoring/engine.py#L180). El campo se lee únicamente para elegir texto de flag en [engine.py:271](core/scoring/engine.py#L271), nunca en el cálculo numérico.
- Hay 13 spots reales `low_better` y 46 `mid_better` (ninguno `high_better` todavía).
- Ejemplo (spot con rango `0.3–1.4 m`, `low_better`): nivel `0.1` → score `0.90` con flag "Marea baja (bueno aquí)"; nivel `1.6` (marea alta, debería ser peor para este spot) → score también `0.90`. Dentro del rango, `0.3` y `1.4` reciben ambos `0.80` pese a que uno es la marea "buena" declarada y el otro no.
- El texto le comunica al usuario una preferencia que el número no representa.
- **Cobertura:** no cubierta. `make_spot()` en los tests fija siempre `mid_better`; ningún test compara `low_better` vs `high_better`.

### 2. Salto inverso al cruzar el límite de marea (`marea_min`/`marea_max`) — **alta**

- Código: [engine.py:196-207](core/scoring/engine.py#L196).
- Justo en el límite (`nivel == marea_min` o `== marea_max`) la rama "dentro del rango" da `0.80`. Un valor infinitesimalmente afuera entra en la otra fórmula y da casi `1.00`.
- Ejemplo (spot `0.3–1.4 m`): `nivel=0.300000` → `0.80`; `nivel=0.299999` → `0.9999995`. Mismo patrón en el límite superior.
- Empeorar la marea en una milésima de metro puede subir el componente de marea ~0.20 y cruzar el umbral de ventana `0.60`, cambiando si esa hora califica como "buena ventana" o no.
- **Cobertura:** no cubierta. Los casos "fuera de rango" de los tests usan niveles lejanos (`0.0`, `3.0`, `10.0`), nunca valores adyacentes al límite.

### 3. Límites de período y viento del `SpotConfig` cargados pero nunca usados — **alta**

**✅ RESUELTO** — commit `d8638eb` (`fix(#3): usar periodo_min/viento_max del spot en vez de escalones globales`).

- Campos: `swell_periodo_min`, `viento_max_offshore`, `viento_max_onshore` en [models.py:149-151](core/scoring/models.py#L149). El registry los carga desde JSON, pero `_score_periodo()` y `_score_viento()` en engine.py usan únicamente escalones globales hardcodeados, ignorando estos campos por completo.
- Ejemplo real: un spot tolera onshore hasta 18 km/h según su config, otro (Necochea) tiene máximo onshore configurado en 10 km/h. Un onshore de 12 km/h recibe exactamente `0.30` en ambos — el límite específico del spot no cambia nada.
- **Cobertura:** no cubierta. Los tests verifican la curva global de viento/período, nunca que cambiar estos campos en un `SpotConfig` altere el resultado.

### 4. Ola por encima del máximo configurado sigue subiendo el score — **alta**

**✅ RESUELTO** — commit `ac80e91` (`fix(#4): ola por encima del máximo del spot ya no sigue subiendo el score`).

- `swell_altura_max` solo dispara un flag negativo ("Ola grande para el spot") en [engine.py:251-260](core/scoring/engine.py#L251-260). El score de energía sigue creciendo monótonamente con `altura²` en `_energia_proxy`/`_score_energia` ([engine.py:47-56](core/scoring/engine.py#L47)), sin tope relacionado a `swell_altura_max`.
- Ejemplo (spot con máximo 2 m, período 12s): altura 2m → energía 48, score energía ≈0.82. Altura 4m (el doble del máximo del spot) → energía 192, score energía ≈1.00. El motor agrega el flag de advertencia, pero numéricamente premia la condición que el spot marca como peligrosa/inadecuada.
- **Cobertura:** parcialmente ejercitada pero no detectada — `test_score_rango` prueba alturas hasta 4m pero solo verifica que el total quede en `[0,100]`, no que superar el máximo penalice.

### 5. `direcciones_ideales` de los 59 spots no llegan al motor — **alta**

**✅ RESUELTO** — commit `dbf52f5` (`fix(#5): usar direcciones_ideales del spot en el score de dirección swell`).

- Todos los JSON de spot declaran `direcciones_ideales` (lista), pero `SpotConfig` no tiene ese campo, el registry no lo carga, y el engine solo usa `orientacion_costa_deg ± tolerancia_swell_deg`.
- Ejemplo real: un spot declara `direcciones_ideales = [90, 135, 180]` pero tiene `orientacion_costa_deg=95` y `tolerancia=45`. Para swell de 180° (una de sus direcciones marcadas como ideales): diff=85°, score dirección ≈0.465 — el flag lo clasifica apenas como "aceptable", no como ideal.
- Una dirección explícitamente marcada como ideal en la fuente de verdad de configuración puede recibir menos de la mitad del componente de dirección.
- **Cobertura:** no cubierta. Los tests crean spots manuales sin `direcciones_ideales`.

### 6. Discontinuidad al cruzar `2.5 × tolerancia` en score de dirección — **media**

- Código: [engine.py:107-119](core/scoring/engine.py#L107-119).
- Con tolerancia 45° (rango real de tolerancias: 30°-60°): `diff=112.5°` (exactamente `2.5×tolerancia`) → `0.20`. `diff=112.500001°` → `max(0.05, 0.20-0.003×112.5)` = `0.05`. Un cambio infinitesimal en la dirección del swell hace caer el componente 0.15 de golpe.
- Como todas las tolerancias reales son ≥30°, la tercera rama (`else`) entra directamente en el piso `0.05` para casi cualquier diff que la alcance — el decaimiento gradual pensado en el diseño no se nota en la práctica actual.
- **Cobertura:** no cubierta. Solo se prueban dirección perfecta, un caso oblicuo, y 180° — no fronteras ni continuidad entre ramas.

### 7. Penalización fuera del rango de marea usa metros absolutos, no relativos — **media**

- Código: [engine.py:196-207](core/scoring/engine.py#L196). Dentro del rango, la distancia al centro se normaliza por `amplitud` propia del spot. Afuera, la penalización es `0.50 × desvío_en_metros` sin relación a esa amplitud.
- Ejemplo con desvío de 0.2m: en un spot con rango `0.2–0.8` (amplitud angosta), 0.2m equivale al 67% de su semiamplitud. En un spot con rango `0.5–2.0` (amplitud ancha), 0.2m es solo 27% de su semiamplitud. Ambos reciben exactamente `0.90` — el significado relativo del rango configurado desaparece apenas se cruza el límite.
- **Cobertura:** no cubierta. Los tests usan un único rango de marea (`0.4–1.6`), nunca comparan spots con amplitudes distintas.

### 8. Ráfagas de viento (`rafaga_kmh`) completamente ignoradas — **media**

- `WindData.rafaga_kmh` existe en [models.py:21-26](core/scoring/models.py#L21) pero `_score_viento()` ([engine.py:149-173](core/scoring/engine.py#L149)) solo lee `velocidad_kmh` y dirección.
- Ejemplo: viento sostenido 3 km/h con ráfaga 5 km/h → score `1.00`. Viento sostenido 3 km/h con ráfaga 100 km/h → también `1.00`, aunque ese segundo escenario sería impracticable o peligroso.
- **Cobertura:** no cubierta. El test de viento calmo usa ráfaga 5 km/h y no compara distintas ráfagas con la misma velocidad sostenida.

### 9. Altura ajustada negativa se vuelve energía positiva silenciosamente — **baja (riesgo de configuración futura)**

**✅ RESUELTO** — commit `b478ff9` (`fix(#9): clampear altura ajustada a piso 0.0 (delta_altura negativo)`).

- `swell_ajustado.altura_m = hour.swell.altura_m + spot.delta_altura` ([engine.py:310-316](core/scoring/engine.py#L310)). Si `delta_altura` es negativo y de magnitud mayor a la altura real, la altura ajustada queda negativa; `_energia_proxy` eleva al cuadrado y el signo se pierde.
- Ejemplo: altura real 0.2m, `delta_altura=-1.0` → altura ajustada -0.8m, energía con T=10 → `(-0.8)²×10=6.4`, igual a una ola real de +0.8m.
- Los 59 JSON actuales tienen todos `delta_altura=0` (no se dispara hoy), pero `delta_altura` es ajustable en runtime vía `spot_adjustments` de SQLite (calibración empírica por usuario/admin, ver CLAUDE.md/AGENTS.md) — no hay validación de piso.
- **Cobertura:** no cubierta. No hay tests de `delta_altura` ni de altura ajustada negativa.

### 10. Tolerancia de swell = 0 produce `ZeroDivisionError` — **baja (riesgo de configuración futura)**

**✅ RESUELTO** — commit `26feb0b` (`fix(#10): tolerancia de swell <=0 ya no crashea ni contradice el flag`).

- Código: [engine.py:107-115](core/scoring/engine.py#L107). Con `tolerancia=0` y `diff=0`: `diff/tolerancia` = `0/0` → excepción no capturada (esto sí sería un crash, no un error silencioso, pero queda documentado junto a los demás hallazgos de esta zona del código).
- Ninguno de los 59 JSON actuales usa tolerancia 0 (rango real: 30°-60°), y el registry no valida que el valor sea positivo.
- **Cobertura:** no cubierta. Todos los tests usan tolerancia 45°.

---

## core/analysis/ (tides.py, daylight.py, best_hour.py, weekly.py, hourly_view.py)

Auditado contra `tests/test_tides.py`, `tests/test_daylight.py`, `tests/test_best_hour.py`, `tests/test_hourly_weekly.py`.

### 11. `analizar_semana()` incluye horas ya pasadas del día de hoy — **alta**

**✅ RESUELTO** — commit `f663e8b` (`fix(#11): no recomendar ni promediar horas ya pasadas del día de hoy`).

- Código: filtro de fechas [weekly.py:94-100](core/analysis/weekly.py#L94), análisis diario [weekly.py:133-173](core/analysis/weekly.py#L133). `calcular_mejor_hora()` tampoco filtra contra "ahora" ([best_hour.py:75-83](core/analysis/best_hour.py#L75)).
- Solo se excluyen fechas anteriores a hoy; para el día de hoy se conservan **todas** las horas diurnas, incluidas las que ya pasaron.
- Ejemplo a las 17:00: hora 09:00 (ya pasada) con score 0.90, hora 18:00 (futura) con score 0.60. El análisis semanal marca las 09:00 como "mejor hora de hoy" y la incluye en el promedio del día — una recomendación que ya no se puede usar, y que además sobrevalora el día de hoy frente a mañana en la comparación semanal.
- **Cobertura:** no cubierta. Los fixtures construyen días completos de fecha fija; no hay un caso "hoy" con mezcla de horas pasadas/futuras.

### 12. Ranking con solo horas nocturnas lanza `ValueError` no capturado — **alta**

**✅ RESUELTO** — commit `ddc69e6` (`fix(#12): calcular_ranking_dia() no debe crashear sin horas diurnas`).

- Código: [best_hour.py:166-177](core/analysis/best_hour.py#L166) (`calcular_ranking_dia`), impacta también a [hourly_view.py:63-68](core/analysis/hourly_view.py#L63) (`generar_vista_horaria`, que llama con `incluir_noche=True` por default).
- Con `incluir_noche=True`, si hay horas para la fecha pero **ninguna** tiene `es_dia=True`, `max(x[1].score_total for x in scored_con_flag if x[2])` opera sobre un generador vacío y crashea.
- Ejemplo: forecast corto que solo cubre 22:00, 23:00 y 00:00 locales para la fecha pedida — la vista horaria debería degradarse mostrando filas nocturnas (para eso existe `incluir_noche`), pero en cambio revienta.
- **Cobertura:** no cubierta. Los tests con `incluir_noche=True` siempre construyen las 24 horas del día, garantizando que existan horas diurnas.

### 13. `delta_altura` del spot ajusta tanto el swell como la marea con el mismo valor — **alta**

**✅ RESUELTO** — commit `dd3ea29` (`fix(#13): separar delta_marea de delta_altura, aplicar en scoring completo`). Decisión de producto (separar en dos campos) tomada por Ivan explícitamente, no fue una decisión unilateral de implementación.

- Aplicación en marea: [tides.py:98-104](core/analysis/tides.py#L98). Aplicación en swell: [engine.py:310-313](core/scoring/engine.py#L310-313). `SpotConfig` solo tiene un campo `delta_altura` ([models.py:152](core/scoring/models.py#L152)) para ambos usos.
- Ejemplo: calibrar un spot con `delta_altura=+0.3` porque Open-Meteo subestima la altura de ola desplaza **también** todos los niveles de marea +0.3m — sin evidencia de que ambas correcciones físicas deban coincidir. Puede mover artificialmente eventos de marea dentro o fuera del rango óptimo del spot.
- **Cobertura:** el comportamiento está "cubierto" en el sentido de que `test_delta_altura_se_aplica()` exige explícitamente que el delta desplace la marea — el test consagra el acoplamiento en vez de detectarlo como posible bug. Es una decisión de producto pendiente: ¿el campo es de swell, de marea, o de ambos por diseño?

### 14. `detectar_mareas_del_dia()` pierde extremos exactamente en los bordes del día — **media**

- Recorte previo por fecha: [tides.py:155-169](core/analysis/tides.py#L155). El detector de extremos ignora el primer y último índice de la serie que recibe ([tides.py:188-220](core/analysis/tides.py#L188)).
- Ejemplo: 23:00 del día anterior = 0.9m, 00:00 del día pedido = 1.1m, 01:00 = 0.9m. Las 00:00 es una pleamar local clara, pero al recortarse el array por fecha queda en el índice 0 de la serie filtrada y nunca se evalúa como extremo.
- **Cobertura:** no cubierta. El test diario solo verifica que los eventos devueltos pertenezcan a la fecha correcta, no que se detecten extremos cercanos a medianoche.

### 15. Mesetas de 2+ horas en pleamar/bajamar no generan evento — **media**

- Código: comparaciones estrictas `curr > prev and curr > next` en [tides.py:200-218](core/analysis/tides.py#L200).
- Ejemplo (serie suavizada): `0.50, 0.80, 0.80, 0.50` — ninguno de los dos `0.80` es estrictamente mayor que ambos vecinos, así que la pleamar de 2 horas no se detecta.
- **Cobertura:** no cubierta. Hay tests con serie plana y con senoidales suaves, pero ninguno con una meseta corta en un extremo.

### 16. `tiene_datos=False` confunde 4 estados distintos — **media**

- `calcular_mejor_hora()` absorbe cualquier error de scoring silenciosamente ([best_hour.py:85-95](core/analysis/best_hour.py#L85)); `weekly.py` convierte cualquier resultado `None` en "sin datos" ([weekly.py:142-154](core/analysis/weekly.py#L142)).
- Se pierden la distinción entre: no hay forecast para el día / solo hay forecast nocturno / falló el cálculo solar (excepción polar) / el scoring falló para todas las horas del día.
- Ejemplo: una config de spot rota hace fallar `score_fn` para las 12 horas diurnas del día — el resultado semanal no informa error, marca el día exactamente igual que si no existiera forecast.
- **Cobertura:** no cubierta. Los mocks de test nunca lanzan excepciones; el test de `tiene_datos=False` prueba fechas sin forecast, no datos que fallan al evaluarse.

### 17. `nivel_actual` no respeta el filtro `desde` — **media**

- Código: [tides.py:113-127](core/analysis/tides.py#L113). `niveles[0]` toma el primer elemento de todo `forecast`, no el primero relevante después de aplicar `desde`.
- Ejemplo: forecast 08:00=0.4m, 09:00=0.6m, 10:00=0.8m, con `desde=10:00`. `nivel_actual` queda en 0.4m aunque el único período relevante para el caller empieza en 0.8m. Inconsistente además con `_calcular_tendencia()`, que sí busca el índice más cercano al reloj real.
- **Cobertura:** no cubierta. El test de `desde` solo inspecciona timestamps de eventos, nunca `nivel_actual`.

### 18. `tiene_extremos_claros` mezcla eventos filtrados con amplitud histórica sin filtrar — **media**

- Filtrado de eventos por `desde`: [tides.py:106-111](core/analysis/tides.py#L106). Cálculo de amplitud (para decidir "claros"): [tides.py:118-121](core/analysis/tides.py#L118) — sobre la serie **completa**, sin aplicar `desde`.
- Ejemplo: pasado con niveles entre 0.2m y 1.4m (amplitud 1.2m, sobre el umbral 0.05), futuro desde el corte casi plano (0.50, 0.51, 0.50, con un micro-extremo de 1cm). El evento futuro sobrevive al filtro `desde`, la amplitud histórica infla el resultado, y `tiene_extremos_claros=True` aunque el extremo real disponible no sea claro en absoluto.
- **Cobertura:** no cubierta. Los tests prueban el filtro `desde` y las series planas/senoidales por separado, nunca combinados con amplitudes distintas antes/después del corte.

### 19. Caché de daylight puede devolver datos de otro spot con la misma `key` — **media**

- Código: [daylight.py:97-112](core/analysis/daylight.py#L97). El caché usa solo `(spot.key, fecha)` como clave — no incluye lat/lon/tz — y es un dict mutable con default de argumento (vive toda la vida del proceso).
- Ejemplo: si se recarga/edita un spot conservando la misma `key` pero con coordenadas distintas (ej. corrección de lat/lon en `config/spots/*.json` seguida de un redeploy sin reinicio limpio del proceso, o dos spots distintos compartiendo `key` por error de config), el amanecer/atardecer cacheado con las coordenadas viejas se sigue devolviendo para esa fecha.
- **Cobertura:** parcial. Hay test de reutilización para mismo spot/día y de separación por fecha, pero no de misma `key` con coordenadas o tz distintas.

### 20. `mejor_ventana_semana` desempata usando el score redondeado a entero — **baja**

- Código: [weekly.py:60-67](core/analysis/weekly.py#L60). La selección usa `BestHourResult.score_100` (entero redondeado) en vez de `breakdown.score_total` (float).
- Ejemplo: Lunes `score_total=0.8041` → `score_100=80`; Martes `score_total=0.8049` → `score_100=80`. Empatan en `max()` y gana el primero (Lunes) aunque Martes tenga el score real más alto.
- **Cobertura:** no cubierta. El test también compara solo `score_100`, aceptando el mismo empate artificial que el código produce.

### 21. `es_mejor` se asigna por comparación de timestamp, no de identidad — **baja**

- Código: [hourly_view.py:79-97](core/analysis/hourly_view.py#L79). `r.hour.timestamp == mejor_rank1.hour.timestamp` en vez de comparar por rank o identidad del objeto.
- Si el forecast trajera dos registros con el mismo timestamp (duplicado de datos, ej. por un bug de merge de proveedor), ambos quedarían marcados `es_mejor=True` aunque tuvieran rank/score distintos.
- **Cobertura:** no cubierta — todos los fixtures usan timestamps únicos. (Para empates normales de score con timestamps distintos, el sort estable ya garantiza un único ganador — eso no es un bug.)

---

## core/windows/detector.py

Auditado en dos pasadas contra `tests/test_scoring.py` (los 4 tests que ejercitan `detectar_ventanas`/`calcular_score_actual` — no hay `test_detector.py` dedicado).

### 22. No aplica el límite de 48 horas que promete su propio contrato — **alta**

**✅ RESUELTO** — commit `d283660` (`fix(#22): detector respeta el horizonte de 48h declarado`).

- El docstring y la feature ("Próximas olas 48h") prometen una ventana de 48h ([detector.py:35-53](core/windows/detector.py#L35)), pero no hay ningún filtro `now + 48h` en el código.
- `core/forecast/open_meteo.py` devuelve hasta 168 horas (7 días) ([open_meteo.py:79-84](core/forecast/open_meteo.py#L79), [open_meteo.py:148-152](core/forecast/open_meteo.py#L148)), y los handlers le pasan esa lista completa al detector sin recortar.
- Ejemplo: si mañana hay una ventana con score promedio 0.70 y dentro de 6 días hay una con 0.90, con `top_n=3` la ventana de dentro de 6 días puede aparecer en la lista de "Próximas olas — 48h", contradiciendo el título de la sección.
- **Cobertura:** no cubierta. El fixture de test solo tiene registros dentro de un mismo día, nunca prueba el límite temporal.

### 23. Fallos de scoring se convierten silenciosamente en "no hay ventanas" — **alta si el fallo es sistemático, media si es puntual**

**✅ RESUELTO** — commit `3ffa0c2` (`fix(#23): propagar error cuando el scoring falla para todas las horas`). Solo cubre el caso 100% de fallos, tal como se diseñó; un fallo parcial sigue descartándose en silencio a propósito.

- Código: `except Exception as e: logger.warning(...); continue` en [detector.py:79-85](core/windows/detector.py#L79). La hora se descarta del pool, el error solo queda en logs, no se retorna ningún estado de error.
- Si `calcular_score()` falla para todas las horas (ej. una config de spot rota — como la tolerancia=0 del hallazgo #10 de engine.py), el detector devuelve `[]`, exactamente lo mismo que devolvería un forecast válido sin buenas condiciones. El caller (bot/handlers) no tiene forma de distinguir "no hay olas" de "no se pudo calcular".
- Si falla solo una hora intermedia, además puede disparar el hallazgo #24 (fusión de huecos), porque la hora fallida desaparece de la lista sin dejar rastro de que había un hueco temporal ahí.
- **Cobertura:** no cubierta. Ningún test provoca una excepción dentro de `calcular_score()`.

### 24. Ventana parcialmente pasada conserva promedio y hora pico de horas ya pasadas — **media**

**✅ RESUELTO** — commit `22470d4` (`fix(#24): ventana en curso recalcula stats solo con horas vigentes`).

- Código: filtro final [detector.py:117-119](core/windows/detector.py#L117) solo elimina ventanas con `fin <= now`; una ventana en curso se conserva completa, sin recortar las horas que ya pasaron.
- Ejemplo a las 12:30, ventana 10:00–14:00 con scores 10:00=0.95, 11:00=0.90, 12:00=0.70, 13:00=0.60: se muestra `score_promedio=0.7875`, `score_max=0.95`, `hora_pico=10:00` (ya pasada) y `horas_count=4`, aunque en la práctica solo queda la franja 13:00–14:00 por delante.
- **Cobertura:** no cubierta. El fixture está enteramente en el pasado (todas las ventanas caen fuera por el filtro `fin > now`), nunca hay una ventana que cruce el reloj actual.

### 25. Agrupamiento de ventanas por adyacencia en la lista, sin verificar continuidad horaria real — **media**

- Código: [detector.py:93-108](core/windows/detector.py#L93). El corte de ventana solo chequea cambio de día local (fix histórico ya documentado en CLAUDE.md), no que los timestamps consecutivos estén separados exactamente 1h.
- Ejemplo: si faltan datos de las 09:00 (gap del provider, o descartada por el hallazgo #23), las horas 08:00 y 10:00 —ambas sobre el umbral— quedan adyacentes en la lista `scored` y se fusionan en una sola ventana continua, aunque haya un hueco de una hora sin datos en el medio.
- **Cobertura:** no cubierta. El fixture sí tiene huecos horarios (06:00, 07:00, 08:00, 09:00, 10:00, 14:00, 18:00 UTC), pero las horas después del primer bloque tienen condiciones malas, así que ningún test ejercita dos horas buenas separadas por un hueco.

### 26. Ventanas de una sola hora se generan pese al contrato de "mínimo 2h" — **media**

- El agrupamiento acepta grupos de tamaño 1 ([detector.py:93-108](core/windows/detector.py#L93)) y `_construir_ventana()` no valida tamaño mínimo ([detector.py:126-137](core/windows/detector.py#L126)). El flujo de "condiciones actuales" en `bot/handlers/main.py:410` documenta explícitamente "ventana más cercana (mínimo 2h)" como expectativa.
- Ejemplo: 09:00=0.40, 10:00=0.61, 11:00=0.40 → se genera una ventana 10:00–11:00 con `horas_count=1`.
- **Cobertura:** no cubierta. Los tests solo verifican que exista al menos una ventana, nunca inspeccionan `horas_count`.

### 27. La descripción puede etiquetar como "offshore" un viento que no lo es — **media**

- Código: [detector.py:198-204](core/windows/detector.py#L198). El highlight de viento se infiere solo de `score_viento >= 0.85`, pero en el engine cualquier viento < 5 km/h da `score_viento=1.0` sin importar la dirección (ver `_score_viento`, engine.py:153-154).
- Ejemplo: viento onshore de 3 km/h → `score_viento=1.0` → la ventana se describe como "offshore", una afirmación de dirección meteorológica falsa, no solo una valoración subjetiva optimista.
- **Cobertura:** no cubierta. El test de descripción solo verifica que el texto tenga más de 5 caracteres, no su contenido.

### 28. La descripción de la ventana muestra valores crudos, no los ajustados por `delta_altura`/`factor_periodo` que sí usa el score — **media**

- Altura mostrada: [detector.py:213-215](core/windows/detector.py#L213). Período mostrado: [detector.py:206-211](core/windows/detector.py#L206). El engine sí aplica `factor_periodo`/`delta_altura` para calcular el score ([engine.py:310-316](core/scoring/engine.py#L310)), pero el detector lee los valores crudos de `ForecastHour` para el texto.
- Ejemplo: altura cruda 0.7m con `delta_altura=+0.3` — el score usa 1.0m, pero la descripción muestra "0.7m". Período crudo 13s con `factor_periodo=1.1` — el score usa 14.3s (puede calificar como groundswell), pero la descripción muestra "13s" y no agrega el highlight "groundswell". Hay 4 spots reales con `factor_periodo != 1`, así que esto es disparable hoy.
- **Cobertura:** no cubierta. El spot de test usa ajustes neutros (`delta_altura=0`, `factor_periodo=1`).

### 29. Fail-safe de daylight captura cualquier excepción, no solo la de fenómeno polar — **baja hoy**

- Código: [detector.py:62-72](core/windows/detector.py#L62). `except Exception` (no `except ValueError`) incluye la hora igual, fail-open, ante cualquier error calculando luz solar — no solo ante el caso polar documentado.
- Esto puede esconder errores no relacionados (ej. un bug futuro en `daylight.py` o un `SpotConfig` con lat/lon corruptos) tratándolos silenciosamente como "incluir la hora igual", incluyendo potencialmente horas nocturnas en las recomendaciones.
- **Cobertura:** no cubierta. Ningún test fuerza una excepción en `get_daylight_for_forecast_hour`. Severidad baja hoy porque ninguno de los 59 spots está en latitud polar — pero el `except` amplio esconde más que el caso que dice manejar.

### 30. `umbral`/`top_n` sin validación de rango — **baja**

**✅ RESUELTO** — commit `642677b` (`fix(#30): validar umbral/top_n en vez de dejar comportamientos silenciosos`). Resuelto con validación que lanza `ValueError`, no con clamp — ver discusión con Codex en el historial de la sesión.

- Lectura: [detector.py:55-57](core/windows/detector.py#L55). Uso: [detector.py:93-94](core/windows/detector.py#L93) y [detector.py:121-123](core/windows/detector.py#L121).
- `umbral` negativo → casi todo califica igual. `umbral > 1.0` → nada califica nunca. `top_n=0` → `[]`. `top_n=-1` → slicing `ventanas[:-1]` devuelve todas menos la última (comportamiento no intuitivo si se pasara por error). `top_n` no entero → `TypeError` en el slicing.
- Los valores versionados actuales (`0.60`, `3`) son válidos — el riesgo es de configuración o de llamada incorrecta, no disparable hoy.
- **Cobertura:** no cubierta. Los tests solo prueban umbrales `0.40`/`0.50`, nunca pasan `top_n` explícito.

---

## core/spots/registry.py + persistence/session_store.py

Encontrado por Codex durante la revisión del fix #13 (Grupo 3), no parte de la auditoría original.

### 31. Los ajustes de spot persistidos en SQLite nunca se recargan al iniciar el proceso — **media**

- `persistence/session_store.py::get_all_spot_adjustments()` está definida (lee la tabla `spot_adjustments` completa) pero **no se llama desde ningún lado del código** — confirmado por búsqueda en todo el repo.
- El comando `/ajuste` (`bot/handlers/main.py:handle_ajuste`) sí hace ambas cosas: `actualizar_ajuste()` (aplica el valor en memoria sobre el `SpotConfig` cacheado) y `session_store.set_spot_adjustment()` (lo persiste en SQLite) — pero nada en el arranque del bot vuelve a leer esa tabla para reaplicar los ajustes sobre el registry recién cargado.
- Efecto: un ajuste de `/ajuste` funciona mientras el proceso sigue vivo, pero se pierde en cada restart (Render free tier reinicia con frecuencia — cold starts, deploys, sleep del tier gratuito) aunque el mensaje de confirmación ("✅ Ajuste aplicado... invalidado") no deja ver que la persistencia es parcial.
- **Cobertura:** no cubierta. No hay ningún test de arranque/reload que ejercite `get_all_spot_adjustments()`.

---

## config/spots/peru.json, chile.json, brasil.json — calidad de datos geográficos

Auditoría distinta a las anteriores: no es código, es **calidad de datos** de los 34 spots de estos tres archivos, pedida por Ivan antes de arrancar la Fase 2 (agregar spots nuevos), para no duplicar ni construir sobre datos ya rotos. Mismo método (Codex vía `mcp__codex__codex-reply`, tres tandas por país), formato de severidad distinto (ver abajo) porque la naturaleza del problema es otra.

**⚠️ Advertencia de confiabilidad, antes de actuar sobre esto:** Codex no tuvo acceso a herramienta cartográfica/geocoding en esta sesión — todo lo que sigue sale de su conocimiento geográfico entrenado, no de una verificación en vivo contra un mapa o servicio de geocoding real. Es una buena primera pasada para priorizar qué revisar, **no es una fuente de verdad**. Antes de corregir cualquier coordenada, confirmar contra Google Maps/Google Earth o conocimiento directo del spot (Ivan u otra fuente confiable) — no aplicar las coordenadas "estimadas" de Codex directamente al JSON sin esa verificación.

Severidad (distinta a la de `core/`, adaptada a datos geográficos):
- **alta** = `orientacion_costa_deg` con signos de estar copiado/genérico (afecta directamente `_score_dir_swell`/`_tipo_viento` del motor de scoring — esto SÍ es funcional, no solo cosmético) o spot asignado a la región administrativa incorrecta.
- **media** = lat/lon con desplazamiento notable (cientos de metros a pocos km) respecto al spot nombrado. Impacto funcional probablemente bajo (Open-Meteo consulta un modelo meteorológico de grilla gruesa, no una boya hiperlocal — un desplazamiento de pocos km rara vez cambia el dato de forecast obtenido), pero sí afecta la precisión de sunrise/sunset y la integridad de la fuente de verdad.
- **baja** = inconsistencia cosmética (nombre mal escrito/no oficial) sin impacto funcional en el scoring, solo en UX/búsqueda.

**Cobertura:** ninguno de estos 24 hallazgos tiene test — no existe ningún test que verifique coordenadas contra un valor de referencia real, ni geocoding, ni validación de que `orientacion_costa_deg` no se repita sin justificación entre spots.

### peru.json (12 spots auditados)

### 32. `cerro_azul` en región `ica`, pero administrativamente es Lima/Cañete — **alta**

**✅ RESUELTO** — aplicado en el mismo commit que esta actualización de KNOWN_ISSUES.md ("grupo confiable"). Región renombrada de `"ica"` a `"canete"` (nombre visible "Cañete (Lima)"), revisado por Codex. `ciudad` sin cambios (ya era correcta).

- Cerro Azul es un distrito de la provincia de Cañete, región **Lima**, no Ica.
- lat/lon (`-13.0240, -76.4760`) son plausibles para el spot en sí — el problema es solo la región del árbol de navegación del bot.
- Corrección sugerida por Codex: mover a la región `lima`, o crear una región más precisa (`canete`) dentro de Lima si la organización del bot lo justifica. Ciudad podría ser más específica: "Cerro Azul, Cañete" en vez de solo "Cañete".
- Caso ya detectado por Ivan antes de pedir esta auditoría.

### 33. `punta_rocas` — coordenadas corresponden al cluster de Punta Hermosa, no a Punta Rocas — **media**

**✅ RESUELTO (coordenadas)** — commit `804ef02`. Ivan verificó contra Google Maps: `lat -12.354237, lon -76.811514`. `orientacion_costa_deg` sigue sin tocar (pendiente, ver #40).

- `-12.3010, -76.8060` cae en el entorno de Punta Hermosa/norte de Punta Hermosa, no en Punta Rocas (Punta Negra), que está más al sur — estimación de Codex: `~-12.36, -76.79/-76.80`.
- La ciudad (`Punta Negra`) y la región (`lima`) sí son correctas — solo el punto exacto está mal.
- `orientacion_costa_deg=270` debería recalcularse después de corregir el punto (posiblemente más WSW/SW).

### 34. `el_silencio` — coordenadas corresponden a otro punto del cluster Punta Hermosa — **media**

**✅ RESUELTO (coordenadas)** — commit `804ef02`. Ivan verificó contra Google Maps: `lat -12.31561, lon -76.83615`. `orientacion_costa_deg` sigue sin tocar (pendiente, ver #40).

- `-12.3500, -76.8310` queda al norte/oeste de donde está realmente Playa El Silencio — estimación de Codex: `~-12.37, -76.80`.
- Región y referencia a Punta Hermosa correctas.
- `orientacion_costa_deg=270` no debería conservarse sin recalcular tras corregir el punto.

### 35. `miraflores_waikiki` — longitud parece tierra adentro, no sobre la Costa Verde — **media**

- `lon=-77.0306` ubicaría el punto tierra adentro en Miraflores, no sobre la playa. Estimación de Codex: `~-12.13, -77.038/-77.041`.
- `orientacion_costa_deg=270` es poco plausible para ese tramo inclinado de la Costa Verde — Codex estima la normal real más cerca de WSW/SW, rango `230–250°`. Si esto se confirma, es severidad **alta** (afecta scoring), no solo la coordenada.

### 36. `pico_alto` — coordenadas demasiado cerca de la costa para un reef break offshore — **media**

**🔍 COORDENADAS: VERIFICADO — EVIDENCIA INSUFICIENTE.** Ivan investigó en Google Maps pero la búsqueda encontró un hotel llamado "Pico Alto", no el break real — lat/lon siguen siendo las originales de abajo, sin cambios.
**✅ ORIENTACIÓN: RESUELTA** — `orientacion_costa_deg` 270°→225°, aplicado junto con el resto del cluster Punta Hermosa (ver #40), en el mismo commit que esta actualización de KNOWN_ISSUES.md.

- Pico Alto es una rompiente mar adentro; `-12.3380, -76.8250` parece estar dentro del conjunto urbano/playas de Punta Hermosa, no sobre el pico real. Estimación de Codex: `~-12.33/-12.34, -76.835/-76.84`.
- `orientacion_costa_deg=270` es dudoso para un reef offshore — su exposición real depende de la geometría del arrecife, no de una costa genérica.

### 37-39. `chicama`, `huanchaco`, `mancora` — `orientacion_costa_deg` sospechoso de ser valor genérico, no calculado por spot — **alta (si se confirma)**

**✅ RESUELTO** — aplicado en el mismo commit que esta actualización de KNOWN_ISSUES.md ("grupo confiable"), revisado por Codex. `chicama` 280°→235°, `huanchaco` 280°→250°, `mancora` 280°→305°. **Advertencia:** son estimaciones de Codex (punto medio de los rangos sugeridos), sin verificación cartográfica de Ivan — a diferencia de las coordenadas de otros hallazgos, esto no fue chequeado en Google Maps. Confirmado que `_score_dir_swell()` no cambia (domina `direcciones_ideales`, ya poblada en los 59 spots); el efecto real es en `_tipo_viento()`/`score_viento`.

- Los tres comparten un patrón: valores en el rango 280° que no calzan claramente con la exposición real de cada costa según Codex:
  - `chicama` (280°): costa de Puerto Chicama/Malabrigo con exposición predominantemente SW — Codex sugiere rango `220–250°`.
  - `huanchaco` (280°): normal marina local más W/SW — Codex sugiere rango `240–260°`.
  - `mancora` (280°): costa con componente NW clara — Codex sugiere rango `295–315°`.
- Como `orientacion_costa_deg` alimenta directamente `_score_dir_swell()`/`_tipo_viento()` del motor, un valor mal calibrado cambia recomendaciones reales — por eso alta severidad si se confirma, no solo estética.

### 40. Grupo Punta Hermosa (`pico_alto`, `caballeros`, `senoritas`, `la_isla`, `el_silencio`) — orientación repetida ~260-270° sin diferenciar geometría real de cada playa/reef — **media**

**✅ RESUELTO** — aplicado en el mismo commit que esta actualización de KNOWN_ISSUES.md ("grupo confiable"), revisado por Codex. Valores diferenciados por spot: `pico_alto` 270°→225°, `caballeros` 270°→235°, `senoritas` 265°→240°, `la_isla` 260°→255°, `el_silencio` 270°→230°. Misma advertencia que #37-39: estimaciones de Codex sin verificación cartográfica — las coordenadas lat/lon de `pico_alto` y `el_silencio` tienen su propio estado por separado (ver #33/#34/#36).

- 5 spots muy cercanos entre sí comparten valores de `orientacion_costa_deg` casi idénticos pese a ser bahías, puntas y reefs con geometría distinta según Codex.
- No implica que todos estén mal — implica que conviene recalcular cada uno individualmente en vez de asumir que comparten orientación por estar geográficamente cerca.

### chile.json (7 spots auditados)

### 41. `las_machas` — coordenadas ubicadas al sur de donde está la playa real — **media**

**✅ RESUELTO** — commit `804ef02`. Ivan verificó contra Google Maps: `lat -18.4456, lon -70.3039`.

- `-18.4870, -70.3410` cae junto al conjunto El Laucho/La Lisera/Isla Alacrán, no en Las Machas (que está al norte de Arica, continuando desde Chinchorro). Estimación de Codex: `~-18.42/-18.44, -70.32/-70.33`.
- Región (Arica y Parinacota) correcta.

### 42. `renaca` — longitud parece varios cientos de metros tierra adentro — **media**

- `lon=-71.5385` — Codex estima la línea de playa real más cerca de `-71.545/-71.547`.
- Región y nombre correctos.

### 43. `punta_de_lobos` — longitud parece mar adentro — **media**

**✅ RESUELTO** — aplicado junto con esta misma actualización de KNOWN_ISSUES.md (un solo commit, ver `git log -- config/spots/chile.json`). Ivan verificó contra Google Maps: `lat -34.425496, lon -72.042924`.

- `lon=-72.0833` ubicaría el punto varios km mar adentro; Codex estima la rompiente/promontorio real en `~-34.43, -72.04/-72.05`.
- Latitud, región y ciudad correctas — Codex no marca `orientacion_costa_deg=260` como sospechoso acá.

### 44. `la_boca_matanzas` — coordenadas/nombre corresponden a Matanzas en general, no específicamente a "La Boca" — **baja/media**

**✅ RESUELTO (coordenadas + ciudad)** — commit `804ef02`. Ivan verificó contra Google Maps y confirmó explícitamente que el spot representa "La Boca", Navidad, O'Higgins específicamente (no una fusión con Matanzas): `lat -33.924223, lon -71.849743`, `ciudad` corregida de "Matanzas" a "Navidad". Codex señaló que el `nombre` visible "La Boca (Matanzas)" queda ahora semánticamente confuso con `ciudad=Navidad` — sugerencia pendiente de decisión: renombrar a "La Boca (Navidad)" o solo "La Boca".

- `-33.9610, -71.8710` corresponde mejor a Matanzas propiamente que al sector "La Boca" (junto a la desembocadura), que está más al norte — estimación de Codex: `~-33.95, -71.86/-71.87`.
- Alternativa planteada por Codex: si se prefiere conservar la coordenada actual, el nombre debería simplificarse a "Matanzas" en vez de "La Boca (Matanzas)".

### 45. `pichilemu_infiernillo` — sospechoso, longitud posiblemente corrida al este; nombre ambiguo — **baja (sin confirmar)**

**✅ VERIFICADO — SIN CAMBIOS NECESARIOS.** Ivan lo chequeó contra Google Maps: la diferencia es menor a 1km, y confirmó que "Infiernillo" es un lugar real y distinto de Punta de Lobos, no un nombre fusionado por error. No se toca.

- `-34.3870, -72.0090` podría estar algo al este de la rompiente real de Infiernillo — Codex estima `~-72.013/-72.016`, pero lo marca como "requiere mapa", no alta confianza.
- Nombre compuesto "Pichilemu / Infiernillo" es ambiguo — si la coordenada representa la rompiente de Infiernillo específicamente, convendría llamarlo solo "Infiernillo".

### brasil.json (15 spots auditados)

### 46. `barra_ibiraquera` — coordenadas demasiado al norte del spot real — **media**

**✅ RESUELTO** — aplicado en el mismo commit que esta actualización de KNOWN_ISSUES.md. Ivan verificó contra Google Maps: `lat -28.152467, lon -48.649614`.

- `-28.1000, -48.6400` cae en el entorno Ouvidor/Rosa norte, no en la barra de la Lagoa de Ibiraquera (que está al sur de Praia do Rosa). Estimación de Codex: `~-28.15/-28.16, -48.64/-48.66`.

### 47. `silveira` — latitud no respeta el orden geográfico real del cluster Garopaba/Imbituba — **media**

**✅ RESUELTO** — commit `804ef02`. Ivan verificó contra Google Maps: `lat -28.036951, lon -48.607447`.

- `-28.0950, -48.6350` cae en el entorno Ouvidor/Rosa norte, no en Praia da Silveira (inmediatamente al sur de Garopaba Central, al norte de Ferrugem). Estimación de Codex: `~-28.04/-28.05, -48.61/-48.63`.
- Codex lo marca como "el error más claro de posible copia o intercambio" dentro del cluster — la secuencia norte-sur real es Garopaba → Silveira → Ferrugem → Ouvidor → Rosa → Ibiraquera, y la coordenada actual de `silveira` no respeta esa secuencia.

### 48. `floripa_joaquina` — coordenadas parecen caer tierra adentro (dunas/interior de la isla) — **media**

**✅ RESUELTO** — aplicado en el mismo commit que esta actualización de KNOWN_ISSUES.md. Ivan verificó contra Google Maps: `lat -27.6295, lon -48.4490`.

- `-27.6428, -48.4736` no corresponde a la línea de rompiente de Joaquina, que está más al norte y al este. Estimación de Codex: `~-27.62/-27.64, -48.44/-48.46`.

### 49. `floripa_praia_mole` — longitud parece ~1.5-2km tierra adentro — **media**

**✅ RESUELTO** — aplicado en el mismo commit que esta actualización de KNOWN_ISSUES.md. Ivan verificó contra Google Maps: `lat -27.6029, lon -48.4330`.

- `lon=-48.4530` — Codex estima la playa real más cerca de `-48.43/-48.44`.
- Latitud correcta.

### 50. `itamambuca` — coordenadas claramente incorrectas (mar adentro y al norte de Ubatuba) — **media**

**✅ RESUELTO** — aplicado en el mismo commit que esta actualización de KNOWN_ISSUES.md ("grupo confiable"), revisado por Codex. `lat -23.4030, lon -45.0060`. `orientacion_costa_deg=160` sin cambios (Codex confirmó que sigue siendo razonable con el punto corregido). **Advertencia:** estimación de Codex sin verificación cartográfica de Ivan.

- `-23.3340, -44.8820` queda muy al este de la costa de Ubatuba (probablemente mar adentro) y demasiado al norte — el más marcado de los desplazamientos encontrados en Brasil. Estimación de Codex: `~-23.39/-23.41, -45.00/-45.02`.
- Región y ciudad correctas.

### 51. `maresia` — nombre no coincide con el nombre oficial del destino, además de coordenada corrida — **baja (nombre) / media (coordenada)**

**✅ RESUELTO** — aplicado en el mismo commit que esta actualización de KNOWN_ISSUES.md ("grupo confiable"), revisado por Codex. `nombre` "Maresia"→"Maresias", `lat -23.7915, lon -45.5590`. `key` deliberadamente **sin cambiar** (sigue `"maresia"`) — riesgo de romper favoritos/ajustes persistidos en SQLite si algún usuario real ya la favoriteó, señalado por Codex; decisión de Ivan fue no arriesgarlo. **Advertencia:** coordenada es estimación de Codex sin verificación cartográfica de Ivan.

- El nombre reconocido del destino es **"Maresias"** (con "s" final), no "Maresia" — afecta tanto `key` como `nombre` en el JSON.
- Latitud `-23.776` parece algo al norte de la playa principal — Codex estima `~-23.79, -45.56`. Longitud plausible.

### 52. `praia_madeiro` — longitud parece mar adentro — **media**

- `lon=-35.0430` — Codex estima la playa real más cerca de `-35.055/-35.065`.
- Región, ciudad y nombre correctos. `orientacion_costa_deg=15` debería recalcularse tras corregir el punto (Codex nota que la playa curva y su exposición no necesariamente coincide con la de Pipa central).

### 53. `praia_do_rosa` — sospechoso, longitud posiblemente corrida al oeste — **baja (sin confirmar)**

**✅ RESUELTO** — aplicado en el mismo commit que esta actualización de KNOWN_ISSUES.md. Ivan verificó contra Google Maps: `lat -28.1301, lon -48.6421`.

- `lon=-48.6600` podría caer algo al oeste de la playa real (área urbana/lagunar) — Codex estima `~-48.64/-48.65`, pero lo marca "requiere mapa".
- Codex confirma explícitamente que la secuencia norte-sur respecto a Ferrugem es correcta (no están intercambiadas entre sí).

### 54. `floripa_campeche` — coordenadas podrían representar el barrio/acceso, no la línea de rompiente — **baja (sin confirmar)**

**🔍 VERIFICADO — EVIDENCIA INSUFICIENTE PARA CORREGIR.** Ivan investigó en Google Maps pero la búsqueda encontró el centro del barrio de Campeche, no la playa/rompiente específica — no hay coordenada confiable todavía. Sigue con las coordenadas originales de abajo.

- `-27.6875, -48.4900` está en el sector correcto según Codex, pero Campeche es una playa extensa — podría convenir elegir un pico concreto más al este (`~-48.47/-48.48`) en vez del punto de acceso general.

### 55. `garopaba_central` — `orientacion_costa_deg=110` dudoso para la geometría real de la bahía — **media (sin confirmar)**

- Coordenadas plausibles, pero Codex señala que la bahía de Garopaba Central es más protegida y curvada que Ferrugem/Rosa/Silveira, por lo que probablemente no comparte la orientación ESE genérica (`110°`) de esos spots — estima que el sector central podría mirar más hacia E/NE (`60–100°`).

**Spots auditados sin hallazgos** (Codex no encontró problemas suficientemente sólidos): `caballeros`, `senoritas`, `la_isla`, `lobitos` (Perú); `el_gringo`, `ritoque` (Chile); `ferrugem`, `imbituba_porto`, `floripa_barra`, `pipa`, `baia_formosa` (Brasil). **No se encontraron nombres duplicados ni dos keys representando claramente el mismo spot** en ninguno de los tres archivos.

---

## Priorización original (según Codex, histórica — ver estado real arriba en cada hallazgo)

Esta lista refleja la priorización sugerida en la auditoría original, antes de los 3 grupos de fixes. Se conserva como registro histórico; el estado vigente de cada ítem está marcado en su sección correspondiente más arriba, no acá.

**Podían estar alterando recomendaciones reales en ese momento**, con la config de 59 spots en producción:

1. ✅ Detector no respeta el horizonte de 48h (#22) — resuelto, `d283660`.
2. 🔲 Salto inverso en los límites de marea del engine — un cambio de milésimas de metro puede cruzar el umbral de ventana óptima (#2) — **sigue abierto**.
3. ✅ `marea_tipo_efecto` ignorado en 13 spots reales `low_better` (#1) — resuelto, `605a9e1`.
4. ✅ `analizar_semana()` mezcla horas ya pasadas de hoy (#11) — resuelto, `f663e8b`.
5. ✅ Límites específicos de período/viento del spot ignorados (#3) — resuelto, `d8638eb`.
6. ✅ Altura máxima configurada que no penaliza (#4) — resuelto, `ac80e91`.
7. ✅ `direcciones_ideales` de los 59 spots descartadas por el motor (#5) — resuelto, `dbf52f5`.
8. ✅ Fallos de scoring sistemáticos indistinguibles de "no hay buenas condiciones" (#23) — resuelto, `3ffa0c2`.
9. ✅ Ventana parcialmente pasada conserva score/hora pico viejo (#24) — resuelto, `22470d4`.
10. ✅ `delta_altura` acoplaba swell y marea (#13) — resuelto, `dd3ea29`.

**Riesgos de configuración futura** (no disparables con los datos/config versionados en ese momento):
- ✅ Tolerancia swell = 0 (#10) — resuelto, `26feb0b`.
- ✅ Altura ajustada negativa (#9) — resuelto, `b478ff9`.
- ✅ `umbral`/`top_n` fuera de rango (#30) — resuelto, `642677b`.

**Crash confirmado (no solo silencioso)**: ranking con solo horas nocturnas + `incluir_noche=True` (#12) — ✅ resuelto, `ddc69e6`.

**Sin priorizar en la ronda original, todavía abiertos**: #6, #7, #8, #14, #15, #16, #17, #18, #19, #20, #21, #25, #26, #27, #28, #29, y #31 (hallado después, durante la revisión del fix #13).
