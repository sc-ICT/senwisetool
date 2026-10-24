from typing import Optional, Dict, Any, List
from datetime import datetime
from pydantic import BaseModel, EmailStr, Field, ConfigDict, computed_field
from pydantic.alias_generators import to_camel

class OnboardingDraftSaveRequest(BaseModel):
    email: EmailStr
    step: int = Field(ge=1, le=10)
    draft_data: Dict[str, Any]

class OnboardingDraftResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    email: str
    step: int
    draft_data: Dict[str, Any]
    updated_at: datetime

class ContactEmailOTPRequest(BaseModel):
    contact_email: EmailStr

class ContactEmailOTPResponse(BaseModel):
    sent: bool
    message: str
    dev_otp: Optional[str] = None

class ContactEmailVerifyRequest(BaseModel):
    contact_email: EmailStr
    otp: str

class ContactEmailVerifyResponse(BaseModel):
    valid: bool
    message: str

class EmailAvailabilityCheckResponse(BaseModel):
    email: str
    available: bool
    message: str
    used_as: Optional[str] = None

class DocumentUploadResponse(BaseModel):
    filename: str
    original_name: str
    url: str
    size_bytes: int
    size_human: str
    content_type: str


class OnboardingSubmissionRequest(BaseModel):
    # Étape 1 : Type & Prérequis
    type: str = Field(min_length=2)  # Entreprise, ONG, Coopérative...
    legal_doc_name: Optional[str] = None
    legal_doc_url: Optional[str] = None
    
    # Étape 2 : Identité & Dénomination
    name: str = Field(min_length=2, max_length=200)
    code: str = Field(min_length=2, max_length=50)
    country: str = Field(min_length=2)
    region: Optional[str] = None
    city: Optional[str] = None
    address: Optional[str] = None
    postal_code: Optional[str] = None
    
    # Étape 3 : Plan & Contacts
    plan: str = "pro"
    sector: Optional[str] = "Sylviculture & Agro-écologie"
    contact_email: EmailStr
    phone: Optional[str] = None
    website: Optional[str] = None
    logo_url: Optional[str] = None
    
    # Étape 4 : Réponses et questions au questionnaire personnalisé (le cas échéant)
    custom_questions: Optional[List[Dict[str, Any]]] = None
    custom_answers: Optional[Dict[str, Any]] = None

class OnboardingSubmissionResponse(BaseModel):
    organization_id: int
    code: str
    name: str
    status: str
    message: str
    waiting_url: str

class OrganizationStatusCheckResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    organization_id: Optional[int] = None
    organization_name: Optional[str] = None
    organization_code: Optional[str] = None
    logo_url: Optional[str] = None
    status: str  # pending, revision_requested, active, rejected, none
    review_notes: Optional[str] = None  # Mini-éditeur WYSIWYG notes from Super Admin
    rejection_reason: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    reviewed_by_name: Optional[str] = None
    custom_questions: Optional[List[Dict[str, Any]]] = None
    can_edit: bool = False
    can_access_dashboard: bool = False

    # Détails complets du dossier pour le récépissé et l'attestation
    country: Optional[str] = None
    region: Optional[str] = None
    city: Optional[str] = None
    address: Optional[str] = None
    postal_code: Optional[str] = None
    type: Optional[str] = None
    sector: Optional[str] = None
    plan: Optional[str] = None
    contact_email: Optional[str] = None
    phone: Optional[str] = None
    website: Optional[str] = None
    legal_doc_url: Optional[str] = None
    legal_doc_name: Optional[str] = None
    custom_answers: Optional[Dict[str, Any]] = None
    created_at: Optional[datetime] = None

    @computed_field
    @property
    def organizationId(self) -> Optional[int]:
        return self.organization_id

    @computed_field
    @property
    def organizationName(self) -> Optional[str]:
        return self.organization_name

    @computed_field
    @property
    def organizationCode(self) -> Optional[str]:
        return self.organization_code

    @computed_field
    @property
    def logoUrl(self) -> Optional[str]:
        return self.logo_url

    @computed_field
    @property
    def reviewNotes(self) -> Optional[str]:
        return self.review_notes

    @computed_field
    @property
    def customQuestions(self) -> Optional[List[Dict[str, Any]]]:
        return self.custom_questions

    @computed_field
    @property
    def canEdit(self) -> bool:
        return self.can_edit

    @computed_field
    @property
    def canAccessDashboard(self) -> bool:
        return self.can_access_dashboard

    @computed_field
    @property
    def postalCode(self) -> Optional[str]:
        return self.postal_code

    @computed_field
    @property
    def contactEmail(self) -> Optional[str]:
        return self.contact_email

    @computed_field
    @property
    def legalDocUrl(self) -> Optional[str]:
        return self.legal_doc_url

    @computed_field
    @property
    def legalDocName(self) -> Optional[str]:
        return self.legal_doc_name

    @computed_field
    @property
    def customAnswers(self) -> Optional[Dict[str, Any]]:
        return self.custom_answers

    @computed_field
    @property
    def createdAt(self) -> Optional[datetime]:
        return self.created_at

class AttestationVerificationResponse(BaseModel):
    authentic: bool
    status: str
    message: str
    organization_name: Optional[str] = None
    organization_code: Optional[str] = None
    logo_url: Optional[str] = None
    contact_email_masked: Optional[str] = None
    country: Optional[str] = None
    city: Optional[str] = None
    type: Optional[str] = None
    plan: Optional[str] = None
    submitted_at: Optional[str] = None
    certificate_seal: Optional[str] = None

class ReviewDetailsResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    organization_id: int
    name: str
    code: str
    status: str
    logo_url: Optional[str] = None
    review_notes: Optional[str] = None
    custom_questions: Optional[List[Dict[str, Any]]] = None
    existing_answers: Optional[Dict[str, Any]] = None

    @computed_field
    @property
    def organizationId(self) -> int:
        return self.organization_id

    @computed_field
    @property
    def logoUrl(self) -> Optional[str]:
        return self.logo_url

    @computed_field
    @property
    def reviewNotes(self) -> Optional[str]:
        return self.review_notes

    @computed_field
    @property
    def customQuestions(self) -> Optional[List[Dict[str, Any]]]:
        return self.custom_questions

    @computed_field
    @property
    def existingAnswers(self) -> Optional[Dict[str, Any]]:
        return self.existing_answers

class CandidateReviewSubmitRequest(BaseModel):
    organization_id: int
    token: Optional[str] = None
    custom_answers: Dict[str, Any]
    comments: Optional[str] = None
