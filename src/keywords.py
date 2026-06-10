"""Extracción y normalización de keywords con LLM (OpenAI-compatible)."""

from __future__ import annotations

import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

from src.db import Database

load_dotenv()

LLM_TIMEOUT = 60
MAX_KEYWORD_LEN = 45

_GARBAGE_PATTERNS = re.compile(
    r"|".join(
        [
            r"user wants",
            r"i need to",
            r"key concepts?",
            r"^title\s*:",
            r"^abstract\s*:",
            r"paper topic",
            r"let'?s identify",
            r"respond only",
            r"json array",
            r"the paper is about",
            r"topic analysis",
            r"no additional text",
            r"without any additional",
            r"must be in spanish",
            r"keywords must",
            r"keywords should",
            r"extract.*keywords",
            r"^here are",
            r"^the keywords",
            r"reasoning",
            r"step by step",
            r"traduce los",
            r"let'?s use",
            r"so yes",
            r"for keywords",
        ]
    ),
    re.IGNORECASE,
)


def _get_client() -> OpenAI | None:
    api_key = os.getenv("LLM_API_KEY", "")
    if not api_key or api_key.startswith("sk-..."):
        return None
    return OpenAI(
        api_key=api_key,
        base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"),
        timeout=LLM_TIMEOUT,
    )


def _is_valid_keyword(kw: str, titulo: str = "") -> bool:
    kw = kw.strip().strip('"\'')
    if not kw or len(kw) < 3 or len(kw) > MAX_KEYWORD_LEN:
        return False
    if titulo and kw.lower() in titulo.lower():
        return False
    if _GARBAGE_PATTERNS.search(kw):
        return False
    if kw.count(" ") > 5:
        return False
    if "->" in kw or "→" in kw:
        return False
    if re.fullmatch(r"(kw|keyword)\d+", kw, re.IGNORECASE):
        return False
    words = kw.split()
    if len(words) == 1:
        # Evita fragmentos sueltos del título en inglés ("Current", "Under", "Flow")
        if re.fullmatch(r"[A-Za-z]+", kw) and len(kw) < 12 and not kw.isupper():
            return False
    return True


def _extract_numbered_keywords(text: str, titulo: str = "") -> list[str]:
    """Fallback: listas numeradas del razonamiento del modelo."""
    items: list[str] = []
    for match in re.finditer(r"(?:^|\n)\s*\d+[.)\-:]\s*([^\n\[]]{3,50})", text):
        kw = match.group(1).strip().strip('"\'`,;.')
        if _is_valid_keyword(kw, titulo):
            items.append(kw)
    return items


def _keywords_json_schema(n: int) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "paper_keywords",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": n,
                        "maxItems": n,
                    }
                },
                "required": ["keywords"],
                "additionalProperties": False,
            },
        },
    }


def _parse_llm_keywords_content(content: str, n: int) -> list[str]:
    """Parsea respuesta JSON nativa o texto legacy."""
    if not content or not content.strip():
        return []
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            for key in ("keywords", "palabras_clave", "palabras_clave_tecnicas"):
                val = data.get(key)
                if isinstance(val, list):
                    return [str(x).strip() for x in val if str(x).strip()]
        if isinstance(data, list):
            return [str(x).strip() for x in data if isinstance(x, str) and str(x).strip()]
    except json.JSONDecodeError:
        pass
    return _extract_json_array(text, n)


