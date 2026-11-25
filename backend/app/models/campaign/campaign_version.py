import enum
from sqlalchemy import (
    Column,
    Integer,
    String,
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


class CampaignVersionStatus(str, enum.Enum):
    DRAFT = "draft"
    OPEN = "open"
    ACTIVE = "active"
    PAUSED = "paused"
    CLOSED = "closed"
    ARCHIVED = "archived"


class CampaignVersion(Base):
    __tablename__ = "campaign_versions"

    id = Column(Integer, primary_key=True, index=True)
    campaign_id = Column(
        Integer, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_number = Column(String(50), nullable=False)  # ex: '1.0.0'
    status = Column(
        String(50), nullable=False, default=CampaignVersionStatus.DRAFT.value, index=True
    )

    # Contenu opérationnel et directives
    description = Column(Text, nullable=True)  # Markdown enrichi
    objectives = Column(Text, nullable=True)
    targets = Column(JSON, nullable=True, default=dict)  # Récurrence, cibles, timeline

    # Période de validité de cette version
    start_date = Column(DateTime(timezone=True), nullable=True)
    end_date = Column(DateTime(timezone=True), nullable=True)

    # Budget & devise pour cette version
    budget = Column(Numeric(15, 2), nullable=True)
    currency = Column(String(10), nullable=False, default="XOF")

    # Périmètre opérationnel lié
    target_forms = Column(JSON, nullable=True, default=list)
    target_projects = Column(JSON, nullable=True, default=list)

    # Paramètres et gouvernance
    settings = Column(JSON, nullable=True, default=dict)
    template_governance = Column(JSON, nullable=True, default=dict)
    changelog = Column(Text, nullable=True)
    metrics = Column(
        JSON,
        nullable=True,
        default=lambda: {
            "progress_percentage": 0,
            "total_submissions": 0,
            "validated_submissions": 0,
        },
    )

    # Traçabilité & clôture
    created_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    closed_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("campaign_id", "version_number", name="uq_campaign_version_number"),
    )

    # Relations
    campaign = relationship("Campaign", back_populates="versions")
    created_by = relationship("User", foreign_keys=[created_by_id], lazy="selectin")
    closed_by = relationship("User", foreign_keys=[closed_by_id], lazy="selectin")

    def __repr__(self):
        return f"<CampaignVersion id={self.id} campaign_id={self.campaign_id} version='{self.version_number}' status='{self.status}'>"
