"""Script enfocado: scrapea abstracts de IEEE Xplore solo para papers LCDA de power electronics."""

from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import time
from src.db import Database
from src.abstracts_ieee import enrich_paper_ieee


def run():
    db = Database('data/lcda.db')

    # Solo papers de investigadores LCDA, sin abstract, con keywords de PE
    papers = db.query("""
        SELECT DISTINCT p.id, p.titulo, p.anio, p.citado_por, p.abstract,
               p.autores_texto, p.scholar_pub_id, p.venue, p.url_scholar,
               p.doi, p.url_doi, p.url_ieee
        FROM papers p
        JOIN autorias a ON p.id = a.paper_id
        JOIN paper_keywords pk ON p.id = pk.paper_id
        JOIN keywords k ON k.id = pk.keyword_id
        WHERE (p.abstract IS NULL OR trim(p.abstract) = '')
        AND (
            LOWER(k.termino) LIKE '%power%' OR
            LOWER(k.termino) LIKE '%converter%' OR
            LOWER(k.termino) LIKE '%inverter%' OR
            LOWER(k.termino) LIKE '%electronic%' OR
            LOWER(k.termino) LIKE '%motor%' OR
            LOWER(k.termino) LIKE '%grid%' OR
            LOWER(k.termino) LIKE '%voltage%' OR
            LOWER(k.termino) LIKE '%energy%' OR
            LOWER(k.termino) LIKE '%control%' OR
            LOWER(k.termino) LIKE '%drive%'
        )
        ORDER BY p.citado_por DESC
    """)

    total = len(papers)
    print(f"Papers LCDA de PE sin abstract: {total}")
    print(f"Fuente: IEEE Xplore search API")
    print(f"Delay: 0.8s entre papers, pausa 5s cada 50 papers\n")

    ok = 0
    fail = 0

    for i, paper in enumerate(papers, 1):
        titulo_corto = (paper["titulo"] or "")[:55]
        print(f"  [{i}/{total}] {titulo_corto}...", end="", flush=True)

        success = enrich_paper_ieee(db, paper)

        if success:
            ok += 1
            print(" ✓", flush=True)
        else:
            fail += 1
            print(" ✗", flush=True)

        time.sleep(0.8)

        if i % 50 == 0:
            pct = 100 * ok / i if i > 0 else 0
            print(f"\n  --- Progreso: {ok}/{i} ({pct:.0f}%) ---\n", flush=True)
            time.sleep(5)

    pct = 100 * ok / total if total > 0 else 0
    print(f"\nResultado: {ok}/{total} ({pct:.0f}%)")

    # Estado final
    stats = db.query_one("""
        SELECT 
            COUNT(*) AS total,
            SUM(CASE WHEN abstract IS NOT NULL AND trim(abstract) != '' THEN 1 ELSE 0 END) AS con_abstract
        FROM papers
    """)
    print(f"Estado final: {stats['con_abstract']}/{stats['total']} con abstract")


if __name__ == "__main__":
    run()
