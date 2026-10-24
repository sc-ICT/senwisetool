from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field, field_validator, ConfigDict


class SubmissionAnswerInput(BaseModel):
    question_code: str = Field(..., description="Code unique de la question répondue")
    section_code: str = Field(..., description="Code de la section parente")
    value: Any = Field(None, description="Valeur saisie (texte, nombre, sélection, GPS, etc.)")
    raw_text: Optional[str] = None
    compliance_choice: Optional[str] = Field(None, description="COMPLIANT, NON_COMPLIANT, NOT_APPLICABLE")
    is_applicable: bool = Field(default=True, description="Si False, exclu de l'évaluation du score")
    evidence_notes: Optional[str] = None
    evidence_media_urls: List[str] = Field(default_factory=list)

    @field_validator("evidence_media_urls", mode="before")
    @classmethod
    def ensure_list(cls, v):
        return v if v is not None else []


class FormSubmissionCreate(BaseModel):
    form_id: int
    answers: List[SubmissionAnswerInput] = Field(..., min_length=1)
    geolocation: Optional[Dict[str, Any]] = None
    offline_created_at: Optional[datetime] = None
    metadata_info: Dict[str, Any] = Field(default_factory=dict)


class SubmissionAnswerResponse(BaseModel):
    id: int
    question_code: str
    section_code: str
    value: Any
    raw_text: Optional[str] = None
    compliance_choice: Optional[str] = None
    is_compliant: Optional[bool] = None
    is_critical_failure: bool = False
    points_awarded: Optional[float] = None
    is_applicable: bool = True
    evidence_notes: Optional[str] = None
    evidence_media_urls: List[str] = Field(default_factory=list)
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class FormSubmissionResponse(BaseModel):
    id: int
    form_id: int
    organization_id: Optional[int] = None
    submission_code: str
    submitted_by_id: Optional[int] = None
    status: str
    total_score: Optional[float] = None
    compliance_status: str
    critical_failures_count: int = 0
    section_scores: Dict[str, Any] = Field(default_factory=dict)
    geolocation: Optional[Dict[str, Any]] = None
    offline_created_at: Optional[datetime] = None
    submitted_at: datetime
    validated_by_id: Optional[int] = None
    validated_at: Optional[datetime] = None
    validation_notes: Optional[str] = None
    metadata_info: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class FormSubmissionDetailResponse(FormSubmissionResponse):
    answers: List[SubmissionAnswerResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)
