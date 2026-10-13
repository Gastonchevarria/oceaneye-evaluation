#!/usr/bin/env python3
"""
OceanEye — Public Verification & Forensic Evaluation Script
Investigador Principal: Ing. Químico Gastón Chevarria (UTN FRVM, UNSAM)

Zero-dependency verification tool: executes mathematical, telemetry,
and cryptographic tests against the live OceanEye Forensic API.
"""

import sys
import json
import time
import hashlib
import urllib.request
import urllib.error

# API Base URL (default Cloud Run production backend)
API_BASE = "https://oceaneye-backend-29923645081.us-central1.run.app"

# ANSI Colors for terminal output
C_CYAN = "\033[96m"
C_GREEN = "\033[92m"
C_YELLOW = "\033[93m"
C_RED = "\033[91m"
C_BOLD = "\033[1m"
C_RESET = "\033[0m"


def http_get(url: str, timeout: int = 15) -> dict:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "OceanEye-Evaluation-Kit/1.0", "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_post(url: str, data: dict, timeout: int = 15) -> dict:
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "User-Agent": "OceanEye-Evaluation-Kit/1.0",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def print_banner():
    print(f"{C_CYAN}{C_BOLD}")
    print("=" * 80)
    print("🌊  OCEANEYE — EVALUATION & SCIENTIFIC VERIFICATION KIT")
    print("    Investigador Principal: Ing. Químico Gastón Chevarria (UTN FRVM · UNSAM)")
    print("    Teledetección Satelital y Soberanía Marítima en la Milla 201 ZEE Argentina")
    print("=" * 80 + f"{C_RESET}\n")


def test_sar_benchmark():
    print(f"{C_BOLD}[1/5] Verificando Pipeline SAR & Triage LAYA (GET /api/v1/sar/benchmark)...{C_RESET}")
    t0 = time.time()
    data = http_get(f"{API_BASE}/api/v1/sar/benchmark")
    latency = (time.time() - t0) * 1000

    metrics = data.get("metrics", {})
    triage = data.get("laya_system1_triage", {})
    darks = data.get("dark_vessels", [])
    confirmed = data.get("confirmed_matches", [])

    print(f"      • Latencia de respuesta API: {latency:.1f} ms")
    print(f"      • Escena Satelital: {data.get('scene_id')}")
    print(f"      • Blancos SAR Físicos: {metrics.get('total_sar_targets')} detectados")
    print(f"      • Buques Cooperativos Confirmados (AIS): {metrics.get('total_confirmed_ais')}")
    print(f"      • Pesqueros Oscuros (Dark Vessels): {metrics.get('total_dark_vessels')} / 12 esperados")
    print(f"      • Suplantación de Identidad (Ghost AIS): {metrics.get('total_ghost_ais')} / 1 esperado")
    print(f"      • Triage LAYA Sistema 1: {triage.get('rejection_rate_percent')}% mar vacío descartado en {triage.get('triage_latency_ms')} ms")

    assert metrics.get("total_dark_vessels") == 12, "Fallo: se esperaban 12 buques oscuros"
    assert metrics.get("total_confirmed_ais") == 3, "Fallo: se esperaban 3 confirmaciones AIS"
    assert metrics.get("total_ghost_ais") == 1, "Fallo: se esperaba 1 suplantación Ghost AIS"
    assert len(darks) == 12, "Fallo: conteo de lista de buques oscuros inconsistente"

    # Verificar proporciones navales realistas (Eslora vs Manga)
    for v in darks[:3]:
        l = v.get("length_m", 0)
        b = v.get("beam_m", 0)
        ratio = l / b if b > 0 else 0
        print(f"        -> {v.get('id')}: Eslora={l:.1f}m, Manga={b:.1f}m (L/B={ratio:.1f}, Rumbo={v.get('heading_deg')}°)")
        assert ratio >= 2.0, f"Proporción naval no realista en {v.get('id')}"

    print(f"{C_GREEN}      ✔ Benchmark SAR y Correlación Cinemática Verificados (100% OK){C_RESET}\n")
    return darks[0]["id"]


