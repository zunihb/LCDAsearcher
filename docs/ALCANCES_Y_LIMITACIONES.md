# LCDA Searcher — Alcances, Limitaciones y Costos

**Fecha:** 10 de junio de 2026  
**Versión:** Prototipo funcional (fase A-C completada)

---

## 1. Estado actual del sistema

### 1.1 Base de datos

| Métrica | Valor |
|---------|-------|
| Investigadores mapeados | 16 |
| Papers únicos | 6.609 |
| Keywords generadas por IA | 44.454 |
| Vínculos paper-keyword | 97.727 |
| Papers con keywords | 6.609 (100%) |
| Papers con abstract | 1.670 (25,3%) |
| Alias de normalización | 8.982 |
| Rango temporal | 1910–2026 |

### 1.2 Cobertura por investigador

| Investigador | Papers | Con abstract | Cobertura |
|-------------|--------|--------------|-----------|
| Jose Rodriguez | 1.924 | 532 | 27,7% |
| Pat Wheeler | 1.238 | 266 | 21,5% |
| Haitham Abu-Rub | 813 | 180 | 22,1% |
| Marco Rivera | 664 | 149 | 22,4% |
| Geza Joos | 601 | 196 | 32,6% |
| Javier Muñoz Vidal | 439 | 101 | 23,0% |
| Mariusz Malinowski | 361 | 59 | 16,3% |
| Marian Kazmierkowski | 348 | 80 | 23,0% |
| **José R. Espinoza** | **307** | **292** | **95,1%** |
| Daniel Sbarbaro | 263 | 47 | 17,9% |
| Marcelo Perez | 216 | 98 | 45,4% |
| Luis Morán | 214 | 119 | 55,6% |
| Christian Rojas | 128 | 54 | 42,2% |
| Miguel Figueroa | 128 | 17 | 13,3% |
| **Miguel Torres** | **53** | **50** | **94,3%** |
| Claudio Molina | 5 | 4 | 80,0% |

---

## 2. Qué puede hacer el sistema hoy

### 2.1 Chat agentic con herramientas

El sistema tiene un chat interactivo que responde preguntas en lenguaje natural usando function calling. El LLM planifica, ejecuta herramientas y muestra resultados en tiempo real.

**Herramientas disponibles:**

| Herramienta | Qué hace |
|-------------|----------|
| `search_topic_hybrid` | Busca temas con normalización, aliases y ranking por relevancia |
| `get_researchers_by_topic` | Lista investigadores ordenados por actividad en un tema |
| `get_topic_evidence` | Devuelve papers que justifican que un investigador trabaja en un tema |
| `compare_researchers` | Compara dos o más investigadores por temas, papers y actividad |
| `get_trending_topics` | Identifica temas en crecimiento dentro de la base |
| `get_data_quality_report` | Reporta cobertura de abstracts, duplicados, fragmentación |
| `get_suspicious_records` | Lista papers sospechosos para revisión manual |
| `search_papers` | Busca papers por keyword o título |
| `get_researcher_profile` | Perfil detallado de un investigador |
| `get_topic_matches` | Matches temáticos entre investigadores |

### 2.2 Preguntas que ya puede responder

- "¿Quién trabaja en control predictivo?"
- "Compara a Espinoza y Torres en electrónica de potencia"
- "¿Qué papers justifican que Miguel Torres trabaja en máquina síncrona virtual?"
- "¿Qué temas están creciendo en 2024-2026?"
- "¿Qué pares de investigadores comparten temas pero no tienen coautoría?"
- "¿Qué investigadores tienen baja cobertura de abstracts?"
- "¿Qué papers tienen año sospechoso o faltante?"
- "¿Qué keywords están fragmentadas en variantes similares?"

### 2.3 Búsqueda semántica con aliases

El sistema resuelve acrónimos y variantes automáticamente:

| Consulta del usuario | Resuelve a | Papers encontrados |
|---------------------|-----------|-------------------|
| "MPC" | control predictivo | 486 |
| "FCS-MPC" | control predictivo de conjunto finito | — |
| "PV" | fotovoltaica | 45 |
| "VSC" | convertidores de potencia | 250 |
| "MMC" | convertidor modular multinivel | — |
| "PLL" | lazo de seguimiento de fase | — |

### 2.4 Diagnóstico de calidad de datos

El sistema genera reportes automáticos que identifican:
- Papers sin abstract (4.939 papers)
- DOI duplicados (44 grupos)
- OpenAlex ID duplicados (45 grupos)
- Papers sin año (299)
- Papers fuera de dominio o sospechosos
- Keywords fragmentadas (40 grupos con 3+ variantes)

---

## 3. Qué NO puede hacer hoy (limitaciones)

### 3.1 Abstracts incompletos

**Problema:** Solo el 25,3% de los papers tienen abstract. Esto limita la calidad de las keywords generadas por IA para el 74,7% restante.

