"""Cruce temático real entre investigadores.

A diferencia de ``Database.get_sinergias()``, que detecta keywords en papers
coautorados por pares de investigadores, este módulo construye una matriz
investigador-keyword y luego compara pares de investigadores aunque no hayan
publicado juntos. Ese es el match que se necesita para escalar el piloto a
80-90 perfiles.
"""

from __future__ import annotations

import csv
import math
import time
from itertools import combinations
from pathlib import Path
from typing import Any

from src.db import Database


def _topic_potential(score: float) -> str:
    if score >= 25:
        return "ALTA"
    if score >= 12:
        return "MEDIA"
    return "EXPLORATORIA"


def _evidence_for_keyword(
    db: Database,
    scholar_id: str,
    keyword: str,
    limit: int = 3,
    recent_since: int | None = None,
) -> str:
    where_recent = ""
    params: list[Any] = [scholar_id, keyword]
    if recent_since is not None:
        where_recent = "AND p.anio >= ?"
        params.append(recent_since)

    rows = db.query(
        f"""
        SELECT p.titulo, p.anio, COALESCE(p.citado_por, 0) AS citado_por
        FROM papers p
        JOIN autorias a ON p.id = a.paper_id
        JOIN paper_keywords pk ON p.id = pk.paper_id
        JOIN keywords k ON k.id = pk.keyword_id
        WHERE a.scholar_id = ?
          AND COALESCE(k.termino_canonico, k.termino) = ?
          {where_recent}
        ORDER BY COALESCE(p.citado_por, 0) DESC, p.anio DESC
        LIMIT ?
        """,
        tuple(params + [limit]),
    )
    parts = []
    for row in rows:
        year = row["anio"] if row["anio"] is not None else "s/a"
        parts.append(f"{row['titulo']} ({year}, {row['citado_por']} citas)")
    return " | ".join(parts)


def get_investigador_keyword_matrix(
    db: Database,
    min_papers: int = 1,
    recent_since: int | None = None,
) -> list[dict[str, Any]]:
    """Devuelve filas investigador-keyword con conteos, impacto y rango temporal."""

    where_recent = ""
    params: list[Any] = []
    if recent_since is not None:
        where_recent = "WHERE p.anio >= ?"
        params.append(recent_since)

    return db.query(
        f"""
        SELECT
            i.scholar_id,
            i.nombre,
            COALESCE(k.termino_canonico, k.termino) AS keyword,
            COUNT(DISTINCT p.id) AS papers,
            SUM(COALESCE(p.citado_por, 0)) AS citas,
            MIN(p.anio) AS primer_anio,
            MAX(p.anio) AS ultimo_anio
        FROM investigadores i
        JOIN autorias a ON i.scholar_id = a.scholar_id
        JOIN papers p ON p.id = a.paper_id
        JOIN paper_keywords pk ON pk.paper_id = p.id
        JOIN keywords k ON k.id = pk.keyword_id
        {where_recent}
        GROUP BY i.scholar_id, i.nombre, keyword
        HAVING papers >= ?
        ORDER BY i.nombre, papers DESC, citas DESC, keyword
        """,
        tuple(params + [min_papers]),
    )


