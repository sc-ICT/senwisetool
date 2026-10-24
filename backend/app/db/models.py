# Re-export from app.models for backward compatibility
from app.models import User, UserRole, UserStatus, RefreshToken, TrustedDevice

__all__ = ["User", "UserRole", "UserStatus", "RefreshToken", "TrustedDevice"]
