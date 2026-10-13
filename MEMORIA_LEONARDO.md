# Memoria Científico-Técnica — Proyecto OceanEye 🛰️🇦🇷

**Convocatoria:** Becas Leonardo a Investigadores y Creadores Culturales 2026  
**Área Temática:** Ciencias de la Computación / Inteligencia Artificial & Teledetección  
**Investigador Principal:** Ing. Químico Gastón Chevarria  
**Filiación e Institución de Referencia:** Investigador Independiente · Especialista en Arquitectura de Sistemas de Inteligencia Artificial (Universidad Tecnológica Nacional FRVM · Universidad Nacional de San Martín).  
**Antecedente Previo de Investigación Ambiental:**  
*«Clasificación y Monitoreo de Parámetros de Calidad de Agua en la Franja Costera del Río de la Plata mediante Modelos de Machine Learning (en colaboración con datos del Ministerio de Ambiente y Desarrollo Sostenible de la República Argentina)»*.

---

## 1. Resumen Ejecutivo y Planteamiento del Problema

Cada temporada estival y otoñal austral, entre los paralelos 44°S y 48°S del Mar Argentino (específicamente en la confluencia de la Corriente de Malvinas y la Corriente de Brasil, sector conocido como **Agujero Azul** o **Corredor Milla 201**), se concentra una de las mayores flotas pesqueras de aguas distantes del planeta: más de 450 a 500 buques poteros y arrastreros extranjeros (banderas de China, España, Taiwán, Corea del Sur y banderas de conveniencia).

El objetivo principal de esta flota es la captura intensiva del calamar patagónico (*Illex argentinus*), especie clave en la cadena trófica del Atlántico Sur que sustenta poblaciones de merluza común, aves marinas y mamíferos protegidos. Para evadir los controles de la Prefectura Naval Argentina (PNA) y la Armada Argentina, numerosos buques apagan deliberadamente sus transpondedores del **Sistema de Identificación Automática (AIS)** —comportamiento tipificado internacionalmente como «Buques Oscuros» (*Dark Vessels*)— e incursionan dentro de la Zona Económica Exclusiva (ZEE) argentina al amparo de densas nieblas patagónicas y tormentas australes.

Los métodos ópticos satelitales (como Sentinel-2 o Landsat) resultan inoperantes en esta región debido a la nubosidad constante (>70% de los días). Por su parte, el patrullaje aeronaval mediante corbetas y patrulleros oceánicos (OPV) resulta económicamente prohibitivo para un monitoreo continuo de 24 horas (costos superiores a USD 15.000 por día de navegación por navío).

**OceanEye** resuelve esta brecha combinando:
1. **Radar de Apertura Sintética (SAR) en Banda C** proveniente de la constelación Sentinel-1 de la ESA (indiferente a la noche y la niebla).
2. Un motor de **Triage Determinista en micro-latencia (LAYA - Sistema 1)** que descarta mar vacío en milisegundos sin requerir costoso cómputo en GPU.
3. Clasificación morfológica y polarimétrica de cascos navales.
4. Correlación cinemática espacio-temporal contra emisiones AIS mediante el algoritmo húngaro y cálculo de distancias geodésicas a la frontera de la Milla 200.
5. Emisión automatizada de **Actas Periciales Forenses Inmutables** con firma digital asimétrica **Ed25519** y sellado **SHA-256**, otorgando plena validez probatoria ante tribunales federales e internacionales (CONVEMAR).

---

## 2. Marco Teórico y Arquitectura Neuro-Simbólica de Dos Sistemas

Inspirado en la teoría de los dos sistemas de procesamiento cognitivo (Kahneman) y las arquitecturas de inteligencia artificial neuro-simbólica, OceanEye se estructura en dos capas operativas complementarias:

```
┌────────────────────────────────────────────────────────────────────────┐
│                   ESCENA SATELITAL SENTINEL-1 SAR                      │
│                (Banda C 5.405 GHz · Canales VV + VH)                   │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│               SISTEMA 1: LAYA (Reflejo Determinista O(1))               │
│  · Triage estadístico de varianza espacial (< 10 ms en CPU estándar)   │
│  · Descarte del 40% al 60% de océano vacío sin activación de GPU       │
│  · AIS Guardian: Detección instantánea de teletransporte / blackout    │
└──────────────────┬─────────────────────────────────────────────────────┘
                   │  [Solo chips con densidad metálica anómala]
                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│               SISTEMA 2: INFERENCIA PESADA Y DERECHO NAVAL             │
│  · Fusión física Dual-Pol (alpha=0.75) + Filtro Lee Vectorizado        │
│  · 2D CA-CFAR con anti-enmascaramiento de flota concentrada            │
│  · Tensor de inercia: extracción de eslora, manga y rumbo de casco     │
│  · Correlación cinemática Dead Reckoning + Algoritmo Húngaro           │
│  · Motor de Dictamen Jurídico formal (Ley 24.922 & UNCLOS Art. 73)     │
└──────────────────┬─────────────────────────────────────────────────────┘
                   │
                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│                 CADENA DE CUSTODIA FORENSE CRIPTOGRÁFICA               │
│  · Paquete probatorio canónico JSON ordenado                           │
│  · Digest inmutable SHA-256                                            │
│  · Firma digital asimétrica Ed25519 (RFC 8032)                         │
└────────────────────────────────────────────────────────────────────────┘
```

