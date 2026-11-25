from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from pydantic import BaseModel, Field, computed_field, ConfigDict, field_validator


# =====================================================================
# SCHÉMAS DE VERSION DE CAMPAGNE (CAMPAIGN VERSION)
# =====================================================================

class CampaignVersionBase(BaseModel):
    version_number: str = Field(..., description="Numéro sémantique de version (ex: '1.0.0', '1.1.0')")
    status: str = Field(default="draft", description="Statut de la version: draft, open, active, paused, closed, archived")
    description: Optional[str] = Field(None, description="Directives et description opérationnelle en Markdown enrichi")
    objectives: Optional[str] = Field(None, description="Objectifs stratégiques")
    targets: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Périodicité, cibles chiffrées, timeline et jalons")
    start_date: Optional[datetime] = Field(None, description="Date de début de cette version")
    end_date: Optional[datetime] = Field(None, description="Date de fin de cette version")
    budget: Optional[float] = Field(None, description="Budget alloué à cette version")
    currency: str = Field(default="XOF", description="Devise du budget")
    target_forms: List[Any] = Field(default_factory=list, description="Formulaires rattachés à cette version")
    target_projects: List[Any] = Field(default_factory=list, description="Projets types rattachés à cette version")
    settings: Dict[str, Any] = Field(default_factory=dict, description="Paramètres spécifiques à cette itération")
    template_governance: Dict[str, Any] = Field(default_factory=dict, description="Règles de gouvernance")
    changelog: Optional[str] = Field(None, description="Notes d'évolution de la version")

    @field_validator("target_forms", "target_projects", mode="before")
    @classmethod
    def ensure_list_version(cls, v):
        return v if v is not None else []

    @field_validator("targets", "settings", "template_governance", mode="before")
    @classmethod
    def ensure_dict_version(cls, v):
        return v if v is not None else {}


class CampaignVersionCreate(BaseModel):
    version_number: str = Field(..., description="Numéro de version (ex: '1.1.0')")
    status: Optional[str] = Field("draft", description="Statut initial")
    description: Optional[str] = None
    objectives: Optional[str] = None
    targets: Optional[Dict[str, Any]] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    budget: Optional[float] = None
    currency: Optional[str] = "XOF"
    target_forms: Optional[List[Any]] = None
    target_projects: Optional[List[Any]] = None
    settings: Optional[Dict[str, Any]] = None
    template_governance: Optional[Dict[str, Any]] = None
    changelog: Optional[str] = None


class CampaignVersionUpdate(BaseModel):
    status: Optional[str] = None
    description: Optional[str] = None
    objectives: Optional[str] = None
    targets: Optional[Dict[str, Any]] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    budget: Optional[float] = None
    currency: Optional[str] = None
    target_forms: Optional[List[Any]] = None
    target_projects: Optional[List[Any]] = None
    settings: Optional[Dict[str, Any]] = None
    template_governance: Optional[Dict[str, Any]] = None
    changelog: Optional[str] = None


class CampaignVersionCloneRequest(BaseModel):
    source_version_id: Optional[int] = Field(None, description="ID de la version source à dupliquer (par défaut la version courante)")
    new_version_number: str = Field(..., description="Nouveau numéro de version incrémentale (ex: '1.1.0')")
    changelog: Optional[str] = Field(None, description="Notes de mise à jour pour cette nouvelle version")
    copy_scope: Optional[bool] = Field(False, description="Conserver les projets et formulaires de la version source")


class CampaignVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    campaign_id: int
    version_number: str
    status: str
    description: Optional[str] = None
    objectives: Optional[str] = None
    targets: Dict[str, Any] = Field(default_factory=dict)
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    budget: Optional[float] = None
    currency: str = "XOF"
    target_forms: List[Any] = Field(default_factory=list)
    target_projects: List[Any] = Field(default_factory=list)
    settings: Dict[str, Any] = Field(default_factory=dict)
    template_governance: Dict[str, Any] = Field(default_factory=dict)
    changelog: Optional[str] = None
    metrics: Dict[str, Any] = Field(default_factory=dict)
    created_by_id: Optional[int] = None
    closed_at: Optional[datetime] = None
    closed_by_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    @field_validator("target_forms", "target_projects", mode="before")
    @classmethod
    def ensure_list(cls, v):
        return v if v is not None else []

    @field_validator("targets", "settings", "template_governance", "metrics", mode="before")
    @classmethod
    def ensure_dict(cls, v):
        return v if v is not None else {}

    @computed_field
    @property
    def versionNumber(self) -> str:
        return self.version_number

    @computed_field
    @property
    def templateGovernance(self) -> Dict[str, Any]:
        return self.template_governance or {}

    @computed_field
    @property
    def campaignId(self) -> int:
        return self.campaign_id

    @computed_field
    @property
    def startDate(self) -> Optional[datetime]:
        return self.start_date

    @computed_field
    @property
    def endDate(self) -> Optional[datetime]:
        return self.end_date

    @computed_field
    @property
    def targetForms(self) -> List[Any]:
        return self.target_forms or []

    @computed_field
    @property
    def targetProjects(self) -> List[Any]:
        return self.target_projects or []

    @computed_field
    @property
    def createdById(self) -> Optional[int]:
        return self.created_by_id

    @computed_field
    @property
    def closedAt(self) -> Optional[datetime]:
        return self.closed_at

    @computed_field
    @property
    def closedById(self) -> Optional[int]:
        return self.closed_by_id

    @computed_field
    @property
    def createdAt(self) -> datetime:
        return self.created_at

    @computed_field
    @property
    def updatedAt(self) -> datetime:
        return self.updated_at


