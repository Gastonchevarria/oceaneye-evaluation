#!/usr/bin/env python3
"""Valida un arena_replay.*.json contra el schema y comprueba invariantes que el schema no expresa.

Uso:  python3 -I validate_arena.py [archivo.json] [--schema arena_replay.schema.json] [--repo RUTA] [--selftest]

Requiere el paquete jsonschema (no se instala nada: se busca en el site-packages del usuario).
--repo    contrasta verdad de cada caso y veredicto contra backend/app/legal/rules_engine.py real
          (necesita pydantic; carga el motor sin importar el paquete app).
--selftest  construye variantes rotas y una variante v1 con stream_timeline y exige que el schema las trate bien.
"""

import copy
import importlib.util
import json
import os
import site
import sys
import types
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.append(site.getusersitepackages())
sys.path.insert(0, HERE)
import jsonschema  # noqa: E402
import reference_validator_spans as rvs  # noqa: E402


def u16(s):
    return s.encode("utf-16-le")


def slice16(s, a, b):
    return u16(s)[2 * a:2 * b].decode("utf-16-le")


def len16(s):
    return len(u16(s)) // 2


def check_invariants(doc):
    errs = []
    case_ids = [c["case_id"] for c in doc["cases"]]
    if len(case_ids) != len(set(case_ids)):
        errs.append("case_id repetido")
    if doc["replay"]["featured_case_id"] not in (None, *case_ids):
        errs.append("featured_case_id no existe en cases")
    if doc["replay"]["sides"]["left"] == doc["replay"]["sides"]["right"]:
        errs.append("replay.sides repite el modelo")
    if doc["replay"]["default_speed"] not in doc["replay"]["speed_options"]:
        errs.append("default_speed fuera de speed_options")
    prices = doc["provenance"]["price_source"]["usd_per_1m_tokens"]
    ui = doc["data_quality"]["ui_policy"]
    for c in doc["cases"]:
        for model, run in c["runs"].items():
            tag = "%s/%s" % (c["case_id"], model)
            if run["model_requested"] != model:
                errs.append(tag + ": model_requested != clave")
            u = run["usage"]
            if u["total"] != u["prompt"] + u["completion"]:
                errs.append(tag + ": usage.total != prompt + completion")
            if u["reasoning"] > u["completion"]:
                errs.append(tag + ": reasoning > completion")
            p = prices[model]
            cost = round((u["prompt"] * p["input"] + u["completion"] * p["output"]) / 1e6, 6)
            if abs(cost - run["cost_usd_estimate"]) > 1e-9:
                errs.append(tag + ": cost_usd_estimate %s != %s" % (run["cost_usd_estimate"], cost))
            nar = run["narrative_full"]
            if unicodedata.normalize("NFC", nar) != nar:
                errs.append(tag + ": narrative_full no esta en NFC")
            if run["narrative_truncated"] and not ui["narrative_is_truncated"]:
                errs.append(tag + ": ui_policy dice no truncado pero la corrida esta truncada")
            if run["timing_basis"] == "interpolated":
                est = run["interpolation"]["estimated_ttft_ms"]
                if not (0 <= est <= run["latency_ms"]):
                    errs.append(tag + ": estimated_ttft_ms fuera de [0, latency_ms]")
                if abs(est - round(run["latency_ms"] * u["reasoning"] / u["completion"], 2)) > 0.011:
                    errs.append(tag + ": estimated_ttft_ms no sale de la formula")
            else:
                tl = run["stream_timeline"]
                ts = [t for t, _ in tl]
                offs = [o for _, o in tl]
                if ts != sorted(ts) or offs != sorted(offs):
                    errs.append(tag + ": stream_timeline no es monotono")
                if offs[-1] != len16(nar):
                    errs.append(tag + ": ultimo checkpoint != largo de narrative_full (UTF-16)")
                if ts[-1] > run["latency_ms"] + 1e-6:
                    errs.append(tag + ": checkpoint posterior a latency_ms")
                if run["time_to_first_token_ms"] > ts[-1]:
                    errs.append(tag + ": TTFT posterior al ultimo checkpoint")
            v = run["validator"]
            errs += check_validator(tag, nar, v, truncated=run["narrative_truncated"])
        pr = c["synthetic_probe"]
        if pr is not None:
            errs += check_validator(c["case_id"] + "/probe", pr["text"], pr["validator"], truncated=False)
        if pr is not None and not c["scenario"]["adversarial"]:
            errs.append(c["case_id"] + ": synthetic_probe en un caso no adversarial")
    return errs