### 2.1. Sistema 1: Motor LAYA (Lightweight Asymmetric Yield Architecture)
Los pipelines satelitales convencionales cometen el error de enviar matrices gigantescas (escenas de 250 km × 180 km que superan los 1.5 GB en formato float32) directamente a inferencia neuronal profunda, saturando memoria y multiplicando costos de infraestructura.

LAYA divide la escena en teselas ortogonales de resolución optimizada y evalúa el coeficiente de dispersión cuadrática en microsegundos. Si una celda oceánica no supera el umbral de varianza característico del oleaje marino en escala de Rayleigh, es **descartada inmediatamente**. En la escena de referencia calibrada del Agujero Azul, LAYA descarta el **44% de los tiles en menos de 4 ms**, garantizando escalabilidad a costo cero en infraestructura sin servidor (*serverless*).

### 2.2. Sistema 2: Física de Microondas y Detección CFAR
En los sectores que superan el triage:
- Se aplica la **fusión polarimétrica dual** $\sigma^0_{\text{fused}} = 0.25 \sigma^0_{VV} + 0.75 \sigma^0_{VH}$, aprovechando que el canal despolarizado $VH$ suprime el eco de las crestas de las olas pero responde vivamente a esquinas ortogonales metálicas (casco, grúas y chimeneas navales).
- Se ejecuta el detector **CA-CFAR bidimensional** con un esquema específico de *anti-enmascaramiento*, diseñado para la flota potera: cuando 20 buques operan separados por apenas 400 metros entre sí, el algoritmo tradicional infla la media de ruido circundante y "tapa" a los buques vecinos. OceanEye filtra los valores extremos de las celdas de entrenamiento mediante un corte de orden estadístico antes de fijar el umbral $P_{fa} = 10^{-5}$.
- Se computa el **tensor de momentos centrales de inercia** sobre los píxeles activados para deducir las dimensiones estructurales del navío (eslora y manga en metros) y su rumbo angular en relación al norte geográfico.

---

## 3. Validez Legal y Cadena de Custodia Criptográfica

Para que una detección satelital se convierta en una herramienta efectiva de soberanía y conservación, no basta con una coordenada sobre un mapa: debe resistir el escrutinio pericial en litigios judiciales o reclamos diplomáticos.

OceanEye incorpora la doctrina jurídica de la **Ley 25.506 de Firma Digital de la República Argentina** y la jurisprudencia del Tribunal Internacional del Derecho del Mar (**ITLOS**):
1. **Inmutabilidad:** Cada expediente reúne la telemetría WGS84, el recorte del chip SAR original, la distancia calculada en millas náuticas a la traza oficial de la ZEE (Ley 23.968) y los artículos infringidos.
2. **Digest Criptográfico:** Se genera un hash **SHA-256** del expediente normalizado canónicamente.
3. **Firma Asimétrica Ed25519:** El servidor pericial firma el hash mediante una clave privada persistente custodiada en Google Cloud Secret Manager. La clave pública correspondiente (`585ea459...`) permite a cualquier fiscalía, ONG de conservación o tribunal verificar en menos de 2 milisegundos que los datos de posición y dimensiones no fueron manipulados con posterioridad a la captura satelital.

---

## 4. Plan de Trabajo con la Beca Leonardo BBVA 2026 (12 Meses)

| Trimestre | Objetivos Principales | Entregables Verificables |
|---|---|---|
| **Q1 (Meses 1–3)** | Ingesta masiva automatizada de Sentinel-1 IW GRD vía ESA Copernicus Data Space API. Fine-tuning de ResNet-18 y YOLOv11-OBB sobre catálogo extendido de buques del Atlántico Sur. | Pipeline de ingesta en streaming continuo. Modelo re-entrenado con F1 > 96.5% sobre dataset de 1.200 buques australes etiquetados. |
| **Q2 (Meses 4–6)** | Integración de datos satelitales ópticos de apoyo (Sentinel-2 MSI) e infrarrojo nocturno (VIIRS/DNB - Day/Night Band) para detectar las potentes luminarias halógenas de los poteros en horario nocturno. | Algoritmo de correlación tri-modal: SAR + Óptico + VIIRS DNB. |
| **Q3 (Meses 7–9)** | Despliegue de nodos edge de LAYA en microcontroladores y computación embarcada para pruebas directas en patrulleros navales y estaciones terrestres costeras argentinas. | Paquete de firmware LAYA C++/Rust optimizado para CPUs sin GPU a < 5ms. |
| **Q4 (Meses 10–12)** | Publicación de informes técnicos de biodiversidad con INIDEP / CONICET. Apertura de API científica gratuita para investigadores de conservación marina. | Plataforma abierta de auditoría, paper científico en revista Q1 de Teledetección y código abierto para la comunidad científica. |

---

## 5. Compromiso Ético y Declaración de Transparencia

El presente proyecto ha sido concebido y desarrollado de forma individual por el **Ing. Gastón Chevarria** como una contribución técnica, rigurosa y desinteresada a la protección de los recursos naturales y la soberanía marítima de la República Argentina. No existen fines de lucro, intereses comerciales, ni ataduras a modelos SaaS corporativos.
