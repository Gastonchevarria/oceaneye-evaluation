"""
Deterministic Maritime Legal Rules Engine (Motor Neuro-Simbólico Formal).

Strictly enforces maritime legal logic according to:
- Convención de las Naciones Unidas sobre el Derecho del Mar (CONVEMAR / UNCLOS 1982).
- Ley Federal de Pesca 24.922 (República Argentina) y modificatorias (Ley 27.564).
- Ley de Espacios Marítimos 23.968.
- Convenio Internacional para la Seguridad de la Vida Humana en el Mar (SOLAS Cap. V, Reg. 19).
- Resoluciones OMI A.1106(29) y UIT RR Art. 19.

Garantiza un límite formal inmutable: Cero alucinaciones, tipificación matemática estricta.
"""

from enum import Enum
from typing import Dict, Any, List, Optional, Tuple
from pydantic import BaseModel, Field


class MaritimeJurisdictionZone(str, Enum):
    INTERNAL_WATERS = "AGUAS_INTERIORES"
    TERRITORIAL_SEA = "MAR_TERRITORIAL_12NM"
    CONTIGUOUS_ZONE = "ZONA_CONTIGUA_24NM"
    EXCLUSIVE_ECONOMIC_ZONE = "ZONA_ECONOMICA_EXCLUSIVA_200NM"
    CRITICAL_BUFFER_3NM = "ZONA_AMORTIGUACION_FRONTERIZA_3NM"
    HIGH_SEAS_ADJACENT = "ALTA_MAR_ADYACENTE_MILLA_201"


class LegalInfractionSeverity(str, Enum):
    CRITICAL_SOVEREIGN_VIOLATION = "CRITICA_VIOLACION_SOBERANIA"
    HIGH_IUU_PROBABILITY = "ALTA_PROBABILIDAD_IUU"
    MODERATE_TELEMETRY_ANOMALY = "MODERADA_ANOMALIA_TELEMETRIA"
    COMPLIANT_NAVIGATION = "CUMPLIMIENTO_NORMATIVO"


class LegalVerdict(BaseModel):
    rule_id: str
    jurisdiction_zone: MaritimeJurisdictionZone
    severity: LegalInfractionSeverity
    infraction_code: str
    title_es: str
    description_es: str
    legal_basis_articles: List[str]
    enforcement_measures: List[str]
    economic_penalty_range_usd: Optional[Tuple[float, float]] = None
    presumption_of_guilt_applied: bool = False
    evidence_requirements: List[str]


def determine_maritime_zone(distance_to_eez_nm: float) -> MaritimeJurisdictionZone:
    """
    Determines maritime zone based on signed geodesic distance to EEZ border.
    Negative values indicate inside EEZ.
    """
    if distance_to_eez_nm < -188.0:
        return MaritimeJurisdictionZone.TERRITORIAL_SEA
    elif distance_to_eez_nm < -176.0:
        return MaritimeJurisdictionZone.CONTIGUOUS_ZONE
    elif distance_to_eez_nm < 0.0:
        return MaritimeJurisdictionZone.EXCLUSIVE_ECONOMIC_ZONE
    elif distance_to_eez_nm <= 3.0:
        return MaritimeJurisdictionZone.CRITICAL_BUFFER_3NM
    else:
        return MaritimeJurisdictionZone.HIGH_SEAS_ADJACENT


