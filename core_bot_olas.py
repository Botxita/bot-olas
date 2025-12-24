from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Dict, Any, Tuple, List, Optional

from spots_config import SPOTS
from ajustes_spots import cargar_ajustes_para, aplicar_ajustes
from pronostico_api import (
    obtener_pronostico_formateado,
    _obtener_datos_olas_reales,
    _calcular_scores,
    _score_a_estrellas,
    _texto_calidad,
    _agrupar_mejores_ventanas,
    _descripcion_resumen,
    PuntoOla,
    _filtrar_horas_luz,
)


Estado = Dict[str, Any]

def crear_estado_inicial() -> Estado:
    return {
        "fase": "eligiendo_spot",  # eligiendo_spot | eligiendo_playa | menu_consulta | esperando_dia
        "spot_key": None,
        "playa_key": None,
    }


def _normalizar_texto(texto: str) -> str:
    return texto.strip().lower()


def _listar_spots() -> str:
    lineas = ["🌊 Hola, soy el Bot de Olas.", "Elegí un spot:", ""]
    for idx, (spot_key, spot) in enumerate(SPOTS.items(), start=1):
        # Solo número + nombre visible
        lineas.append(f"{idx}) {spot['nombre']}")
    lineas.append("")
    lineas.append("Podés responder con el número o con el nombre.")
    return "\n".join(lineas)



def _listar_playas(spot_key: str) -> str:
    spot = SPOTS[spot_key]
    playas = spot["playas"]
    lineas = [
        f"Elegiste {spot['nombre']}.",
        "Ahora elegí la playa:",
        "",
    ]
    for idx, (playa_key, playa) in enumerate(playas.items(), start=1):
        # Solo número + nombre visible
        lineas.append(f"{idx}) {playa['nombre']}")
    lineas.append("")
    lineas.append("Escribí 'v' para volver al listado de spots.")
    return "\n".join(lineas)


def _texto_menu_consultas(spot_key: str, playa_key: str) -> str:
    spot = SPOTS[spot_key]
    playa = spot["playas"][playa_key]
    lineas = [
        f"Elegiste {playa['nombre']} ({spot['nombre']}).",
        "",
        "¿Qué querés consultar?",
        "",
        "1) Pronóstico de HOY",
        "2) Pronóstico de MAÑANA",
        "3) Pronóstico para OTRA FECHA",
        "4) MEJOR HORARIO de hoy",
        "5) MEJOR DÍA/HORARIO próximos 7 días",
        "",
        "Podés responder con el número o con el texto (ej: 'hoy', 'mañana', 'mejor horario').",
        "",
        "Escribí 'v' para volver y elegir otra playa o spot.",
    ]
    return "\n".join(lineas)


def _parsear_fecha_desde_usuario(texto: str) -> Optional[date]:
    texto = texto.strip()
    formatos = ["%d/%m/%Y", "%d-%m-%Y", "%d/%m", "%d-%m"]
    hoy = date.today()

    for fmt in formatos:
        try:
            dt = datetime.strptime(texto, fmt).date()
            if "%Y" not in fmt:
                dt = dt.replace(year=hoy.year)
            return dt
        except ValueError:
            continue
    return None


def _formatear_fecha_larga(d: date) -> str:
    meses = [
        "enero", "febrero", "marzo", "abril", "mayo", "junio",
        "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
    ]
    return f"{d.day} de {meses[d.month - 1]} de {d.year}"


