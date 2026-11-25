import enum
from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    DateTime,
    ForeignKey,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import relationship
from app.db.database import Base


class FormStatus(str, enum.Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class FormVisibility(str, enum.Enum):
    PUBLIC = "public"
    PRIVATE = "private"


class FormType(str, enum.Enum):
    STANDARD = "standard"
    AUDIT = "audit"
    SURVEY = "survey"
    INSPECTION = "inspection"
    CHECKLIST = "checklist"
    ONBOARDING = "onboarding"
    CUSTOM = "custom"


class Form(Base):
    """
    Entité principale d'un Formulaire, totalement autonome et indépendante des projets.
    Porte l'identité fondamentale du formulaire et agrège ses versions successives.
    """
    __tablename__ = "forms"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )

    name = Column(String(255), nullable=False, index=True)
    code = Column(String(100), nullable=False, index=True)
    description = Column(Text, nullable=True)

    # Visibilité : 'public' ou 'private'
    visibility = Column(String(50), nullable=False, default=FormVisibility.PUBLIC.value, index=True)

    # Version active courante du formulaire (ex: '1.0.0')
    version = Column(String(50), nullable=False, default="1.0.0", index=True)

    # Statut global du formulaire ('draft', 'published', 'archived')
    status = Column(String(50), nullable=False, default=FormStatus.DRAFT.value, index=True)

    created_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "code", name="uq_form_org_code"),
    )

    # Relations
    organization = relationship("Organization", lazy="joined")
    created_by = relationship("User", foreign_keys=[created_by_id], lazy="selectin")
    versions = relationship(
        "FormVersion",
        back_populates="form",
        cascade="all, delete-orphan",
        order_by="FormVersion.id.desc()",
        lazy="selectin"
    )

    # Note architecturale : Les sections et questions appartiennent strictement à chaque FormVersion.
    # Les propriétés ci-dessous exposent dynamiquement la structure de la version active courante.
    @property
    def active_version(self):
        if not self.versions:
            return None
        # Priorité absolue au brouillon en cours de conception s'il existe
        draft_ver = next((v for v in self.versions if v.status == "draft"), None)
        if draft_ver:
            return draft_ver
        for v in self.versions:
            if v.version_number == self.version:
                return v
        return self.versions[0]

    @property
    def sections(self):
        if hasattr(self, "_active_sections") and self._active_sections is not None:
            return self._active_sections
        v = self.active_version
        return v.sections if v and v.sections else []

    @sections.setter
    def sections(self, val):
        self._active_sections = val

    @property
    def questions(self):
        if hasattr(self, "_active_questions") and self._active_questions is not None:
            return self._active_questions
        v = self.active_version
        return v.questions if v and v.questions else []

    @questions.setter
    def questions(self, val):
        self._active_questions = val

    @property
    def settings(self):
        if hasattr(self, "_active_settings") and self._active_settings is not None:
            return self._active_settings
        v = self.active_version
        return v.settings if v and v.settings else {}

    @settings.setter
    def settings(self, val):
        self._active_settings = val
        v = self.active_version
        if v:
            v.settings = val

    @property
    def resource_binding_config(self):
        if hasattr(self, "_active_resource_binding_config") and self._active_resource_binding_config is not None:
            return self._active_resource_binding_config
        s = self.settings or {}
        return s.get("resource_binding_config") or {}

    @resource_binding_config.setter
    def resource_binding_config(self, val):
        self._active_resource_binding_config = val
        s = dict(self.settings or {})
        s["resource_binding_config"] = val
        self.settings = s
        v = self.active_version
        if v:
            v_settings = dict(v.settings or {})
            v_settings["resource_binding_config"] = val
            v.settings = v_settings
