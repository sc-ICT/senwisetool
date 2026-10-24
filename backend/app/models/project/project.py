import enum
from sqlalchemy import (
    Column,
    Integer,
    String,
    Boolean,
    Text,
    Numeric,
    DateTime,
    ForeignKey,
    JSON,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import relationship
from app.db.database import Base


class ProjectStatus(str, enum.Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class ProjectType(str, enum.Enum):
    AUDIT = "audit"              # Audit réglementaire (ex: EUDR, Rainforest Alliance)
    CERTIFICATION = "certification"  # Certification filière durable (Bio, Fairtrade)
    SURVEY = "survey"            # Recensement et enquêtes terrain
    TRACEABILITY = "traceability" # Cartographie et traçabilité parcelles
    MONITORING = "monitoring"    # Suivi agronomique et environnemental
    CUSTOM = "custom"            # Projet sur-mesure défini par l'organisation


class Project(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    plugin_id = Column(
        Integer, ForeignKey("plugins.id", ondelete="SET NULL"), nullable=True, index=True
    )
    plugin_code = Column(String(100), nullable=True, index=True)

    # Gouvernance et distribution de l'outil
    is_template = Column(Boolean, nullable=False, default=True, index=True)  # True = Modèle conçu par Super Admin pour plugins
    scope = Column(String(30), nullable=False, default="tenant", index=True)  # "tenant", "plugin_shared", "preconfigured"
    is_shared = Column(Boolean, nullable=False, default=False, index=True)
    is_immutable = Column(Boolean, nullable=False, default=False, index=True)
    immutable_fields = Column(JSON, nullable=True, default=list)  # ex: ["category", "target_forms", "code"]
    parent_template_id = Column(Integer, nullable=True, index=True)

    # Exigences & Gouvernance imposées aux organisations qui instancient ce modèle
    # {
    #   "allow_tenant_add_sections": bool,
    #   "allow_tenant_add_questions": bool,
    #   "allow_tenant_modify_threshold": bool,
    #   "allow_tenant_add_forms": bool,
    #   "sanctuarized_sections": ["SEC_LEGALITY", "SEC_DEFORESTATION"],
    #   "sanctuarized_questions": ["Q_GPS_POLYGON", "Q_CUTOFF_DATE"],
    #   "enforce_critical_gates": bool
    # }
    template_governance = Column(JSON, nullable=True, default=lambda: {
        "allow_tenant_add_sections": True,
        "allow_tenant_add_questions": True,
        "allow_tenant_modify_threshold": False,
        "allow_tenant_add_forms": True,
        "enforce_critical_gates": True,
        "sanctuarized_sections": [],
        "sanctuarized_questions": []
    })

    name = Column(String(255), nullable=False, index=True)
    code = Column(String(100), nullable=False, index=True)
    category = Column(String(100), nullable=False, default="general", index=True)
    project_type = Column(String(50), nullable=False, default=ProjectType.AUDIT.value, index=True)
    status = Column(String(50), nullable=False, default=ProjectStatus.DRAFT.value, index=True)
    version = Column(String(20), nullable=False, default="1.0.0", index=True)
    icon = Column(String(50), nullable=False, default="FolderKanban")

    description = Column(Text, nullable=True)
    objectives = Column(Text, nullable=True)
    start_date = Column(DateTime(timezone=True), nullable=True)
    end_date = Column(DateTime(timezone=True), nullable=True)

    budget = Column(Numeric(15, 2), nullable=True)
    currency = Column(String(10), nullable=False, default="XOF")

    targets = Column(JSON, nullable=True, default=dict)      # Objectifs chiffrés (ex: nb de parcelles, % conformité visé)
    settings = Column(JSON, nullable=True, default=dict)     # Paramètres d'audit, géoloc requise, seuils d'alerte
    metrics = Column(JSON, nullable=True, default=lambda: {
        "forms_count": 0,
        "submissions_count": 0,
        "avg_compliance_score": 0.0,
        "critical_issues_count": 0
    })

    created_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "code", name="uq_project_org_code"),
    )

    # Relations
    organization = relationship("Organization", lazy="joined")
    plugin = relationship("Plugin", lazy="selectin")
    created_by = relationship("User", foreign_keys=[created_by_id], lazy="selectin")
    forms = relationship("Form", back_populates="project", cascade="all, delete-orphan", lazy="selectin")
