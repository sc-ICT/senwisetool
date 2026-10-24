import re
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, EmailStr, Field, ConfigDict, field_validator

class CustomQuestionSchema(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    id: str
    label: str
    type: str = "text"
    is_required: bool = Field(default=True, alias="isRequired")
    options: Optional[List[str]] = None
    placeholder: Optional[str] = None
    help_text: Optional[str] = Field(default=None, alias="helpText")

class OrganizationInvitationCreate(BaseModel):
    admin_email: EmailStr
    expiration_hours: int = Field(default=48, ge=1, le=720)  # Max 30 jours
    custom_questions: Optional[List[CustomQuestionSchema]] = None

class OrganizationInvitationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    admin_email: str
    token: str
    token_expires_at: datetime
    is_used: bool
    custom_questions: Optional[List[CustomQuestionSchema]] = None
    invite_url: str
    created_at: datetime


class VerifyInvitationRequest(BaseModel):
    token: str
    email: EmailStr

class VerifyInvitationResponse(BaseModel):
    valid: bool
    admin_email: str
    otp_sent: bool
    expires_in_hours: float
    has_custom_questions: bool
    message: str
    dev_otp: Optional[str] = None
    custom_questions: Optional[List[CustomQuestionSchema]] = None


class OTPVerifyRequest(BaseModel):
    token: str
    email: EmailStr
    otp: str

class OTPVerifyResponse(BaseModel):
    valid: bool
    session_ticket: str
    message: str

class SetupFirstPasswordRequest(BaseModel):
    token: str
    email: EmailStr
    session_ticket: Optional[str] = None
    password: str = Field(min_length=8)
    full_name: str = Field(min_length=2)

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Le mot de passe doit comporter au moins 8 caractères.")
        if not re.search(r"[A-Z]", v):
            raise ValueError("Le mot de passe doit contenir au moins une lettre majuscule.")
        if not re.search(r"[a-z]", v):
            raise ValueError("Le mot de passe doit contenir au moins une lettre minuscule.")
        if not re.search(r"[0-9]", v):
            raise ValueError("Le mot de passe doit contenir au moins un chiffre.")
        if not re.search(r"[!@#$%^&*()_+\-=\[\]{};':\"\\|,.<>\/?~]", v):
            raise ValueError("Le mot de passe doit contenir au moins un caractère spécial.")
        return v

class SetupFirstPasswordResponse(BaseModel):
    success: bool
    access_token: str
    refresh_token: Optional[str] = None
    token_type: str = "bearer"
    user_id: int
    email: str
    redirect_url: str
    custom_questions: Optional[List[CustomQuestionSchema]] = None
