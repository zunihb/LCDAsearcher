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
