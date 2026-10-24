from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, func
from sqlalchemy.orm import relationship
from app.db.database import Base

class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Hash SHA-256 du refresh token (évite de stocker le token en clair)
    token_hash = Column(String(64), unique=True, index=True, nullable=False)
    
    # Métadonnées de session
    device_info = Column(String(255), nullable=True)
    ip_address = Column(String(45), nullable=True)
    
    # Contrôle de session
    is_revoked = Column(Boolean, nullable=False, default=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Relation vers l'utilisateur
    user = relationship("User", back_populates="refresh_tokens")

    def __repr__(self) -> str:
        return f"<RefreshToken id={self.id} user_id={self.user_id} revoked={self.is_revoked}>"
