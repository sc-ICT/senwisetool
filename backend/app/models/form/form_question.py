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
    # 1. Basique / Saisie Standard
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
    LOCATION = "location"  # Localisation Administrative & Géographique Progressive (Pays -> Divisions -> Point GPS)

    # 8. Types Composés, Audit & Référentiels
    AUDIT_COMPLIANCE = "audit_compliance"
    FORMULA_CALCULATED = "formula_calculated"
    MATRIX_GRID = "matrix_grid"
    RESOURCE_LOOKUP = "resource_lookup"
    CROSS_FORM_LOOKUP = "cross_form_lookup"


class FormQuestion(Base):
    __tablename__ = "form_questions"

    id = Column(Integer, primary_key=True, index=True)
    form_id = Column(
        Integer, ForeignKey("forms.id", ondelete="CASCADE"), nullable=True, index=True
    )
    form_version_id = Column(
        Integer, ForeignKey("form_versions.id", ondelete="CASCADE"), nullable=True, index=True
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
    is_sanctuarized = Column(Boolean, nullable=False, default=False)

    # Paramètres de scoring et conformité
    is_critical = Column(Boolean, nullable=False, default=False)
    weight = Column(Numeric(5, 2), nullable=False, default=1.0)
    scoring_config = Column(JSON, nullable=True, default=lambda: {
        "compliant_score": 100.0,
        "non_compliant_score": 0.0,
        "is_scoring_eligible": True,
        "require_proof_on_fail": True
    })

    options = Column(JSON, nullable=True, default=list)
    resource_binding = Column(JSON, nullable=True, default=dict)
    formula_expression = Column(String(500), nullable=True)
    validation_rules = Column(JSON, nullable=True, default=dict)
    dependency_rules = Column(JSON, nullable=True, default=dict)
    settings = Column(JSON, nullable=True, default=dict)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relations
    form = relationship("Form", lazy="joined")
    version = relationship("FormVersion", back_populates="questions", lazy="joined")
    section = relationship("FormSection", back_populates="questions", lazy="joined")
