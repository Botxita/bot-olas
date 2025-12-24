"""Configuración de spots y playas para el bot de olas.

Cada spot es una ciudad o zona general (Mar del Plata, Chapadmalal, etc.).
Dentro de cada spot puede haber una o varias playas con pronóstico propio.
"""

SPOTS = {
    # -------------------------------------------------
    # MAR DEL PLATA
    # -------------------------------------------------
    "mar_del_plata": {
        "nombre": "Mar del Plata",
        "playas": {
            # Varese
            # Coordenadas aproximadas de Playa Varese / Torreón, Mar del Plata
            "varese": {
                "nombre": "Varese",
                "lat": -38.0088389,
                "lon": -57.532875,
                "fuente": "open-meteo",
                "id_fuente": "mdq_varese",
            },
            # La Perla
            # Coordenadas aproximadas de Balneario Alfonsina / La Perla
            "la_perla": {
                "nombre": "La Perla",
                "lat": -37.9942866,
                "lon": -57.5457393,
                "fuente": "open-meteo",
                "id_fuente": "mdq_la_perla",
            },
            # Biología
            # Coordenadas aproximadas de Playa Biología
            "biologia": {
                "nombre": "Biología",
                "lat": -38.0291667,
                "lon": -57.5325,
                "fuente": "open-meteo",
                "id_fuente": "mdq_biologia",
            },
            # Mariano
            # Coordenadas aproximadas de Playa Mariano
            "mariano": {
                "nombre": "Mariano",
                "lat": -38.0833333,
                "lon": -57.5388889,
                "fuente": "open-meteo",
                "id_fuente": "mdq_mariano",
            },
            # Sun Rider / Waikiki
            # Coordenadas de "Mar del Plata - Playa Sun Rider" (Windy)
            "sun_waikiki": {
                "nombre": "Sun Rider / Waikiki",
                "lat": -37.9554,
                "lon": -57.538,
                "fuente": "open-meteo",
                "id_fuente": "mdq_sun_waikiki",
            },
            # General para MDP (otras playas)
            # Coordenadas generales de Mar del Plata ciudad
            "general": {
                "nombre": "General (otras playas)",
                "lat": -38.00042,
                "lon": -57.5562,
                "fuente": "open-meteo",
                "id_fuente": "mdq_general",
            },
        },
    },

    # -------------------------------------------------
    # CHAPADMALAL
    # -------------------------------------------------
    "chapadmalal": {
        "nombre": "Chapadmalal",
        "playas": {
            # General Chapadmalal – todas las playas internas
            # Coordenadas aproximadas de Chapadmalal
            "general": {
                "nombre": "General",
                "lat": -38.03,
                "lon": -57.72,
                "fuente": "open-meteo",
                "id_fuente": "chapa_general",
            },
        },
    },

    # -------------------------------------------------
    # MIRAMAR
    # -------------------------------------------------
    "miramar": {
        "nombre": "Miramar",
        "playas": {
            # General Miramar – todas las playas internas
            # Coordenadas generales de Miramar
            "general": {
                "nombre": "General",
                "lat": -38.27044,
                "lon": -57.8388,
                "fuente": "open-meteo",
                "id_fuente": "miramar_general",
            },
        },
    },

    # -------------------------------------------------
    # QUEQUÉN
    # -------------------------------------------------
    "quequen": {
        "nombre": "Quequén",
        "playas": {
            # Monte Pasubio (antes "Monte Pasubio / La Hélice"; dejamos solo el nombre Monte Pasubio)
            # Coordenadas de la playa Monte Pasubio
            "monte_pasubio": {
                "nombre": "Monte Pasubio",
                "lat": -38.57419,
                "lon": -58.6901,
                "fuente": "open-meteo",
                "id_fuente": "quequen_monte_pasubio",
            },
        },
    },

    # -------------------------------------------------
    # NECOCHEA
    # -------------------------------------------------
    "necochea": {
        "nombre": "Necochea",
        "playas": {
            # Escollera (Escollera Sur Necochea)
            # Coordenadas de la escollera de Necochea
            "escollera": {
                "nombre": "Escollera",
                "lat": -38.584204,
                "lon": -58.697162,
                "fuente": "open-meteo",
                "id_fuente": "necochea_escollera",
            },
        },
    },
}


def listar_spots():
    """Devuelve una lista de (key, datos_spot) ordenada por nombre."""
    return sorted(SPOTS.items(), key=lambda kv: kv[1]["nombre"].lower())


def listar_playas(spot_key):
    """Devuelve una lista de (playa_key, datos_playa) para un spot dado."""
    spot = SPOTS[spot_key]
    return sorted(spot["playas"].items(), key=lambda kv: kv[1]["nombre"].lower())
