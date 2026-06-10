"""Búsqueda semántica: retrieval de SQLite + contexto para LLM."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from datetime import datetime
from itertools import combinations
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

from src.db import Database
from src.matching import get_investigador_keyword_matrix, get_matches_investigadores

load_dotenv()

STOPWORDS_ES = {
    "de", "la", "el", "en", "y", "con", "para", "del", "los", "las", "una",
    "por", "que", "se", "su", "al", "es", "lo", "como", "más", "o", "no",
    "un", "ya", "pero", "fue", "son", "está", "hay", "qué", "quién",
    "quien", "cuál", "cual", "dónde", "donde", "cómo", "cuando", "cuándo",
    "este", "esta", "estos", "estas", "ese", "esa", "esos", "esas",
    "tiene", "tienen", "puede", "sobre", "entre", "todo", "también",
    "me", "mi", "nos", "te", "tu", "sus", "muy", "han", "ser",
    "the", "a", "an", "of", "in", "for", "and", "or", "with", "using",
    "based", "is", "are", "was", "were", "be", "been", "has", "have",
    "had", "do", "does", "did", "will", "would", "can", "could",
    "this", "that", "these", "those", "it", "its", "from", "by",
    "at", "to", "on", "as", "but", "not", "no", "if", "or", "so",
}


def _tokenize(text: str) -> list[str]:
    """Extrae tokens significativos de una consulta."""
    text = text.lower().strip()
    text = re.sub(r"[¿?¡!.,;:()\"']", " ", text)
    words = text.split()
    return [w for w in words if len(w) >= 3 and w not in STOPWORDS_ES]


def _detect_researchers(db: Database, query_text: str) -> list[dict[str, Any]]:
    """Detecta si la consulta menciona un investigador por nombre."""
    invs = db.get_investigadores()
    query_lower = query_text.lower()
    matched = []
    for inv in invs:
        nombre_lower = inv["nombre"].lower()
        # Buscar apellido o nombre completo
        partes = nombre_lower.split()
        for parte in partes:
            if len(parte) >= 4 and parte in query_lower:
                matched.append(inv)
                break
    return matched


def _get_papers_for_researcher(
    db: Database,
    scholar_id: str,
    sort_by: str = "recent",
    limit: int = 15,
) -> list[dict[str, Any]]:
    """Papers de un investigador, ordenados por año o citas."""
    order = "p.anio DESC, p.citado_por DESC" if sort_by == "recent" else "p.citado_por DESC, p.anio DESC"
    return db.query(
        f"""
        SELECT p.titulo, p.anio, p.citado_por, p.autores_texto,
               SUBSTR(p.abstract, 1, 300) AS abstract_corto,
               p.url_doi, p.url_ieee
        FROM papers p
        JOIN autorias a ON p.id = a.paper_id
        WHERE a.scholar_id = ?
        ORDER BY {order}
        LIMIT ?
        """,
        (scholar_id, limit),
    )


def _get_researcher_keyword_profile(
    db: Database,
    scholar_id: str,
    limit: int = 12,
) -> list[dict[str, Any]]:
    """Top keywords de un investigador con stats."""
    return db.query(
        """
        SELECT
            COALESCE(k.termino_canonico, k.termino) AS keyword,
            COUNT(DISTINCT p.id) AS papers,
            SUM(COALESCE(p.citado_por, 0)) AS citas,
            MIN(p.anio) AS primer_anio,
            MAX(p.anio) AS ultimo_anio
        FROM autorias a
        JOIN papers p ON p.id = a.paper_id
        JOIN paper_keywords pk ON pk.paper_id = p.id
        JOIN keywords k ON k.id = pk.keyword_id
        WHERE a.scholar_id = ?
        GROUP BY keyword
        ORDER BY papers DESC, citas DESC
        LIMIT ?
        """,
        (scholar_id, limit),
    )


def _match_keywords(db: Database, tokens: list[str], limit: int = 15) -> list[dict[str, Any]]:
    """Busca keywords que coincidan con los tokens de la query."""
    if not tokens:
        return []

    conditions = " OR ".join(["COALESCE(k.termino_canonico, k.termino) LIKE ?" for _ in tokens])
    params = [f"%{t}%" for t in tokens]

    rows = db.query(
        f"""
        SELECT
            COALESCE(k.termino_canonico, k.termino) AS keyword,
            COUNT(DISTINCT pk.paper_id) AS papers,
            SUM(COALESCE(p.citado_por, 0)) AS citas
        FROM keywords k
        JOIN paper_keywords pk ON k.id = pk.keyword_id
        JOIN papers p ON p.id = pk.paper_id
        WHERE {conditions}
        GROUP BY keyword
        ORDER BY papers DESC, citas DESC
        LIMIT ?
        """,
        tuple(params + [limit]),
    )
    return rows


def _match_titles(db: Database, tokens: list[str], limit: int = 10) -> list[dict[str, Any]]:
    """Busca papers cuyo título contenga algún token."""
    if not tokens:
        return []

    conditions = " OR ".join(["p.titulo LIKE ?" for _ in tokens])
    params = [f"%{t}%" for t in tokens]

    return db.query(
        f"""
        SELECT p.id, p.titulo, p.anio, p.citado_por, p.abstract, p.autores_texto
        FROM papers p
        WHERE {conditions}
        ORDER BY p.citado_por DESC
        LIMIT ?
        """,
        tuple(params + [limit]),
    )


def _get_investigators_for_keywords(
    db: Database,
    keywords: list[str],
) -> list[dict[str, Any]]:
    """Para cada keyword, qué investigadores tienen papers en ese tema."""
    if not keywords:
        return []

    conditions = " OR ".join(
        ["COALESCE(k.termino_canonico, k.termino) = ?" for _ in keywords]
    )

    return db.query(
        f"""
        SELECT
            i.scholar_id,
            i.nombre,
            i.afiliacion,
            COALESCE(k.termino_canonico, k.termino) AS keyword,
            COUNT(DISTINCT p.id) AS papers_en_tema,
            SUM(COALESCE(p.citado_por, 0)) AS citas_en_tema,
            MAX(p.anio) AS ultimo_anio
        FROM investigadores i
        JOIN autorias a ON i.scholar_id = a.scholar_id
        JOIN papers p ON p.id = a.paper_id
        JOIN paper_keywords pk ON pk.paper_id = p.id
        JOIN keywords k ON k.id = pk.keyword_id
        WHERE {conditions}
        GROUP BY i.scholar_id, i.nombre, keyword
        ORDER BY papers_en_tema DESC, citas_en_tema DESC
        """,
        tuple(keywords),
    )


def _get_top_papers_for_keywords(
    db: Database,
    keywords: list[str],
    limit_per_kw: int = 3,
) -> list[dict[str, Any]]:
    """Papers representativos por keyword (top por citas)."""
    if not keywords:
        return []

    conditions = " OR ".join(
        ["COALESCE(k.termino_canonico, k.termino) = ?" for _ in keywords]
    )

    rows = db.query(
        f"""
        SELECT
            p.titulo,
            p.anio,
            p.citado_por,
            p.autores_texto,
            COALESCE(k.termino_canonico, k.termino) AS keyword,
            SUBSTR(p.abstract, 1, 300) AS abstract_corto
        FROM papers p
        JOIN paper_keywords pk ON pk.paper_id = p.id
        JOIN keywords k ON k.id = pk.keyword_id
        WHERE {conditions}
        ORDER BY p.citado_por DESC
        LIMIT ?
        """,
        tuple(keywords + [limit_per_kw * len(keywords)]),
    )
    return rows


# --- Cache de matches para evitar recomputar en cada mensaje ---

import time as _time


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
    """Versión rápida: calcula matches SIN evidence (evita 128K queries).

    La evidencia solo se consulta bajo demanda (chat, /perfil).
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


