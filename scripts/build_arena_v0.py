#!/usr/bin/env python3
"""Arma arena_replay.v0.example.json SOLO con datos que existen hoy (solo stdlib).

Uso (desde cualquier directorio):
    python3 -I build_arena_v0.py --repo /ruta/al/repo [--out arena_replay.v0.example.json]
                                 [--cases TC-EEZ-001,TC-SKM-010,TC-ADV-029] [--generated-at ISO]

Entradas (solo lectura, relativas a --repo):
    backend/ml/meta_muse_sam31_benchmark_2026.json   resultados 30 casos x 2 modelos
    scripts/run_meta_full_evaluation_suite.py        de ahi se extrae el prompt real (AST, sin ejecutar el suite)
    backend/app/legal/legal_eval_harness.py          SCENARIOS_30 (verdad de cada caso) via AST
    backend/app/legal/neuro_symbolic_validator.py    solo se hashea
    backend/app/legal/rules_engine.py                solo se hashea

Lo que el artefacto NO tiene queda en null/[] y se declara en data_quality; nada se inventa.
"""

import argparse
import ast
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import reference_validator_spans as rvs  # noqa: E402

ARTIFACT_REL = "backend/ml/meta_muse_sam31_benchmark_2026.json"
SUITE_REL = "scripts/run_meta_full_evaluation_suite.py"
HARNESS_REL = "backend/app/legal/legal_eval_harness.py"
VALIDATOR_REL = "backend/app/legal/neuro_symbolic_validator.py"
RULES_REL = "backend/app/legal/rules_engine.py"
ENGINE_REL = "backend/app/laya/meta_muse_engine.py"

WONDER, SPARK = "muse-wonder-1.0", "muse-spark-1.3"
RESULT_KEYS = {WONDER: "muse_wonder_results", SPARK: "muse_spark_results"}
PRICE_URL = "https://dev.meta.ai/docs/pricing-rate-limits"
REASONING_URL = "https://dev.meta.ai/docs/reasoning"
PRICES = {
    WONDER: {"input": 4.00, "output": 20.00, "cached_input": 0.20},
    SPARK: {"input": 1.25, "output": 4.25, "cached_input": 0.15},
}
CERT_MARKER = "[CERTIFICACIÓN NEURO-SIMBÓLICA"
PROBE_VALIDATOR_VERSION = "reference-spans-legacy-parity-0.1"


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def read(repo, rel):
    with open(os.path.join(repo, rel), "rb") as f:
        raw = f.read()
    return raw, raw.decode("utf-8")


def git_last_commit(repo, rel):
    try:
        r = subprocess.run(["git", "-C", repo, "log", "-1", "--format=%h", "--", rel],
                           capture_output=True, text=True, timeout=30)
        h = r.stdout.strip()
        return h if r.returncode == 0 and re.fullmatch(r"[0-9a-f]{7,40}", h) else None
    except Exception:
        return None


def line_numbers(src, needle):
    return [i + 1 for i, line in enumerate(src.splitlines()) if needle in line]


def parse_scenarios(harness_src):
    """SCENARIOS_30 via AST. Los enums (LegalInfractionSeverity.X) se guardan como el nombre del miembro."""
    tree = ast.parse(harness_src)
    node = None
    for n in ast.walk(tree):
        tgt = None
        if isinstance(n, ast.Assign) and len(n.targets) == 1:
            tgt = n.targets[0]
        elif isinstance(n, ast.AnnAssign):
            tgt = n.target
        if isinstance(tgt, ast.Name) and tgt.id == "SCENARIOS_30":
            node = n.value
    if node is None:
        raise SystemExit("SCENARIOS_30 no encontrado en el harness")
    cats = []
    for i, line in enumerate(harness_src.splitlines()):
        m = re.match(r"\s*# ── Categoría ([A-F]):", line)
        if m:
            cats.append((i + 1, m.group(1)))
    out = []
    for d in node.elts:
        sc = {}
        for k, v in zip(d.keys, d.values):
            sc[k.value] = v.attr if isinstance(v, ast.Attribute) else ast.literal_eval(v)
        sc["category"] = [c for ln, c in cats if ln <= d.lineno][-1]
        out.append(sc)
    return out


