# Documentación LCDA Searcher

Índice de documentación técnica del proyecto.

| Documento | Contenido |
|-----------|-----------|
| [FUENTES_DE_DATOS.md](FUENTES_DE_DATOS.md) | Scholar (scraping) vs OpenAlex (API) vs IEEE Xplore |
| [ESQUEMA_BD.md](ESQUEMA_BD.md) | Tablas SQLite, campos, relaciones |
| [LLM_Y_KEYWORDS.md](LLM_Y_KEYWORDS.md) | Modelos OpenCode Go, JSON schema, 15 keywords |
| [IEEE_PDF.md](IEEE_PDF.md) | Cómo descargar PDFs con suscripción institucional (sin API key) |
| [DECISIONES_Y_TROUBLESHOOTING.md](DECISIONES_Y_TROUBLESHOOTING.md) | Decisiones del piloto, problemas resueltos, benchmarks, próximos pasos |

## Pipeline actual (7 pasos)

1. **Extracción Scholar** — perfiles y lista de papers (caché JSON)
2. **Abstracts OpenAlex** — abstract, DOI, URLs, autores por paper
3. **Citantes** — acotado (top-5 papers)
4. **Keywords** — 15 términos/paper vía LLM + `json_schema`
5. **Tendencias** — OpenAlex global + brecha interna
6. **Grafo** — NetworkX + Pyvis
7. **Reporte** — sinergias, métricas, narrativa

## Comandos frecuentes

```bash
# Solo llenar abstracts (~2 min para 350 papers)
python main.py --only-abstracts

# Pipeline sin re-scrapear Scholar
python main.py --skip-extract

# Reprocesar keywords (15 términos, requiere abstracts)
python main.py --skip-extract --skip-citations --reprocess-keywords
```

## Roadmap RAG

Ver [FUENTES_DE_DATOS.md § Embeddings](FUENTES_DE_DATOS.md) e [IEEE_PDF.md](IEEE_PDF.md).
