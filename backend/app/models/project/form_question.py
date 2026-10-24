import enum
from sqlalchemy import (
    Column,
    Integer,
    String,
    Boolean,
    Text,
    Numeric,
    DateTime,
    ForeignKey,
    JSON,
    func,
)
from sqlalchemy.orm import relationship
from app.db.database import Base


class QuestionType(str, enum.Enum):
    # 1. Basique / Saisie Standard (Types simples)
    TEXT_SHORT = "text_short"
    TEXT_LONG = "text_long"
    NUMBER_INTEGER = "number_integer"
    NUMBER_DECIMAL = "number_decimal"
    BOOLEAN_TOGGLE = "boolean_toggle"

    # 2. Numérique & Finance
    CURRENCY = "currency"
    PERCENTAGE = "percentage"

    # 3. Date & Heure
    DATE = "date"
    TIME = "time"
    DATETIME = "datetime"
    DATE_RANGE = "date_range"

    # 4. Contact & Web
    EMAIL = "email"
    PHONE = "phone"
    URL = "url"

    # 5. Choix & Évaluation
    CHOICE_SELECT = "choice_select"
    CHOICE_RADIO = "choice_radio"
    CHOICE_MULTI = "choice_multi"
    RATING_SCALE = "rating_scale"

    # 6. Médias & Preuves
    PHOTO = "photo"
    SIGNATURE = "signature"
    DOCUMENT_FILE = "document_file"
    AUDIO = "audio"
    VIDEO = "video"

    # 7. Cartographie & Traçabilité (Terrain)
    GPS_POINT = "gps_point"
    GPS_POLYGON = "gps_polygon"
    QR_BARCODE = "qr_barcode"

    # 8. Types Composés, Audit & Référentiels
    AUDIT_COMPLIANCE = "audit_compliance"
    FORMULA_CALCULATED = "formula_calculated"
    MATRIX_GRID = "matrix_grid"
    RESOURCE_LOOKUP = "resource_lookup"



class FormQuestion(Base):
    __tablename__ = "form_questions"

    id = Column(Integer, primary_key=True, index=True)
    form_id = Column(
        Integer, ForeignKey("forms.id", ondelete="CASCADE"), nullable=True, index=True
    )
    section_id = Column(
        Integer, ForeignKey("form_sections.id", ondelete="CASCADE"), nullable=True, index=True
    )

    code = Column(String(100), nullable=False, index=True)
    label = Column(Text, nullable=False)
    help_text = Column(Text, nullable=True)
    placeholder = Column(String(255), nullable=True)
    question_type = Column(String(50), nullable=False, default=QuestionType.TEXT_SHORT.value, index=True)

    order_index = Column(Integer, nullable=False, default=0)
    is_required = Column(Boolean, nullable=False, default=True)
    is_readonly = Column(Boolean, nullable=False, default=False)
    is_sanctuarized = Column(Boolean, nullable=False, default=False)  # Critère obligatoire imposé par le modèle Super Admin

    # Paramètres de scoring et conformité
    is_critical = Column(Boolean, nullable=False, default=False)  # Non-conformité majeure = blocage
    weight = Column(Numeric(5, 2), nullable=False, default=1.0)
    scoring_config = Column(JSON, nullable=True, default=lambda: {
        "compliant_score": 100.0,
        "non_compliant_score": 0.0,
        "is_scoring_eligible": True,
        "require_proof_on_fail": True
    })

    # Options statiques (pour radio, select, multi)
    # [{"label": "...", "value": "...", "points": 10, "is_critical_trigger": false}]
    options = Column(JSON, nullable=True, default=list)

    # Configuration de liaison dynamique avec une Ressource (type resource_lookup)
    # {
    #   "resource_id": 14,
    #   "resource_code": "planters_registry",
    #   "label_field": "full_name",
    #   "value_field": "farmer_id",
    #   "secondary_label_field": "village",
    #   "filter_criteria": {"status": "active"},
    #   "cascade_autofill": {"Q_VILLAGE": "village", "Q_NATIONAL_ID": "cin_number"}
    # }
    resource_binding = Column(JSON, nullable=True, default=dict)

    # Formule mathématique ou logique pour FORMULA_CALCULATED
    # Ex: "ROUND({Q_GROSS_WEIGHT} * (1 - ({Q_MOISTURE} - 12)/100), 2)"
    formula_expression = Column(String(500), nullable=True)

    # Règles de validation technique (min, max, regex, formats autorisés, résolution GPS min)
    validation_rules = Column(JSON, nullable=True, default=dict)

    # Règles de conditions & dépendances universelles (Nœud vers Nœud)
    # Conditionnée par rapport à une autre question OU une autre section / sous-section
    dependency_rules = Column(JSON, nullable=True, default=dict)

    settings = Column(JSON, nullable=True, default=dict)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relations
    form = relationship("Form", back_populates="questions", lazy="joined")
    section = relationship("FormSection", back_populates="questions", lazy="joined")
    answers = relationship("SubmissionAnswer", back_populates="question", cascade="all, delete-orphan", lazy="selectin")