def zone_of(d):
    if d < -188.0:
        return "MAR_TERRITORIAL_12NM"
    if d < -176.0:
        return "ZONA_CONTIGUA_24NM"
    if d < 0.0:
        return "ZONA_ECONOMICA_EXCLUSIVA_200NM"
    if d <= 3.0:
        return "ZONA_AMORTIGUACION_FRONTERIZA_3NM"
    return "ALTA_MAR_ADYACENTE_MILLA_201"


def verdict(d, cls, lic=False, nat=False):
    """Espejo de rules_engine.evaluate_maritime_case (rule_id, zona, infraction_code, tope de multa).
    validate_arena.py lo contrasta con el motor real."""
    zone = zone_of(d)
    if cls == "GHOST_AIS":
        return "RULE-INT-ITU-004", zone, "SPOOFING_TELEMATICO_GHOST_AIS", 1_000_000.0
    if d < 0.0 and (cls == "DARK_VESSEL" or (not lic and not nat)):
        return "RULE-ARG-EEZ-001", zone, "INCURSION_IUU_ZEE_ART51", 5_000_000.0
    if zone == "ZONA_AMORTIGUACION_FRONTERIZA_3NM" and cls == "DARK_VESSEL":
        return "RULE-ARG-EEZ-002", zone, "SKIMMING_BORDER_PREDATORIO", 500_000.0
    if zone == "ALTA_MAR_ADYACENTE_MILLA_201" and cls == "DARK_VESSEL":
        return "RULE-INT-SOLAS-003", zone, "BLACKOUT_TACTICO_ALTA_MAR", 100_000.0
    return "RULE-INT-UNCLOS-005", zone, "NAVEGACION_COOPERATIVA_CONFORME", None


class SuitePrompt:
    """Extrae del AST del suite el system prompt y el f-string del prompt de usuario."""

    ALLOWED = (ast.JoinedStr, ast.FormattedValue, ast.Constant, ast.Name, ast.Subscript, ast.Load)

    def __init__(self, suite_src):
        tree = ast.parse(suite_src)
        self.node = None
        self.system = None
        for n in ast.walk(tree):
            if (isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)
                    and n.targets[0].id == "prompt" and isinstance(n.value, ast.JoinedStr)):
                self.node = n.value
            if isinstance(n, ast.Dict):
                kv = {k.value: v.value for k, v in zip(n.keys, n.values)
                      if isinstance(k, ast.Constant) and isinstance(v, ast.Constant)}
                if kv.get("role") == "system":
                    self.system = kv.get("content")
        if self.node is None or self.system is None:
            raise SystemExit("No pude extraer el prompt del suite")
        for n in ast.walk(self.node):
            if not isinstance(n, self.ALLOWED):
                raise SystemExit("El prompt del suite contiene nodos no permitidos: %s" % type(n).__name__)
        names = {n.id for n in ast.walk(self.node) if isinstance(n, ast.Name)}
        if not names <= {"cid", "sc", "lat", "lon", "dist", "length_m", "rcs_db"}:
            raise SystemExit("El prompt usa nombres inesperados: %s" % sorted(names))

    def _render(self, values):
        expr = ast.Expression(body=self.node)
        ast.fix_missing_locations(expr)
        return eval(compile(expr, "<suite-prompt>", "eval"), {"__builtins__": {}}, values)

    def render(self, sc):
        return self._render({"cid": sc["case_id"], "sc": {"description": sc["description"]},
                             "lat": sc["lat"], "lon": sc["lon"], "dist": sc["distance_to_eez_nm"],
                             "length_m": sc["length_m"], "rcs_db": sc["peak_rcs_db"]})

    def template(self):
        return self._render({"cid": "{case_id}", "sc": {"description": "{description}"},
                             "lat": "{lat}", "lon": "{lon}", "dist": "{distance_to_eez_nm}",
                             "length_m": "{length_m}", "rcs_db": "{peak_rcs_db}"})


def uuid7_ms(request_id):
    h = request_id.split("-", 1)[-1].replace("-", "")
    if len(h) >= 13 and h[12] == "7":
        return int(h[:12], 16)
    return None


def iso_from_ms(ms):
    return datetime.datetime.fromtimestamp(ms / 1000.0, datetime.timezone.utc).isoformat(timespec="seconds")


def prompt_hash(system, user):
    return sha256_bytes((system + "\n---\n" + user).encode("utf-8"))


