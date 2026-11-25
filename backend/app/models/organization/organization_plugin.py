from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, ForeignKey, JSON, func
from sqlalchemy.orm import relationship
from app.db.database import Base


class OrganizationPlugin(Base):
    """
    Table miroir locale des plugins pour les organisations.
    Puisque les organisations ne modifient pas directement le catalogue global,
    les champs organization_ids et user_ids stockent sous forme de tableau JSON
    l'ensemble des organisations et utilisateurs utilisant ce plugin.
    """
    __tablename__ = "organization_plugins"

    id = Column(Integer, primary_key=True, index=True)
    source_plugin_id = Column(Integer, ForeignKey("plugins.id", ondelete="SET NULL"), nullable=True, index=True)

    # Listes des organisations et utilisateurs ayant téléchargé / exploitant ce plugin
    organization_ids = Column(JSON, nullable=False, default=list)
    user_ids = Column(JSON, nullable=False, default=list)

    # Identiques à Plugin
    code = Column(String(100), index=True, nullable=False)
    name = Column(String(200), nullable=False, index=True)
    category = Column(String(100), nullable=False, index=True)
    description = Column(Text, nullable=True)
    short_description = Column(String(500), nullable=True)
    icon_url = Column(String(500), nullable=True)
    banner_url = Column(String(500), nullable=True)
    author = Column(String(100), nullable=False, default="SenWiseTool")
    status = Column(String(30), nullable=False, default="active", index=True)
    tags = Column(JSON, nullable=True, default=list)
    is_public = Column(Boolean, nullable=False, default=True)
    allowed_organization_ids = Column(JSON, nullable=True, default=list)

    # Paramètres et configurations
    settings = Column(JSON, nullable=False, default=dict)
    parameters = Column(JSON, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    # Relations
    source_plugin = relationship("Plugin", foreign_keys=[source_plugin_id])
    versions = relationship(
        "OrganizationPluginVersion",
        back_populates="organization_plugin",
        cascade="all, delete-orphan",
        order_by="desc(OrganizationPluginVersion.id)",
        lazy="selectin",
    )
    features = relationship(
        "OrganizationFeature",
        back_populates="organization_plugin",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<OrganizationPlugin id={self.id} code='{self.code}' name='{self.name}'>"


class OrganizationPluginVersion(Base):
    """
    Table miroir locale des versions de plugins.
    Rattachée à organization_plugins (organization_plugin_id).
    organization_ids et user_ids stockent la liste des organisations et utilisateurs
    ayant téléchargé / utilisant cette version spécifique.
    """
    __tablename__ = "organization_plugin_versions"

    id = Column(Integer, primary_key=True, index=True)
    organization_plugin_id = Column(Integer, ForeignKey("organization_plugins.id", ondelete="CASCADE"), nullable=False, index=True)
    source_plugin_version_id = Column(Integer, ForeignKey("plugin_versions.id", ondelete="SET NULL"), nullable=True, index=True)

    # Listes des organisations et utilisateurs utilisant cette version
    organization_ids = Column(JSON, nullable=False, default=list)
    user_ids = Column(JSON, nullable=False, default=list)

    # Identiques à PluginVersion
    version_number = Column(String(50), nullable=False, index=True)
    changelog = Column(Text, nullable=True)
    package_data = Column(JSON, nullable=False, default=dict)
    usage_policy = Column(Text, nullable=True)
    status = Column(String(30), nullable=False, default="active", index=True)

    published_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    published_at = Column(DateTime(timezone=True), nullable=True)

    deprecated_at = Column(DateTime(timezone=True), nullable=True)
    deprecated_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    deprecated_eol_date = Column(DateTime(timezone=True), nullable=True)
    deprecation_reason = Column(Text, nullable=True)
    is_blocked = Column(Boolean, default=False, nullable=False)

    # Paramètres et configurations
    settings = Column(JSON, nullable=False, default=dict)
    parameters = Column(JSON, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    # Relations
    organization_plugin = relationship("OrganizationPlugin", back_populates="versions")
    source_plugin_version = relationship("PluginVersion", foreign_keys=[source_plugin_version_id])
    published_by = relationship("User", foreign_keys=[published_by_id])

    # Dépendances locales embarquées
    projects = relationship(
        "OrganizationProject",
        back_populates="organization_plugin_version",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    campaigns = relationship(
        "OrganizationCampaign",
        back_populates="organization_plugin_version",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    resources = relationship(
        "OrganizationResource",
        back_populates="organization_plugin_version",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    forms = relationship(
        "OrganizationForm",
        back_populates="organization_plugin_version",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    features = relationship(
        "OrganizationFeature",
        back_populates="organization_plugin_version",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<OrganizationPluginVersion id={self.id} version='{self.version_number}'>"
