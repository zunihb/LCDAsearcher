# Decisiones de diseño y troubleshooting

Registro de lo acordado durante el desarrollo del piloto LCDA Searcher. Complementa [`README.md`](../README.md) y [`PLAN.md`](../PLAN.md).

## Investigadores piloto

| Scholar ID | Nombre | Afiliación | Papers (~) | h-index |
|------------|--------|------------|------------|---------|
| `Wk2naEgAAAAJ` | José R. Espinoza | Universidad de Concepción | ~307 | 50 |
| `6O2aO7IAAAAJ` | Miguel Torres | Universidad de los Andes | ~53 | — |

**BD actual:** 353 papers únicos en `data/lcda.db`.

---

## Arquitectura de 3 capas (acordada)

| Capa | Tecnología | Rol |
|------|------------|-----|
| 1 | Google Scholar (`scholarly`) | **Scraping** (no API): lista papers, citas, coautores del perfil |
| 2 | OpenAlex (API REST) | Abstract, DOI, autores/afiliaciones, `url_doi`, `url_ieee` |
| 3 | IEEE Xplore (browser + suscripción UdeC) | PDF para RAG — **sin credenciales en el repo** |

El **paper** es la entidad central en SQLite. El grafo se reconstruye al vuelo (NetworkX); no se persiste como grafo.

---

## Cambios implementados (piloto)

### Keywords (`src/keywords.py`)

- **15 keywords** por paper (`por_paper: 15` en `config.yaml`)
- Salida **`json_schema` strict** + fallback `json_object` + parser legacy
- Procesamiento **paralelo** (`parallel_workers: 4`) con **guardado 1-a-1** (`save_paper_keywords`) para ver progreso en tiempo real
- Filtros anti-basura: meta-texto del modelo ("The user wants..."), fragmentos de título
- Modelo recomendado: **`mimo-v2.5-pro`** + `LLM_JSON_MODE=json_schema`

### Base de datos (`src/db.py`)

- Campos nuevos en `papers`: `url_scholar`, `url_pdf`, `url_doi`, `url_ieee`, `doi`, `openalex_id`
- Tabla `paper_autores` (nombre, afiliación, orden, `openalex_author_id`)
- Migración automática; abstract no se sobrescribe con vacío (`COALESCE`)

### Abstracts (`src/abstracts.py`)

- Paso 2 del pipeline vía **OpenAlex**
- Fallback Scholar opcional (`abstracts.source: scholarly`)
- CLI: `--only-abstracts`, `--skip-abstracts`

### Extracción (`src/extract.py`)

- URLs Scholar, mejora de `scholar_pub_id`
- Autores persistidos en `paper_autores` cuando hay `autores_texto`

### Orquestador (`main.py`)

- Pipeline de **7 pasos** (abstracts entre extract y citations)
- Flags: `--only-abstracts`, `--skip-abstracts`, `--reprocess-keywords`

---

## Problemas encontrados y soluciones

| Problema | Causa | Solución |
|----------|-------|----------|
| ~530/1262 keywords basura | Kimi "thinking" + parser línea a línea | `json_schema`, filtros, cambio a MiMo |
| 111 fallback | Sin JSON, sin abstract, timeouts paralelos | `json_schema`, paso abstracts OpenAlex |
| 353/353 sin abstract | `fill_each_paper: false` en Scholar | `abstracts.py` vía OpenAlex |
| Kimi k2.5 lento/malo | Razonamiento 3000+ tokens | Evitar; usar `mimo-v2.5-pro` |
| deepseek/glm vacíos | `finish: length` vía proxy OpenCode | No recomendados para keywords |
| 0 citantes en corridas | Scholar bloquea `citedby` | Acotar a top-5; fase 2 para masivo |
| Pipeline parecía colgado | Keywords en batch sin commits | Guardado 1-a-1 + logs por paper |

---

## Benchmark LLM (10 papers reales)

| Modelo | OK (5–15 kw) | Notas |
|--------|--------------|-------|
| `mimo-v2.5-pro` + `json_schema` | 6/10 | Mejor en títulos difíciles |
| `mimo-v2.5` | 1/10 | Más barato; más fallos sin JSON mode |
| `kimi-k2.5` | Evitar | Razonamiento largo → fallback y basura |