def build_run(model, r):
    u = r["usage"]
    p, c, rs, t = u["prompt_tokens"], u["completion_tokens"], u["reasoning_tokens"], u["total_tokens"]
    assert t == p + c, (r["case_id"], model, "total != prompt + completion")
    assert 0 <= rs <= c, (r["case_id"], model, "reasoning fuera de completion")
    price = PRICES[model]
    cost = round((p * price["input"] + c * price["output"]) / 1e6, 6)
    est_ttft = round(r["latency_ms"] * rs / c, 2)
    preview = rvs.normalize(r["narrative_preview"])
    assert CERT_MARKER not in preview, (r["case_id"], model, "el preview contiene el bloque de certificacion")
    adh = r["adherence_percent"]
    n = (100.0 - adh) / 15.0
    assert abs(n - round(n)) < 1e-9 and (adh > 0.0), (r["case_id"], model, "adherencia no es 100-15n")
    n = int(round(n))
    assert (n == 0) == bool(r["is_valid_raw"]), (r["case_id"], model, "is_valid_raw inconsistente con adherencia")
    if n == 0:
        note = ("El validador heredado no reporto hallazgos sobre el texto completo original. Eso no prueba que el "
                "texto sea correcto: el validador heredado no revisa el RCS.")
    else:
        note = ("adherence_percent=%s implica %d hallazgo(s) del validador heredado sobre el texto completo original "
                "(formula max(0, 100 - 15*n)). El artefacto no guarda cuales fueron ni donde, y este texto esta truncado, "
                "asi que no se pueden ubicar." % (adh, n))
    return {
        "model_requested": model,
        "model_served": None,
        "request_id": r["request_id"],
        "finish_reason": None,
        "latency_ms": r["latency_ms"],
        "time_to_first_token_ms": None,
        "timing_basis": "interpolated",
        "interpolation": {
            "method": "reasoning_share_linear",
            "estimated_ttft_ms": est_ttft,
            "formula": "estimated_ttft_ms = latency_ms * usage.reasoning / usage.completion; el texto visible se "
                       "reparte linealmente entre estimated_ttft_ms y latency_ms",
        },
        "stream_timeline": None,
        "usage": {"prompt": p, "completion": c, "reasoning": rs, "total": t, "cached_prompt": None},
        "cost_usd_estimate": cost,
        "narrative_full": preview,
        "narrative_truncated": len(r["narrative_preview"]) >= 240,
        "narrative_source": "artifact.narrative_preview",
        "validator": {
            "version": None,
            "status": "legacy_unverified",
            "is_valid": bool(r["is_valid_raw"]),
            "adherence_percent": adh,
            "findings": [],
            "findings_available": False,
            "notes": [note],
        },
    }


