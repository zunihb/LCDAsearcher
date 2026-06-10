"""Chat interactivo para LCDA Searcher."""

from __future__ import annotations

import readline  # noqa: F401 — habilita historial de flechas en input()
import sys
from typing import Any

from openai import OpenAI
from rich.console import Console
from rich.live import Live
from rich.spinner import Spinner

from src.cli_output import (
    StreamRenderer,
    console,
    print_db_stats,
    print_error,
    print_info,
    print_match_table,
    print_researcher_profile,
    print_response,
    print_sources,
    print_welcome,
    _strip_inline_thinking,
)
from src.db import Database
from src.matching import get_investigador_keyword_matrix, get_matches_investigadores
from src.search import build_search_context, format_context_for_prompt, ask_llm, ask_llm_stream


def _cmd_matches(db: Database) -> None:
    matches = get_matches_investigadores(db, limit=15)
    if not matches:
        print_info("No hay matches temáticos disponibles.")
        return
    print_match_table(matches, limit=15)


def _cmd_perfil(db: Database, nombre_parcial: str) -> None:
    invs = db.get_investigadores()
    target = None
    for inv in invs:
        if nombre_parcial.lower() in inv["nombre"].lower():
            target = inv
            break
    if not target:
        nombres = ", ".join(i["nombre"] for i in invs)
        print_error(f"No encontré '{nombre_parcial}'. Investigadores: {nombres}")
        return

    sid = target["scholar_id"]
    matrix = get_investigador_keyword_matrix(db)
    inv_kws = sorted(
        [r for r in matrix if r["scholar_id"] == sid],
        key=lambda r: r["papers"],
        reverse=True,
    )[:12]

    kws = [
        {
            "keyword": kw["keyword"],
            "papers": kw["papers"],
            "citas": kw["citas"],
            "rango": f"{kw['primer_anio']}-{kw['ultimo_anio']}",
        }
        for kw in inv_kws
    ]
    print_researcher_profile(target, kws)


def _cmd_fuentes(db: Database) -> None:
    stats = db.query("""
        SELECT
            (SELECT COUNT(*) FROM investigadores) AS investigadores,
            (SELECT COUNT(*) FROM papers) AS papers,
            (SELECT COUNT(*) FROM papers WHERE abstract IS NOT NULL AND trim(abstract) != '') AS con_abstract,
            (SELECT COUNT(*) FROM keywords) AS keywords,
            (SELECT COUNT(*) FROM paper_keywords) AS vinculos_kw,
            (SELECT COUNT(*) FROM autorias) AS autorias,
            (SELECT MIN(anio) FROM papers WHERE anio IS NOT NULL) AS min_anio,
            (SELECT MAX(anio) FROM papers) AS max_anio
    """)
    if not stats:
        print_error("Error al consultar la base de datos.")
        return
    print_db_stats(stats[0])


def run_chat(db: Database, client: OpenAI) -> None:
    """Loop principal del chat interactivo."""
    print_welcome(
        "LCDA Searcher — Chat de Investigación",
        commands=[
            ("/matches", "ver matches temáticos top"),
            ("/perfil <nombre>", "resumen de un investigador"),
            ("/fuentes", "estadísticas de la base de datos"),
            ("/historial", "ver sesiones de chat guardadas"),
            ("/limpiar", "limpiar historial de conversación"),
            ("/salir", "salir del chat"),
        ],
        examples=[
            '"¿quién trabaja en control predictivo?"',
            '"compará a los investigadores en electrónica de potencia"',
            '"últimos papers de Espinoza"',
        ],
    )

    historial: list[dict[str, str]] = []
    max_history = 10

    # Crear sesión de chat en la DB
    sesion_id = db.crear_sesion_chat(modo="chat")

    while True:
        try:
            user_input = console.input("\n[bold cyan]>[/bold cyan] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]¡Hasta luego![/dim]")
            break

        if not user_input:
            continue

        # Comandos especiales
        cmd = user_input.lower()

        if cmd == "/salir":
            console.print("[dim]¡Hasta luego![/dim]")
            break

        if cmd == "/matches":
            _cmd_matches(db)
            continue

        if cmd.startswith("/perfil"):
            parts = user_input.split(maxsplit=1)
            nombre = parts[1] if len(parts) > 1 else ""
            if not nombre:
                print_info("Uso: /perfil <nombre o parte del nombre>")
            else:
                _cmd_perfil(db, nombre)
            continue

        if cmd == "/fuentes":
            _cmd_fuentes(db)
            continue

        if cmd == "/limpiar":
            historial.clear()
            print_info("Historial limpiado.")
            continue

        if cmd == "/historial":
            stats = db.get_chat_stats()
            sesiones = db.get_sesiones_chat(limit=5)
            console.print(f"\n[bold]Sesiones:[/bold] {stats.get('total_sesiones', 0)} | "
                          f"[bold]Mensajes:[/bold] {stats.get('total_mensajes', 0)}")
            for s in sesiones:
                console.print(f"  [cyan]#{s['id']}[/cyan] {s['iniciada_en']} ({s['modo']}, {s['total_mensajes']} msgs)")
            console.print()
            continue

        if user_input.startswith("/"):
            print_error(f"Comando desconocido: {user_input}")
            print_info("Comandos: /matches, /perfil, /fuentes, /limpiar, /salir")
            continue

        # Búsqueda y respuesta con streaming
        try:
            # Construir contexto
            with Live(Spinner("dots", text=" [dim]Buscando...[/dim]"), console=console, transient=True):
                context = build_search_context(db, user_input)

            # Mostrar fuentes
            kw_found = context["keywords_encontradas"]
            papers_found = len(context["papers_representativos"]) + len(context["papers_por_titulo"])
            matches_found = len(context["matches_tematicos"])
            print_sources(kw_found, papers_found, matches_found)

            # Streaming con spinner
            trimmed = historial[-(max_history * 2):] if historial else None
            renderer = StreamRenderer()
            renderer.start()
            with Live(Spinner("dots", text=" [dim]Generando respuesta...[/dim]"), console=console, transient=True):
                for chunk in ask_llm_stream(client, context, user_input, trimmed):
                    renderer.add(chunk)
            content = renderer.stop()
            content = _strip_inline_thinking(content)
        except Exception as e:
            print_error(str(e))
            continue

        # Guardar en historial y en DB
        historial.append({"role": "user", "content": user_input})
        historial.append({"role": "assistant", "content": content})

        # Persistir en DB
        db.guardar_mensaje_chat(sesion_id, "user", user_input)
        db.guardar_mensaje_chat(
            sesion_id, "assistant", content,
            keywords_detectadas=kw_found,
            papers_encontrados=papers_found,
            matches_relevantes=matches_found,
        )