def build_search_context(
    db: Database,
    query_text: str,
) -> dict[str, Any]:
    """Construye contexto estructurado para el LLM a partir de una consulta."""
    tokens = _tokenize(query_text)

    # 0. Detectar si la consulta menciona un investigador
    detected_researchers = _detect_researchers(db, query_text)

    # 1. Buscar keywords coincidentes
    kw_rows = _match_keywords(db, tokens)
    matched_keywords = [r["keyword"] for r in kw_rows]

    # 2. Buscar papers por título
    title_matches = _match_titles(db, tokens)

    # 3. Investigadores relevantes para esas keywords
    investigators = _get_investigators_for_keywords(db, matched_keywords)

    # 4. Papers representativos por keyword
    top_papers = _get_top_papers_for_keywords(db, matched_keywords)

    # 5. Matches entre investigadores (cacheados)
    all_matches = _get_cached_matches(db)

    # 6. Filtrar matches relevantes para la query
    relevant_matches = []
    for m in all_matches:
        kw = m["keyword"]
        kw_lower = kw.lower()
        if any(t in kw_lower for t in tokens):
            relevant_matches.append(m)
    # Si hay pocos matches relevantes, rellenar con top matches
    if len(relevant_matches) < 5:
        seen = {(m["keyword"], m["investigador_1"], m["investigador_2"]) for m in relevant_matches}
        for m in all_matches:
            key = (m["keyword"], m["investigador_1"], m["investigador_2"])
            if key not in seen:
                relevant_matches.append(m)
            if len(relevant_matches) >= 10:
                break

    # 7. Info de todos los investigadores (con conteo real de papers)
    all_inv = db.get_investigadores()
    paper_counts = db.query("""
        SELECT a.scholar_id, COUNT(DISTINCT a.paper_id) AS papers_reales
        FROM autorias a
        GROUP BY a.scholar_id
    """)
    counts_map = {r["scholar_id"]: r["papers_reales"] for r in paper_counts}

    # 8. Papers coautorados entre investigadores detectados
    coauthored_papers: list[dict[str, Any]] = []
    if len(all_inv) >= 2:
        inv_ids = [i["scholar_id"] for i in all_inv]
        # Construir JOIN para cada par de investigadores
        if len(inv_ids) == 2:
            coauthored_papers = db.query(
                """
                SELECT p.titulo, p.anio, p.citado_por, p.autores_texto,
                       SUBSTR(p.abstract, 1, 300) AS abstract_corto
                FROM papers p
                JOIN autorias a1 ON p.id = a1.paper_id AND a1.scholar_id = ?
                JOIN autorias a2 ON p.id = a2.paper_id AND a2.scholar_id = ?
                ORDER BY p.citado_por DESC
                """,
                (inv_ids[0], inv_ids[1]),
            )

    # 8. Si se detectó un investigador, traer sus datos directamente
    researcher_data = []
    for inv in detected_researchers:
        sid = inv["scholar_id"]
        recent_papers = _get_papers_for_researcher(db, sid, sort_by="recent", limit=15)
        top_cited = _get_papers_for_researcher(db, sid, sort_by="citations", limit=10)
        kw_profile = _get_researcher_keyword_profile(db, sid, limit=12)
        researcher_data.append({
            "nombre": inv["nombre"],
            "afiliacion": inv.get("afiliacion"),
            "scholar_id": sid,
            "indice_h": inv.get("indice_h"),
            "indice_i10": inv.get("indice_i10"),
            "citas_total": inv.get("citas_total"),
            "papers_recientes": [
                {
                    "titulo": p["titulo"],
                    "anio": p["anio"],
                    "citado_por": p["citado_por"],
                    "autores": p.get("autores_texto", ""),
                    "abstract_resumen": p.get("abstract_corto", ""),
                }
                for p in recent_papers
            ],
            "papers_top_citas": [
                {
                    "titulo": p["titulo"],
                    "anio": p["anio"],
                    "citado_por": p["citado_por"],
                }
                for p in top_cited
            ],
            "keywords_principales": [
                {
                    "keyword": k["keyword"],
                    "papers": k["papers"],
                    "citas": k["citas"],
                    "rango": f"{k['primer_anio']}-{k['ultimo_anio']}",
                }
                for k in kw_profile
            ],
        })

    return {
        "query_tokens": tokens,
        "keywords_encontradas": matched_keywords[:15],
        "investigadores_detectados": [
            {"nombre": i["nombre"], "scholar_id": i["scholar_id"]}
            for i in detected_researchers
        ],
        "investigador_detalle": researcher_data,
        "investigadores_perfil": [
            {
                "nombre": i["nombre"],
                "afiliacion": i.get("afiliacion"),
                "papers_total": counts_map.get(i["scholar_id"], 0),
                "citas_total": i.get("citas_total", 0),
                "indice_h": i.get("indice_h"),
                "indice_i10": i.get("indice_i10"),
            }
            for i in all_inv
        ],
        "investigadores_por_keyword": [
            {
                "nombre": r["nombre"],
                "keyword": r["keyword"],
                "papers_en_tema": r["papers_en_tema"],
                "citas_en_tema": r["citas_en_tema"],
                "ultimo_anio": r["ultimo_anio"],
            }
            for r in investigators[:30]
        ],
        "papers_representativos": [
            {
                "titulo": r["titulo"],
                "anio": r["anio"],
                "citado_por": r["citado_por"],
                "autores": r.get("autores_texto", ""),
                "keyword": r["keyword"],
                "abstract_resumen": r.get("abstract_corto", ""),
            }
            for r in top_papers[:20]
        ],
        "papers_por_titulo": [
            {
                "titulo": r["titulo"],
                "anio": r["anio"],
                "citado_por": r["citado_por"],
            }
            for r in title_matches[:10]
        ],
        "matches_tematicos": [
            {
                "keyword": m["keyword"],
                "investigador_1": m["investigador_1"],
                "papers_inv1": m["papers_inv1"],
                "investigador_2": m["investigador_2"],
                "papers_inv2": m["papers_inv2"],
                "potencial": m["potencial"],
                "evidencia_inv1": m.get("evidencia_inv1", ""),
                "evidencia_inv2": m.get("evidencia_inv2", ""),
            }
            for m in relevant_matches[:15]
        ],
        "papers_coautorados": [
            {
                "titulo": r["titulo"],
                "anio": r["anio"],
                "citado_por": r["citado_por"],
                "autores": r.get("autores_texto", ""),
                "abstract_resumen": r.get("abstract_corto", ""),
            }
            for r in coauthored_papers
        ],
    }