Ver detalle en [`LLM_Y_KEYWORDS.md`](LLM_Y_KEYWORDS.md).

---

## OpenAlex — prueba manual (3 papers)

- Abstracts: 728–1329 caracteres
- DOI IEEE: `10.1109/...`
- Autores con afiliación ✅

---

## IEEE Xplore — acceso PDF

**Decisión:** no usar API key ni credenciales en `.env`. El usuario inicia sesión en el navegador con suscripción institucional UdeC; el agente usa esa sesión para descargar.

Flujo documentado en [`IEEE_PDF.md`](IEEE_PDF.md). Módulo futuro: `src/ieee.py` → `data/pdfs/`.

---

## Embeddings y RAG (fase futura)

- **No requiere Gemini** para embeddings
- Embeddings locales: `multilingual-e5-base` o `bge-m3`
- Vector store: `sqlite-vec` en la misma BD
- Tablas planificadas: `documentos`, `doc_chunks`, `chunk_embeddings`

Chat/keywords y embeddings son servicios independientes.

---

## Configuración LLM (`.env` — no commitear)

```env
LLM_BASE_URL=https://opencode.ai/zen/go/v1
LLM_API_KEY=sk-...
LLM_MODEL=mimo-v2.5-pro
LLM_JSON_MODE=json_schema
```

