from pydantic import BaseModel, Field
from app.schemas.user.user import UserRead

class LoginRequest(BaseModel):
    identifier: str = Field(..., min_length=3, description="Nom d'utilisateur ou adresse e-mail")
    password: str = Field(..., min_length=1, description="Mot de passe")
    remember_me: bool = Field(False, description="Conserver la session active (30 jours)")

class LoginResponse(BaseModel):
    status: str = "success"
    message: str = "Authentification réussie"
    user: UserRead

class MfaRequiredResponse(BaseModel):
    status: str = "mfa_required"
    message: str = "Vérification en deux étapes requise"
    mfa_token: str
    expires_in: int = 300
