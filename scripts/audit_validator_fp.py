#!/usr/bin/env python3
"""Auditoria de falsos positivos del validador heredado sobre los datos actuales (solo lectura).

Uso:  python3 -I audit_validator_fp.py --repo /ruta/al/repo [--all]

Carga el validador REAL (necesita pydantic del site-packages del usuario; no instala nada) y lo corre sobre:
  1. los 240 caracteres guardados de cada resultado (--all: los 60; por defecto, solo los is_valid_raw=false),
  2. los textos adversariales del harness (SCENARIOS_30[*].adversarial_llm_text).
Compara ademas con reference_validator_spans.py (paridad de conteo) y muestra el contexto de cada hallazgo para
leerlo a mano. El texto guardado esta truncado: si n_preview < n_original, parte de los hallazgos cae fuera de los 240
caracteres y no se puede revisar.
"""

import importlib.util
import json
import os
import re
import site
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.append(site.getusersitepackages())
sys.path.insert(0, HERE)
import build_arena_v0 as b  # noqa: E402
import reference_validator_spans as rvs  # noqa: E402


def load_legacy(repo):
    pkg = types.ModuleType("app")
    pkg.__path__ = []
    sub = types.ModuleType("app.legal")
    sub.__path__ = []
    sys.modules["app"], sys.modules["app.legal"] = pkg, sub
    mods = {}
    for name in ("rules_engine", "neuro_symbolic_validator"):
        spec = importlib.util.spec_from_file_location("app.legal." + name, os.path.join(repo, "backend/app/legal", name + ".py"))
        m = importlib.util.module_from_spec(spec)
        sys.modules["app.legal." + name] = m
        spec.loader.exec_module(m)
        mods[name] = m
    return mods["rules_engine"], mods["neuro_symbolic_validator"]


def ctx(text, needle, width=28):
    i = text.lower().find(needle.lower())
    if i < 0:
        return ""
    return text[max(0, i - width):i + len(needle) + width].replace("\n", " ")


def main():
    args = sys.argv[1:]
    repo = args[args.index("--repo") + 1]
    every = "--all" in args
    rules, val = load_legacy(repo)
    _, harness_src = b.read(repo, b.HARNESS_REL)
    scen = {s["case_id"]: s for s in b.parse_scenarios(harness_src)}
    _, art_src = b.read(repo, b.ARTIFACT_REL)
    cmp_ = json.loads(art_src)["comparative_30_case_evaluation"]

    def evidence(sc, flags):
        lic, nat = (sc.get("has_valid_license", False), sc.get("is_national_flag", False)) if flags else (False, False)
        v = rules.evaluate_maritime_case(sc["distance_to_eez_nm"], sc["classification"], has_valid_license=lic, is_national_flag=nat)
        return val.EvidenceVector(
            target_id=sc["case_id"], lat=sc["lat"], lon=sc["lon"], length_m=sc["length_m"], beam_m=sc["beam_m"],
            peak_rcs_db=sc["peak_rcs_db"], snr_db=sc["snr_db"], distance_to_eez_nm=sc["distance_to_eez_nm"],
            classification=sc["classification"],
            boundary_status="INSIDE_ARGENTINE_EEZ" if sc["distance_to_eez_nm"] < 0 else "OUTSIDE_EEZ_BUFFER", legal_verdict=v)

    def ref_ev(sc, flags):
        lic, nat = (sc.get("has_valid_license", False), sc.get("is_national_flag", False)) if flags else (False, False)
        rid, zone, inf, pen = b.verdict(sc["distance_to_eez_nm"], sc["classification"], lic, nat)
        return {"lat": sc["lat"], "lon": sc["lon"], "distance_to_eez_nm": sc["distance_to_eez_nm"], "length_m": sc["length_m"],
                "classification": sc["classification"], "zone": zone, "infraction_code": inf, "max_penalty_usd": pen}

    parity_bad = 0
    print("== Resultados del artefacto (texto = primeros 240 caracteres) ==")
    explained = partial = 0
    for model, key in ((b.WONDER, "muse_wonder_results"), (b.SPARK, "muse_spark_results")):
        for r in cmp_[key]:
            if r["is_valid_raw"] and not every:
                continue
            sc = scen[r["case_id"]]
            text = rvs.normalize(r["narrative_preview"])
            res = val.validate_legal_narrative(text, evidence(sc, flags=False))
            _, found = rvs.validate_with_spans(text, ref_ev(sc, flags=False))
            if len(found) != res.discrepancy_count:
                parity_bad += 1
            n_orig = int(round((100.0 - r["adherence_percent"]) / 15.0))
            if res.discrepancy_count == n_orig:
                explained += 1
            else:
                partial += 1
            print("%s %s orig_n=%d preview_n=%d (paridad ref=%d)" % (r["case_id"], model, n_orig, res.discrepancy_count, len(found)))
            for f in found:
                print("    %-15s esperado=%s dijo=%s  [%s]  ...%s..." % (f["rule_id"], f["expected"], f["observed"], f["matched_text"], ctx(text, f["matched_text"])))
    print("\nresultados con n_preview == n_original: %d; con parte fuera de los 240 caracteres: %d" % (explained, partial))

    print("\n== Textos adversariales del harness (no son salida de modelos) ==")
    for cid, sc in scen.items():
        if "adversarial_llm_text" not in sc:
            continue
        res = val.validate_legal_narrative(sc["adversarial_llm_text"], evidence(sc, flags=True))
        _, found = rvs.validate_with_spans(sc["adversarial_llm_text"], ref_ev(sc, flags=True))
        if len(found) != res.discrepancy_count:
            parity_bad += 1
        print("%s legacy_n=%d ref_n=%d %s" % (cid, res.discrepancy_count, len(found), [f["rule_id"] + ":" + f["matched_text"] for f in found]))

    print("\n== Paridad (conteo) reference_validator_spans vs validador real, todas las pruebas: %s ==" %
          ("OK" if parity_bad == 0 else "%d diferencias" % parity_bad))
    sys.exit(1 if parity_bad else 0)


if __name__ == "__main__":
    main()
