from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator
from app.models.plugin.plugin import PluginStatus, PluginVersionStatus, OrgPluginStatus


# ==========================================
# PLUGIN VERSION SCHEMAS
# ==========================================

# ==========================================
# PLUGIN VERSION TOOLS (MODULES & FEATURES)
# ==========================================

class PluginVersionToolBase(BaseModel):
    item_key: str = Field(..., description="Clé unique de l'élément dans la version (ex: projects_12, tool_projects)")
    tool_code: str = Field(..., description="Code du module ou outil (projects, forms, resources, campaigns)")
    type: str = Field(default="module", description="feature ou module")
    config_target: str = Field(default="tool_resource", description="tool_global ou tool_resource")
    name: str = Field(..., description="Nom d'affichage du composant")
    description: Optional[str] = Field(None, description="Description enrichie (Markdown)")
    selected_resource_id: Optional[int] = None
    selected_resource_code: Optional[str] = None
    selected_resource_name: Optional[str] = None
    selected_resource_status: Optional[str] = None
    distribution_mode: str = Field(default="tenant", description="tenant, universal, plugin_shared, preconfigured")
    permissions_mode: str = Field(default="restricted", description="restricted ou shared")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="Paramètres et options spécifiques du module")


class PluginVersionToolCreate(PluginVersionToolBase):
    pass


class PluginVersionToolUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    distribution_mode: Optional[str] = None
    permissions_mode: Optional[str] = None
    parameters: Optional[Dict[str, Any]] = None


class PluginVersionToolResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: Optional[int] = None
    plugin_version_id: Optional[int] = None
    item_key: Optional[str] = ""
    tool_code: Optional[str] = None
    code: Optional[str] = None
    type: Optional[str] = "module"
    config_target: Optional[str] = "tool_resource"
    name: Optional[str] = ""
    description: Optional[str] = None
    selected_resource_id: Optional[int] = None
    selected_resource_code: Optional[str] = None
    selected_resource_name: Optional[str] = None
    selected_resource_status: Optional[str] = None
    distribution_mode: Optional[str] = "tenant"
    permissions_mode: Optional[str] = "restricted"
    parameters: Optional[Dict[str, Any]] = Field(default_factory=dict)
    settings: Optional[Dict[str, Any]] = Field(default_factory=dict)
    declaration: Optional[Dict[str, Any]] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @model_validator(mode="before")
    @classmethod
    def harmonize_tool_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if not data.get("tool_code") and data.get("code"):
                data["tool_code"] = data["code"]
            if not data.get("code") and data.get("tool_code"):
                data["code"] = data["tool_code"]
            if not data.get("name") and data.get("title"):
                data["name"] = data["title"]
            if not data.get("item_key") and data.get("id"):
                data["item_key"] = str(data["id"])
            if not data.get("item_key") and data.get("tool_code"):
                data["item_key"] = f"tool_{data['tool_code']}"
        return data

    @computed_field
    @property
    def itemKey(self) -> str:
        return self.item_key or ""

    @computed_field
    @property
    def toolCode(self) -> str:
        return self.tool_code or self.code or ""

    @computed_field
    @property
    def configTarget(self) -> str:
        return self.config_target or "tool_resource"

    @computed_field
    @property
    def pluginVersionId(self) -> Optional[int]:
        return self.plugin_version_id

    @computed_field
    @property
    def selectedResourceId(self) -> Optional[int]:
        return self.selected_resource_id

    @computed_field
    @property
    def selectedResourceCode(self) -> Optional[str]:
        return self.selected_resource_code

    @computed_field
    @property
    def selectedResourceName(self) -> Optional[str]:
        return self.selected_resource_name

    @computed_field
    @property
    def selectedResourceStatus(self) -> Optional[str]:
        return self.selected_resource_status

    @computed_field
    @property
    def distributionMode(self) -> str:
        return self.distribution_mode or "tenant"

    @computed_field
    @property
    def permissionsMode(self) -> str:
        return self.permissions_mode or "restricted"


