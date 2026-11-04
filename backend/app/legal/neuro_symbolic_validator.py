"""
Neuro-Symbolic Legal Consistency & Anti-Hallucination Validator.

Provides both:
- `validate_with_spans(raw_text, evidence, ...)` -> `(text_nfc, findings)` returning exact
  UTF-16 `[char_start, char_end)` spans matching `specs/arena_replay/arena_replay.schema.json`.
- `validate_legal_narrative(raw_narrative, evidence, ...)` -> `NeuroSymbolicValidationResult`.

Implements fixes F1-F6 from `specs/arena_replay/README.md`:
- F1: Hemisphere token boundaries (`(?<![\\w.-])`, `(?!\\d)`, `(?![^\\W\\d_])`) preventing
      false positives on IDs (`TC-EEZ-001 en`, `1SDV`) and Spanish words (`-57.1 se`, `Milla 200 en`).
- F2: Unit boundary on `m` (`(?![^\\W\\d_])`) so `"12 millas"` never matches `"12 m"`, and
      exclusion of legal boundary phrases (`"de mar territorial"`, `"de zona"`).
- F3: Sentence-level negation/refutation guard (`"no"`, `"descarta"`, `"incompatible con"`,
      `"sin"`, `"ni"`) for semantic rules (`SEM-EXON-001`, `SEM-ZONE-001`, `SEM-VESSEL-001`,
      `SEM-DEFAM-001`).
- F4: RCS validation against `peak_rcs_db` (`NUM-RCS-001`, default tolerance `0.5 dB`).
- F5: Whitespace trimming on `matched_text` and equal-length masking of IDs (`TC-XXX-NNN`,
      `SAR-TGT-NNN`, `S1A_IW_...`) before extraction so UTF-16 offsets never shift.
- F6: `adherence_percent = max(0.0, 100.0 - 15.0 * len(findings))` treated as a penalty score.
"""

import re
import time
import unicodedata
from typing import Dict, Any, List, Optional, Tuple, Union
from pydantic import BaseModel, Field

from app.legal.rules_engine import LegalVerdict, evaluate_maritime_case, MaritimeJurisdictionZone


VALIDATOR_VERSION = "neuro-symbolic-spans-2.1.0"

SEVERITY_MAP = {
    "CRITICAL_COORDINATE_HALLUCINATION": "critical",
    "BOUNDARY_DISTANCE_HALLUCINATION": "high",
    "VESSEL_DIMENSION_HALLUCINATION": "medium",
    "RCS_SIGNATURE_HALLUCINATION": "medium",
    "FALSE_EXONERATION_CONTRADICTION": "critical",
    "JURISDICTION_ZONE_CONFUSION": "high",
    "PENALTY_EXAGGERATION_HALLUCINATION": "medium",
    "FABRICATED_MILITARY_VESSEL_TYPE": "critical",
    "DEFAMATORY_LEGAL_HALLUCINATION": "critical",
}

# Equal-length masking patterns (ASCII only -> 1 char == 1 UTF-16 code unit)
MASK_ID_PATTERNS = [
    re.compile(r"\bTC-[A-Z]+-\d+\b"),
    re.compile(r"\bSAR-TGT-\d+\b"),
    re.compile(r"\bS1[AB]_IW_[A-Z0-9_]+\b"),
    re.compile(r"\b1SDV\b", re.IGNORECASE),
]

# F1: Coordinate regexes with strict token boundaries
FIXED_LAT_RE = re.compile(
    r"(?<![\w.-])(?:"
    r"lat(?:itud)?[:\s]+([+-]?\d{1,2}(?:\.\d+)?)(?!\d)"
    r"|"
    r"([+-]?\d{1,2}(?:\.\d+)?)(?!\d)\s*°?\s*([sSnN])(?![^\W\d_])"
    r")",
    re.IGNORECASE,
)

