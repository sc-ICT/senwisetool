from typing import Optional, Dict, Any, List
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict


class FormVersionBase(BaseModel):
    version_number: str = Field(..., min_length=1, max_length=50, description="Numéro de version sémantique (ex: 1.0.0)")
    description: Optional[str] = Field(None, description="Notes de version / changelog")
    form_type: str = Field(default="standard", description="standard, audit, survey, inspection, checklist, onboarding, custom")
    settings: Dict[str, Any] = Field(default_factory=dict)
    template_governance: Dict[str, Any] = Field(default_factory=dict)


class FormVersionCreate(FormVersionBase):
    pass


class FormVersionCloneRequest(BaseModel):
    source_version_id: int = Field(..., description="ID de la version à cloner")
    new_version_number: str = Field(..., min_length=1, max_length=50, description="Nouveau numéro de version")
    description: Optional[str] = Field(None, description="Notes sur la nouvelle version")


class FormVersionUpdate(BaseModel):
    description: Optional[str] = None
    form_type: Optional[str] = None
    settings: Optional[Dict[str, Any]] = None
    template_governance: Optional[Dict[str, Any]] = None


class FormVersionResponse(FormVersionBase):
    id: int
    form_id: int
    status: str
    created_by_id: Optional[int] = None
    published_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    sections_count: Optional[int] = 0
    questions_count: Optional[int] = 0

    model_config = ConfigDict(from_attributes=True)
