from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field, field_validator, ConfigDict


class ProjectBase(BaseModel):
    name: str = Field(..., min_length=2, max_length=255, description="Nom officiel du projet")
    code: str = Field(..., min_length=2, max_length=100, description="Slug identifiant unique")
    category: str = Field(default="general", description="Catégorie (eudr_compliance, certification, survey, etc.)")
    project_type: str = Field(default="audit", description="Type de projet (audit, certification, survey, traceability, monitoring, custom)")
    icon: str = Field(default="FolderKanban", description="Icône Lucide")
    description: Optional[str] = None
    objectives: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    budget: Optional[float] = None
    currency: str = Field(default="XOF")
    targets: Dict[str, Any] = Field(default_factory=dict)
    settings: Dict[str, Any] = Field(default_factory=dict)

    is_template: bool = Field(default=True, description="True si ce projet est un modèle/blueprint destiné aux plugins")
    version: str = Field(default="1.0.0", description="Version sémantique du modèle (ex: 1.0.0)")
    status: Optional[str] = Field(default="draft", description="Statut (draft, published, active, etc.)")
    template_governance: Dict[str, Any] = Field(default_factory=dict, description="Exigences, sanctuarisations et permissions concédées aux organisations")
    plugin_id: Optional[int] = None
    plugin_code: Optional[str] = None
    scope: str = Field(default="tenant")
    is_shared: bool = Field(default=False)
    is_immutable: bool = Field(default=False)
    immutable_fields: List[str] = Field(default_factory=list)
    parent_template_id: Optional[int] = None

    @field_validator("immutable_fields", mode="before")
    @classmethod
    def ensure_list(cls, v):
        return v if v is not None else []

    @field_validator("targets", "settings", "template_governance", mode="before")
    @classmethod
    def ensure_dict(cls, v):
        return v if v is not None else {}


class ProjectCreate(ProjectBase):
    pass


class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=255)
    category: Optional[str] = None
    project_type: Optional[str] = None
    status: Optional[str] = None
    version: Optional[str] = None
    icon: Optional[str] = None
    description: Optional[str] = None
    objectives: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    budget: Optional[float] = None
    currency: Optional[str] = None
    targets: Optional[Dict[str, Any]] = None
    settings: Optional[Dict[str, Any]] = None
    is_template: Optional[bool] = None
    template_governance: Optional[Dict[str, Any]] = None
    scope: Optional[str] = None
    is_shared: Optional[bool] = None
    is_immutable: Optional[bool] = None
    immutable_fields: Optional[List[str]] = None


class ProjectResponse(ProjectBase):
    id: int
    organization_id: Optional[int] = None
    status: str
    metrics: Dict[str, Any] = Field(default_factory=dict)
    created_by_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
