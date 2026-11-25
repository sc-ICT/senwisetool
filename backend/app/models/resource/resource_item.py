from sqlalchemy import (
    Column,
    Integer,
    DateTime,
    ForeignKey,
    JSON,
    func,
)
from sqlalchemy.orm import relationship
from app.db.database import Base


class ResourceItem(Base):
    __tablename__ = "resource_items"

    id = Column(Integer, primary_key=True, index=True)
    resource_id = Column(
        Integer, ForeignKey("resources.id", ondelete="CASCADE"), nullable=False, index=True
    )
    resource_version_id = Column(
        Integer, ForeignKey("resource_versions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )

    # Données dynamiques de l'entrée correspondant au schema_fields de la ressource
    data = Column(JSON, nullable=False, default=dict)

    created_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relations
    resource = relationship("Resource", back_populates="items", lazy="joined")
    version = relationship("ResourceVersion", foreign_keys=[resource_version_id], lazy="joined")
    organization = relationship("Organization", lazy="joined")
    created_by = relationship("User", foreign_keys=[created_by_id], lazy="selectin")
