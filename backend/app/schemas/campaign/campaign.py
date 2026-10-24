from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from pydantic import BaseModel, Field, computed_field, ConfigDict, field_validator


class CampaignBase(BaseModel):
    name: str = Field(..., min_length=2, max_length=255, description="Nom officiel de la campagne")
    code: str = Field(..., min_length=2, max_length=100, description="Slug identifiant unique au sein de l'organisation")
    campaign_type: str = Field(default="harvest", description="Type de campagne (harvest, audit, survey, inspection, custom)")
    description: Optional[str] = Field(None, description="Description générale et contexte opérationnel")
    objectives: Optional[str] = Field(None, description="Objectifs qualitatifs ou stratégiques")
    targets: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Cibles chiffrées (producteurs, parcelles, volumes, etc.)")
    start_date: Optional[datetime] = Field(None, description="Date de lancement de la campagne")
    end_date: Optional[datetime] = Field(None, description="Date de fin prévisionnelle")
    budget: Optional[float] = Field(None, description="Budget alloué à la campagne")
    currency: str = Field(default="XOF", description="Devise du budget (XOF, EUR, USD)")
    target_forms: List[Any] = Field(default_factory=list, description="Formulaires rattachés à cette campagne")
    target_projects: List[Any] = Field(default_factory=list, description="Projets types rattachés à cette campagne")
    settings: Dict[str, Any] = Field(default_factory=dict, description="Surcharges de comportement et paramètres du plugin")
    plugin_id: Optional[int] = Field(None, description="ID du plugin dont est issue cette campagne")
    plugin_code: Optional[str] = Field(None, description="Code slug du plugin associé")
    scope: str = Field(default="tenant", description="Portée de l'outil: tenant, plugin_shared, preconfigured")
    is_shared: bool = Field(default=False, description="Indique si la campagne est partagée entre toutes les organisations du plugin")
    is_immutable: bool = Field(default=False, description="Indique si la configuration de base de la campagne est immuable / verrouillée")
    immutable_fields: List[str] = Field(default_factory=list, description="Liste des champs verrouillés contre toute modification")
    parent_template_id: Optional[int] = Field(None, description="ID du template modèle ou de la campagne partagée d'origine")

    @field_validator("immutable_fields", "target_forms", "target_projects", mode="before")
    @classmethod
    def ensure_list_base(cls, v):
        return v if v is not None else []

    @field_validator("targets", "settings", mode="before")
    @classmethod
    def ensure_dict_base(cls, v):
        return v if v is not None else {}


class CampaignCreate(CampaignBase):
    pass


class CampaignUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=255)
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
    scope: Optional[str] = None
    is_shared: Optional[bool] = None
    is_immutable: Optional[bool] = None
    immutable_fields: Optional[List[str]] = None


class CampaignStatusUpdate(BaseModel):
    status: str = Field(..., description="Nouveau statut : open, paused, closed, archived")
    reason: Optional[str] = Field(None, description="Motif de changement de statut")


class CampaignResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    organization_id: Optional[int] = None
    plugin_id: Optional[int] = None
    plugin_code: Optional[str] = None
    scope: str = "tenant"
    is_shared: bool = False
    is_immutable: bool = False
    immutable_fields: List[str] = Field(default_factory=list)
    parent_template_id: Optional[int] = None
    name: str
    code: str
    campaign_type: str
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
    metrics: Dict[str, Any] = Field(default_factory=dict)
    created_by_id: Optional[int] = None
    closed_at: Optional[datetime] = None
    closed_by_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    @field_validator("immutable_fields", "target_forms", "target_projects", mode="before")
    @classmethod
    def ensure_list_response(cls, v):
        return v if v is not None else []

    @field_validator("targets", "settings", "metrics", mode="before")
    @classmethod
    def ensure_dict_response(cls, v):
        return v if v is not None else {}

    @computed_field
    @property
    def organizationId(self) -> Optional[int]:
        return self.organization_id

    @computed_field
    @property
    def isShared(self) -> bool:
        return self.is_shared

    @computed_field
    @property
    def isImmutable(self) -> bool:
        return self.is_immutable

    @computed_field
    @property
    def immutableFields(self) -> List[str]:
        return self.immutable_fields or []

    @computed_field
    @property
    def parentTemplateId(self) -> Optional[int]:
        return self.parent_template_id

    @computed_field
    @property
    def pluginId(self) -> Optional[int]:
        return self.plugin_id

    @computed_field
    @property
    def pluginCode(self) -> Optional[str]:
        return self.plugin_code

    @computed_field
    @property
    def campaignType(self) -> str:
        return self.campaign_type

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

    @computed_field
    @property
    def daysRemaining(self) -> Optional[int]:
        if not self.end_date:
            return None
        now = datetime.now(timezone.utc)
        end = self.end_date if self.end_date.tzinfo else self.end_date.replace(tzinfo=timezone.utc)
        diff = (end - now).total_seconds()
        return max(0, int(diff // 86400))

    @computed_field
    @property
    def progressPercentage(self) -> int:
        if self.metrics and "progress_percentage" in self.metrics:
            return int(self.metrics["progress_percentage"])
        if self.status == "closed" or self.status == "archived":
            return 100
        if self.start_date and self.end_date:
            now = datetime.now(timezone.utc)
            start = self.start_date if self.start_date.tzinfo else self.start_date.replace(tzinfo=timezone.utc)
            end = self.end_date if self.end_date.tzinfo else self.end_date.replace(tzinfo=timezone.utc)
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
