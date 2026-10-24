import enum
from sqlalchemy import Column, Integer, String, Text, Float, DateTime, ForeignKey, JSON, func
from sqlalchemy.orm import relationship
from app.db.database import Base

class OrgStatusEnum(str, enum.Enum):
    PENDING = "pending"
    ACTIVE = "active"
    REVISION_REQUESTED = "revision_requested"
    REJECTED = "rejected"
    SUSPENDED = "suspended"
    ARCHIVED = "archived"

class OrgPlanEnum(str, enum.Enum):
    STARTER = "starter"
    STANDARD = "standard"
    PRO = "pro"
    ENTERPRISE = "enterprise"

class Organization(Base):
    __tablename__ = "organizations"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False, index=True)
    code = Column(String(50), unique=True, index=True, nullable=False)
    logo_url = Column(String(500), nullable=True)
    
    # Géographie et localisation
    country = Column(String(100), nullable=False, index=True)
    region = Column(String(100), nullable=True)
    city = Column(String(100), nullable=True)
    address = Column(String(255), nullable=True)
    postal_code = Column(String(50), nullable=True)
    
    # Classification et gouvernance
    type = Column(String(100), nullable=False)  # Entreprise, ONG, Institution publique, etc.
    sector = Column(String(100), nullable=True)
    status = Column(String(50), nullable=False, default=OrgStatusEnum.PENDING.value, index=True)
    plan = Column(String(50), nullable=False, default=OrgPlanEnum.PRO.value)
    
    # Propriétaire (Règle 1 compte = 1 organisation assurée au niveau applicatif pour les Org Admins)
    owner_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    contact_email = Column(String(255), nullable=False)
    phone = Column(String(50), nullable=True)
    website = Column(String(255), nullable=True)
    
    # Pièces justificatives et questionnaire dynamique
    legal_doc_url = Column(String(500), nullable=True)
    legal_doc_name = Column(String(255), nullable=True)
    custom_answers = Column(JSON, nullable=True)  # Réponses au questionnaire sur mesure
    custom_questions = Column(JSON, nullable=True)  # Questions personnalisées définies ou demandées lors de révisions
    
    # Instruction et revue Super Admin (avec éditeur WYSIWYG)
    review_notes = Column(Text, nullable=True)  # Contenu HTML/Markdown riche WYSIWYG
    reviewed_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    rejection_reason = Column(Text, nullable=True)
    
    # Métriques et quotas
    members_count = Column(Integer, default=1, nullable=False)
    storage_used_gb = Column(Float, default=0.0, nullable=False)
    storage_limit_gb = Column(Float, default=50.0, nullable=False)
    iot_nodes_count = Column(Integer, default=0, nullable=False)
    
    # Audit
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    # Relations
    owner = relationship("User", foreign_keys=[owner_id])
    reviewer = relationship("User", foreign_keys=[reviewed_by_id])
    members = relationship("OrganizationMember", back_populates="organization", cascade="all, delete-orphan")
    roles = relationship("OrganizationRole", back_populates="organization", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Organization id={self.id} code={self.code} name={self.name} status={self.status}>"
