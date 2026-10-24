import enum
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Enum, func
from sqlalchemy.orm import relationship
from app.db.database import Base

class UserRole(str, enum.Enum):
    SUPER_ADMIN = "SUPER_ADMIN"
    ADMIN_ORG = "ADMIN_ORG"
    MANAGER = "MANAGER"
    OPERATOR = "OPERATOR"
    AUDITOR = "AUDITOR"
    USER = "USER"

class UserStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    PENDING_VERIFICATION = "PENDING_VERIFICATION"
    SUSPENDED = "SUSPENDED"
    LOCKED = "LOCKED"
    INACTIVE = "INACTIVE"

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    username = Column(String(100), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(150), nullable=True)
    
    # Rôles et statuts
    role = Column(Enum(UserRole), nullable=False, default=UserRole.SUPER_ADMIN)
    status = Column(Enum(UserStatus), nullable=False, default=UserStatus.PENDING_VERIFICATION)
    is_email_verified = Column(Boolean, nullable=False, default=False)
    
    # Sécurité MFA
    mfa_enabled = Column(Boolean, nullable=False, default=False)
    mfa_secret = Column(String(64), nullable=True)  # Clé secrète Base32 TOTP
    
    # Politique anti brute-force
    failed_login_attempts = Column(Integer, nullable=False, default=0)
    locked_until = Column(DateTime(timezone=True), nullable=True)
    
    # Historique et audit
    last_login_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    # Relations
    refresh_tokens = relationship("RefreshToken", back_populates="user", cascade="all, delete-orphan")
    trusted_devices = relationship("TrustedDevice", back_populates="user", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email} role={self.role} status={self.status}>"
