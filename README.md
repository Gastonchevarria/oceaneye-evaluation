# OceanEye: kit de evaluación 🛰️

[![Licencia: MIT](https://img.shields.io/badge/licencia-MIT-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-3776AB.svg)](https://www.python.org)
[![Sin dependencias](https://img.shields.io/badge/dependencias-ninguna-success.svg)](verify.py)

Detección de pesca ilegal en el Atlántico Sudoccidental con radar satelital (Sentinel-1) e inteligencia artificial verificable.

## Qué es

Este repositorio es el kit de evaluación de **OceanEye**, un proyecto de investigación de **Gastón Chevarría**, presentado a las Becas Leonardo a Investigadores y Creadores Culturales 2026 (Fundación BBVA). Permite comprobar en pocos minutos, sin servidor ni credenciales, la coherencia de los resultados que cita la memoria.

- Sitio: <https://oceaneye-ai.com>
- Contacto: <gaston@oceaneye-ai.com> · <contacto@oceaneye-ai.com>
- Memoria presentada: [`MEMORIA_LEONARDO.md`](MEMORIA_LEONARDO.md), copia literal del texto enviado a la convocatoria. Algunas direcciones que cita corresponden a la infraestructura anterior y hoy no responden; el sitio vigente es <https://oceaneye-ai.com> y el material verificable es este repositorio. La memoria llama «ResNet-18 Dual-Stream» al clasificador que aquí se describe con más precisión como CNN residual compacta inspirada en ResNet-18.
- Dónde está hoy lo que la memoria enlaza en sus secciones 4.4 y 7:
  - Plataforma web y consola táctica: <https://oceaneye-ai.com> y <https://oceaneye-ai.com/app>.
  - Benchmark de la escena de referencia: [`fixtures/sample_benchmark.json`](fixtures/sample_benchmark.json).
  - Benchmark legal de 30 casos: [`resultados/evaluation_results.json`](resultados/evaluation_results.json).
  - Entrenamiento y evaluación del clasificador: [`resultados/evaluation_metrics.json`](resultados/evaluation_metrics.json). El cuaderno de entrenamiento y el repositorio de desarrollo no son públicos; este kit no vuelve a entrenar el modelo.

## Cómo verificar en un comando

Requiere solo Python 3.9 o superior. No usa la red ni instala nada.

```bash
python3 verify.py
```

Salida real de la última ejecución (código de salida 0):

```text
OceanEye - kit de evaluación
La escena de referencia es SINTÉTICA (semilla fija 1982, 15 blancos inyectados);
los nombres, MMSI e IMO de los buques son ficticios. Verifica coherencia del flujo,
no la precisión de detección sobre escenas reales.

Verdad de terreno (fixture_metadata.json)
  [ok]    15 blancos recalculados de la lista, igual al total declarado
  [ok]    3 blancos con AIS activo, igual a lo declarado
  [ok]    12 blancos sin AIS, igual a lo declarado
  [ok]    1 emisor AIS sin blanco a menos de 1 NM, igual a lo declarado
  [ok]    eslora/manga >= 2 en los 15 blancos de la verdad de terreno
Resultado de la escena (sample_benchmark.json)
  [ok]    misma escena en resultado y metadatos
  [ok]    resultado: 12 blancos oscuros (lista y métrica coinciden)
  [ok]    resultado: 3 blancos confirmados por AIS (lista y métrica coinciden)
  [ok]    resultado: 1 AIS fantasma (lista y métrica coinciden)
  [ok]    resultado: 15 blancos = oscuros + confirmados
  [ok]    resultado: porcentaje de oscuros recalculado (80.0 %)
  [ok]    resultado: eslora/manga >= 2 en los 15 blancos
  [ok]    resultado: triage, descartadas + activas = total
  [ok]    resultado: triage, porcentaje recalculado (52.0 %)
Correspondencia con la verdad de terreno
  [ok]    cada blanco oscuro del resultado corresponde a uno distinto de la verdad de terreno sin AIS (12 de 12, tolerancia 2 px y 0,5 NM)
  [ok]    cada confirmación AIS une un blanco con el MMSI ficticio de su verdad de terreno
  [ok]    el AIS fantasma del resultado es el emisor sin blanco de los metadatos

Métricas del clasificador (resultados/evaluation_metrics.json)
  [ok]    2501 recortes de entrenamiento, 2500 de validación y 1542 de prueba
  [ok]    F1 recalculado desde precisión y exhaustividad (0.9663)
  [ok]    precisión, exhaustividad y exactitud compatibles con una matriz de confusión entera de 1542 recortes (p. ej. TP=1118, FP=32, FN=46, TN=346)
Motor de reglas y validador (resultados/evaluation_results.json)
  [ok]    30 casos con identificador único
  [ok]    30 de 30 casos con la regla esperada
  [ok]    8 de 8 textos adulterados detectados
  [ok]    ninguna detección en los 22 casos sin adulterar

Verificador Ed25519 (RFC 8032, vector de prueba 1)
  [ok]    firma del vector 1 aceptada
  [ok]    firma alterada rechazada
  [ok]    mensaje alterado rechazado

Acta firmada
  [info]  No hay acta firmada incluida; verificar una requiere el modo --api.

27 comprobaciones correctas, 0 con falla.
Todas las comprobaciones pasaron.
```

El script termina con código 0 solo si todas las comprobaciones pasan; con 1 si alguna falla y con 2 si, en modo `--api`, el servicio no responde.

## Qué verifica y qué no

Verifica, sobre los archivos de `fixtures/` y `resultados/`:

- **Conteos recalculados a partir de las listas**: 15 blancos, 3 confirmados por AIS, 12 oscuros y 1 AIS fantasma, cruzados contra `fixture_metadata.json`.
- **Proporciones físicas**: relación eslora/manga mayor o igual que 2 en todos los blancos.
- **Coherencia del triage**: teselas descartadas más activas igual al total, y porcentaje recalculado.
- **Correspondencia con la verdad de terreno**: cada blanco oscuro del resultado coincide (por posición en píxeles y en coordenadas) con uno distinto de los 12 blancos sin AIS sembrados; cada confirmación AIS usa el MMSI de su blanco.
- **Resultados registrados** (`resultados/`): tamaños de los conjuntos de entrenamiento, validación y prueba; F1 recalculado desde precisión y exhaustividad; existencia de una matriz de confusión entera de 1.542 recortes compatible con la precisión, la exhaustividad y la exactitud publicadas; y recuento de los 30 casos (30 con la regla esperada, 8 de 8 textos adulterados detectados, ninguna detección en los 22 restantes).
- **Verificador Ed25519** (RFC 8032, implementado en Python puro, solo verificación): acepta el vector de prueba 1 de la RFC y rechaza una firma alterada.

No verifica:

- Que el pipeline vuelva a producir esos resultados: para eso hay que ejecutar el código del prototipo. Este kit comprueba que los archivos de resultado son coherentes entre sí.
- Que el entrenamiento o la evaluación vuelvan a dar esas cifras: los archivos de `resultados/` son copia literal de lo que registró el prototipo. El kit comprueba su coherencia aritmética; no vuelve a entrenar ni a evaluar.
- Rendimiento sobre escenas reales. La escena de referencia es **sintética** (generada con semilla fija, `np.random.seed(1982)`, con 15 blancos inyectados). Los 12 de 12 oscuros recuperados miden recuperación de blancos sembrados, no precisión de detección.
- Ninguna acta firmada, salvo que exista `fixtures/sample_dossier.json`. Verificar un acta requiere el modo `--api`.

Los nombres, MMSI e IMO de los buques son **ficticios**; cualquier coincidencia con embarcaciones o empresas reales es casual.

## Resultados medidos y su procedencia

Proceden del prototipo. Los dos archivos originales (`backend/ml/evaluation_metrics.json` y `backend/app/legal/evaluation_results.json`) están copiados sin cambios en [`resultados/`](resultados/); `verify.py` comprueba su coherencia interna, no los recalcula desde los datos.

Los nombres de campo y las etiquetas internas de esos archivos (por ejemplo `verdict_status` o `certified_…`) son los que generó el prototipo y no implican ninguna certificación externa. Las cifras que sostiene el proyecto son las de esta tabla.

| Resultado | Valor | Procedencia |
| :--- | :--- | :--- |
| Conjunto de datos | LS-SSDD (Sentinel-1, VV + VH): 2.501 recortes de entrenamiento, 2.500 de validación, 1.542 de prueba | `evaluation_metrics.json` |
| Precisión (prueba) | 97,22 % | `evaluation_metrics.json` |
| Exhaustividad (*recall*) | 96,05 % | `evaluation_metrics.json` |
| F1 | 0,9663 | `evaluation_metrics.json` |
| Exactitud | 94,94 % | `evaluation_metrics.json` |
| Latencia de inferencia | 0,835 ms por recorte (medición por lotes sobre Apple Silicon, `mps`) | `evaluation_metrics.json` |
| Tamaño del modelo | 4,7 MB (ONNX) | `oceaneye_sar_resnet.onnx` |
| Motor de reglas | 30 de 30 casos tipificados como se esperaba | `evaluation_results.json` |
| Validador de textos | 8 de 8 textos adulterados detectados | `evaluation_results.json` |

El conjunto de 30 casos fue escrito por el propio proyecto; su ampliación está prevista en el plan de trabajo. La escena de referencia (`fixtures/`) se describe arriba: es sintética.

## Fundamentos

Descripción resumida de las etapas del prototipo.

1. **Calibración radiométrica.** $\sigma^0 = DN^2 / A_{DN}^2$, con $A_{DN}$ la tabla de calibración del producto Sentinel-1 GRD.
2. **Fusión de polarizaciones.** $I = \alpha\,\sigma^0_{VH} + (1-\alpha)\,\sigma^0_{VV}/\kappa$, con $\alpha = 0{,}75$ y $\kappa$ la razón de medianas VV/VH del fondo marino. La polarización cruzada suele ser menos sensible al oleaje y más sensible a las superestructuras metálicas; el peso es un parámetro del prototipo, no un resultado optimizado.
3. **Filtro de Lee** (ventana de 7 × 7, $ENL = 4{,}4$): $\hat I = \bar I + W\,(I-\bar I)$, con $W = \max(0,\,1 - C_v^2/C_i^2)$, $C_v = 1/\sqrt{ENL}$ y $C_i = s/\bar I$.
4. **CA-CFAR bidimensional.** Umbral $T = \alpha_{cfar}\,P_{clutter}$ con $\alpha_{cfar} = N\,(P_{fa}^{-1/N}-1)$; el código usa $P_{fa} = 10^{-6}$ por defecto. Para limitar el enmascaramiento entre buques vecinos, se excluyen del fondo los valores atípicos muy altos.
5. **Dimensiones del blanco.** Eslora, manga y rumbo a partir de los momentos centrales de segundo orden de cada componente conexa.
6. **Clasificador.** `OceanEyeSarNet`, CNN residual compacta de doble polarización, inspirada en ResNet-18, sobre recortes de 64 × 64 píxeles (canales VV y VH). Solo la cabeza de embarcación frente a *clutter* está evaluada; las cabezas de arte de pesca y de eslora son preliminares y sus salidas no se presentan como resultado.
7. **Correlación con AIS.** Cada reporte AIS se proyecta al instante de la captura, $\vec x(t_{sar}) = \vec x(t_{ais}) + \vec v\,(t_{sar}-t_{ais})$, y se asigna a los blancos con el algoritmo húngaro (distancia ortodrómica, compuerta de 3,0 NM). Un blanco sin AIS se marca como oscuro; un emisor AIS sin eco de radar, como señal fantasma.
8. **Sellado.** Cada informe se serializa como JSON canónico (claves ordenadas), se resume con SHA-256 y se firma con Ed25519 (RFC 8032). La firma se verifica con la clave pública y sin acceso a la clave privada. La clave del prototipo es de desarrollo.

Normas a las que hace referencia el motor de reglas: Ley 24.922 (arts. 3, 21, 22 y 51), Ley 27.564, CONVEMAR (arts. 17, 56.1.a, 63.2, 73 y 87), Código Penal (art. 292), Resolución OMI A.1106(29) y Reglamento de Radiocomunicaciones de la UIT (art. 19). El motor señala qué norma resulta pertinente según reglas explícitas; no sustituye el análisis de un profesional del derecho ni la decisión de la autoridad competente.

## Modo en línea (opcional)

```bash
python3 verify.py --api https://<url-de-la-api>
# o bien: OCEANEYE_API=https://<url-de-la-api> python3 verify.py
```

Además de las comprobaciones locales, ejecuta cinco pruebas contra la API (benchmark de la escena, benchmark legal, clave pública, acta de un blanco y verificación de su firma) y verifica la firma del acta de forma local con el verificador incluido en `verify.py`. Si la API no responde, informa el error y termina con código 2.

## Estado de la API

El servicio en línea está en migración de infraestructura. El modo `--api` queda disponible para cuando se publique la nueva dirección. Mientras tanto, todo lo anterior funciona sin red.

## Límites y estado actual

- La escena de referencia es sintética; todavía no se validó el flujo sobre escenas Sentinel-1 reales del área.
- El modelo se entrenó con LS-SSDD, no con datos del Atlántico Sur.
- El límite de la ZEE que usa el prototipo es una línea aproximada y solo referencial, no el límite oficial.
- La cabeza de estimación de eslora del clasificador es preliminar: su error medio absoluto en prueba es de 72,55 m (`resultados/evaluation_metrics.json`). No se presenta como resultado.
- El despacho de alertas es una simulación: no se comunica con ninguna autoridad.
- La clave de firma Ed25519 es de desarrollo; la custodia en HSM o KMS es trabajo futuro.
- OceanEye aporta evidencia técnica; la calificación jurídica de los hechos corresponde a la autoridad competente.

## Cómo citar

Ver [`CITATION.cff`](CITATION.cff).

## Licencia

MIT. Ver [`LICENSE`](LICENSE).
