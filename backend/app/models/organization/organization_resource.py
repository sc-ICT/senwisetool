from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, ForeignKey, JSON, func
from sqlalchemy.orm import relationship
from app.db.database import Base


class OrganizationResource(Base):
    """
    Table miroir locale des ressources (bases de connaissances, référentiels, tables dynamiques) pour l'organisation cliente.
    Rattachée à organization_plugin_versions (organization_plugin_version_id),
    à l'organisation cliente (organization_id) et à l'utilisateur ayant téléchargé (downloaded_by_user_id).
    source_resource_id relie à la table d'origine resources.
    """
    __tablename__ = "organization_resources"

    id = Column(Integer, primary_key=True, index=True)
    source_resource_id = Column(Integer, ForeignKey("resources.id", ondelete="SET NULL"), nullable=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    organization_plugin_version_id = Column(Integer, ForeignKey("organization_plugin_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    downloaded_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    # Identiques à Resource
    name = Column(String(255), nullable=False, index=True)
    code = Column(String(100), nullable=False, index=True)
    category = Column(String(100), nullable=False, default="general")
    visibility = Column(String(50), nullable=False, default="private")
    authorized_organization_ids = Column(JSON, nullable=True, default=list)
    cover_image_url = Column(Text, nullable=True)
    icon = Column(String(50), nullable=False, default="database")
    status = Column(String(50), nullable=False, default="active", index=True)
    version = Column(String(20), nullable=False, default="1.0.0")

    scope = Column(String(30), nullable=False, default="organization")
    is_shared = Column(Boolean, nullable=False, default=False)
    is_immutable = Column(Boolean, nullable=False, default=False)
    immutable_fields = Column(JSON, nullable=True, default=list)
    is_lts = Column(Boolean, nullable=False, default=False)
    schema_history = Column(JSON, nullable=True, default=list)
    description = Column(Text, nullable=True)
    schema_fields = Column(JSON, nullable=False, default=list)

    # Paramètres et configurations
    settings = Column(JSON, nullable=False, default=dict)
    parameters = Column(JSON, nullable=False, default=dict)
    metrics = Column(JSON, nullable=True, default=dict)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    # Relations
    organization = relationship("Organization")
    organization_plugin_version = relationship("OrganizationPluginVersion", back_populates="resources")
    source_resource = relationship("Resource", foreign_keys=[source_resource_id], lazy="selectin")
    downloaded_by_user = relationship("User", foreign_keys=[downloaded_by_user_id])

    versions = relationship(
        "OrganizationResourceVersion",
        back_populates="organization_resource",
        cascade="all, delete-orphan",
        order_by="desc(OrganizationResourceVersion.id)",
        lazy="selectin",
    )
    items = relationship(
        "OrganizationResourceItem",
        back_populates="organization_resource",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<OrganizationResource id={self.id} org_id={self.organization_id} code='{self.code}' name='{self.name}'>"


class OrganizationResourceVersion(Base):
    """
    Table miroir locale des versions de ressources téléchargées.
    Liaisons internes vers organization_resources (organization_resource_id)
    et organization_plugin_versions (organization_plugin_version_id).
    source_resource_version_id relie à resource_versions.
    """
    __tablename__ = "organization_resource_versions"

    id = Column(Integer, primary_key=True, index=True)
    organization_resource_id = Column(Integer, ForeignKey("organization_resources.id", ondelete="CASCADE"), nullable=False, index=True)
    organization_plugin_version_id = Column(Integer, ForeignKey("organization_plugin_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    source_resource_version_id = Column(Integer, ForeignKey("resource_versions.id", ondelete="SET NULL"), nullable=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    downloaded_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    # Identiques à ResourceVersion
    version_number = Column(String(50), nullable=False, index=True)
    status = Column(String(50), nullable=False, default="published", index=True)
    description = Column(Text, nullable=True)
    changelog = Column(Text, nullable=True)
    schema_fields = Column(JSON, nullable=False, default=list)
    metrics = Column(JSON, nullable=True, default=dict)
    template_governance = Column(JSON, nullable=True, default=dict)

    # Paramètres et configurations
    settings = Column(JSON, nullable=False, default=dict)
    parameters = Column(JSON, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    # Relations
    organization = relationship("Organization")
    organization_resource = relationship("OrganizationResource", back_populates="versions")
    organization_plugin_version = relationship("OrganizationPluginVersion")
    source_resource_version = relationship("ResourceVersion", foreign_keys=[source_resource_version_id], lazy="selectin")
    downloaded_by_user = relationship("User", foreign_keys=[downloaded_by_user_id])

    items = relationship(
        "OrganizationResourceItem",
        back_populates="organization_resource_version",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<OrganizationResourceVersion id={self.id} org_id={self.organization_id} version='{self.version_number}'>"


class OrganizationResourceItem(Base):
    """
    Table miroir locale des données / items de ressources téléchargées.
    Liaisons internes vers organization_resources (organization_resource_id)
    et organization_resource_versions (organization_resource_version_id)
    et organization_plugin_versions (organization_plugin_version_id).
    source_resource_item_id relie à resource_items.
    """
    __tablename__ = "organization_resource_items"

    id = Column(Integer, primary_key=True, index=True)
    organization_resource_id = Column(Integer, ForeignKey("organization_resources.id", ondelete="CASCADE"), nullable=False, index=True)
    organization_resource_version_id = Column(Integer, ForeignKey("organization_resource_versions.id", ondelete="CASCADE"), nullable=True, index=True)
    organization_plugin_version_id = Column(Integer, ForeignKey("organization_plugin_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    source_resource_item_id = Column(Integer, ForeignKey("resource_items.id", ondelete="SET NULL"), nullable=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    downloaded_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    # Identiques à ResourceItem
    data = Column(JSON, nullable=False, default=dict)

    # Paramètres et configurations
    settings = Column(JSON, nullable=False, default=dict)
    parameters = Column(JSON, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    # Relations
    organization = relationship("Organization")
    organization_resource = relationship("OrganizationResource", back_populates="items")
    organization_resource_version = relationship("OrganizationResourceVersion", back_populates="items")
    organization_plugin_version = relationship("OrganizationPluginVersion")
    source_resource_item = relationship("ResourceItem", foreign_keys=[source_resource_item_id])
    downloaded_by_user = relationship("User", foreign_keys=[downloaded_by_user_id])

    def __repr__(self) -> str:
        return f"<OrganizationResourceItem id={self.id} org_id={self.organization_id} resource_id={self.organization_resource_id}>"
