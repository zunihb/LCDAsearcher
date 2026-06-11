"""Diagnóstico de calidad de datos."""

from __future__ import annotations

from typing import Any

from src.topic_search import normalize_keyword


def get_data_quality_report(db) -> dict[str, Any]:
    stats = db.query_one(
        """
        SELECT
            (SELECT COUNT(*) FROM papers) AS papers,
            (SELECT COUNT(*) FROM papers WHERE abstract IS NOT NULL AND trim(abstract) != '') AS con_abstract,
            (SELECT COUNT(*) FROM papers WHERE anio IS NULL) AS sin_anio,
            (SELECT COUNT(*) FROM papers WHERE anio < 1980) AS antes_1980,
            (SELECT COUNT(*) FROM papers WHERE anio < 1990) AS antes_1990,
            (SELECT COUNT(*) FROM papers WHERE doi IS NOT NULL AND trim(doi) != '') AS con_doi,
            (SELECT COUNT(*) FROM papers WHERE openalex_id IS NOT NULL AND trim(openalex_id) != '') AS con_openalex_id,
            (SELECT COUNT(*) FROM papers WHERE url_ieee IS NOT NULL AND trim(url_ieee) != '') AS con_ieee
        """
    ) or {}

    doi_dups = db.query(
        """
        SELECT doi, COUNT(*) AS n
        FROM papers
        WHERE doi IS NOT NULL AND trim(doi) != ''
        GROUP BY doi
        HAVING n > 1
        ORDER BY n DESC, doi
        """
    )

    oa_dups = db.query(
        """
        SELECT openalex_id, COUNT(*) AS n
        FROM papers
        WHERE openalex_id IS NOT NULL AND trim(openalex_id) != ''
        GROUP BY openalex_id
        HAVING n > 1
        ORDER BY n DESC, openalex_id
        """
    )

    kw_rows = db.get_all_keywords()
    norm_map: dict[str, list[str]] = {}
    for row in kw_rows:
        key = normalize_keyword(row["termino_canonico"] or row["termino"])
        norm_map.setdefault(key, []).append(row["termino"])

    fragmented = [
        {"norm": k, "variantes": v, "n": len(v)}
        for k, v in sorted(norm_map.items(), key=lambda kv: len(kv[1]), reverse=True)
        if len(v) >= 3
    ]

    low_abstract_researchers = db.query(
        """
        SELECT
            i.nombre,
            COUNT(DISTINCT p.id) AS papers,
            SUM(CASE WHEN p.abstract IS NOT NULL AND trim(p.abstract) != '' THEN 1 ELSE 0 END) AS con_abstract
        FROM investigadores i
        JOIN autorias a ON i.scholar_id = a.scholar_id
        JOIN papers p ON p.id = a.paper_id
        GROUP BY i.scholar_id, i.nombre
        HAVING papers > 0
        ORDER BY (1.0 * con_abstract / papers) ASC, papers DESC
        """
    )

    suspicious = db.query(
        """
        SELECT id, titulo, anio, doi, openalex_id, citado_por
        FROM papers
        WHERE anio IS NULL OR anio < 1980 OR (abstract IS NULL OR trim(abstract) = '')
        ORDER BY citado_por DESC, anio ASC
        LIMIT 50
        """
    )

    total_papers = stats.get("papers") or 0
    with_abstract = stats.get("con_abstract") or 0

    return {
        "papers": total_papers,
        "papers_con_abstract": with_abstract,
        "cobertura_abstract": round((with_abstract / total_papers) * 100, 1) if total_papers else 0.0,
        "sin_anio": stats.get("sin_anio") or 0,
        "antes_1980": stats.get("antes_1980") or 0,
        "antes_1990": stats.get("antes_1990") or 0,
        "con_doi": stats.get("con_doi") or 0,
        "con_openalex_id": stats.get("con_openalex_id") or 0,
        "con_ieee": stats.get("con_ieee") or 0,
        "doi_duplicados": doi_dups,
        "openalex_duplicados": oa_dups,
        "keywords_fragmentadas": fragmented[:40],
        "investigadores_abstract_bajo": [
            {
                "nombre": r["nombre"],
                "papers": r["papers"],
                "con_abstract": r["con_abstract"],
                "cobertura": round((r["con_abstract"] / r["papers"]) * 100, 1) if r["papers"] else 0.0,
            }
            for r in low_abstract_researchers[:20]
        ],
        "papers_sospechosos": suspicious,
    }


def get_suspicious_records(db, limit: int = 50) -> list[dict[str, Any]]:
    return db.query(
        """
        SELECT id, titulo, anio, doi, openalex_id, citado_por, abstract
        FROM papers
        WHERE anio IS NULL OR anio < 1980 OR abstract IS NULL OR trim(abstract) = ''
        ORDER BY citado_por DESC, anio ASC
        LIMIT ?
        """,
        (limit,),
    )
