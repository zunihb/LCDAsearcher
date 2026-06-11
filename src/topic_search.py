"""Normalización y búsqueda temática híbrida."""

from __future__ import annotations

import unicodedata
import re
from typing import Any

STOPWORDS = {
    "de", "la", "el", "en", "y", "con", "para", "del", "los", "las", "una",
    "por", "que", "se", "su", "al", "es", "lo", "como", "más", "o", "no",
    "un", "ya", "pero", "fue", "son", "está", "hay", "qué", "quien",
    "cuál", "dónde", "cómo", "cuando", "este", "esta", "estos", "estas",
    "that", "this", "these", "those", "with", "using", "based", "from",
}

# Keywords demasiado genéricas para trending/ranking — descriptores del campo,
# no temas de investigación específicos.
GENERIC_KEYWORDS: frozenset[str] = frozenset({
    "electronica de potencia",
    "electronica potencia",
    "ingenieria electrica",
    "ingenieria electronica",
    "conversion de energia",
    "conversion de potencia",
    "conversiones de energia",
    "sistema de potencia",
    "sistema de energia",
    "sistema de control",
    "sistemas de potencia",
    "eficiencia energetica",
    "energia electrica",
    "control avanzado",
    "control digital",
    "algoritmo de control",
    "estrategia de control",
    "control de potencia",
    "tecnica de control",
    "tecnica de modulacion",
    "metodo de control",
    "energia renovable",
    "fuentes de energia",
    "gestion de energia",
    "calidad de energia",
    "red electricas",
    "red electrica",
    "ingenieria de sistemas",
    "accionamiento electrico",
    "accionamientos electricos",
    "aplicaciones industriales",
    "sistema electrico",
    "simulacion numerica",
    "modelado matematico",
    "analisis de sistemas",
})


