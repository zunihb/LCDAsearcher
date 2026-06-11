"""Chat agentic para LCDA Searcher.

El LLM actúa como un agente: planifica, ejecuta tools, observa resultados,
y decide siguiente paso. El usuario ve cada paso en tiempo real.
"""

from __future__ import annotations

import json
import os
import readline  # noqa: F401
import time
from typing import Any

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.spinner import Spinner
from rich.text import Text

from src.cli_output import (
    console,
    print_db_stats,
    print_error,
    print_info,
    print_match_table,
    print_researcher_profile,
    print_welcome,
)
from src.db import Database
from src.tools import TOOLS, execute_tool
from src.llm_backend import LLMBackend
from src.topic_search import search_keywords_hybrid

SYSTEM_PROMPT = """Eres el asistente de LCDA Searcher, un sistema de mapeo de investigación en electrónica de potencia.

Tienes acceso a una base de datos con investigadores, papers y keywords a través de tools.

## Instrucciones
- Responde en español, tono académico pero directo.
- Cita papers como: "Título (Año, N citas)"
- Usa bullets y tablas cuando corresponda.
- NO inventes datos. Si no tienes información, di qué falta.
- Sé conciso. Respuestas directas, no ensayos.

## Reglas de tools
- Llama SOLO los tools que necesitas. No llames tools por curiosidad.
- Si `search_papers` o `search_keywords` ya te dan suficiente info, NO llames `get_researcher_profile` para cada investigador.
- Máximo 3-4 tool calls por turno. Si necesitas más, resume lo que tienes y pregunta al usuario.
- Puedes llamar múltiples tools en paralelo si son independientes.
- Para preguntas generales ("¿quién trabaja en X?"), usa `search_topic_hybrid` + `search_papers`.
- Si el usuario pide evidencia, usa `get_topic_evidence`.
- Si el usuario pide comparar, usa `compare_researchers`.
- Si pregunta por calidad, usa `get_data_quality_report` o `get_suspicious_records`.

## Reglas de contexto (MUY IMPORTANTE)
- Mantén el contexto de la conversación anterior. Si el usuario mencionó un año, tema o investigador antes, ÚSALO en las siguientes preguntas.
- Ejemplo: si el usuario preguntó por "tendencias de 2026" y luego dice "papers de inversor multinivel", entiende que quiere papers de inversor multinivel DE 2026.
- Usa `year_from` y `year_to` en `search_papers` cuando el usuario mencione un año o período.
- Si el usuario dice "de este año", "de 2026", "del 2024-2026", SIEMPRE pasa los filtros de año a la tool."""


def _detect_intent(text: str) -> str:
    t = text.lower()
    if any(k in t for k in ["compar", "vs", "diferencia", "compara"]):
        return "compare"
    if any(k in t for k in ["evidencia", "justifica", "demuestra", "prueba"]):
        return "evidence"
    if any(k in t for k in ["calidad", "sospech", "duplic", "faltante"]):
        return "quality"
    if any(k in t for k in ["tendenc", "crece", "crecimiento", "trend"]):
        return "trend"
    if any(k in t for k in ["quien", "qué investigador", "quienes trabajan", "trabaja en", "tema"]):
        return "topic"
    return "general"


def _router_preview(db: Database, text: str) -> None:
    intent = _detect_intent(text)
    if intent == "topic":
        rows = search_keywords_hybrid(db, text, limit=3)
        console.print("  [dim]router → search_topic_hybrid[/dim]")
        for r in rows:
            console.print(f"  [dim]  · {r['keyword']} ({r['papers']} papers, score {r['score']})[/dim]")
    elif intent == "quality":
        console.print("  [dim]router → get_data_quality_report[/dim]")
    elif intent == "compare":
        console.print("  [dim]router → compare_researchers[/dim]")
    elif intent == "evidence":
        console.print("  [dim]router → get_topic_evidence[/dim]")
    elif intent == "trend":
        console.print("  [dim]router → get_trending_topics[/dim]")


# ── Utilidades de display ────────────────────────────────────────────


def _show_tool_call(name: str, args: dict) -> None:
    """Muestra qué tool se está ejecutando."""
    args_str = ", ".join(f"{k}={v!r}" for k, v in args.items()) if args else ""
    console.print(f"  [dim]▸ {name}({args_str})[/dim]")


def _show_tool_result(name: str, result: str) -> None:
    """Muestra resumen del resultado del tool."""
    try:
        data = json.loads(result)
        if isinstance(data, list):
            console.print(f"  [dim]  → {len(data)} resultados[/dim]")
        elif isinstance(data, dict):
            if "error" in data:
                console.print(f"  [dim]  → error: {data['error']}[/dim]")
            else:
                keys = list(data.keys())[:4]
                console.print(f"  [dim]  → {keys}[/dim]")
    except (json.JSONDecodeError, TypeError):
        pass