SYSTEM_PROMPT = """Eres el asistente de LCDA Searcher.

Responde con un objeto JSON valido que tenga exactamente esta forma:
{{"answer": "respuesta final en markdown"}}

El campo answer debe contener SOLO la respuesta final para el usuario.
No incluyas razonamiento interno, analisis, borradores, pasos privados ni campos extra.
Empieza directamente con la informacion que el usuario pide.
Formato markdown: ## secciones, - bullets, **negritas**, tablas.
Español, académico. Usa SOLO datos del contexto. Cita: "Titulo (Año, N citas)".

Contexto:
{contexto}"""


PLAIN_SYSTEM_PROMPT = """Eres el asistente de LCDA Searcher.

Responde SOLO con la respuesta final para el usuario.
No muestres razonamiento interno, analisis, borradores ni pasos privados.
Empieza directamente con la informacion que el usuario pide.
Formato markdown: ## secciones, - bullets, **negritas**, tablas.
Español, académico. Usa SOLO datos del contexto. Cita: "Titulo (Año, N citas)".

Contexto:
{contexto}"""


def _answer_json_schema() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "lcda_search_answer",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "answer": {"type": "string"},
                },
                "required": ["answer"],
                "additionalProperties": False,
            },
        },
    }


def _parse_answer_content(content: str) -> str:
    """Lee la respuesta estructurada del LLM y tolera fences JSON si aparecen."""
    text = (content or "").strip()
    if not text:
        return ""
    if text.startswith("```"):
        text = text.removeprefix("```json").removeprefix("```").strip()
        text = text.removesuffix("```").strip()
    try:
        data = json.loads(text, strict=False)
    except json.JSONDecodeError:
        match = re.search(r'^\s*\{\s*"answer"\s*:\s*"(.*)"\s*\}\s*$', text, re.DOTALL)
        if match:
            return match.group(1).replace(r"\n", "\n").replace(r"\"", '"').strip()
        return content
    if isinstance(data, dict) and isinstance(data.get("answer"), str):
        answer = data["answer"].strip()
        return answer or content
    return content


