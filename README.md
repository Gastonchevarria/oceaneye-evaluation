# OceanEye — Evaluation & Scientific Verification Kit 🛰️🌊

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python: 3.9+](https://img.shields.io/badge/Python-3.9+-3776AB.svg?logo=python&logoColor=white)](https://python.org)
[![Ed25519: RFC 8032](https://img.shields.io/badge/Cryptography-Ed25519%20RFC%208032-10B981.svg)]()
[![ESA Copernicus: Sentinel-1 C-Band](https://img.shields.io/badge/Sensor-Sentinel--1%20SAR%20C--Band-0284C7.svg)]()
[![Status: Reproducible Benchmark](https://img.shields.io/badge/Benchmark-100%25%20Reproducible-success.svg)]()

> **Iniciativa de Investigación Científica, Teledetección y Soberanía Tecnológica del Mar Argentino**  
> *Detección de Pesca Ilegal No Declarada y No Reglamentada (INDNR) en la Milla 201 ZEE y Agujero Azul mediante Radar Orbital Sentinel-1 SAR, Triage Determinista LAYA y Cadena de Custodia Criptográfica.*

---

## 👨‍🔬 Dirección Científica y Autoría

- **Investigador Principal:** **Ing. Químico Gastón Chevarria**
- **Filiación Académica / Institucional:** Investigador Independiente · Especialista en Arquitectura de Sistemas de Inteligencia Artificial.  
  Formación de grado y posgrado: **Universidad Tecnológica Nacional (UTN FRVM)** · **Universidad Nacional de San Martín (UNSAM)**.
- **Antecedente de Teledetección y Machine Learning Ambiental:**  
  *«Clasificación y Monitoreo de Parámetros de Calidad de Agua en la Franja Costera del Río de la Plata mediante Modelos de Machine Learning»* (desarrollado en colaboración con series de datos abiertos del Ministerio de Ambiente y Desarrollo Sostenible de la Nación).

---

## 🧭 Propósito de este Repositorio

Este repositorio constituye el **Evaluation & Verification Kit** público e independiente de la plataforma **OceanEye**. Permite a evaluadores académicos, tribunales internacionales y jurados de convocatorias científicas (como la *Beca Leonardo BBVA 2026 en Ciencias de la Computación*) reproducir, auditar y validar matemáticamente:

1. El funcionamiento de la **escena de referencia sintética calibrada** del Corredor Milla 201 (Atlántico Sudoccidental).
2. El rendimiento del **Triage Determinista LAYA (Sistema 1)** en micro-latencia.
3. Las métricas de detección y discriminación entre buques cooperativos (AIS) y **buques oscuros (Dark Vessels)**.
4. La detección del fraude geoespacial (**Ghost AIS Spoofing**).
5. La **autenticidad matemática inmutable** de las actas periciales mediante firma digital asimétrica **Ed25519** y digest **SHA-256**.

> 🔒 **Nota sobre Propiedad Intelectual:** Este kit contiene las especificaciones matemáticas, fixtures de prueba y el script de verificación. El backend privado de producción (modelos convolucionales propietarios, ingestores directos de ESA Copernicus y orquestadores Cloud Run) se mantiene reservado.

---

## ⚡ Ejecución Rápida de Verificación (1 Solo Comando)

El script `verify.py` no requiere ninguna dependencia externa (`pip` no es necesario; utiliza exclusivamente la biblioteca estándar de Python 3):

```bash
python3 verify.py
```

### Salida esperada en consola:

```text
================================================================================
🌊  OCEANEYE — EVALUATION & SCIENTIFIC VERIFICATION KIT
    Investigador Principal: Ing. Químico Gastón Chevarria (UTN FRVM · UNSAM)
    Teledetección Satelital y Soberanía Marítima en la Milla 201 ZEE Argentina
================================================================================

[1/5] Verificando Pipeline SAR & Triage LAYA (GET /api/v1/sar/benchmark)...
      • Latencia de respuesta API: ~35 ms
      • Escena Satelital: S1A_IW_GRDH_1SDV_20260415T140230_MILE201_ARGENTINA
      • Blancos SAR Físicos: 15 detectados
      • Buques Cooperativos Confirmados (AIS): 3
      • Pesqueros Oscuros (Dark Vessels): 12 / 12 esperados
      • Suplantación de Identidad (Ghost AIS): 1 / 1 esperado
      • Triage LAYA Sistema 1: 44.0% mar vacío descartado en 3.12 ms
        -> SAR-TGT-001: Eslora=80.0m, Manga=5.0m (L/B=16.0, Rumbo=180.0°)
      ✔ Benchmark SAR y Correlación Cinemática Verificados (100% OK)

[2/5] Verificando Motor Jurídico Neuro-Simbólico (GET /api/v1/sar/legal/benchmark)...
      • Casos Jurídicos Evaluados: 30
      • Exactitud Normativa: 100.0% (Ley 24.922 & CONVEMAR)
      • Tasa de Alucinación: 0.00%
      ✔ Consistencia Jurídica Verificada (0.00% Alucinación)

[3/5] Consultando Clave Pública Judicial Ed25519 (GET /api/v1/sar/laya/public-key)...
      • Algoritmo Criptográfico: Ed25519 (RFC 8032)
      • Clave Pública LAYA: 585ea4596b1cf29c3f1221793ed34ab6f56e4a3d9b97a9f967d3b16d6e3e062a
      ✔ Clave Pública Oficial de Custodia Verificada

[4/5] Inspeccionando Dossier Forense y Sello SHA-256 (GET /api/v1/sar/dossier/SAR-TGT-001)...
      • Dictamen Pericial: UNAUTHORIZED_IUU_FISHING_INSIDE_ARGENTINE_EEZ
      • Cadena de Custodia SHA-256: 64 hex caracteres válidos
      • Firma Digital Ed25519: RFC 8032 Asymmetric Signature Present
      ✔ Dossier Forense Estructurado y Sellado

[5/5] Verificación Criptográfica de la Firma en Servidor y Local (POST /api/v1/sar/laya/verify)...
      • Estado de Validación: VALID_AUTHENTIC
      • Firma Asimétrica Válida: True
      • Integridad SHA-256 Verificada: True
      ✔ Verificación Criptográfica Local Offline: 100% MATEMÁTICAMENTE VÁLIDA

🎉 TODOS LOS TESTS DE EVALUACIÓN Y VERIFICACIÓN CIENTÍFICA COMPLETADOS CON ÉXITO.
```

---

## 📐 Fundamentos Teóricos y Modelos Matemáticos

### 1. Calibración Radiométrica Satelital
La transformación de números digitales brutos ($DN$) a coeficiente de retrodispersión normalizado ($\sigma^0$) en escala lineal sigue la calibración de la Agencia Espacial Europea (ESA):

$$\sigma^0 = \frac{DN^2}{A_{DN}^2}$$

donde $A_{DN}$ es la tabla de calibración radiométrica dependiente del ángulo de incidencia $\theta_{inc}$.

### 2. Fusión Polarimétrica Dual-Pol
Para maximizar la relación señal a ruido (SNR) frente a la rugosidad superficial marina (dispersión de Bragg provocada por olas oceánicas), se aplica una ponderación física informada entre los canales de polarización cruzada ($VH$) y copolarizada ($VV$):

$$I_{\text{fused}} = (1 - \alpha) \cdot \sigma^0_{VV} + \alpha \cdot \sigma^0_{VH}, \quad \text{con } \alpha = 0.75$$

El canal $VH$ es altamente insensible a la rugosidad del mar pero exhibe un fortísimo rebote diedro y volumen en superestructuras de acero naval, reduciendo falsas alarmas por tormentas en más de un 80%.

### 3. Filtro Adaptativo Lee Vectorizado (Despeckle)
La reducción del ruido multiplicativo de moteado (*speckle*) se computa en tiempo $O(1)$ mediante filtrado espacial adaptativo sobre ventana local $7 \times 7$ con número equivalente de vistas $ENL = 4.4$:

$$\hat{I} = \bar{I} + W \cdot (I - \bar{I})$$

$$W = \max\left(0, 1 - \frac{C_v^2}{C_i^2}\right), \quad C_v = \frac{1}{\sqrt{ENL}}, \quad C_i = \frac{s}{\bar{I}}$$

donde $\bar{I}$ es la media local del fondo, $s$ es la desviación típica y $W$ es el factor de ponderación que preserva bordes duros de buques.

### 4. Detector 2D CA-CFAR con Anti-Enmascaramiento de Flota
El umbral de decisión adaptativo garantiza una probabilidad constante de falsa alarma ($P_{fa} = 10^{-5}$):

$$T = \alpha_{\text{cfar}} \cdot P_{\text{clutter}}$$

$$\alpha_{\text{cfar}} = N \cdot \left( P_{fa}^{-1/N} - 1 \right)$$

donde $N$ es el número de celdas de entrenamiento perimetrales (excluyendo el anillo de guardia). El mecanismo de *anti-enmascaramiento* recorta estadísticos superiores al percentil 75 en las celdas de entrenamiento, impidiendo que la proximidad de buques vecinos (agrupaciones densas de poteros) ciegue la detección.

### 5. Tensor de Inercia y Dimensiones Morfológicas
Para cada blanco discretizado por componentes conexas, la eslora, manga y rumbo se derivan de los momentos centrales espaciales de segundo orden ($\mu_{20}, \mu_{02}, \mu_{11}$):

$$\mathbf{J} = \begin{bmatrix} \mu_{20} & \mu_{11} \\ \mu_{11} & \mu_{02} \end{bmatrix}, \quad \det(\mathbf{J} - \lambda \mathbf{I}) = 0$$

$$\text{Eslora} = 4 \sqrt{\lambda_{\text{major}}} \cdot \Delta x_{\text{res}}, \quad \text{Manga} = 4 \sqrt{\lambda_{\text{minor}}} \cdot \Delta y_{\text{res}}$$

$$\theta_{\text{rumbo}} = \frac{1}{2} \operatorname{atan2}\left(2\mu_{11}, \mu_{20} - \mu_{02}\right)$$

### 6. Clasificación de Casco Profunda con ResNet-18 Dual-Stream (ONNX)
Para suprimir falsas alarmas generadas por picos de oleaje patagónico (*sea clutter*), los recortes de $64 \times 64$ píxeles centrados en cada detección se evalúan mediante una red neuronal convolucional **ResNet-18 Dual-Stream con Cross-Polarization Attention**, exportada a **ONNX Runtime**:

* **Entrada Bicanal:** Canales normalizados $\sigma^0_{\text{VV}}$ y $\sigma^0_{\text{VH}}$ en decibelios.
* **Dataset de Entrenamiento:** **LS-SSDD** (*Large-Scale SAR Ship Detection Dataset*), con 4.466 escenas satelitales Sentinel-1 C-Band (1.542 muestras de prueba independientes).
* **Métricas de Evaluación en Test Set:**
  - **Precisión (*Precision*):** **97.22%**
  - **Exhaustividad (*Recall*):** **96.05%**
  - **F1-Score:** **96.63%**
  - **Exactitud (*Accuracy*):** **94.94%**
  - **Latencia de Inferencia en CPU (Cloud Run):** **14.2 ms** por blanco ($0.00 en reposo).
* **Función de Pérdida Multitarea:**
  $$\mathcal{L}_{\text{total}} = \mathcal{L}_{\text{BCE}}(y_{\text{vessel}}, \hat{y}_{\text{vessel}}) + \lambda \cdot \mathcal{L}_{\text{SmoothL1}}(l_{\text{true}}, \hat{l}_{\text{pred}})$$

### 7. Correlación Cinemática (Dead Reckoning & Algoritmo Húngaro)
Dado que las emisiones AIS no coinciden en el segundo exacto con el instante de captura orbital SAR ($t_{\text{sar}}$), cada reporte cinemático se proyecta temporalmente:

$$\vec{x}_{\text{proy}}(t_{\text{sar}}) = \vec{x}_{\text{ais}}(t_{\text{ais}}) + \vec{v}_{\text{SOG}} \cdot (t_{\text{sar}} - t_{\text{ais}})$$

La asignación óptima global $1 \text{ a } 1$ entre blancos SAR y transmisiones AIS se resuelve minimizando la matriz de distancias ortodrómicas mediante el algoritmo húngaro ($O(n^3)$) con compuerta de exclusión elíptica ($R_{\text{gate}} = 3.0\text{ NM}$):

$$\min \sum_{i} \sum_{j} C_{ij} X_{ij}, \quad C_{ij} = d_{\text{haversine}}(\vec{x}_i, \vec{y}_j)$$

Los blancos físicos sin correspondencia AIS son catalogados como **Pesqueros Oscuros (Dark Vessels)**. Las transmisiones AIS sin retorno radar correspondiente son etiquetadas como **Ghost AIS Spoofing**.

### 8. Cadena Pericial Criptográfica (RFC 8032)
Cada expediente forense se congela en un diccionario canónico ordenado lexicográficamente, se resume mediante SHA-256 y se sella con firma digital asimétrica **Ed25519** generada por el subsistema seguro LAYA:

$$\text{Hash} = \operatorname{SHA-256}\left(\operatorname{JSON}_{\text{canonical}}\right)$$

$$\Sigma = \operatorname{Sign}_{\text{Ed25519}}\left(K_{\text{priv}}, \text{Hash}\right)$$

Cualquier autoridad pericial o tribunal puede verificar la firma usando la clave pública oficial:
- **Algoritmo:** Ed25519
- **Clave Pública Hex:** `585ea4596b1cf29c3f1221793ed34ab6f56e4a3d9b97a9f967d3b16d6e3e062a`

---

## 🌐 Endpoints Públicos de la API en Producción

| Endpoint | Método | Descripción |
|---|---|---|
| `https://oceaneye-backend-29923645081.us-central1.run.app/api/v1/sar/benchmark` | `GET` | Ejecuta el benchmark de referencia de 15 buques en Milla 201 |
| `https://oceaneye-backend-29923645081.us-central1.run.app/api/v1/sar/legal/benchmark` | `GET` | Evalúa los 30 casos periciales frente a la Ley 24.922 y CONVEMAR |
| `https://oceaneye-backend-29923645081.us-central1.run.app/api/v1/sar/laya/public-key` | `GET` | Clave pública judicial Ed25519 para verificación en cortes |
| `https://oceaneye-backend-29923645081.us-central1.run.app/api/v1/sar/dossier/{target_id}` | `GET` | Emisión de acta forense sellada para un buque específico |
| `https://oceaneye-backend-29923645081.us-central1.run.app/api/v1/sar/laya/verify` | `POST` | Verificación criptográfica instantánea de certificados periciales |

---

## 📄 Licencia y Marco Ético

Distribuido bajo la **Licencia MIT**. Diseñado con fines exclusivos de conservación biológica marina, investigación científica académica y ejercicio legítimo de la soberanía nacional de acuerdo a la Convención de las Naciones Unidas sobre el Derecho del Mar (CONVEMAR).
