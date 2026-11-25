from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, JSON, func
from sqlalchemy.orm import relationship
from app.db.database import Base


class OrganizationFeature(Base):
    """
    Table miroir locale des fonctionnalités embarquées dans un plugin téléchargé.
    Spécifie la version et le plugin correspondants (organization_plugin_id, organization_plugin_version_id),
    les organisations qui l'utilisent (organization_ids), l'utilisateur qui l'a téléchargé (downloaded_by_user_id),
    et l'ensemble des paramètres / permissions.
    """
    __tablename__ = "organization_features"

    id = Column(Integer, primary_key=True, index=True)
    organization_plugin_id = Column(Integer, ForeignKey("organization_plugins.id", ondelete="CASCADE"), nullable=False, index=True)
    organization_plugin_version_id = Column(Integer, ForeignKey("organization_plugin_versions.id", ondelete="CASCADE"), nullable=False, index=True)

    # Organisations et utilisateurs
    organization_ids = Column(JSON, nullable=False, default=list)
    user_ids = Column(JSON, nullable=False, default=list)
    downloaded_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    feature_code = Column(String(100), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)

    parameters = Column(JSON, nullable=False, default=dict)
    permissions = Column(JSON, nullable=False, default=dict)
    settings = Column(JSON, nullable=False, default=dict)

    status = Column(String(30), nullable=False, default="active", index=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    # Relations
    organization_plugin = relationship("OrganizationPlugin", back_populates="features")
    organization_plugin_version = relationship("OrganizationPluginVersion", back_populates="features")
    downloaded_by_user = relationship("User", foreign_keys=[downloaded_by_user_id])

    def __repr__(self) -> str:
        return f"<OrganizationFeature id={self.id} code='{self.feature_code}' name='{self.name}'>"
