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
    is_public = Column(Boolean, nullable=False, default=True, index=True)
    allowed_organization_ids = Column(JSON, nullable=True, default=list)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    # Relations
    versions = relationship(
        "PluginVersion",
        back_populates="plugin",
        cascade="all, delete-orphan",
        order_by="desc(PluginVersion.id)",
        lazy="selectin"
    )


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
    tools = relationship(
        "PluginVersionTool",
        back_populates="version",
        cascade="all, delete-orphan",
        order_by="PluginVersionTool.id",
        lazy="selectin"
    )

    __table_args__ = (
        UniqueConstraint("plugin_id", "version_number", name="uq_plugin_version_number"),
    )

    def __repr__(self) -> str:
        return f"<PluginVersion id={self.id} plugin_id={self.plugin_id} version={self.version_number} status={self.status}>"


class PluginVersionTool(Base):
    __tablename__ = "plugin_version_tools"

    id = Column(Integer, primary_key=True, index=True)
    plugin_version_id = Column(Integer, ForeignKey("plugin_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    item_key = Column(String(100), nullable=False, index=True)
    tool_code = Column(String(50), nullable=False, index=True)
    type = Column(String(30), nullable=False, default="module")  # 'feature' ou 'module'
    config_target = Column(String(30), nullable=False, default="tool_resource")  # 'tool_global' ou 'tool_resource'
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    selected_resource_id = Column(Integer, nullable=True)
    selected_resource_code = Column(String(100), nullable=True)
    selected_resource_name = Column(String(200), nullable=True)
    selected_resource_status = Column(String(50), nullable=True)
    distribution_mode = Column(String(50), nullable=False, default="tenant")
    permissions_mode = Column(String(50), nullable=False, default="restricted")
    parameters = Column(JSON, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    version = relationship("PluginVersion", back_populates="tools")

    __table_args__ = (
        UniqueConstraint("plugin_version_id", "item_key", name="uq_plugin_version_tool_item_key"),
    )

    def to_dict(self) -> dict:
        params = self.parameters if isinstance(self.parameters, dict) else {}
        base = {
            "id": self.id,
            "item_key": self.item_key,
            "tool_code": self.tool_code,
            "type": self.type,
            "config_target": self.config_target,
            "name": self.name,
            "description": self.description or "",
            "selected_resource_id": self.selected_resource_id,
            "selected_resource_code": self.selected_resource_code,
            "selected_resource_name": self.selected_resource_name,
            "selected_resource_status": self.selected_resource_status,
            "distribution_mode": self.distribution_mode,
            "permissions_mode": self.permissions_mode,
            "parameters": params,
        }
        # Fusion des paramètres plats pour compatibilité immédiate avec les clients existants
        for k, v in params.items():
            if k not in base:
                base[k] = v
        return base

    def __repr__(self) -> str:
        return f"<PluginVersionTool id={self.id} version_id={self.plugin_version_id} item_key={self.item_key} tool_code={self.tool_code}>"


