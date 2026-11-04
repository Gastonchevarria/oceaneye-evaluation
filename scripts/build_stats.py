#!/usr/bin/env python3
"""build_stats.py - reference generator for stats.json (OceanEye Sentinel 2.0).

Usage (stdlib only):
    python3 -I build_stats.py <artifact.json> <out.json>
        [--schema PATH] [--repo-root DIR] [--sam-masks sam_masks.json] [--source-commit SHA]
        [--generated-at ISO8601] [--site-packages DIR] [--require-jsonschema]

Every measured value in the output is computed here from the artifact
(meta_muse_sam31_benchmark_2026.json). The only literals are:
  * the published price table (PRICES, with source URL and read date),
  * declared facts about the scene (synthetic flag, seed, ground-truth and dark
    target counts, verified against the fixture sources when --repo-root is given),
  * text (disclosures, caveats, forbidden-claim patterns).

Exit codes: 0 ok | 1 schema validation failed | 2 blocking integrity failure
            | 3 forbidden claim found in the generated stats | 4 bad input
"""

import argparse
import hashlib
import json
import math
import os
import re
import statistics
import sys
from datetime import datetime, timezone
from fractions import Fraction
from math import comb

GENERATOR_VERSION = "1.0.0"
SCHEMA_VERSION = "1.0.0"
GENERATOR_SCRIPT = "scripts/build_stats.py"
PRODUCER_SCRIPTS = [
    "scripts/run_meta_hackathon_benchmark.py",
    "scripts/run_meta_full_evaluation_suite.py",
]

PRICE_SOURCE_URL = "https://dev.meta.ai/docs/pricing-rate-limits"
PRICE_READ_ON = "2026-10-29"
REASONING_DOC_URL = "https://dev.meta.ai/docs/reasoning"
USAGE_DOC_URL = "https://dev.meta.ai/docs/api-reference/chat-completions/schemas"
PRICES = {
    "muse-wonder-1.0": {"input": 4.00, "output": 20.00, "cached_input": 0.20},
    "muse-spark-1.3": {"input": 1.25, "output": 4.25, "cached_input": 0.15},
    "muse-image-1.0": {"per_image": 0.01},
    "sam-3.1": {"per_1000_images": 2.50},
}

SCENE_SEED_SOURCE = "backend/app/ingestion/sentinel_fixtures.py"
SCENE_SEED_SOURCE_LINE = 34
SCENE_FIXTURE_META = "backend/tests/fixtures/fixture_metadata.json"
SCENE_GT_LINE = 17
SCENE_DARK_LINE = 18

WONDER = "muse-wonder-1.0"
SPARK = "muse-spark-1.3"
CMP = "$.comparative_30_case_evaluation"
DOS = "$.forensic_dossiers_by_target"
SAMP = "$.sam31_multimodal_reconstructions"
SUMM = "$.summary_metrics"
TEL = "$.telemetry_ledger"

PENALTY_PER_DISCREPANCY = 15.0

_DOS_USAGE = f"{DOS}.*.meta_muse_forensic_analysis.meta_api_telemetry.usage"
_FLEET_USAGE = "$.fleet_transshipment_intelligence.usage"
_WONDER_USAGE = f"{CMP}.muse_wonder_results[*].usage"
_SPARK_USAGE = f"{CMP}.muse_spark_results[*].usage"
CHAT_USAGE_PATHS = [_DOS_USAGE, _FLEET_USAGE, _WONDER_USAGE, _SPARK_USAGE]

FORBIDDEN_CLAIMS = [
    {
        "id": "fc-100-adherence",
        "severity": "error",
        "pattern": r"100(?:[.,]0+)?\s*%\s*(?:de\s+)?(?:adherencia|adherence|precisi[oó]n|precision|exactitud|accuracy)",
        "flags": "i",
        "applies_to": ["frontend/**/*.html", "frontend/**/*.js", "frontend/**/*.json"],
        "reason": "100% de adherencia/precision es un literal, no una medicion (post_validator_adherence_percent es constante en el generador).",
        "use_instead": "comparison.models.*.raw_valid_count",
    },
    {
        "id": "fc-100-s30-casos",
        "severity": "error",
        "pattern": r"100(?:[.,]0+)?\s*%\s*s/\s*30\s*casos",
        "flags": "i",
        "applies_to": ["frontend/**/*.html", "frontend/**/*.js"],
        "reason": "Mismo literal 100% en el marquee y en el PDF.",
        "use_instead": "comparison.models.muse_wonder_1_0.raw_valid_count",
    },
    {
        "id": "fc-exactitud-reglas-100",
        "severity": "error",
        "pattern": r"Exactitud de reglas periciales:\s*100",
        "flags": "i",
        "applies_to": ["frontend/**/*.html", "frontend/**/*.js"],
        "reason": "Linea hard-coded del PDF (index.html ~3451).",
        "use_instead": "comparison.models.muse_wonder_1_0.raw_valid_count",
    },
    {
        "id": "fc-zero-hallucination",
        "severity": "error",
        "pattern": r"0[.,]00\s*%\s*(?:de\s+)?(?:alucinaci[oó]n|hallucination)|alucinaci[oó]n\s+(?:legal\s+)?certificada\s*:?\s*0[.,]00|\(\s*0[.,]00\s*%\s*alucinaci",
        "flags": "i",
        "applies_to": ["frontend/**/*.html", "frontend/**/*.js"],
        "reason": "0.00% de alucinacion es un literal; el artefacto mide adherencia y el validador tiene falsos positivos y falsos negativos.",
        "use_instead": "dossiers.flagged_rate_percent",
    },
    {
        "id": "fc-alucinacion-certificada-label",
        "severity": "error",
        "pattern": r"Alucinaci[oó]n\s+Certificada|Tasa de alucinaci[oó]n certificada|cero desv[ií]os normativos|zero hallucination",
        "flags": "i",
        "applies_to": ["frontend/**/*.html", "frontend/**/*.js"],
        "reason": "Etiqueta que presenta una cifra no medida como certificada.",
        "use_instead": "dossiers.flagged_count",
    },
    {
        "id": "fc-counter-literals",
        "severity": "error",
        "pattern": r"animateValue\(\s*[\"']counter(?:Precision|Hallucination)[\"']|id=[\"']counter(?:Precision|Hallucination)[\"']\s*>\s*(?:100|0[.,]00)",
        "flags": "i",
        "applies_to": ["frontend/**/*.html", "frontend/**/*.js"],
        "reason": "Los contadores de la landing animan hacia 100.0 y 0.00 escritos a mano (index.html ~990, ~997, ~1396-1397).",
        "use_instead": "comparison.models.muse_wonder_1_0.raw_valid_count",
    },
    {
        "id": "fc-hallucination-label-from-deficit",
        "severity": "error",
        "pattern": r"ALUCINACI[OÓ]N\)",
        "flags": "i",
        "applies_to": ["frontend/**/*.html", "frontend/**/*.js"],
        "reason": "Rotula como alucinacion el deficit de puntaje (100 - adherencia); no es una tasa de alucinacion (app/index.html ~1750, ~3022).",
        "use_instead": "dossiers.adherence_deficit_percent",
    },
    {
        "id": "fc-real-satellite-telemetry",
        "severity": "error",
        "pattern": r"(?<!no es )(?<!no son )(?<!sin )(?<!no )telemetr[ií]a\s+satelital\s+real|(?<!not )(?<!no )real\s+satellite\s+telemetry",
        "flags": "i",
        "applies_to": ["frontend/**/*.html", "frontend/**/*.js", "frontend/**/*.json"],
        "reason": "La escena es sintetica (semilla fija, verdad terreno inyectada).",
        "use_instead": "scene.disclosure",
    },
    {
        "id": "fc-official-judicial-document",
        "severity": "error",
        "pattern": r"PODER JUDICIAL DE LA NACI[OÓ]N|PERITO JUDICIAL|Ejemplar Oficial|Peritaje Oficial|Acta (?:Pericial )?Oficial|Dossier (?:Pericial )?Oficial|EVIDENCIA JUR[IÍ]DICA V[AÁ]LIDA|EVIDENCIA JUDICIAL CRIPTOGR[AÁ]FICA|validez jur[ií]dica internacional|PDF Certificado|Plena validez pericial|Fuero Federal de|documento judicial|Tribunal ITLOS|EXP-SAR-\d{4}",
        "flags": "i",
        "applies_to": ["frontend/**/*.html", "frontend/**/*.js"],
        "reason": "Implica documento oficial o judicial; el PDF es un borrador tecnico de apoyo a la decision, simulacion.",
        "use_instead": None,
    },
    {
        "id": "fc-api-calls-83",
        "severity": "error",
        "pattern": r"\b83\s*(?:API\s*calls|llamadas|requests|solicitudes)",
        "flags": "i",
        "applies_to": ["frontend/**/*.html", "frontend/**/*.js"],
        "reason": "83 contradice telemetry_ledger.total_requests=23 y los registros documentados.",
        "use_instead": "api_usage.requests.documented_calls_excluding_uploads",
    },
    {
        "id": "fc-statistical-significance",
        "severity": "error",
        "pattern": r"estad[ií]sticamente\s+significativ|statistically\s+significant",
        "flags": "i",
        "applies_to": ["frontend/**/*.html", "frontend/**/*.js"],
        "reason": "n=30, una sola corrida, casos escritos a mano: resultados indicativos, no concluyentes.",
        "use_instead": "comparison.tests",
    },
    {
        "id": "fc-sam-rectangle-as-mask",
        "severity": "warn",
        "pattern": r"SAM-3\.1\s+MASK|M[ÁA]SCARA\s+RLE\s+LOSSLESS",
        "flags": "i",
        "applies_to": ["frontend/**/*.html", "frontend/**/*.js"],
        "reason": "El canvas dibuja un rectangulo (bbox) etiquetado como mascara; el formato real de SAM 3.1 no es RLE y la mascara almacenada esta truncada.",
        "use_instead": "sam.disclosure",
    },
]

# --------------------------------------------------------------------------
# number formatting (es-AR / en)
# --------------------------------------------------------------------------


def fnum(v, nd, loc):
    body = f"{abs(v):,.{nd}f}"
    if loc == "es":
        body = body.replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    negative = v < 0 and round(abs(v), nd) != 0
    return ("-" if negative else "") + body


def bi(es, en=None):
    return {"es": es, "en": en if en is not None else es}


