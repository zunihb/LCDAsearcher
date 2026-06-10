"""Enriquecimiento de papers: abstract, DOI, URLs y autores (OpenAlex / Scholar)."""

from __future__ import annotations

import random
import re
import time
from difflib import SequenceMatcher
from typing import Any

import requests

from src.db import Database


def _reconstruct_openalex_abstract(inverted: dict[str, list[int]] | None) -> str:
    if not inverted:
        return ""
    max_pos = max(max(positions) for positions in inverted.values())
    words = [""] * (max_pos + 1)
    for word, positions in inverted.items():
        for p in positions:
            words[p] = word
    return " ".join(w for w in words if w).strip()


def _normalize_title(title: str) -> str:
    title = title.lower().strip()
    title = re.sub(r"[^a-z0-9áéíóúñü]+", " ", title)
    return re.sub(r"\s+", " ", title).strip()


def _title_score(query_title: str, candidate_title: str) -> float:
    q = _normalize_title(query_title)
    c = _normalize_title(candidate_title)
    if not q or not c:
        return 0.0
    if q == c:
        return 1.0
    # La inclusión permite subtítulos o variantes menores de capitalización.
    if len(q) > 18 and (q in c or c in q):
        return 0.96
    return SequenceMatcher(None, q, c).ratio()


def _is_plausible_match(
    query_title: str,
    work: dict[str, Any],
    query_year: int | None = None,
    min_score: float = 0.88,
) -> bool:
    score = _title_score(query_title, work.get("title") or "")
    if score >= 0.96:
        return True
    if score < min_score:
        return False
    if query_year is None:
        return True
    work_year = work.get("publication_year")
    if not isinstance(work_year, int):
        return True
    return abs(work_year - query_year) <= 1


def _openalex_search_work(
    titulo: str,
    mailto: str,
    anio: int | None = None,
) -> dict[str, Any] | None:
    """Busca el work en OpenAlex sin aceptar a ciegas el primer resultado.

    Antes se retornaba results[0] cuando no había match claro; eso podía asignar
    DOI, autores o abstracts de otro paper. Ahora se ordena por similitud de
    título y se exige confianza mínima, usando año como segunda señal cuando está.
    """
    try:
        r = requests.get(
            "https://api.openalex.org/works",
            params={"search": titulo, "per_page": 5, "mailto": mailto},
            timeout=25,
        )
        r.raise_for_status()
        results = r.json().get("results", [])
        if not results:
            return None
        ranked = sorted(
            results,
            key=lambda w: _title_score(titulo, w.get("title") or ""),
            reverse=True,
        )
        best = ranked[0]
        if _is_plausible_match(titulo, best, anio):
            return best
        return None
    except Exception:
        return None


def _extract_authors_openalex(work: dict[str, Any]) -> list[dict[str, Any]]:
    authors: list[dict[str, Any]] = []
    for i, auth in enumerate(work.get("authorships", []) or []):
        author = auth.get("author") or {}
        nombre = author.get("display_name") or ""
        if not nombre:
            continue
        afils = auth.get("institutions") or []
        afiliacion = ", ".join(
            a.get("display_name", "") for a in afils if a.get("display_name")
        ) or None
        authors.append({
            "nombre": nombre,
            "afiliacion": afiliacion,
            "orden": i,
            "openalex_author_id": (author.get("id") or "").split("/")[-1] or None,
        })
    return authors


def _ieee_url_from_work(work: dict[str, Any], doi: str | None) -> str | None:
    for loc in work.get("locations") or []:
        src = (loc.get("source") or {})
        org = (src.get("host_organization_name") or "").upper()
        name = (src.get("display_name") or "").upper()
        if "IEEE" in org or "IEEE" in name:
            return loc.get("landing_page_url") or loc.get("pdf_url")
    if doi and doi.startswith("10.1109"):
        return f"https://doi.org/{doi}"
    return None


def _work_to_metadata(work: dict[str, Any]) -> dict[str, Any]:
    oa = work.get("open_access") or {}
    doi = work.get("doi") or ""
    if doi.startswith("https://doi.org/"):
        doi = doi.replace("https://doi.org/", "")
    primary = work.get("primary_location") or {}
    url_pdf = oa.get("oa_url") or primary.get("pdf_url")
    return {
        "abstract": _reconstruct_openalex_abstract(work.get("abstract_inverted_index")),
        "doi": doi or None,
        "url_doi": f"https://doi.org/{doi}" if doi else None,
        "url_ieee": _ieee_url_from_work(work, doi or None),
        "openalex_id": (work.get("id") or "").split("/")[-1] or None,
        "url_pdf": url_pdf or None,
        "venue": (primary.get("source") or {}).get("display_name")
        or (work.get("host_venue") or {}).get("display_name"),
        "autores": _extract_authors_openalex(work),
    }


