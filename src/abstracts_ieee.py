"""Enriquecimiento de abstracts vía IEEE Xplore search API.

Busca papers por título en IEEE Xplore y extrae el abstract del resultado.
No requiere autenticación — usa el endpoint público de búsqueda.
"""

from __future__ import annotations

import re
import time
import requests
from typing import Any

from src.db import Database


def _clean_ieee_abstract(text: str) -> str:
    """Limpia abstract de IEEE Xplore (quita highlight markers)."""
    if not text:
        return ""
    # Quitar markers [::keyword::]
    text = re.sub(r"\[::(.*?)::\]", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _normalize_for_match(text: str) -> str:
    """Normaliza título para comparación."""
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9áéíóúñü\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    # Singularizar palabras comunes
    text = re.sub(r"applications\b", "application", text)
    text = re.sub(r"converters\b", "converter", text)
    text = re.sub(r"inverters\b", "inverter", text)
    text = re.sub(r"systems\b", "system", text)
    text = re.sub(r"controls\b", "control", text)
    text = re.sub(r"drives\b", "drive", text)
    text = re.sub(r"techniques\b", "technique", text)
    text = re.sub(r"methods\b", "method", text)
    return text


def _title_match_score(t1: str, t2: str) -> float:
    """Calcula score de match entre títulos con sinónimos."""
    n1 = _normalize_for_match(t1)
    n2 = _normalize_for_match(t2)

    words1 = set(n1.split())
    words2 = set(n2.split())

    # Stopwords que no aportan al match
    stop = {"an", "the", "of", "for", "and", "or", "in", "on", "a", "to", "with", "by"}
    words1 -= stop
    words2 -= stop

    if not words1 or not words2:
        return 0.0

    overlap = len(words1 & words2) / max(len(words1), len(words2))

    # Bonus si una título contiene al otro
    if n1 in n2 or n2 in n1:
        overlap = max(overlap, 0.8)

    return overlap


def _search_ieee_xplore(titulo: str) -> dict[str, Any] | None:
    """Busca un paper en IEEE Xplore por título."""
    try:
        r = requests.post(
            "https://ieeexplore.ieee.org/rest/search",
            json={
                "queryText": titulo,
                "highlight": True,
                "returnFacets": ["ALL"],
                "returnType": "SEARCH",
                "rowsPerPage": 5,
            },
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                "Content-Type": "application/json",
                "Referer": "https://ieeexplore.ieee.org/search/searchresult.jsp",
                "Origin": "https://ieeexplore.ieee.org",
            },
            timeout=25,
        )
        if r.status_code != 200:
            return None

        records = r.json().get("records", [])
        if not records:
            return None

        # Buscar el mejor match por título
        best = None
        best_score = 0

        for rec in records:
            rec_title = rec.get("articleTitle", "")
            if not rec_title:
                continue

            score = _title_match_score(titulo, rec_title)
            if score > best_score:
                best_score = score
                best = rec

        if not best or best_score < 0.25:
            return None

        abstract = _clean_ieee_abstract(best.get("abstract", ""))
        if not abstract or len(abstract) < 50:
            return None

        return {
            "abstract": abstract,
            "doi": best.get("doi"),
            "article_title": best.get("articleTitle", ""),
        }

    except Exception:
        return None


def enrich_paper_ieee(
    db: Database,
    paper: dict[str, Any],
) -> bool:
    """ Enriquece un paper con abstract de IEEE Xplore. """
    result = _search_ieee_xplore(paper["titulo"])
    if not result:
        return False

    abstract = result["abstract"]
    if not abstract:
        return False

    # Solo actualizar si no tenemos abstract o si el nuevo es más largo
    existing = paper.get("abstract") or ""
    if existing and len(existing) >= len(abstract):
        return False

    doi = result.get("doi") or paper.get("doi")
    url_doi = f"https://doi.org/{doi}" if doi else paper.get("url_doi")
    url_ieee = f"https://doi.org/{doi}" if doi and doi.startswith("10.1109") else paper.get("url_ieee")

    db.upsert_paper(
        titulo=paper["titulo"],
        abstract=abstract,
        doi=doi,
        url_doi=url_doi,
        url_ieee=url_ieee,
        scholar_pub_id=paper.get("scholar_pub_id"),
        anio=paper.get("anio"),
        citado_por=paper.get("citado_por") or 0,
        autores_texto=paper.get("autores_texto"),
        venue=paper.get("venue"),
        url_scholar=paper.get("url_scholar"),
    )
    return True


def run_abstracts_ieee(
    db: Database,
    limit: int | None = None,
    batch_size: int = 10,
    batch_pause_sec: float = 5.0,
    delay_sec: float = 1.0,
) -> dict[str, Any]:
    """Pipeline de abstracts vía IEEE Xplore search API."""
    import time as _time

    t0 = _time.time()
    papers = db.get_papers_sin_abstract()
    if limit:
        papers = papers[:limit]
    total = len(papers)
    ok = 0
    fail = 0

    print(f"      {total} papers sin abstract · fuente ieee-xplore", flush=True)
    print(f"      delay entre papers: {delay_sec}s, lote cada {batch_size} ({batch_pause_sec}s)", flush=True)

    for i, paper in enumerate(papers, 1):
        titulo_corto = (paper["titulo"] or "")[:60]
        print(f"      [{i}/{total}] {titulo_corto}...", end="", flush=True)

        success = enrich_paper_ieee(db, paper)

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
    db.log_metrica("abstracts_ieee", dur, f"{ok}/{total} con abstract vía IEEE Xplore")
    return {
        "pendientes": total,
        "enriquecidos": ok,
        "sin_match": fail,
        "duracion_seg": dur,
    }