def d_count(v):
    return bi(fnum(v, 0, "es"), fnum(v, 0, "en"))


def d_ratio_n(v, n):
    return bi(f"{fnum(v, 0, 'es')}/{fnum(n, 0, 'es')}", f"{fnum(v, 0, 'en')}/{fnum(n, 0, 'en')}")


def d_pct(v, nd=1):
    return bi(fnum(v, nd, "es") + "%", fnum(v, nd, "en") + "%")


def d_ms(v):
    if v >= 1000:
        return bi(fnum(v / 1000.0, 1, "es") + " s", fnum(v / 1000.0, 1, "en") + " s")
    return bi(fnum(v, 0, "es") + " ms", fnum(v, 0, "en") + " ms")


def d_tok(v):
    return bi(fnum(v, 0, "es") + " tok", fnum(v, 0, "en") + " tok")


def d_usd(v):
    nd = 2 if v >= 1 else (3 if v >= 0.01 else 4)
    return bi("US$ " + fnum(v, nd, "es"), "$" + fnum(v, nd, "en"))


def d_p(v):
    if v < 0.0001:
        return bi("p < 0,0001", "p < 0.0001")
    return bi("p = " + fnum(v, 4, "es"), "p = " + fnum(v, 4, "en"))


def d_x(v):
    return bi(fnum(v, 2, "es") + "x", fnum(v, 2, "en") + "x")


def d_seconds(v):
    return bi(fnum(v, 1, "es") + " s", fnum(v, 1, "en") + " s")


# --------------------------------------------------------------------------
# metric constructor
# --------------------------------------------------------------------------


def M(value, unit, display, source_path, formula, n=None, caveat=None, caveat_en=None,
      indicative=None, ci95=None):
    m = {
        "value": value,
        "unit": unit,
        "display": display,
        "n": n,
        "source_path": source_path,
        "formula": formula,
        "caveat": caveat,
    }
    if caveat_en is not None:
        m["caveat_en"] = caveat_en
    if indicative is not None:
        m["indicative"] = indicative
    if ci95 is not None:
        m["ci95"] = ci95
    return m


def r(x, nd):
    return round(float(x), nd)


# --------------------------------------------------------------------------
# statistics
# --------------------------------------------------------------------------


def mean(xs):
    return sum(xs) / len(xs)


def percentile_linear(xs, q):
    """Type-7 (linear interpolation) percentile, q in [0, 1]."""
    s = sorted(xs)
    if len(s) == 1:
        return float(s[0])
    pos = q * (len(s) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def wilson_ci(k, n, z=1.959963984540054):
    if n == 0:
        return None
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, centre - half), min(1.0, centre + half)


def fisher_exact_two_sided(a, b, c, d):
    """Two-sided Fisher exact test for [[a, b], [c, d]] using exact integer arithmetic.

    p = sum of P(x) over all tables with the same margins whose probability is
    <= the observed table's probability (the usual 'minimum likelihood' definition).
    """
    r1, r2, k = a + b, c + d, a + c
    total = r1 + r2
    lo, hi = max(0, k - r2), min(r1, k)
    weights = {x: comb(r1, x) * comb(r2, k - x) for x in range(lo, hi + 1)}
    obs = weights[a]
    num = sum(w for w in weights.values() if w <= obs)
    return float(Fraction(num, comb(total, k)))