def _mejor_horario_en_dia(spot_key: str, playa_key: str, fecha: date) -> str:
    spot = SPOTS[spot_key]
    playa = spot["playas"][playa_key]
    lat = playa["lat"]
    lon = playa["lon"]

    try:
        puntos: List[PuntoOla] = _obtener_datos_olas_reales(lat, lon, fecha)
    except Exception as e:
        return (
            f"No pude obtener datos para {playa['nombre']} ({spot['nombre']}) el {fecha.strftime('%d/%m/%Y')}.\n"
            f"Detalle técnico: {e}\n\nEscribí 'v' para volver al menú anterior."
        )

    if not puntos:
        return (
            f"No hay datos de olas para {playa['nombre']} el {fecha.strftime('%d/%m/%Y')}."
            "\n\nEscribí 'v' para volver al menú anterior."
        )

    puntos, amanecer, atardecer = _filtrar_horas_luz(puntos, lat, lon, fecha)

    ajustes = cargar_ajustes_para(spot_key, playa_key)
    for p in puntos:
        p.altura_m, p.periodo_s = aplicar_ajustes(p.altura_m, p.periodo_s, ajustes)

    _calcular_scores(puntos)

    alturas = [p.altura_m for p in puntos if p.altura_m > 0]
    periodos = [p.periodo_s for p in puntos if p.periodo_s > 0]
    scores = [p.score for p in puntos]

    h_min = min(alturas) if alturas else 0.0
    h_max = max(alturas) if alturas else 0.0
    T_min = min(periodos) if periodos else 0.0
    T_max = max(periodos) if periodos else 0.0
    score_prom = sum(scores) / len(scores) if scores else 0.0

    ventanas = _agrupar_mejores_ventanas(puntos)
    etiqueta = _texto_calidad(score_prom)

    txt_fecha = fecha.strftime("%d/%m/%Y")
    lineas: List[str] = []

    lineas.append(f"🔎 Mejor horario para {playa['nombre']} ({spot['nombre']})")
    lineas.append(f"📅 Día: {txt_fecha} ({_formatear_fecha_larga(fecha)})")
    if amanecer and atardecer:
        lineas.append(
            f"☀️ Horas consideradas: {amanecer.strftime('%H:%M')} – {atardecer.strftime('%H:%M')} hs"
        )
    lineas.append("")
    lineas.append(
        f"- Calidad global del día: {etiqueta} · {_score_a_estrellas(score_prom)} "
        f"(score medio ~ {score_prom:.0f}/100)"
    )
    lineas.append(
        f"- Rango de olas del día: {h_min:.1f} – {h_max:.1f} m · período {T_min:.0f} – {T_max:.0f} s"
    )

    if ventanas:
        inicio, fin, prom = ventanas[0]
        h_ini = inicio.strftime("%H:%M")
        h_fin = fin.strftime("%H:%M")

        puntos_ventana = [p for p in puntos if inicio <= p.dt <= fin]
        if puntos_ventana:
            h_min_v = min(p.altura_m for p in puntos_ventana)
            h_max_v = max(p.altura_m for p in puntos_ventana)
            T_min_v = min(p.periodo_s for p in puntos_ventana)
            T_max_v = max(p.periodo_s for p in puntos_ventana)
        else:
            h_min_v = h_min
            h_max_v = h_max
            T_min_v = T_min
            T_max_v = T_max

        lineas.append("")
        lineas.append(f"🕒 Ventana recomendada: {h_ini} – {h_fin} hs")
        lineas.append(
            f"- Olas en la ventana: {h_min_v:.1f} – {h_max_v:.1f} m · período {T_min_v:.0f} – {T_max_v:.0f} s"
        )
        lineas.append(
            f"- Calidad estimada de la ventana: {_texto_calidad(prom)} · {_score_a_estrellas(prom)} "
            f"(score ~ {prom:.0f}/100)"
        )
    else:
        mejor = max(puntos, key=lambda p: p.score)
        hora = mejor.dt.strftime("%H:%M")

        lineas.append("")
        lineas.append("No hay un bloque largo que supere el umbral de calidad, pero lo mejor del día sería:")
        lineas.append(f"- Hora: {hora} hs")
        lineas.append(
            f"- Olas: {mejor.altura_m:.1f} m · {mejor.periodo_s:.0f} s · "
            f"{f'viento {mejor.viento_vel_kmh:.0f} km/h' if mejor.viento_vel_kmh is not None else 'viento N/D'}"
        )
        lineas.append(
            f"- Calidad estimada en ese momento: {_score_a_estrellas(mejor.score)} "
            f"(score ~ {mejor.score:.0f}/100)"
        )

    lineas.append("")
    lineas.append("Escribí 'v' para volver al menú anterior.")
    return "\n".join(lineas)