def enrich_paper_openalex(
    db: Database,
    paper: dict[str, Any],
    mailto: str,
) -> bool:
    work = _openalex_search_work(paper["titulo"], mailto, paper.get("anio"))
    if not work:
        return False
    meta = _work_to_metadata(work)
    if not meta["abstract"]:
        return False

    autores_texto = ", ".join(a["nombre"] for a in meta["autores"])
    paper_id = db.upsert_paper(
        titulo=paper["titulo"],
        abstract=meta["abstract"],
        venue=meta.get("venue") or paper.get("venue"),
        autores_texto=autores_texto or paper.get("autores_texto"),
        doi=meta.get("doi"),
        url_doi=meta.get("url_doi"),
        url_ieee=meta.get("url_ieee"),
        openalex_id=meta.get("openalex_id"),
        url_pdf=meta.get("url_pdf"),
        url_scholar=paper.get("url_scholar"),
        scholar_pub_id=paper.get("scholar_pub_id"),
        anio=paper.get("anio"),
        citado_por=paper.get("citado_por") or 0,
    )
    for a in meta["autores"]:
        db.upsert_paper_autor(
            paper_id=paper_id,
            nombre=a["nombre"],
            afiliacion=a.get("afiliacion"),
            orden=a.get("orden", 0),
            openalex_author_id=a.get("openalex_author_id"),
        )
    return True


def _scholar_fill_paper(titulo: str, use_proxies: bool = False) -> dict[str, Any] | None:
    try:
        if use_proxies:
            from scholarly import ProxyGenerator
            from scholarly import scholarly as sch

            pg = ProxyGenerator()
            pg.FreeProxies()
            sch.use_proxy(pg)
        else:
            from scholarly import scholarly as sch

        query = sch.search_pubs(titulo)
        pub = next(query, None)
        if not pub:
            return None
        filled = sch.fill(pub)
        bib = filled.get("bib", {}) or {}
        return {
            "abstract": bib.get("abstract") or "",
            "autores_texto": ", ".join(bib.get("author", []) or []),
            "url_scholar": filled.get("pub_url"),
            "url_pdf": filled.get("eprint_url"),
            "venue": bib.get("venue") or bib.get("journal") or "",
        }
    except Exception:
        return None


def enrich_paper_scholar(
    db: Database,
    paper: dict[str, Any],
    use_proxies: bool = False,
) -> bool:
    meta = _scholar_fill_paper(paper["titulo"], use_proxies)
    if not meta or not meta.get("abstract"):
        return False
    paper_id = db.upsert_paper(
        titulo=paper["titulo"],
        abstract=meta["abstract"],
        autores_texto=meta.get("autores_texto"),
        venue=meta.get("venue") or paper.get("venue"),
        url_scholar=meta.get("url_scholar") or paper.get("url_scholar"),
        url_pdf=meta.get("url_pdf") or paper.get("url_pdf"),
        scholar_pub_id=paper.get("scholar_pub_id"),
        anio=paper.get("anio"),
        citado_por=paper.get("citado_por") or 0,
    )
    if meta.get("autores_texto"):
        for i, nombre in enumerate(n.strip() for n in meta["autores_texto"].split(",") if n.strip()):
            db.upsert_paper_autor(paper_id=paper_id, nombre=nombre, orden=i)
    return True


def run_abstracts(
    db: Database,
    mailto: str = "lcda@example.com",
    source: str = "openalex",
    pause_sec: float = 0.35,
    scholar_pause_min: float = 2,
    scholar_pause_max: float = 4,
    use_proxies: bool = False,
) -> dict[str, Any]:
    t0 = time.time()
    papers = db.get_papers_sin_abstract()
    total = len(papers)
    ok = 0
    fail = 0

    print(f"      {total} papers sin abstract · fuente {source}", flush=True)

    for i, paper in enumerate(papers, 1):
        success = False
        if source == "scholarly":
            success = enrich_paper_scholar(db, paper, use_proxies)
            time.sleep(random.uniform(scholar_pause_min, scholar_pause_max))
        else:
            success = enrich_paper_openalex(db, paper, mailto)
            time.sleep(pause_sec)

        if success:
            ok += 1
        else:
            fail += 1

        if i % 10 == 0 or i == total:
            print(f"      [{i}/{total}] ok:{ok} sin_match:{fail}", flush=True)

    dur = time.time() - t0
    db.log_metrica("abstracts", dur, f"{ok}/{total} con abstract, fuente={source}")
    return {
        "pendientes": total,
        "enriquecidos": ok,
        "sin_match": fail,
        "duracion_seg": dur,
    }