def normalize_text(text: str) -> str:
    text = (text or "").strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9\s\-+]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_keyword(text: str) -> str:
    text = normalize_text(text)
    text = text.replace("-", " ")

    # ── FASE 1: Frases compuestas inglés (deben ir ANTES de las reglas de palabras sueltas) ──

    # Control predictivo
    text = re.sub(r"\b(fcs\s*m?pc|fcs mpc)\b", "control predictivo de conjunto finito", text)
    text = re.sub(r"\bfinite[- ]?control[- ]?set\s+mpc\b", "control predictivo de conjunto finito", text)
    text = re.sub(r"\bmodel[ -]?predictive[ -]?control\b", "control predictivo modelo", text)

    # PWM y modulación (compuestos primero)
    text = re.sub(r"\bpulse[- ]?width\s+modulation\b", "modulacion pwm", text)
    text = re.sub(r"\bspace[- ]?vector\s+(pwm|modulation)\b", "modulacion vectorial espacial", text)
    text = re.sub(r"\bsvpwm\b", "modulacion vectorial espacial", text)
    text = re.sub(r"\bspwm\b", "modulacion pwm", text)
    text = re.sub(r"\bsvm\b", "modulacion vectorial espacial", text)

    # Máquinas (compuestos primero)
    text = re.sub(r"\bpermanent[- ]?magnet\s+synchronous\s+(motor|machine)\b", "motor sincrono iman permanente", text)
    text = re.sub(r"\bpmsm\b", "motor sincrono iman permanente", text)
    text = re.sub(r"\binduction\s+(motor|machine)\b", "motor de induccion", text)
    text = re.sub(r"\bswitched\s+reluctance\s+(motor|machine)\b", "motor reluctancia conmutada", text)

    # Convertidores compuestos
    text = re.sub(r"\bmodular multilevel converter\b", "convertidor multinivel modular", text)
    text = re.sub(r"\bmmc\b", "convertidor multinivel modular", text)
    text = re.sub(r"\bmatrix converter(s)?\b", "convertidor matricial", text)
    text = re.sub(r"\bmultilevel (inverter|converter)(s)?\b", "inversor multinivel", text)
    text = re.sub(r"\bvoltage source inverter(s)?\b", "inversor fuente de voltaje", text)
    text = re.sub(r"\bcurrent source inverter(s)?\b", "inversor fuente de corriente", text)
    text = re.sub(r"\bactive power filter(s)?\b", "filtro activo de potencia", text)
    text = re.sub(r"\bshunt active (power )?filter\b", "filtro activo de potencia", text)
    text = re.sub(r"\b(grid connected|grid-connected)\b", "conectado a red", text)

    # Energías y aplicaciones compuestas
    text = re.sub(r"\brenewable energy\b", "energia renovable", text)
    text = re.sub(r"\bwind energy\b", "energia eolica", text)
    text = re.sub(r"\bsolar energy\b", "energia solar", text)
    text = re.sub(r"\benergy storage\b", "almacenamiento de energia", text)
    text = re.sub(r"\belectric vehicle(s)?\b", "vehiculo electrico", text)
    text = re.sub(r"\bpower factor\b", "factor de potencia", text)
    text = re.sub(r"\bpower electronics?\b", "electronica de potencia", text)
    text = re.sub(r"\belectric(al)? machines?\b", "maquinas electricas", text)

    # ── FASE 2: Palabras sueltas inglés → español (después de compuestos) ──
    text = re.sub(r"\bphotovoltaic(s)?\b", "fotovoltaica", text)
    text = re.sub(r"\bconverter(s)?\b", "convertidor", text)
    text = re.sub(r"\bcontrollers?\b", "control", text)
    text = re.sub(r"\bsystems?\b", "sistema", text)
    text = re.sub(r"\bharmonic(s)?\b", "armonicos", text)
    text = re.sub(r"\bdrive(s)?\b", "accionamiento", text)
    text = re.sub(r"\binverter(s)?\b", "inversor", text)
    text = re.sub(r"\brectifier(s)?\b", "rectificador", text)
    text = re.sub(r"\btransformer(s)?\b", "transformador", text)
    text = re.sub(r"\bpermanent magnet\b", "iman permanente", text)
    text = re.sub(r"\bmpc\b", "control predictivo", text)

    # ── FASE 3: Plurales español → singular ───────────────────────────
    text = re.sub(r"\bconvertidores\b", "convertidor", text)
    text = re.sub(r"\bconversor(es)?\b", "convertidor", text)
    text = re.sub(r"\binversores\b", "inversor", text)
    text = re.sub(r"\brectificadores\b", "rectificador", text)
    text = re.sub(r"\btransformadores\b", "transformador", text)
    text = re.sub(r"\baccionamientos\b", "accionamiento", text)
    text = re.sub(r"\bmotores\b", "motor", text)
    text = re.sub(r"\bgeneradores\b", "generador", text)
    text = re.sub(r"\bfiltros\b", "filtro", text)
    text = re.sub(r"\balgoritmos\b", "algoritmo", text)
    text = re.sub(r"\bsistemas\b", "sistema", text)
    text = re.sub(r"\bredes\b", "red", text)
    text = re.sub(r"\benergias\b", "energia", text)
    text = re.sub(r"\btopologias\b", "topologia", text)
    text = re.sub(r"\bestrateg(ias|ia)\b", "estrategia", text)
    text = re.sub(r"\btecnicas\b", "tecnica", text)
    text = re.sub(r"\bmetodos\b", "metodo", text)
    text = re.sub(r"\bmodelos\b", "modelo", text)
    text = re.sub(r"\bpaneles\b", "panel", text)
    text = re.sub(r"\bvehiculos\b", "vehiculo", text)
    text = re.sub(r"\bmaquinas\b", "maquina", text)

    # ── FASE 4: Variantes españolas compuestas ────────────────────────
    # Motor síncrono — normalizar variantes españolas
    text = re.sub(r"\bmotor\s+s[iy]ncrono\s+(de\s+)?iman\s+permanente\b", "motor sincrono iman permanente", text)

    # Convertidor multinivel modular — variantes españolas
    text = re.sub(r"\bconvertidor(es)?\s+multinivel(es)?\s+modular(es)?\b", "convertidor multinivel modular", text)

    # Motor de inducción — variantes españolas
    text = re.sub(r"\bmaquina\s+(de\s+)?induccion\b", "motor de induccion", text)
    text = re.sub(r"\bmaquina\s+asincron[ao]\b", "motor de induccion", text)

    # Filtro activo — variantes españolas
    text = re.sub(r"\bfiltro\s+activo(\s+(de\s+potencia|paralelo|serie|hibrido))?\b",
                  "filtro activo de potencia", text)

    # Control predictivo MPC — colapsar variantes con y sin "modelo"
    text = re.sub(r"\bcontrol predictivo\s+(basado en modelo|de modelos?|por modelo)\b",
                  "control predictivo modelo", text)

    # PWM variantes españolas
    text = re.sub(r"\bmodulacion\s+(por\s+|de\s+)?ancho\s+(de\s+)?pulso\b", "modulacion pwm", text)

    # ── FASE 5: Abreviaciones topológicas ─────────────────────────────
    text = re.sub(r"\b(dc[ /]dc|cc[ /]cc)\b", "dc dc", text)
    text = re.sub(r"\b(ac[ /]dc|ca[ /]cc)\b", "ac dc", text)
    text = re.sub(r"\b(dc[ /]ac|cc[ /]ca)\b", "dc ac", text)
    text = re.sub(r"\b(ac[ /]ac|ca[ /]ca)\b", "ac ac", text)

    text = re.sub(r"\s+", " ", text).strip()
    return text