# =====================================================================
# SCHÉMAS DE LA CAMPAGNE MAÎTRESSE (CAMPAIGN)
# =====================================================================

class CampaignBase(BaseModel):
    name: str = Field(..., min_length=2, max_length=255, description="Nom officiel de la campagne")
    code: str = Field(..., min_length=2, max_length=100, description="Slug identifiant unique globalement")
    description: Optional[str] = Field(None, description="Description générale et directives en Markdown")
    visibility: str = Field(default="public", description="Visibilité: public ou private")
    initial_version: Optional[str] = Field("1.0.0", description="Version sémantique initiale")
    start_date: Optional[datetime] = Field(None, description="Date de lancement de la version initiale")
    end_date: Optional[datetime] = Field(None, description="Date de fin prévisionnelle")
    settings: Dict[str, Any] = Field(default_factory=dict, description="Paramètres opérationnels")

    @field_validator("settings", mode="before")
    @classmethod
    def ensure_dict_base(cls, v):
        return v if v is not None else {}


class CampaignCreate(CampaignBase):
    pass


class CampaignUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=255)
    code: Optional[str] = Field(None, min_length=2, max_length=100)
    description: Optional[str] = None
    visibility: Optional[str] = None
    version: Optional[str] = None
    settings: Optional[Dict[str, Any]] = None


class CampaignStatusUpdate(BaseModel):
    status: str = Field(..., description="Nouveau statut : open, paused, closed, archived")
    reason: Optional[str] = Field(None, description="Motif de changement de statut")


class CampaignResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    code: str
    description: Optional[str] = None
    visibility: str = "public"
    status: str
    version: str = "1.0.0"

    settings: Dict[str, Any] = Field(default_factory=dict)
    created_by_id: Optional[int] = None
    closed_at: Optional[datetime] = None
    closed_by_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    versions: List[CampaignVersionResponse] = Field(default_factory=list)

    @field_validator("settings", mode="before")
    @classmethod
    def ensure_dict_response(cls, v):
        return v if v is not None else {}

    @computed_field
    @property
    def organizationId(self) -> Optional[int]:
        return None

    @computed_field
    @property
    def pluginId(self) -> Optional[int]:
        return None

    @computed_field
    @property
    def pluginCode(self) -> Optional[str]:
        return None

    @computed_field
    @property
    def isShared(self) -> bool:
        return False

    @computed_field
    @property
    def isImmutable(self) -> bool:
        return False

    @computed_field
    @property
    def immutableFields(self) -> List[str]:
        return []

    @computed_field
    @property
    def parentTemplateId(self) -> Optional[int]:
        return None

    @computed_field
    @property
    def campaignType(self) -> str:
        return "campaign"

    @computed_field
    @property
    def createdById(self) -> Optional[int]:
        return self.created_by_id

    @computed_field
    @property
    def closedAt(self) -> Optional[datetime]:
        return self.closed_at

    @computed_field
    @property
    def closedById(self) -> Optional[int]:
        return self.closed_by_id

    @computed_field
    @property
    def createdAt(self) -> datetime:
        return self.created_at

    @computed_field
    @property
    def updatedAt(self) -> datetime:
        return self.updated_at

    @computed_field
    @property
    def startDate(self) -> Optional[datetime]:
        if self.versions:
            active_v = next(
                (v for v in self.versions if v.status in ["open", "active"]),
                self.versions[0]
            )
            return active_v.start_date
        return None

    @computed_field
    @property
    def endDate(self) -> Optional[datetime]:
        if self.versions:
            active_v = next(
                (v for v in self.versions if v.status in ["open", "active"]),
                self.versions[0]
            )
            return active_v.end_date
        return None

    @computed_field
    @property
    def daysRemaining(self) -> Optional[int]:
        end_d = self.endDate
        if not end_d:
            return None
        now = datetime.now(timezone.utc)
        end = end_d if end_d.tzinfo else end_d.replace(tzinfo=timezone.utc)
        diff = (end - now).total_seconds()
        return max(0, int(diff // 86400))

    @computed_field
    @property
    def progressPercentage(self) -> int:
        if self.status == "closed" or self.status == "archived":
            return 100
        start_d = self.startDate
        end_d = self.endDate
        if start_d and end_d:
            now = datetime.now(timezone.utc)
            start = start_d if start_d.tzinfo else start_d.replace(tzinfo=timezone.utc)
            end = end_d if end_d.tzinfo else end_d.replace(tzinfo=timezone.utc)
            total = (end - start).total_seconds()
            if total > 0:
                elapsed = (now - start).total_seconds()
                pct = int((elapsed / total) * 100)
                return max(0, min(100, pct))
        return 0


class CampaignListResponse(BaseModel):
    items: List[CampaignResponse]
    total: int
    open_count: int
    draft_count: int
    closed_count: int


class CampaignMetricsResponse(BaseModel):
    campaign_id: int
    status: str
    days_elapsed: Optional[int]
    days_remaining: Optional[int]
    progress_percentage: int
    total_submissions: int = 0
    validated_submissions: int = 0
    target_achievement_rate: float = 0.0
