from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field, field_validator, ConfigDict


class FormQuestionBase(BaseModel):
    code: str = Field(..., min_length=1, max_length=100, description="Code unique de la question dans le formulaire")
    label: str = Field(..., min_length=1, max_length=10000, description="Intitulé affiché de la question")
    help_text: Optional[str] = None
    placeholder: Optional[str] = None
    question_type: str = Field(default="text_short", description="Type de champ (text, number, choice, audit, gps, photo, resource_lookup, etc.)")
    order_index: int = Field(default=0)
    is_required: bool = Field(default=True)
    is_readonly: bool = Field(default=False)
    is_critical: bool = Field(default=False, description="Si True, une non-conformité constitue un blocage immédiat")
    is_sanctuarized: bool = Field(default=False, description="Question sanctuarisée par le modèle Super Admin (non supprimable)")
    weight: float = Field(default=1.0, description="Pondération dans le calcul du score")
    
    scoring_config: Dict[str, Any] = Field(default_factory=dict)
    options: List[Dict[str, Any]] = Field(default_factory=list)
    resource_binding: Dict[str, Any] = Field(default_factory=dict)
    formula_expression: Optional[str] = None
    validation_rules: Dict[str, Any] = Field(default_factory=dict)
    dependency_rules: Dict[str, Any] = Field(default_factory=dict)
    settings: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("options", mode="before")
    @classmethod
    def ensure_list(cls, v):
        return v if v is not None else []

    @field_validator("scoring_config", "resource_binding", "validation_rules", "dependency_rules", "settings", mode="before")
    @classmethod
    def ensure_dict(cls, v):
        return v if v is not None else {}


class FormQuestionCreate(FormQuestionBase):
    form_id: Optional[int] = None
    section_id: Optional[int] = None


class FormQuestionUpdate(BaseModel):
    code: Optional[str] = None
    label: Optional[str] = None
    help_text: Optional[str] = None
    placeholder: Optional[str] = None
    question_type: Optional[str] = None
    order_index: Optional[int] = None
    is_required: Optional[bool] = None
    is_readonly: Optional[bool] = None
    is_critical: Optional[bool] = None
    is_sanctuarized: Optional[bool] = None
    weight: Optional[float] = None
    scoring_config: Optional[Dict[str, Any]] = None
    options: Optional[List[Dict[str, Any]]] = None
    resource_binding: Optional[Dict[str, Any]] = None
    formula_expression: Optional[str] = None
    validation_rules: Optional[Dict[str, Any]] = None
    dependency_rules: Optional[Dict[str, Any]] = None
    settings: Optional[Dict[str, Any]] = None


class FormQuestionResponse(FormQuestionBase):
    id: int
    form_id: Optional[int] = None
    section_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
