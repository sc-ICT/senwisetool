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


class SubmissionAnswer(Base):
    __tablename__ = "submission_answers"

    id = Column(Integer, primary_key=True, index=True)
    submission_id = Column(
        Integer, ForeignKey("form_submissions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    question_id = Column(
        Integer, ForeignKey("form_questions.id", ondelete="CASCADE"), nullable=False, index=True
    )

    question_code = Column(String(100), nullable=False, index=True)
    section_code = Column(String(100), nullable=False, index=True)

    # Valeur de la réponse (scalaire, tableau de sélections, coordonnées GPS, objet JSON...)
    value = Column(JSON, nullable=True)
    raw_text = Column(Text, nullable=True)

    # Statut d'évaluation de conformité
    compliance_choice = Column(String(30), nullable=True)  # "COMPLIANT", "NON_COMPLIANT", "NOT_APPLICABLE"
    is_compliant = Column(Boolean, nullable=True)
    is_critical_failure = Column(Boolean, nullable=False, default=False)
    points_awarded = Column(Numeric(6, 2), nullable=True, default=0.0)

    # Applicabilité conditionnelle (exclu du dénominateur si inapplicable)
    is_applicable = Column(Boolean, nullable=False, default=True)

    # Justifications et preuves d'audit terrain
    evidence_notes = Column(Text, nullable=True)
    evidence_media_urls = Column(JSON, nullable=True, default=list)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Relations
    submission = relationship("FormSubmission", back_populates="answers", lazy="joined")
    question = relationship("FormQuestion", back_populates="answers", lazy="joined")
