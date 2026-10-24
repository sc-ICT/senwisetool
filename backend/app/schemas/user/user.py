from datetime import datetime
from pydantic import BaseModel, EmailStr, ConfigDict
from app.models.user.user import UserRole, UserStatus

class UserBase(BaseModel):
    email: EmailStr
    username: str
    full_name: str | None = None
    role: UserRole = UserRole.SUPER_ADMIN

class UserCreate(UserBase):
    password: str

class UserUpdate(BaseModel):
    full_name: str | None = None
    email: EmailStr | None = None

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

class UserRead(BaseModel):
    id: int
    email: EmailStr
    username: str
    full_name: str | None = None
    role: UserRole
    status: UserStatus
    is_email_verified: bool
    mfa_enabled: bool
    last_login_at: datetime | None = None
    created_at: datetime

    # Contexte d'organisation rattachée
    organization_id: int | None = None
    organization_name: str | None = None
    organization_code: str | None = None
    is_org_owner: bool = False
    is_org_admin: bool = False
    org_role_name: str | None = None

    model_config = ConfigDict(from_attributes=True)
