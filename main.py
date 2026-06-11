#!/usr/bin/env python3
"""CLI: orquesta el pipeline LCDA Searcher."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

from src.abstracts import run_abstracts
from src.chat import run_chat
from src.citations import run_citations
from src.cli_output import console, print_error, print_response
from src.db import Database
from src.extract import run_extract
from src.graph import run_graph
from src.keywords import run_keywords
from src.report import run_report
from src.search import get_llm_client
from src.topic_search import seed_default_keyword_aliases
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
    parser.add_argument("--reprocess-abstracts", action="store_true", help="Reprocesar abstracts sobre toda la base")
    parser.add_argument("--phase-a", action="store_true", help="Corre fase A: abstracts + keywords + aliases")
    parser.add_argument("--abstracts-limit", type=int, default=None, help="Limita cuántos papers procesa abstracts")
    parser.add_argument("--abstracts-batch-size", type=int, default=5, help="Tamaño de lote para abstracts")
    parser.add_argument("--abstracts-batch-pause", type=float, default=3.0, help="Pausa entre lotes de abstracts")
    parser.add_argument("--source", type=str, default=None, help="Fuente de abstracts: ieee-pw | openalex | scholarly | scholarly-pw | multisource | ieee")
    parser.add_argument("--skip-citations", action="store_true", help="Omitir citantes")
    parser.add_argument("--skip-keywords", action="store_true", help="Omitir keywords IA")
    parser.add_argument("--reprocess-keywords", action="store_true", help="Borrar keywords y reprocesar con LLM")
    parser.add_argument("--skip-trends", action="store_true", help="Omitir tendencias OpenAlex")
    parser.add_argument("--force-refresh", action="store_true", help="Re-scrapear Scholar (ignorar caché)")
    parser.add_argument("--search", type=str, default=None, help="Búsqueda single-shot con LLM")
    parser.add_argument("--chat", action="store_true", help="Modo chat interactivo con LLM (terminal)")
    parser.add_argument("--tui", action="store_true", help="Modo TUI interactivo con Textual (visual)")
    parser.add_argument("--history", action="store_true", help="Mostrar historial de chats guardados")
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    data_dir = Path(cfg.get("data_dir", "data"))
    output_dir = Path(cfg.get("output_dir", "output"))
    raw_dir = data_dir / "raw"
    db_path = data_dir / "lcda.db"

    db = Database(db_path)
    db.init_schema()
    seed_default_keyword_aliases(db)

    # --- Historial de chats ---
    if args.history:
        stats = db.get_chat_stats()
        sesiones = db.get_sesiones_chat(limit=10)
        console.print(f"\n[bold bright_white]Historial de chats[/bold bright_white]")
        console.print(f"Sesiones: [cyan]{stats.get('total_sesiones', 0)}[/cyan] | "
                      f"Mensajes: [cyan]{stats.get('total_mensajes', 0)}[/cyan] | "
                      f"Preguntas: [cyan]{stats.get('total_preguntas', 0)}[/cyan]\n")
        for s in sesiones:
            msgs = db.get_mensajes_sesion(s["id"])
            console.print(f"[bold cyan]Sesion {s['id']}[/bold cyan] — {s['iniciada_en']} ({s['modo']}, {s['total_mensajes']} msgs)")
            for m in msgs[:6]:
                rol = "[bold green]U[/bold green]" if m["rol"] == "user" else "[bold yellow]A[/bold yellow]"
                preview = (m["contenido"] or "")[:80].replace("\n", " ")
                console.print(f"  {rol} {preview}")
            if len(msgs) > 6:
                console.print(f"  [dim]... +{len(msgs) - 6} mensajes mas[/dim]")
            console.print()
        return 0

    # --- Modos de búsqueda / chat (no ejecutan el pipeline) ---
    if args.search or args.chat or args.tui:
        backend = os.getenv("LLM_BACKEND", "openai").lower()
        client = get_llm_client() if backend != "gemini" else None
        if backend != "gemini" and not client:
            print_error("No se encontró LLM_API_KEY en .env")
            return 1
        if args.search:
            from src.chat import _agent_loop
            from src.llm_backend import LLMBackend
            llm = LLMBackend()
            response = _agent_loop(llm, db, args.search, historial=[])
            print_response(response, None)
            return 0
        if args.tui:
            from src.tui import LCDATui
            app = LCDATui(db, client)
            app.run()
            return 0
        if args.chat:
            run_chat(db, client)
            return 0

    investigadores = cfg["investigadores"]
    scholarly_cfg = cfg.get("scholarly", {})
    abstracts_cfg = cfg.get("abstracts", {})
    citations_cfg = cfg.get("citations", {})
    keywords_cfg = cfg.get("keywords", {})
    trends_cfg = cfg.get("trends", {})

    print("=== LCDA Searcher — Pipeline ===\n")

    if args.phase_a:
        print("[*] Fase A: abstracts + keywords + aliases...\n")

        source = args.source or abstracts_cfg.get("source", "openalex")
        if source == "ieee-pw":
            from src.abstracts_ieee_pw import run_abstracts_ieee_playwright
            r = run_abstracts_ieee_playwright(
                db,
                limit=args.abstracts_limit,
                batch_size=args.abstracts_batch_size,
                batch_pause_sec=args.abstracts_batch_pause,
            )
            print(f"      abstracts → {r['enriquecidos']}/{r['pendientes']} en {r['duracion_seg']:.1f}s")
        elif source == "scholarly-pw":
            from src.abstracts_pw import run_abstracts_playwright
            r = run_abstracts_playwright(
                db,
                limit=args.abstracts_limit,
                batch_size=args.abstracts_batch_size,
                batch_pause_sec=args.abstracts_batch_pause,
            )
            print(f"      abstracts → {r['enriquecidos']}/{r['pendientes']} en {r['duracion_seg']:.1f}s")
        elif source == "multisource":
            from src.abstracts_multi import run_abstracts_multisource
            r = run_abstracts_multisource(
                db,
                limit=args.abstracts_limit,
                batch_size=args.abstracts_batch_size,
                batch_pause_sec=args.abstracts_batch_pause,
            )
            print(f"      abstracts → {r['enriquecidos']}/{r['pendientes']} en {r['duracion_seg']:.1f}s")
        elif source == "ieee":
            from src.abstracts_ieee import run_abstracts_ieee
            r = run_abstracts_ieee(
                db,
                limit=args.abstracts_limit,
                batch_size=args.abstracts_batch_size,
                batch_pause_sec=args.abstracts_batch_pause,
            )
            print(f"      abstracts → {r['enriquecidos']}/{r['pendientes']} en {r['duracion_seg']:.1f}s")
        elif source != "skip":
            r = run_abstracts(
                db,
                mailto=trends_cfg.get("openalex_mailto", "lcda@example.com"),
                source=source,
                pause_sec=abstracts_cfg.get("pause_sec", 0.35),
                scholar_pause_min=abstracts_cfg.get("scholar_pause_min", 2),
                scholar_pause_max=abstracts_cfg.get("scholar_pause_max", 4),
                use_proxies=scholarly_cfg.get("use_proxies", False),
                reprocess_all=True,
                limit=args.abstracts_limit,
                batch_size=args.abstracts_batch_size,
                batch_pause_sec=args.abstracts_batch_pause,
            )
            print(f"      abstracts → {r['enriquecidos']}/{r['pendientes']} en {r['duracion_seg']:.1f}s")
        else:
            print("      abstracts → omitidos (--source skip)")

        if not args.skip_keywords:
            with db.connect() as conn:
                conn.execute("DELETE FROM tendencias_globales")
                conn.execute("DELETE FROM paper_keywords")
                conn.execute("DELETE FROM keywords")

            r = run_keywords(
                db,
                por_paper=keywords_cfg.get("por_paper", 15),
                parallel_workers=keywords_cfg.get("parallel_workers", 1),
                progress_every=keywords_cfg.get("progress_every", 1),
                idioma=keywords_cfg.get("idioma", "es"),
                json_mode=keywords_cfg.get("json_mode", "json_schema"),
            )
            print(f"      keywords → {r['papers_procesados']} papers, llm={r.get('llm_ok', 0)}, fallback={r.get('fallback', 0)}")
        else:
            print("      keywords → omitidas (--skip-keywords)")

        from src.data_quality import get_data_quality_report
        report = get_data_quality_report(db)
        print(f"      calidad → {report['cobertura_abstract']}% abstracts, {len(report['keywords_fragmentadas'])} grupos fragmentados")
        return 0

    if args.only_abstracts:
        print("[*] Solo enriquecimiento de abstracts...")
        source = args.source or abstracts_cfg.get("source", "openalex")
        if source == "ieee-pw":
            from src.abstracts_ieee_pw import run_abstracts_ieee_playwright
            r = run_abstracts_ieee_playwright(
                db,
                limit=args.abstracts_limit,
                batch_size=args.abstracts_batch_size,
                batch_pause_sec=args.abstracts_batch_pause,
            )
        elif source == "scholarly-pw":
            from src.abstracts_pw import run_abstracts_playwright
            r = run_abstracts_playwright(
                db,
                limit=args.abstracts_limit,
                batch_size=args.abstracts_batch_size,
                batch_pause_sec=args.abstracts_batch_pause,
            )
        elif source == "multisource":
            from src.abstracts_multi import run_abstracts_multisource
            r = run_abstracts_multisource(
                db,
                limit=args.abstracts_limit,
                batch_size=args.abstracts_batch_size,
                batch_pause_sec=args.abstracts_batch_pause,
            )
        elif source == "ieee":
            from src.abstracts_ieee import run_abstracts_ieee
            r = run_abstracts_ieee(
                db,
                limit=args.abstracts_limit,
                batch_size=args.abstracts_batch_size,
                batch_pause_sec=args.abstracts_batch_pause,
            )
        else:
            r = run_abstracts(
                db,
                mailto=trends_cfg.get("openalex_mailto", "lcda@example.com"),
                source=source,
                pause_sec=abstracts_cfg.get("pause_sec", 0.35),
                scholar_pause_min=abstracts_cfg.get("scholar_pause_min", 2),
                scholar_pause_max=abstracts_cfg.get("scholar_pause_max", 4),
                use_proxies=scholarly_cfg.get("use_proxies", False),
                reprocess_all=args.reprocess_abstracts,
                limit=args.abstracts_limit,
                batch_size=args.abstracts_batch_size,
                batch_pause_sec=args.abstracts_batch_pause,
            )
        print(f"      → {r['enriquecidos']}/{r['pendientes']} con abstract en {r['duracion_seg']:.1f}s")
        return 0

    if not args.skip_extract:
        print("[1/7] Extracción Google Scholar...")
        r = run_extract(
            db,
            investigadores,
            raw_dir,
            scholarly_cfg,
            force_refresh=args.force_refresh,
        )
        print(f"      → {r['papers']} papers en {r['duracion_seg']:.1f}s")
    else:
        print("[1/7] Extracción omitida")

    if abstracts_cfg.get("enabled", True) and not args.skip_abstracts:
        print("[2/7] Abstracts, DOI, URLs y autores (IEEE Playwright)...")
        r = run_abstracts(
            db,
            mailto=trends_cfg.get("openalex_mailto", "lcda@example.com"),
            source=args.source or abstracts_cfg.get("source", "openalex"),
            pause_sec=abstracts_cfg.get("pause_sec", 0.35),
            scholar_pause_min=abstracts_cfg.get("scholar_pause_min", 2),
            scholar_pause_max=abstracts_cfg.get("scholar_pause_max", 4),
            use_proxies=scholarly_cfg.get("use_proxies", False),
            reprocess_all=args.reprocess_abstracts,
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
            por_paper=keywords_cfg.get("por_paper", 15),
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
    if r.get("matches_investigadores"):
        print(f"      → {r['matches_investigadores']}")

    print("\n=== Pipeline completado ===")
    print(f"Salidas en: {output_dir.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
