"""Normalización y búsqueda temática híbrida."""

from __future__ import annotations

import unicodedata
import re
from typing import Any

STOPWORDS = {
    "de", "la", "el", "en", "y", "con", "para", "del", "los", "las", "una",
    "por", "que", "se", "su", "al", "es", "lo", "como", "más", "o", "no",
    "un", "ya", "pero", "fue", "son", "está", "hay", "qué", "quien",
    "cuál", "dónde", "cómo", "cuando", "este", "esta", "estos", "estas",
    "that", "this", "these", "those", "with", "using", "based", "from",
}


def normalize_text(text: str) -> str:
    text = (text or "").strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9\s\-+]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_keyword(text: str) -> str:
    text = normalize_text(text)
    text = text.replace("-", " ")
    text = re.sub(r"\b(fcs\s*m?pc|fcs-mpc)\b", "control predictivo de conjunto finito", text)
    text = re.sub(r"\b(mpc|model predictive control)\b", "control predictivo", text)
    text = re.sub(r"\b(grid connected|grid-connected)\b", "conectado a red", text)
    text = re.sub(r"\bphotovoltaic\b", "fotovoltaica", text)
    text = re.sub(r"\bconverter(s)?\b", "convertidor", text)
    text = re.sub(r"\bcontrollers?\b", "control", text)
    text = re.sub(r"\bsystems?\b", "sistema", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def tokenize_query(text: str) -> list[str]:
    norm = normalize_text(text)
    tokens = []
    for token in norm.split():
        if len(token) < 3 or token in STOPWORDS:
            continue
        tokens.append(token)
    return tokens


def load_keyword_aliases(db) -> dict[str, str]:
    rows = db.get_keyword_aliases()
    return {r["alias"]: r["canonical"] for r in rows}


def resolve_keyword_alias(db, keyword: str) -> str:
    norm = normalize_keyword(keyword)
    aliases = load_keyword_aliases(db)
    return aliases.get(norm, norm)


def search_keywords_hybrid(db, term: str, limit: int = 15) -> list[dict[str, Any]]:
    # Resolver alias antes de tokenizar
    resolved = resolve_keyword_alias(db, term)
    tokens = tokenize_query(resolved)
    if not tokens:
        return []

    aliases = load_keyword_aliases(db)
    rows = db.query(
        """
        SELECT
            COALESCE(k.termino_canonico, k.termino) AS keyword,
            COALESCE(k.keyword_norm, k.termino_canonico, k.termino) AS keyword_norm,
            COUNT(DISTINCT pk.paper_id) AS papers,
            SUM(COALESCE(p.citado_por, 0)) AS citas,
            MAX(p.anio) AS ultimo_anio
        FROM keywords k
        JOIN paper_keywords pk ON k.id = pk.keyword_id
        JOIN papers p ON p.id = pk.paper_id
        GROUP BY keyword, keyword_norm
        """
    )

    scored: list[dict[str, Any]] = []
    for row in rows:
        keyword_norm = normalize_keyword(row["keyword_norm"] or row["keyword"])
        canonical = aliases.get(keyword_norm, keyword_norm)
        text = f"{keyword_norm} {canonical}"
        hits = sum(1 for t in tokens if t in text)
        if not hits:
            continue
        common_penalty = 1.0 / max(1.0, (row["papers"] or 1) ** 0.35)
        score = (hits * 2.5) + (row["papers"] or 0) * 0.08 + ((row["citas"] or 0) ** 0.25) + common_penalty
        scored.append({
            "keyword": row["keyword"],
            "keyword_norm": keyword_norm,
            "canonical": canonical,
            "papers": row["papers"],
            "citas": row["citas"],
            "ultimo_anio": row["ultimo_anio"],
            "score": round(score, 3),
        })

    scored.sort(key=lambda r: (r["score"], r["papers"], r["citas"]), reverse=True)
    return scored[:limit]


def keyword_growth_proxy(db, keyword: str) -> float:
    rows = db.query(
        """
        SELECT p.anio, COUNT(*) AS conteo
        FROM papers p
        JOIN paper_keywords pk ON pk.paper_id = p.id
        JOIN keywords k ON k.id = pk.keyword_id
        WHERE COALESCE(k.termino_canonico, k.termino, k.keyword_norm) LIKE ?
          AND p.anio IS NOT NULL
        GROUP BY p.anio
        ORDER BY p.anio
        """,
        (f"%{normalize_keyword(keyword)}%",),
    )
    if not rows:
        return 0.0
    series = [float(r["conteo"]) for r in rows]
    if len(series) < 2:
        return 0.0
    from src.trends import _slope

    return _slope(series)


def seed_default_keyword_aliases(db) -> None:
    defaults = {
        "mpc": "control predictivo",
        "model predictive control": "control predictivo",
        "fcs mpc": "control predictivo de conjunto finito",
        "fcs-mpc": "control predictivo de conjunto finito",
        "photovoltaic": "fotovoltaica",
        "grid connected": "conectado a red",
        "grid-connected": "conectado a red",
    }
    for alias, canonical in defaults.items():
        db.upsert_keyword_alias(alias, canonical, fuente="default")
