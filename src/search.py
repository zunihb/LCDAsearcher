"""Helpers de retrieval y matching para el chat de LCDA Searcher.

El camino single-shot (search_and_respond, build_search_context, ask_llm)
fue reemplazado por el motor agentico en src/llm_backend.py.
Este módulo conserva solo los helpers de matching y el cliente LLM.
"""

from __future__ import annotations

import math
import os
import time as _time
from itertools import combinations
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

from src.db import Database
from src.matching import get_investigador_keyword_matrix

load_dotenv()


def get_llm_client() -> OpenAI | None:
    """Retorna cliente LLM configurado o None si no hay key."""
    api_key = os.getenv("LLM_API_KEY", "")
    if not api_key or api_key.startswith("sk-..."):
        return None
    return OpenAI(
        api_key=api_key,
        base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"),
    )


def _topic_potential(score: float) -> str:
    if score >= 25:
        return "ALTA"
    if score >= 12:
        return "MEDIA"
    return "EXPLORATORIA"


_matches_cache: dict[str, Any] = {
    "sig": None,
    "matches": None,
    "ts": 0,
}


def _db_signature(db: Database) -> str:
    """Firma rápida del estado de la BD (counts de tablas clave)."""
    counts = db.query("""
        SELECT
            (SELECT COUNT(*) FROM investigadores) AS inv,
            (SELECT COUNT(*) FROM papers) AS papers,
            (SELECT COUNT(*) FROM paper_keywords) AS pk
    """)
    if not counts:
        return ""
    c = counts[0]
    return f"{c['inv']}:{c['papers']}:{c['pk']}"


def _get_cached_matches(db: Database, ttl: int = 300) -> list[dict[str, Any]]:
    """Matches cacheados. Recomputa solo si la BD cambió o pasó el TTL."""
    now = _time.time()
    sig = _db_signature(db)

    if (
        _matches_cache["matches"] is not None
        and _matches_cache["sig"] == sig
        and (now - _matches_cache["ts"]) < ttl
    ):
        return _matches_cache["matches"]

    matches = get_matches_investigadores_fast(db, limit=30)
    _matches_cache["sig"] = sig
    _matches_cache["matches"] = matches
    _matches_cache["ts"] = now
    return matches


def get_matches_investigadores_fast(
    db: Database,
    min_papers_each: int = 1,
    limit: int = 30,
) -> list[dict[str, Any]]:
    """Calcula matches entre investigadores por tema compartido.

    Versión rápida sin evidence (evita N² queries). La evidencia se consulta
    bajo demanda via la tool topic_evidence.
    """
    matrix = get_investigador_keyword_matrix(db, min_papers=min_papers_each)

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

            matches.append({
                "keyword": keyword,
                "investigador_1": left["nombre"],
                "scholar_id_1": left["scholar_id"],
                "papers_inv1": int(left["papers"]),
                "citas_inv1": int(left["citas"] or 0),
                "ultimo_anio_inv1": left["ultimo_anio"],
                "investigador_2": right["nombre"],
                "scholar_id_2": right["scholar_id"],
                "papers_inv2": int(right["papers"]),
                "citas_inv2": int(right["citas"] or 0),
                "ultimo_anio_inv2": right["ultimo_anio"],
                "ultimo_anio": ultimo_anio or None,
                "score": round(score, 3),
                "potencial": _topic_potential(score),
            })

    matches.sort(
        key=lambda r: (r["score"], r["ultimo_anio"] or 0, r["papers_inv1"] + r["papers_inv2"]),
        reverse=True,
    )
    return matches[:limit] if limit else matches
