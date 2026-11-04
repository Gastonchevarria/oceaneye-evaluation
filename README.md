# OceanEye Sentinel 2.0 — Verification Kit & Meta AI Benchmark Harness

Continuous orbital maritime surveillance fusing C-Band Synthetic Aperture Radar (Sentinel-1A, 5.405 GHz VV+VH) with **Meta SAM 3.1** sub-pixel segmentation, **Muse Wonder 1.0 vs Muse Spark 1.3** neuro-symbolic reasoning, **Muse Image 1.0** conditioning, and **WebCrypto Ed25519 (RFC 8032)** cryptographic chain of custody to intercept illegal dark fishing fleets in the South Atlantic Blue Hole corridor (Mile 201).

* **Live Landing:** [https://sentinel.oceaneye-ai.com/](https://sentinel.oceaneye-ai.com/)
* **Judges Quick-Start (Interactive 90s Guided Pitch):** [https://sentinel.oceaneye-ai.com/app/?pitch=1](https://sentinel.oceaneye-ai.com/app/?pitch=1)
* **Live Tactical War Room Console:** [https://sentinel.oceaneye-ai.com/app/](https://sentinel.oceaneye-ai.com/app/)
* **Hackathon Track & Theme:** Startup Track — **Push the Frontier**

---

## Built During the Meta Global AI Hackathon (Oct 26 – Nov 4, 2026)

* **Meta Model API 4-Model Pipeline:** Engineered the live orchestration integrating `muse-image-1.0`, `sam-3.1` (Gate A sub-pixel mask extraction + negative open-ocean wave-clutter control), `muse-spark-1.3`, and `muse-wonder-1.0`.
* **Statistical Evaluation Harness ($n = 30$, Seed `1982`, Temp `0.1`):** Built the automated evaluation suite testing reasoning models across 30 standardized South Atlantic tactical scenarios with negative 6-meter wave-clutter controls and exact McNemar statistical verification ($p = 0.0156$).
* **Neuro-Symbolic Validator & WebCrypto Chain of Custody:** Implemented deterministic physical-threshold auditing (RCS, kinematics, AIS reconciliation, OPV interception vectoring) and canonical SHA-256 + Ed25519 signature verification directly verifiable inside the browser and via CLI.
* **3D Orbital SAR Tactical Console & 90s Judge Pitch:** Developed the interactive optical-versus-SAR lens comparator, the LAYA 8x8 tile triage matrix, the SAM 3.1 mask inspector, the guided 90-second judge pitch (`/app/?pitch=1`), and automatic fallback to signed benchmark artifacts (`Cached Verified Run — Ed25519 Signed`) so judges never encounter rate-limit errors.

---

## Four-Stage Multimodal Production Cascade

1. **Stage 0 — LAYA System-1 Deterministic Triage (CPU · `9.12 ms`):**
   Evaluates spatial variance across an $8 \times 8$ tile grid on CPU in `9.12 ms`, discarding 59 of 64 empty ocean tiles to save **44% of GPU compute** before invoking foundation models.
2. **Stage 1 — Sub-Pixel Hull Segmentation (`sam-3.1` Gate A + `muse-image-1.0`):**
   Adaptive CA-CFAR detection feeds active Sentinel-1A C-Band SAR chips (`5.405 GHz`, dual-polarization `VV + VH`) into **Meta `sam-3.1`** and **`muse-image-1.0`**, extracting sub-pixel steel hull masks (`>+51.6 dB RCS`) across 10 candidate chips (9 dark vessels + 1 rough-sea negative control with **0% false positives** on 6-meter Bragg wave scatter).
3. **Stage 2 — Rapid Semantic Alerting (`muse-spark-1.3` · `11.7 s` mean latency):**
   Low-latency first-responder triage across radar backscatter and AIS telemetry.
4. **Stage 3 — Forensic Neuro-Symbolic Reasoning (`muse-wonder-1.0` · `28.6 s` mean latency + Ed25519):**
   Reconciles 15 injected radar targets against 3 cooperative AIS broadcasts and 1 kinematic ghost track to isolate **12 dark targets** (including *LU RONG YU 588* / `SAR-TGT-001`), computes the interception vector for *OPV ARA Bouchard (P-51)* (`148°` bearing, `15.2 NM`), audits every claim via the deterministic Neuro-Symbolic Validator, and seals the evidentiary package with **Ed25519 + SHA-256**.
5. **Next Steps / Future Roadmap (Layer 4 — On-Chain Financial Traceability):**
   Integrating cyclic-graph on-chain financial forensics and autonomous Z3 formal verification (`/x402/*`) to trace cross-border laundering by sanctioned IUU fishing syndicates.

---

## Meta AI Model Arena Benchmark ($n = 30$, Seed `1982`, Temp `0.1`)

| Evaluation Metric | `muse-wonder-1.0` | `muse-spark-1.3` | Benchmark Protocol |
| :--- | :--- | :--- | :--- |
| **Clean Pre-Validator Pass** | **86.7% (26 / 30)** | 63.3% (19 / 30) | **McNemar $p = 0.0156$** (exact binomial) |
| **Successful Scenarios** | **26** | 19 | Total $n = 30$ tactical scenarios |
| **Mean Rule Adherence** | **98.0%** | 92.0% | Deterministic neuro-symbolic penalty audit |
| **Mean Inference Latency** | **28.6 s** | **11.7 s** | Sampling temperature `0.1` |
| **Clutter False Positives** | **Zero (0 / 10)** | **Zero (0 / 10)** | Gate A negative control (6m waves) |

---

## Local Reproduction & Cryptographic Verification

Judges can verify all pre-signed benchmark artifacts, SAM 3.1 masks, McNemar statistics, and Ed25519 signatures offline in under 2 seconds (without spending API credits), or run live Meta Model API queries by configuring `.env`:

```bash
git clone https://github.com/Gastonchevarria/oceaneye-evaluation.git
cd oceaneye-evaluation
cp .env.example .env
# Optional: add your META_MODEL_API_KEY inside .env for live API re-execution
python3 run_benchmark.py --verify-signatures
```

Additional integrity checks included in this repository:

```bash
python3 scripts/check_integrity.py
python3 verify.py
```

---

## Repository Structure

* `run_benchmark.py` — Unified CLI entrypoint (`--verify-signatures` for deterministic audit of `data/meta_muse_sam31_benchmark_2026.json`, `data/sam_masks.json`, `data/arena_replay.json`, `data/stats.json`, and Ed25519 chain of custody; `--live` for Meta Model API execution).
* `data/` — Pre-signed Meta AI Hackathon benchmark outputs (`meta_muse_sam31_benchmark_2026.json`, `sam_masks.json`, `arena_replay.json`, `stats.json`, `sar_benchmark_2026.json`, `real_patagonia_fleet_2026.json`, and `sam_chips/`).
* `scripts/` — Full Meta Model API benchmark runners (`run_meta_hackathon_benchmark.py`, `run_sam_puerta_a.py`, `check_integrity.py`, `validate_arena.py`).
* `verify.py` & `fixtures/` — RFC 8032 Ed25519 + SHA-256 canonical chain-of-custody verifier.
* `.env.example` — Clean environment variable template (zero hardcoded keys).