FIXED_LON_RE = re.compile(
    r"(?<![\w.-])(?:"
    r"lon(?:gitud)?[:\s]+([+-]?\d{1,3}(?:\.\d+)?)(?!\d)"
    r"|"
    r"([+-]?\d{1,3}(?:\.\d+)?)(?!\d)\s*(?:°\s*([wWoOeE])|([wWeE]|O))(?![^\W\d_])"
    r")",
    re.IGNORECASE,
)

# F2: Distance and length regexes with unit boundaries and legal-zone exclusions
FIXED_DIST_RE = re.compile(
    r"(?<![\w.-])(\d+(?:\.\d+)?)(?!\d)\s*(?:NM|millas?(?:\s*n[aá]uticas?)?)(?![^\W\d_])"
    r"(?!\s+de\s+(?:mar\s+territorial|zona\b))",
    re.IGNORECASE,
)

FIXED_LEN_RE = re.compile(
    r"(?<![\w.-])(\d+(?:\.\d+)?)(?!\d)\s*(?:metros?(?:\s+de\s+eslora)?|m(?:\s+de\s+eslora)?)(?![^\W\d_])"
    r"(?!\s+de\s+manga\b)",
    re.IGNORECASE,
)

# F4: RCS regex
FIXED_RCS_RE = re.compile(
    r"(?<![\w.-])([+-]?\d+(?:\.\d+)?)(?!\d)\s*(?:dB|decibelios?)(?![^\W\d_])",
    re.IGNORECASE,
)

FIXED_MILLONES_RE = re.compile(
    r"(?<![\w.-])(\d+(?:\.\d+)?)(?!\d)\s*millones?(?![^\W\d_])",
    re.IGNORECASE,
)

# Legacy regexes (for optional legacy_parity=True)
LEGACY_LAT_RE = re.compile(
    r"(?:lat(?:itud)?[:\s]+(-?\d{1,2}(?:\.\d{1,6})?)|(-?\d{1,2}(?:\.\d{1,6})?)\s*°?\s*([sSnN])(?![mM]))",
    re.IGNORECASE,
)
LEGACY_LON_RE = re.compile(
    r"(?:lon(?:gitud)?[:\s]+(-?\d{1,3}(?:\.\d{1,6})?)|(-?\d{1,3}(?:\.\d{1,6})?)\s*°?\s*([wWoOeE])(?!gs))",
    re.IGNORECASE,
)
LEGACY_DIST_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:NM|millas?\s*(?:n[aá]uticas?)?)",
    re.IGNORECASE,
)
LEGACY_LEN_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:m|metros?)(?:\s*(?:de\s*)?eslora)?",
    re.IGNORECASE,
)
LEGACY_MILLONES_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*millones?",
    re.IGNORECASE,
)

NEGATION_CUE_RE = re.compile(
    r"\b(?:no|sin|ni|nunca|jam[aá]s|descarta\w*|excluye\w*|incompatible\s+con|lejos\s+de|ausencia\s+de|carece\s+de|tampoco)\b",
    re.IGNORECASE,
)


class EvidenceVector(BaseModel):
    target_id: str
    lat: float
    lon: float
    length_m: float
    beam_m: float
    peak_rcs_db: float
    snr_db: float
    distance_to_eez_nm: float
    classification: str
    boundary_status: str
    legal_verdict: LegalVerdict


class ValidationDiscrepancy(BaseModel):
    metric_name: str
    llm_stated_value: Any
    ground_truth_value: Any
    absolute_error: float
    error_severity: str
    rule_id: Optional[str] = None
    char_start: Optional[int] = None
    char_end: Optional[int] = None
    matched_text: Optional[str] = None


class NeuroSymbolicValidationResult(BaseModel):
    is_valid: bool
    hallucination_detected: bool
    discrepancies: List[ValidationDiscrepancy]
    discrepancy_count: int
    ground_truth_adherence_percent: float
    sanitized_legal_text: str
    validation_latency_ms: float
    findings: List[Dict[str, Any]] = Field(default_factory=list)
    validator_version: str = VALIDATOR_VERSION
    certifying_authority: str = "OceanEye Neuro-Symbolic Legal Validator v2.1"


