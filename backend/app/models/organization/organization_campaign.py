from sqlalchemy import Column, Integer, String, Text, Numeric, DateTime, ForeignKey, JSON, func
from sqlalchemy.orm import relationship
from app.db.database import Base


class OrganizationCampaign(Base):
    """
    Table miroir locale des campagnes pour l'organisation cliente.
    Rattachée à organization_plugin_versions (organization_plugin_version_id),
    à l'organisation cliente (organization_id) et à l'utilisateur ayant téléchargé (downloaded_by_user_id).
    source_campaign_id relie à la table d'origine campaigns.
    """
    __tablename__ = "organization_campaigns"

    id = Column(Integer, primary_key=True, index=True)
    source_campaign_id = Column(Integer, ForeignKey("campaigns.id", ondelete="SET NULL"), nullable=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    organization_plugin_version_id = Column(Integer, ForeignKey("organization_plugin_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    downloaded_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    # Identiques à Campaign
    name = Column(String(255), nullable=False, index=True)
    code = Column(String(100), nullable=False, index=True)
    description = Column(Text, nullable=True)
    visibility = Column(String(50), nullable=False, default="private")
    status = Column(String(50), nullable=False, default="active", index=True)
    version = Column(String(50), nullable=False, default="1.0.0")

    # Paramètres et configurations
    settings = Column(JSON, nullable=False, default=dict)
    parameters = Column(JSON, nullable=False, default=dict)

    closed_at = Column(DateTime(timezone=True), nullable=True)
    closed_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    # Relations
    organization = relationship("Organization")
    organization_plugin_version = relationship("OrganizationPluginVersion", back_populates="campaigns")
    source_campaign = relationship("Campaign", foreign_keys=[source_campaign_id])
    downloaded_by_user = relationship("User", foreign_keys=[downloaded_by_user_id])
    closed_by = relationship("User", foreign_keys=[closed_by_id])

    versions = relationship(
        "OrganizationCampaignVersion",
        back_populates="organization_campaign",
        cascade="all, delete-orphan",
        order_by="desc(OrganizationCampaignVersion.id)",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<OrganizationCampaign id={self.id} org_id={self.organization_id} code='{self.code}' name='{self.name}'>"


class OrganizationCampaignVersion(Base):
    """
    Table miroir locale des versions de campagnes téléchargées.
    Liaisons internes vers organization_campaigns (organization_campaign_id)
    et organization_plugin_versions (organization_plugin_version_id).
    source_campaign_version_id relie à campaign_versions.
    """
    __tablename__ = "organization_campaign_versions"

    id = Column(Integer, primary_key=True, index=True)
    organization_campaign_id = Column(Integer, ForeignKey("organization_campaigns.id", ondelete="CASCADE"), nullable=False, index=True)
    organization_plugin_version_id = Column(Integer, ForeignKey("organization_plugin_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    source_campaign_version_id = Column(Integer, ForeignKey("campaign_versions.id", ondelete="SET NULL"), nullable=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    downloaded_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    # Identiques à CampaignVersion
    version_number = Column(String(50), nullable=False, index=True)
    status = Column(String(50), nullable=False, default="native", index=True)
    description = Column(Text, nullable=True)
    objectives = Column(Text, nullable=True)
    targets = Column(JSON, nullable=True, default=dict)
    start_date = Column(DateTime(timezone=True), nullable=True)
    end_date = Column(DateTime(timezone=True), nullable=True)
    budget = Column(Numeric(15, 2), nullable=True)
    currency = Column(String(10), nullable=False, default="XOF")
    target_forms = Column(JSON, nullable=True, default=list)
    target_projects = Column(JSON, nullable=True, default=list)
    changelog = Column(Text, nullable=True)
    metrics = Column(JSON, nullable=True, default=dict)
    template_governance = Column(JSON, nullable=True, default=dict)
    publication_number = Column(Integer, nullable=True, index=True)
    origin_version_id = Column(Integer, ForeignKey("organization_campaign_versions.id", ondelete="SET NULL"), nullable=True, index=True)
    published_at = Column(DateTime(timezone=True), nullable=True)

    # Paramètres et configurations
    settings = Column(JSON, nullable=False, default=dict)
    parameters = Column(JSON, nullable=False, default=dict)

    closed_at = Column(DateTime(timezone=True), nullable=True)
    closed_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    # Relations
    organization = relationship("Organization")
    organization_campaign = relationship("OrganizationCampaign", back_populates="versions")
    organization_plugin_version = relationship("OrganizationPluginVersion")
    source_campaign_version = relationship("CampaignVersion", foreign_keys=[source_campaign_version_id])
    downloaded_by_user = relationship("User", foreign_keys=[downloaded_by_user_id])
    closed_by = relationship("User", foreign_keys=[closed_by_id])

    def __repr__(self) -> str:
        return f"<OrganizationCampaignVersion id={self.id} org_id={self.organization_id} version='{self.version_number}'>"
