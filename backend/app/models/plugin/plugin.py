import enum
from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, ForeignKey, JSON, UniqueConstraint, func
from sqlalchemy.orm import relationship
from app.db.database import Base


class PluginStatus(str, enum.Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"


class PluginVersionStatus(str, enum.Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    DEPRECATED = "deprecated"


class OrgPluginStatus(str, enum.Enum):
    INSTALLED = "installed"
    DISABLED = "disabled"
    UNINSTALLED = "uninstalled"


class Plugin(Base):
    __tablename__ = "plugins"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(100), unique=True, index=True, nullable=False)  # ex: 'cocoa-eudr'
    name = Column(String(200), nullable=False, index=True)
    category = Column(String(100), nullable=False, index=True)
    description = Column(Text, nullable=True)
    short_description = Column(String(500), nullable=True)
    icon_url = Column(String(500), nullable=True)
    banner_url = Column(String(500), nullable=True)
    author = Column(String(100), nullable=False, default="SenWiseTool")
    status = Column(String(30), nullable=False, default=PluginStatus.DRAFT.value, index=True)
    tags = Column(JSON, nullable=True, default=list)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    # Relations
    versions = relationship(
        "PluginVersion",
        back_populates="plugin",
        cascade="all, delete-orphan",
        order_by="desc(PluginVersion.id)"
    )
    installations = relationship("OrganizationPlugin", back_populates="plugin")


    def __repr__(self) -> str:
        return f"<Plugin id={self.id} code={self.code} name={self.name} status={self.status}>"


class PluginVersion(Base):
    __tablename__ = "plugin_versions"

    id = Column(Integer, primary_key=True, index=True)
    plugin_id = Column(Integer, ForeignKey("plugins.id", ondelete="CASCADE"), nullable=False, index=True)
    version_number = Column(String(50), nullable=False, index=True)  # ex: '1.0.0'
    changelog = Column(Text, nullable=True)  # Markdown ou texte résumant les ajouts / correctifs
    package_data = Column(JSON, nullable=False, default=dict)  # Projets, formulaires, télémétrie, étapes
    usage_policy = Column(Text, nullable=False)  # Politique d'utilisation obligatoire (EULA)
    status = Column(String(30), nullable=False, default=PluginVersionStatus.DRAFT.value, index=True)

    published_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    published_at = Column(DateTime(timezone=True), nullable=True)

    # Dépréciation, échéance EOL et blocage définitif (Règle 9)
    deprecated_at = Column(DateTime(timezone=True), nullable=True)
    deprecated_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    deprecated_eol_date = Column(DateTime(timezone=True), nullable=True)  # Date limite avant blocage et fin de vie
    deprecation_reason = Column(Text, nullable=True)
    is_blocked = Column(Boolean, default=False, nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


    # Relations
    plugin = relationship("Plugin", back_populates="versions")
    published_by = relationship("User", foreign_keys=[published_by_id])
    installations = relationship("OrganizationPlugin", back_populates="installed_version")

    __table_args__ = (
        UniqueConstraint("plugin_id", "version_number", name="uq_plugin_version_number"),
    )

    def __repr__(self) -> str:
        return f"<PluginVersion id={self.id} plugin_id={self.plugin_id} version={self.version_number} status={self.status}>"


class OrganizationPlugin(Base):
    __tablename__ = "organization_plugins"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    plugin_id = Column(Integer, ForeignKey("plugins.id", ondelete="RESTRICT"), nullable=False, index=True)
    installed_version_id = Column(Integer, ForeignKey("plugin_versions.id", ondelete="RESTRICT"), nullable=False, index=True)

    status = Column(String(30), nullable=False, default=OrgPluginStatus.INSTALLED.value, index=True)

    # Traçabilité juridique de la politique d'utilisation
    policy_accepted_at = Column(DateTime(timezone=True), nullable=False)
    policy_accepted_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    policy_version = Column(String(50), nullable=False)

    # Paramètres locaux et configurations spécifiques à l'organisation
    local_settings = Column(JSON, nullable=True, default=dict)

    installed_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    # Relations
    organization = relationship("Organization")
    plugin = relationship("Plugin", back_populates="installations")
    installed_version = relationship("PluginVersion", foreign_keys=[installed_version_id], back_populates="installations")
    policy_accepted_by = relationship("User", foreign_keys=[policy_accepted_by_user_id])

    # Contrainte absolue : 1 organisation ne peut intégrer un même plugin qu'une seule fois
    __table_args__ = (
        UniqueConstraint("organization_id", "plugin_id", name="uq_organization_plugin"),
    )

    def __repr__(self) -> str:
        return f"<OrganizationPlugin id={self.id} org_id={self.organization_id} plugin_id={self.plugin_id} version_id={self.installed_version_id} status={self.status}>"
