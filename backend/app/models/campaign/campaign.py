import enum
from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
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


class Campaign(Base):
    __tablename__ = "campaigns"

    id = Column(Integer, primary_key=True, index=True)

    # Identifiants et métadonnées globales de la campagne
    name = Column(String(255), nullable=False, index=True)
    code = Column(String(100), nullable=False, unique=True, index=True)
    description = Column(Text, nullable=True)
    visibility = Column(String(50), nullable=False, default="public", index=True)  # "public" | "private"

    # Statut global et version active / courante
    status = Column(String(50), nullable=False, default=CampaignStatus.DRAFT.value, index=True)
    version = Column(String(50), nullable=False, default="1.0.0", index=True)

    # Configuration et paramètres
    settings = Column(JSON, nullable=True, default=dict)

    # Traçabilité & clôture
    created_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    closed_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("code", name="uq_campaigns_code"),
    )

    # Relations
    created_by = relationship("User", foreign_keys=[created_by_id], lazy="selectin")
    closed_by = relationship("User", foreign_keys=[closed_by_id], lazy="selectin")
    versions = relationship(
        "CampaignVersion",
        back_populates="campaign",
        cascade="all, delete-orphan",
        order_by="desc(CampaignVersion.id)",
        lazy="selectin",
    )

    def __repr__(self):
        return f"<Campaign id={self.id} code='{self.code}' name='{self.name}' status='{self.status}' version='{self.version}'>"
