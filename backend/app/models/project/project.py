import enum
from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
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
    CUSTOM = "custom"            # Projet sur-mesure


class Project(Base):
    __tablename__ = "projects"

    # 1. Identifiant unique
    id = Column(Integer, primary_key=True, index=True)

    # 2. Nom du projet
    name = Column(String(255), nullable=False, index=True)

    # 3. Code technique unique (immuable, identifie le projet globalement)
    code = Column(String(100), nullable=False, unique=True, index=True)

    # 4. Type de projet (domaine métier)
    project_type = Column(String(50), nullable=False, default=ProjectType.AUDIT.value, index=True)

    # 5. Catégorie thématique
    category = Column(String(100), nullable=False, default="general", index=True)

    # 6. Visibilité multi-tenant ("public" | "private")
    visibility = Column(String(50), nullable=False, default="public", index=True)

    # 7. Organisations autorisées si visibilité privée
    authorized_organization_ids = Column(JSON, nullable=True, default=list)

    # 8. Image de couverture globale
    cover_image_url = Column(Text, nullable=True)

    # 9. Audit & Traçabilité
    created_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("code", name="uq_projects_code"),
    )

    # Relations
    created_by = relationship("User", foreign_keys=[created_by_id], lazy="selectin")
    versions = relationship(
        "ProjectVersion",
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="desc(ProjectVersion.id)",
        lazy="selectin"
    )


class ProjectVersion(Base):
    __tablename__ = "project_versions"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    version_number = Column(String(50), nullable=False)  # ex: '1.0.0'
    status = Column(String(50), nullable=False, default=ProjectStatus.DRAFT.value, index=True)
    description = Column(Text, nullable=True)  # Markdown enrichi
    changelog = Column(Text, nullable=True)
    metrics = Column(JSON, nullable=True, default=lambda: {
        "forms_count": 0,
        "submissions_count": 0,
        "avg_compliance_score": 0.0,
        "critical_issues_count": 0
    })
    settings = Column(JSON, nullable=True, default=dict)
    template_governance = Column(JSON, nullable=True, default=dict)
    created_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("project_id", "version_number", name="uq_project_version_number"),
    )

    # Relations
    project = relationship("Project", back_populates="versions")
    created_by = relationship("User", foreign_keys=[created_by_id], lazy="selectin")
