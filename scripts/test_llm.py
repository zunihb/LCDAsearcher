#!/usr/bin/env python3
"""Prueba rápida de conexión al LLM configurado en .env"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# Modelos OpenCode Go compatibles con /v1/chat/completions (cliente OpenAI)
OPENCODE_CHAT_MODELS = [
    "deepseek-v4-flash",
    "deepseek-v4-pro",
    "glm-5.1",
    "glm-5",
    "kimi-k2.6",
    "kimi-k2.5",
    "mimo-v2.5",
    "mimo-v2.5-pro",
]

# Requieren endpoint Anthropic (/v1/messages) — no funcionan con este script
OPENCODE_ANTHROPIC_ONLY = [
    "minimax-m3", "minimax-m2.7", "minimax-m2.5",
    "qwen3.7-max", "qwen3.7-plus", "qwen3.6-plus",
]


def test_model(client: OpenAI, model: str) -> bool:
    try:
        r = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Responde solo: OK"}],
            max_tokens=10,
            temperature=0,
        )
        text = (r.choices[0].message.content or "").strip()
        print(f"  ✓ {model}: {text[:60]}")
        return True
    except Exception as e:
        print(f"  ✗ {model}: {e}")
        return False


def main():
    base = os.getenv("LLM_BASE_URL", "")
    key = os.getenv("LLM_API_KEY", "")
    model = os.getenv("LLM_MODEL", "deepseek-v4-flash")

    if not key or key.startswith("sk-tu") or key.startswith("sk-..."):
        print("ERROR: Configura LLM_API_KEY en .env (copia desde .env.example)")
        return 1

    print(f"Base URL: {base}")
    print(f"Modelo en .env: {model}\n")

    client = OpenAI(api_key=key, base_url=base)

    print("--- Prueba del modelo configurado ---")
    if not test_model(client, model):
        return 1

    if "--all" in sys.argv:
        print("\n--- Probando todos los modelos chat/completions ---")
        ok = sum(test_model(client, m) for m in OPENCODE_CHAT_MODELS)
        print(f"\n{ok}/{len(OPENCODE_CHAT_MODELS)} modelos OK")
        print("\n(No probados — requieren API Anthropic):", ", ".join(OPENCODE_ANTHROPIC_ONLY))

    print("\nListo. Ejecuta el pipeline con:")
    print("  python main.py --skip-extract --skip-citations   # si ya tienes datos")
    print("  python main.py                                   # extracción Scholar completa")
    return 0


if __name__ == "__main__":
    sys.exit(main())
