import enum
from sqlalchemy import (
    Column,
    Integer,
    String,
    Boolean,
    Text,
    DateTime,
    ForeignKey,
    JSON,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import relationship
from app.db.database import Base


class ResourceStatus(str, enum.Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class Resource(Base):
    __tablename__ = "resources"

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
    immutable_fields = Column(JSON, nullable=True, default=list)  # ex: ["schema", "code", "name"]
    parent_template_id = Column(Integer, nullable=True, index=True)

    name = Column(String(255), nullable=False, index=True)
    code = Column(String(100), nullable=False, index=True)
    description = Column(Text, nullable=True)
    category = Column(String(100), nullable=False, default="general", index=True)
    icon = Column(String(50), nullable=False, default="Database")
    status = Column(String(50), nullable=False, default=ResourceStatus.DRAFT.value, index=True)

    # Versioning et cycle de vie
    version = Column(String(20), nullable=False, default="1.0.0", index=True)
    is_lts = Column(Boolean, nullable=False, default=False, index=True)
    schema_history = Column(JSON, nullable=True, default=list)

    # Schéma dynamique des champs de la ressource
    # Liste de dicts: [{"name": "...", "label": "...", "type": "...", "required": bool, "unique": bool, "options": [...], "default_value": ...}]
    schema_fields = Column(JSON, nullable=False, default=list)

    settings = Column(JSON, nullable=True, default=dict)
    metrics = Column(JSON, nullable=True, default=lambda: {"item_count": 0})

    created_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "code", name="uq_resource_org_code"),
    )

    # Relations
    organization = relationship("Organization", lazy="joined")
    plugin = relationship("Plugin", lazy="selectin")
    created_by = relationship("User", foreign_keys=[created_by_id], lazy="selectin")
    items = relationship("ResourceItem", back_populates="resource", cascade="all, delete-orphan", lazy="selectin")
