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

from openai import OpenAI
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
- Para preguntas generales ("¿quién trabaja en X?"), usa `search_keywords` + `search_papers`. No perfiles individuales."""


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


# ── Agente loop ──────────────────────────────────────────────────────


def _agent_loop(
    client: OpenAI,
    db: Database,
    model: str,
    user_input: str,
    historial: list[dict[str, str]],
) -> str:
    """Ejecuta el agente loop: LLM → tools → LLM → ... → respuesta final.

    Retorna el contenido de la respuesta final.
    """
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
    ]

    # Historial reciente
    for h in historial[-20:]:
        messages.append({"role": h["role"], "content": h["content"]})

    messages.append({"role": "user", "content": user_input})

    max_rounds = 6
    total_tool_calls = 0

    for round_num in range(max_rounds):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=TOOLS,
                temperature=float(os.getenv("LLM_TEMPERATURE", "0.1")),
                max_tokens=int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "2000")),
            )
        except Exception as e:
            if round_num == 0:
                raise
            # Si ya hubo tool calls, intentar una respuesta sin tools
            console.print(f"  [dim]⚠ Error en round {round_num + 1}, generando respuesta final...[/dim]")
            messages.append({"role": "system", "content": "Genera una respuesta final basada en los datos recopilados hasta ahora."})
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=float(os.getenv("LLM_TEMPERATURE", "0.1")),
                max_tokens=int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "2000")),
            )
            return resp.choices[0].message.content or "(error generando respuesta)"

        msg = resp.choices[0].message
        messages.append(msg)

        # Sin tool calls → respuesta final
        if not msg.tool_calls:
            return msg.content or "(sin respuesta)"

        # Ejecutar tool calls
        for tc in msg.tool_calls:
            total_tool_calls += 1
            try:
                args = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except json.JSONDecodeError:
                args = {}

            _show_tool_call(tc.function.name, args)

            try:
                result = execute_tool(db, tc.function.name, args)
            except Exception as e:
                result = json.dumps({"error": str(e)})

            _show_tool_result(tc.function.name, result)

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result,
            })

    # Máximo de rounds alcanzado
    console.print(f"  [dim]⚠ Máximo de {max_rounds} rounds alcanzado ({total_tool_calls} tool calls)[/dim]")
    return f"(Agente alcanzó el límite de {max_rounds} rounds. Intenta una pregunta más específica.)"


# ── Chat loop principal ──────────────────────────────────────────────


def run_chat(db: Database, client: OpenAI) -> None:
    """Loop principal del chat agentic."""
    model = os.getenv("LLM_MODEL", "mimo-v2.5-pro")

    print_welcome(
        "LCDA Searcher — Agente de Investigación",
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

        # ── Ejecutar agente ──
        t0 = time.time()
        try:
            content = _agent_loop(client, db, model, user_input, historial)
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