def _mejor_dia_semana(spot_key: str, playa_key: str, dias: int = 7) -> str:
    spot = SPOTS[spot_key]
    playa = spot["playas"][playa_key]

    hoy = date.today()
    registros: List[Dict[str, Any]] = []

    for offset in range(dias):
        f = hoy + timedelta(days=offset)
        lat = playa["lat"]
        lon = playa["lon"]
        try:
            puntos: List[PuntoOla] = _obtener_datos_olas_reales(lat, lon, f)
        except Exception:
            continue
        if not puntos:
            continue

        puntos, amanecer, atardecer = _filtrar_horas_luz(puntos, lat, lon, f)

        ajustes = cargar_ajustes_para(spot_key, playa_key)
        for p in puntos:
            p.altura_m, p.periodo_s = aplicar_ajustes(p.altura_m, p.periodo_s, ajustes)

        _calcular_scores(puntos)

        scores = [p.score for p in puntos]
        if not scores:
            continue

        score_prom = sum(scores) / len(scores)
        score_max = max(scores)

        ventanas = _agrupar_mejores_ventanas(puntos)
        registros.append(
            {
                "fecha": f,
                "puntos": puntos,
                "score_prom": score_prom,
                "score_max": score_max,
                "ventanas": ventanas,
            }
        )

    if not registros:
        return (
            f"No pude estimar el mejor día de los próximos {dias} días para "
            f"{playa['nombre']} ({spot['nombre']}).\n\nEscribí 'v' para volver al menú anterior."
        )

    mejor_dia = max(registros, key=lambda r: r["score_max"])
    f = mejor_dia["fecha"]
    puntos = mejor_dia["puntos"]
    ventanas = mejor_dia["ventanas"]
    score_prom = mejor_dia["score_prom"]
    score_max = mejor_dia["score_max"]

    alturas = [p.altura_m for p in puntos if p.altura_m > 0]
    periodos = [p.periodo_s for p in puntos if p.periodo_s > 0]
    h_min = min(alturas) if alturas else 0.0
    h_max = max(alturas) if alturas else 0.0
    T_min = min(periodos) if periodos else 0.0
    T_max = max(periodos) if periodos else 0.0

    etiqueta = _texto_calidad(score_prom)

    dias_semana = [
        "Lunes", "Martes", "Miércoles", "Jueves",
        "Viernes", "Sábado", "Domingo",
    ]
    nombre_dia = dias_semana[f.weekday()]

    lineas: List[str] = []
    lineas.append(
        f"📆 Mejor día/horario en los próximos {dias} días para {playa['nombre']} ({spot['nombre']}):"
    )
    lineas.append("")
    lineas.append(
        f"- Día sugerido: {nombre_dia} {f.strftime('%d/%m/%Y')} "
        f"(calidad global {etiqueta} · {_score_a_estrellas(score_prom)}; "
        f"pico ~ {_score_a_estrellas(score_max)} / score máx ~ {score_max:.0f}/100)"
    )
    lineas.append(
        f"- Rango de olas ese día: {h_min:.1f} – {h_max:.1f} m · período {T_min:.0f} – {T_max:.0f} s"
    )

    if ventanas:
        inicio, fin, prom_v = ventanas[0]
        h_ini = inicio.strftime("%H:%M")
        h_fin = fin.strftime("%H:%M")
        lineas.append(
            f"- Mejor ventana estimada: {h_ini} – {h_fin} hs · "
            f"{_texto_calidad(prom_v)} · {_score_a_estrellas(prom_v)} (score ~ {prom_v:.0f}/100)"
        )
    else:
        mejor = max(puntos, key=lambda p: p.score)
        lineas.append(
            f"- Horario más prometedor: {mejor.dt.strftime('%H:%M')} hs · "
            f"{mejor.altura_m:.1f} m · {mejor.periodo_s:.0f} s · "
            f"{f'viento {mejor.viento_vel_kmh:.0f} km/h' if mejor.viento_vel_kmh is not None else 'viento N/D'}"
        )

    lineas.append("")
    lineas.append("Resumen de la tendencia próxima:")
    registros_ordenados = sorted(registros, key=lambda r: r["fecha"])
    for r in registros_ordenados:
        f2 = r["fecha"]
        etiqueta2 = _texto_calidad(r["score_prom"])
        lineas.append(
            f"- {f2.strftime('%d/%m')} · {etiqueta2} · "
            f"{_score_a_estrellas(r['score_prom'])} (score medio ~ {r['score_prom']:.0f}/100)"
        )

    lineas.append("")
    lineas.append("Escribí 'v' para volver al menú anterior.")
    return "\n".join(lineas)


