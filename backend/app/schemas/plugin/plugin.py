from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from pydantic import BaseModel, ConfigDict, Field, computed_field
from app.models.plugin.plugin import PluginStatus, PluginVersionStatus, OrgPluginStatus


# ==========================================
# PLUGIN VERSION SCHEMAS
# ==========================================

class PluginVersionBase(BaseModel):
    version_number: str = Field(..., description="Numéro de version SemVer, ex: 1.0.0")
    changelog: Optional[str] = Field(None, description="Notes de version / changelog Markdown")
    package_data: Dict[str, Any] = Field(default_factory=dict, description="Projets, formulaires, télémétrie")
    usage_policy: str = Field(..., description="Politique d'utilisation obligatoire (EULA)")


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
    package_data: Dict[str, Any] = Field(default_factory=dict)
    usage_policy: str
    status: str
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
        return self.package_data

    @computed_field
    @property
    def usagePolicy(self) -> str:
        return self.usage_policy

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


class OrganizationPluginResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: int
    organization_id: int
    plugin_id: int
    installed_version_id: int
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
    has_update_available: bool = False

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
