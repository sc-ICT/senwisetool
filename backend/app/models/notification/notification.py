from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, ForeignKey, func
from app.db.database import Base

class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    target_role = Column(String(50), nullable=True, index=True)  # 'superadmin', 'admin', etc.
    title = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)
    link = Column(String(500), nullable=True)  # Lien cliquable vers l'entité (ex: /dashboard/organizations/2)
    type = Column(String(50), nullable=False, default="info")  # 'submission' | 'review' | 'success' | 'warning' | 'info'
    is_read = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
