#!/usr/bin/env python3
"""CLI: orquesta el pipeline LCDA Searcher."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

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
    parser.add_argument("--skip-citations", action="store_true", help="Omitir citantes")
    parser.add_argument("--skip-keywords", action="store_true", help="Omitir keywords IA")
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
    citations_cfg = cfg.get("citations", {})
    keywords_cfg = cfg.get("keywords", {})
    trends_cfg = cfg.get("trends", {})

    print("=== LCDA Searcher — Pipeline ===\n")

    if not args.skip_extract:
        print("[1/6] Extracción Google Scholar...")
        r = run_extract(db, investigadores, raw_dir, scholarly_cfg)
        print(f"      → {r['papers']} papers en {r['duracion_seg']:.1f}s")
    else:
        print("[1/6] Extracción omitida")

    if not args.skip_citations:
        print("[2/6] Citantes (acotado)...")
        r = run_citations(
            db,
            investigadores,
            top_papers=citations_cfg.get("top_papers", 5),
            max_citantes=citations_cfg.get("max_citantes_por_paper", 50),
            use_proxies=scholarly_cfg.get("use_proxies", False),
        )
        print(f"      → {r['citantes']} citantes en {r['duracion_seg']:.1f}s")
    else:
        print("[2/6] Citantes omitidos")

    if not args.skip_keywords:
        print("[3/6] Keywords con IA...")
        r = run_keywords(
            db,
            por_paper=keywords_cfg.get("por_paper", 5),
            batch_size=keywords_cfg.get("batch_size", 10),
            idioma=keywords_cfg.get("idioma", "es"),
        )
        llm = "sí" if r["llm_usado"] else "no (fallback local)"
        print(f"      → {r['papers_procesados']} papers, LLM: {llm}")
    else:
        print("[3/6] Keywords omitidas")

    trends_data = []
    if trends_cfg.get("enabled", True) and not args.skip_trends:
        print("[4/6] Tendencias (OpenAlex)...")
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
        print("[4/6] Tendencias omitidas")

    print("[5/6] Grafo interactivo...")
    r = run_graph(db, output_dir)
    print(f"      → {r['nodos']} nodos → {r['output']}")

    print("[6/6] Reporte...")
    r = run_report(db, output_dir, trends_data)
    print(f"      → {r['reporte_md']}")

    print("\n=== Pipeline completado ===")
    print(f"Salidas en: {output_dir.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
