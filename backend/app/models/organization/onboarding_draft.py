from sqlalchemy import Column, Integer, String, DateTime, JSON, func
from app.db.database import Base

class OnboardingDraft(Base):
    """
    Sauvegarde automatique pas à pas du formulaire d'onboarding.
    Permet à l'administrateur d'organisation de quitter et revenir sans perdre sa saisie.
    """
    __tablename__ = "onboarding_drafts"

    id = Column(Integer, primary_key=True, index=True)
    admin_email = Column(String(255), unique=True, index=True, nullable=False)
    current_step = Column(Integer, default=1, nullable=False)
    draft_data = Column(JSON, default=dict, nullable=False)
    
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    def __repr__(self) -> str:
        return f"<OnboardingDraft email={self.admin_email} step={self.current_step}>"