def format_context_for_prompt(ctx: dict[str, Any]) -> str:
    """Formatea el contexto como texto legible para el prompt."""
    parts = []

    # Fecha actual
    fecha = datetime.now().strftime("%Y-%m-%d (%A)")
    parts.append(f"=== FECHA ACTUAL ===\n{fecha}")

    # Investigadores
    parts.append("=== INVESTIGADORES ===")
    for inv in ctx["investigadores_perfil"]:
        parts.append(f"- {inv['nombre']} ({inv['afiliacion'] or 'sin afiliación'})")
        parts.append(f"  Papers: {inv.get('papers_total', '?')}, Citas: {inv.get('citas_total', '?')}, h-index: {inv.get('indice_h', '?')}")

    # Detalle de investigador detectado en la consulta
    if ctx.get("investigador_detalle"):
        for rd in ctx["investigador_detalle"]:
            parts.append(f"\n=== PERFIL DETALLADO: {rd['nombre']} ===")
            parts.append(f"  Afiliación: {rd.get('afiliacion') or 'N/A'}")
            parts.append(f"  h-index: {rd.get('indice_h', '?')} | i10: {rd.get('indice_i10', '?')} | Citas: {rd.get('citas_total', '?')}")

            if rd.get("keywords_principales"):
                parts.append("\n  Líneas de investigación principales:")
                for kw in rd["keywords_principales"]:
                    parts.append(
                        f"    - {kw['keyword']}: {kw['papers']} papers, "
                        f"{kw['citas']} citas ({kw['rango']})"
                    )

            if rd.get("papers_recientes"):
                parts.append("\n  Papers más recientes:")
                for p in rd["papers_recientes"][:12]:
                    abstract_brief = (p.get("abstract_resumen") or "")[:120]
                    line = f"    - \"{p['titulo']}\" ({p['anio']}, {p['citado_por']} citas)"
                    if abstract_brief:
                        line += f"\n      {abstract_brief}..."
                    parts.append(line)

            if rd.get("papers_top_citas"):
                parts.append("\n  Papers más citados:")
                for p in rd["papers_top_citas"][:8]:
                    parts.append(f"    - \"{p['titulo']}\" ({p['anio']}, {p['citado_por']} citas)")

    # Keywords encontradas
    if ctx["keywords_encontradas"]:
        parts.append(f"\n=== KEYWORDS DETECTADAS EN LA CONSULTA ===")
        parts.append(", ".join(ctx["keywords_encontradas"][:10]))

    # Investigadores por keyword
    if ctx["investigadores_por_keyword"]:
        parts.append("\n=== INVESTIGADORES POR TEMA ===")
        for r in ctx["investigadores_por_keyword"][:20]:
            parts.append(
                f"- {r['nombre']}: {r['keyword']} → {r['papers_en_tema']} papers, "
                f"{r['citas_en_tema']} citas, último año: {r['ultimo_anio']}"
            )

    # Papers representativos
    if ctx["papers_representativos"]:
        parts.append("\n=== PAPERS REPRESENTATIVOS ===")
        for p in ctx["papers_representativos"][:15]:
            abstract_brief = (p["abstract_resumen"] or "")[:150]
            if abstract_brief:
                abstract_brief += "..."
            parts.append(
                f"- [{p['keyword']}] \"{p['titulo']}\" ({p['anio']}, {p['citado_por']} citas)"
            )
            if abstract_brief:
                parts.append(f"  Abstract: {abstract_brief}")

    # Papers encontrados por título
    if ctx["papers_por_titulo"]:
        parts.append("\n=== PAPERS ENCONTRADOS POR TÍTULO ===")
        for p in ctx["papers_por_titulo"][:8]:
            parts.append(f"- \"{p['titulo']}\" ({p['anio']}, {p['citado_por']} citas)")

    # Matches temáticos
    if ctx["matches_tematicos"]:
        parts.append("\n=== MATCHES TEMÁTICOS ENTRE INVESTIGADORES ===")
        for m in ctx["matches_tematicos"][:12]:
            parts.append(
                f"- {m['keyword']}: {m['investigador_1']} ({m['papers_inv1']} papers) ↔ "
                f"{m['investigador_2']} ({m['papers_inv2']} papers) — {m['potencial']}"
            )
            if m.get("evidencia_inv1"):
                parts.append(f"  Evidencia {m['investigador_1']}: {m['evidencia_inv1'][:120]}")

    # Papers coautorados
    if ctx.get("papers_coautorados"):
        parts.append("\n=== PAPERS COAUTORADOS ENTRE INVESTIGADORES ===")
        for p in ctx["papers_coautorados"][:10]:
            abstract_brief = (p.get("abstract_resumen") or "")[:150]
            parts.append(f"- \"{p['titulo']}\" ({p['anio']}, {p['citado_por']} citas)")
            if abstract_brief:
                parts.append(f"  Abstract: {abstract_brief}...")

    return "\n".join(parts)


