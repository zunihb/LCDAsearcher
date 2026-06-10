# LLM, JSON estructurado, chat y keywords

## Modelo recomendado (OpenCode Go)

```env
LLM_BASE_URL=https://opencode.ai/zen/go/v1
LLM_API_KEY=sk-...
LLM_MODEL=mimo-v2.5-pro
LLM_JSON_MODE=json_schema
```

### Comparativa rápida (benchmark 10 papers reales)

| Modelo | OK 5/15 kw | Notas |
|--------|------------|-------|
| `mimo-v2.5-pro` | 6/10 | Mejor calidad en títulos difíciles |
| `mimo-v2.5` | 1/10 | Más barato, más fallos sin JSON mode |
| `kimi-k2.5` | — | Evitar: razonamiento largo → fallback |

Límites OpenCode Go: ver [documentación oficial](https://opencode.ai/docs/go/).

## JSON nativo (`response_format`)

El pipeline **no parsea texto libre** si el modelo lo soporta. Tanto el chat como la extracción de keywords usan:

1. **`json_schema` (strict)** — preferido
2. **`json_object`** — fallback
3. Parser legacy — solo si ambos fallan

Schema obligatorio para keywords:

```json
{"keywords": ["kw1", "kw2", "... hasta 15"]}
```

Schema obligatorio para el chat:

```json
{"answer": "respuesta final en markdown"}
```

El razonamiento interno no se renderiza ni se guarda como respuesta. Si el proveedor lo envía en un campo separado (`reasoning` o `reasoning_content`), se conserva separado del contenido final.

Config:

```yaml
keywords:
  por_paper: 15
  json_mode: json_schema
  parallel_workers: 4
```

### Modelos compatibles con `/v1/chat/completions`

`mimo-v2.5`, `mimo-v2.5-pro`, `kimi-k2.5`, `kimi-k2.6`, `deepseek-v4-flash`, `deepseek-v4-pro`, `glm-5`, `glm-5.1`

### Modelos solo Anthropic (`/v1/messages`) — no usados aún

MiniMax M2.5/M2.7/M3, Qwen3.6/3.7 Plus/Max

## Fallback local

Si el LLM falla, `_fallback_keywords()` extrae frases del título. Se registra como `fallback` en métricas. Con `json_schema` + `mimo-v2.5-pro` el fallback debería ser mínimo.

## Reprocesar keywords

```bash
python main.py --skip-extract --skip-citations --skip-abstracts --reprocess-keywords
```

Con abstracts ya en BD (recomendado antes de keywords):

```bash
python main.py --only-abstracts
python main.py --skip-extract --skip-citations --reprocess-keywords
```
