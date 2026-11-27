from sqlalchemy import Column, Integer, String, Text, Boolean, Numeric, DateTime, ForeignKey, JSON, func
from sqlalchemy.orm import relationship
from app.db.database import Base


class OrganizationForm(Base):
    """
    Table miroir locale des formulaires pour l'organisation cliente.
    Rattachée à organization_plugin_versions (organization_plugin_version_id),
    à l'organisation cliente (organization_id) et à l'utilisateur ayant téléchargé (downloaded_by_user_id).
    source_form_id relie à la table d'origine forms.
    """
    __tablename__ = "organization_forms"

    id = Column(Integer, primary_key=True, index=True)
    source_form_id = Column(Integer, ForeignKey("forms.id", ondelete="SET NULL"), nullable=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    organization_plugin_version_id = Column(Integer, ForeignKey("organization_plugin_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    downloaded_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    # Identiques à Form
    name = Column(String(255), nullable=False, index=True)
    code = Column(String(100), nullable=False, index=True)
    description = Column(Text, nullable=True)
    visibility = Column(String(50), nullable=False, default="private")
    version = Column(String(50), nullable=False, default="1.0.0")
    status = Column(String(50), nullable=False, default="active", index=True)

    # Paramètres et configurations
    settings = Column(JSON, nullable=False, default=dict)
    parameters = Column(JSON, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    # Relations
    organization = relationship("Organization")
    organization_plugin_version = relationship("OrganizationPluginVersion", back_populates="forms")
    source_form = relationship("Form", foreign_keys=[source_form_id])
    downloaded_by_user = relationship("User", foreign_keys=[downloaded_by_user_id])

    versions = relationship(
        "OrganizationFormVersion",
        back_populates="organization_form",
        cascade="all, delete-orphan",
        order_by="desc(OrganizationFormVersion.id)",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<OrganizationForm id={self.id} org_id={self.organization_id} code='{self.code}' name='{self.name}'>"


class OrganizationFormVersion(Base):
    """
    Table miroir locale des versions de formulaires téléchargées.
    Liaisons internes vers organization_forms (organization_form_id)
    et organization_plugin_versions (organization_plugin_version_id).
    source_form_version_id relie à form_versions.
    """
    __tablename__ = "organization_form_versions"

    id = Column(Integer, primary_key=True, index=True)
    organization_form_id = Column(Integer, ForeignKey("organization_forms.id", ondelete="CASCADE"), nullable=False, index=True)
    organization_plugin_version_id = Column(Integer, ForeignKey("organization_plugin_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    source_form_version_id = Column(Integer, ForeignKey("form_versions.id", ondelete="SET NULL"), nullable=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    downloaded_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    # Identiques à FormVersion
    version_number = Column(String(50), nullable=False, index=True)
    status = Column(String(50), nullable=False, default="native", index=True)
    description = Column(Text, nullable=True)
    form_type = Column(String(50), nullable=False, default="standard")
    template_governance = Column(JSON, nullable=True, default=dict)
    published_at = Column(DateTime(timezone=True), nullable=True)
    publication_number = Column(Integer, nullable=True, index=True)
    origin_version_id = Column(Integer, ForeignKey("organization_form_versions.id", ondelete="SET NULL"), nullable=True, index=True)

    # Paramètres et configurations
    settings = Column(JSON, nullable=False, default=dict)
    parameters = Column(JSON, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    # Relations
    organization = relationship("Organization")
    organization_form = relationship("OrganizationForm", back_populates="versions")
    organization_plugin_version = relationship("OrganizationPluginVersion")
    source_form_version = relationship("FormVersion", foreign_keys=[source_form_version_id])
    downloaded_by_user = relationship("User", foreign_keys=[downloaded_by_user_id])

    sections = relationship(
        "OrganizationFormSection",
        back_populates="organization_form_version",
        cascade="all, delete-orphan",
        order_by="OrganizationFormSection.order_index",
        lazy="selectin",
    )
    questions = relationship(
        "OrganizationFormQuestion",
        back_populates="organization_form_version",
        cascade="all, delete-orphan",
        order_by="OrganizationFormQuestion.order_index",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<OrganizationFormVersion id={self.id} org_id={self.organization_id} version='{self.version_number}'>"


class OrganizationFormSection(Base):
    """
    Table miroir locale des sections de formulaires.
    Liaisons internes vers organization_forms (organization_form_id),
    organization_form_versions (organization_form_version_id),
    parent_section_id vers organization_form_sections.id
    et organization_plugin_versions (organization_plugin_version_id).
    source_form_section_id relie à form_sections.
    """
    __tablename__ = "organization_form_sections"

    id = Column(Integer, primary_key=True, index=True)
    organization_form_id = Column(Integer, ForeignKey("organization_forms.id", ondelete="CASCADE"), nullable=False, index=True)
    organization_form_version_id = Column(Integer, ForeignKey("organization_form_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    organization_plugin_version_id = Column(Integer, ForeignKey("organization_plugin_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    source_form_section_id = Column(Integer, ForeignKey("form_sections.id", ondelete="SET NULL"), nullable=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    downloaded_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    parent_section_id = Column(Integer, ForeignKey("organization_form_sections.id", ondelete="CASCADE"), nullable=True, index=True)

    # Identiques à FormSection
    title = Column(Text, nullable=False)
    code = Column(String(100), nullable=False, index=True)
    description = Column(Text, nullable=True)
    order_index = Column(Integer, nullable=False, default=0)
    is_repeatable = Column(Boolean, nullable=False, default=False)
    min_repeats = Column(Integer, nullable=False, default=1)
    max_repeats = Column(Integer, nullable=True)
    is_sanctuarized = Column(Boolean, nullable=False, default=False)
    allow_custom_questions = Column(Boolean, nullable=False, default=True)
    weight = Column(Numeric(5, 2), nullable=False, default=1.00)
    scoring_category = Column(String(100), nullable=True)
    is_critical = Column(Boolean, nullable=False, default=False)
    min_passing_score = Column(Numeric(5, 2), nullable=True)
    dependency_rules = Column(JSON, nullable=True, default=dict)

    # Paramètres et configurations
    settings = Column(JSON, nullable=False, default=dict)
    parameters = Column(JSON, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    # Relations
    organization = relationship("Organization")
    organization_form = relationship("OrganizationForm")
    organization_form_version = relationship("OrganizationFormVersion", back_populates="sections")
    organization_plugin_version = relationship("OrganizationPluginVersion")
    parent_section = relationship("OrganizationFormSection", remote_side=[id], back_populates="subsections")
    subsections = relationship("OrganizationFormSection", back_populates="parent_section", cascade="all, delete-orphan")
    questions = relationship("OrganizationFormQuestion", back_populates="section", cascade="all, delete-orphan")
    source_form_section = relationship("FormSection", foreign_keys=[source_form_section_id])
    downloaded_by_user = relationship("User", foreign_keys=[downloaded_by_user_id])

    def __repr__(self) -> str:
        return f"<OrganizationFormSection id={self.id} org_id={self.organization_id} code='{self.code}' title='{self.title[:30]}'>"


class OrganizationFormQuestion(Base):
    """
    Table miroir locale des questions de formulaires.
    Liaisons internes vers organization_forms (organization_form_id),
    organization_form_versions (organization_form_version_id),
    organization_form_sections (organization_form_section_id)
    et organization_plugin_version (organization_plugin_version_id).
    source_form_question_id relie à form_questions.
    """
    __tablename__ = "organization_form_questions"

    id = Column(Integer, primary_key=True, index=True)
    organization_form_id = Column(Integer, ForeignKey("organization_forms.id", ondelete="CASCADE"), nullable=False, index=True)
    organization_form_version_id = Column(Integer, ForeignKey("organization_form_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    organization_form_section_id = Column(Integer, ForeignKey("organization_form_sections.id", ondelete="CASCADE"), nullable=True, index=True)
    organization_plugin_version_id = Column(Integer, ForeignKey("organization_plugin_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    source_form_question_id = Column(Integer, ForeignKey("form_questions.id", ondelete="SET NULL"), nullable=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    downloaded_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    # Identiques à FormQuestion
    code = Column(String(100), nullable=False, index=True)
    label = Column(Text, nullable=False)
    help_text = Column(Text, nullable=True)
    placeholder = Column(String(255), nullable=True)
    question_type = Column(String(50), nullable=False)
    order_index = Column(Integer, nullable=False, default=0)
    is_required = Column(Boolean, nullable=False, default=False)
    is_readonly = Column(Boolean, nullable=False, default=False)
    is_sanctuarized = Column(Boolean, nullable=False, default=False)
    is_critical = Column(Boolean, nullable=False, default=False)
    weight = Column(Numeric(5, 2), nullable=False, default=1.00)
    scoring_config = Column(JSON, nullable=True, default=dict)
    options = Column(JSON, nullable=True, default=list)
    resource_binding = Column(JSON, nullable=True, default=dict)
    formula_expression = Column(String(500), nullable=True)
    validation_rules = Column(JSON, nullable=True, default=dict)
    dependency_rules = Column(JSON, nullable=True, default=dict)

    # Paramètres et configurations
    settings = Column(JSON, nullable=False, default=dict)
    parameters = Column(JSON, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    # Relations
    organization = relationship("Organization")
    organization_form = relationship("OrganizationForm")
    organization_form_version = relationship("OrganizationFormVersion", back_populates="questions")
    section = relationship("OrganizationFormSection", back_populates="questions")
    organization_plugin_version = relationship("OrganizationPluginVersion")
    source_form_question = relationship("FormQuestion", foreign_keys=[source_form_question_id])
    downloaded_by_user = relationship("User", foreign_keys=[downloaded_by_user_id])

    def __repr__(self) -> str:
        return f"<OrganizationFormQuestion id={self.id} org_id={self.organization_id} code='{self.code}' label='{self.label[:30]}'>"