def _extract_json_array(text: str, n: int = 5) -> list[str]:
    """Extrae arrays JSON embebidos en razonamiento largo (modelos 'thinking')."""
    if not text or not text.strip():
        return []

    candidates: list[list[str]] = []
    for i, ch in enumerate(text):
        if ch != "[":
            continue
        depth = 0
        for j in range(i, len(text)):
            if text[j] == "[":
                depth += 1
            elif text[j] == "]":
                depth -= 1
                if depth == 0:
                    chunk = text[i : j + 1]
                    try:
                        data = json.loads(chunk)
                    except json.JSONDecodeError:
                        break
                    if isinstance(data, list) and data and all(isinstance(x, str) for x in data):
                        parsed = [str(x).strip() for x in data if str(x).strip()]
                        if parsed:
                            candidates.append(parsed)
                    break

    if not candidates:
        return []

    def score(arr: list[str]) -> tuple[int, int]:
        return (abs(len(arr) - n), -len(arr))

    return min(candidates, key=score)


def _sanitize_keywords(
    raw: list[str],
    titulo: str,
    abstract: str,
    n: int,
) -> list[str]:
    seen: set[str] = set()
    clean: list[str] = []
    for kw in raw:
        kw = re.sub(r"^\d+[\.\)]\s*", "", kw.strip())
        if not _is_valid_keyword(kw, titulo):
            continue
        key = kw.lower()
        if key in seen:
            continue
        seen.add(key)
        clean.append(kw)
        if len(clean) >= n:
            break

    if len(clean) < n:
        for kw in _fallback_keywords(titulo, abstract, n):
            key = kw.lower()
            if key not in seen:
                seen.add(key)
                clean.append(kw)
            if len(clean) >= n:
                break
    return clean[:n]


def extract_keywords_llm(
    client: OpenAI,
    titulo: str,
    abstract: str,
    n: int = 5,
    idioma: str = "es",
    json_mode: str | None = None,
) -> list[str]:
    model = os.getenv("LLM_MODEL", "mimo-v2.5-pro")
    mode = json_mode or os.getenv("LLM_JSON_MODE", "json_schema")
    resumen = abstract.strip() if abstract and abstract.strip() else None
    cuerpo = f"Título: {titulo}"
    if resumen:
        cuerpo += f"\nResumen: {resumen}"
    else:
        cuerpo += "\n(Sin resumen; infiere palabras clave solo del título.)"

    system = (
        "Eres un extractor de palabras clave bibliográficas. "
        f"Responde en json con exactamente {n} strings en {idioma}. "
        'Formato: {"keywords": ["kw1", "kw2", ...]}. '
        "Sin explicaciones ni razonamiento. Cada keyword: 1-4 palabras, término técnico conciso."
    )
    user = (
        f"Extrae {n} palabras clave técnicas en {idioma} para este artículo "
        "de ingeniería eléctrica / electrónica de potencia.\n"
        "Si el título está en inglés, traduce los términos al español.\n\n"
        f"{cuerpo}"
    )
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]

    modes_to_try: list[str | None] = []
    if mode and mode != "text":
        modes_to_try.append(mode)
        if mode == "json_schema":
            modes_to_try.append("json_object")
    modes_to_try.append(None)

    last_content = ""
    for fmt in modes_to_try:
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": max(400, n * 40) if fmt else 1024,
            "timeout": LLM_TIMEOUT,
        }
        if fmt == "json_schema":
            kwargs["response_format"] = _keywords_json_schema(n)
        elif fmt == "json_object":
            kwargs["response_format"] = {"type": "json_object"}

        try:
            resp = client.chat.completions.create(**kwargs)
            content = resp.choices[0].message.content or ""
            last_content = content
            raw = _parse_llm_keywords_content(content, n)
            if fmt and len(raw) >= min(3, n):
                return _sanitize_keywords(raw, titulo, abstract, n)
            if not fmt:
                if len(raw) < n:
                    extra = _extract_numbered_keywords(content, titulo)
                    raw = raw + [k for k in extra if k not in raw]
                return _sanitize_keywords(raw, titulo, abstract, n)
        except Exception:
            continue

    raw = _parse_llm_keywords_content(last_content, n)
    return _sanitize_keywords(raw, titulo, abstract, n)


