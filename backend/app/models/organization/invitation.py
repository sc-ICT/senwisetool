from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, JSON, func
from sqlalchemy.orm import relationship
from app.db.database import Base

class OrganizationInvitation(Base):
    __tablename__ = "organization_invitations"

    id = Column(Integer, primary_key=True, index=True)
    admin_email = Column(String(255), unique=True, index=True, nullable=False)
    token = Column(String(128), unique=True, index=True, nullable=False)  # Jeton à usage unique sécurisé
    
    # Validation OTP première connexion
    otp_code = Column(String(6), nullable=True)
    otp_expires_at = Column(DateTime(timezone=True), nullable=True)
    otp_verified = Column(Boolean, default=False, nullable=False)
    
    # Expiration du lien d'inscription
    token_expires_at = Column(DateTime(timezone=True), nullable=False)
    
    # Questionnaire personnalisé défini par le Super Admin (optionnel)
    custom_questions = Column(JSON, nullable=True)  # Liste d'objets question { id, label, type, is_required, options }
    
    # Cycle de vie
    is_used = Column(Boolean, default=False, nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=True)
    
    # Super Admin émetteur
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    creator = relationship("User", foreign_keys=[created_by_id])

    def __repr__(self) -> str:
        return f"<OrganizationInvitation email={self.admin_email} used={self.is_used}>"
