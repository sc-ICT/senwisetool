import enum
from sqlalchemy import (
    Column,
    Integer,
    String,
    Numeric,
    DateTime,
    ForeignKey,
    JSON,
    Text,
    func,
)
from sqlalchemy.orm import relationship
from app.db.database import Base


class SubmissionStatus(str, enum.Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    VALIDATED = "validated"
    REJECTED = "rejected"
    FLAGGED = "flagged"


class ComplianceStatus(str, enum.Enum):
    COMPLIANT = "COMPLIANT"
    NON_COMPLIANT = "NON_COMPLIANT"
    PARTIALLY_COMPLIANT = "PARTIALLY_COMPLIANT"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class FormSubmission(Base):
    __tablename__ = "form_submissions"

    id = Column(Integer, primary_key=True, index=True)
    form_id = Column(
        Integer, ForeignKey("forms.id", ondelete="CASCADE"), nullable=False, index=True
    )
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )

    submission_code = Column(String(100), unique=True, nullable=False, index=True)
    submitted_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    status = Column(String(50), nullable=False, default=SubmissionStatus.SUBMITTED.value, index=True)
    total_score = Column(Numeric(5, 2), nullable=True)  # Ex: 87.50%
    compliance_status = Column(
        String(50), nullable=False, default=ComplianceStatus.COMPLIANT.value, index=True
    )
    critical_failures_count = Column(Integer, nullable=False, default=0)

    # Décomposition granulaire des scores par section et sous-section
    # {
    #   "SEC_PRELIMINARY": {"score": 100.0, "status": "COMPLIANT", "critical_fail": false},
    #   "SEC_DEFORESTATION": {"score": 66.67, "status": "NON_COMPLIANT", "critical_fail": true}
    # }
    section_scores = Column(JSON, nullable=True, default=dict)

    # Métadonnées de localisation et horodatage
    geolocation = Column(JSON, nullable=True)  # {"lat": 5.345, "lng": -4.012, "accuracy_m": 4.5}
    offline_created_at = Column(DateTime(timezone=True), nullable=True)
    submitted_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Cycle de validation (Gouvernance & revue superviseur)
    validated_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    validated_at = Column(DateTime(timezone=True), nullable=True)
    validation_notes = Column(Text, nullable=True)

    metadata_info = Column(JSON, nullable=True, default=dict)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relations
    form = relationship("Form", back_populates="submissions", lazy="joined")
    organization = relationship("Organization", lazy="joined")
    submitted_by = relationship("User", foreign_keys=[submitted_by_id], lazy="selectin")
    validated_by = relationship("User", foreign_keys=[validated_by_id], lazy="selectin")
    answers = relationship(
        "SubmissionAnswer",
        back_populates="submission",
        cascade="all, delete-orphan",
        lazy="selectin"
    )
