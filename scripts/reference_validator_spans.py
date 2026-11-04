"""Referencia (solo stdlib) de la API de validacion con spans para el Arena Replay.

Que hace
    Replica en modo "legacy_parity" las reglas de backend/app/legal/neuro_symbolic_validator.py
    (sha256 9a810e1f...) pero devuelve, por cada hallazgo, el texto exacto y su posicion
    (char_start, char_end) en lugar de un conteo. Los regex numericos se copian literalmente
    del validador heredado para poder probar paridad (ver audit_validator_fp.py).

Que NO hace
    No corrige los falsos positivos conocidos. Esa correccion esta especificada en el README
    y debe hacerse en el validador del repo antes de la recaptura. Por eso el estado que
    produce es "reference_provisional", nunca "verified".

Convenciones de offsets
    El texto se normaliza a NFC antes de buscar. Los offsets son unidades de codigo UTF-16
    (lo que usa String.prototype.slice en JS), intervalo semiabierto [char_start, char_end).
"""

import re
import unicodedata

LEGACY_LAT_RE = r"(?:lat(?:itud)?[:\s]+(-?\d{1,2}(?:\.\d{1,6})?)|(-?\d{1,2}(?:\.\d{1,6})?)\s*°?\s*([sSnN])(?![mM]))"
LEGACY_LON_RE = r"(?:lon(?:gitud)?[:\s]+(-?\d{1,3}(?:\.\d{1,6})?)|(-?\d{1,3}(?:\.\d{1,6})?)\s*°?\s*([wWoOeE])(?!gs))"
LEGACY_DIST_RE = r"(\d+(?:\.\d+)?)\s*(?:NM|millas?\s*(?:n[aá]uticas?)?)"
LEGACY_LEN_RE = r"(\d+(?:\.\d+)?)\s*(?:m|metros?)(?:\s*(?:de\s*)?eslora)?"
LEGACY_RCS_RE = r"(\d+(?:\.\d+)?)\s*(?:dB|decibelios?)"
LEGACY_MILLONES_RE = r"(\d+(?:\.\d+)?)\s*millones?"

TOLERANCE_NM = 0.5
TOLERANCE_COORD = 0.05
TOLERANCE_DIMENSION = 5.0

SEVERITY_UI = {
    "CRITICAL_COORDINATE_HALLUCINATION": "critical",
    "BOUNDARY_DISTANCE_HALLUCINATION": "high",
    "VESSEL_DIMENSION_HALLUCINATION": "medium",
    "FALSE_EXONERATION_CONTRADICTION": "high",
    "JURISDICTION_ZONE_CONFUSION": "medium",
    "PENALTY_EXAGGERATION_HALLUCINATION": "high",
    "FABRICATED_MILITARY_VESSEL_TYPE": "critical",
    "DEFAMATORY_LEGAL_HALLUCINATION": "critical",
}


def normalize(text):
    return unicodedata.normalize("NFC", text)


def utf16_offset(text, cp_index):
    """Indice en code points -> indice en unidades UTF-16."""
    return len(text[:cp_index].encode("utf-16-le")) // 2


def _span(text, m_start, m_end):
    return utf16_offset(text, m_start), utf16_offset(text, m_end)


def extract_claims(text):
    """Devuelve lista de claims {kind, value, start, end, text}; start/end en code points."""
    claims = []

    def add(kind, value, m):
        claims.append({"kind": kind, "value": value, "start": m.start(), "end": m.end(), "text": m.group(0)})

    for m in re.finditer(LEGACY_LAT_RE, text, re.IGNORECASE):
        s = m.group(1) or m.group(2)
        if s:
            v = float(s)
            if m.group(3) and m.group(3).upper() == "S" and v > 0:
                v = -v
            add("lat", v, m)
    for m in re.finditer(LEGACY_LON_RE, text, re.IGNORECASE):
        s = m.group(1) or m.group(2)
        if s:
            v = float(s)
            if m.group(3) and m.group(3).upper() in ("W", "O") and v > 0:
                v = -v
            add("lon", v, m)
    for m in re.finditer(LEGACY_DIST_RE, text, re.IGNORECASE):
        add("dist", float(m.group(1)), m)
    for m in re.finditer(LEGACY_LEN_RE, text, re.IGNORECASE):
        add("len", float(m.group(1)), m)
    for m in re.finditer(LEGACY_RCS_RE, text, re.IGNORECASE):
        add("rcs", float(m.group(1)), m)
    for m in re.finditer(LEGACY_MILLONES_RE, text, re.IGNORECASE):
        add("pen", float(m.group(1)) * 1_000_000.0, m)
    return claims