def normalize_nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def _py_to_u16_table(s: str) -> List[int]:
    out = [0]
    acc = 0
    for ch in s:
        acc += 2 if ord(ch) > 0xFFFF else 1
        out.append(acc)
    return out


def _mask_identifiers(text: str) -> str:
    masked = text
    for pat in MASK_ID_PATTERNS:
        masked = pat.sub(lambda m: " " * (m.end() - m.start()), masked)
    return masked


def _trim_span(text: str, py_start: int, py_end: int) -> Tuple[int, int]:
    while py_start < py_end and text[py_start].isspace():
        py_start += 1
    while py_end > py_start and text[py_end - 1].isspace():
        py_end -= 1
    return py_start, py_end


def _is_negated_in_sentence(text: str, match_start: int, match_end: int) -> bool:
    """
    F3: Check if the sentence containing [match_start, match_end) negates or refutes the term.
    """
    sent_start = 0
    for i in range(match_start - 1, -1, -1):
        if text[i] in ".;\n":
            sent_start = i + 1
            break
    sent_end = len(text)
    for i in range(match_end, len(text)):
        if text[i] in ".;\n":
            sent_end = i
            break
    prefix = text[sent_start:match_start]
    sentence = text[sent_start:sent_end]
    if NEGATION_CUE_RE.search(prefix):
        return True
    # Also handle post-positioned refutation in the same sentence, e.g. "hipótesis de piratería descartada"
    suffix = text[match_end:sent_end]
    if re.search(r"\b(?:descartad[ao]s?|excluid[ao]s?|inexistente|falso|falsa)\b", suffix, re.IGNORECASE):
        return True
    return False


def _find_semantic_match(
    text: str,
    phrases: List[str],
    check_negation: bool = True,
) -> Optional[Tuple[int, int]]:
    low = text.lower()
    best: Optional[Tuple[int, int]] = None
    for p in phrases:
        pl = p.lower()
        start = 0
        while True:
            idx = low.find(pl, start)
            if idx < 0:
                break
            end = idx + len(pl)
            if check_negation and _is_negated_in_sentence(text, idx, end):
                start = end
                continue
            if best is None or idx < best[0]:
                best = (idx, end)
            break
    return best


def _coerce_evidence_dict(evidence: Union[EvidenceVector, Dict[str, Any]]) -> Dict[str, Any]:
    if isinstance(evidence, dict):
        zone = evidence.get("zone") or evidence.get("jurisdiction_zone", "")
        if hasattr(zone, "value"):
            zone = zone.value
        return {
            "lat": float(evidence["lat"]),
            "lon": float(evidence["lon"]),
            "distance_to_eez_nm": float(evidence["distance_to_eez_nm"]),
            "length_m": float(evidence.get("length_m", 0.0)),
            "peak_rcs_db": float(evidence.get("peak_rcs_db", 0.0)),
            "classification": str(evidence["classification"]),
            "zone": str(zone),
            "infraction_code": str(evidence.get("infraction_code", "")),
            "max_penalty_usd": evidence.get("max_penalty_usd", evidence.get("economic_penalty_max_usd")),
        }
    zone_val = (
        evidence.legal_verdict.jurisdiction_zone.value
        if hasattr(evidence.legal_verdict.jurisdiction_zone, "value")
        else str(evidence.legal_verdict.jurisdiction_zone)
    )
    max_pen = None
    if evidence.legal_verdict.economic_penalty_range_usd:
        max_pen = evidence.legal_verdict.economic_penalty_range_usd[1]
    return {
        "lat": float(evidence.lat),
        "lon": float(evidence.lon),
        "distance_to_eez_nm": float(evidence.distance_to_eez_nm),
        "length_m": float(evidence.length_m),
        "peak_rcs_db": float(evidence.peak_rcs_db),
        "classification": str(evidence.classification),
        "zone": zone_val,
        "infraction_code": str(evidence.legal_verdict.infraction_code),
        "max_penalty_usd": max_pen,
    }


