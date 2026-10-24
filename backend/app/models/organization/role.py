import enum
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, JSON, Text, func
from sqlalchemy.orm import relationship
from app.db.database import Base

class OrganizationRole(Base):
    __tablename__ = "organization_roles"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True)
    name = Column(String(100), nullable=False)
    code = Column(String(20), nullable=False)
    description = Column(Text, nullable=True)
    is_system = Column(Boolean, default=False, nullable=False)
    status = Column(String(20), default="active", nullable=False)  # active, inactif
    permissions = Column(JSON, nullable=True)  # Matrice permissions { module: { action: "active"|"partial"|"inactive" } }

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    organization = relationship("Organization", back_populates="roles")
    members = relationship("OrganizationMember", back_populates="role")

    def __repr__(self) -> str:
        return f"<OrganizationRole id={self.id} code={self.code} name={self.name} org_id={self.organization_id}>"
