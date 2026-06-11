"""Enriquecimiento de abstracts vía múltiples APIs gratuitas.

Fuentes en orden de prioridad:
1. Semantic Scholar API (gratis, sin autenticación, buena cobertura)
2. CrossRef API (gratis, para papers con DOI)
3. OpenAlex (cuando hay presupuesto)
"""

from __future__ import annotations

import re
import time
import requests
from typing import Any

from src.db import Database


def _clean_abstract(text: str) -> str:
    """Limpia abstract de tags HTML y espacios extra."""
    if not text:
        return ""
    # Quitar tags HTML comunes en abstracts de Scholar
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _search_semantic_scholar(titulo: str) -> dict[str, Any] | None:
    """Busca un paper en Semantic Scholar (bulk endpoint) y retorna abstract."""
    try:
        r = requests.get(
            "https://api.semanticscholar.org/graph/v1/paper/search/bulk",
            params={
                "query": titulo,
                "fields": "title,abstract,year,externalIds",
            },
            timeout=25,
        )
        if r.status_code == 429:
            time.sleep(10)
            r = requests.get(
                "https://api.semanticscholar.org/graph/v1/paper/search/bulk",
                params={
                    "query": titulo,
                    "fields": "title,abstract,year,externalIds",
                },
                timeout=25,
            )
        if r.status_code != 200:
            return None

        results = r.json().get("data", [])
        if not results:
            return None

        # Buscar el mejor match por título
        titulo_lower = titulo.lower().strip()
        titulo_words = set(titulo_lower.split())
        best = None
        best_score = 0

        for paper in results[:10]:
            paper_title = (paper.get("title") or "").lower().strip()
            if not paper_title:
                continue

            paper_words = set(paper_title.split())
            if titulo_words and paper_words:
                overlap = len(titulo_words & paper_words) / max(len(titulo_words), len(paper_words))
                if overlap > best_score:
                    best_score = overlap
                    best = paper

        if not best or best_score < 0.4:
            return None

        abstract = _clean_abstract(best.get("abstract") or "")
        if not abstract or len(abstract) < 50:
            return None

        doi = (best.get("externalIds") or {}).get("DOI")

        return {
            "abstract": abstract,
            "doi": doi,
            "year": best.get("year"),
            "source": "semantic_scholar",
        }

    except Exception:
        return None


def _search_crossref(doi: str) -> dict[str, Any] | None:
    """Busca abstract en CrossRef por DOI."""
    if not doi:
        return None
    try:
        r = requests.get(
            f"https://api.crossref.org/works/{doi}",
            headers={"User-Agent": "LCDA-Scholarer/1.0 (mailto:lcda@example.com)"},
            timeout=20,
        )
        if r.status_code != 200:
            return None

        data = r.json().get("message", {})
        abstract = _clean_abstract(data.get("abstract") or "")
        if not abstract or len(abstract) < 50:
            return None

        return {
            "abstract": abstract,
            "source": "crossref",
        }
    except Exception:
        return None


def enrich_paper_multisource(
    db: Database,
    paper: dict[str, Any],
) -> bool:
    """Enriquece un paper probando múltiples fuentes."""
    titulo = paper["titulo"]
    doi = paper.get("doi")

    # 1. Intentar Semantic Scholar
    result = _search_semantic_scholar(titulo)
    if result and result.get("abstract"):
        abstract = result["abstract"]
        # Solo actualizar si no tenemos abstract o si el nuevo es más largo
        existing = paper.get("abstract") or ""
        if existing and len(existing) >= len(abstract):
            return False

        db.upsert_paper(
            titulo=paper["titulo"],
            abstract=abstract,
            doi=result.get("doi") or doi,
            scholar_pub_id=paper.get("scholar_pub_id"),
            anio=result.get("year") or paper.get("anio"),
            citado_por=paper.get("citado_por") or 0,
            autores_texto=paper.get("autores_texto"),
            venue=paper.get("venue"),
            url_scholar=paper.get("url_scholar"),
        )
        return True

    # 2. Intentar CrossRef si tenemos DOI
    if doi:
        result = _search_crossref(doi)
        if result and result.get("abstract"):
            abstract = result["abstract"]
            existing = paper.get("abstract") or ""
            if existing and len(existing) >= len(abstract):
                return False

            db.upsert_paper(
                titulo=paper["titulo"],
                abstract=abstract,
                doi=doi,
                scholar_pub_id=paper.get("scholar_pub_id"),
                anio=paper.get("anio"),
                citado_por=paper.get("citado_por") or 0,
                autores_texto=paper.get("autores_texto"),
                venue=paper.get("venue"),
                url_scholar=paper.get("url_scholar"),
            )
            return True

    return False


def run_abstracts_multisource(
    db: Database,
    limit: int | None = None,
    batch_size: int = 10,
    batch_pause_sec: float = 5.0,
    delay_sec: float = 0.5,
) -> dict[str, Any]:
    """Pipeline de abstracts vía múltiples APIs gratuitas."""
    import time as _time

    t0 = _time.time()
    papers = db.get_papers_sin_abstract()
    if limit:
        papers = papers[:limit]
    total = len(papers)
    ok = 0
    fail = 0
    sources: dict[str, int] = {}

    print(f"      {total} papers sin abstract · fuente multisource (S2 + CrossRef)", flush=True)
    print(f"      delay entre papers: {delay_sec}s, lote cada {batch_size} ({batch_pause_sec}s)", flush=True)

    for i, paper in enumerate(papers, 1):
        titulo_corto = (paper["titulo"] or "")[:60]
        print(f"      [{i}/{total}] {titulo_corto}...", end="", flush=True)

        success = enrich_paper_multisource(db, paper)

        if success:
            ok += 1
            print(" ✓", flush=True)
        else:
            fail += 1
            print(" ✗", flush=True)

        time.sleep(delay_sec)

        if batch_size > 0 and i < total and i % batch_size == 0:
            print(f"      -- pausa de lote ({batch_pause_sec}s) --", flush=True)
            time.sleep(batch_pause_sec)

    dur = _time.time() - t0
    db.log_metrica("abstracts_multisource", dur, f"{ok}/{total} con abstract vía S2+CrossRef")
    return {
        "pendientes": total,
        "enriquecidos": ok,
        "sin_match": fail,
        "duracion_seg": dur,
    }
