from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, func
from sqlalchemy.orm import relationship
from app.db.database import Base

class TrustedDevice(Base):
    __tablename__ = "trusted_devices"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Hash SHA-256 du token d'appareil de confiance
    device_token_hash = Column(String(64), unique=True, index=True, nullable=False)
    
    device_name = Column(String(255), nullable=True)
    ip_address = Column(String(45), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Relation vers l'utilisateur
    user = relationship("User", back_populates="trusted_devices")

    def __repr__(self) -> str:
        return f"<TrustedDevice id={self.id} user_id={self.user_id}>"
