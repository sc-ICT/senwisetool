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
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import relationship
from app.db.database import Base


class FormStatus(str, enum.Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class FormType(str, enum.Enum):
    AUDIT = "audit"              # Audit et inspection avec conformité / non-conformité
    SURVEY = "survey"            # Questionnaire et recensement
    INSPECTION = "inspection"    # Contrôle technique, parcelle ou usine
    CHECKLIST = "checklist"      # Liste de vérification rapide
    ONBOARDING = "onboarding"    # Enregistrement producteur / membre
    CUSTOM = "custom"


class Form(Base):
    __tablename__ = "forms"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(
        Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )

    name = Column(String(255), nullable=False, index=True)
    code = Column(String(100), nullable=False, index=True)
    description = Column(Text, nullable=True)
    form_type = Column(String(50), nullable=False, default=FormType.AUDIT.value, index=True)
    version = Column(String(20), nullable=False, default="1.0.0", index=True)
    status = Column(String(50), nullable=False, default=FormStatus.DRAFT.value, index=True)

    order_index = Column(Integer, nullable=False, default=0)
    is_template = Column(Boolean, nullable=False, default=True, index=True)
    is_sanctuarized = Column(Boolean, nullable=False, default=False)  # Formulaire imposé par le modèle
    allow_custom_sections = Column(Boolean, nullable=False, default=True)  # L'organisation peut-elle ajouter des sections ?
    is_mandatory = Column(Boolean, nullable=False, default=True)
    is_repeatable = Column(Boolean, nullable=False, default=False)
    allow_draft_save = Column(Boolean, nullable=False, default=True)
    offline_capable = Column(Boolean, nullable=False, default=True)

    # Paramètres de calcul et de conformité / scoring
    scoring_enabled = Column(Boolean, nullable=False, default=True)
    scoring_scale = Column(String(30), nullable=False, default="percentage_100")
    passing_score_percentage = Column(Numeric(5, 2), nullable=False, default=80.00)
    critical_fail_threshold = Column(Integer, nullable=False, default=1)  # 1 échec critique = rejet global

    # Liaison dynamique avec les ressources (génération automatique ou stockage de données)
    resource_binding_config = Column(JSON, nullable=True, default=dict)

    settings = Column(JSON, nullable=True, default=dict)
    metrics = Column(JSON, nullable=True, default=lambda: {
        "submissions_count": 0,
        "avg_score": 0.0,
        "compliant_count": 0,
        "non_compliant_count": 0
    })

    created_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("project_id", "code", name="uq_form_project_code"),
    )

    # Relations
    project = relationship("Project", back_populates="forms", lazy="joined")
    organization = relationship("Organization", lazy="joined")
    created_by = relationship("User", foreign_keys=[created_by_id], lazy="selectin")
    sections = relationship(
        "FormSection",
        primaryjoin="and_(Form.id == FormSection.form_id, FormSection.parent_section_id.is_(None))",
        back_populates="form",
        cascade="all, delete-orphan",
        order_by="FormSection.order_index",
        lazy="selectin"
    )
    questions = relationship(
        "FormQuestion",
        primaryjoin="and_(Form.id == FormQuestion.form_id, FormQuestion.section_id.is_(None))",
        back_populates="form",
        cascade="all, delete-orphan",
        order_by="FormQuestion.order_index",
        lazy="selectin"
    )
    submissions = relationship(
        "FormSubmission",
        back_populates="form",
        cascade="all, delete-orphan",
        lazy="selectin"
    )
