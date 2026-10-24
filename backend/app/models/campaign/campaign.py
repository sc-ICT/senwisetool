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


class CampaignStatus(str, enum.Enum):
    DRAFT = "draft"
    OPEN = "open"
    PAUSED = "paused"
    CLOSED = "closed"
    ARCHIVED = "archived"


class CampaignType(str, enum.Enum):
    HARVEST = "harvest"          # Récolte agricole / filière
    AUDIT = "audit"              # Audit de conformité réglementaire (ex: EUDR)
    SURVEY = "survey"            # Recensement / Enquête producteurs
    INSPECTION = "inspection"    # Contrôle technique / sécurité
    CUSTOM = "custom"            # Type sur-mesure défini par le plugin


class Campaign(Base):
    __tablename__ = "campaigns"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    plugin_id = Column(
        Integer, ForeignKey("plugins.id", ondelete="SET NULL"), nullable=True, index=True
    )
    plugin_code = Column(String(100), nullable=True, index=True)

    # Gouvernance et distribution de l'outil
    scope = Column(String(30), nullable=False, default="tenant", index=True)  # "tenant", "plugin_shared", "preconfigured"
    is_shared = Column(Boolean, nullable=False, default=False, index=True)
    is_immutable = Column(Boolean, nullable=False, default=False, index=True)
    immutable_fields = Column(JSON, nullable=True, default=list)  # ex: ["campaign_type", "target_forms", "code"]
    parent_template_id = Column(Integer, nullable=True, index=True)

    name = Column(String(255), nullable=False, index=True)
    code = Column(String(100), nullable=False, index=True)
    campaign_type = Column(String(100), nullable=False, default=CampaignType.HARVEST.value, index=True)
    status = Column(String(50), nullable=False, default=CampaignStatus.DRAFT.value, index=True)

    description = Column(Text, nullable=True)
    objectives = Column(Text, nullable=True)
    targets = Column(JSON, nullable=True, default=dict)

    start_date = Column(DateTime(timezone=True), nullable=True)
    end_date = Column(DateTime(timezone=True), nullable=True)

    budget = Column(Numeric(15, 2), nullable=True)
    currency = Column(String(10), nullable=False, default="XOF")

    target_forms = Column(JSON, nullable=True, default=list)
    target_projects = Column(JSON, nullable=True, default=list)
    settings = Column(JSON, nullable=True, default=dict)
    metrics = Column(JSON, nullable=True, default=dict)

    created_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    closed_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "code", name="uq_campaign_org_code"),
    )

    # Relations
    organization = relationship("Organization", lazy="joined")
    plugin = relationship("Plugin", lazy="selectin")
    created_by = relationship("User", foreign_keys=[created_by_id], lazy="selectin")
    closed_by = relationship("User", foreign_keys=[closed_by_id], lazy="selectin")