def mcnemar_exact_two_sided(b, c):
    """Exact (binomial, p=0.5) two-sided McNemar test on the discordant pair counts."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(comb(n, i) for i in range(k + 1))
    return float(min(Fraction(1), Fraction(2 * tail, 2 ** n)))


# --------------------------------------------------------------------------
# input
# --------------------------------------------------------------------------


class InputError(Exception):
    pass


def load_artifact(path):
    with open(path, "rb") as f:
        raw = f.read()
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as e:
        raise InputError(f"artifact is not valid UTF-8 JSON: {e}")
    return data, hashlib.sha256(raw).hexdigest()


def need(d, *keys):
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            raise InputError("artifact is missing key: " + ".".join(keys))
        cur = cur[k]
    return cur


def usage_of(rec):
    u = rec["usage"]
    return int(u["prompt_tokens"]), int(u["completion_tokens"]), int(u.get("reasoning_tokens", 0)), int(u["total_tokens"])


def cost_chat(model, prompt, completion):
    p = PRICES[model]
    return prompt * p["input"] / 1e6 + completion * p["output"] / 1e6


# --------------------------------------------------------------------------
# suspected false positive (SAR-TGT-010 class): a scene id such as "1SDV" is read
# as latitude "1 S" by the validator's suffix regex.
# --------------------------------------------------------------------------


def diagnose_discrepancy(disc, scene_id, raw_text, truth_tol=1e-3):
    name = disc.get("metric_name", "")
    stated = disc.get("llm_stated_value")
    truth = disc.get("ground_truth_value")
    if name not in ("latitude_wgs84", "longitude_wgs84"):
        return None
    if not isinstance(stated, (int, float)) or not isinstance(truth, (int, float)):
        return None
    if float(stated) != int(stated):
        return None
    s = str(int(abs(stated)))
    if not re.search(r"(?<!\d)" + re.escape(s) + r"[SNWEO]", scene_id or "", re.I):
        return None
    nums = [float(x) for x in re.findall(r"-?\d+\.\d+", raw_text or "")]
    if not any(abs(x - truth) <= truth_tol for x in nums):
        return None
    return (f"valor atribuido {stated} coincide con '{s}' + letra dentro del id de escena '{scene_id}', "
            f"y la narrativa contiene el valor correcto ({truth:.4f}).")


# --------------------------------------------------------------------------
# main builder
# --------------------------------------------------------------------------


def build(art, art_sha256, art_file_name, repo_root=None, source_commit=None, generated_at=None, sam_masks=None):
    summary = need(art, "summary_metrics")
    cmp_ = need(art, "comparative_30_case_evaluation")
    dossiers = need(art, "forensic_dossiers_by_target")
    sams = need(art, "sam31_multimodal_reconstructions")
    ledger = need(art, "telemetry_ledger")
    fleet = need(art, "fleet_transshipment_intelligence")
    w_res = need(cmp_, "muse_wonder_results")
    s_res = need(cmp_, "muse_spark_results")

    checks = []

    def check(cid, desc, reported_path, reported, recomputed, ok, severity="error", note=None):
        c = {"id": cid, "description": desc, "reported_path": reported_path, "reported": reported,
             "recomputed": recomputed, "ok": ok, "severity": severity, "note": note}
        checks.append(c)

    # ---- pairing ---------------------------------------------------------
    w_by = {x["case_id"]: x for x in w_res}
    s_by = {x["case_id"]: x for x in s_res}
    if len(w_by) != len(w_res) or len(s_by) != len(s_res) or set(w_by) != set(s_by):
        raise InputError("comparative results are not a clean 1:1 pairing by case_id")
    case_ids = [x["case_id"] for x in w_res]
    n_cases = len(case_ids)

    # ---- provenance ------------------------------------------------------
    provenance = {
        "artifact": {
            "file_name": art_file_name,
            "sha256": art_sha256,
            "benchmark_id": art.get("benchmark_id", ""),
            "generated_at_utc": art.get("generated_at_utc", ""),
            "updated_at_utc": art.get("updated_at_utc", ""),
            "producer_scripts": PRODUCER_SCRIPTS,
        },
        "generator": {"script": GENERATOR_SCRIPT, "version": GENERATOR_VERSION},
        "generated_at": generated_at,
        "source_commit": source_commit,
    }

    # ---- scene -----------------------------------------------------------
    scene_ids = sorted({v["laya_ed25519_certificate"]["scene_id"] for v in dossiers.values()})
    scene_id = scene_ids[0] if scene_ids else ""
    check("scene_id_unique", "All dossiers reference the same scene id", f"{DOS}.*.laya_ed25519_certificate.scene_id",
          len(scene_ids), 1, len(scene_ids) == 1, "warn")
    seed_declared = 1982
    seed_verified = None
    if repo_root:
        p = os.path.join(repo_root, SCENE_SEED_SOURCE)
        try:
            with open(p, "r", encoding="utf-8") as f:
                m = re.search(r"np\.random\.seed\(\s*(\d+)\s*\)", f.read())
            seed_verified = bool(m) and int(m.group(1)) == seed_declared
        except OSError:
            seed_verified = False
    check("scene_seed_matches_source", "Declared scene seed equals the seed in the fixture source",
          f"code:{SCENE_SEED_SOURCE}:{SCENE_SEED_SOURCE_LINE}", seed_declared,
          seed_declared if seed_verified else None, seed_verified, "warn",
          None if repo_root else "not checked: pass --repo-root to verify")
    scene_src = f"code:{SCENE_SEED_SOURCE}:{SCENE_SEED_SOURCE_LINE}"
    gt_declared, dark_declared = 15, 12
    gt_verified = dark_verified = None
    if repo_root:
        try:
            with open(os.path.join(repo_root, SCENE_FIXTURE_META), "r", encoding="utf-8") as f:
                fx = json.load(f)
            fx_gt = fx.get("ground_truth_targets", [])
            fx_dark = sum(1 for t in fx_gt if t.get("has_active_ais") is False)
            gt_verified = fx.get("total_ground_truth_targets") == len(fx_gt) == gt_declared
            dark_verified = fx.get("expected_dark_vessels") == fx_dark == dark_declared
        except (OSError, ValueError):
            gt_verified = dark_verified = False
    not_checked = None if repo_root else "not checked: pass --repo-root to verify"
    check("scene_gt_targets_match_fixture", "Declared ground-truth target count equals the fixture metadata (field and list length)",
          f"code:{SCENE_FIXTURE_META}:{SCENE_GT_LINE}", gt_declared, gt_declared if gt_verified else None, gt_verified, "warn", not_checked)
    check("scene_dark_targets_match_fixture", "Declared dark target count equals the fixture metadata (field and count of has_active_ais false)",
          f"code:{SCENE_FIXTURE_META}:{SCENE_DARK_LINE}", dark_declared, dark_declared if dark_verified else None, dark_verified, "warn", not_checked)
    check("scene_dark_equals_dossiers", "Declared dark target count equals summary_metrics.total_dark_vessels_evaluated and the number of dossiers",
          f"{SUMM}.total_dark_vessels_evaluated", summary.get("total_dark_vessels_evaluated"), dark_declared,
          summary.get("total_dark_vessels_evaluated") == len(dossiers) == dark_declared)
    scene = {
        "synthetic": M(True, "boolean", bi("Escena sintetica", "Synthetic scene"), scene_src,
                       "declared: SAR scene is generated by sentinel_fixtures with injected ground truth", None,
                       "La escena S1A_... es un nombre de producto de aspecto real, pero la imagen y los blancos son sinteticos; no es telemetria satelital real.",
                       "The S1A_... id looks like a real product name, but image and targets are synthetic; this is not real satellite telemetry."),
        "seed": M(seed_declared, "seed", bi(str(seed_declared)), scene_src,
                  "declared: numpy RNG seed of the scene generator", None, None),
        "ground_truth_targets": M(gt_declared, "count", d_count(gt_declared), f"code:{SCENE_FIXTURE_META}:{SCENE_GT_LINE}",
                                  "declared: total_ground_truth_targets of the fixture metadata (equals the length of its ground_truth_targets list)", gt_declared,
                                  "Blancos inyectados en la escena sintetica; la cantidad de detecciones del matcher puede diferir.",
                                  "Targets injected into the synthetic scene; the matcher's detection count can differ."),
        "dark_targets": M(dark_declared, "count", d_count(dark_declared), f"code:{SCENE_FIXTURE_META}:{SCENE_DARK_LINE}",
                          "declared: expected_dark_vessels of the fixture metadata (equals count(ground_truth_targets with has_active_ais false) and summary_metrics.total_dark_vessels_evaluated)", dark_declared,
                          "Blancos inyectados sin AIS activo (oscuros); los otros tienen AIS confirmado o AIS fantasma.",
                          "Injected targets with no active AIS (dark); the rest are AIS-confirmed or ghost AIS."),
        "scene_id": M(scene_id, "text", bi(scene_id), f"{DOS}.*.laya_ed25519_certificate.scene_id",
                      "unique value of laya_ed25519_certificate.scene_id across the 12 dossiers", len(dossiers),
                      "Identificador con formato de producto Sentinel-1; la escena es sintetica.",
                      "Sentinel-1-style product id; the scene is synthetic."),
        "disclosure": bi(
            "Escena SAR sintetica (semilla fija, verdad terreno inyectada): los 12 blancos y sus metricas son parte del escenario de prueba, no telemetria satelital real. Los dossiers son borradores tecnicos de apoyo a la decision, simulacion.",
            "Synthetic SAR scene (fixed seed, injected ground truth): the 12 targets and their metrics are part of the test scenario, not real satellite telemetry. Dossiers are technical decision-support drafts, simulation.",
        ),
        "badge": bi("ESCENA SINTETICA - SIMULACION", "SYNTHETIC SCENE - SIMULATION"),
    }

    # ---- dossiers --------------------------------------------------------
    d_items = []
    dos_valid = 0
    d_adh = []
    flagged = 0
    suspected = 0
    chat_records = []  # (phase, model, prompt, completion, reasoning, total, request_id)
    for tid, dv in dossiers.items():
        mf = dv["meta_muse_forensic_analysis"]
        v = mf["neuro_symbolic_validation"]
        tel = mf["meta_api_telemetry"]
        p, c, rs, t = usage_of(tel)
        chat_records.append(("dossiers", tel["model"], p, c, rs, t, tel["request_id"]))
        is_valid = bool(v["is_valid"])
        discs = v.get("discrepancies", [])
        check(f"dossier_valid_matches_discrepancies:{tid}", "is_valid == (no discrepancies)",
              f"{DOS}.{tid}.meta_muse_forensic_analysis.neuro_symbolic_validation", is_valid, len(discs) == 0,
              is_valid == (len(discs) == 0), "error")
        reasons = []
        for dc in discs:
            dg = diagnose_discrepancy(dc, dv["laya_ed25519_certificate"].get("scene_id"), mf.get("raw_muse_narrative"))
            if dg:
                reasons.append(dg)
        is_susp = bool(discs) and len(reasons) == len(discs)
        dos_valid += is_valid
        flagged += (not is_valid)
        suspected += is_susp
        d_adh.append(float(v["ground_truth_adherence_percent"]))
        d_items.append({
            "target_id": tid,
            "is_valid": is_valid,
            "adherence_percent": r(v["ground_truth_adherence_percent"], 2),
            "discrepancy_count": len(discs),
            "latency_ms": r(tel["latency_ms"], 2),
            "prompt_tokens": p,
            "completion_tokens": c,
            "reasoning_tokens": rs,
            "total_tokens": t,
            "request_id": tel["request_id"],
            "discrepancy_metrics": [str(dc.get("metric_name")) for dc in discs],
            "suspected_false_positive": is_susp,
            "false_positive_reason": "; ".join(reasons) if is_susp else None,
            "source_path": f"{DOS}.{tid}.meta_muse_forensic_analysis",
        })
    n_dos = len(dossiers)
    mean_adh = mean(d_adh)
    check("summary_total_dark_vessels", "summary_metrics.total_dark_vessels_evaluated == number of dossiers",
          f"{SUMM}.total_dark_vessels_evaluated", summary.get("total_dark_vessels_evaluated"), n_dos,
          summary.get("total_dark_vessels_evaluated") == n_dos)
    check("summary_valid_dossiers", "summary_metrics.neuro_symbolic_valid_dossiers == count(is_valid)",
          f"{SUMM}.neuro_symbolic_valid_dossiers", summary.get("neuro_symbolic_valid_dossiers"), dos_valid,
          summary.get("neuro_symbolic_valid_dossiers") == dos_valid)
    check("summary_mean_adherence", "summary_metrics.average_ground_truth_adherence_percent == mean(adherence)",
          f"{SUMM}.average_ground_truth_adherence_percent", summary.get("average_ground_truth_adherence_percent"),
          r(mean_adh, 2), abs(summary.get("average_ground_truth_adherence_percent", -1) - mean_adh) < 0.005)
    check("summary_hallucination_is_deficit",
          "summary_metrics.numerical_hallucination_rate_percent == 100 - mean adherence (a penalty deficit, not a claim-level rate)",
          f"{SUMM}.numerical_hallucination_rate_percent", summary.get("numerical_hallucination_rate_percent"),
          r(100 - mean_adh, 2), abs(summary.get("numerical_hallucination_rate_percent", -1) - (100 - mean_adh)) < 0.005,
          "error", "Consistente pero semanticamente debil: no es una tasa sobre afirmaciones numericas.")

    susp_ids = [i["target_id"] for i in d_items if i["suspected_false_positive"]]
    known_issues = []
    for tid in susp_ids:
        it = next(i for i in d_items if i["target_id"] == tid)
        known_issues.append({
            "id": f"validator-scene-id-as-latitude:{tid}",
            "target_id": tid,
            "status": "open_pending_validator_fix",
            "description": bi(
                f"{tid} figura como invalido por un falso positivo del validador: el id de escena (p. ej. 1SDV) se interpreta como latitud 1 S, aunque la narrativa declara la latitud correcta. Detalle: {it['false_positive_reason']}",
                f"{tid} is flagged invalid by a validator false positive: the scene id (e.g. 1SDV) is parsed as latitude 1 S although the narrative states the correct latitude. Detail: {it['false_positive_reason']}",
            ),
            "evidence": [
                f"{DOS}.{tid}.meta_muse_forensic_analysis.neuro_symbolic_validation.discrepancies",
                "code:backend/app/legal/neuro_symbolic_validator.py:53-66",
            ],
        })
    fp_cav = f"Indicativo: n={n_dos} blancos de una escena sintetica, una sola corrida."
    fp_cav_en = f"Indicative: n={n_dos} targets of a synthetic scene, single run."
    if susp_ids:
        fp_cav += (" Cifra tal como la emite el artefacto: " + ", ".join(susp_ids) +
                   " esta marcado invalido por un falso positivo conocido del validador (id de escena leido como latitud); regenerar el artefacto con el validador corregido.")
        fp_cav_en += (" Figure as emitted by the artifact: " + ", ".join(susp_ids) +
                      " is flagged invalid by a known validator false positive (scene id read as latitude); regenerate the artifact with the fixed validator.")
    dos_paths = [f"{DOS}.*.meta_muse_forensic_analysis.neuro_symbolic_validation.is_valid"]
    ci_v = wilson_ci(dos_valid, n_dos)
    dossiers_sec = {
        "total": M(n_dos, "count", d_count(n_dos), f"{DOS}.*", "count of dossier objects", n_dos),
        "valid": M(dos_valid, "count", d_ratio_n(dos_valid, n_dos), dos_paths,
                   "count(is_valid == true)", n_dos, fp_cav, fp_cav_en, True),
        "valid_rate_percent": M(r(100.0 * dos_valid / n_dos, 2), "percent", d_pct(100.0 * dos_valid / n_dos), dos_paths,
                                "100 * valid / total", n_dos, fp_cav, fp_cav_en, True,
                                {"low": r(100 * ci_v[0], 2), "high": r(100 * ci_v[1], 2), "method": "wilson"}),
        "mean_adherence_percent": M(r(mean_adh, 2), "percent", d_pct(mean_adh, 2),
                                    f"{DOS}.*.meta_muse_forensic_analysis.neuro_symbolic_validation.ground_truth_adherence_percent",
                                    "mean of ground_truth_adherence_percent; validator scores 100 - 15 * discrepancies",
                                    n_dos, fp_cav, fp_cav_en, True),
        "adherence_deficit_percent": M(r(100.0 - mean_adh, 2), "percent", d_pct(100.0 - mean_adh, 2),
                                       [f"{SUMM}.numerical_hallucination_rate_percent"],
                                       "100 - mean_adherence_percent (equals summary_metrics.numerical_hallucination_rate_percent)",
                                       n_dos,
                                       "Es un deficit medio de puntaje (15 puntos por discrepancia), no una tasa de alucinacion sobre afirmaciones numericas. No presentarlo como 'tasa de alucinacion'.",
                                       "Mean score deficit (15 points per discrepancy), not a claim-level hallucination rate. Do not present it as a 'hallucination rate'.",
                                       True),
        "flagged_count": M(flagged, "count", d_ratio_n(flagged, n_dos), dos_paths,
                           "count(is_valid == false)", n_dos, fp_cav, fp_cav_en, True),
        "flagged_rate_percent": M(r(100.0 * flagged / n_dos, 2), "percent", d_pct(100.0 * flagged / n_dos), dos_paths,
                                  "100 * flagged / total", n_dos, fp_cav, fp_cav_en, True),
        "suspected_false_positive_count": M(suspected, "count", d_count(suspected), f"{DOS}.*.meta_muse_forensic_analysis.neuro_symbolic_validation.discrepancies",
                                            "count(dossiers whose every discrepancy matches the scene-id-as-coordinate pattern)", n_dos,
                                            "Heuristica de diagnostico, no una correccion: no cambia las cifras principales.",
                                            "Diagnostic heuristic, not a correction: it does not change the headline figures.", True),
        "valid_if_suspected_false_positives_removed": M(
            dos_valid + suspected, "count", d_ratio_n(dos_valid + suspected, n_dos),
            f"{DOS}.*.meta_muse_forensic_analysis.neuro_symbolic_validation",
            "valid + suspected_false_positive_count", n_dos,
            "Sensibilidad, no resultado: mostrar solo como nota al pie hasta regenerar el artefacto con el validador corregido.",
            "Sensitivity, not a result: show only as a footnote until the artifact is regenerated with the fixed validator.", True),
        "known_issues": known_issues,
        "items": d_items,
    }

    # ---- fleet + comparison chat usage -------------------------------------
    fp, fc, fr, ft = usage_of(fleet)
    chat_records.append(("fleet", fleet["model"], fp, fc, fr, ft, fleet["request_id"]))

    def case_row(rec):
        p, c, rs, t = usage_of(rec)
        return p, c, rs, t

    for phase, res in (("comparison_wonder", w_res), ("comparison_spark", s_res)):
        for x in res:
            p, c, rs, t = case_row(x)
            chat_records.append((phase, x["model"], p, c, rs, t, x["request_id"]))

    # ---- comparison -------------------------------------------------------
    def model_block(res, model_key, model_id, summ_key, out_key):
        n = len(res)
        valid = sum(1 for x in res if x["is_valid_raw"])
        adh = [float(x["adherence_percent"]) for x in res]
        lat = [float(x["latency_ms"]) for x in res]
        ps = sum(usage_of(x)[0] for x in res)
        cs = sum(usage_of(x)[1] for x in res)
        rs = sum(usage_of(x)[2] for x in res)
        ts = sum(usage_of(x)[3] for x in res)
        discs = [(100.0 - a) / PENALTY_PER_DISCREPANCY for a in adh]
        usd = cost_chat(model_id, ps, cs)
        ci = wilson_ci(valid, n)
        path_res = f"{CMP}.{model_key}"
        sm = cmp_[summ_key]
        check(f"cmp_{model_key}_avg_adherence", f"{summ_key}.average_adherence_raw_percent == mean(adherence_percent)",
              f"{CMP}.{summ_key}.average_adherence_raw_percent", sm.get("average_adherence_raw_percent"), r(mean(adh), 2),
              abs(sm.get("average_adherence_raw_percent", -1) - mean(adh)) < 0.005)
        check(f"cmp_{model_key}_avg_latency", f"{summ_key}.average_latency_ms == mean(latency_ms) rounded to 0.1",
              f"{CMP}.{summ_key}.average_latency_ms", sm.get("average_latency_ms"), r(mean(lat), 1),
              abs(sm.get("average_latency_ms", -1) - mean(lat)) < 0.051)
        check(f"cmp_{model_key}_total_tokens", f"{summ_key}.total_tokens == sum(usage.total_tokens)",
              f"{CMP}.{summ_key}.total_tokens", sm.get("total_tokens"), ts, sm.get("total_tokens") == ts)
        check(f"cmp_{model_key}_post_validator_is_unmeasured",
              f"{summ_key}.post_validator_adherence_percent is backed by per-case records",
              f"{CMP}.{summ_key}.post_validator_adherence_percent", sm.get("post_validator_adherence_percent"),
              None, False if not any("post_validator" in "".join(x.keys()) for x in res) else None, "warn",
              "0 de %d registros por caso tienen un campo post-validador: el 100.0 es un literal del generador "
              "(scripts/run_meta_full_evaluation_suite.py:174,180). No mostrar." % n)
        reason_share = 100.0 * rs / cs if cs else 0.0
        caveat_lat = ("Latencia de cliente de extremo a extremo con hasta 8 llamadas concurrentes (ambos modelos en paralelo) y una sola corrida; "
                      "el campo 'model' guarda el modelo pedido, no el servido (el motor reintenta con el otro modelo ante HTTP != 200 o contenido vacio).")
        caveat_lat_en = ("End-to-end client latency with up to 8 concurrent calls (both models in parallel), single run; "
                         "the 'model' field stores the requested model, not the served one (the engine falls back to the other model on non-200 or empty content).")
        lat_srcs = [f"{path_res}[*].latency_ms"]
        tok_src = lambda f: f"{path_res}[*].usage.{f}"
        blk = {
            "model_id": model_id,
            "served_model_verifiable": False,
            "raw_valid_count": M(valid, "count", d_ratio_n(valid, n), f"{path_res}[*].is_valid_raw",
                                 "count(is_valid_raw == true)", n,
                                 "Validez cruda segun el validador actual, que tiene falsos positivos conocidos; el prompt entrega al modelo los valores de verdad terreno, asi que mide fidelidad de transcripcion, no alucinacion espontanea. Indicativo.",
                                 "Raw validity under the current validator, which has known false positives; the prompt hands the model the ground-truth values, so this measures transcription fidelity, not spontaneous hallucination. Indicative.",
                                 True),
            "raw_valid_rate_percent": M(r(100.0 * valid / n, 2), "percent", d_pct(100.0 * valid / n), f"{path_res}[*].is_valid_raw",
                                        "100 * raw_valid / n", n, "Indicativo (n=30, una corrida).", "Indicative (n=30, single run).", True,
                                        {"low": r(100 * ci[0], 2), "high": r(100 * ci[1], 2), "method": "wilson"}),
            "raw_mean_adherence_percent": M(r(mean(adh), 2), "percent", d_pct(mean(adh), 1), f"{path_res}[*].adherence_percent",
                                            "mean(adherence_percent); validator scores 100 - 15 * discrepancies", n,
                                            "Indicativo. El promedio oculta que la distribucion es discreta (100/85/70).",
                                            "Indicative. The mean hides that the distribution is discrete (100/85/70).", True),
            "discrepancies_per_response": M(r(mean(discs), 3), "ratio", bi(fnum(mean(discs), 2, "es"), fnum(mean(discs), 2, "en")),
                                            f"{path_res}[*].adherence_percent",
                                            "mean((100 - adherence_percent) / 15)", n,
                                            "Derivado invirtiendo la penalizacion del validador (15 puntos por discrepancia).",
                                            "Derived by inverting the validator penalty (15 points per discrepancy).", True),
            "latency_mean_ms": M(r(mean(lat), 1), "ms", d_ms(mean(lat)), lat_srcs, "mean(latency_ms)", n, caveat_lat, caveat_lat_en, True),
            "latency_median_ms": M(r(statistics.median(lat), 1), "ms", d_ms(statistics.median(lat)), lat_srcs, "median(latency_ms)", n, caveat_lat, caveat_lat_en, True),
            "latency_p90_ms": M(r(percentile_linear(lat, 0.9), 1), "ms", d_ms(percentile_linear(lat, 0.9)), lat_srcs,
                                "linear-interpolation percentile (type 7) q=0.90 of latency_ms", n, caveat_lat, caveat_lat_en, True),
            "tokens": {
                "prompt_total": M(ps, "tokens", d_tok(ps), tok_src("prompt_tokens"), "sum(usage.prompt_tokens)", n),
                "completion_total": M(cs, "tokens", d_tok(cs), tok_src("completion_tokens"), "sum(usage.completion_tokens)", n,
                                      "Incluye los tokens de razonamiento.", "Includes reasoning tokens."),
                "reasoning_total": M(rs, "tokens", d_tok(rs), tok_src("reasoning_tokens"), "sum(usage.reasoning_tokens)", n,
                                     "Subconjunto de completion_tokens (completion_tokens_details.reasoning_tokens).",
                                     "Subset of completion_tokens (completion_tokens_details.reasoning_tokens)."),
                "total": M(ts, "tokens", d_tok(ts), tok_src("total_tokens"), "sum(usage.total_tokens) == prompt + completion", n),
                "mean_total_per_case": M(r(ts / n, 1), "tokens_per_case", d_tok(ts / n), tok_src("total_tokens"), "sum(total_tokens) / n", n),
                "reasoning_share_of_completion_percent": M(r(reason_share, 2), "percent", d_pct(reason_share), [tok_src("reasoning_tokens"), tok_src("completion_tokens")],
                                                           "100 * sum(reasoning_tokens) / sum(completion_tokens)", n),
            },
            "cost_usd": {
                "total": M(r(usd, 6), "usd", d_usd(usd), [tok_src("prompt_tokens"), tok_src("completion_tokens"), f'stats:pricing.models["{model_id}"]'],
                           "sum(prompt) * input_price/1e6 + sum(completion) * output_price/1e6", n,
                           "Estimacion con precios de lista; todos los tokens de entrada al precio estandar (no hay desglose de cache) y razonamiento facturado como salida (ver pricing).",
                           "Estimate at list prices; all input tokens at the standard rate (no cache breakdown) and reasoning billed as output (see pricing).", True),
                "per_case": M(r(usd / n, 6), "usd", d_usd(usd / n), "stats:comparison.models." + out_key + ".cost_usd.total", "total_usd / n", n),
                "per_raw_valid_response": M(r(usd / valid, 6) if valid else None, "usd", d_usd(usd / valid) if valid else bi("n/a"),
                                            ["stats:comparison.models." + out_key + ".cost_usd.total", f"{path_res}[*].is_valid_raw"],
                                            "total_usd / raw_valid_count", valid,
                                            "Costo por respuesta que paso el validador crudo; sensible a los falsos positivos del validador.",
                                            "Cost per response that passed the raw validator; sensitive to validator false positives.", True),
                "price_source": {"url": PRICE_SOURCE_URL, "read_on": PRICE_READ_ON},
            },
        }
        return blk, dict(valid=valid, n=n, lat=lat, usd=usd, ts=ts)

    wb, wagg = model_block(w_res, "muse_wonder_results", WONDER, "muse_wonder_1_0_summary", "muse_wonder_1_0")
    sb, sagg = model_block(s_res, "muse_spark_results", SPARK, "muse_spark_1_3_summary", "muse_spark_1_3")
    check("cmp_scenarios_count", "scenarios_count == number of paired cases", f"{CMP}.scenarios_count", cmp_.get("scenarios_count"), n_cases,
          cmp_.get("scenarios_count") == n_cases)
    check("cmp_total_model_evaluations", "total_model_evaluations == wonder + spark results", f"{CMP}.total_model_evaluations",
          cmp_.get("total_model_evaluations"), len(w_res) + len(s_res), cmp_.get("total_model_evaluations") == len(w_res) + len(s_res))
    check("summary_harness_cases_are_evaluations", "summary_metrics.total_legal_harness_cases_evaluated counts model evaluations (2 x cases), not cases",
          f"{SUMM}.total_legal_harness_cases_evaluated", summary.get("total_legal_harness_cases_evaluated"), len(w_res) + len(s_res),
          summary.get("total_legal_harness_cases_evaluated") == len(w_res) + len(s_res), "warn",
          "El nombre dice 'cases' pero cuenta evaluaciones: 60 = 30 casos x 2 modelos. Mostrar '30 casos'.")

    # paired table
    both_valid = sum(1 for k in case_ids if w_by[k]["is_valid_raw"] and s_by[k]["is_valid_raw"])
    w_only = sum(1 for k in case_ids if w_by[k]["is_valid_raw"] and not s_by[k]["is_valid_raw"])
    s_only = sum(1 for k in case_ids if (not w_by[k]["is_valid_raw"]) and s_by[k]["is_valid_raw"])
    both_inv = n_cases - both_valid - w_only - s_only
    wv, sv = wagg["valid"], sagg["valid"]
    p_mc = mcnemar_exact_two_sided(w_only, s_only)
    p_fi = fisher_exact_two_sided(wv, n_cases - wv, sv, n_cases - sv)
    pair_src = [f"{CMP}.muse_wonder_results[*].is_valid_raw", f"{CMP}.muse_spark_results[*].is_valid_raw"]
    ind_cav = ("Indicativo: n=30 casos escritos a mano (~8 adversariales), una sola corrida, validador con falsos positivos conocidos. "
               "No concluyente ni extrapolable.")
    ind_cav_en = ("Indicative: n=30 hand-written cases (~8 adversarial), single run, validator with known false positives. "
                  "Not conclusive and not generalizable.")
    tests = {
        "label": bi("Resultados indicativos (n=30, una corrida)", "Indicative results (n=30, single run)"),
        "paired_table": {
            "both_valid": M(both_valid, "count", d_count(both_valid), pair_src, "count(wonder valid and spark valid)", n_cases),
            "wonder_only_valid": M(w_only, "count", d_count(w_only), pair_src, "count(wonder valid and spark invalid)  [McNemar b]", n_cases),
            "spark_only_valid": M(s_only, "count", d_count(s_only), pair_src, "count(wonder invalid and spark valid)  [McNemar c]", n_cases),
            "both_invalid": M(both_inv, "count", d_count(both_inv), pair_src, "n - the other three cells", n_cases),
        },
        "mcnemar_exact_two_sided": M(r(p_mc, 6), "p_value", d_p(p_mc), pair_src,
                                     "exact binomial test p=0.5 on discordant pairs (b=wonder_only, c=spark_only): min(1, 2 * P(X <= min(b,c)))",
                                     w_only + s_only, ind_cav + " Es la prueba apropiada: los casos estan pareados.",
                                     ind_cav_en + " This is the appropriate test: cases are paired.", True),
        "fisher_exact_two_sided": M(r(p_fi, 6), "p_value", d_p(p_fi), pair_src,
                                    "Fisher exact two-sided on [[wonder valid, wonder invalid], [spark valid, spark invalid]]; p = sum of P(table) <= P(observed)",
                                    2 * n_cases, ind_cav + " Supone muestras independientes; los datos son pareados, asi que es solo una referencia.",
                                    ind_cav_en + " Assumes independent samples; the data are paired, so this is a reference only.", True),
    }
    check("fisher_mcnemar_sanity", "Recomputed tests are probabilities in [0, 1]", "stats:comparison.tests",
          [p_mc, p_fi], None, 0 <= p_mc <= 1 and 0 <= p_fi <= 1)

    lat_ratio = mean(wagg["lat"]) / mean(sagg["lat"])
    cost_ratio = wagg["usd"] / sagg["usd"]
    tok_ratio = sagg["ts"] / wagg["ts"]
    ratios = {
        "latency_mean_wonder_over_spark": M(r(lat_ratio, 3), "ratio", d_x(lat_ratio),
                                            [f"{CMP}.muse_wonder_results[*].latency_ms", f"{CMP}.muse_spark_results[*].latency_ms"],
                                            "mean(wonder latency) / mean(spark latency)", n_cases, ind_cav, ind_cav_en, True),
        "cost_wonder_over_spark": M(r(cost_ratio, 3), "ratio", d_x(cost_ratio), ["stats:comparison.models.muse_wonder_1_0.cost_usd.total", "stats:comparison.models.muse_spark_1_3.cost_usd.total"],
                                    "wonder total_usd / spark total_usd", n_cases, ind_cav, ind_cav_en, True),
        "tokens_spark_over_wonder": M(r(tok_ratio, 3), "ratio", d_x(tok_ratio), ["stats:comparison.models.muse_spark_1_3.tokens.total", "stats:comparison.models.muse_wonder_1_0.tokens.total"],
                                      "spark sum(total_tokens) / wonder sum(total_tokens)", n_cases, ind_cav, ind_cav_en, True),
    }

    cases = []
    for k in case_ids:
        row = {"case_id": k}
        for tag, by, mid in (("wonder", w_by, WONDER), ("spark", s_by, SPARK)):
            x = by[k]
            p, c, rs, t = usage_of(x)
            row[tag] = {
                "raw_valid": bool(x["is_valid_raw"]),
                "adherence_percent": r(x["adherence_percent"], 2),
                "discrepancy_count": int(round((100.0 - float(x["adherence_percent"])) / PENALTY_PER_DISCREPANCY)),
                "latency_ms": r(x["latency_ms"], 2),
                "prompt_tokens": p,
                "completion_tokens": c,
                "reasoning_tokens": rs,
                "total_tokens": t,
                "cost_usd": r(cost_chat(mid, p, c), 6),
                "request_id": x["request_id"],
            }
        wv_, sv_ = row["wonder"]["raw_valid"], row["spark"]["raw_valid"]
        row["pair_outcome"] = ("both_valid" if wv_ and sv_ else "wonder_only_valid" if wv_ else "spark_only_valid" if sv_ else "both_invalid")
        cases.append(row)

    methodology = [
        {"id": "prompt-hands-ground-truth", "text": bi(
            "El prompt de cada caso incluye los cinco valores de verdad terreno; se mide fidelidad de transcripcion, no alucinacion espontanea.",
            "Each case prompt includes the five ground-truth values; this measures transcription fidelity, not spontaneous hallucination."),
         "evidence": "code:scripts/run_meta_full_evaluation_suite.py:44-53"},
        {"id": "hand-typed-cases", "text": bi(
            "Los 30 casos estan escritos a mano (aprox. 8 adversariales); no son una muestra aleatoria.",
            "The 30 cases are hand-typed (about 8 adversarial); they are not a random sample."),
         "evidence": "code:backend/app/legal/legal_eval_harness.py:30-355"},
        {"id": "requested-not-served-model", "text": bi(
            "El campo 'model' guarda el modelo pedido; el motor reintenta con el otro modelo ante error o contenido vacio, asi que no se puede verificar que cada fila sea del modelo declarado.",
            "The 'model' field stores the requested model; the engine retries with the other model on error or empty content, so the served model of each row cannot be verified."),
         "evidence": "code:scripts/run_meta_full_evaluation_suite.py:86; code:backend/app/laya/meta_muse_engine.py:135-139,156-158,178"},
        {"id": "validator-false-positives", "text": bi(
            "El validador tiene falsos positivos conocidos (id de escena leido como latitud, 'millas nauticas' contada como eslora, 'pirateria' marcada aun negada) y no verifica el RCS; la validez cruda es una cota inferior y tambien una medida imperfecta.",
            "The validator has known false positives (scene id read as latitude, 'millas nauticas' counted as length, 'pirateria' flagged even when negated) and does not check RCS; raw validity is a lower bound and an imperfect measure."),
         "evidence": "code:backend/app/legal/neuro_symbolic_validator.py:53-124,242"},
        {"id": "preview-240-chars", "text": bi(
            "narrative_preview guarda solo 240 caracteres: las narrativas de las 60 evaluaciones no se pueden revalidar desde el artefacto.",
            "narrative_preview keeps only 240 characters: the narratives of the 60 evaluations cannot be re-validated from the artifact."),
         "evidence": "code:scripts/run_meta_full_evaluation_suite.py:92"},
        {"id": "post-validator-literal", "text": bi(
            "post_validator_adherence_percent = 100.0 es un literal del generador; no se muestra.",
            "post_validator_adherence_percent = 100.0 is a literal in the generator; it is not displayed."),
         "evidence": "code:scripts/run_meta_full_evaluation_suite.py:174,180"},
        {"id": "single-run-concurrency", "text": bi(
            "Una sola corrida, temperatura 0.1, hasta 8 llamadas concurrentes con ambos modelos en paralelo; las latencias dependen de la carga del servicio en ese momento.",
            "Single run, temperature 0.1, up to 8 concurrent calls with both models in parallel; latencies depend on service load at that moment."),
         "evidence": "code:scripts/run_meta_full_evaluation_suite.py:62-64,128-135"},
    ]

    runs_per_case = len(w_res) // n_cases if n_cases else 0
    check("cmp_runs_per_case", "Results per case and model: len(results) == scenarios_count * runs for both models, runs == 1",
          f"{CMP}.muse_wonder_results", len(w_res), n_cases * runs_per_case,
          len(w_res) == len(s_res) == n_cases * runs_per_case and runs_per_case == 1, "warn")

    comparison = {
        "scenarios": M(n_cases, "count", d_count(n_cases), f"{CMP}.scenarios_count", "count of paired cases (case_id present in both result lists)", n_cases,
                       "Casos escritos a mano; 60 evaluaciones = 30 casos x 2 modelos.", "Hand-typed cases; 60 evaluations = 30 cases x 2 models."),
        "runs": M(runs_per_case, "count", d_count(runs_per_case), f"{CMP}.muse_wonder_results",
                  "len(muse_wonder_results) / scenarios_count, and the same for muse_spark_results (results per case and model)", n_cases,
                  "Una sola corrida por caso y modelo, temperatura 0.1; no hay repeticiones que midan la variabilidad.",
                  "A single run per case and model at temperature 0.1; there are no repeats to measure variability."),
        "indicative_label": bi("Resultados indicativos (n=30, una corrida)", "Indicative results (n=30, single run)"),
        "models": {"muse_wonder_1_0": wb, "muse_spark_1_3": sb},
        "tests": tests,
        "ratios": ratios,
        "methodology_caveats": methodology,
        "cases": cases,
    }

    # ---- SAM --------------------------------------------------------------
    sam_recs = list(sams.values())
    n_sam = len(sam_recs)
    sam_lat = [float(s["meta_sam31_segmentation"]["latency_ms"]) for s in sam_recs]
    img_lat = [float(s["meta_muse_image_generation"]["latency_ms"]) for s in sam_recs]
    pipe_lat = [float(s["total_pipeline_latency_ms"]) for s in sam_recs if "total_pipeline_latency_ms" in s]
    dets = [int(s["meta_sam31_segmentation"]["detections_count"]) for s in sam_recs]
    det_ok = all(int(s["meta_sam31_segmentation"]["detections_count"]) == len(s["meta_sam31_segmentation"]["bounding_boxes"]) for s in sam_recs)
    check("sam_detections_match_boxes", "detections_count == len(bounding_boxes) in every SAM record", f"{SAMP}.*.meta_sam31_segmentation",
          sum(dets), sum(len(s["meta_sam31_segmentation"]["bounding_boxes"]) for s in sam_recs), det_ok)
    check("summary_sam_count", "summary_metrics.total_sam31_reconstructions == number of SAM records", f"{SUMM}.total_sam31_reconstructions",
          summary.get("total_sam31_reconstructions"), n_sam, summary.get("total_sam31_reconstructions") == n_sam)
    gen_imgs = sum(1 for s in sam_recs if s.get("meta_muse_image_generation", {}).get("model") == "muse-image-1.0")
    decodable = 0
    for s in sam_recs:
        for m in s["meta_sam31_segmentation"].get("lossless_masks", []):
            if len(m.get("rle_preview", "")) >= int(m.get("rle_length_chars", 0)) > 0:
                decodable += 1
    sizes = {(b["image_width"], b["image_height"]) for s in sam_recs for b in s["meta_sam31_segmentation"]["bounding_boxes"]}
    size_txt = "x".join(str(v) for v in sorted(sizes)[0]) if len(sizes) == 1 else "mixed"
    sam_disc = bi(
        "Imagen ilustrativa generada por muse-image-1.0 a partir de un texto y luego segmentada con el prompt 'ship'. No es un recorte SAR ni evidencia de deteccion; la mascara almacenada esta truncada y no se puede decodificar.",
        "Illustrative image generated by muse-image-1.0 from a text prompt and then segmented with the prompt 'ship'. It is not a SAR chip nor detection evidence; the stored mask is truncated and cannot be decoded.",
    )
    sam_src = f"{SAMP}.*"
    rs_status = "not_run"
    SM = "file:sam_masks.json#"
    real_sar = {
        "status": M(rs_status, "text", bi("No ejecutado", "Not run"), SM + "$.run.mode",
                    "'completed' only when sam_masks.json has run.mode == 'live' and every chip has status 'measured'; otherwise 'not_run'", 0,
                    "Hasta que exista esta corrida, toda mascara mostrada proviene de imagen ilustrativa generada.",
                    "Until this run exists, every mask shown comes from an illustrative generated image."),
        "input_kind": M("not_applicable", "text", bi("No aplica (sin corrida)", "Not applicable (no run)"),
                        SM + "$.chips[*].source.kind", "'not_applicable' while status == 'not_run'", 0),
        "results": None,
    }
    sm_chips = sam_masks.get("chips") if isinstance(sam_masks, dict) and isinstance(sam_masks.get("chips"), list) else []
    if sm_chips and (sam_masks.get("run") or {}).get("mode") == "live" and all(c.get("status") == "measured" for c in sm_chips):
        negs = [c for c in sm_chips if (c.get("control") or {}).get("is_negative_control")]
        targets = [c for c in sm_chips if c not in negs]
        neg_fp = sum(int((c.get("control") or {}).get("observed_detections") or 0) for c in negs)
        ious = [float(d["metrics"]["iou_vs_ground_truth"]["value"]) for c in targets for d in (c.get("sam_response") or {}).get("detections", [])
                if (d.get("metrics") or {}).get("iou_vs_ground_truth")]
        ilat = [float(c["sam_response"]["latency_ms"]) for c in sm_chips if c.get("sam_response")]
        kinds = sorted({str((c.get("source") or {}).get("kind")) for c in sm_chips})
        kind = "synthetic_sar_chip" if kinds == ["synthetic_fixture"] else "+".join(kinds)
        isrc = SM + "$.chips"
        n_ch = len(sm_chips)
        real_sar["status"] = M("completed", "text", bi("Ejecutado", "Completed"), SM + "$.run.mode", "run.mode == 'live' and every chip.status == 'measured'", n_ch)
        real_sar["input_kind"] = M(kind, "text", bi(kind), SM + "$.chips[*].source.kind", "'synthetic_sar_chip' when every chip.source.kind == 'synthetic_fixture'", n_ch)
        real_sar["results"] = {
            "chips_total": M(n_ch, "count", d_count(n_ch), isrc, "len(chips)", n_ch),
            "target_chips": M(len(targets), "count", d_count(len(targets)), isrc + "[?(@.control.is_negative_control==false)]", "count(chips with control.is_negative_control false)", n_ch),
            "negative_controls": M(len(negs), "count", d_count(len(negs)), isrc + "[?(@.control.is_negative_control==true)]", "count(chips with control.is_negative_control true)", n_ch),
            "negative_control_detections": M(neg_fp, "count", d_count(neg_fp), isrc + "[?(@.control.is_negative_control==true)].control.observed_detections", "sum(control.observed_detections) over negative controls", len(negs),
                                             "Detecciones en mar vacio = falsos positivos del control negativo.", "Detections on empty sea = negative-control false positives.", True),
            "iou_mean_vs_ground_truth": M(r(mean(ious), 4) if ious else None, "ratio", bi(fnum(mean(ious), 3, "es"), fnum(mean(ious), 3, "en")) if ious else bi("n/a"),
                                          isrc + "[*].sam_response.detections[*].metrics.iou_vs_ground_truth.value", "mean(iou_vs_ground_truth.value) over target chips", len(ious),
                                          "Indicativo: pocos chips.", "Indicative: few chips.", True),
            "latency_mean_ms": M(r(mean(ilat), 1) if ilat else None, "ms", d_ms(mean(ilat)) if ilat else bi("n/a"), isrc + "[*].sam_response.latency_ms", "mean(sam_response.latency_ms)", len(ilat)),
        }
    sam_sec = {
        "input_kind": M("illustrative_generated_image", "text", bi("Imagen ilustrativa generada", "Illustrative generated image"),
                        f"{SAMP}.*.meta_muse_image_generation.model", "all records carry meta_muse_image_generation.model == 'muse-image-1.0'", n_sam),
        "disclosure": sam_disc,
        "records_total": M(n_sam, "count", d_count(n_sam), sam_src, "count of SAM records", n_sam),
        "generated_image_records": M(gen_imgs, "count", d_ratio_n(gen_imgs, n_sam), f"{SAMP}.*.meta_muse_image_generation.model",
                                     "count(meta_muse_image_generation.model == 'muse-image-1.0')", n_sam),
        "detections_total": M(sum(dets), "count", d_count(sum(dets)), f"{SAMP}.*.meta_sam31_segmentation.detections_count", "sum(detections_count)", n_sam,
                              "Detecciones sobre imagenes generadas; no son detecciones de buques sobre SAR.", "Detections on generated images; not vessel detections on SAR."),
        "segmentation_latency_mean_ms": M(r(mean(sam_lat), 1), "ms", d_ms(mean(sam_lat)), f"{SAMP}.*.meta_sam31_segmentation.latency_ms", "mean(latency_ms)", n_sam),
        "segmentation_latency_median_ms": M(r(statistics.median(sam_lat), 1), "ms", d_ms(statistics.median(sam_lat)), f"{SAMP}.*.meta_sam31_segmentation.latency_ms", "median(latency_ms)", n_sam),
        "segmentation_latency_max_ms": M(r(max(sam_lat), 1), "ms", d_ms(max(sam_lat)), f"{SAMP}.*.meta_sam31_segmentation.latency_ms", "max(latency_ms)", n_sam),
        "image_generation_latency_mean_ms": M(r(mean(img_lat), 1), "ms", d_ms(mean(img_lat)), f"{SAMP}.*.meta_muse_image_generation.latency_ms", "mean(latency_ms)", n_sam),
        "pipeline_latency_mean_ms": M(r(mean(pipe_lat), 1) if pipe_lat else None, "ms", d_ms(mean(pipe_lat)) if pipe_lat else bi("n/a"),
                                      f"{SAMP}.*.total_pipeline_latency_ms", "mean(total_pipeline_latency_ms)", len(pipe_lat),
                                      "Incluye la generacion de imagen, que domina el tiempo.", "Includes image generation, which dominates the time."),
        "image_size_px": M(size_txt, "text", bi(size_txt), f"{SAMP}.*.meta_sam31_segmentation.bounding_boxes[*].image_width/image_height", "unique (image_width x image_height) across boxes", n_sam),
        "masks_decodable_from_artifact": M(decodable, "count", d_ratio_n(decodable, n_sam), f"{SAMP}.*.meta_sam31_segmentation.lossless_masks[*]",
                                           "count(len(rle_preview) >= rle_length_chars)", n_sam,
                                           "rle_preview esta truncado (~120 caracteres de miles): las mascaras almacenadas no se pueden decodificar. El campo 'encoding: lossless_rle' esta mal rotulado; el formato documentado de SAM 3.1 es 'H,W,~<base85>' sin perdida (https://dev.meta.ai/docs/sam/reading-segmentation).",
                                           "rle_preview is truncated (~120 chars of thousands): stored masks cannot be decoded. The 'encoding: lossless_rle' label is wrong; the documented SAM 3.1 format is lossless 'H,W,~<base85>' (https://dev.meta.ai/docs/sam/reading-segmentation)."),
        "prompt_targets": sorted({s["meta_sam31_segmentation"]["prompt_target"] for s in sam_recs}),
        "real_sar_inspector": real_sar,
    }

    # ---- API usage --------------------------------------------------------
    n_chat = len(chat_records)
    by_phase = {}
    for ph, model, p, c, rs, t, rid in chat_records:
        a = by_phase.setdefault(ph, [0, 0, 0, 0, 0])
        a[0] += 1; a[1] += p; a[2] += c; a[3] += rs; a[4] += t
    tot_p = sum(x[2] for x in chat_records)
    tot_c = sum(x[3] for x in chat_records)
    tot_r = sum(x[4] for x in chat_records)
    tot_t = sum(x[5] for x in chat_records)
    for k_, v_, lbl in (("total_prompt_tokens", tot_p, "prompt"), ("total_completion_tokens", tot_c, "completion"),
                        ("total_reasoning_tokens", tot_r, "reasoning"), ("total_tokens_consumed", tot_t, "total")):
        check(f"summary_{k_}", f"summary_metrics.{k_} == sum over all {n_chat} chat-completion records ({lbl})",
              f"{SUMM}.{k_}", summary.get(k_), v_, summary.get(k_) == v_)
    subset_ok = all(rs_ <= c_ and t_ == p_ + c_ for _, _, p_, c_, rs_, t_, _ in chat_records)
    check("tokens_reasoning_subset_and_total", "reasoning <= completion and total == prompt + completion in every chat record",
          CHAT_USAGE_PATHS, n_chat, sum(1 for _, _, p_, c_, rs_, t_, _ in chat_records if rs_ <= c_ and t_ == p_ + c_), subset_ok)
    ids = [x[6] for x in chat_records]
    check("chat_request_ids_unique", "Every chat-completion record has a distinct request_id", "usage records request_id", len(ids), len(set(ids)), len(ids) == len(set(ids)))

    n_up = sum(1 for s in sam_recs if s.get("meta_files_upload", {}).get("file_id"))
    doc_chat = n_chat
    doc_total = doc_chat + n_sam + gen_imgs
    doc_total_up = doc_total + n_up
    sum_req = summary.get("total_meta_api_requests")
    led_req = ledger.get("total_requests")
    inconsistent = not (sum_req == led_req == doc_total)
    check("api_request_count_inconsistent", "summary_metrics.total_meta_api_requests vs telemetry_ledger.total_requests vs calls documented by records",
          [f"{SUMM}.total_meta_api_requests", f"{TEL}.total_requests"], [sum_req, led_req], doc_total, not inconsistent, "warn",
          "83 = 23 (ledger del primer run: 13 chat + 5 imagen + 5 SAM) + 60 (comparativo); los 7 pares imagen+SAM agregados despues no estan contados. Mostrar el desglose documentado.")
    req_src = [f"{SUMM}.total_meta_api_requests", f"{TEL}.total_requests"]
    doc_srcs = [f"{CMP}.muse_wonder_results[*].request_id", f"{CMP}.muse_spark_results[*].request_id", f"{DOS}.*.meta_muse_forensic_analysis.meta_api_telemetry.request_id",
                "$.fleet_transshipment_intelligence.request_id", f"{SAMP}.*.meta_sam31_segmentation.response_id", f"{SAMP}.*.meta_muse_image_generation"]
    docs_cav = ("Cota inferior: solo cuenta llamadas exitosas con registro en el artefacto; no incluye intentos fallidos ni reintentos no registrados.")
    docs_cav_en = ("Lower bound: counts only successful calls with a record in the artifact; excludes failed or unlogged retry attempts.")
    requests = {
        "reported_in_summary": M(sum_req, "count", d_count(sum_req), f"{SUMM}.total_meta_api_requests", "as reported by the artifact", None,
                                 "No usar: es inconsistente con el ledger y con los registros.", "Do not use: inconsistent with the ledger and the records."),
        "reported_in_telemetry_ledger": M(led_req, "count", d_count(led_req), f"{TEL}.total_requests", "as reported by the artifact", None,
                                          "Snapshot del primer run (antes del suite de 60 casos y de 7 pares imagen+SAM).", "Snapshot of the first run (before the 60-case suite and 7 image+SAM pairs)."),
        "documented_chat_comparison": M(len(w_res) + len(s_res), "count", d_count(len(w_res) + len(s_res)), doc_srcs[:2], "len(wonder results) + len(spark results)", None),
        "documented_chat_dossiers": M(n_dos, "count", d_count(n_dos), doc_srcs[2], "number of dossiers (one chat call each)", None),
        "documented_chat_fleet": M(1, "count", d_count(1), doc_srcs[3], "fleet_transshipment_intelligence is a single record", None),
        "documented_image_generation": M(gen_imgs, "count", d_count(gen_imgs), doc_srcs[5], "count(meta_muse_image_generation records)", None),
        "documented_sam_segmentation": M(n_sam, "count", d_count(n_sam), doc_srcs[4], "count(meta_sam31_segmentation records)", None),
        "documented_files_upload": M(n_up, "count", d_count(n_up), f"{SAMP}.*.meta_files_upload.file_id", "count(meta_files_upload.file_id)", None,
                                     "El ledger no cuenta las subidas de archivos.", "The ledger does not count file uploads."),
        "documented_calls_excluding_uploads": M(doc_total, "count", d_count(doc_total), doc_srcs, "chat + image + SAM (the ledger's counting convention)", None, docs_cav, docs_cav_en),
        "documented_calls_including_uploads": M(doc_total_up, "count", d_count(doc_total_up), doc_srcs + [f"{SAMP}.*.meta_files_upload.file_id"], "chat + image + SAM + file uploads", None, docs_cav, docs_cav_en),
        "inconsistency": {
            "detected": bool(inconsistent),
            "summary_value": sum_req,
            "ledger_value": led_req,
            "documented_value": doc_total,
            "summary_minus_ledger": (sum_req or 0) - (led_req or 0),
            "documented_minus_summary": doc_total - (sum_req or 0),
            "source_paths": req_src,
            "explanation": None,
            "recommended_display_key": "api_usage.requests.documented_calls_excluding_uploads",
        },
    }
    led_sam = int(ledger.get("total_sam31_segmentations", 0))
    requests["inconsistency"]["explanation"] = bi(
        f"El artefacto reporta {sum_req} (summary_metrics) y {led_req} (telemetry_ledger); los registros documentan {doc_total} llamadas. "
        f"{sum_req} = {led_req} (ledger del primer run, con {led_sam} pares imagen+SAM) + {len(w_res) + len(s_res)} (comparativo), y omite los pares imagen+SAM de los otros {n_sam - led_sam} blancos.",
        f"The artifact reports {sum_req} (summary_metrics) and {led_req} (telemetry_ledger); the records document {doc_total} calls. "
        f"{sum_req} = {led_req} (first-run ledger, with {led_sam} image+SAM pairs) + {len(w_res) + len(s_res)} (comparison), and omits the image+SAM pairs of the other {n_sam - led_sam} targets.")
    check("api_request_count_explained", "summary total == ledger total + comparison evaluations (explains the 83)",
          [f"{SUMM}.total_meta_api_requests", f"{TEL}.total_requests"], sum_req, (led_req or 0) + len(w_res) + len(s_res),
          sum_req == (led_req or 0) + len(w_res) + len(s_res), "info")

    tok_srcs = CHAT_USAGE_PATHS
    tokens = {
        "prompt_total": M(tot_p, "tokens", d_tok(tot_p), f"{SUMM}.total_prompt_tokens", "sum(usage.prompt_tokens) over all chat records (equals summary)", n_chat),
        "completion_total": M(tot_c, "tokens", d_tok(tot_c), f"{SUMM}.total_completion_tokens", "sum(usage.completion_tokens) over all chat records (equals summary)", n_chat,
                              "Incluye los tokens de razonamiento.", "Includes reasoning tokens."),
        "reasoning_total": M(tot_r, "tokens", d_tok(tot_r), f"{SUMM}.total_reasoning_tokens", "sum(usage.reasoning_tokens) over all chat records (equals summary)", n_chat,
                             "Subconjunto de completion_tokens; no sumar aparte.", "Subset of completion_tokens; do not add separately."),
        "total": M(tot_t, "tokens", d_tok(tot_t), f"{SUMM}.total_tokens_consumed", "prompt + completion (reasoning is a subset of completion)", n_chat),
        "reasoning_share_of_completion_percent": M(r(100.0 * tot_r / tot_c, 2), "percent", d_pct(100.0 * tot_r / tot_c), [f"{SUMM}.total_reasoning_tokens", f"{SUMM}.total_completion_tokens"],
                                                   "100 * reasoning / completion", n_chat),
        "reasoning_is_subset_of_completion": M(bool(subset_ok), "boolean", bi("Si" if subset_ok else "No", "Yes" if subset_ok else "No"), tok_srcs,
                                               "all(reasoning <= completion and total == prompt + completion)", n_chat,
                                               f"Consistente con la documentacion ({REASONING_DOC_URL}, {USAGE_DOC_URL}): el razonamiento se factura como salida y esta dentro de completion_tokens.",
                                               f"Consistent with the docs ({REASONING_DOC_URL}, {USAGE_DOC_URL}): reasoning is billed as output and is part of completion_tokens."),
        "by_phase": {
            ph: {
                "calls": M(v[0], "count", d_count(v[0]), tok_srcs, "number of chat records in the phase", v[0]),
                "total_tokens": M(v[4], "tokens", d_tok(v[4]), tok_srcs, "sum(usage.total_tokens) in the phase", v[0]),
            } for ph, v in sorted(by_phase.items())
        },
    }

    chat_cost = {}
    for ph, model, p, c, rs, t, rid in chat_records:
        chat_cost.setdefault(model, 0.0)
        chat_cost[model] += cost_chat(model, p, c)
    c_w = chat_cost.get(WONDER, 0.0)
    c_s = chat_cost.get(SPARK, 0.0)
    c_img = gen_imgs * PRICES["muse-image-1.0"]["per_image"]
    c_sam = n_sam * PRICES["sam-3.1"]["per_1000_images"] / 1000.0
    c_tot = c_w + c_s + c_img + c_sam
    cost_cav = ("Estimacion con precios de lista. Subestima: no incluye intentos fallidos ni reintentos no registrados. Sobreestima la parte de entrada: aplica el precio estandar a todos los tokens de prompt aunque algunos puedan haber sido de cache.")
    cost_cav_en = ("Estimate at list prices. Underestimates: excludes failed or unlogged retries. Overestimates the input part: applies the standard rate to all prompt tokens even if some were cached.")
    pr_src = "stats:pricing.models"
    cost_usd = {
        "chat_wonder": M(r(c_w, 6), "usd", d_usd(c_w), [_DOS_USAGE, _FLEET_USAGE, _WONDER_USAGE, pr_src], "sum(prompt) * input_usd_per_1m/1e6 + sum(completion) * output_usd_per_1m/1e6 (pricing.models.muse-wonder-1.0)", None, cost_cav, cost_cav_en, True),
        "chat_spark": M(r(c_s, 6), "usd", d_usd(c_s), [_SPARK_USAGE, pr_src], "sum(prompt) * input_usd_per_1m/1e6 + sum(completion) * output_usd_per_1m/1e6 (pricing.models.muse-spark-1.3)", None, cost_cav, cost_cav_en, True),
        "image_generation": M(r(c_img, 6), "usd", d_usd(c_img), [doc_srcs[5], pr_src], "documented_image_generation * usd_per_image", None, cost_cav, cost_cav_en, True),
        "sam_segmentation": M(r(c_sam, 6), "usd", d_usd(c_sam), [doc_srcs[4], pr_src], "documented_sam_segmentation * usd_per_1000_images / 1000", None, cost_cav, cost_cav_en, True),
        "total": M(r(c_tot, 6), "usd", d_usd(c_tot), ["stats:api_usage.cost_usd_estimate.chat_wonder", "stats:api_usage.cost_usd_estimate.chat_spark", "stats:api_usage.cost_usd_estimate.image_generation", "stats:api_usage.cost_usd_estimate.sam_segmentation"], "chat_wonder + chat_spark + image_generation + sam_segmentation", None, cost_cav, cost_cav_en, True),
        "price_source": {"url": PRICE_SOURCE_URL, "read_on": PRICE_READ_ON},
    }
    exec_s = art.get("total_execution_seconds")
    execution = {
        "first_run_seconds": M(exec_s, "seconds", d_seconds(exec_s), "$.total_execution_seconds", "as reported by the artifact", None,
                               "Mide solo el primer run (dossiers, 5 pares imagen+SAM, flota); no incluye el comparativo de 30 casos ni los 7 pares agregados despues.",
                               "Measures the first run only (dossiers, 5 image+SAM pairs, fleet); excludes the 30-case comparison and the 7 pairs added later.") if isinstance(exec_s, (int, float)) else None,
    }
    api_usage = {
        "requests": requests,
        "tokens": tokens,
        "cost_usd_estimate": cost_usd,
        "execution": execution,
    }

    # ---- pricing ------------------------------------------------------------
    def price(v, unit, src_field):
        return M(v, unit, bi(fnum(v, 2, "es"), fnum(v, 2, "en")), f"doc:{PRICE_SOURCE_URL}@{PRICE_READ_ON}", f"published list price ({src_field})", None)

    pricing = {
        "source_url": PRICE_SOURCE_URL,
        "read_on": PRICE_READ_ON,
        "reasoning_billing_doc_url": REASONING_DOC_URL,
        "usage_schema_doc_url": USAGE_DOC_URL,
        "assumptions": [
            bi("Los tokens de razonamiento se facturan como salida y estan incluidos en completion_tokens (por eso el costo usa completion_tokens).",
               "Reasoning tokens are billed as output and are included in completion_tokens (so cost uses completion_tokens)."),
            bi("El artefacto no registra usage.prompt_tokens_details.cached_tokens: todo el prompt se valora al precio estandar de entrada (cota superior de la parte de entrada si hubo tokens en cache).",
               "The artifact does not record usage.prompt_tokens_details.cached_tokens: the whole prompt is priced at the standard input rate (upper bound on the input part if any tokens were cached)."),
            bi("Los precios son los de lista leidos en la fecha indicada; pueden cambiar.", "Prices are the list prices read on the stated date; they may change."),
        ],
        "models": {
            WONDER: {
                "input_usd_per_1m": price(PRICES[WONDER]["input"], "usd_per_1m_tokens", "input"),
                "output_usd_per_1m": price(PRICES[WONDER]["output"], "usd_per_1m_tokens", "output"),
                "cached_input_usd_per_1m": price(PRICES[WONDER]["cached_input"], "usd_per_1m_tokens", "cached input (not applied)"),
            },
            SPARK: {
                "input_usd_per_1m": price(PRICES[SPARK]["input"], "usd_per_1m_tokens", "input"),
                "output_usd_per_1m": price(PRICES[SPARK]["output"], "usd_per_1m_tokens", "output"),
                "cached_input_usd_per_1m": price(PRICES[SPARK]["cached_input"], "usd_per_1m_tokens", "cached input (not applied)"),
            },
            "muse-image-1.0": {"usd_per_image": price(PRICES["muse-image-1.0"]["per_image"], "usd_per_image", "per image")},
            "sam-3.1": {"usd_per_1000_images": price(PRICES["sam-3.1"]["per_1000_images"], "usd_per_1000_images", "per 1,000 images")},
        },
    }

    # ---- integrity summary ---------------------------------------------------
    blocking = [c for c in checks if c["ok"] is False and c["severity"] == "error"]
    known = [c for c in checks if c["ok"] is False and c["severity"] != "error"]
    integrity = {
        "checks": checks,
        "blocking_failures": len(blocking),
        "known_inconsistencies": len(known),
    }

    out = {
        "schema_version": SCHEMA_VERSION,
        "provenance": provenance,
        "scene": scene,
        "integrity": integrity,
        "dossiers": dossiers_sec,
        "comparison": comparison,
        "sam": sam_sec,
        "api_usage": api_usage,
        "pricing": pricing,
        "forbidden_claims": FORBIDDEN_CLAIMS,
    }
    return out


# --------------------------------------------------------------------------
# self-checks and CLI
# --------------------------------------------------------------------------


def iter_strings(node, path=""):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from iter_strings(v, f"{path}.{k}" if path else k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from iter_strings(v, f"{path}[{i}]")
    elif isinstance(node, str):
        yield path, node


def scan_forbidden(out):
    hits = []
    pats = [(c["id"], re.compile(c["pattern"], re.I if "i" in c.get("flags", "") else 0)) for c in FORBIDDEN_CLAIMS]
    body = {k: v for k, v in out.items() if k != "forbidden_claims"}
    for path, s in iter_strings(body):
        for cid, rx in pats:
            if rx.search(s):
                hits.append((cid, path, s[:80]))
    return hits


def validate_schema(out, schema_path, site_packages, require):
    if site_packages:
        sys.path.append(site_packages)
    try:
        import jsonschema  # noqa
    except ImportError:
        msg = "jsonschema no esta disponible: validacion de esquema omitida"
        if require:
            print("ERROR: " + msg, file=sys.stderr)
            return 1
        print("AVISO: " + msg, file=sys.stderr)
        return 0
    with open(schema_path, "r", encoding="utf-8") as f:
        schema = json.load(f)
    jsonschema.Draft202012Validator.check_schema(schema)
    v = jsonschema.Draft202012Validator(schema)
    errs = sorted(v.iter_errors(out), key=lambda e: list(e.absolute_path))
    if errs:
        for e in errs[:20]:
            print("SCHEMA: /" + "/".join(str(p) for p in e.absolute_path) + ": " + e.message[:200], file=sys.stderr)
        return 1
    try:
        from importlib.metadata import version as _pkg_version
        js_version = _pkg_version("jsonschema")
    except Exception:
        js_version = "unknown"
    print(f"schema OK (jsonschema {js_version}, Draft 2020-12)", file=sys.stderr)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build stats.json from the Meta benchmark artifact")
    ap.add_argument("artifact")
    ap.add_argument("out")
    ap.add_argument("--schema", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "stats.schema.json"))
    ap.add_argument("--repo-root", default=None, help="repo checkout; verifies the declared scene seed against the fixture source")
    ap.add_argument("--sam-masks", default=None, help="sam_masks.json; fills sam.real_sar_inspector only when run.mode is live and every chip is measured")
    ap.add_argument("--source-commit", default=None)
    ap.add_argument("--generated-at", default=None, help="ISO-8601 UTC; pass the previous value to get a reproducible rebuild")
    ap.add_argument("--site-packages", default=None, help="extra dir (appended to sys.path) where jsonschema lives, for python3 -I")
    ap.add_argument("--require-jsonschema", action="store_true")
    args = ap.parse_args(argv)

    try:
        art, sha = load_artifact(args.artifact)
        gen_at = args.generated_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        sam_masks = None
        if args.sam_masks:
            with open(args.sam_masks, "r", encoding="utf-8") as f:
                sam_masks = json.load(f)
        out = build(art, sha, os.path.basename(args.artifact), args.repo_root, args.source_commit, gen_at, sam_masks)
    except (InputError, KeyError, TypeError, ValueError, OSError) as e:
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 4

    if args.sam_masks and out["sam"]["real_sar_inspector"]["status"]["value"] != "completed":
        print("note: sam_masks.json is not a complete live run; sam.real_sar_inspector stays not_run", file=sys.stderr)

    if out["integrity"]["blocking_failures"]:
        for c in out["integrity"]["checks"]:
            if c["ok"] is False and c["severity"] == "error":
                print(f"INTEGRITY[{c['id']}]: reported={c['reported']} recomputed={c['recomputed']}", file=sys.stderr)
        print("nothing written: the artifact contradicts itself", file=sys.stderr)
        return 2

    hits = scan_forbidden(out)
    if hits:
        for cid, path, s in hits:
            print(f"FORBIDDEN[{cid}] in generated stats at {path}: {s}", file=sys.stderr)
        print("nothing written: generated text matches a forbidden claim", file=sys.stderr)
        return 3

    if validate_schema(out, args.schema, args.site_packages, args.require_jsonschema) != 0:
        print("nothing written: output does not satisfy the schema", file=sys.stderr)
        return 1

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, sort_keys=False)
        f.write("\n")
    print(f"wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
