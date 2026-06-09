#!/usr/bin/env python3
"""Carga datos de demostración (papers reales del piloto) sin scraping Scholar."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.db import Database

PAPERS = [
    {
        "titulo": "A robust phase-locked loop algorithm to synchronize static-power converters with polluted AC systems",
        "anio": 2008,
        "citado_por": 156,
        "abstract": "PLL synchronization for static power converters in polluted AC systems.",
        "autores": "MA Pérez, JR Espinoza, LA Morán, MA Torres, EA Araya",
        "authors": ["Wk2naEgAAAAJ", "6O2aO7IAAAAJ"],
    },
    {
        "titulo": "State of the art of finite control set model predictive control in power electronics",
        "anio": 2012,
        "citado_por": 2228,
        "abstract": "FCS-MPC applications in power electronics, drives, active filters, distributed generation.",
        "autores": "J Rodriguez, MP Kazmierkowski, JR Espinoza, P Zanchetta",
        "authors": ["Wk2naEgAAAAJ"],
    },
    {
        "titulo": "Self-tuning virtual synchronous machine: A control strategy for energy storage systems to support dynamic frequency control",
        "anio": 2014,
        "citado_por": 440,
        "abstract": "Virtual synchronous machine for dynamic frequency control in power systems.",
        "autores": "M Torres, LAC Lopes, J Espinoza, L Morán",
        "authors": ["6O2aO7IAAAAJ", "Wk2naEgAAAAJ"],
    },
    {
        "titulo": "Integration of a large-scale photovoltaic plant using a multilevel converter topology and virtual synchronous generator control",
        "anio": 2014,
        "citado_por": 27,
        "abstract": "Large-scale PV integration with multilevel converters and VSG control.",
        "autores": "M Torres, J Espinoza, L Moran, J Rohten, P Melin",
        "authors": ["6O2aO7IAAAAJ", "Wk2naEgAAAAJ"],
    },
    {
        "titulo": "MPC algorithm with reduced computational burden and fixed switching spectrum for a multilevel inverter in a photovoltaic system",
        "anio": 2020,
        "citado_por": 35,
        "abstract": "Model predictive control for multilevel inverter in photovoltaic applications.",
        "autores": "JJ Silva, JR Espinoza, JA Rohten, MA Torres",
        "authors": ["Wk2naEgAAAAJ", "6O2aO7IAAAAJ"],
    },
    {
        "titulo": "Virtual synchronous generator control in autonomous wind-diesel power systems",
        "anio": 2009,
        "citado_por": 120,
        "abstract": "VSG control for autonomous wind-diesel microgrids.",
        "autores": "M Torres, LAC Lopes",
        "authors": ["6O2aO7IAAAAJ"],
    },
    {
        "titulo": "PWM regenerative rectifiers: State of the art",
        "anio": 2005,
        "citado_por": 1215,
        "abstract": "State of the art in PWM regenerative rectifiers for industrial applications.",
        "autores": "JR Rodríguez, JW Dixon, JR Espinoza",
        "authors": ["Wk2naEgAAAAJ"],
    },
    {
        "titulo": "Grid connected PV system with maximum power point estimation based on reference cells",
        "anio": 2015,
        "citado_por": 14,
        "abstract": "Grid-connected PV with MPPT based on reference cells.",
        "autores": "J Silva, J Espinoza, J Rohten, M Torres",
        "authors": ["Wk2naEgAAAAJ", "6O2aO7IAAAAJ"],
    },
]

INVESTIGADORES = [
    {"scholar_id": "Wk2naEgAAAAJ", "nombre": "José R. Espinoza", "afiliacion": "Universidad de Concepción", "citas_total": 12900, "indice_h": 50, "indice_i10": 158},
    {"scholar_id": "6O2aO7IAAAAJ", "nombre": "Miguel Torres", "afiliacion": "Universidad de los Andes", "citas_total": 1200, "indice_h": 15, "indice_i10": 20},
]

COAUTORES = [
    ("Wk2naEgAAAAJ", "Jose Rodriguez", "Universidad San Sebastian", None),
    ("Wk2naEgAAAAJ", "Carlos R. Baier", "Universidad de Talca", None),
    ("Wk2naEgAAAAJ", "Pedro E. Melín C.", "Universidad del Bío-Bío", None),
    ("Wk2naEgAAAAJ", "Luis Morán", "Universidad de Concepción", None),
]


def main():
    db = Database("data/lcda.db")
    db.init_schema()

    for inv in INVESTIGADORES:
        db.upsert_investigador(**inv)

    for nombre, aff, sid in [(c[1], c[2], c[3]) for c in COAUTORES]:
        db.upsert_coautor("Wk2naEgAAAAJ", nombre, aff, sid)

    for p in PAPERS:
        pid = db.upsert_paper(
            titulo=p["titulo"],
            abstract=p["abstract"],
            anio=p["anio"],
            citado_por=p["citado_por"],
            autores_texto=p["autores"],
        )
        for sid in p["authors"]:
            db.add_autoria(sid, pid)

    print(f"Seed: {len(INVESTIGADORES)} investigadores, {len(PAPERS)} papers")


if __name__ == "__main__":
    main()
