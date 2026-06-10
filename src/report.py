"""Reporte de matches temáticos, tendencias y métricas."""

from __future__ import annotations

import csv
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from src.db import Database
from src.matching import get_matches_investigadores, write_matching_outputs

load_dotenv()


def _get_client() -> OpenAI | None:
    api_key = os.getenv("LLM_API_KEY", "")
    if not api_key or api_key.startswith("sk-..."):
        return None
    return OpenAI(
        api_key=api_key,
        base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"),
    )


def write_sinergias_csv(db: Database, output_dir: Path) -> Path:
    """Exporta sinergias como matches temáticos entre investigadores.

    Mantiene el nombre ``sinergias.csv`` por compatibilidad con el pipeline,
    pero ahora mide coincidencia investigador-keyword-investigador incluso si
    los investigadores no han coautorado el mismo paper.
    """

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "sinergias.csv"
    matches = get_matches_investigadores(db)

    with path.open("w", newline="", encoding="utf-8") as f:
        fieldnames = [
            "keyword",
            "investigador_1",
            "papers_inv1",
            "investigador_2",
            "papers_inv2",
            "score",
            "potencial",
            "ultimo_anio",
            "evidencia_inv1",
            "evidencia_inv2",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for match in matches:
            writer.writerow({key: match.get(key) for key in fieldnames})
    return path


def _generar_resumen_llm(
    client: OpenAI,
    invs: list[dict],
    matches: list[dict],
    trends: list[dict] | None,
) -> str:
    model = os.getenv("LLM_MODEL", "mimo-v2.5-pro")
    context = {
        "investigadores": [{"nombre": i["nombre"], "citas": i.get("citas_total")} for i in invs],
        "matches_tematicos_top": [
            {
                "keyword": m["keyword"],
                "investigador_1": m["investigador_1"],
                "papers_inv1": m["papers_inv1"],
                "investigador_2": m["investigador_2"],
                "papers_inv2": m["papers_inv2"],
                "potencial": m["potencial"],
            }
            for m in matches[:10]
        ],
        "tendencias": (trends or [])[:8],
    }
    prompt = f"""Genera un resumen ejecutivo en español (3-4 párrafos) sobre el potencial de colaboración
entre estos investigadores, los matches temáticos detectados y las tendencias/oportunidades.
Sé concreto y académico. No inventes información: usa solo los datos entregados.

Datos:
{context}"""

    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.4,
    )
    return resp.choices[0].message.content or ""


def _resumen_fallback(invs: list[dict], matches: list[dict], trends: list[dict] | None) -> str:
    names = " y ".join(i["nombre"] for i in invs)
    top = ", ".join(m["keyword"] for m in matches[:5]) or "ninguno detectado aún"
    oportunidades = ", ".join(
        t["keyword"] for t in (trends or []) if t.get("categoria") == "Oportunidad"
    )[:200] or "pendiente de análisis"
    return f"""## Resumen de colaboración

Se analizaron los perfiles de **{names}** a partir de sus publicaciones, abstracts y keywords normalizadas.

### Matches temáticos detectados
Los temas con mayor solapamiento investigador-keyword-investigador son: {top}.

### Tendencias y oportunidades
Temas con crecimiento global donde el grupo podría ampliar cobertura: {oportunidades}.

### Nota
Este resumen fue generado sin LLM (configure `LLM_API_KEY` en `.env` para análisis narrativo con IA).
"""


def write_reporte_md(
    db: Database,
    output_dir: Path,
    trends: list[dict] | None = None,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "reporte.md"

    invs = db.get_investigadores()
    matches = get_matches_investigadores(db, limit=20)
    total_seg = db.get_metricas_totales()
    horas_manual = len(invs) * 4  # estimación: 4 h por investigador a mano
    minutos_auto = total_seg / 60

    client = _get_client()
    if client:
        try:
            resumen = _generar_resumen_llm(client, invs, matches, trends)
        except Exception:
            resumen = _resumen_fallback(invs, matches, trends)
    else:
        resumen = _resumen_fallback(invs, matches, trends)

    tendencias_block = ""
    if trends:
        tendencias_block = "\n## Temas emergentes y oportunidades\n\n"
        tendencias_block += "| Keyword | Categoría | Crec. global | Papers grupo |\n"
        tendencias_block += "|---------|-----------|--------------|-------------|\n"
        for t in trends[:12]:
            tendencias_block += (
                f"| {t['keyword']} | {t['categoria']} | {t['crecimiento_global']} | {t['papers_grupo']} |\n"
            )

    content = f"""# LCDA Searcher — Reporte del piloto

{resumen}

## Métrica de tiempo

| Método | Tiempo estimado |
|--------|-----------------|
| Manual (revisar abstracts y cruzar temas) | ~{horas_manual:.0f} horas ({len(invs)} investigadores × 4 h) |
| Pipeline automático | ~{minutos_auto:.1f} minutos ({total_seg:.0f} s) |

## Matches temáticos (top 10)

| Keyword | Investigador 1 | Papers | Investigador 2 | Papers | Potencial |
|---------|----------------|--------|----------------|--------|-----------|
"""
    for m in matches[:10]:
        content += (
            f"| {m['keyword']} | {m['investigador_1']} | {m['papers_inv1']} | "
            f"{m['investigador_2']} | {m['papers_inv2']} | {m['potencial']} |\n"
        )

    content += "\n## Evidencia breve\n\n"
    for m in matches[:5]:
        content += f"### {m['keyword']}\n\n"
        content += f"- **{m['investigador_1']}**: {m.get('evidencia_inv1') or 'sin evidencia resumida'}\n"
        content += f"- **{m['investigador_2']}**: {m.get('evidencia_inv2') or 'sin evidencia resumida'}\n\n"

    content += tendencias_block
    content += "\n---\n*Generado por LCDA Searcher*\n"

    path.write_text(content, encoding="utf-8")
    return path


def run_report(db: Database, output_dir: Path, trends: list[dict] | None = None) -> dict:
    t0 = time.time()
    sin_path = write_sinergias_csv(db, output_dir)
    match_paths = write_matching_outputs(db, output_dir)
    rep_path = write_reporte_md(db, output_dir, trends)
    dur = time.time() - t0
    db.log_metrica("report", dur)
    return {
        "sinergias_csv": str(sin_path),
        "matriz_investigador_keyword": match_paths["matriz"],
        "matches_investigadores": match_paths["matches"],
        "matches_recientes": match_paths["matches_recientes"],
        "reporte_md": str(rep_path),
        "duracion_seg": dur,
    }
