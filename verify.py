#!/usr/bin/env python3
"""Kit de evaluación de OceanEye: verificación de la escena de referencia sin servidor.

Solo biblioteca estándar (Python 3.9+). Por defecto no usa la red: comprueba los
archivos de fixtures/ y resultados/. Con --api URL (o la variable OCEANEYE_API) ejecuta además
cinco pruebas contra una API en línea y verifica la firma del acta con el
verificador Ed25519 incluido en este archivo (RFC 8032, solo verificación).

Códigos de salida: 0 todo correcto; 1 alguna comprobación falló; 2 la API no responde.
"""

import argparse
import base64
import hashlib
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(AQUI, "fixtures")
RESULTADOS = os.path.join(AQUI, "resultados")

CLAVE_PUBLICA_ACTA = "585ea4596b1cf29c3f1221793ed34ab6f56e4a3d9b97a9f967d3b16d6e3e062a"

RFC8032_PUB = "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"
RFC8032_SIG = (
    "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555"
    "fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"
)

ESPERADO = {"blancos": 15, "confirmados": 3, "oscuros": 12, "fantasma": 1}


# ---------------------------------------------------------------------------
# Ed25519 (RFC 8032, sección 6, implementación de referencia; solo verificación)
# ---------------------------------------------------------------------------
_P = 2 ** 255 - 19
_Q = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_I = pow(2, (_P - 1) // 4, _P)


def _recuperar_x(y, signo):
    if y >= _P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    if x2 == 0:
        return None if signo else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * _I % _P
    if (x * x - x2) % _P != 0:
        return None
    if (x & 1) != signo:
        x = _P - x
    return x


_GY = 4 * pow(5, _P - 2, _P) % _P
_GX = _recuperar_x(_GY, 0)
_G = (_GX, _GY, 1, _GX * _GY % _P)
_NEUTRO = (0, 1, 1, 0)


def _sumar(a, b):
    A = (a[1] - a[0]) * (b[1] - b[0]) % _P
    B = (a[1] + a[0]) * (b[1] + b[0]) % _P
    C = 2 * a[3] * b[3] * _D % _P
    D = 2 * a[2] * b[2] % _P
    E, F, G, H = B - A, D - C, D + C, B + A
    return (E * F % _P, G * H % _P, F * G % _P, E * H % _P)


def _multiplicar(s, punto):
    q = _NEUTRO
    while s > 0:
        if s & 1:
            q = _sumar(q, punto)
        punto = _sumar(punto, punto)
        s >>= 1
    return q


def _iguales(a, b):
    return (a[0] * b[2] - b[0] * a[2]) % _P == 0 and (a[1] * b[2] - b[1] * a[2]) % _P == 0


def _descomprimir(s):
    if len(s) != 32:
        return None
    y = int.from_bytes(s, "little")
    signo = y >> 255
    y &= (1 << 255) - 1
    x = _recuperar_x(y, signo)
    if x is None:
        return None
    return (x, y, 1, x * y % _P)


def ed25519_verificar(clave_publica, mensaje, firma):
    """True si la firma Ed25519 es válida para (clave_publica, mensaje)."""
    if len(clave_publica) != 32 or len(firma) != 64:
        return False
    A = _descomprimir(clave_publica)
    R = _descomprimir(firma[:32])
    if A is None or R is None:
        return False
    s = int.from_bytes(firma[32:], "little")
    if s >= _Q:
        return False
    h = int.from_bytes(hashlib.sha512(firma[:32] + clave_publica + mensaje).digest(), "little") % _Q
    izq = _multiplicar(8, _multiplicar(s, _G))
    der = _multiplicar(8, _sumar(R, _multiplicar(h, A)))
    return _iguales(izq, der)


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
class Resultado:
    def __init__(self):
        self.ok = 0
        self.fallos = []

    def chequeo(self, condicion, descripcion, detalle=""):
        if condicion:
            self.ok += 1
            print("  [ok]    " + descripcion)
        else:
            self.fallos.append(descripcion)
            print("  [FALLA] " + descripcion + (" -> " + detalle if detalle else ""))
        return bool(condicion)


def cargar(nombre):
    with open(os.path.join(FIXTURES, nombre), encoding="utf-8") as f:
        return json.load(f)


def distancia_nm(lat1, lon1, lat2, lon2):
    f1, f2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((f2 - f1) / 2) ** 2 + math.cos(f1) * math.cos(f2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 3440.065 * math.asin(math.sqrt(a))


def relacion_eslora_manga(blanco):
    eslora = blanco.get("length_m") or 0
    manga = blanco.get("beam_m") or 0
    return eslora / manga if manga > 0 else 0.0


def acta_a_verificar(acta, clave_hex):
    """Reconstruye el mensaje canónico del acta y devuelve (valida, hash_ok)."""
    canonico = acta.get("canonical_payload")
    if not canonico:
        canonico = {
            "dossier_id": acta.get("dossier_id"),
            "target_id": acta.get("target_id"),
            "scene_id": acta.get("scene_id"),
            "satellite_sensor": "Sentinel-1A SAR C-Band",
            "timestamp_utc": acta.get("timestamp_utc"),
            "lat": acta.get("wgs84_latitude", acta.get("lat")),
            "lon": acta.get("wgs84_longitude", acta.get("lon")),
            "distance_to_eez_nm": acta.get("distance_to_eez_nm"),
            "boundary_status": acta.get("boundary_status"),
            "length_meters": acta.get("length_meters"),
            "beam_meters": acta.get("beam_meters"),
            "peak_rcs_db": acta.get("peak_rcs_db"),
            "legal_infraction": acta.get("legal_infraction"),
            "legal_framework": [
                "Ley Federal de Pesca 24.922 (Art. 51)",
                "CONVEMAR Art. 56 y 73",
                "Resolucion OMI A.1106(29)",
            ],
            "radar_chip_sha256": acta.get("radar_chip_sha256", hashlib.sha256(b"").hexdigest()),
        }
    mensaje = json.dumps(canonico, sort_keys=True).encode("utf-8")
    esperado = acta.get("sha256_evidence_hash") or acta.get("evidence_sha256")
    hash_ok = (hashlib.sha256(mensaje).hexdigest() == esperado) if esperado else True
    try:
        firma = base64.b64decode(acta.get("ed25519_signature_base64") or acta.get("signature_base64") or "")
        valida = ed25519_verificar(bytes.fromhex(clave_hex), mensaje, firma)
    except ValueError:
        valida = False
    return valida, hash_ok


# ---------------------------------------------------------------------------
# Comprobaciones sobre la escena de referencia
# ---------------------------------------------------------------------------
def comprobar_resultado(res, bench, etiqueta):
    """Coherencia interna de un resultado de benchmark (fixture o respuesta de la API)."""
    m = bench.get("metrics", {})
    oscuros = bench.get("dark_vessels", [])
    confirmados = bench.get("confirmed_matches", [])
    fantasmas = bench.get("ghost_ais", [])
    res.chequeo(len(oscuros) == ESPERADO["oscuros"] and m.get("total_dark_vessels") == len(oscuros),
                "%s: 12 blancos oscuros (lista y métrica coinciden)" % etiqueta,
                "lista=%d métrica=%s" % (len(oscuros), m.get("total_dark_vessels")))
    res.chequeo(len(confirmados) == ESPERADO["confirmados"] and m.get("total_confirmed_ais") == len(confirmados),
                "%s: 3 blancos confirmados por AIS (lista y métrica coinciden)" % etiqueta,
                "lista=%d métrica=%s" % (len(confirmados), m.get("total_confirmed_ais")))
    res.chequeo(len(fantasmas) == ESPERADO["fantasma"] and m.get("total_ghost_ais") == len(fantasmas),
                "%s: 1 AIS fantasma (lista y métrica coinciden)" % etiqueta,
                "lista=%d métrica=%s" % (len(fantasmas), m.get("total_ghost_ais")))
    total = len(oscuros) + len(confirmados)
    res.chequeo(total == ESPERADO["blancos"] and m.get("total_sar_targets") == total,
                "%s: 15 blancos = oscuros + confirmados" % etiqueta,
                "suma=%d métrica=%s" % (total, m.get("total_sar_targets")))
    pct = round(100.0 * len(oscuros) / total, 1) if total else None
    res.chequeo(pct is not None and m.get("dark_vessel_percentage") == pct,
                "%s: porcentaje de oscuros recalculado (%s %%)" % (etiqueta, pct),
                "informado=%s" % m.get("dark_vessel_percentage"))
    blancos = list(oscuros) + [c.get("sar_target", {}) for c in confirmados]
    malos = [b.get("id") for b in blancos if relacion_eslora_manga(b) < 2.0]
    res.chequeo(not malos, "%s: eslora/manga >= 2 en los %d blancos" % (etiqueta, len(blancos)),
                "incumplen: %s" % malos)
    t = bench.get("laya_system1_triage", {})
    descartadas, activas, tot = t.get("discarded_empty_tiles"), t.get("active_tiles"), t.get("total_tiles")
    res.chequeo(None not in (descartadas, activas, tot) and descartadas + activas == tot,
                "%s: triage, descartadas + activas = total" % etiqueta,
                "%s + %s != %s" % (descartadas, activas, tot))
    if tot:
        pct_t = round(100.0 * descartadas / tot, 1)
        res.chequeo(t.get("rejection_rate_percent") == pct_t,
                    "%s: triage, porcentaje recalculado (%s %%)" % (etiqueta, pct_t),
                    "informado=%s" % t.get("rejection_rate_percent"))


def comprobar_fixtures(res):
    meta = cargar("fixture_metadata.json")
    bench = cargar("sample_benchmark.json")
    gt = meta["ground_truth_targets"]
    con_ais = [g for g in gt if g.get("has_active_ais")]
    sin_ais = [g for g in gt if not g.get("has_active_ais")]
    mmsi_gt = set(g["mmsi"] for g in con_ais)
    fantasmas = [r for r in meta.get("ais_reports", [])
                 if not any(distancia_nm(r["lat"], r["lon"], g["lat"], g["lon"]) < 1.0 for g in gt)]

    print("Verdad de terreno (fixture_metadata.json)")
    res.chequeo(len(gt) == meta.get("total_ground_truth_targets") == ESPERADO["blancos"],
                "15 blancos recalculados de la lista, igual al total declarado")
    res.chequeo(len(con_ais) == meta.get("expected_ais_confirmed") == ESPERADO["confirmados"],
                "3 blancos con AIS activo, igual a lo declarado")
    res.chequeo(len(sin_ais) == meta.get("expected_dark_vessels") == ESPERADO["oscuros"],
                "12 blancos sin AIS, igual a lo declarado")
    res.chequeo(len(fantasmas) == meta.get("expected_ghost_ais") == ESPERADO["fantasma"],
                "1 emisor AIS sin blanco a menos de 1 NM, igual a lo declarado")
    res.chequeo(all(relacion_eslora_manga(g) >= 2.0 for g in gt),
                "eslora/manga >= 2 en los 15 blancos de la verdad de terreno")

    print("Resultado de la escena (sample_benchmark.json)")
    res.chequeo(bench.get("scene_id") == meta.get("scene_id"), "misma escena en resultado y metadatos")
    comprobar_resultado(res, bench, "resultado")

    print("Correspondencia con la verdad de terreno")
    libres = list(sin_ais)
    sin_par = []
    for v in bench["dark_vessels"]:
        mejor = min(libres, key=lambda g: math.hypot(g["pixel_r"] - v["pixel_r"], g["pixel_c"] - v["pixel_c"]),
                    default=None)
        if mejor is not None and math.hypot(mejor["pixel_r"] - v["pixel_r"], mejor["pixel_c"] - v["pixel_c"]) <= 2.0 \
                and distancia_nm(v["lat"], v["lon"], mejor["lat"], mejor["lon"]) <= 0.5:
            libres.remove(mejor)
        else:
            sin_par.append(v["id"])
    res.chequeo(not sin_par and not libres,
                "cada blanco oscuro del resultado corresponde a uno distinto de la verdad de terreno sin AIS "
                "(12 de 12, tolerancia 2 px y 0,5 NM)",
                "sin par: %s; sin recuperar: %s" % (sin_par, [g["target_id"] for g in libres]))
    ok_conf = True
    for c in bench["confirmed_matches"]:
        s, a = c["sar_target"], c["ais_vessel"]
        par = [g for g in con_ais if g["mmsi"] == a.get("mmsi")]
        ok_conf = ok_conf and len(par) == 1 and a["mmsi"] in mmsi_gt \
            and math.hypot(par[0]["pixel_r"] - s["pixel_r"], par[0]["pixel_c"] - s["pixel_c"]) <= 2.0
    res.chequeo(ok_conf, "cada confirmación AIS une un blanco con el MMSI ficticio de su verdad de terreno")
    ghost = [g.get("mmsi") for g in bench["ghost_ais"]]
    res.chequeo(ghost == [f["mmsi"] for f in fantasmas],
                "el AIS fantasma del resultado es el emisor sin blanco de los metadatos")


def matrices_compatibles(n, precision, recall, exactitud):
    """Matrices de confusión enteras (TP, FP, FN, TN) de n muestras que dan esas tres cifras a 4 decimales."""
    halladas = []
    for tp in range(1, n + 1):
        for fp in range(max(0, int(tp * (1 / precision - 1)) - 1), int(tp * (1 / precision - 1)) + 3):
            if round(tp / (tp + fp), 4) != precision:
                continue
            for fn in range(max(0, int(tp * (1 / recall - 1)) - 1), int(tp * (1 / recall - 1)) + 3):
                tn = n - tp - fp - fn
                if tn >= 0 and round(tp / (tp + fn), 4) == recall and round((tp + tn) / n, 4) == exactitud:
                    halladas.append((tp, fp, fn, tn))
    return halladas


def comprobar_resultados_registrados(res):
    print("Métricas del clasificador (resultados/evaluation_metrics.json)")
    with open(os.path.join(RESULTADOS, "evaluation_metrics.json"), encoding="utf-8") as f:
        met = json.load(f)
    m = met["test_metrics"]
    p, r, n = m["precision"], m["recall"], met["test_samples"]
    res.chequeo((met["train_samples"], met["val_samples"], n) == (2501, 2500, 1542),
                "2501 recortes de entrenamiento, 2500 de validación y 1542 de prueba")
    f1 = round(2 * p * r / (p + r), 4)
    res.chequeo(f1 == m["f1_score"], "F1 recalculado desde precisión y exhaustividad (%.4f)" % f1)
    matrices = matrices_compatibles(n, p, r, m["accuracy"])
    res.chequeo(len(matrices) > 0,
                "precisión, exhaustividad y exactitud compatibles con una matriz de confusión entera de %d recortes%s"
                % (n, " (p. ej. TP=%d, FP=%d, FN=%d, TN=%d)" % matrices[0] if matrices else ""))

    print("Motor de reglas y validador (resultados/evaluation_results.json)")
    with open(os.path.join(RESULTADOS, "evaluation_results.json"), encoding="utf-8") as f:
        casos = json.load(f)["scenarios"]
    ids = [c["case_id"] for c in casos]
    res.chequeo(len(casos) == 30 and len(set(ids)) == 30, "30 casos con identificador único")
    aciertos = sum(1 for c in casos if c["rule_match"] is True)
    res.chequeo(aciertos == len(casos), "%d de %d casos con la regla esperada" % (aciertos, len(casos)))
    adulterados = [c for c in casos if c["case_id"].startswith("TC-ADV")]
    detectados = sum(1 for c in adulterados if c["hallucination_detected"] is True)
    res.chequeo(len(adulterados) == 8 and detectados == 8,
                "%d de %d textos adulterados detectados" % (detectados, len(adulterados)))
    limpios = [c for c in casos if not c["case_id"].startswith("TC-ADV")]
    res.chequeo(not any(c["hallucination_detected"] for c in limpios),
                "ninguna detección en los %d casos sin adulterar" % len(limpios))


def autotest_ed25519(res):
    print("Verificador Ed25519 (RFC 8032, vector de prueba 1)")
    pub, sig = bytes.fromhex(RFC8032_PUB), bytes.fromhex(RFC8032_SIG)
    res.chequeo(ed25519_verificar(pub, b"", sig), "firma del vector 1 aceptada")
    alterada = bytearray(sig)
    alterada[10] ^= 0x01
    res.chequeo(not ed25519_verificar(pub, b"", bytes(alterada)), "firma alterada rechazada")
    res.chequeo(not ed25519_verificar(pub, b"x", sig), "mensaje alterado rechazado")


def comprobar_acta_incluida(res):
    ruta = os.path.join(FIXTURES, "sample_dossier.json")
    print("Acta firmada")
    if not os.path.exists(ruta):
        print("  [info]  No hay acta firmada incluida; verificar una requiere el modo --api.")
        return
    with open(ruta, encoding="utf-8") as f:
        doc = json.load(f)
    acta = doc.get("laya_ed25519_certificate", doc)
    valida, hash_ok = acta_a_verificar(acta, CLAVE_PUBLICA_ACTA)
    res.chequeo(valida and hash_ok, "firma Ed25519 y hash SHA-256 del acta incluida válidos con la clave publicada")


# ---------------------------------------------------------------------------
# Modo en línea
# ---------------------------------------------------------------------------
def http(url, datos=None, timeout=20):
    cuerpo = json.dumps(datos).encode("utf-8") if datos is not None else None
    cab = {"User-Agent": "OceanEye-Evaluation-Kit/2.0", "Accept": "application/json"}
    if cuerpo is not None:
        cab["Content-Type"] = "application/json"
    with urllib.request.urlopen(urllib.request.Request(url, data=cuerpo, headers=cab), timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def modo_api(res, base):
    base = base.rstrip("/")
    print("Modo en línea: %s" % base)
    t0 = time.time()
    print("[1/5] GET /api/v1/sar/benchmark")
    bench = http(base + "/api/v1/sar/benchmark")
    print("      respuesta en %.0f ms; escena %s" % ((time.time() - t0) * 1000, bench.get("scene_id")))
    comprobar_resultado(res, bench, "API")
    objetivo = bench["dark_vessels"][0]["id"] if bench.get("dark_vessels") else None

    print("[2/5] GET /api/v1/sar/legal/benchmark")
    legal = http(base + "/api/v1/sar/legal/benchmark")
    res.chequeo(legal.get("total_test_cases") == 30, "30 casos jurídicos", "recibidos=%s" % legal.get("total_test_cases"))
    res.chequeo(legal.get("rule_engine_accuracy_percent") == 100.0, "30 de 30 casos tipificados como se esperaba")
    res.chequeo(legal.get("adversarial_hallucination_catch_rate_percent") == 100.0,
                "textos adulterados detectados en el 100 % del conjunto")

    print("[3/5] GET /api/v1/sar/laya/public-key")
    clave = http(base + "/api/v1/sar/laya/public-key")
    clave_hex = clave.get("public_key_hex", "")
    res.chequeo(clave.get("algorithm") == "Ed25519" and len(clave_hex) == 64, "clave pública Ed25519 de 32 bytes")
    print("      clave publicada por la API: %s" % clave_hex)
    print("      clave de desarrollo; no hay custodia en HSM o KMS todavía.")

    if not objetivo:
        res.chequeo(False, "hay un blanco oscuro para pedir su acta")
        return
    print("[4/5] GET /api/v1/sar/dossier/%s" % objetivo)
    acta = http(base + "/api/v1/sar/dossier/" + objetivo)
    cert = acta.get("laya_ed25519_certificate", {})
    res.chequeo(len(acta.get("chain_of_custody_hash_sha256", "")) == 64, "hash SHA-256 del acta de 64 caracteres hexadecimales")
    res.chequeo(bool(cert.get("ed25519_signature_base64")), "el acta incluye firma Ed25519")

    print("[5/5] POST /api/v1/sar/laya/verify y verificación local")
    srv = http(base + "/api/v1/sar/laya/verify", cert)
    res.chequeo(srv.get("is_valid") is True, "el servidor declara válida la firma")
    valida, hash_ok = acta_a_verificar(cert, clave_hex)
    res.chequeo(valida, "firma verificada localmente con el verificador Ed25519 incluido en este archivo")
    res.chequeo(hash_ok, "hash SHA-256 del mensaje canónico recalculado localmente")


def main():
    ap = argparse.ArgumentParser(description="Verificación de la escena de referencia de OceanEye.")
    ap.add_argument("--api", default=os.environ.get("OCEANEYE_API"),
                    help="URL base de una API de OceanEye (opcional; también OCEANEYE_API)")
    args = ap.parse_args()

    print("OceanEye - kit de evaluación")
    print("La escena de referencia es SINTÉTICA (semilla fija 1982, 15 blancos inyectados);")
    print("los nombres, MMSI e IMO de los buques son ficticios. Verifica coherencia del flujo,")
    print("no la precisión de detección sobre escenas reales.\n")

    res = Resultado()
    try:
        comprobar_fixtures(res)
        print()
        comprobar_resultados_registrados(res)
        print()
        autotest_ed25519(res)
        print()
        comprobar_acta_incluida(res)
        if args.api:
            print()
            modo_api(res, args.api)
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        print("\nNo se pudo contactar con la API (%s): %s" % (args.api, e))
        print("El servicio en línea puede estar en migración. Sin --api el kit funciona sin red.")
        return 2
    except (OSError, ValueError, KeyError) as e:
        print("\nError al leer los datos: %s: %s" % (type(e).__name__, e))
        return 1

    print("\n%d comprobaciones correctas, %d con falla." % (res.ok, len(res.fallos)))
    if res.fallos:
        print("Fallas: " + "; ".join(res.fallos))
        return 1
    print("Todas las comprobaciones pasaron.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