def evaluate_maritime_case(
    distance_to_eez_nm: float,
    classification: str,
    dl_vessel_prob: float = 0.9,
    dl_fishing_prob: float = 0.85,
    speed_knots: Optional[float] = None,
    has_valid_license: bool = False,
    is_national_flag: bool = False
) -> LegalVerdict:
    """
    Deterministic rule engine that returns formal legal verdict without LLM ambiguity.
    Evaluates specific technical anomalies with strict precedence.
    """
    zone = determine_maritime_zone(distance_to_eez_nm)

    # REGLA PRIORITARIA 4: Suplantación Telemática / Buque Fantasma (Ghost AIS)
    if classification == "GHOST_AIS":
        return LegalVerdict(
            rule_id="RULE-INT-ITU-004",
            jurisdiction_zone=zone,
            severity=LegalInfractionSeverity.HIGH_IUU_PROBABILITY,
            infraction_code="SPOOFING_TELEMATICO_GHOST_AIS",
            title_es="Emisión de AIS Fantasma / Suplantación Telemática de Identidad",
            description_es=(
                "Transmisión radioeléctrica de posición AIS recibida pero carente de correspondencia de eco físico en radar SAR. "
                "Indicio grave de clonación de transpondedor o encubrimiento de buque nodriza."
            ),
            legal_basis_articles=[
                "Reglamento de Radiocomunicaciones de la Unión Internacional de Telecomunicaciones (UIT) - Art. 19",
                "Convenio SOLAS Cap. V, Reg. 19 (Integridad de transmisiones de seguridad)",
                "Código Penal de la Nación Argentina Art. 292 (Falsificación de documentos telemáticos públicos)"
            ],
            enforcement_measures=[
                "Alerta radioeléctrica inmediata a estaciones costeras de control de tráfico marítimo",
                "Investigación sobre titular registral del MMSI ante el Registro Naval de Bandera",
                "Inspección prioritaria en primer puerto de recalada"
            ],
            economic_penalty_range_usd=(100_000.0, 1_000_000.0),
            presumption_of_guilt_applied=True,
            evidence_requirements=[
                "Registro de paquetes VDM/VDO con estampas de tiempo",
                "Ausencia de retorno de retrodispersión radar en radio de 3 NM de la posición reclamada"
            ]
        )

    # REGLA 1: Incursión de Buque No Autorizado dentro de la ZEE (Pesca Ilegal Flagrante)
    if distance_to_eez_nm < 0.0:
        if classification == "DARK_VESSEL" or (not has_valid_license and not is_national_flag):
            return LegalVerdict(
                rule_id="RULE-ARG-EEZ-001",
                jurisdiction_zone=zone,
                severity=LegalInfractionSeverity.CRITICAL_SOVEREIGN_VIOLATION,
                infraction_code="INCURSION_IUU_ZEE_ART51",
                title_es="Pesca Ilegal No Declarada No Reglamentada en ZEE Soberana",
                description_es=(
                    f"Embarcación detectada a {abs(distance_to_eez_nm):.2f} NM en el interior de la Zona Económica "
                    f"Exclusiva Argentina sin transmitir AIS y careciendo de permiso de pesca de la autoridad de aplicación. "
                    f"Configura infracción grave con presunción legal de faena ilícita."
                ),
                legal_basis_articles=[
                    "Ley Federal de Pesca 24.922 - Art. 3, 21 y 51 (Prohibición absoluta de pesca a buques extranjeros sin permiso)",
                    "Ley 27.564 - Régimen Sancionatorio Pesquero (Actualización de unidades de pesca UO)",
                    "CONVEMAR (UNCLOS) - Art. 56.1.a y Art. 73 (Facultad de aprensión, fianza y decomiso del Estado ribereño)",
                    "Código Procesal Penal Federal - Delito flagrante contra recursos soberanos de la Nación"
                ],
                enforcement_measures=[
                    "Orden de captura e interceptación marítima por Prefectura Naval Argentina / Armada Argentina",
                    "Secuestro judicial del buque y escolta a puerto argentino (Puerto Madryn o Mar del Plata)",
                    "Decomiso total de la carga en bodega (captura) y de artes de pesca",
                    "Apertura de sumario administrativo y demanda judicial por costas operativas de patrullaje"
                ],
                economic_penalty_range_usd=(1_500_000.0, 5_000_000.0),
                presumption_of_guilt_applied=True,
                evidence_requirements=[
                    "Copia fotográfica radar SAR con resolución < 12m y RCS confirmatorio",
                    "Certificado criptográfico LAYA Ed25519 con coordenadas WGS84 inmutables",
                    "Certificación de ausencia de emisión AIS en momento de barrido satelital",
                    "Cálculo geodésico de penetración respecto a la línea oficial del SHN"
                ]
            )

    # REGLA 2: Proximidad Crítica a la Frontera (Boundary Skimming / Agujero Azul)
    if zone == MaritimeJurisdictionZone.CRITICAL_BUFFER_3NM:
        if classification == "DARK_VESSEL":
            return LegalVerdict(
                rule_id="RULE-ARG-EEZ-002",
                jurisdiction_zone=zone,
                severity=LegalInfractionSeverity.HIGH_IUU_PROBABILITY,
                infraction_code="SKIMMING_BORDER_PREDATORIO",
                title_es="Navegación Furtiva Predatoria en Zona de Amortiguación Fronteriza (Milla 200)",
                description_es=(
                    f"Buque apagado detectado a {distance_to_eez_nm:.2f} NM en el exterior inmediato del límite de la ZEE. "
                    f"Maniobra de acecho (skimming) sobre cardúmenes transzonales con alto riesgo de incursión nocturna furtiva."
                ),
                legal_basis_articles=[
                    "Ley Federal de Pesca 24.922 - Art. 21 (Monitoreo de especies altamente migratorias y transzonales)",
                    "CONVEMAR Art. 63.2 (Deber de cooperación en poblaciones adyacentes a la ZEE)",
                    "Acuerdo de Nueva York de 1995 sobre Poblaciones de Peces Transzonales"
                ],
                enforcement_measures=[
                    "Posicionamiento de Patrullero Oceánico (OPV) de la Armada en vector de intercepción preventiva",
                    "Programación prioritaria de pasada SAR Sentinel-1 en ciclo orbital siguiente (T+12h)",
                    "Emisión de advertencia por canal 16 VHF y reporte preventivo a Estado de abanderamiento"
                ],
                economic_penalty_range_usd=(0.0, 500_000.0),
                presumption_of_guilt_applied=False,
                evidence_requirements=[
                    "Vector de rumbo COG y velocidad SOG estimados por radar o cinemática",
                    "Registro de transpondedor silenciado en radio de 5 NM de frontera"
                ]
            )

    # REGLA 3: Apagado Táctico de AIS en Alta Mar (Blackout en Milla 201)
    if zone == MaritimeJurisdictionZone.HIGH_SEAS_ADJACENT:
        if classification == "DARK_VESSEL":
            return LegalVerdict(
                rule_id="RULE-INT-SOLAS-003",
                jurisdiction_zone=zone,
                severity=LegalInfractionSeverity.MODERATE_TELEMETRY_ANOMALY,
                infraction_code="BLACKOUT_TACTICO_ALTA_MAR",
                title_es="Apagado Ilegítimo de Transpondedor AIS en Aguas Internacionales",
                description_es=(
                    f"Buque de porte industrial detectado a {distance_to_eez_nm:.2f} NM del límite de la ZEE "
                    f"sin transmisión de AIS, infringiendo normativas de seguridad de la vida en el mar de la OMI."
                ),
                legal_basis_articles=[
                    "Convenio SOLAS (Seguridad de la Vida en el Mar) - Cap. V, Reg. 19.2.4",
                    "Resolución OMI A.1106(29) - Directrices para el uso del AIS a bordo",
                    "Acuerdo sobre Medidas del Estado Rector del Puerto (AMERP - FAO 2009)"
                ],
                enforcement_measures=[
                    "Registro en la lista negra internacional de buques sospechosos IUU",
                    "Notificación a la Organización Regional de Ordenación Pesquera (OROP)",
                    "Denegación de acceso a puertos de escala firmantes del AMERP"
                ],
                economic_penalty_range_usd=(0.0, 100_000.0),
                presumption_of_guilt_applied=False,
                evidence_requirements=[
                    "Contraste de blanco SAR con base global AIS satelital de 6 horas previas"
                ]
            )

    # REGLA 5: Tránsito Legal Cooperativo
    return LegalVerdict(
        rule_id="RULE-INT-UNCLOS-005",
        jurisdiction_zone=zone,
        severity=LegalInfractionSeverity.COMPLIANT_NAVIGATION,
        infraction_code="NAVEGACION_COOPERATIVA_CONFORME",
        title_es="Navegación Conforme y Cooperativa",
        description_es="Buque con telemetría AIS activa y parámetros cinemáticos compatibles con tránsito legal.",
        legal_basis_articles=[
            "CONVEMAR Art. 17 (Derecho de paso inocente) y Art. 87 (Libertades de la alta mar)",
            "Ley 24.922 Art. 22 (Régimen de tránsito por la ZEE con artes estibadas)"
        ],
        enforcement_measures=["Monitoreo pasivo continuo de derrotero"],
        economic_penalty_range_usd=None,
        presumption_of_guilt_applied=False,
        evidence_requirements=["Reportes periódicos AIS almacenados en base de datos"]
    )