def check_validator(tag, text, v, truncated):
    errs = []
    n = len(v["findings"])
    if v["is_valid"] != (n == 0) and v.get("findings_available", True):
        errs.append(tag + ": is_valid inconsistente con findings")
    if v.get("findings_available", True):
        want = 100.0 if n == 0 else max(0.0, 100.0 - 15.0 * n)
        if abs(v["adherence_percent"] - want) > 1e-9:
            errs.append(tag + ": adherence_percent %s != max(0, 100 - 15*%d)" % (v["adherence_percent"], n))
    for f in v["findings"]:
        if f["char_end"] <= f["char_start"]:
            errs.append(tag + ": span vacio o invertido")
        if f["char_end"] > len16(text):
            errs.append(tag + ": span fuera del texto")
        elif slice16(text, f["char_start"], f["char_end"]) != f["matched_text"]:
            errs.append(tag + ": matched_text != texto[char_start:char_end]")
    if truncated and n and v.get("status") == "verified":
        errs.append(tag + ": hallazgos 'verified' sobre texto truncado")
    return errs


def load_real_rules_engine(repo):
    sys.path.append(site.getusersitepackages())
    pkg = types.ModuleType("app")
    pkg.__path__ = []
    sub = types.ModuleType("app.legal")
    sub.__path__ = []
    sys.modules["app"], sys.modules["app.legal"] = pkg, sub
    spec = importlib.util.spec_from_file_location("app.legal.rules_engine", os.path.join(repo, "backend/app/legal/rules_engine.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["app.legal.rules_engine"] = mod
    spec.loader.exec_module(mod)
    return mod


def check_against_rules_engine(doc, repo):
    errs = []
    re_ = load_real_rules_engine(repo)
    import build_arena_v0 as b  # noqa: E402
    _, harness_src = b.read(repo, b.HARNESS_REL)
    scen = {s["case_id"]: s for s in b.parse_scenarios(harness_src)}
    for cid, sc in scen.items():
        for lic, nat in ((sc.get("has_valid_license", False), sc.get("is_national_flag", False)), (False, False)):
            real = re_.evaluate_maritime_case(sc["distance_to_eez_nm"], sc["classification"], has_valid_license=lic, is_national_flag=nat)
            mine = b.verdict(sc["distance_to_eez_nm"], sc["classification"], lic, nat)
            pen = real.economic_penalty_range_usd[1] if real.economic_penalty_range_usd else None
            if (real.rule_id, real.jurisdiction_zone.value, real.infraction_code, pen) != mine:
                errs.append("%s: veredicto espejo difiere del motor real (flags %s/%s)" % (cid, lic, nat))
    for c in doc["cases"]:
        sc = scen[c["case_id"]]
        gt = c["scenario"]["ground_truth"]
        for k in ("lat", "lon", "distance_to_eez_nm", "length_m", "beam_m", "peak_rcs_db", "snr_db", "classification"):
            if gt[k] != sc[k]:
                errs.append("%s: ground_truth.%s != harness" % (c["case_id"], k))
        mine = b.verdict(sc["distance_to_eez_nm"], sc["classification"], sc.get("has_valid_license", False), sc.get("is_national_flag", False))
        if gt.get("infraction_code") != mine[2]:
            errs.append("%s: ground_truth.infraction_code != veredicto del motor" % c["case_id"])
    return errs


def selftest(schema, doc):
    v = jsonschema.Draft202012Validator(schema)
    bad = []

    def mutate(label, fn):
        d = copy.deepcopy(doc)
        fn(d)
        if not list(v.iter_errors(d)):
            bad.append("mutacion aceptada por el schema: " + label)

    mutate("label LIVE", lambda d: d["replay"].__setitem__("label", "LIVE"))
    mutate("sin narrative_full", lambda d: d["cases"][0]["runs"]["muse-wonder-1.0"].pop("narrative_full"))
    mutate("modelo desconocido", lambda d: d["cases"][0]["runs"].__setitem__("gpt-x", d["cases"][0]["runs"]["muse-spark-1.3"]))
    mutate("n_per_case 3", lambda d: d["provenance"].__setitem__("n_per_case", 3))
    mutate("sha256 corto", lambda d: d["provenance"]["validator"].__setitem__("sha256", "abc"))
    mutate("replay_banner_required false", lambda d: d["data_quality"]["ui_policy"].__setitem__("replay_banner_required", False))
    mutate("findings con findings_available false", lambda d: d["cases"][0]["runs"]["muse-wonder-1.0"]["validator"].__setitem__(
        "findings", [{"rule_id": "NUM-LAT-001", "field": "latitude", "expected": 1, "observed": 2, "matched_text": "x",
                      "char_start": 0, "char_end": 1, "severity": "high"}]))
    mutate("timeline en run interpolado", lambda d: d["cases"][0]["runs"]["muse-wonder-1.0"].__setitem__("stream_timeline", [[0, 0], [10, 5]]))
    mutate("probe marcado como salida de modelo", lambda d: next(c for c in d["cases"] if c["synthetic_probe"])["synthetic_probe"].__setitem__("is_model_output", True))
    mutate("span con char_end 0", lambda d: next(c for c in d["cases"] if c["synthetic_probe"])["synthetic_probe"]["validator"]["findings"][0].__setitem__("char_end", 0))

    good = copy.deepcopy(doc)
    good["provenance"]["dataset_version"] = "v1-streamed-capture"
    good["data_quality"]["ui_policy"].update({"red_pen_enabled": True, "narrative_is_truncated": False,
                                              "timing_is_interpolated": False, "validator_verified": True})
    for c in good["cases"]:
        for model, run in c["runs"].items():
            n = len16(run["narrative_full"])
            run.update({"model_served": model + "-2026-10-29", "narrative_truncated": False, "narrative_source": "stream",
                        "timing_basis": "measured_sse", "interpolation": None, "time_to_first_token_ms": 1000.0,
                        "stream_timeline": [[1000.0, 0], [1500.0, n // 2], [run["latency_ms"], n]],
                        "finish_reason": "stop"})
            run["validator"].update({"status": "verified", "findings_available": True, "findings": [],
                                     "is_valid": True, "adherence_percent": 100.0})
    errs = list(jsonschema.Draft202012Validator(schema).iter_errors(good))
    if errs:
        bad.append("variante v1 valida rechazada: " + errs[0].message)
    good2 = copy.deepcopy(good)
    good2["cases"][0]["runs"]["muse-wonder-1.0"]["model_served"] = None
    if not list(jsonschema.Draft202012Validator(schema).iter_errors(good2)):
        bad.append("v1 con model_served null aceptado")
    inv = check_invariants(good)
    if inv:
        bad.append("invariantes v1: " + "; ".join(inv[:3]))
    return bad


def main():
    args = sys.argv[1:]
    schema_path = os.path.join(HERE, "arena_replay.schema.json")
    repo = None
    run_selftest = False
    files = []
    i = 0
    while i < len(args):
        if args[i] == "--schema":
            schema_path = args[i + 1]
            i += 2
        elif args[i] == "--repo":
            repo = args[i + 1]
            i += 2
        elif args[i] == "--selftest":
            run_selftest = True
            i += 1
        else:
            files.append(args[i])
            i += 1
    if not files:
        files = [os.path.join(HERE, "arena_replay.v0.example.json")]
    with open(schema_path, encoding="utf-8") as f:
        schema = json.load(f)
    jsonschema.Draft202012Validator.check_schema(schema)
    print("schema OK (draft 2020-12, %s)" % os.path.basename(schema_path))
    failed = False
    for path in files:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        schema_errs = sorted(jsonschema.Draft202012Validator(schema).iter_errors(doc), key=lambda e: list(e.path))
        for e in schema_errs[:15]:
            print("SCHEMA %s: %s" % ("/".join(map(str, e.path)), e.message[:200]))
        inv = check_invariants(doc) if not schema_errs else []
        if repo and not schema_errs:
            inv += check_against_rules_engine(doc, repo)
        for e in inv:
            print("INVARIANTE:", e)
        ok = not schema_errs and not inv
        failed |= not ok
        print("%s: %s (%d casos)" % (os.path.basename(path), "OK" if ok else "FALLA", len(doc.get("cases", []))))
        if run_selftest and ok:
            bad = selftest(schema, doc)
            for b in bad:
                print("SELFTEST:", b)
            failed |= bool(bad)
            print("selftest: %s" % ("OK" if not bad else "FALLA"))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