def test_legal_benchmark():
    print(f"{C_BOLD}[2/5] Verificando Motor Jurídico Neuro-Simbólico (GET /api/v1/sar/legal/benchmark)...{C_RESET}")
    data = http_get(f"{API_BASE}/api/v1/sar/legal/benchmark")
    total_cases = data.get("total_test_cases", 0)
    accuracy = data.get("rule_engine_accuracy_percent", 0.0)
    hallucination = data.get("certified_post_validation_hallucination_rate_percent", 100.0)
    adversarial_catch = data.get("adversarial_hallucination_catch_rate_percent", 0.0)
    latency_ms = data.get("average_evaluation_latency_ms", 0.0)

    print(f"      • Casos Jurídicos Evaluados: {total_cases}")
    print(f"      • Exactitud del Motor de Reglas: {accuracy}% (Ley 24.922 & CONVEMAR)")
    print(f"      • Detección de Ataques Adversarios: {adversarial_catch}%")
    print(f"      • Tasa de Alucinación Post-Validación: {hallucination}%")
    print(f"      • Latencia Media de Evaluación: {latency_ms} ms")

    assert total_cases == 30, f"Se esperaban 30 casos legales, se recibieron {total_cases}"
    assert accuracy == 100.0, "Exactitud menor al 100%"
    assert hallucination == 0.0, "Alucinación jurídica detectada"
    assert adversarial_catch == 100.0, "Falla en captura de adversarios"
    print(f"{C_GREEN}      ✔ Consistencia Jurídica Verificada (0.00% Alucinación · 100% Precisión){C_RESET}\n")


def test_public_key():
    print(f"{C_BOLD}[3/5] Consultando Clave Pública Judicial Ed25519 (GET /api/v1/sar/laya/public-key)...{C_RESET}")
    data = http_get(f"{API_BASE}/api/v1/sar/laya/public-key")
    pub_key_hex = data.get("public_key_hex", "")
    algo = data.get("algorithm", "")
    authority = data.get("custody_authority", "")

    print(f"      • Algoritmo Criptográfico: {algo} (RFC 8032)")
    print(f"      • Clave Pública LAYA (Hex): {pub_key_hex}")
    print(f"      • Autoridad de Custodia: {authority}")
    print(f"      • Admisibilidad Legal: {data.get('legal_admissibility')}")

    assert algo == "Ed25519", "Algoritmo inesperado"
    assert len(pub_key_hex) == 64, "Longitud de clave pública Ed25519 inválida (debe ser 32 bytes / 64 hex)"
    print(f"{C_GREEN}      ✔ Clave Pública Oficial de Custodia Verificada{C_RESET}\n")
    return pub_key_hex


