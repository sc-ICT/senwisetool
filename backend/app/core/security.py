import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from fastapi import Response
import jwt
import pyotp
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

from app.core.config import settings

# Configuration du hachage de mot de passe (Argon2id recommandé)
password_hash = PasswordHash((Argon2Hasher(),))

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Vérifie un mot de passe en clair contre son hachage."""
    return password_hash.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    """Génère le hachage sécurisé d'un mot de passe."""
    return password_hash.hash(password)

def hash_token(token: str) -> str:
    """Génère une empreinte SHA-256 pour stocker les refresh tokens et device tokens de façon sécurisée."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

def create_access_token(
    subject: str | int,
    expires_delta: timedelta | None = None,
    extra_claims: dict[str, Any] | None = None
) -> str:
    """Crée un jeton d'accès JWT courte durée."""
    now = datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        
    payload: dict[str, Any] = {
        "sub": str(subject),
        "iat": now,
        "exp": expire,
        "type": "access",
    }
    if extra_claims:
        payload.update(extra_claims)
        
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)

def create_refresh_token(
    subject: str | int,
    remember_me: bool = False
) -> tuple[str, datetime]:
    """
    Crée un refresh token JWT cryptographiquement unique (via jti).
    Retourne le token et sa date d'expiration exacte.
    """
    now = datetime.now(timezone.utc)
    days = (
        settings.REFRESH_TOKEN_EXPIRE_DAYS_REMEMBER_ME 
        if remember_me 
        else settings.REFRESH_TOKEN_EXPIRE_DAYS
    )
    expires_at = now + timedelta(days=days)
    
    payload: dict[str, Any] = {
        "sub": str(subject),
        "jti": secrets.token_hex(16),
        "iat": now,
        "exp": expires_at,
        "type": "refresh",
        "remember_me": remember_me,
    }
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return token, expires_at

def create_mfa_token(user_id: int) -> str:
    """Crée un token temporaire pour le challenge MFA (valide 5 minutes)."""
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.MFA_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": expire,
        "type": "mfa",
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)

def create_password_reset_token(email: str) -> str:
    """Crée un token temporaire pour la réinitialisation de mot de passe (valide 15 minutes)."""
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": email,
        "iat": now,
        "exp": expire,
        "type": "password_reset",
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)

def decode_token(token: str) -> dict[str, Any]:
    """Décode et valide un JWT (signature, expiration). Lève jwt.PyJWTError si invalide."""
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])

# ==========================================
# Gestion TOTP / MFA
# ==========================================

def generate_totp_secret() -> str:
    """Génère une clé secrète aléatoire Base32 pour TOTP (Google Authenticator)."""
    return pyotp.random_base32()

def verify_totp(secret: str, code: str) -> bool:
    """Vérifie un code TOTP à 6 chiffres avec tolérance temporelle de 1 intervalle."""
    totp = pyotp.TOTP(secret)
    return totp.verify(code, valid_window=1)

def get_totp_uri(secret: str, email: str) -> str:
    """Génère l'URI standard otpauth:// pour le QR code."""
    totp = pyotp.TOTP(secret)
    return totp.provisioning_uri(name=email, issuer_name="SenWiseTool")

# ==========================================
# Gestion Sécurisée des Cookies (Anti-XSS)
# ==========================================

def set_auth_cookies(
    response: Response,
    access_token: str,
    refresh_token: str,
    remember_me: bool = False
) -> None:
    """
    Injecte les jetons d'authentification directement dans les cookies HttpOnly du navigateur avec sécurité maximale.
    Empêche tout vol de jeton par script JavaScript malveillant (protection anti-XSS).
    """
    # 1. Cookie Access Token
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/"
    )
    
    # 2. Cookie Refresh Token
    refresh_days = (
        settings.REFRESH_TOKEN_EXPIRE_DAYS_REMEMBER_ME 
        if remember_me 
        else settings.REFRESH_TOKEN_EXPIRE_DAYS
    )
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
        max_age=refresh_days * 86400,
        path="/"
    )

def clear_auth_cookies(response: Response) -> None:
    """Supprime les cookies de session du navigateur."""
    response.delete_cookie(key="access_token", path="/")
    response.delete_cookie(key="refresh_token", path="/")

