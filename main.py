#!/usr/bin/env python3
"""CLI: orquesta el pipeline LCDA Searcher."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

from src.abstracts import run_abstracts
from src.citations import run_citations
from src.db import Database
from src.extract import run_extract
from src.graph import run_graph
from src.keywords import run_keywords
from src.report import run_report
from src.trends import run_trends

load_dotenv()


def load_config(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def main() -> int:
    parser = argparse.ArgumentParser(description="LCDA Searcher — piloto de grafos de investigación")
    parser.add_argument("--config", default="config.yaml", help="Ruta a config.yaml")
    parser.add_argument("--skip-extract", action="store_true", help="Omitir extracción Scholar")
    parser.add_argument("--skip-abstracts", action="store_true", help="Omitir enriquecimiento abstracts")
    parser.add_argument("--only-abstracts", action="store_true", help="Solo enriquecer abstracts y salir")
    parser.add_argument("--skip-citations", action="store_true", help="Omitir citantes")
    parser.add_argument("--skip-keywords", action="store_true", help="Omitir keywords IA")
    parser.add_argument("--reprocess-keywords", action="store_true", help="Borrar keywords y reprocesar con LLM")
    parser.add_argument("--skip-trends", action="store_true", help="Omitir tendencias OpenAlex")
    parser.add_argument("--force-refresh", action="store_true", help="Re-scrapear Scholar (ignorar caché)")
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    data_dir = Path(cfg.get("data_dir", "data"))
    output_dir = Path(cfg.get("output_dir", "output"))
    raw_dir = data_dir / "raw"
    db_path = data_dir / "lcda.db"

    db = Database(db_path)
    db.init_schema()

    investigadores = cfg["investigadores"]
    scholarly_cfg = cfg.get("scholarly", {})
    abstracts_cfg = cfg.get("abstracts", {})
    citations_cfg = cfg.get("citations", {})
    keywords_cfg = cfg.get("keywords", {})
    trends_cfg = cfg.get("trends", {})

    print("=== LCDA Searcher — Pipeline ===\n")

    if args.only_abstracts:
        print("[*] Solo enriquecimiento de abstracts...")
        r = run_abstracts(
            db,
            mailto=trends_cfg.get("openalex_mailto", "lcda@example.com"),
            source=abstracts_cfg.get("source", "openalex"),
            pause_sec=abstracts_cfg.get("pause_sec", 0.35),
            scholar_pause_min=abstracts_cfg.get("scholar_pause_min", 2),
            scholar_pause_max=abstracts_cfg.get("scholar_pause_max", 4),
            use_proxies=scholarly_cfg.get("use_proxies", False),
        )
        print(f"      → {r['enriquecidos']}/{r['pendientes']} con abstract en {r['duracion_seg']:.1f}s")
        return 0

    if not args.skip_extract:
        print("[1/7] Extracción Google Scholar...")
        r = run_extract(db, investigadores, raw_dir, scholarly_cfg)
        print(f"      → {r['papers']} papers en {r['duracion_seg']:.1f}s")
    else:
        print("[1/7] Extracción omitida")

    if abstracts_cfg.get("enabled", True) and not args.skip_abstracts:
        print("[2/7] Abstracts, DOI, URLs y autores (OpenAlex)...")
        r = run_abstracts(
            db,
            mailto=trends_cfg.get("openalex_mailto", "lcda@example.com"),
            source=abstracts_cfg.get("source", "openalex"),
            pause_sec=abstracts_cfg.get("pause_sec", 0.35),
            scholar_pause_min=abstracts_cfg.get("scholar_pause_min", 2),
            scholar_pause_max=abstracts_cfg.get("scholar_pause_max", 4),
            use_proxies=scholarly_cfg.get("use_proxies", False),
        )
        print(f"      → {r['enriquecidos']}/{r['pendientes']} con abstract en {r['duracion_seg']:.1f}s")
    else:
        print("[2/7] Abstracts omitidos")

    if not args.skip_citations:
        print("[3/7] Citantes (acotado)...")
        r = run_citations(
            db,
            investigadores,
            top_papers=citations_cfg.get("top_papers", 5),
            max_citantes=citations_cfg.get("max_citantes_por_paper", 50),
            use_proxies=scholarly_cfg.get("use_proxies", False),
        )
        print(f"      → {r['citantes']} citantes en {r['duracion_seg']:.1f}s")
    else:
        print("[3/7] Citantes omitidos")

    if args.reprocess_keywords:
        print("[*] Reprocesando keywords (limpia tablas keywords)...")
        with db.connect() as conn:
            conn.execute("DELETE FROM tendencias_globales")
            conn.execute("DELETE FROM paper_keywords")
            conn.execute("DELETE FROM keywords")

    if not args.skip_keywords:
        print("[4/7] Keywords con IA...")
        r = run_keywords(
            db,
            por_paper=keywords_cfg.get("por_paper", 5),
            parallel_workers=keywords_cfg.get("parallel_workers", 1),
            progress_every=keywords_cfg.get("progress_every", 1),
            idioma=keywords_cfg.get("idioma", "es"),
            json_mode=keywords_cfg.get("json_mode", "json_schema"),
        )
        if r["llm_usado"]:
            print(f"      → {r['papers_procesados']} papers, LLM ok: {r.get('llm_ok', '?')}, fallback: {r.get('fallback', 0)}")
        else:
            print(f"      → {r['papers_procesados']} papers, LLM: no (fallback local)")
    else:
        print("[4/7] Keywords omitidas")

    trends_data = []
    if trends_cfg.get("enabled", True) and not args.skip_trends:
        print("[5/7] Tendencias (OpenAlex)...")
        r = run_trends(
            db,
            mailto=trends_cfg.get("openalex_mailto", "lcda@example.com"),
            output_dir=output_dir,
            ventana_anios=trends_cfg.get("ventana_anios", 6),
            top_n=trends_cfg.get("top_n_keywords", 15),
        )
        trends_data = r.get("trends", [])
        print(f"      → {r['keywords_analizadas']} keywords en {r['duracion_seg']:.1f}s")
    else:
        print("[5/7] Tendencias omitidas")

    print("[6/7] Grafo interactivo...")
    r = run_graph(db, output_dir)
    print(f"      → {r['nodos']} nodos → {r['output']}")

    print("[7/7] Reporte...")
    r = run_report(db, output_dir, trends_data)
    print(f"      → {r['reporte_md']}")

    print("\n=== Pipeline completado ===")
    print(f"Salidas en: {output_dir.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
