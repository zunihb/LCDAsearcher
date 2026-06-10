"""Chat interactivo para LCDA Searcher — con tool calling.

En lugar de enviar todo el contexto al LLM, el modelo pide datos
bajo demanda via function calls. Mucho más rápido y escalable.
"""

from __future__ import annotations

import json
import os
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
    print_welcome,
    _strip_inline_thinking,
)
from src.db import Database
from src.tools import TOOLS, execute_tool

SYSTEM_PROMPT = """Eres el asistente de LCDA Searcher, un sistema de mapeo de investigación en electrónica de potencia.

Tienes acceso a una base de datos con investigadores, papers y keywords. Usa las tools disponibles para responder las preguntas del usuario.

Reglas:
- Responde en español, tono académico pero directo.
- Cita papers como: "Título (Año, N citas)"
- Si no tienes datos suficientes, di qué información falta.
- Sé conciso. Usa bullets y tablas cuando corresponda.
- No inventes datos que no estén en la base."""


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


def _run_tool_loop(
    client: OpenAI,
    db: Database,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    max_rounds: int = 5,
) -> str:
    """Ejecuta el loop de tool calling hasta obtener respuesta final."""
    for _ in range(max_rounds):
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            temperature=float(os.getenv("LLM_TEMPERATURE", "0.1")),
            max_tokens=int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "4000")),
        )

        msg = resp.choices[0].message
        messages.append(msg)

        # Si no hay tool calls, retornar respuesta final
        if not msg.tool_calls:
            return msg.content or "(sin respuesta)"

        # Ejecutar cada tool call
        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments) if tc.function.arguments else {}
            result = execute_tool(db, tc.function.name, args)
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result,
            })

    return "(demasiadas iteraciones de tools)"


def run_chat(db: Database, client: OpenAI) -> None:
    """Loop principal del chat con tool calling."""
    model = os.getenv("LLM_MODEL", "mimo-v2.5-pro")

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
            '"¿cuál es la fecha de hoy?"',
        ],
    )

    historial: list[dict[str, str]] = []
    max_history = 10

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

        # ── Tool calling loop ──
        try:
            messages: list[dict[str, Any]] = [
                {"role": "system", "content": SYSTEM_PROMPT},
            ]

            # Historial reciente
            trimmed = historial[-(max_history * 2):] if historial else []
            for h in trimmed:
                messages.append({"role": h["role"], "content": h["content"]})

            messages.append({"role": "user", "content": user_input})

            renderer = StreamRenderer()
            renderer.start()
            with Live(Spinner("dots", text=" [dim]Pensando...[/dim]"), console=console, transient=True):
                # Primera llamada (puede retornar tool calls)
                resp = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    tools=TOOLS,
                    temperature=float(os.getenv("LLM_TEMPERATURE", "0.1")),
                    max_tokens=int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "4000")),
                )

                msg = resp.choices[0].message
                messages.append(msg)

                if msg.tool_calls:
                    # Ejecutar tools y obtener respuesta final
                    for tc in msg.tool_calls:
                        args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                        result = execute_tool(db, tc.function.name, args)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result,
                        })

                    # Segunda llamada con resultados de tools (streaming)
                    for chunk in client.chat.completions.create(
                        model=model,
                        messages=messages,
                        tools=TOOLS,
                        temperature=float(os.getenv("LLM_TEMPERATURE", "0.1")),
                        max_tokens=int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "4000")),
                        stream=True,
                    ):
                        delta = chunk.choices[0].delta if chunk.choices else None
                        if delta and delta.content:
                            renderer.add(delta.content)
                else:
                    # Respuesta directa sin tools
                    if msg.content:
                        renderer.add(msg.content)

            content = renderer.stop()
            content = _strip_inline_thinking(content)

        except Exception as e:
            print_error(str(e))
            continue

        historial.append({"role": "user", "content": user_input})
        historial.append({"role": "assistant", "content": content})

        db.guardar_mensaje_chat(sesion_id, "user", user_input)
        db.guardar_mensaje_chat(sesion_id, "assistant", content)
