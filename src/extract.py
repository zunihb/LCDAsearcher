"""Extracción de perfiles y publicaciones desde Google Scholar."""

from __future__ import annotations

import json
import random
import time
from pathlib import Path
from typing import Any

from src.db import Database


def _pause(min_sec: float, max_sec: float) -> None:
    time.sleep(random.uniform(min_sec, max_sec))


def _setup_scholarly(use_proxies: bool) -> None:
    if use_proxies:
        from scholarly import ProxyGenerator

        pg = ProxyGenerator()
        pg.FreeProxies()
        from scholarly import scholarly

        scholarly.use_proxy(pg)


def _safe_int(val: Any, default: int = 0) -> int:
    try:
        return int(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def _pub_to_dict(pub: dict) -> dict[str, Any]:
    bib = pub.get("bib", {}) or {}
    return {
        "scholar_pub_id": pub.get("pub_url") or pub.get("eprint_url") or None,
        "titulo": bib.get("title") or pub.get("title") or "Sin título",
        "abstract": bib.get("abstract") or "",
        "anio": _safe_int(bib.get("pub_year")),
        "venue": bib.get("venue") or bib.get("journal") or "",
        "citado_por": _safe_int(pub.get("num_citations")),
        "autores_texto": ", ".join(bib.get("author", []) or []),
    }


def _profile_to_dict(author: dict, filled: dict | None = None) -> dict[str, Any]:
    filled = filled or author
    name = author.get("name", "")
    affil = ""
    if author.get("affiliation"):
        affil = author["affiliation"]
    elif filled.get("affiliation"):
        affil = filled["affiliation"]

    coautores = []
    for c in filled.get("coauthors", []) or []:
        coautores.append(
            {
                "nombre": c.get("name", ""),
                "afiliacion": c.get("affiliation", ""),
                "coautor_scholar_id": c.get("user_id") or c.get("scholar_id"),
            }
        )

    pubs = []
    for pub in filled.get("publications", []) or []:
        try:
            from scholarly import scholarly

            filled_pub = scholarly.fill(pub)
            _pause(0.5, 1.5)
            pubs.append(_pub_to_dict(filled_pub))
        except Exception:
            pubs.append(_pub_to_dict(pub))

    citedby = filled.get("citedby5y") or filled.get("citedby") or {}
    return {
        "scholar_id": author.get("scholar_id") or author.get("user_id"),
        "nombre": name,
        "afiliacion": affil,
        "citas_total": _safe_int(citedby.get("table") and sum(citedby.get("citations", [])) or filled.get("citedby")),
        "indice_h": _safe_int(filled.get("hindex")),
        "indice_i10": _safe_int(filled.get("i10index")),
        "coautores": coautores,
        "publications": pubs,
    }


def fetch_profile(
    scholar_id: str,
    nombre: str,
    afiliacion: str,
    raw_dir: Path,
    use_proxies: bool = False,
    pause_min: float = 2,
    pause_max: float = 5,
    force_refresh: bool = False,
) -> dict[str, Any]:
    cache_path = raw_dir / f"{scholar_id}.json"
    if cache_path.exists() and not force_refresh:
        return json.loads(cache_path.read_text(encoding="utf-8"))

    _setup_scholarly(use_proxies)
    from scholarly import scholarly

    _pause(pause_min, pause_max)
    author = scholarly.search_author_id(scholar_id)
    _pause(pause_min, pause_max)
    filled = scholarly.fill(author, sections=["basics", "indices", "counts", "coauthors", "publications"])

    data = _profile_to_dict(author, filled)
    data["nombre"] = data.get("nombre") or nombre
    data["afiliacion"] = data.get("afiliacion") or afiliacion
    data["scholar_id"] = scholar_id

    raw_dir.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def persist_profile(db: Database, profile: dict[str, Any]) -> int:
    scholar_id = profile["scholar_id"]
    db.upsert_investigador(
        scholar_id=scholar_id,
        nombre=profile["nombre"],
        afiliacion=profile.get("afiliacion"),
        citas_total=profile.get("citas_total", 0),
        indice_h=profile.get("indice_h", 0),
        indice_i10=profile.get("indice_i10", 0),
    )

    for co in profile.get("coautores", []):
        if co.get("nombre"):
            db.upsert_coautor(
                scholar_id=scholar_id,
                nombre=co["nombre"],
                afiliacion=co.get("afiliacion"),
                coautor_scholar_id=co.get("coautor_scholar_id"),
            )

    count = 0
    for pub in profile.get("publications", []):
        paper_id = db.upsert_paper(
            titulo=pub["titulo"],
            scholar_pub_id=pub.get("scholar_pub_id"),
            abstract=pub.get("abstract"),
            anio=pub.get("anio") or None,
            venue=pub.get("venue"),
            citado_por=pub.get("citado_por", 0),
            autores_texto=pub.get("autores_texto"),
        )
        db.add_autoria(scholar_id, paper_id)
        count += 1
    return count


def run_extract(
    db: Database,
    investigadores: list[dict],
    raw_dir: Path,
    scholarly_cfg: dict,
) -> dict[str, Any]:
    t0 = time.time()
    total_papers = 0
    for inv in investigadores:
        profile = fetch_profile(
            scholar_id=inv["scholar_id"],
            nombre=inv["nombre"],
            afiliacion=inv.get("afiliacion", ""),
            raw_dir=raw_dir,
            use_proxies=scholarly_cfg.get("use_proxies", False),
            pause_min=scholarly_cfg.get("pause_min_sec", 2),
            pause_max=scholarly_cfg.get("pause_max_sec", 5),
        )
        total_papers += persist_profile(db, profile)

    dur = time.time() - t0
    db.log_metrica("extract", dur, f"{len(investigadores)} investigadores, {total_papers} papers")
    return {"investigadores": len(investigadores), "papers": total_papers, "duracion_seg": dur}
