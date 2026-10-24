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
    Une section peut contenir des sous-sections (via parent_section_id)
    et des questions (via questions relationship).
    """
    __tablename__ = "form_sections"

    id = Column(Integer, primary_key=True, index=True)
    form_id = Column(
        Integer, ForeignKey("forms.id", ondelete="CASCADE"), nullable=False, index=True
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

    # Exigences & Sanctuarisation du Modèle Super Admin
    is_sanctuarized = Column(Boolean, nullable=False, default=False)  # Section obligatoire imposée par le modèle
    allow_custom_questions = Column(Boolean, nullable=False, default=True)  # L'organisation peut-elle ajouter des questions ici ?

    # Paramètres d'évaluation et de pondération pour le scoring
    weight = Column(Numeric(5, 2), nullable=False, default=1.0)
    scoring_category = Column(String(100), nullable=True, index=True)
    is_critical = Column(Boolean, nullable=False, default=False)  # Si non-conforme, bloque l'audit
    min_passing_score = Column(Numeric(5, 2), nullable=True)     # Score minimal requis pour cette section

    # Règles de conditions & dépendances universelles (Nœud vers Nœud)
    # Ex: Dépendance par rapport à une autre Question ou par rapport à une autre Section/Sous-section
    # Structure:
    # {
    #   "logic": "AND" | "OR",
    #   "conditions": [
    #     {
    #       "source_type": "question" | "section",
    #       "source_code": "Q_IS_FOREST_ZONE" | "SEC_PRELIMINARY",
    #       "operator": "EQUALS" | "NOT_EQUALS" | "COMPLIANCE_GREATER_EQUAL" | "STATUS_EQUALS",
    #       "value": true | 80 | "COMPLETED"
    #     }
    #   ],
    #   "actions": {
    #     "on_true": "SHOW" | "HIDE" | "ENABLE" | "DISABLE" | "MAKE_REQUIRED",
    #     "on_false": "HIDE" | "MAKE_OPTIONAL"
    #   }
    # }
    dependency_rules = Column(JSON, nullable=True, default=dict)

    settings = Column(JSON, nullable=True, default=dict)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relations
    form = relationship("Form", back_populates="sections", lazy="joined")
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
        lazy="selectin"
    )
    questions = relationship(
        "FormQuestion",
        back_populates="section",
        cascade="all, delete-orphan",
        order_by="FormQuestion.order_index",
        lazy="selectin"
    )
