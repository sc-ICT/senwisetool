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


class FormSection(Base):
    """
    Section ou Sous-section récursive d'un formulaire ($N$-aire).
    Liée à un formulaire et à une version spécifique de formulaire.
    """
    __tablename__ = "form_sections"

    id = Column(Integer, primary_key=True, index=True)
    form_id = Column(
        Integer, ForeignKey("forms.id", ondelete="CASCADE"), nullable=False, index=True
    )
    form_version_id = Column(
        Integer, ForeignKey("form_versions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    parent_section_id = Column(
        Integer, ForeignKey("form_sections.id", ondelete="CASCADE"), nullable=True, index=True
    )

    title = Column(Text, nullable=False)
    code = Column(String(100), nullable=False, index=True)
    description = Column(Text, nullable=True)
    order_index = Column(Integer, nullable=False, default=0)

    # Propriétés de structure & répétition
    is_repeatable = Column(Boolean, nullable=False, default=False)
    min_repeats = Column(Integer, nullable=False, default=1)
    max_repeats = Column(Integer, nullable=True)

    # Exigences & Sanctuarisation
    is_sanctuarized = Column(Boolean, nullable=False, default=False)
    allow_custom_questions = Column(Boolean, nullable=False, default=True)

    # Paramètres d'évaluation et de pondération pour le scoring
    weight = Column(Numeric(5, 2), nullable=False, default=1.0)
    scoring_category = Column(String(100), nullable=True, index=True)
    is_critical = Column(Boolean, nullable=False, default=False)
    min_passing_score = Column(Numeric(5, 2), nullable=True)

    dependency_rules = Column(JSON, nullable=True, default=dict)
    settings = Column(JSON, nullable=True, default=dict)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relations
    form = relationship("Form", lazy="joined")
    version = relationship("FormVersion", back_populates="sections", lazy="joined")
    parent_section = relationship(
        "FormSection",
        remote_side=[id],
        back_populates="subsections",
        lazy="joined"
    )
    subsections = relationship(
        "FormSection",
        back_populates="parent_section",
        cascade="all, delete-orphan",
        order_by="FormSection.order_index",
        lazy="selectin",
        join_depth=5,
    )
    questions = relationship(
        "FormQuestion",
        back_populates="section",
        cascade="all, delete-orphan",
        order_by="FormQuestion.order_index",
        lazy="selectin",
    )
