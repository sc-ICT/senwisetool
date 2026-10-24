from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field, field_validator, ConfigDict
from app.schemas.project.form_question import FormQuestionResponse, FormQuestionCreate


class FormSectionBase(BaseModel):
    title: str = Field(..., min_length=1, max_length=2000, description="Titre de la section ou sous-section")
    code: str = Field(..., min_length=1, max_length=100, description="Code unique de la section")
    description: Optional[str] = None
    order_index: int = Field(default=0)
    is_repeatable: bool = Field(default=False)
    min_repeats: int = Field(default=1)
    max_repeats: Optional[int] = None
    weight: float = Field(default=1.0, description="Pondération de la section dans le formulaire")
    scoring_category: Optional[str] = None
    is_critical: bool = Field(default=False)
    is_sanctuarized: bool = Field(default=False, description="Section modèle sanctuarisée non supprimable par l'organisation")
    allow_custom_questions: bool = Field(default=True, description="L'organisation peut-elle ajouter des questions dans cette section ?")
    min_passing_score: Optional[float] = None
    dependency_rules: Dict[str, Any] = Field(default_factory=dict)
    settings: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("dependency_rules", "settings", mode="before")
    @classmethod
    def ensure_dict(cls, v):
        return v if v is not None else {}


class FormSectionCreate(FormSectionBase):
    form_id: Optional[int] = None
    parent_section_id: Optional[int] = None
    questions: List[FormQuestionCreate] = Field(default_factory=list)
    subsections: List["FormSectionCreate"] = Field(default_factory=list)


class FormSectionUpdate(BaseModel):
    title: Optional[str] = None
    code: Optional[str] = None
    description: Optional[str] = None
    order_index: Optional[int] = None
    is_repeatable: Optional[bool] = None
    min_repeats: Optional[int] = None
    max_repeats: Optional[int] = None
    weight: Optional[float] = None
    scoring_category: Optional[str] = None
    is_critical: Optional[bool] = None
    is_sanctuarized: Optional[bool] = None
    allow_custom_questions: Optional[bool] = None
    min_passing_score: Optional[float] = None
    parent_section_id: Optional[int] = None
    dependency_rules: Optional[Dict[str, Any]] = None
    settings: Optional[Dict[str, Any]] = None


class FormSectionResponse(FormSectionBase):
    id: int
    form_id: int
    parent_section_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime
    questions: List[FormQuestionResponse] = Field(default_factory=list)
    subsections: List["FormSectionResponse"] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


# Rebuild self-referencing models for Pydantic v2
FormSectionCreate.model_rebuild()
FormSectionResponse.model_rebuild()