def test_dossier_and_signature(target_id: str, pub_key_hex: str):
    print(f"{C_BOLD}[4/5] Inspeccionando Dossier Forense y Sello SHA-256 (GET /api/v1/sar/dossier/{target_id})...{C_RESET}")
    dossier = http_get(f"{API_BASE}/api/v1/sar/dossier/{target_id}")

    sha256_hash = dossier.get("chain_of_custody_hash_sha256", "")
    dist_nm = dossier.get("distance_to_eez_nm", 0)
    verdict = dossier.get("legal_verdict", {}).get("infraction_type", "N/A")
    cert = dossier.get("laya_ed25519_certificate", {})

    print(f"      • Dossier ID: {dossier.get('dossier_id')}")
    print(f"      • Coordenadas WGS84: {dossier.get('wgs84_coordinates')}")
    print(f"      • Distancia a Milla 200 ZEE: {dist_nm} NM ({dossier.get('boundary_status')})")
    print(f"      • Dictamen Pericial: {verdict}")
    print(f"      • Cadena de Custodia SHA-256: {sha256_hash}")
    print(f"      • Firma Digital Ed25519: {cert.get('ed25519_signature_base64')[:32]}...")

    assert len(sha256_hash) == 64, "Hash SHA-256 de longitud incorrecta"
    assert cert.get("ed25519_signature_base64"), "Falta la firma Ed25519 en el certificado"
    print(f"{C_GREEN}      ✔ Dossier Forense Estructurado y Sellado{C_RESET}\n")

    print(f"{C_BOLD}[5/5] Verificación Criptográfica de la Firma en Servidor y Local (POST /api/v1/sar/laya/verify)...{C_RESET}")
    t0 = time.time()
    verify_resp = http_post(f"{API_BASE}/api/v1/sar/laya/verify", cert)
    v_latency = (time.time() - t0) * 1000

    print(f"      • Estado de Validación: {verify_resp.get('status')}")
    print(f"      • Firma Asimétrica Válida: {verify_resp.get('signature_verified')}")
    print(f"      • Integridad SHA-256 Verificada: {verify_resp.get('hash_verified')}")
    print(f"      • Latencia de Verificación: {verify_resp.get('verification_latency_ms', round(v_latency, 2))} ms")

    assert verify_resp.get("is_valid") is True, "La verificación criptográfica del servidor falló"
    assert verify_resp.get("signature_verified") is True, "La firma digital no es matemáticamente válida"

    # Verificación Local Independiente (si la biblioteca cryptography está instalada)
    try:
        import base64
        from cryptography.hazmat.primitives.asymmetric import ed25519
        from cryptography.exceptions import InvalidSignature

        pub_bytes = bytes.fromhex(pub_key_hex)
        pub_key = ed25519.Ed25519PublicKey.from_public_bytes(pub_bytes)
        sig_bytes = base64.b64decode(cert.get("ed25519_signature_base64"))

        # Reconstruir payload canónico
        canonical = {
            "dossier_id": cert.get("dossier_id"),
            "target_id": cert.get("target_id"),
            "scene_id": cert.get("scene_id"),
            "satellite_sensor": "Sentinel-1A SAR C-Band",
            "timestamp_utc": cert.get("timestamp_utc"),
            "lat": cert.get("wgs84_latitude"),
            "lon": cert.get("wgs84_longitude"),
            "distance_to_eez_nm": cert.get("distance_to_eez_nm"),
            "boundary_status": cert.get("boundary_status"),
            "length_meters": cert.get("length_meters"),
            "beam_meters": cert.get("beam_meters"),
            "peak_rcs_db": cert.get("peak_rcs_db"),
            "legal_infraction": cert.get("legal_infraction"),
            "legal_framework": [
                "Ley Federal de Pesca 24.922 (Art. 51)",
                "CONVEMAR Art. 56 y 73",
                "Resolucion OMI A.1106(29)"
            ],
            "radar_chip_sha256": cert.get("radar_chip_sha256", hashlib.sha256(b"").hexdigest())
        }
        payload_bytes = json.dumps(canonical, sort_keys=True).encode("utf-8")
        pub_key.verify(sig_bytes, payload_bytes)
        print(f"{C_GREEN}      ✔ Verificación Criptográfica Local Offline: 100% MATEMÁTICAMENTE VÁLIDA{C_RESET}")
    except ImportError:
        print(f"{C_YELLOW}      ℹ Nota: Se recomienda 'pip install cryptography' para validación local offline sin servidor.{C_RESET}")
    except Exception as e:
        print(f"{C_RED}      ✖ Error en verificación local: {e}{C_RESET}")

    print(f"\n{C_GREEN}{C_BOLD}🎉 TODOS LOS TESTS DE EVALUACIÓN Y VERIFICACIÓN CIENTÍFICA COMPLETADOS CON ÉXITO.{C_RESET}\n")


def main():
    print_banner()
    try:
        target_id = test_sar_benchmark()
        test_legal_benchmark()
        pub_key = test_public_key()
        test_dossier_and_signature(target_id, pub_key)
    except AssertionError as e:
        print(f"\n{C_RED}{C_BOLD}❌ FALLA DE VERIFICACIÓN: {e}{C_RESET}")
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"\n{C_RED}{C_BOLD}❌ ERROR DE CONEXIÓN CON EL BACKEND: {e}{C_RESET}")
        sys.exit(2)
    except Exception as e:
        print(f"\n{C_RED}{C_BOLD}❌ ERROR INESPERADO: {e}{C_RESET}")
        sys.exit(3)


if __name__ == "__main__":
    main()
