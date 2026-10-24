import enum
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, JSON, Text, func
from sqlalchemy.orm import relationship
from app.db.database import Base

class MemberStatus(str, enum.Enum):
    ACTIF = "actif"
    SUSPENDU = "suspendu"
    INVITE = "invite"

class OrganizationMember(Base):
    __tablename__ = "organization_members"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    email = Column(String(255), nullable=False, index=True)
    name = Column(String(150), nullable=True)

    # Rôles assignés
    is_owner = Column(Boolean, default=False, nullable=False)
    role_id = Column(Integer, ForeignKey("organization_roles.id", ondelete="SET NULL"), nullable=True)
    role_code = Column(String(50), nullable=False, default="OPR")
    roles_list = Column(JSON, nullable=True)  # ex: ["Opérateur terrain"] ou ["Administrateur", "Manager"]

    # Projets accessibles
    accessible_projects = Column(JSON, nullable=True)  # ex: ["Tous les projets"]

    # Statut opérationnel et sécurité
    status = Column(String(30), nullable=False, default=MemberStatus.ACTIF.value, index=True)
    mfa_enabled = Column(Boolean, nullable=False, default=False)
    custom_invitation = Column(Boolean, nullable=False, default=False)

    # Métadonnées d'invitation
    invitation_message = Column(Text, nullable=True)
    invitation_token = Column(String(128), unique=True, nullable=True)
    invited_at = Column(DateTime(timezone=True), nullable=True)
    joined_at = Column(DateTime(timezone=True), nullable=True)

    # Session et télémétrie
    last_login_at = Column(DateTime(timezone=True), nullable=True)
    last_device = Column(String(255), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    # Relations
    organization = relationship("Organization", back_populates="members")
    user = relationship("User", foreign_keys=[user_id])
    role = relationship("OrganizationRole", back_populates="members")

    def __repr__(self) -> str:
        return f"<OrganizationMember id={self.id} email={self.email} org_id={self.organization_id} status={self.status}>"