def get_llm_client() -> OpenAI | None:
    """Retorna cliente LLM configurado o None si no hay key."""
    api_key = os.getenv("LLM_API_KEY", "")
    if not api_key or api_key.startswith("sk-..."):
        return None
    return OpenAI(
        api_key=api_key,
        base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"),
    )


def ask_llm(
    client: OpenAI,
    context: dict[str, Any],
    pregunta: str,
    historial: list[dict[str, str]] | None = None,
) -> dict[str, str]:
    """Envía contexto + pregunta al LLM. Retorna reasoning separado y respuesta final."""
    model = os.getenv("LLM_MODEL", "mimo-v2.5-pro")
    contexto_texto = format_context_for_prompt(context)

    attempts: list[tuple[str | None, str]] = [
        ("json_schema", SYSTEM_PROMPT),
        ("json_object", SYSTEM_PROMPT),
        (None, PLAIN_SYSTEM_PROMPT),
    ]
    last_error: Exception | None = None

    for fmt, prompt_template in attempts:
        system = prompt_template.format(contexto=contexto_texto)
        messages: list[dict[str, str]] = [{"role": "system", "content": system}]

        if historial:
            messages.extend(historial)

        messages.append({"role": "user", "content": pregunta})

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": float(os.getenv("LLM_TEMPERATURE", "0.1")),
            "max_tokens": int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "4000")),
        }
        if fmt == "json_schema":
            kwargs["response_format"] = _answer_json_schema()
        elif fmt == "json_object":
            kwargs["response_format"] = {"type": "json_object"}

        try:
            resp = client.chat.completions.create(**kwargs)
        except Exception as exc:
            last_error = exc
            continue

        msg = resp.choices[0].message
        content = msg.content or ""
        if fmt:
            content = _parse_answer_content(content)
        return {
            "reasoning": (
                getattr(msg, "reasoning", None)
                or getattr(msg, "reasoning_content", None)
                or ""
            ),
            "content": content or "(sin respuesta)",
        }

    if last_error:
        raise last_error
    return {"reasoning": "", "content": "(sin respuesta)"}