def _first(text, phrases):
    best = None
    for p in phrases:
        m = re.search(re.escape(p), text, re.IGNORECASE)
        if m and (best is None or m.start() < best.start()):
            best = m
    return best


def validate_with_spans(raw_text, ev):
    """Valida `raw_text` contra la evidencia `ev` y devuelve (texto_normalizado, findings).

    ev: dict con lat, lon, distance_to_eez_nm, length_m, classification, zone (valor del enum),
        infraction_code, max_penalty_usd (o None). Tambien acepta directamente el objeto
        scenario.ground_truth del JSON (claves jurisdiction_zone y economic_penalty_max_usd).
    """
    ev = dict(ev)
    ev.setdefault("zone", ev.get("jurisdiction_zone"))
    ev.setdefault("max_penalty_usd", ev.get("economic_penalty_max_usd"))
    ev.setdefault("infraction_code", ev["classification"])
    text = normalize(raw_text)
    out = []

    def emit(rule_id, field, expected, observed, start, end, legacy_sev):
        s, e = _span(text, start, end)
        out.append({
            "rule_id": rule_id, "field": field, "expected": expected, "observed": observed,
            "matched_text": text[start:end], "char_start": s, "char_end": e,
            "severity": SEVERITY_UI[legacy_sev], "legacy_severity": legacy_sev,
        })

    claims = extract_claims(text)
    expected_dist = abs(ev["distance_to_eez_nm"])
    for c in claims:
        k, v = c["kind"], c["value"]
        if k == "lat" and abs(v - ev["lat"]) > TOLERANCE_COORD:
            emit("NUM-LAT-001", "latitude", ev["lat"], v, c["start"], c["end"], "CRITICAL_COORDINATE_HALLUCINATION")
        elif k == "lon" and abs(v - ev["lon"]) > TOLERANCE_COORD:
            emit("NUM-LON-001", "longitude", ev["lon"], v, c["start"], c["end"], "CRITICAL_COORDINATE_HALLUCINATION")
        elif k == "dist" and abs(v - expected_dist) > TOLERANCE_NM:
            emit("NUM-DIST-001", "distance_to_eez_nm", expected_dist, v, c["start"], c["end"], "BOUNDARY_DISTANCE_HALLUCINATION")
        elif k == "len" and ev["length_m"] > 0.0 and abs(v - ev["length_m"]) > TOLERANCE_DIMENSION:
            emit("NUM-LEN-001", "length_m", ev["length_m"], v, c["start"], c["end"], "VESSEL_DIMENSION_HALLUCINATION")
        elif k == "pen" and ev.get("max_penalty_usd") and v > ev["max_penalty_usd"] * 2.0:
            emit("SEM-PEN-001", "economic_penalty_usd", ev["max_penalty_usd"], v, c["start"], c["end"], "PENALTY_EXAGGERATION_HALLUCINATION")

    if ev["distance_to_eez_nm"] < -2.0 and ev["classification"] == "DARK_VESSEL":
        m = _first(text, ["navegación normal", "tránsito conforme", "paso inocente"])
        if m:
            emit("SEM-EXON-001", "legal_characterization", ev["infraction_code"], "navegación normal", m.start(), m.end(), "FALSE_EXONERATION_CONTRADICTION")

    if ev["zone"] != "MAR_TERRITORIAL_12NM":
        m = _first(text, ["12 millas", "mar territorial"])
        if m:
            emit("SEM-ZONE-001", "jurisdiction_zone", ev["zone"], "mar territorial", m.start(), m.end(), "JURISDICTION_ZONE_CONFUSION")

    m = _first(text, ["submarino"])
    if m:
        emit("SEM-VESSEL-001", "vessel_type", ev["classification"], "submarino", m.start(), m.end(), "FABRICATED_MILITARY_VESSEL_TYPE")

    if ev["classification"] == "COOPERATIVE_VESSEL":
        m = _first(text, ["piratería"])
        if m:
            emit("SEM-DEFAM-001", "infraction", "CUMPLIMIENTO_NORMATIVO", "piratería", m.start(), m.end(), "DEFAMATORY_LEGAL_HALLUCINATION")

    out.sort(key=lambda f: (f["char_start"], f["rule_id"]))
    return text, out


def adherence_from_count(n):
    """Misma formula que el validador heredado: max(0, 100 - 15n). Es una penalizacion, no exactitud."""
    return 100.0 if n == 0 else max(0.0, 100.0 - 15.0 * n)