def get_matches_investigadores(
    db: Database,
    min_papers_each: int = 1,
    recent_since: int | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Compara investigadores por keywords compartidas.

    El score mezcla tres señales simples y auditables:
    - piso compartido: papers mínimos entre ambos investigadores para la keyword;
    - volumen total de papers;
    - impacto bibliométrico aproximado por citas.
    """

    matrix = get_investigador_keyword_matrix(
        db,
        min_papers=min_papers_each,
        recent_since=recent_since,
    )
    by_keyword: dict[str, list[dict[str, Any]]] = {}
    for row in matrix:
        by_keyword.setdefault(row["keyword"], []).append(row)

    matches: list[dict[str, Any]] = []
    for keyword, rows in by_keyword.items():
        if len(rows) < 2:
            continue
        for left, right in combinations(rows, 2):
            total_papers = int(left["papers"]) + int(right["papers"])
            total_citas = int(left["citas"] or 0) + int(right["citas"] or 0)
            floor_shared = min(int(left["papers"]), int(right["papers"]))
            ultimo_anio = max(left["ultimo_anio"] or 0, right["ultimo_anio"] or 0)

            score = (
                floor_shared * 5.0
                + total_papers * 0.25
                + math.log1p(total_citas) * 0.5
            )
            if recent_since is not None:
                score += 1.0

            match = {
                "keyword": keyword,
                "investigador_1": left["nombre"],
                "scholar_id_1": left["scholar_id"],
                "papers_inv1": int(left["papers"]),
                "citas_inv1": int(left["citas"] or 0),
                "primer_anio_inv1": left["primer_anio"],
                "ultimo_anio_inv1": left["ultimo_anio"],
                "investigador_2": right["nombre"],
                "scholar_id_2": right["scholar_id"],
                "papers_inv2": int(right["papers"]),
                "citas_inv2": int(right["citas"] or 0),
                "primer_anio_inv2": right["primer_anio"],
                "ultimo_anio_inv2": right["ultimo_anio"],
                "ultimo_anio": ultimo_anio or None,
                "score": round(score, 3),
                "potencial": _topic_potential(score),
            }
            match["evidencia_inv1"] = _evidence_for_keyword(
                db, left["scholar_id"], keyword, recent_since=recent_since
            )
            match["evidencia_inv2"] = _evidence_for_keyword(
                db, right["scholar_id"], keyword, recent_since=recent_since
            )
            matches.append(match)

    matches.sort(
        key=lambda r: (
            r["score"],
            r["ultimo_anio"] or 0,
            r["papers_inv1"] + r["papers_inv2"],
        ),
        reverse=True,
    )
    return matches[:limit] if limit else matches


def write_matching_outputs(
    db: Database,
    output_dir: Path,
    recent_since: int = 2021,
) -> dict[str, str]:
    """Exporta matriz investigador-keyword y matches temáticos generales/recientes."""

    t0 = time.time()
    output_dir.mkdir(parents=True, exist_ok=True)

    matrix_path = output_dir / "matriz_investigador_keyword.csv"
    matrix = get_investigador_keyword_matrix(db)
    with matrix_path.open("w", newline="", encoding="utf-8") as f:
        fieldnames = [
            "scholar_id",
            "nombre",
            "keyword",
            "papers",
            "citas",
            "primer_anio",
            "ultimo_anio",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(matrix)

    matches_path = output_dir / "matches_investigadores.csv"
    matches = get_matches_investigadores(db)
    _write_matches_csv(matches_path, matches)

    recent_path = output_dir / f"matches_recientes_{recent_since}.csv"
    recent_matches = get_matches_investigadores(db, recent_since=recent_since)
    _write_matches_csv(recent_path, recent_matches)

    db.log_metrica(
        "matching",
        time.time() - t0,
        f"{len(matrix)} filas matriz, {len(matches)} matches, {len(recent_matches)} recientes desde {recent_since}",
    )
    return {
        "matriz": str(matrix_path),
        "matches": str(matches_path),
        "matches_recientes": str(recent_path),
    }


def _write_matches_csv(path: Path, matches: list[dict[str, Any]]) -> None:
    fieldnames = [
        "keyword",
        "investigador_1",
        "papers_inv1",
        "citas_inv1",
        "primer_anio_inv1",
        "ultimo_anio_inv1",
        "investigador_2",
        "papers_inv2",
        "citas_inv2",
        "primer_anio_inv2",
        "ultimo_anio_inv2",
        "ultimo_anio",
        "score",
        "potencial",
        "evidencia_inv1",
        "evidencia_inv2",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in matches:
            writer.writerow({key: row.get(key) for key in fieldnames})
