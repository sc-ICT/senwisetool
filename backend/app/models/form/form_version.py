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


class FormVersionStatus(str, enum.Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ACTIVE = "active"
    ARCHIVED = "archived"


class FormVersion(Base):
    """
    Version immuable et scellée d'un formulaire.
    Chaque version détient sa propre arborescence de sections, questions, paramètres et règles.
    """
    __tablename__ = "form_versions"

    id = Column(Integer, primary_key=True, index=True)
    form_id = Column(
        Integer, ForeignKey("forms.id", ondelete="CASCADE"), nullable=False, index=True
    )

    version_number = Column(String(50), nullable=False, index=True)
    status = Column(String(50), nullable=False, default=FormVersionStatus.DRAFT.value, index=True)
    description = Column(Text, nullable=True)  # Changelog ou description de version
    form_type = Column(String(50), nullable=False, default="standard", index=True)

    # Paramètres de notation, validation, offline, etc.
    settings = Column(JSON, nullable=True, default=dict)

    # Gouvernance et autorisations de la version (allow_edit, allow_clone, etc.)
    template_governance = Column(JSON, nullable=True, default=dict)

    created_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    published_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("form_id", "version_number", name="uq_form_version"),
    )

    # Relations
    form = relationship("Form", back_populates="versions", lazy="joined")
    created_by = relationship("User", foreign_keys=[created_by_id], lazy="selectin")
    sections = relationship(
        "FormSection",
        primaryjoin="and_(FormVersion.id == FormSection.form_version_id, FormSection.parent_section_id.is_(None))",
        back_populates="version",
        cascade="all, delete-orphan",
        order_by="FormSection.order_index",
        lazy="selectin"
    )
    questions = relationship(
        "FormQuestion",
        primaryjoin="and_(FormVersion.id == FormQuestion.form_version_id, FormQuestion.section_id.is_(None))",
        back_populates="version",
        cascade="all, delete-orphan",
        order_by="FormQuestion.order_index",
        lazy="selectin"
    )

    @property
    def sections_count(self) -> int:
        return len(self.sections) if self.sections else 0

    @property
    def questions_count(self) -> int:
        count = len(self.questions) if self.questions else 0
        def _count_sec(sec):
            c = len(sec.questions) if sec.questions else 0
            if sec.subsections:
                for sub in sec.subsections:
                    c += _count_sec(sub)
            return c
        if self.sections:
            for s in self.sections:
                count += _count_sec(s)
        return count

    @property
    def resource_binding_config(self):
        s = self.settings or {}
        return s.get("resource_binding_config") or {}

    @resource_binding_config.setter
    def resource_binding_config(self, val):
        s = dict(self.settings or {})
        s["resource_binding_config"] = val
        self.settings = s