def search_and_respond(
    db: Database,
    client: OpenAI,
    pregunta: str,
    historial: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Búsqueda completa: retrieval + LLM. Retorna respuesta y metadatos."""
    context = build_search_context(db, pregunta)
    result = ask_llm(client, context, pregunta, historial)
    return {
        "respuesta": result["content"],
        "reasoning": result["reasoning"],
        "keywords_detectadas": context["keywords_encontradas"],
        "papers_encontrados": len(context["papers_representativos"]) + len(context["papers_por_titulo"]),
        "matches_relevantes": len(context["matches_tematicos"]),
        "context": context,
    }


def ask_llm_stream(
    client: OpenAI,
    context: dict[str, Any],
    pregunta: str,
    historial: list[dict[str, str]] | None = None,
):
    """Generador que yielda chunks de la respuesta conforme llegan."""
    model = os.getenv("LLM_MODEL", "mimo-v2.5-pro")
    contexto_texto = format_context_for_prompt(context)
    system = PLAIN_SYSTEM_PROMPT.format(contexto=contexto_texto)

    messages: list[dict[str, str]] = [{"role": "system", "content": system}]
    if historial:
        messages.extend(historial)
    messages.append({"role": "user", "content": pregunta})

    stream = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=float(os.getenv("LLM_TEMPERATURE", "0.1")),
        max_tokens=int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "4000")),
        stream=True,
    )

    for chunk in stream:
        delta = chunk.choices[0].delta if chunk.choices else None
        if delta and delta.content:
            yield delta.content
