from pydantic import BaseModel, Field

class MfaVerifyRequest(BaseModel):
    mfa_token: str = Field(..., description="Token temporaire de pré-authentification")
    code: str = Field(..., pattern=r"^\d{6}$", description="Code à 6 chiffres TOTP")
    remember_device: bool = Field(False, description="Mémoriser cet appareil pendant 30 jours")

class MfaSetupResponse(BaseModel):
    secret: str
    otpauth_uri: str