def build(repo, case_ids, generated_at):
    art_raw, art_src = read(repo, ARTIFACT_REL)
    suite_raw, suite_src = read(repo, SUITE_REL)
    harness_raw, harness_src = read(repo, HARNESS_REL)
    val_raw, _ = read(repo, VALIDATOR_REL)
    rules_raw, _ = read(repo, RULES_REL)
    art = json.loads(art_src)
    cmp_ = art["comparative_30_case_evaluation"]
    results = {m: {r["case_id"]: r for r in cmp_[RESULT_KEYS[m]]} for m in RESULT_KEYS}
    scenarios = {sc["case_id"]: sc for sc in parse_scenarios(harness_src)}
    assert len(scenarios) == 30 == len(results[WONDER]) == len(results[SPARK]), "se esperaban 30 casos por modelo"

    temperature = float(re.search(r"temperature=([0-9.]+)", suite_src).group(1))
    max_tokens = int(re.search(r"max_tokens=(\d+)", suite_src).group(1))
    concurrency = int(re.search(r"eval_sem\s*=\s*asyncio\.Semaphore\((\d+)\)", suite_src).group(1))
    assert re.search(r"\(val\.sanitized_legal_text or \"\"\)\[:240\]", suite_src), "el recorte de 240 caracteres cambio"
    streaming = bool(re.search(r"stream\s*=\s*True", suite_src))
    sp = SuitePrompt(suite_src)
    val_hash = sha256_bytes(val_raw)
    val_commit = git_last_commit(repo, VALIDATOR_REL)
    legacy_version = "legacy-2.0-" + val_hash[:8]

    all_runs = [r for m in results for r in results[m].values()]
    ms = [x for x in (uuid7_ms(r["request_id"]) for r in all_runs) if x is not None]
    window = "%s a %s UTC" % (iso_from_ms(min(ms)), iso_from_ms(max(ms))) if ms else "no disponible"
    n_invalid = {m: sum(1 for r in results[m].values() if not r["is_valid_raw"]) for m in results}
    reasoning_share = {m: sum(r["usage"]["reasoning_tokens"] for r in results[m].values()) /
                       sum(r["usage"]["completion_tokens"] for r in results[m].values()) for m in results}
    max_lat = {m: max(r["latency_ms"] for r in results[m].values()) for m in results}
    cert_in_previews = sum(1 for r in all_runs if CERT_MARKER in r["narrative_preview"])
    flag_mismatch = []
    for cid, sc in scenarios.items():
        suite_rule = verdict(sc["distance_to_eez_nm"], sc["classification"])[0]
        if suite_rule != sc["expected_rule"]:
            flag_mismatch.append(cid)
    adh_lines = line_numbers(suite_src, '"post_validator_adherence_percent": 100.0')
    model_line = line_numbers(suite_src, '"model": model_name')

    cases = []
    for cid in case_ids:
        sc = scenarios[cid]
        rule_id, zone, infraction, max_pen = verdict(sc["distance_to_eez_nm"], sc["classification"],
                                                     sc.get("has_valid_license", False), sc.get("is_national_flag", False))
        assert rule_id == sc["expected_rule"], (cid, "el veredicto con flags no coincide con expected_rule")
        user = sp.render(sc)
        probe = None
        if "adversarial_llm_text" in sc:
            ev = {"lat": sc["lat"], "lon": sc["lon"], "distance_to_eez_nm": sc["distance_to_eez_nm"],
                  "length_m": sc["length_m"], "classification": sc["classification"], "zone": zone,
                  "infraction_code": infraction, "max_penalty_usd": max_pen}
            ptext, findings = rvs.validate_with_spans(sc["adversarial_llm_text"], ev)
            probe = {
                "is_model_output": False,
                "label_es": "Texto alucinado de prueba, escrito a mano en el arnés de evaluación del repositorio. "
                            "No es salida de ningún modelo.",
                "source": "%s: SCENARIOS_30[%s].adversarial_llm_text" % (HARNESS_REL, cid),
                "text": ptext,
                "validator": {
                    "version": PROBE_VALIDATOR_VERSION,
                    "status": "reference_provisional",
                    "is_valid": len(findings) == 0,
                    "adherence_percent": rvs.adherence_from_count(len(findings)),
                    "findings": findings,
                    "findings_available": True,
                    "notes": ["Calculado con reference_validator_spans.py en modo paridad con el validador heredado "
                              "(sha256 %s). Falta repetirlo con el validador corregido y sus tests." % val_hash],
                },
            }
        runs = {}
        for m in (WONDER, SPARK):
            run = build_run(m, results[m][cid])
            run["validator"]["version"] = legacy_version
            runs[m] = run
        cases.append({
            "case_id": cid,
            "prompt_variant": "baseline",
            "scenario": {
                "title": sc["description"],
                "category": sc["category"],
                "adversarial": "adversarial_llm_text" in sc,
                "ground_truth": {
                    "lat": sc["lat"], "lon": sc["lon"], "distance_to_eez_nm": sc["distance_to_eez_nm"],
                    "length_m": sc["length_m"], "beam_m": sc["beam_m"], "peak_rcs_db": sc["peak_rcs_db"],
                    "snr_db": sc["snr_db"], "classification": sc["classification"],
                    "jurisdiction_zone": zone, "economic_penalty_max_usd": max_pen,
                    "infraction_code": infraction,
                },
                "expected_rule_id": sc["expected_rule"],
                "expected_severity": sc["expected_severity"],
            },
            "prompt": {"system": sp.system, "user": user, "sha256": prompt_hash(sp.system, user), "reconstructed": True},
            "runs": runs,
            "synthetic_probe": probe,
        })

    updated = art["updated_at_utc"]
    doc = {
        "schema_version": "arena_replay/1.0",
        "provenance": {
            "run_id": "comparative30-%s-v0" % (git_last_commit(repo, ARTIFACT_REL) or "nocommit"),
            "dataset_version": "v0-artifact-derived",
            "generated_at": generated_at,
            "source_run_at": updated,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "n_per_case": 1,
            "concurrency": concurrency,
            "streaming": streaming,
            "reasoning_effort": None,
            "fallback_disabled": None,
            "prompt_template_sha256": prompt_hash(sp.system, sp.template()),
            "price_source": {
                "url": PRICE_URL,
                "retrieved_on": "2026-10-29",
                "usd_per_1m_tokens": PRICES,
                "reasoning_billed_as_output": True,
                "reasoning_billing_source": REASONING_URL,
                "cost_formula": "cost_usd_estimate = (usage.prompt * input + usage.completion * output) / 1e6; "
                                "usage.completion ya incluye el razonamiento; se ignora el descuento por cache "
                                "(cota superior)",
            },
            "validator": {"version": legacy_version, "sha256": val_hash, "source_path": VALIDATOR_REL,
                          "git_commit": val_commit},
            "source": {
                "artifact_path": ARTIFACT_REL,
                "artifact_sha256": sha256_bytes(art_raw),
                "artifact_benchmark_id": art["benchmark_id"],
                "suite_path": SUITE_REL,
                "suite_sha256": sha256_bytes(suite_raw),
                "git_commit": git_last_commit(repo, ARTIFACT_REL),
                "harness_path": HARNESS_REL,
                "harness_sha256": sha256_bytes(harness_raw),
                "rules_engine_sha256": sha256_bytes(rules_raw),
            },
            "sample_note": "n=1 por caso y modelo; %d de 30 casos incluidos en este ejemplo. Las llamadas a la API "
                           "ocurrieron entre %s (segun el timestamp UUIDv7 de los request_id). Corrida concurrente "
                           "(hasta %d en paralelo) y sin alternar modelos en el tiempo: los resultados son "
                           "indicativos, no concluyentes." % (len(case_ids), window, concurrency),
        },
        "replay": {
            "default_speed": 8,
            "speed_options": [1, 2, 4, 8, 16],
            "label": "REPLAY",
            "disclaimer": "Reproducción acelerada de una corrida ya grabada. No se está llamando a ningún modelo "
                          "en este momento.",
            "sides": {"left": WONDER, "right": SPARK},
            "featured_case_id": case_ids[0],
        },
        "data_quality": {
            "missing_fields": [
                {"path": "cases[*].runs.*.narrative_full",
                 "reason": "El suite guarda solo narrative_preview = los primeros 240 caracteres del texto sanitizado (%s:%s)."
                           % (SUITE_REL, ",".join(map(str, line_numbers(suite_src, "[:240]")))),
                 "remedy": "Guardar el content completo de la respuesta (stream acumulado) antes de cualquier recorte."},
                {"path": "cases[*].runs.*.stream_timeline, time_to_first_token_ms",
                 "reason": "El suite no usa streaming; solo existe la latencia total de la respuesta completa.",
                 "remedy": "Llamar con stream=true y guardar checkpoints [t_ms, char_offset] por cada delta de content."},
                {"path": "cases[*].runs.*.model_served",
                 "reason": "El suite guarda el modelo pedido; el campo 'model' de la respuesta no se lee.",
                 "remedy": "Guardar el 'model' del cuerpo (o del primer chunk) y deshabilitar el fallback al otro modelo."},
                {"path": "cases[*].runs.*.finish_reason",
                 "reason": "No se registra; no se puede descartar un corte por max_tokens.",
                 "remedy": "Guardar choices[0].finish_reason y marcar la corrida si es 'length'."},
                {"path": "cases[*].runs.*.validator.findings",
                 "reason": "El artefacto guarda solo is_valid_raw y adherence_percent, y el validador heredado devuelve "
                           "conteos con valores sueltos, sin posicion en el texto.",
                 "remedy": "Validador con spans (char_start/char_end) y texto completo guardado; ver README."},
                {"path": "cases[*].runs.*.usage.cached_prompt",
                 "reason": "No se registra.", "remedy": "Guardar usage.prompt_tokens_details.cached_tokens si la API lo informa."},
                {"path": "cases[*].prompt",
                 "reason": "El prompt no se guarda en el artefacto; se reconstruye desde el codigo del suite (reconstructed=true).",
                 "remedy": "Guardar los mensajes enviados y su sha256 en cada corrida."},
            ],
            "known_issues": [
                {"id": "DQ-01", "severity": "blocker",
                 "summary": "post_validator_adherence_percent = 100.0 es una constante del suite, no una medicion. No mostrarla.",
                 "evidence": "%s lineas %s asignan 100.0 literal para ambos modelos; el artefacto la copia en "
                             "comparative_30_case_evaluation.*_summary." % (SUITE_REL, ", ".join(map(str, adh_lines)))},
                {"id": "DQ-02", "severity": "warning",
                 "summary": "is_valid_raw=false y adherence_percent<100 vienen de un validador por regex sin auditar, "
                            "con falsos positivos por diseño. No llamarlos 'alucinaciones'.",
                 "evidence": "%d de %d resultados tienen is_valid_raw=false (Wonder %d, Spark %d). El validador usa regex "
                             "sin limite de palabra (la unidad 'm' casa con 'millas'; 'S' o 'N' tras un numero casa con "
                             "cualquier palabra que empiece con esa letra) y frases clave sin manejo de negacion "
                             "('submarino', 'mar territorial', 'paso inocente'). Detalle en README." %
                             (n_invalid[WONDER] + n_invalid[SPARK], len(all_runs), n_invalid[WONDER], n_invalid[SPARK])},
                {"id": "DQ-03", "severity": "warning",
                 "summary": "model_requested es el modelo pedido; model_served es desconocido. El cliente puede haber caido "
                            "al otro modelo sin dejar rastro.",
                 "evidence": "%s:%s guarda model_name. %s call_meta_muse_chat reintenta con el otro modelo si el primero falla "
                             "(HTTP distinto de 200, excepcion o contenido vacio), mide la latencia desde antes del primer "
                             "intento y su 'model' de salida es el candidato probado, no el de la respuesta; el suite lo "
                             "descarta." %
                             (SUITE_REL, ",".join(map(str, model_line)), ENGINE_REL)},
                {"id": "DQ-04", "severity": "info",
                 "summary": "La latencia se midio con hasta %d solicitudes en paralelo y sin alternar los modelos en el "
                            "tiempo. Es indicativa." % concurrency,
                 "evidence": "Semaphore(%d) en el suite; ventana de request_id: %s. Latencia maxima: Wonder %.0f ms, Spark "
                             "%.0f ms; el cliente tiene timeout httpx de 90 s por operacion (no total)." % (concurrency, window, max_lat[WONDER], max_lat[SPARK])},
                {"id": "DQ-05", "severity": "info",
                 "summary": "Casi toda la latencia es razonamiento silencioso; el texto visible es una fraccion chica. "
                            "Con timing interpolado el texto aparece al final de la espera.",
                 "evidence": "sum(usage.reasoning)/sum(usage.completion) sobre los 30 casos: Wonder %.1f%%, Spark %.1f%%. "
                             "La API no emite el razonamiento (%s)." % (100 * reasoning_share[WONDER], 100 * reasoning_share[SPARK], REASONING_URL)},
                {"id": "DQ-06", "severity": "info",
                 "summary": "El suite calcula el veredicto sin has_valid_license ni is_national_flag; ground_truth de este "
                            "archivo usa los flags del escenario.",
                 "evidence": "Casos donde el veredicto del suite difiere de expected_rule: %s." %
                             (", ".join(flag_mismatch) if flag_mismatch else "ninguno")},
                {"id": "DQ-07", "severity": "info",
                 "summary": "El prompt original entrega los 5 valores exactos. Con ese prompt, un hallazgo mide fidelidad de "
                            "copia, no razonamiento; por eso existe synthetic_probe.",
                 "evidence": "Plantilla del prompt extraida del AST de %s; sha256 en provenance.prompt_template_sha256." % SUITE_REL},
            ],
            "ui_policy": {
                "replay_banner_required": True,
                "red_pen_enabled": False,
                "narrative_is_truncated": True,
                "timing_is_interpolated": True,
                "validator_verified": False,
            },
        },
        "cases": cases,
    }
    assert cert_in_previews == 0, "hay previews con el bloque de certificacion (%d)" % cert_in_previews
    return doc


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--out", default=os.path.join(HERE, "arena_replay.v0.example.json"))
    ap.add_argument("--cases", default="TC-EEZ-001,TC-SKM-010,TC-ADV-029")
    ap.add_argument("--generated-at", default=None)
    a = ap.parse_args()
    gen = a.generated_at or datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    doc = build(os.path.abspath(a.repo), [c.strip() for c in a.cases.split(",") if c.strip()], gen)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("escrito %s (%d casos)" % (a.out, len(doc["cases"])))


if __name__ == "__main__":
    main()
