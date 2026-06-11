# Esquema de base de datos

Archivo: `data/lcda.db` (SQLite + WAL)

## Estado actual (junio 2026)

| Tabla | Registros |
|-------|-----------|
| `investigadores` | 16 |
| `papers` | ~6.500 (66% con abstract) |
| `keywords` | ~44.500 (38.900 normas únicas) |
| `paper_keywords` | ~96.000 vínculos |
| `keyword_aliases` | ~12.000 |
| `autorias` | ~7.500 |
| `citas` | 0 (crawling de citantes deshabilitado, ver FUENTES_DE_DATOS.md) |

## Diagrama conceptual

```
investigadores ──< autorias >── papers ──< paper_keywords >── keywords
       │                            │                              │
       │                            ├──< paper_autores         keyword_aliases
       └──< coautores               │
                                    ├── doi, url_doi, url_ieee, url_pdf
                                    └── abstract (texto completo)
```

## Tablas

### `investigadores`

| Campo | Descripción |
|-------|-------------|
| `scholar_id` | PK, ID Google Scholar |
| `nombre`, `afiliacion` | Perfil |
| `citas_total`, `indice_h`, `indice_i10` | Métricas Scholar |

### `papers`

| Campo | Fuente típica |
|-------|---------------|
| `titulo`, `anio`, `citado_por` | Scholar |
| `abstract` | IEEE Playwright / OpenAlex |
| `doi`, `url_doi` | IEEE Playwright / OpenAlex |
| `url_ieee` | IEEE Playwright / OpenAlex |
| `url_pdf` | IEEE Playwright |
| `url_scholar` | Scholar |
| `openalex_id` | OpenAlex |
| `venue` | OpenAlex / Scholar |
| `autores_texto` | Lista separada por comas |

**Dedup:** `upsert_paper` resuelve duplicados por DOI (primero), luego por `scholar_pub_id`, luego por título exacto. El DOI se normaliza a minúsculas para evitar falsos duplicados.

### `paper_autores`

Autores **por paper** con afiliación (OpenAlex).

### `coautores`

Red de coautores frecuentes del **perfil Scholar** (no por paper).

### `keywords` / `paper_keywords`

- **15 keywords** por paper (LLM + fallback local)
- `termino`: valor crudo generado por el LLM
- `keyword_norm`: forma normalizada (minúsculas, sin acentos, plurales colapsados, inglés→español). **Campo canónico para búsquedas.**
- `termino_canonico`: el término más representativo dentro del grupo `keyword_norm` (variante con más papers). Seteado por `scripts/fix_keywords.py`.
- `keyword_aliases`: mapeo alias→canónico (seeded por defecto + generado automáticamente).

**Importante:** todas las búsquedas temáticas usan `keyword_norm` (no `termino`). Para queries SQL: `WHERE k.keyword_norm LIKE ?` con el valor normalizado vía `normalize_keyword(term)`.

### `citas`

Tabla creada pero vacía. El crawling de citantes (`src/citations.py`) está deshabilitado por default (Scholar bloquea `citedby` a escala). Solo se activa con `--skip-no-citations` ausente y `top_papers > 0`.

### `tendencias_globales`

Cache OpenAlex por keyword_id y año.

### `pipeline_metricas`

Tiempos por paso: `extract`, `abstracts_ieee_pw`, `keywords`, `trends`, etc.

### `chat_sesiones` / `chat_mensajes`

Historial de conversaciones del agente. Ver `python main.py --history`.

## Índices

Creados automáticamente en `Database._migrate`:
- `idx_keywords_norm` — crítico para búsquedas temáticas
- `idx_papers_anio` — filtros por año
- `idx_papers_doi` — dedup por DOI
- `idx_paper_keywords_paper_id`, `idx_paper_keywords_keyword_id`
- `idx_autorias_scholar_id`, `idx_autorias_paper_id`

## Migraciones

`db.init_schema()` aplica `ALTER TABLE` para columnas nuevas en BDs existentes. Es seguro correr en una BD existente.

## Mantenimiento

```bash
# Re-consolidar keywords (tras añadir nuevos papers o cambiar normalize_keyword)
python scripts/fix_keywords.py --rebuild-norms --merge-pk

# Estadísticas de calidad
python main.py --chat  # luego: /calidad
```

## Consultas útiles

```sql
-- Cobertura de abstracts
SELECT COUNT(*) FROM papers WHERE abstract IS NOT NULL AND trim(abstract) != '';

-- Top keywords por concepto normalizado (sin duplicados de capitalización)
SELECT keyword_norm, COUNT(DISTINCT pk.paper_id) papers
FROM keywords k JOIN paper_keywords pk ON k.id=pk.keyword_id
GROUP BY keyword_norm ORDER BY papers DESC LIMIT 20;

-- Papers de un investigador sin abstract
SELECT p.titulo, p.anio FROM papers p
JOIN autorias a ON p.id=a.paper_id
JOIN investigadores i ON i.scholar_id=a.scholar_id
WHERE i.nombre LIKE '%Espinoza%'
  AND (p.abstract IS NULL OR trim(p.abstract)='')
ORDER BY p.citado_por DESC;
```
