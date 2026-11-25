from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field, field_validator, ConfigDict


# =====================================================================
# SCHÉMAS DE VERSION DE PROJET
# =====================================================================

class ProjectVersionBase(BaseModel):
    version_number: str = Field(..., min_length=1, max_length=50, description="Numéro de version sémantique (ex: 1.0.0)")
    status: str = Field(default="draft", description="Statut (draft, published, archived)")
    description: Optional[str] = Field(None, description="Présentation enrichie du projet en Markdown")
    changelog: Optional[str] = Field(None, description="Notes de version / changelog")
    settings: Dict[str, Any] = Field(default_factory=dict, description="Paramètres et options opérationnelles")
    template_governance: Dict[str, Any] = Field(default_factory=dict, description="Gouvernance et règles de sanctuarisation")

    @field_validator("settings", "template_governance", mode="before")
    @classmethod
    def ensure_dict(cls, v):
        return v if v is not None else {}


class ProjectVersionCreate(ProjectVersionBase):
    pass


class ProjectVersionCloneRequest(BaseModel):
    source_version_number: Optional[str] = Field(None, description="Version source à cloner. Si non fournie, clone la plus récente.")
    new_version_number: str = Field(..., min_length=1, max_length=50, description="Nouveau numéro de version (ex: 1.1.0)")
    changelog: Optional[str] = Field(None, description="Description des évolutions / notes de version")


class ProjectVersionUpdate(BaseModel):
    status: Optional[str] = None
    description: Optional[str] = None
    changelog: Optional[str] = None
    settings: Optional[Dict[str, Any]] = None
    template_governance: Optional[Dict[str, Any]] = None


class ProjectVersionResponse(ProjectVersionBase):
    id: int
    project_id: int
    metrics: Dict[str, Any] = Field(default_factory=dict)
    created_by_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# =====================================================================
# SCHÉMAS DE PROJET MAÎTRE
# =====================================================================

class ProjectBase(BaseModel):
    name: str = Field(..., min_length=2, max_length=255, description="Nom officiel du projet")
    code: str = Field(..., min_length=2, max_length=100, description="Code unique du projet")
    project_type: str = Field(
        default="audit",
        description="Type de projet (audit, certification, survey, traceability, monitoring, custom)"
    )
    category: str = Field(default="general", description="Catégorie métier")
    visibility: str = Field(default="public", description="Visibilité: 'public' ou 'private'")
    authorized_organization_ids: List[Any] = Field(
        default_factory=list,
        description="IDs des organisations autorisées en cas de visibilité privée"
    )
    cover_image_url: Optional[str] = Field(None, description="URL de l'image de couverture")

    @field_validator("authorized_organization_ids", mode="before")
    @classmethod
    def ensure_list(cls, v):
        return v if v is not None else []


class ProjectCreate(ProjectBase):
    # Champs optionnels de la version initiale (créée automatiquement avec le projet)
    version: Optional[str] = Field(default="1.0.0", description="Version initiale (défaut: 1.0.0)")
    description: Optional[str] = Field(None, description="Description / présentation initiale")
    status: Optional[str] = Field(default="draft", description="Statut de la version initiale")
    options: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Paramètres initiaux")
    template_governance: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Gouvernance initiale")


class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=255)
    category: Optional[str] = None
    visibility: Optional[str] = None
    authorized_organization_ids: Optional[List[Any]] = None
    cover_image_url: Optional[str] = None
    project_type: Optional[str] = None
    # Rétrocompatibilité : si l'utilisateur met à jour description/status via le projet, on met à jour la version courante
    description: Optional[str] = None
    status: Optional[str] = None
    options: Optional[Dict[str, Any]] = None
    template_governance: Optional[Dict[str, Any]] = None


class ProjectResponse(ProjectBase):
    id: int
    created_by_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    # Propriétés de la version la plus récente (pour compatibilité et affichage direct)
    version: Optional[str] = None
    status: Optional[str] = "draft"
    description: Optional[str] = None
    metrics: Dict[str, Any] = Field(default_factory=dict)
    settings: Dict[str, Any] = Field(default_factory=dict)
    template_governance: Dict[str, Any] = Field(default_factory=dict)
    versions: List[ProjectVersionResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)

