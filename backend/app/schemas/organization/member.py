from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, EmailStr, ConfigDict

class MemberInviteRequest(BaseModel):
    email: EmailStr
    name: Optional[str] = None
    roles: List[str] = ["Opérateur terrain"]
    accessible_projects: List[str] = ["Tous les projets"]
    message: Optional[str] = None
    mfa_enabled: bool = False
    custom_invitation: bool = False

class MemberUpdateRequest(BaseModel):
    name: Optional[str] = None
    roles: Optional[List[str]] = None
    accessible_projects: Optional[List[str]] = None
    status: Optional[str] = None  # "actif" | "suspendu" | "invite"
    mfa_enabled: Optional[bool] = None

class MemberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    organization_id: int
    user_id: Optional[int] = None
    email: str
    name: Optional[str] = None
    role_code: str
    is_owner: bool = False
    roles: List[str] = []
    accessible_projects: List[str] = []
    status: str
    mfa_enabled: bool = False
    last_login_at: Optional[datetime] = None
    last_device: Optional[str] = None
    invited_at: Optional[datetime] = None
    joined_at: Optional[datetime] = None
    created_at: datetime

class MemberInvitationVerifyResponse(BaseModel):
    valid: bool
    email: str
    name: Optional[str] = None
    organization_id: int
    organization_name: str
    organization_code: Optional[str] = None
    role_name: str
    role_code: str
    mfa_required: bool = False
    user_exists: bool = False

class MemberInvitationAcceptRequest(BaseModel):
    token: str
    password: str
    full_name: Optional[str] = None

class MemberInvitationAcceptResponse(BaseModel):
    status: str = "success"
    message: str
    redirect_url: str = "/dashboard/my-organization"