def validate_with_spans(
    raw_text: str,
    evidence: Union[EvidenceVector, Dict[str, Any]],
    *,
    tolerance_nm: float = 0.5,
    tolerance_coord: float = 0.05,
    tolerance_dimension: float = 5.0,
    tolerance_rcs: float = 0.5,
    legacy_parity: bool = False,
) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Validate narrative against ground-truth evidence vector and return:
        (text_nfc, findings)
    where each finding has:
        rule_id, field, expected, observed, matched_text, char_start, char_end (UTF-16),
        severity, legacy_severity.
    """
    text = normalize_nfc(raw_text)
    u16 = _py_to_u16_table(text)
    ev = _coerce_evidence_dict(evidence)
    out: List[Dict[str, Any]] = []

    search_text = text if legacy_parity else _mask_identifiers(text)

    def emit(
        rule_id: str,
        field: str,
        expected: Any,
        observed: Any,
        py_start: int,
        py_end: int,
        legacy_sev: str,
    ) -> None:
        if not legacy_parity:
            py_start, py_end = _trim_span(text, py_start, py_end)
        cs, ce = u16[py_start], u16[py_end]
        out.append(
            {
                "rule_id": rule_id,
                "field": field,
                "expected": expected,
                "observed": observed,
                "matched_text": text[py_start:py_end],
                "char_start": cs,
                "char_end": ce,
                "severity": SEVERITY_MAP[legacy_sev],
                "legacy_severity": legacy_sev,
            }
        )

    # 1. Latitude (F1)
    lat_re = LEGACY_LAT_RE if legacy_parity else FIXED_LAT_RE
    for m in lat_re.finditer(search_text):
        val_str = m.group(1) or m.group(2)
        if not val_str:
            continue
        v = float(val_str)
        hemi = m.group(3)
        if hemi and hemi.upper() == "S" and v > 0:
            v = -v
        if abs(v - ev["lat"]) > tolerance_coord:
            emit("NUM-LAT-001", "latitude", ev["lat"], v, m.start(), m.end(), "CRITICAL_COORDINATE_HALLUCINATION")

    # 2. Longitude (F1)
    lon_re = LEGACY_LON_RE if legacy_parity else FIXED_LON_RE
    for m in lon_re.finditer(search_text):
        val_str = m.group(1) or m.group(2)
        if not val_str:
            continue
        v = float(val_str)
        hemi = m.group(3) if legacy_parity else (m.group(3) or m.group(4))
        if hemi and hemi.upper() in ("W", "O") and v > 0:
            v = -v
        if abs(v - ev["lon"]) > tolerance_coord:
            emit("NUM-LON-001", "longitude", ev["lon"], v, m.start(), m.end(), "CRITICAL_COORDINATE_HALLUCINATION")

    # 3. Distance to EEZ (F2)
    exp_dist = abs(ev["distance_to_eez_nm"])
    dist_re = LEGACY_DIST_RE if legacy_parity else FIXED_DIST_RE
    for m in dist_re.finditer(search_text):
        v = float(m.group(1))
        if abs(v - exp_dist) > tolerance_nm:
            emit("NUM-DIST-001", "distance_to_eez_nm", exp_dist, v, m.start(), m.end(), "BOUNDARY_DISTANCE_HALLUCINATION")

    # 4. Vessel length (F2)
    if ev["length_m"] > 0.0:
        len_re = LEGACY_LEN_RE if legacy_parity else FIXED_LEN_RE
        for m in len_re.finditer(search_text):
            v = float(m.group(1))
            if abs(v - ev["length_m"]) > tolerance_dimension:
                emit("NUM-LEN-001", "length_m", ev["length_m"], v, m.start(), m.end(), "VESSEL_DIMENSION_HALLUCINATION")

    # 5. RCS signature (F4 — enabled in fixed mode)
    if not legacy_parity and ev.get("peak_rcs_db", 0.0) > 0.0:
        for m in FIXED_RCS_RE.finditer(search_text):
            v = float(m.group(1))
            if abs(v - ev["peak_rcs_db"]) > tolerance_rcs:
                emit("NUM-RCS-001", "peak_rcs_db", ev["peak_rcs_db"], v, m.start(), m.end(), "RCS_SIGNATURE_HALLUCINATION")

    # 6. Penalty exaggeration
    max_pen = ev.get("max_penalty_usd")
    if max_pen:
        pen_re = LEGACY_MILLONES_RE if legacy_parity else FIXED_MILLONES_RE
        for m in pen_re.finditer(search_text):
            pen = float(m.group(1)) * 1_000_000.0
            if pen > max_pen * 2.0:
                emit("SEM-PEN-001", "economic_penalty_usd", max_pen, pen, m.start(), m.end(), "PENALTY_EXAGGERATION_HALLUCINATION")

    # 7. Semantic rules with sentence negation check (F3)
    check_neg = not legacy_parity

    if ev["distance_to_eez_nm"] < -2.0 and ev["classification"] == "DARK_VESSEL":
        span = _find_semantic_match(text, ["navegación normal", "tránsito conforme", "paso inocente"], check_negation=check_neg)
        if span:
            emit("SEM-EXON-001", "legal_characterization", ev["infraction_code"], "navegación normal", span[0], span[1], "FALSE_EXONERATION_CONTRADICTION")

    if ev["zone"] != "MAR_TERRITORIAL_12NM" and ev["zone"] != MaritimeJurisdictionZone.TERRITORIAL_SEA.value:
        span = _find_semantic_match(text, ["12 millas", "mar territorial"], check_negation=check_neg)
        if span:
            emit("SEM-ZONE-001", "jurisdiction_zone", ev["zone"], "mar territorial", span[0], span[1], "JURISDICTION_ZONE_CONFUSION")

    span = _find_semantic_match(text, ["submarino"], check_negation=check_neg)
    if span:
        emit("SEM-VESSEL-001", "vessel_type", ev["classification"], "submarino", span[0], span[1], "FABRICATED_MILITARY_VESSEL_TYPE")

    if ev["classification"] == "COOPERATIVE_VESSEL":
        span = _find_semantic_match(text, ["piratería", "pirateria"], check_negation=check_neg)
        if span:
            emit("SEM-DEFAM-001", "infraction", "CUMPLIMIENTO_NORMATIVO", "piratería", span[0], span[1], "DEFAMATORY_LEGAL_HALLUCINATION")

    out.sort(key=lambda f: (f["char_start"], f["rule_id"]))
    return text, out


def extract_numerical_claims_from_text(text: str) -> Dict[str, List[float]]:
    """
    Extract key maritime numbers using the F1-F5 domain-aware regex patterns.
    """
    claims: Dict[str, List[float]] = {
        "latitudes": [],
        "longitudes": [],
        "distances_nm": [],
        "lengths_m": [],
        "rcs_db": [],
        "penalties_usd": [],
    }
    masked = _mask_identifiers(normalize_nfc(text))

    for m in FIXED_LAT_RE.finditer(masked):
        val_str = m.group(1) or m.group(2)
        if val_str:
            v = float(val_str)
            if m.group(3) and m.group(3).upper() == "S" and v > 0:
                v = -v
            claims["latitudes"].append(v)

    for m in FIXED_LON_RE.finditer(masked):
        val_str = m.group(1) or m.group(2)
        if val_str:
            v = float(val_str)
            hemi = m.group(3) or m.group(4)
            if hemi and hemi.upper() in ("W", "O") and v > 0:
                v = -v
            claims["longitudes"].append(v)

    for m in FIXED_DIST_RE.finditer(masked):
        claims["distances_nm"].append(float(m.group(1)))

    for m in FIXED_LEN_RE.finditer(masked):
        claims["lengths_m"].append(float(m.group(1)))

    for m in FIXED_RCS_RE.finditer(masked):
        claims["rcs_db"].append(float(m.group(1)))

    for m in FIXED_MILLONES_RE.finditer(masked):
        claims["penalties_usd"].append(float(m.group(1)) * 1_000_000.0)

    return claims


def validate_legal_narrative(
    raw_narrative: str,
    evidence: EvidenceVector,
    tolerance_nm: float = 0.5,
    tolerance_coord: float = 0.05,
    tolerance_dimension: float = 5.0,
    tolerance_rcs: float = 0.5,
    legacy_parity: bool = False,
) -> NeuroSymbolicValidationResult:
    """
    Adversarial validation pass backed by `validate_with_spans`.
    """
    t0 = time.perf_counter()
    text_nfc, findings = validate_with_spans(
        raw_narrative,
        evidence,
        tolerance_nm=tolerance_nm,
        tolerance_coord=tolerance_coord,
        tolerance_dimension=tolerance_dimension,
        tolerance_rcs=tolerance_rcs,
        legacy_parity=legacy_parity,
    )

    field_to_legacy_metric = {
        "latitude": "latitude_wgs84",
        "longitude": "longitude_wgs84",
        "distance_to_eez_nm": "distance_to_eez_nm",
        "length_m": "length_meters",
        "peak_rcs_db": "peak_rcs_db",
        "legal_characterization": "legal_characterization",
        "jurisdiction_zone": "jurisdiction_zone",
        "economic_penalty_usd": "economic_penalty_usd",
        "vessel_type": "vessel_type_hallucination",
        "infraction": "infraction_hallucination",
    }

    discrepancies: List[ValidationDiscrepancy] = []
    for f in findings:
        exp = f["expected"]
        obs = f["observed"]
        if isinstance(exp, (int, float)) and isinstance(obs, (int, float)):
            err = round(abs(float(obs) - float(exp)), 4)
        else:
            err = 1.0
        discrepancies.append(
            ValidationDiscrepancy(
                metric_name=field_to_legacy_metric.get(f["field"], f["field"]),
                llm_stated_value=obs,
                ground_truth_value=exp,
                absolute_error=err,
                error_severity=f["legacy_severity"],
                rule_id=f["rule_id"],
                char_start=f["char_start"],
                char_end=f["char_end"],
                matched_text=f["matched_text"],
            )
        )

    sanitized_text = text_nfc
    if discrepancies:
        corrected_block = (
            f"\n\n[CERTIFICACIÓN NEURO-SIMBÓLICA DE INTEGRIDAD PROBATORIA]:\n"
            f"- Posición Certificada: Lat {evidence.lat:.5f}°, Lon {evidence.lon:.5f}° WGS84\n"
            f"- Distancia a Línea Oficial ZEE: {evidence.distance_to_eez_nm:+.2f} NM ({evidence.boundary_status})\n"
            f"- Dimensiones Físicas SAR: Eslora {evidence.length_m:.1f} m, Manga {evidence.beam_m:.1f} m\n"
            f"- RCS Pico: {evidence.peak_rcs_db:.1f} dB (Firma de estructura de acero pesada)\n"
            f"- Tipificación Legal Determinista: {evidence.legal_verdict.title_es} ({evidence.legal_verdict.infraction_code})\n"
            f"- Fundamento Normativo Formal: {', '.join(evidence.legal_verdict.legal_basis_articles)}"
        )
        sanitized_text += corrected_block

    adherence = 100.0 if not discrepancies else max(0.0, 100.0 - (len(discrepancies) * 15.0))
    latency_ms = round((time.perf_counter() - t0) * 1000, 3)

    return NeuroSymbolicValidationResult(
        is_valid=(len(discrepancies) == 0),
        hallucination_detected=(len(discrepancies) > 0),
        discrepancies=discrepancies,
        discrepancy_count=len(discrepancies),
        ground_truth_adherence_percent=adherence,
        sanitized_legal_text=sanitized_text,
        validation_latency_ms=latency_ms,
        findings=findings,
    )
