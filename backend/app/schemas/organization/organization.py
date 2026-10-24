from typing import Optional, Dict, Any, List, Union
from datetime import datetime
from pydantic import BaseModel, EmailStr, ConfigDict

class OrganizationBase(BaseModel):
    name: str
    code: str
    country: str
    type: str
    sector: Optional[str] = None
    plan: str = "pro"
    contact_email: EmailStr
    phone: Optional[str] = None
    website: Optional[str] = None

class OrganizationUpdateProfile(BaseModel):
    name: Optional[str] = None
    logo_url: Optional[str] = None
    phone: Optional[str] = None
    website: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    region: Optional[str] = None
    postal_code: Optional[str] = None
    sector: Optional[str] = None

class OrganizationReviewActionRequest(BaseModel):
    action: str  # "active" | "revision_requested" | "rejected" | "suspended"
    notes: Optional[str] = ""  # Contenu HTML / Markdown riche WYSIWYG (optionnel pour validation directe)
    rejection_reason: Optional[str] = None
    custom_questions: Optional[List[Dict[str, Any]]] = None

class OrganizationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Union[int, str]
    name: str
    code: str
    logo_url: Optional[str] = None
    country: str
    region: Optional[str] = None
    city: Optional[str] = None
    address: Optional[str] = None
    postal_code: Optional[str] = None
    type: str
    sector: Optional[str] = None
    status: str
    plan: str
    owner_id: Optional[int] = None
    owner_name: Optional[str] = None
    owner_email: Optional[str] = None
    contact_email: str
    phone: Optional[str] = None
    website: Optional[str] = None
    legal_doc_url: Optional[str] = None
    legal_doc_name: Optional[str] = None
    custom_answers: Optional[Dict[str, Any]] = None
    review_notes: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None
    members_count: int = 1
    storage_used_gb: float = 0.0
    storage_limit_gb: float = 50.0
    iot_nodes_count: int = 0
    created_at: datetime
    updated_at: datetime
    activated_at: Optional[datetime] = None

    # Champs enrichis pour les organisations en cours de création / onboarding
    is_in_creation: bool = False
    creation_step: Optional[int] = None
    invitation_id: Optional[int] = None
    invitation_token: Optional[str] = None
    invitation_expires_at: Optional[datetime] = None
    invitation_used: Optional[bool] = None
    custom_questions: Optional[List[Dict[str, Any]]] = None
    draft_data: Optional[Dict[str, Any]] = None

class OrganizationMetricsResponse(BaseModel):
    total: int
    active: int
    pending: int
    revision_requested: int
    suspended: int
    rejected: int
    in_creation: int = 0