**Causa:** La API de OpenAlex (fuente principal de abstracts) tiene un presupuesto diario que se agotó durante la corrida. El presupuesto se restablece a medianoche UTC.

**Impacto:** Los papers sin abstract obtienen keywords inferidas solo del título, lo que reduce la precisión de la búsqueda semántica.

**Solución:** Correr el pipeline de abstracts cuando el presupuesto de OpenAlex se restablezca:
```bash
python main.py --only-abstracts --reprocess-abstracts
```

### 3.2 Google Scholar bloqueado

**Problema:** La librería `scholarly` (que scrapea Google Scholar) dejó de funcionar por una incompatibilidad con `httpx` en Python 3.14.

**Impacto:** No se pueden agregar nuevos investigadores vía Scholar ni usar Scholar como fuente alternativa de abstracts.

**Solución:** Downgrade a Python 3.12 o parchear `scholarly`.

### 3.3 Búsqueda aún no es fully semántica

**Problema:** La búsqueda usa normalización + aliases + ranking, pero no embeddings vectoriales. No entiende sinónimos que no están en la tabla de aliases.

**Ejemplo:** "weak grid" no encuentra "red débil" si no hay un alias explícito.

**Solución:** Agregar embeddings locales (multilingual-e5 o bge-m3) + sqlite-vec para búsqueda vectorial.

### 3.4 Sin RAG sobre PDFs

**Problema:** El sistema solo indexa metadata y abstracts. No puede responder preguntas sobre el contenido completo de un paper.

**Ejemplo:** No puede decir "¿qué método propone el paper X para reducir armónicos?"

**Solución:** Implementar pipeline de PDFs con Mistral OCR → chunks → embeddings → RAG.

### 3.5 Sin análisis de citaciones completo

**Problema:** Solo se extrajeron citantes de los top-5 papers más citados por investigador (acotado para evitar bloqueos de Scholar).

**Impacto:** El análisis de impacto y redes de citación es parcial.

**Solución:** Extracción masiva de citantes con pausas y caché.

---

## 4. Costos de IA

### 4.1 Modelo principal: MiMo v2.5 Pro (OpenCode Go)

| Concepto | Costo unitario | Uso estimado | Costo estimado |
|----------|---------------|--------------|----------------|
| Keywords (6.609 papers × 15 kw) | ~$0,001/paper | 6.609 papers | ~$6,60 |
| Chat agentic (~50 consultas/día) | ~$0,002/consulta | 50 consultas | ~$0,10/día |
| Normalización de keywords | ~$0,005/batch | 100 batches | ~$0,50 |
| **Total estimado (keywords + chat)** | | | **~$7,20** |

### 4.2 Modelo alternativo: Gemini

| Concepto | Costo unitario | Uso estimado | Costo estimado |
|----------|---------------|--------------|----------------|
| Gemini Flash Lite (chat) | Gratis (cuota generosa) | Ilimitado | $0 |
| Gemini Pro (si se necesita) | ~$0,00025/1K tokens | Variable | < $1/mes |

### 4.3 APIs externas (sin costo)

| API | Costo | Límite |
|-----|-------|--------|
| OpenAlex | Gratis | Presupuesto diario (~$5/día en requests) |
| Google Scholar | Gratis (scraping) | Sin API oficial; bloqueos frecuentes |
| ORCID | Gratis | Sin límite conocido |

### 4.4 Resumen de costos reales

| Componente | Costo total estimado |
|-----------|---------------------|
| Keywords LLM (6.609 papers) | ~$7 USD |
| Chat interactivo (50 consultas/día × 30 días) | ~$3 USD/mes |
| OpenAlex abstracts | $0 (gratis) |
| Infraestructura (local) | $0 |
| **Total primer mes** | **~$10 USD** |
| **Costo operativo mensual** | **~$3 USD/mes** |

**Nota:** Los costos son bajos porque usamos OpenCode Go como proxy centralizado. Si se usara OpenAI directamente, los costos serían ~10x mayores (~$100 para keywords).

---

## 5. Qué se necesitaría para un trabajo mayor

### 5.1 Escalabilidad

| Dimensión | Actual | Objetivo | Qué se necesita |
|-----------|--------|----------|----------------|
| Investigadores | 16 | 80-90 | Extracción automatizada, desambiguación de nombres |
| Papers | 6.609 | ~50.000 | Pipeline batch, priorización por impacto |
| Abstracts | 25,3% | >80% | OpenAlex + fallback IEEE/Scholar |
| Keywords | 44.454 | ~500.000 | LLM a escala, normalización automática |

### 5.2 Funcionalidades pendientes

