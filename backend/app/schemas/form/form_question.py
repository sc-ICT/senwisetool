from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict


class FormQuestionBase(BaseModel):
    code: str = Field(default="", max_length=100)
    label: str = Field(..., min_length=1)
    help_text: Optional[str] = None
    placeholder: Optional[str] = None
    default_value: Optional[Any] = None
    question_type: str = Field(default="text_short")
    order_index: int = Field(default=0)
    is_required: bool = Field(default=True)
    is_readonly: bool = Field(default=False)
    is_critical: bool = Field(default=False)
    weight: float = Field(default=1.0)
    scoring_config: Dict[str, Any] = Field(default_factory=dict)
    options: List[Dict[str, Any]] = Field(default_factory=list)
    resource_binding: Dict[str, Any] = Field(default_factory=dict)
    formula_expression: Optional[str] = None
    validation_rules: Dict[str, Any] = Field(default_factory=dict)
    dependency_rules: Dict[str, Any] = Field(default_factory=dict)
    settings: Dict[str, Any] = Field(default_factory=dict)


class FormQuestionCreate(FormQuestionBase):
    section_id: Optional[int] = None
    form_version_id: Optional[int] = None


class FormQuestionUpdate(BaseModel):
    label: Optional[str] = None
    help_text: Optional[str] = None
    placeholder: Optional[str] = None
    default_value: Optional[Any] = None
    question_type: Optional[str] = None
    order_index: Optional[int] = None
    is_required: Optional[bool] = None
    is_readonly: Optional[bool] = None
    is_critical: Optional[bool] = None
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
    form_version_id: Optional[int] = None
    section_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