def responder_mensaje(texto: str, estado: Optional[Estado]) -> Tuple[str, Estado]:
    if estado is None:
        estado = crear_estado_inicial()

    t_norm = _normalizar_texto(texto)
    fase = estado.get("fase", "eligiendo_spot")

        # Comando global para volver un nivel atrás
    if t_norm in {"v", "volver", "atrás", "atras"}:
        # Desde el menú de consultas: volver a elegir playa (si hay varias) o a spots
        if fase == "menu_consulta":
            spot_key = estado.get("spot_key")
            if not spot_key or spot_key not in SPOTS:
                estado = crear_estado_inicial()
                return _listar_spots(), estado

            spot = SPOTS[spot_key]
            playas = spot["playas"]

            # Si hay más de una playa, volvemos a la selección de playa
            if len(playas) > 1:
                estado["fase"] = "eligiendo_playa"
                estado["playa_key"] = None
                return _listar_playas(spot_key), estado

            # Si solo hay una playa, volvemos al listado de spots
            estado = crear_estado_inicial()
            return _listar_spots(), estado

        # Desde "esperando_dia": volver al menú de consultas
        if fase == "esperando_dia":
            estado["fase"] = "menu_consulta"
            spot_key = estado.get("spot_key")
            playa_key = estado.get("playa_key")
            if spot_key and playa_key:
                return _texto_menu_consultas(spot_key, playa_key), estado
            else:
                estado = crear_estado_inicial()
                return _listar_spots(), estado

        # Desde elección de playa: volver al listado de spots
        if fase == "eligiendo_playa":
            estado = crear_estado_inicial()
            return _listar_spots(), estado

        # Desde listado de spots: simplemente mostrarlo de nuevo
        if fase == "eligiendo_spot":
            return _listar_spots(), estado


    if t_norm in {"/start", "start"}:
        estado = crear_estado_inicial()
        return _listar_spots(), estado

    if t_norm in {"hola", "buen dia", "buen día"} and fase == "eligiendo_spot":
        return _listar_spots(), estado

    if t_norm in {"cambiar spot", "/spot"}:
        estado = crear_estado_inicial()
        return _listar_spots(), estado

    # Fase: elegir spot
    if fase == "eligiendo_spot":
        keys = list(SPOTS.keys())
        spot_key = None

        if t_norm.isdigit():
            idx = int(t_norm) - 1
            if 0 <= idx < len(keys):
                spot_key = keys[idx]
        else:
            for k, spot in SPOTS.items():
                nombre = spot["nombre"].lower()
                if t_norm == k.lower() or t_norm in nombre:
                    spot_key = k
                    break

        if spot_key is None:
            return _listar_spots(), estado

        estado["spot_key"] = spot_key
        spot = SPOTS[spot_key]
        playas = spot["playas"]

        if len(playas) == 1:
            (playa_key, playa) = next(iter(playas.items()))
            estado["playa_key"] = playa_key
            estado["fase"] = "menu_consulta"
            return _texto_menu_consultas(spot_key, playa_key), estado
        else:
            estado["fase"] = "eligiendo_playa"
            return _listar_playas(spot_key), estado

    # Fase: elegir playa
    if fase == "eligiendo_playa":
        spot_key = estado.get("spot_key")
        if not spot_key or spot_key not in SPOTS:
            estado = crear_estado_inicial()
            return _listar_spots(), estado

        spot = SPOTS[spot_key]
        playas = spot["playas"]
        keys_playa = list(playas.keys())

        playa_key = None
        if t_norm.isdigit():
            idx = int(t_norm) - 1
            if 0 <= idx < len(keys_playa):
                playa_key = keys_playa[idx]
        else:
            for k, playa in playas.items():
                nombre = playa["nombre"].lower()
                if t_norm == k.lower() or t_norm in nombre:
                    playa_key = k
                    break

        if playa_key is None:
            return _listar_playas(spot_key), estado

        estado["playa_key"] = playa_key
        estado["fase"] = "menu_consulta"
        return _texto_menu_consultas(spot_key, playa_key), estado

    # Fase: menú de consultas
    if fase == "menu_consulta":
        spot_key = estado.get("spot_key")
        playa_key = estado.get("playa_key")

        if not spot_key or not playa_key:
            estado = crear_estado_inicial()
            return _listar_spots(), estado

        if t_norm in {"1", "hoy"}:
            fecha = date.today()
            texto_resp = obtener_pronostico_formateado(spot_key, playa_key, fecha)
            return texto_resp, estado

        if t_norm in {"2", "mañana", "maniana"}:
            fecha = date.today() + timedelta(days=1)
            texto_resp = obtener_pronostico_formateado(spot_key, playa_key, fecha)
            return texto_resp, estado

        if t_norm in {"3", "otro dia", "otro día"}:
            estado["fase"] = "esperando_dia"
            return (
                "Decime la fecha que querés consultar en formato dd/mm o dd/mm/aaaa "
                "(por ejemplo 12/01 o 12/01/2026).\n"
                "Escribí 'v' para volver al menú anterior.",
                estado,
            )

        if t_norm in {"4", "mejor horario", "mejor horario hoy"}:
            fecha = date.today()
            texto_resp = _mejor_horario_en_dia(spot_key, playa_key, fecha)
            return texto_resp, estado

        if t_norm in {
            "5",
            "mejor dia",
            "mejor día",
            "mejor dia semana",
            "mejor día semana",
        }:
            texto_resp = _mejor_dia_semana(spot_key, playa_key, dias=7)
            return texto_resp, estado

        return _texto_menu_consultas(spot_key, playa_key), estado

    # Fase: esperando fecha específica
    if fase == "esperando_dia":
        spot_key = estado.get("spot_key")
        playa_key = estado.get("playa_key")

        if not spot_key or not playa_key:
            estado = crear_estado_inicial()
            return _listar_spots(), estado

        fecha = _parsear_fecha_desde_usuario(texto)
        if fecha is None:
            return (
                "No entendí la fecha. Usá el formato dd/mm o dd/mm/aaaa "
                "(ejemplo: 09/12 o 09/12/2025). Probá de nuevo.\n"
                "Escribí 'v' para volver al menú anterior.",
                estado,
            )

        estado["fase"] = "menu_consulta"
        texto_resp = obtener_pronostico_formateado(spot_key, playa_key, fecha)
        return texto_resp, estado

    estado = crear_estado_inicial()
    return _listar_spots(), estado