| Funcionalidad | Complejidad | Impacto | Tiempo estimado |
|--------------|-------------|---------|-----------------|
| Completar abstracts (>80%) | Baja | Alto | 1-2 días |
| Normalización fuerte de keywords | Media | Alto | 2-3 días |
| Búsqueda vectorial (embeddings) | Media | Alto | 1 semana |
| RAG sobre PDFs (Mistral OCR) | Alta | Muy alto | 2-3 semanas |
| Extracción masiva de citaciones | Media | Medio | 1 semana |
| Interfaz web (Next.js) | Alta | Alto | 3-4 semanas |
| Dashboard de métricas | Media | Medio | 1 semana |
| Exportación a Neo4j | Baja | Bajo | 2-3 días |

### 5.3 Infraestructura necesaria

| Componente | Actual | Futuro |
|-----------|--------|--------|
| Base de datos | SQLite local | SQLite (suficiente hasta ~100K papers) |
| LLM | OpenCode Go (proxy) | OpenCode Go + fallback local |
| Embeddings | No implementado | Local: multilingual-e5 o bge-m3 |
| Vector store | No implementado | sqlite-vec (misma BD) |
| PDFs | No implementado | data/pdfs/ + Mistral OCR |
| Interfaz | Terminal (CLI) | Next.js + Vercel |

---

## 6. Justificación para financiar un trabajo mayor

### 6.1 Lo que ya demostramos

1. **El pipeline funciona a escala real**: 16 investigadores, 6.609 papers, 44.454 keywords, todo procesado automáticamente.

2. **El chat agentic responde preguntas reales**: no es un demo hardcodeado; el LLM planifica, ejecuta herramientas y genera respuestas basadas en datos.

3. **La búsqueda semántica con aliases funciona**: "MPC" → "control predictivo" (486 papers). Los acrónimos y variantes se resuelven automáticamente.

4. **El diagnóstico de calidad es automático**: el sistema detecta papers sospechosos, DOI duplicados, keywords fragmentadas y baja cobertura de abstracts.

5. **Los costos son bajos**: ~$10 USD para procesar 6.609 papers con keywords de IA. Costo operativo ~$3 USD/mes.

### 6.2 Lo que NO se puede hacer hoy (y por qué importa)

1. **No se puede buscar por contenido completo de papers**: solo metadata y abstracts. Sin RAG, el sistema no puede responder preguntas sobre metodologías, resultados o conclusiones.

2. **No se puede garantizar cobertura completa de abstracts**: el 74,7% de los papers no tiene abstract, lo que limita la calidad de las keywords.

3. **No se puede escalar a 90 investigadores sin automatizar**: la extracción manual de Scholar es lenta y bloquea. Necesitamos ORCID + OpenAlex como fuentes principales.

4. **No se puede hacer matching cross-lingüístico**: "weak grid" ≠ "red débil" sin embeddings o aliases manuales.

### 6.3 Propuesta de financiamiento

**Objetivo:** Convertir el prototipo actual en una herramienta de producción para el grupo LCDA.

**Fase 1 (1 mes): Base limpia y búsqueda semántica**
- Completar abstracts al 80%+
- Normalización fuerte de keywords (aliases automáticos)
- Búsqueda vectorial con embeddings locales
- Costo estimado: ~$20 USD en LLM + tiempo de desarrollo

**Fase 2 (2 meses): RAG y PDFs**
- Pipeline de PDFs con Mistral OCR
- Chunks + embeddings + FTS5
- Respuestas con citas por página
- Costo estimado: ~$50 USD en LLM + Mistral API

**Fase 3 (1 mes): Interfaz y presentación**
- Dashboard web con métricas
- Exportación de reportes
- Presentación para el profesor
- Costo estimado: ~$10 USD

**Costo total del proyecto (3-4 meses):** ~$80 USD en IA + tiempo de desarrollo.

**Comparación:** Hacer este mapeo manualmente tomaría ~4 horas por investigador × 90 investigadores = **360 horas hombre**. El pipeline lo hace en minutos.

---

## 7. Preguntas frecuentes

**¿Por qué no usamos OpenAI directamente?**  
Porque OpenCode Go ofrece los mismos modelos (MiMo v2.5 Pro, Gemini, DeepSeek) a través de un proxy centralizado con cuota compartida, lo que reduce costos ~10x.

**¿Por qué no usamos embeddings desde el inicio?**  
Porque la búsqueda por keywords normalizadas + aliases ya funciona bien para el 80% de las preguntas. Los embeddings son un complemento, no un reemplazo.

**¿Cuánto cuesta mantener el sistema?**  
~$3 USD/mes en LLM para el chat. La base de datos es local y no tiene costo. OpenAlex es gratis.

**¿Se puede usar con otros grupos de investigación?**  
Sí. El pipeline es genérico: solo cambia la lista de Scholar IDs en `config.yaml`.

**¿Qué tan confiables son los datos?**  
El sistema genera un reporte de calidad automático que identifica duplicados, papers sospechosos y baja cobertura. La confianza depende de la cobertura de abstracts (25,3% actualmente, objetivo 80%+).
