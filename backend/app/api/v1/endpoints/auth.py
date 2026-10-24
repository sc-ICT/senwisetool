from typing import Any
from fastapi import APIRouter, Depends, Request, Response, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.core.config import settings
from app.schemas.user.user import UserRead
from app.schemas.auth.login import LoginRequest, LoginResponse, MfaRequiredResponse
from app.schemas.auth.mfa import MfaVerifyRequest
from app.services.auth.auth_service import auth_service
from app.services.auth.mfa_service import mfa_service
from app.api.deps import require_active_user
from app.models.user.user import User

router = APIRouter()

from app.core.security import set_auth_cookies, clear_auth_cookies

@router.post(
    "/login",
    response_model=LoginResponse | MfaRequiredResponse,
    summary="Connexion utilisateur",
    description="Authentifie l'utilisateur et injecte directement les cookies HttpOnly sécurisés."
)
async def login(
    login_in: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db)
) -> Any:
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    result = await auth_service.authenticate(
        db=db,
        identifier=login_in.identifier,
        password=login_in.password,
        remember_me=login_in.remember_me,
        ip_address=client_ip,
        device_info=user_agent
    )

    # Si le MFA est requis, renvoie le token temporaire de challenge
    if result["status"] == "mfa_required":
        return MfaRequiredResponse(
            status="mfa_required",
            message="Authentification multi-facteurs requise.",
            mfa_token=result["mfa_token"],
            expires_in=result["expires_in"]
        )

    # Injection directe des cookies HttpOnly dans le navigateur
    set_auth_cookies(
        response=response,
        access_token=result["access_token"],
        refresh_token=result["refresh_token"],
        remember_me=result["remember_me"]
    )

    return LoginResponse(
        status="success",
        message="Connexion réussie.",
        user=UserRead.model_validate(result["user"])
    )

@router.post(
    "/mfa/verify",
    response_model=LoginResponse,
    summary="Validation du code MFA",
    description="Vérifie le code TOTP à 6 chiffres et injecte les cookies de session."
)
async def verify_mfa(
    mfa_in: MfaVerifyRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db)
) -> Any:
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    result = await mfa_service.verify_mfa_challenge(
        db=db,
        mfa_token=mfa_in.mfa_token,
        code=mfa_in.code,
        remember_device=mfa_in.remember_device,
        ip_address=client_ip,
        device_info=user_agent
    )

    set_auth_cookies(
        response=response,
        access_token=result["access_token"],
        refresh_token=result["refresh_token"],
        remember_me=False
    )

    return LoginResponse(
        status="success",
        message="Vérification MFA validée avec succès.",
        user=UserRead.model_validate(result["user"])
    )

@router.post(
    "/refresh",
    summary="Rafraîchissement silencieux de session",
    description="Renouvelle l'access_token en arrière-plan sans bloquer l'utilisateur."
)
async def refresh_token(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db)
) -> dict[str, str]:
    refresh_token_cookie = request.cookies.get("refresh_token")
    if not refresh_token_cookie:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Aucun jeton de rafraîchissement trouvé. Veuillez vous reconnecter."
        )

    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    result = await auth_service.refresh_session(
        db=db,
        refresh_token_str=refresh_token_cookie,
        ip_address=client_ip,
        device_info=user_agent
    )

    set_auth_cookies(
        response=response,
        access_token=result["access_token"],
        refresh_token=result["refresh_token"],
        remember_me=result["remember_me"]
    )

    return {"status": "success", "message": "Session renouvelée avec succès."}

@router.post(
    "/logout",
    summary="Déconnexion",
    description="Révoque la session active et supprime les cookies du navigateur."
)
async def logout(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db)
) -> dict[str, str]:
    refresh_token_cookie = request.cookies.get("refresh_token")
    await auth_service.logout(db, refresh_token_cookie)
    clear_auth_cookies(response)
    return {"status": "success", "message": "Déconnexion effectuée avec succès."}

@router.get(
    "/me",
    response_model=UserRead,
    summary="Utilisateur connecté",
    description="Retourne le profil de l'utilisateur actuellement authentifié et actif."
)
async def get_me(
    current_user: User = Depends(require_active_user),
    db: AsyncSession = Depends(get_db)
) -> Any:
    from sqlalchemy import select, func
    from app.models.user.user import UserRole
    from app.models.organization.member import OrganizationMember
    from app.services.organization.organization_service import organization_service

    org = await organization_service.get_user_organization(db=db, user=current_user)

    is_org_owner = False
    is_org_admin = False
    org_role_name = None

    if current_user.role == UserRole.SUPER_ADMIN:
        is_org_admin = True
        org_role_name = "Super Admin"
    elif org:
        is_org_owner = (org.owner_id == current_user.id)
        mem_res = await db.execute(
            select(OrganizationMember).where(
                OrganizationMember.organization_id == org.id,
                (OrganizationMember.user_id == current_user.id) | (func.lower(OrganizationMember.email) == current_user.email.lower())
            )
        )
        mem = mem_res.scalars().first()
        if mem:
            is_org_owner = is_org_owner or getattr(mem, "is_owner", False)
            is_org_admin = is_org_owner or (mem.role_code == "ADM") or ("Administrateur" in (mem.roles_list or []))
            org_role_name = (mem.roles_list[0] if mem.roles_list else mem.role_code) or ("Administrateur" if is_org_owner else "Collaborateur")
        else:
            if is_org_owner:
                is_org_admin = True
                org_role_name = "Propriétaire Fondateur"

    user_dict = {
        "id": current_user.id,
        "email": current_user.email,
        "username": current_user.username,
        "full_name": current_user.full_name,
        "role": current_user.role,
        "status": current_user.status,
        "is_email_verified": current_user.is_email_verified,
        "mfa_enabled": current_user.mfa_enabled,
        "last_login_at": current_user.last_login_at,
        "created_at": current_user.created_at,
        "organization_id": org.id if org else None,
        "organization_name": org.name if org else None,
        "organization_code": org.code if org else None,
        "is_org_owner": is_org_owner,
        "is_org_admin": is_org_admin,
        "org_role_name": org_role_name,
    }
    return UserRead(**user_dict)