def _render_response(content: str) -> None:
    """Renderiza la respuesta final como Markdown."""
    if not content:
        return
    console.print()
    console.print(Markdown(content))
    console.print()


# ── Comandos especiales ──────────────────────────────────────────────


def _cmd_matches(db: Database) -> None:
    from src.matching import get_matches_investigadores
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

    from src.matching import get_investigador_keyword_matrix
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


def _cmd_hibrido(db: Database, term: str) -> None:
    from src.topic_search import search_keywords_hybrid

    rows = search_keywords_hybrid(db, term, limit=10)
    if not rows:
        print_info("No encontré resultados con la búsqueda híbrida.")
        return
    for r in rows:
        console.print(f"[bold cyan]{r['keyword']}[/bold cyan] → papers={r['papers']} citas={r['citas']} score={r['score']}")


def _cmd_calidad(db: Database) -> None:
    from src.data_quality import get_data_quality_report

    report = get_data_quality_report(db)
    console.print(
        Panel(
            f"[bold]Cobertura abstracts[/bold]: {report['cobertura_abstract']}%\n"
            f"[bold]Papers con abstract[/bold]: {report['papers_con_abstract']}/{report['papers']}\n"
            f"[bold]DOI duplicados[/bold]: {len(report['doi_duplicados'])}\n"
            f"[bold]OpenAlex duplicados[/bold]: {len(report['openalex_duplicados'])}\n"
            f"[bold]Keywords fragmentadas[/bold]: {len(report['keywords_fragmentadas'])}",
            title="[bold bright_white]Calidad de datos[/bold bright_white]",
            border_style="bright_magenta",
            padding=(0, 2),
        )
    )


# ── Agente loop ──────────────────────────────────────────────────────


def _agent_loop(
    llm: LLMBackend,
    db: Database,
    user_input: str,
    historial: list[dict[str, str]],
) -> str:
    """Ejecuta el agente loop usando LLMBackend unificado."""
    messages: list[dict[str, Any]] = []
    for h in historial[-20:]:
        messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": user_input})

    def on_tool(name, args):
        _show_tool_call(name, args)

    try:
        return llm.chat_with_tools(
            messages=messages,
            tools=TOOLS,
            system=SYSTEM_PROMPT,
            max_rounds=6,
            on_tool_call=on_tool,
        )
    except Exception as e:
        raise


# ── Chat loop principal ──────────────────────────────────────────────


def run_chat(db: Database, client=None) -> None:
    """Loop principal del chat agentic."""
    llm = LLMBackend()
    backend_name = llm.backend
    model_name = llm.model

    print_welcome(
        f"LCDA Searcher — Agente de Investigación [{backend_name}/{model_name}]",
        commands=[
            ("/matches", "ver matches temáticos top"),
            ("/perfil <nombre>", "resumen de un investigador"),
            ("/fuentes", "estadísticas de la base de datos"),
            ("/calidad", "reporte de calidad de datos"),
            ("/tema <term>", "búsqueda híbrida de un tema"),
            ("/historial", "ver sesiones de chat guardadas"),
            ("/limpiar", "limpiar historial de conversación"),
            ("/salir", "salir del chat"),
        ],
        examples=[
            '"¿quién trabaja en control predictivo?"',
            '"compará a los investigadores en electrónica de potencia"',
            '"últimos papers de Espinoza"',
            '"¿cuántos papers hay sobre fotovoltaica?"',
            '"¿cuál es la fecha de hoy?"',
        ],
    )

    historial: list[dict[str, str]] = []
    sesion_id = db.crear_sesion_chat(modo="chat")

    while True:
        try:
            user_input = console.input("\n[bold cyan]>[/bold cyan] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]¡Hasta luego![/dim]")
            break

        if not user_input:
            continue

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

        if cmd == "/calidad":
            _cmd_calidad(db)
            continue

        if cmd.startswith("/tema"):
            parts = user_input.split(maxsplit=1)
            term = parts[1] if len(parts) > 1 else ""
            if not term:
                print_info("Uso: /tema <término>")
            else:
                _cmd_hibrido(db, term)
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
            print_info("Comandos: /matches, /perfil, /fuentes, /calidad, /tema, /limpiar, /salir")
            continue

        # ── Ejecutar agente ──
        t0 = time.time()
        try:
            _router_preview(db, user_input)
            content = _agent_loop(llm, db, user_input, historial)
            _render_response(content)
        except Exception as e:
            print_error(str(e))
            continue

        elapsed = time.time() - t0
        console.print(f"  [dim]⏱ {elapsed:.1f}s[/dim]", highlight=False)

        historial.append({"role": "user", "content": user_input})
        historial.append({"role": "assistant", "content": content})

        db.guardar_mensaje_chat(sesion_id, "user", user_input)
        db.guardar_mensaje_chat(sesion_id, "assistant", content)