class PluginVersionBase(BaseModel):
    version_number: str = Field(..., description="Numéro de version SemVer, ex: 1.0.0")
    changelog: Optional[str] = Field(None, description="Notes de version / changelog Markdown")
    package_data: Optional[Dict[str, Any]] = Field(default=None, description="Projets, formulaires, télémétrie")
    usage_policy: Optional[str] = Field(default=None, description="Politique d'utilisation obligatoire (EULA)")
    source_version_id: Optional[int] = Field(None, description="ID de la version source à partir de laquelle hériter tous les modules et configurations")


class PluginVersionCreate(PluginVersionBase):
    pass



class PluginVersionUpdate(BaseModel):
    changelog: Optional[str] = None
    package_data: Optional[Dict[str, Any]] = None
    usage_policy: Optional[str] = None


class PluginVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: int
    plugin_id: int
    version_number: str
    changelog: Optional[str] = None
    package_data: Optional[Dict[str, Any]] = Field(default_factory=dict)
    usage_policy: Optional[str] = None
    status: str
    tools: List[PluginVersionToolResponse] = Field(default_factory=list)
    published_by_id: Optional[int] = None
    published_at: Optional[datetime] = None
    deprecated_at: Optional[datetime] = None
    deprecated_by_id: Optional[int] = None
    deprecated_eol_date: Optional[datetime] = None
    deprecation_reason: Optional[str] = None
    is_blocked: bool = False
    created_at: datetime
    updated_at: datetime

    @computed_field
    @property
    def pluginId(self) -> int:
        return self.plugin_id

    @computed_field
    @property
    def versionNumber(self) -> str:
        return self.version_number

    @computed_field
    @property
    def packageData(self) -> Dict[str, Any]:
        return self.package_data or {}

    @computed_field
    @property
    def usagePolicy(self) -> str:
        return self.usage_policy or ""

    @computed_field
    @property
    def publishedById(self) -> Optional[int]:
        return self.published_by_id

    @computed_field
    @property
    def publishedAt(self) -> Optional[datetime]:
        return self.published_at

    @computed_field
    @property
    def deprecatedAt(self) -> Optional[datetime]:
        return self.deprecated_at

    @computed_field
    @property
    def deprecatedById(self) -> Optional[int]:
        return self.deprecated_by_id

    @computed_field
    @property
    def deprecatedEolDate(self) -> Optional[datetime]:
        return self.deprecated_eol_date

    @computed_field
    @property
    def deprecationReason(self) -> Optional[str]:
        return self.deprecation_reason

    @computed_field
    @property
    def isBlocked(self) -> bool:
        if self.is_blocked:
            return True
        if self.deprecated_eol_date:
            now = datetime.now(timezone.utc)
            eol = self.deprecated_eol_date if self.deprecated_eol_date.tzinfo else self.deprecated_eol_date.replace(tzinfo=timezone.utc)
            return now >= eol
        return False

    @computed_field
    @property
    def daysUntilEol(self) -> Optional[int]:
        if not self.deprecated_eol_date:
            return None
        now = datetime.now(timezone.utc)
        eol = self.deprecated_eol_date if self.deprecated_eol_date.tzinfo else self.deprecated_eol_date.replace(tzinfo=timezone.utc)
        diff = (eol - now).total_seconds()
        return max(0, int(diff // 86400))

    @computed_field
    @property
    def createdAt(self) -> datetime:
        return self.created_at

    @computed_field
    @property
    def updatedAt(self) -> datetime:
        return self.updated_at


class PluginVersionPublish(BaseModel):
    details_or_changelog: str = Field(
        ...,
        min_length=10,
        description="Détail exhaustif du contenu embarqué pour la première version, ou nouveautés/changements pour les suivantes"
    )
    usage_policy: Optional[str] = Field(None, description="Mise à jour éventuelle de la politique d'utilisation obligatoire")


class PluginVersionDeprecate(BaseModel):
    days_until_eol: int = Field(default=30, ge=1, le=365, description="Nombre de jours avant blocage et fin de vie définitive")
    reason: str = Field(
        ...,
        min_length=10,
        description="Motif de dépréciation et instructions pour exporter et migrer les données"
    )
    custom_eol_date: Optional[datetime] = None


# ==========================================
# PLUGIN SCHEMAS
# ==========================================

class PluginBase(BaseModel):
    name: str = Field(..., min_length=2, max_length=200)
    code: str = Field(..., min_length=2, max_length=100, description="Identifiant unique slug, ex: cocoa-eudr")
    category: str = Field(..., min_length=2, max_length=100)
    description: Optional[str] = None
    short_description: Optional[str] = None
    icon_url: Optional[str] = None
    banner_url: Optional[str] = None
    author: str = Field(default="SenWiseTool")
    tags: List[str] = Field(default_factory=list)
    is_public: bool = Field(default=True, description="True si public à tous, False si restreint à des organisations")
    allowed_organization_ids: List[int] = Field(default_factory=list, description="IDs des organisations autorisées si privé")


class PluginCreate(PluginBase):
    # Optionnel à la création : première version directement incluse
    initial_version_number: Optional[str] = "1.0.0"
    initial_usage_policy: Optional[str] = None
    initial_package_data: Optional[Dict[str, Any]] = None
    initial_changelog: Optional[str] = "Version initiale de démarrage"


class PluginUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    description: Optional[str] = None
    short_description: Optional[str] = None
    icon_url: Optional[str] = None
    banner_url: Optional[str] = None
    author: Optional[str] = None
    tags: Optional[List[str]] = None
    status: Optional[str] = None
    is_public: Optional[bool] = None
    allowed_organization_ids: Optional[List[int]] = None


class PluginResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: int
    code: str
    name: str
    category: str
    description: Optional[str] = None
    short_description: Optional[str] = None
    icon_url: Optional[str] = None
    banner_url: Optional[str] = None
    author: str
    status: str
    tags: List[str] = Field(default_factory=list)
    is_public: bool = True
    allowed_organization_ids: List[int] = Field(default_factory=list)
    allowed_organizations: Optional[List[Dict[str, Any]]] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    versions: List[PluginVersionResponse] = Field(default_factory=list)
    latest_version: Optional[PluginVersionResponse] = None
    has_draft_version: bool = False
    draft_version: Optional[PluginVersionResponse] = None
    has_ongoing_deprecation: bool = False
    ongoing_deprecation_version: Optional[PluginVersionResponse] = None
    oldest_published_version_id: Optional[int] = None
    versions_count: int = 0
    installations_count: int = 0

    @computed_field
    @property
    def isPublic(self) -> bool:
        return self.is_public

    @computed_field
    @property
    def allowedOrganizationIds(self) -> List[int]:
        return self.allowed_organization_ids or []

    @computed_field
    @property
    def allowedOrganizations(self) -> List[Dict[str, Any]]:
        return self.allowed_organizations or []

    @computed_field
    @property
    def hasDraftVersion(self) -> bool:
        return self.has_draft_version

    @computed_field
    @property
    def draftVersion(self) -> Optional[PluginVersionResponse]:
        return self.draft_version

    @computed_field
    @property
    def hasOngoingDeprecation(self) -> bool:
        return self.has_ongoing_deprecation

    @computed_field
    @property
    def ongoingDeprecationVersion(self) -> Optional[PluginVersionResponse]:
        return self.ongoing_deprecation_version

    @computed_field
    @property
    def oldestPublishedVersionId(self) -> Optional[int]:
        return self.oldest_published_version_id

    @computed_field
    @property
    def shortDescription(self) -> Optional[str]:
        return self.short_description


    @computed_field
    @property
    def iconUrl(self) -> Optional[str]:
        return self.icon_url

    @computed_field
    @property
    def bannerUrl(self) -> Optional[str]:
        return self.banner_url

    @computed_field
    @property
    def latestVersion(self) -> Optional[PluginVersionResponse]:
        return self.latest_version

    @computed_field
    @property
    def versionsCount(self) -> int:
        return self.versions_count

    @computed_field
    @property
    def installationsCount(self) -> int:
        return self.installations_count

    @computed_field
    @property
    def createdAt(self) -> datetime:
        return self.created_at

    @computed_field
    @property
    def updatedAt(self) -> datetime:
        return self.updated_at


class PluginListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    items: List[PluginResponse]
    total: int


# ==========================================
# ORGANIZATION PLUGIN (INSTALLATION) SCHEMAS
# ==========================================

class PluginInstallRequest(BaseModel):
    plugin_id: int
    version_id: Optional[int] = Field(None, description="ID de la version souhaitée (ou la dernière publiée si omis)")
    policy_accepted: bool = Field(..., description="Consentement explicite obligatoire à la politique d'utilisation")
    local_settings: Optional[Dict[str, Any]] = Field(default_factory=dict)


class PluginUpgradeRequest(BaseModel):
    plugin_id: int
    target_version_id: int
    policy_accepted: bool = Field(..., description="Consentement à la politique de la nouvelle version")


class PluginUninstallRequest(BaseModel):
    plugin_id: int
    reason: Optional[str] = None


class PluginVersionSwitchRequest(BaseModel):
    plugin_id: int
    version_id: int


class OrganizationPluginDownloadedVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: int
    organization_plugin_id: int
    plugin_id: int
    plugin_version_id: int
    version_number: str
    status: str
    changelog: Optional[str] = None
    parameters: Optional[Dict[str, Any]] = None
    created_at: Optional[datetime] = None


class OrganizationPluginResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: int
    organization_id: int
    plugin_id: int
    installed_version_id: int
    code: Optional[str] = None
    name: Optional[str] = None
    category: Optional[str] = None
    description: Optional[str] = None
    short_description: Optional[str] = None
    icon_url: Optional[str] = None
    banner_url: Optional[str] = None
    author: Optional[str] = "SenWiseTool"
    status: str
    policy_accepted_at: datetime
    policy_accepted_by_user_id: Optional[int] = None
    policy_version: str
    local_settings: Optional[Dict[str, Any]] = None
    installed_at: datetime
    updated_at: datetime

    plugin: Optional[PluginResponse] = None
    installed_version: Optional[PluginVersionResponse] = None
    latest_version: Optional[PluginVersionResponse] = None
    downloaded_versions: List[OrganizationPluginDownloadedVersionResponse] = Field(default_factory=list)
    has_update_available: bool = False

    @computed_field
    @property
    def downloadedVersions(self) -> List[OrganizationPluginDownloadedVersionResponse]:
        return self.downloaded_versions

    @computed_field
    @property
    def organizationId(self) -> int:
        return self.organization_id

    @computed_field
    @property
    def pluginId(self) -> int:
        return self.plugin_id

    @computed_field
    @property
    def installedVersionId(self) -> int:
        return self.installed_version_id

    @computed_field
    @property
    def policyAcceptedAt(self) -> datetime:
        return self.policy_accepted_at

    @computed_field
    @property
    def policyAcceptedByUserId(self) -> Optional[int]:
        return self.policy_accepted_by_user_id

    @computed_field
    @property
    def policyVersion(self) -> str:
        return self.policy_version

    @computed_field
    @property
    def localSettings(self) -> Optional[Dict[str, Any]]:
        return self.local_settings

    @computed_field
    @property
    def installedVersion(self) -> Optional[PluginVersionResponse]:
        return self.installed_version

    @computed_field
    @property
    def latestVersion(self) -> Optional[PluginVersionResponse]:
        return self.latest_version

    @computed_field
    @property
    def hasUpdateAvailable(self) -> bool:
        return self.has_update_available

    @computed_field
    @property
    def installedAt(self) -> datetime:
        return self.installed_at

    @computed_field
    @property
    def updatedAt(self) -> datetime:
        return self.updated_at


# ==========================================
# MARKETPLACE SCHEMAS
# ==========================================

class MarketplacePluginItem(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    plugin: PluginResponse
    latest_version: PluginVersionResponse
    is_installed: bool = False
    installed_instance: Optional[OrganizationPluginResponse] = None
    has_update_available: bool = False

    @computed_field
    @property
    def latestVersion(self) -> PluginVersionResponse:
        return self.latest_version

    @computed_field
    @property
    def isInstalled(self) -> bool:
        return self.is_installed

    @computed_field
    @property
    def installedInstance(self) -> Optional[OrganizationPluginResponse]:
        return self.installed_instance

    @computed_field
    @property
    def hasUpdateAvailable(self) -> bool:
        return self.has_update_available


class MarketplaceCatalogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    items: List[MarketplacePluginItem]
    categories: List[str]
    total: int


class PluginInstallModuleRequest(BaseModel):
    tool_code: Optional[str] = None
    item_key: Optional[str] = None
    ref_table: Optional[str] = None
    ref_id: Optional[int] = None


# ==========================================
# DOWNLOADED ELEMENTS SCHEMAS (TENANT WORKSPACE)
# ==========================================

class OrganizationPluginVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: int
    organization_id: int
    organization_plugin_id: int
    plugin_id: int
    plugin_version_id: int
    version_number: str
    status: str
    changelog: Optional[str] = None
    usage_policy: Optional[str] = None
    policy_accepted_at: datetime
    policy_version: str
    package_snapshot: Optional[Dict[str, Any]] = None
    parameters: Optional[Dict[str, Any]] = None
    installed_at: datetime
    updated_at: datetime


class OrganizationProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: int
    organization_id: int
    original_project_id: Optional[int] = None
    organization_plugin_id: Optional[int] = None
    name: str
    code: str
    project_type: str = "audit"
    category: str = "general"
    visibility: str = "private"
    authorized_organization_ids: List[int] = Field(default_factory=list)
    cover_image_url: Optional[str] = None
    parameters: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class OrganizationFeatureResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: int
    organization_id: int
    name: str
    feature_code: str
    description: Optional[str] = None
    parameters: Dict[str, Any] = Field(default_factory=dict)
    permissions: Dict[str, Any] = Field(default_factory=dict)
    status: str = "active"
    organization_plugin_id: Optional[int] = None
    organization_plugin_version_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime


class OrganizationProjectModuleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: int
    organization_id: int
    organization_plugin_id: int = 0
    organization_plugin_version_id: int = 0
    original_plugin_version_tool_id: Optional[int] = None
    original_project_id: Optional[int] = None
    original_project_version_id: Optional[int] = None
    cloned_project_id: Optional[int] = None
    item_key: str = ""
    name: str
    code: str
    description: Optional[str] = None
    version_number: Optional[str] = None
    distribution_mode: str = "tenant"
    permissions_mode: str = "restricted"
    parameters: Dict[str, Any] = Field(default_factory=dict)
    status: str = "active"
    created_at: datetime
    updated_at: datetime


class OrganizationProjectModuleUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    parameters: Optional[Dict[str, Any]] = None
    status: Optional[str] = None


class OrganizationProjectFeatureResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: int
    organization_id: int
    organization_plugin_id: int
    organization_plugin_version_id: int
    original_plugin_version_tool_id: Optional[int] = None
    item_key: str
    feature_code: str = "projects"
    name: str
    description: Optional[str] = None
    distribution_mode: str = "tenant"
    permissions_mode: str = "restricted"
    parameters: Dict[str, Any] = Field(default_factory=dict)
    status: str = "active"
    created_at: datetime
    updated_at: datetime


class OrganizationProjectFeatureUpdate(BaseModel):
    parameters: Optional[Dict[str, Any]] = None
    status: Optional[str] = None
