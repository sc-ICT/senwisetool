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


class ResourceVersionStatus(str, enum.Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ACTIVE = "active"
    ARCHIVED = "archived"


class ResourceVersion(Base):
    __tablename__ = "resource_versions"

    id = Column(Integer, primary_key=True, index=True)
    resource_id = Column(Integer, ForeignKey("resources.id", ondelete="CASCADE"), nullable=False, index=True)
    version_number = Column(String(50), nullable=False)  # ex: '1.0.0'
    status = Column(String(50), nullable=False, default=ResourceVersionStatus.DRAFT.value, index=True)
    description = Column(Text, nullable=True)  # Markdown enrichi
    changelog = Column(Text, nullable=True)
    schema_fields = Column(JSON, nullable=False, default=list)
    metrics = Column(JSON, nullable=True, default=lambda: {
        "item_count": 0
    })
    settings = Column(JSON, nullable=True, default=dict)
    template_governance = Column(JSON, nullable=True, default=dict)
    created_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("resource_id", "version_number", name="uq_resource_version_number"),
    )

    # Relations
    resource = relationship("Resource", back_populates="versions", lazy="selectin")
    created_by = relationship("User", foreign_keys=[created_by_id], lazy="selectin")