def normalize_keywords_llm(
    client: OpenAI,
    keywords: list[str],
    idioma: str = "es",
) -> dict[str, str]:
    keywords = [k for k in keywords if _is_valid_keyword(k)]
    if not keywords:
        return {}

    model = os.getenv("LLM_MODEL", "kimi-k2.5")
    batch = keywords[:80]
    prompt = f"""Estandariza estas palabras clave en términos canónicos cortos en {idioma}.
Responde ÚNICAMENTE JSON object, sin texto adicional: {{"original": "canonico", ...}}

{batch}"""

    resp = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": "Responde solo JSON object. Sin explicaciones.",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0,
        max_tokens=2000,
        timeout=LLM_TIMEOUT,
    )
    content = resp.choices[0].message.content or ""
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if match:
        try:
            mapping = json.loads(match.group())
            if isinstance(mapping, dict):
                return {
                    str(k): str(v)
                    for k, v in mapping.items()
                    if _is_valid_keyword(str(v))
                }
        except json.JSONDecodeError:
            pass
    return {k: k for k in keywords}


def _fallback_keywords(titulo: str, abstract: str, n: int = 5) -> list[str]:
    text = f"{titulo} {abstract or ''}".lower()
    stop = {
        "the", "a", "an", "of", "in", "for", "and", "or", "with", "using", "based",
        "de", "la", "el", "en", "y", "con", "para", "del", "los", "las", "una", "por",
        "study", "analysis", "design", "control", "new", "approach", "system",
    }
    phrases = re.findall(
        r"[a-záéíóúñ]{3,}(?:\s+[a-záéíóúñ]{3,}){0,2}",
        text,
    )
    freq: dict[str, int] = {}
    for phrase in phrases:
        words = phrase.split()
        if any(w in stop for w in words):
            continue
        if len(phrase) > MAX_KEYWORD_LEN:
            continue
        freq[phrase] = freq.get(phrase, 0) + len(words)

    if not freq:
        words = re.findall(r"[a-záéíóúñ]{4,}", text)
        for w in words:
            if w not in stop:
                freq[w] = freq.get(w, 0) + 1

    sorted_phrases = sorted(freq, key=freq.get, reverse=True)
    result: list[str] = []
    for p in sorted_phrases:
        if len(p.split()) < 2:
            continue
        if _is_valid_keyword(p, titulo):
            result.append(p)
        if len(result) >= n:
            break
    if len(result) < n:
        for p in sorted_phrases:
            if len(p.split()) != 1:
                continue
            candidate = p if re.search(r"[áéíóúñ]", p) else p.title()
            if _is_valid_keyword(candidate, titulo):
                result.append(candidate)
            if len(result) >= n:
                break
    return result[:n] or [titulo[:MAX_KEYWORD_LEN]]


def _keywords_for_paper(
    paper: dict[str, Any],
    client: OpenAI | None,
    por_paper: int,
    idioma: str,
    json_mode: str = "json_schema",
) -> tuple[list[str], str]:
    titulo = paper["titulo"]
    abstract = paper.get("abstract") or ""
    kws: list[str] = []
    source = "fallback"
    if client:
        try:
            kws = extract_keywords_llm(client, titulo, abstract, por_paper, idioma, json_mode)
            if kws and all(_is_valid_keyword(k, titulo) for k in kws):
                source = "llm"
            elif kws:
                source = "llm+fill"
            else:
                kws = []
        except Exception:
            kws = []
    if not kws:
        kws = _fallback_keywords(titulo, abstract, por_paper)
        source = "fallback"
    return kws[:por_paper], source


def _process_and_save(
    paper: dict[str, Any],
    db: Database,
    por_paper: int,
    idioma: str,
    use_llm: bool,
    json_mode: str = "json_schema",
) -> dict[str, Any]:
    client = _get_client() if use_llm else None
    kws, source = _keywords_for_paper(paper, client, por_paper, idioma, json_mode)
    db.save_paper_keywords(paper["id"], kws)
    return {
        "paper_id": paper["id"],
        "titulo": paper["titulo"][:60],
        "keywords": kws,
        "source": source,
    }