def tokenize_query(text: str) -> list[str]:
    norm = normalize_text(text)
    tokens = []
    for token in norm.split():
        if len(token) < 3 or token in STOPWORDS:
            continue
        tokens.append(token)
    return tokens


def load_keyword_aliases(db) -> dict[str, str]:
    rows = db.get_keyword_aliases()
    return {r["alias"]: r["canonical"] for r in rows}


def resolve_keyword_alias(db, keyword: str) -> str:
    norm = normalize_keyword(keyword)
    aliases = load_keyword_aliases(db)
    return aliases.get(norm, norm)


def search_keywords_hybrid(db, term: str, limit: int = 15) -> list[dict[str, Any]]:
    resolved = resolve_keyword_alias(db, term)
    tokens = tokenize_query(resolved)
    if not tokens:
        return []

    # Agrupar por keyword_norm para eliminar variantes duplicadas
    rows = db.query(
        """
        SELECT
            k.keyword_norm AS keyword_norm,
            COUNT(DISTINCT pk.paper_id) AS papers,
            SUM(COALESCE(p.citado_por, 0)) AS citas,
            MAX(p.anio) AS ultimo_anio
        FROM keywords k
        JOIN paper_keywords pk ON k.id = pk.keyword_id
        JOIN papers p ON p.id = pk.paper_id
        WHERE k.keyword_norm IS NOT NULL
        GROUP BY k.keyword_norm
        """
    )

    scored: list[dict[str, Any]] = []
    for row in rows:
        kn = row["keyword_norm"]
        hits = sum(1 for t in tokens if t in kn)
        if not hits:
            continue
        common_penalty = 1.0 / max(1.0, (row["papers"] or 1) ** 0.35)
        score = (hits * 2.5) + (row["papers"] or 0) * 0.08 + ((row["citas"] or 0) ** 0.25) + common_penalty
        scored.append({
            "keyword": kn,
            "keyword_norm": kn,
            "papers": row["papers"],
            "citas": row["citas"],
            "ultimo_anio": row["ultimo_anio"],
            "score": round(score, 3),
        })

    scored.sort(key=lambda r: (r["score"], r["papers"], r["citas"]), reverse=True)
    return scored[:limit]


def keyword_growth_proxy(db, keyword: str) -> float:
    rows = db.query(
        """
        SELECT p.anio, COUNT(*) AS conteo
        FROM papers p
        JOIN paper_keywords pk ON pk.paper_id = p.id
        JOIN keywords k ON k.id = pk.keyword_id
        WHERE COALESCE(k.termino_canonico, k.termino, k.keyword_norm) LIKE ?
          AND p.anio IS NOT NULL
        GROUP BY p.anio
        ORDER BY p.anio
        """,
        (f"%{normalize_keyword(keyword)}%",),
    )
    if not rows:
        return 0.0
    series = [float(r["conteo"]) for r in rows]
    if len(series) < 2:
        return 0.0
    from src.trends import _slope

    return _slope(series)


def seed_default_keyword_aliases(db) -> None:
    defaults = {
        "mpc": "control predictivo",
        "model predictive control": "control predictivo",
        "fcs mpc": "control predictivo de conjunto finito",
        "fcs-mpc": "control predictivo de conjunto finito",
        "photovoltaic": "fotovoltaica",
        "grid connected": "conectado a red",
        "grid-connected": "conectado a red",
    }
    for alias, canonical in defaults.items():
        db.upsert_keyword_alias(alias, canonical, fuente="default")
