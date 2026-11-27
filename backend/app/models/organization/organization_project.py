from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, JSON, func
from sqlalchemy.orm import relationship
from app.db.database import Base


class OrganizationProject(Base):
    """
    Table miroir locale des projets pour l'organisation cliente.
    Rattachée à organization_plugin_versions (organization_plugin_version_id),
    à l'organisation cliente (organization_id) et à l'utilisateur ayant téléchargé (downloaded_by_user_id).
    source_project_id relie à la table d'origine projects.
    """
    __tablename__ = "organization_projects"

    id = Column(Integer, primary_key=True, index=True)
    source_project_id = Column(Integer, ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    organization_plugin_version_id = Column(Integer, ForeignKey("organization_plugin_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    downloaded_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    # Identiques à Project
    name = Column(String(255), nullable=False, index=True)
    code = Column(String(100), nullable=False, index=True)
    project_type = Column(String(50), nullable=False, default="audit")
    category = Column(String(100), nullable=False, default="general")
    visibility = Column(String(50), nullable=False, default="private")
    authorized_organization_ids = Column(JSON, nullable=True, default=list)
    cover_image_url = Column(Text, nullable=True)

    status = Column(String(50), nullable=False, default="active", index=True)

    # Paramètres et configurations
    settings = Column(JSON, nullable=False, default=dict)
    parameters = Column(JSON, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    # Relations
    organization = relationship("Organization")
    organization_plugin_version = relationship("OrganizationPluginVersion", back_populates="projects")
    source_project = relationship("Project", foreign_keys=[source_project_id])
    downloaded_by_user = relationship("User", foreign_keys=[downloaded_by_user_id])

    versions = relationship(
        "OrganizationProjectVersion",
        back_populates="organization_project",
        cascade="all, delete-orphan",
        order_by="desc(OrganizationProjectVersion.id)",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<OrganizationProject id={self.id} org_id={self.organization_id} code='{self.code}' name='{self.name}'>"


class OrganizationProjectVersion(Base):
    """
    Table miroir locale des versions de projets téléchargées.
    Liaisons internes vers organization_projects (organization_project_id)
    et organization_plugin_versions (organization_plugin_version_id).
    source_project_version_id relie à project_versions.
    """
    __tablename__ = "organization_project_versions"

    id = Column(Integer, primary_key=True, index=True)
    organization_project_id = Column(Integer, ForeignKey("organization_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    organization_plugin_version_id = Column(Integer, ForeignKey("organization_plugin_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    source_project_version_id = Column(Integer, ForeignKey("project_versions.id", ondelete="SET NULL"), nullable=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    downloaded_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    # Identiques à ProjectVersion
    version_number = Column(String(50), nullable=False, index=True)
    status = Column(String(50), nullable=False, default="native", index=True)
    description = Column(Text, nullable=True)
    changelog = Column(Text, nullable=True)
    metrics = Column(JSON, nullable=True, default=dict)
    template_governance = Column(JSON, nullable=True, default=dict)
    publication_number = Column(Integer, nullable=True, index=True)
    origin_version_id = Column(Integer, ForeignKey("organization_project_versions.id", ondelete="SET NULL"), nullable=True, index=True)
    published_at = Column(DateTime(timezone=True), nullable=True)

    # Paramètres et configurations
    settings = Column(JSON, nullable=False, default=dict)
    parameters = Column(JSON, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    # Relations
    organization = relationship("Organization")
    organization_project = relationship("OrganizationProject", back_populates="versions")
    organization_plugin_version = relationship("OrganizationPluginVersion")
    source_project_version = relationship("ProjectVersion", foreign_keys=[source_project_version_id])
    downloaded_by_user = relationship("User", foreign_keys=[downloaded_by_user_id])

    def __repr__(self) -> str:
        return f"<OrganizationProjectVersion id={self.id} org_id={self.organization_id} version='{self.version_number}'>"