def _print_progress(
    done: int,
    total: int,
    t0: float,
    llm_ok: int,
    fallback_count: int,
    last: dict[str, Any] | None,
    parallel: bool,
) -> None:
    elapsed = time.time() - t0
    rate = done / elapsed if elapsed > 0 else 0
    eta = (total - done) / rate if rate > 0 else 0
    mode = f"{parallel} workers" if parallel > 1 else "1 a 1"
    line = (
        f"      [{done}/{total}] {elapsed:.0f}s · {mode} · "
        f"LLM:{llm_ok} fallback:{fallback_count} · ETA ~{eta/60:.0f} min"
    )
    if last and parallel <= 1:
        preview = ", ".join(last["keywords"][:3])
        line += f"\n      ✓ guardado paper {last['paper_id']}: {preview}"
    print(line, flush=True)


def run_keywords(
    db: Database,
    por_paper: int = 5,
    parallel_workers: int = 1,
    progress_every: int = 1,
    idioma: str = "es",
    json_mode: str = "json_schema",
) -> dict[str, Any]:
    t0 = time.time()
    client = _get_client()
    papers = db.get_papers_sin_keywords()
    total = len(papers)
    all_terms: list[str] = []
    llm_ok = 0
    fallback_count = 0
    done = 0
    progress_lock = threading.Lock()

    if total == 0:
        print("      Sin papers pendientes.", flush=True)
        return {
            "papers_procesados": 0,
            "keywords_unicas": 0,
            "llm_usado": client is not None,
            "llm_ok": 0,
            "fallback": 0,
            "duracion_seg": 0.0,
        }

    workers = max(1, parallel_workers)
    mode = f"paralelo ({workers} workers)" if workers > 1 else "secuencial (guardado 1 a 1)"
    print(f"      {total} papers pendientes · modo {mode} · json={json_mode}", flush=True)

    def _on_result(result: dict[str, Any]) -> None:
        nonlocal done, llm_ok, fallback_count
        with progress_lock:
            done += 1
            if result["source"] == "fallback":
                fallback_count += 1
            else:
                llm_ok += 1
            all_terms.extend(result["keywords"])
            if done % progress_every == 0 or done == total:
                _print_progress(
                    done, total, t0, llm_ok, fallback_count, result, workers
                )

    if workers == 1:
        for paper in papers:
            result = _process_and_save(
                paper, db, por_paper, idioma, client is not None, json_mode
            )
            _on_result(result)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(
                    _process_and_save,
                    paper, db, por_paper, idioma, client is not None, json_mode,
                )
                for paper in papers
            ]
            for fut in as_completed(futures):
                _on_result(fut.result())

    unique_terms = list({k for k in set(all_terms) if _is_valid_keyword(k)})
    if client and unique_terms:
        print(f"      Normalizando {len(unique_terms)} keywords...", flush=True)
        try:
            mapping = normalize_keywords_llm(client, unique_terms, idioma)
            for term, canon in mapping.items():
                if not _is_valid_keyword(canon):
                    continue
                row = db.query_one("SELECT id FROM keywords WHERE termino = ?", (term,))
                if row:
                    db.update_keyword_canonical(row["id"], canon)
        except Exception as e:
            print(f"      [!] Normalización omitida: {e}", flush=True)

    dur = time.time() - t0
    db.log_metrica(
        "keywords", dur,
        f"{done} papers, {len(unique_terms)} kw, llm={llm_ok}, fallback={fallback_count}, workers={workers}",
    )
    return {
        "papers_procesados": done,
        "keywords_unicas": len(unique_terms),
        "llm_usado": client is not None,
        "llm_ok": llm_ok,
        "fallback": fallback_count,
        "parallel_workers": workers,
        "duracion_seg": dur,
    }
