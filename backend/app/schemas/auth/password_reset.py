from pydantic import BaseModel, EmailStr, Field

class ForgotPasswordRequest(BaseModel):
    email: EmailStr = Field(..., description="Adresse e-mail associée au compte")

class ResetPasswordRequest(BaseModel):
    token: str = Field(..., description="Jeton de réinitialisation sécurisé")
    new_password: str = Field(..., min_length=8, description="Nouveau mot de passe (min 8 caractères)")

class PasswordResetResponse(BaseModel):
    status: str = "success"
    message: str