Límites OpenCode Go: ver [documentación oficial](https://opencode.ai/docs/go/).

---

## Comandos de recuperación

```bash
# Llenar abstracts (~2–3 min para ~350 papers)
python main.py --only-abstracts

# Pipeline sin re-scrapear Scholar
python main.py --skip-extract

# Reprocesar keywords (15 términos; requiere abstracts)
python main.py --skip-extract --skip-citations --reprocess-keywords

# Probar LLM
python scripts/test_llm.py
```

---

## Última corrida exitosa (jun 2026)

| Métrica | Resultado |
|---------|-----------|
| Papers en BD | 353 |
| Con abstract (OpenAlex) | 335 (~95 %) |
| Con DOI | 332 |
| Keywords LLM ok | **353/353** |
| Fallback | **0** |
| Promedio keywords/paper | ~20 (15 generadas + normalización canónica) |
| Términos únicos | 4 062 |
| Grafo | 4 408 nodos → `output/grafo.html` |
| Tiempo pipeline | ~64 min |

Comando usado:

```bash
python main.py --only-abstracts
python main.py --skip-extract --skip-citations --reprocess-keywords
```

18 papers sin match en OpenAlex (libros, capítulos, sin DOI indexado).

## Próximos pasos

1. Módulo `src/ieee.py`: cola PDF desde `doi`/`url_ieee`, descarga con sesión browser (usuario logueado en Xplore UdeC)
2. Fase RAG: chunks + embeddings locales
3. Escalado a 80–90 investigadores (fase 2)

---

## Métrica de valor

> Hacer el mapeo manual tomaría ~4 h por investigador. Con el pipeline, minutos.

Se reporta en `output/reporte.md`.

---

## Optimización de rendimiento del chat (2026-06-10)

### Problema

Con 16 investigadores y 6,609 papers, cada mensaje del chat tardaba **~60 segundos** en responder.

### Causa raíz

`get_matches_investigadores()` se ejecutaba en **cada mensaje** del usuario. Este flujo:

1. Consulta una matriz investigador-keyword de **69,426 filas** (5-table JOIN)
2. Genera **64,169 combinaciones** de pares de investigadores
3. Para **cada par**, ejecuta 2 queries de evidencia (`_evidence_for_keyword`)
4. Total: **~128,000 queries SQL** por mensaje

### Solución

#### 1. Caché de matches en memoria (`src/search.py`)

```python
_matches_cache = {"sig": None, "matches": None, "ts": 0}

def _get_cached_matches(db, ttl=300):
    # Firma de BD: counts de tablas clave
    sig = _db_signature(db)  # "16:6609:99491"
    
    # Si la firma no cambió y el TTL no expiró, retornar caché
    if cache["sig"] == sig and (now - cache["ts"]) < ttl:
        return cache["matches"]
    
    # Solo si la BD cambió, recompute
    matches = get_matches_investigadores_fast(db, limit=30)
    cache.update(sig=sig, matches=matches, ts=now)
    return matches
```

**Firma de BD**: `(COUNT investigadores, COUNT papers, COUNT paper_keywords)`. Si estos números no cambian, los matches son los mismos.

#### 2. Versión rápida sin evidencia (`get_matches_investigadores_fast`)

La versión original computaba `_evidence_for_keyword()` para cada par (2 queries SQL × 64K pares). La versión rápida:

- Calcula score por pares (en Python, sin SQL extra)
- **No ejecuta queries de evidencia** (se puede pedir bajo demanda)
- Reduce de ~128K queries a **0 queries extra**

#### 3. Índices SQLite

```sql
CREATE INDEX idx_keywords_termino ON keywords(termino);
CREATE INDEX idx_keywords_termino_canonico ON keywords(termino_canonico);
CREATE INDEX idx_papers_titulo ON papers(titulo);
CREATE INDEX idx_autorias_scholar_id ON autorias(scholar_id);
CREATE INDEX idx_autorias_paper_id ON autorias(paper_id);
CREATE INDEX idx_paper_keywords_paper_id ON paper_keywords(paper_id);
CREATE INDEX idx_paper_keywords_keyword_id ON paper_keywords(keyword_id);
CREATE INDEX idx_paper_autores_paper_id ON paper_autores(paper_id);
CREATE INDEX idx_coautores_scholar_id ON coautores(scholar_id);
CREATE INDEX idx_citas_investigador_id ON citas(investigador_id);
```

### Resultados

| Escenario | Antes | Después | Mejora |
|-----------|-------|---------|--------|
| 1er mensaje (sin caché) | ~60s | 0.64s | **94x** |
| Mensajes siguientes (caché) | ~60s | 0.07s | **857x** |

### Arquitectura del caché

```
Mensaje usuario
    │
    ▼
build_search_context()
    │
    ├── _get_cached_matches()
    │       │
    │       ├── _db_signature() → "16:6609:99491"
    │       │
    │       ├── firma == caché && TTL < 5min?
    │       │   ├── SÍ → return caché (0.07ms)
    │       │   └── NO → get_matches_fast() → guardar → return
    │       │
    │       └── matches (sin evidencia, solo scores)
    │
    ├── filtrar relevantes por tokens del query
    ├── si < 5 relevantes → rellenar con top matches
    └── return contexto para LLM
```

### Decisiones de diseño

- **TTL de 5 minutos**: suficiente para una sesión de chat, no stale por mucho tiempo
- **Invalidación por firma de BD**: si se agregan papers/investigadores, la firma cambia y se recomputa
- **Evidencia bajo demanda**: la versión rápida no computa evidencia (paper titles por par). Si el chat necesita evidencia, se puede llamar `_evidence_for_keyword` solo para los matches que se muestran al usuario (top 10-15, no 64K)

---

## Tool calling para el chat (2026-06-10)

### Problema

El chat enviaba **~5000 tokens** de contexto (todos los investigadores, keywords, papers, matches) en cada mensaje. El LLM tardaba **~60s** en procesar y generar respuesta.

### Solución: Function calling

En lugar de enviar todo el contexto, el LLM pide datos **bajo demanda** via tools:

```
Usuario: "¿quién trabaja en control predictivo?"

Flujo:
1. System prompt mínimo (~200 tokens) → LLM
2. LLM llama a search_papers(query="predictive") + search_keywords(term="predictivo")
3. Python ejecuta las queries SQL (~0.1s)
4. Resultados van al LLM → genera respuesta enfocada
```

### Tools disponibles (`src/tools.py`)

| Tool | Descripción |
|------|-------------|
| `get_current_date` | Fecha y hora actual |
| `list_researchers` | Lista investigadores con métricas |
| `get_researcher_profile` | Perfil detallado de un investigador |
| `search_papers` | Busca papers por keyword/título |
| `get_topic_matches` | Matches temáticos entre investigadores |
| `search_keywords` | Busca keywords en la BD |
| `get_db_stats` | Estadísticas generales de la BD |

### Resultados

| Escenario | Antes (contexto gigante) | Después (tools) |
|-----------|-------------------------|-----------------|
| Prompt tokens | ~5000 | ~200 |
| Respuesta simple | ~60s | **~11s** |
| Respuesta compleja | ~60s | **~15s** |
| Primera llamada LLM | N/A | ~4-5s |
| Ejecución tools | N/A | ~0.1s |
| Segunda llamada LLM | N/A | ~7-10s |

### Arquitectura

```
┌──────────────────────────────────────┐
│ System prompt (~200 tokens)          │
│ + Tools definitions (7 tools)        │
│ + Mensaje usuario                    │
│ + Historial reciente                 │
└──────────────┬───────────────────────┘
               │
               ▼
        ┌──────────────┐
        │ LLM (1ra)    │ ← 4-5s
        │ tool_calls?  │
        └──────┬───────┘
               │ SÍ
               ▼
        ┌──────────────┐
        │ execute_tool │ ← 0.1s
        │ (Python/SQL) │
        └──────┬───────┘
               │
               ▼
        ┌──────────────┐
        │ LLM (2da)    │ ← 7-10s streaming
        │ respuesta    │
        └──────────────┘
```

### Ventajas sobre el approach anterior

1. **Escalable**: no importa si hay 16 o 1000 investigadores, el prompt siempre es pequeño
2. **Preciso**: el LLM pide exactamente los datos que necesita
3. **Rápido**: 4-5x más rápido que enviar todo el contexto
4. **Mantenible**: cada tool es una función Python independiente

---

## Chat agentic (2026-06-10)

### Evolución

El chat pasó por 3 arquitecturas:

| Versión | Approach | Tiempo | Tokens prompt |
|---------|----------|--------|---------------|
| v1 | Contexto gigante | ~60s | ~5000 |
| v2 | Tool calling simple | ~15s | ~200 |
| v3 | **Agente loop** | ~10-40s | ~200 |

### Arquitectura agentic (v3)

```
Usuario: "¿quién trabaja en control predictivo?"
    │
    ▼
┌─ Agente Loop (max 6 rounds) ──────────────────────┐
│                                                     │
│  Round 1:                                           │
│    LLM → search_keywords("predictivo")              │
│    LLM → search_papers("predictivo")                │
│    (tool calls en paralelo)                         │
│    ▸ Ejecutar tools → resultados                    │
│                                                     │
│  Round 2:                                           │
│    LLM → respuesta final (con datos de tools)       │
│    ✅ FIN                                           │
│                                                     │
└─────────────────────────────────────────────────────┘
```

### Reglas del agente (system prompt)

- Máximo 3-4 tool calls por turno
- No llamar `get_researcher_profile` para cada investigador encontrado
- Usar `search_keywords` + `search_papers` para preguntas generales
- Si los datos son suficientes, generar respuesta sin más tools

### Tools disponibles

| Tool | Descripción | Tiempo |
|------|-------------|--------|
| `get_current_date` | Fecha actual | ~0.01s |
| `list_researchers` | Lista investigadores | ~0.05s |
| `get_researcher_profile` | Perfil detallado | ~0.1s |
| `search_papers` | Buscar papers | ~0.03s |
| `get_topic_matches` | Matches entre investigadores | ~0.1s |
| `search_keywords` | Buscar keywords | ~0.03s |
| `get_db_stats` | Stats de la BD | ~0.02s |

### Performance por tipo de pregunta

| Tipo | Tool calls | Tiempo |
|------|-----------|--------|
| Simple ("¿cuántos papers?") | 1 | ~8s |
| Perfil ("papers de Espinoza") | 1 | ~15s |
| Temática ("control predictivo") | 2-3 | ~15-40s |
| Fecha ("¿qué día es hoy?") | 1 | ~8s |

### Limitaciones actuales

- **Modelo**: mimo-v2.5-pro es un modelo de razonamiento (thinking), lo que agrega latencia. Un modelo más ligero sería más rápido para tool calling.
- **Sin streaming de tool calls**: el usuario ve los tools ejecutándose pero la respuesta final se genera completa (no streaming parcial).
- **Historial**: se mantiene en memoria, se pierde al reiniciar.
