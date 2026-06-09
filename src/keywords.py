"""Extracción y normalización de keywords con LLM (OpenAI-compatible)."""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

from src.db import Database

load_dotenv()


def _get_client() -> OpenAI | None:
    api_key = os.getenv("LLM_API_KEY", "")
    if not api_key or api_key.startswith("sk-..."):
        return None
    return OpenAI(
        api_key=api_key,
        base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"),
    )


def _parse_json_list(text: str) -> list[str]:
    text = text.strip()
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return [line.strip("- •\"'") for line in text.splitlines() if line.strip()]


def extract_keywords_llm(
    client: OpenAI,
    titulo: str,
    abstract: str,
    n: int = 5,
    idioma: str = "es",
) -> list[str]:
    model = os.getenv("LLM_MODEL", "gpt-4o-mini")
    prompt = f"""Extrae las {n} palabras clave más representativas de este paper de investigación.
Responde SOLO con un JSON array de strings en {idioma}, sin explicación.

Título: {titulo}
Abstract: {abstract or '(sin abstract)'}"""

    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
    )
    content = resp.choices[0].message.content or "[]"
    kws = _parse_json_list(content)
    return [k for k in kws if k][:n]


def normalize_keywords_llm(
    client: OpenAI,
    keywords: list[str],
    idioma: str = "es",
) -> dict[str, str]:
    """Segunda pasada: unifica sinónimos -> término canónico."""
    if not keywords:
        return {}

    model = os.getenv("LLM_MODEL", "gpt-4o-mini")
    prompt = f"""Estandariza estas palabras clave de investigación en términos canónicos en {idioma}.
Agrupa sinónimos (ej. "PLL" y "phase-locked loop" -> "Sincronización PLL").
Responde SOLO con un JSON object: {{"termino_original": "termino_canonico", ...}}

Keywords:
{json.dumps(keywords, ensure_ascii=False)}"""

    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
    )
    content = resp.choices[0].message.content or "{}"
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return {k: k for k in keywords}


def _fallback_keywords(titulo: str, abstract: str, n: int = 5) -> list[str]:
    """Sin LLM: extrae tokens frecuentes del título/abstract."""
    text = f"{titulo} {abstract}".lower()
    stop = {"the", "a", "an", "of", "in", "for", "and", "or", "de", "la", "el", "en", "y", "con"}
    words = re.findall(r"[a-záéíóúñ]{4,}", text)
    freq: dict[str, int] = {}
    for w in words:
        if w not in stop:
            freq[w] = freq.get(w, 0) + 1
    sorted_w = sorted(freq, key=freq.get, reverse=True)
    return [w.title() for w in sorted_w[:n]] or [titulo[:40]]


def run_keywords(
    db: Database,
    por_paper: int = 5,
    batch_size: int = 10,
    idioma: str = "es",
) -> dict[str, Any]:
    t0 = time.time()
    client = _get_client()
    papers = db.get_papers_sin_keywords()
    all_terms: list[str] = []
    processed = 0

    for paper in papers:
        if client:
            try:
                kws = extract_keywords_llm(
                    client, paper["titulo"], paper.get("abstract") or "", por_paper, idioma
                )
            except Exception:
                kws = _fallback_keywords(paper["titulo"], paper.get("abstract") or "", por_paper)
        else:
            kws = _fallback_keywords(paper["titulo"], paper.get("abstract") or "", por_paper)

        for kw in kws:
            kid = db.upsert_keyword(kw, kw)
            db.link_paper_keyword(paper["id"], kid)
            all_terms.append(kw)
        processed += 1

    unique_terms = list(set(all_terms))
    if client and unique_terms:
        try:
            mapping = normalize_keywords_llm(client, unique_terms, idioma)
            for term, canon in mapping.items():
                row = db.query_one("SELECT id FROM keywords WHERE termino = ?", (term,))
                if row:
                    db.update_keyword_canonical(row["id"], canon)
        except Exception:
            pass

    dur = time.time() - t0
    db.log_metrica("keywords", dur, f"{processed} papers, {len(unique_terms)} keywords únicas")
    return {
        "papers_procesados": processed,
        "keywords_unicas": len(unique_terms),
        "llm_usado": client is not None,
        "duracion_seg": dur,
    }
