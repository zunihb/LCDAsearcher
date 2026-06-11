"""Abstracción de LLM para soportar OpenAI y Gemini.

Oculta las diferencias de formato entre OpenAI SDK y Google GenAI SDK.
"""

from __future__ import annotations

import json
import os
from typing import Any

from src.tools import TOOLS, execute_tool
from src.db import Database


def _convert_tools_to_gemini(openai_tools: list[dict]) -> list:
    """Convierte tools de formato OpenAI a formato Gemini."""
    from google.genai import types

    def _convert_schema(schema: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        typ = schema.get("type", "string")
        if typ == "array":
            out["type"] = "ARRAY"
            items = schema.get("items") or {}
            if isinstance(items, dict):
                out["items"] = _convert_schema(items)
        elif typ == "object":
            out["type"] = "OBJECT"
            props = schema.get("properties", {})
            out["properties"] = {k: _convert_schema(v) for k, v in props.items() if isinstance(v, dict)}
            if schema.get("required"):
                out["required"] = schema["required"]
        else:
            out["type"] = str(typ).upper()
        if "description" in schema:
            out["description"] = schema["description"]
        if "enum" in schema:
            out["enum"] = schema["enum"]
        if "minItems" in schema:
            out["minItems"] = schema["minItems"]
        if "maxItems" in schema:
            out["maxItems"] = schema["maxItems"]
        return out

    function_declarations = []
    for tool in openai_tools:
        fn = tool["function"]
        params = fn.get("parameters", {}).get("properties", {})
        required = fn.get("parameters", {}).get("required", [])

        gemini_params = {pname: _convert_schema(pdef) for pname, pdef in params.items()}

        function_declarations.append({
            "name": fn["name"],
            "description": fn.get("description", ""),
            "parameters": {
                "type": "object",
                "properties": gemini_params,
                "required": required,
            },
        })

    return [types.Tool(function_declarations=function_declarations)]


class LLMBackend:
    """Interfaz unificada para llamar al LLM con tool calling."""

    def __init__(self):
        self.backend = os.getenv("LLM_BACKEND", "openai").lower()

        if self.backend == "gemini":
            self._init_gemini()
        else:
            self._init_openai()

    def _init_openai(self):
        from openai import OpenAI
        self.client = OpenAI(
            api_key=os.getenv("LLM_API_KEY"),
            base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"),
        )
        self.model = os.getenv("LLM_MODEL", "mimo-v2.5-pro")

    def _init_gemini(self):
        from google import genai
        self.client = genai.Client(
            api_key=os.getenv("GEMINI_API_KEY"),
        )
        self.model = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")

    def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict] | None = None,
        system: str | None = None,
        max_rounds: int = 5,
        on_tool_call=None,
    ) -> str:
        """Ejecuta el agente loop con tool calling.

        Args:
            messages: historial de mensajes
            tools: definiciones de tools (formato OpenAI)
            system: system prompt
            max_rounds: máximo de iteraciones
            on_tool_call: callback(name, args) para mostrar tool calls

        Returns:
            contenido de la respuesta final
        """
        if self.backend == "gemini":
            return self._gemini_loop(messages, tools, system, max_rounds, on_tool_call)
        else:
            return self._openai_loop(messages, tools, system, max_rounds, on_tool_call)

    def _openai_loop(self, messages, tools, system, max_rounds, on_tool_call) -> str:
        """Loop con OpenAI SDK."""
        api_messages = []
        if system:
            api_messages.append({"role": "system", "content": system})
        api_messages.extend(messages)

        for _ in range(max_rounds):
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=api_messages,
                tools=tools or TOOLS,
                temperature=float(os.getenv("LLM_TEMPERATURE", "0.1")),
                max_tokens=int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "2000")),
            )
            msg = resp.choices[0].message
            api_messages.append(msg)

            if not msg.tool_calls:
                return msg.content or "(sin respuesta)"

            for tc in msg.tool_calls:
                args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                if on_tool_call:
                    on_tool_call(tc.function.name, args)
                result = execute_tool(self._db(), tc.function.name, args)
                api_messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })

        return "(máximo de iteraciones alcanzado)"

    def _gemini_loop(self, messages, tools, system, max_rounds, on_tool_call) -> str:
        """Loop con Google GenAI SDK."""
        from google.genai import types

        gemini_tools = _convert_tools_to_gemini(tools or TOOLS)

        # Construir contents para Gemini
        contents = []
        for msg in messages:
            if msg["role"] == "user":
                contents.append(types.Content(
                    role="user",
                    parts=[types.Part.from_text(text=msg["content"])],
                ))
            elif msg["role"] == "assistant":
                contents.append(types.Content(
                    role="model",
                    parts=[types.Part.from_text(text=msg["content"])],
                ))

        # Prepend system a la primer mensaje del usuario
        if system and contents:
            first_text = contents[0].parts[0].text
            contents[0] = types.Content(
                role="user",
                parts=[types.Part.from_text(text=f"{system}\n\nUsuario: {first_text}")],
            )

        config = types.GenerateContentConfig(
            tools=gemini_tools,
            thinking_config=types.ThinkingConfig(
                thinking_level=os.getenv("GEMINI_THINKING", "MINIMAL"),
            ),
        )

        for _ in range(max_rounds):
            response = self.client.models.generate_content(
                model=self.model,
                contents=contents,
                config=config,
            )

            if not response.candidates or not response.candidates[0].content.parts:
                return "(sin respuesta)"

            has_tool_calls = False
            for part in response.candidates[0].content.parts:
                if part.function_call:
                    has_tool_calls = True
                    fc = part.function_call
                    args = dict(fc.args)
                    if on_tool_call:
                        on_tool_call(fc.name, args)
                    result = execute_tool(self._db(), fc.name, args)

                    contents.append(response.candidates[0].content)
                    contents.append(types.Content(
                        role="function",
                        parts=[types.Part.from_function_response(
                            name=fc.name,
                            response={"result": result},
                        )],
                    ))

            if not has_tool_calls:
                return response.text or "(sin respuesta)"

        return "(máximo de iteraciones alcanzado)"

    def _db(self) -> Database:
        """Lazy load de la BD."""
        if not hasattr(self, "_db_instance"):
            from src.db import Database
            self._db_instance = Database(os.getenv("LCDA_DB_PATH", "data/lcda.db"))
        return self._db_instance
