import secrets
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from fastapi import HTTPException, status
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import logging
from app.models.organization.organization import Organization
from app.models.organization.role import OrganizationRole
from app.models.organization.member import OrganizationMember, MemberStatus
from app.models.user.user import User, UserRole, UserStatus
from app.models.auth.refresh_token import RefreshToken
from app.core import security
from app.core.config import settings
from app.services.email.email_service import email_service
from app.schemas.organization.member import MemberInviteRequest, MemberUpdateRequest
from app.schemas.organization.role import RoleCreateRequest, RoleUpdateRequest


DEFAULT_SYSTEM_ROLES = [
    {
        "name": "Administrateur",
        "code": "ADM",
        "description": "Administration complète de l'organisation, des membres et de la sécurité",
        "is_system": True,
        "permissions": {
            "users": {"view": "active", "create_edit": "active", "delete": "active"},
            "projects": {"view": "active", "create_edit": "active", "delete": "active"},
            "campaigns": {"view": "active", "create_edit": "active", "delete": "active"},
            "forms": {"view": "active", "create_edit": "active", "delete": "active"},
            "resources": {"view": "active", "create_edit": "active", "delete": "active"},
        }
    },
    {
        "name": "Manager projet",
        "code": "MGR",
        "description": "Supervision des projets, des campagnes et coordination des équipes",
        "is_system": True,
        "permissions": {
            "users": {"view": "active", "create_edit": "partial", "delete": "inactive"},
            "projects": {"view": "active", "create_edit": "active", "delete": "partial"},
            "campaigns": {"view": "active", "create_edit": "active", "delete": "partial"},
            "forms": {"view": "active", "create_edit": "active", "delete": "partial"},
            "resources": {"view": "active", "create_edit": "partial", "delete": "inactive"},
        }
    },
    {
        "name": "Opérateur terrain",
        "code": "OPR",
        "description": "Accès direct aux missions, saisie et collecte sur formulaires terrain",
        "is_system": True,
        "permissions": {
            "users": {"view": "inactive", "create_edit": "inactive", "delete": "inactive"},
            "projects": {"view": "active", "create_edit": "partial", "delete": "inactive"},
            "campaigns": {"view": "active", "create_edit": "inactive", "delete": "inactive"},
            "forms": {"view": "active", "create_edit": "active", "delete": "inactive"},
            "resources": {"view": "inactive", "create_edit": "inactive", "delete": "inactive"},
        }
    },
    {
        "name": "Auditeur",
        "code": "AUD",
        "description": "Contrôle qualité, conformité technique et revue des jeux de données",
        "is_system": True,
        "permissions": {
            "users": {"view": "active", "create_edit": "inactive", "delete": "inactive"},
            "projects": {"view": "active", "create_edit": "inactive", "delete": "inactive"},
            "campaigns": {"view": "active", "create_edit": "inactive", "delete": "inactive"},
            "forms": {"view": "active", "create_edit": "inactive", "delete": "inactive"},
            "resources": {"view": "active", "create_edit": "inactive", "delete": "inactive"},
        }
    },
    {
        "name": "Consultant",
        "code": "CNS",
        "description": "Consultation avancée, analyses thématiques et rapports d'expertise",
        "is_system": True,
        "permissions": {
            "users": {"view": "inactive", "create_edit": "inactive", "delete": "inactive"},
            "projects": {"view": "active", "create_edit": "inactive", "delete": "inactive"},
            "campaigns": {"view": "active", "create_edit": "inactive", "delete": "inactive"},
            "forms": {"view": "active", "create_edit": "inactive", "delete": "inactive"},
            "resources": {"view": "active", "create_edit": "inactive", "delete": "inactive"},
        }
    },
    {
        "name": "Lecteur",
        "code": "LCR",
        "description": "Consultation en lecture seule des synthèses et tableaux de bord",
        "is_system": True,
        "permissions": {
            "users": {"view": "inactive", "create_edit": "inactive", "delete": "inactive"},
            "projects": {"view": "active", "create_edit": "inactive", "delete": "inactive"},
            "campaigns": {"view": "active", "create_edit": "inactive", "delete": "inactive"},
            "forms": {"view": "active", "create_edit": "inactive", "delete": "inactive"},
            "resources": {"view": "active", "create_edit": "inactive", "delete": "inactive"},
        }
    },
]

class MemberService:

    async def ensure_roles_seeded(self, db: AsyncSession, org_id: int):
        """Initialise les rôles par défaut de l'organisation s'ils n'existent pas encore."""
        result = await db.execute(
            select(OrganizationRole).where(OrganizationRole.organization_id == org_id)
        )
        existing = result.scalars().all()
        if not existing:
            for r_data in DEFAULT_SYSTEM_ROLES:
                role = OrganizationRole(
                    organization_id=org_id,
                    name=r_data["name"],
                    code=r_data["code"],
                    description=r_data["description"],
                    is_system=r_data["is_system"],
                    status="active",
                    permissions=r_data["permissions"]
                )
                db.add(role)
            await db.commit()

    async def ensure_owner_is_member(self, db: AsyncSession, org: Organization):
        """S'assure que le créateur/propriétaire de l'organisation est enregistré comme membre Administrateur et Propriétaire."""
        if not org.owner_id:
            return

        result = await db.execute(
            select(OrganizationMember).where(
                OrganizationMember.organization_id == org.id,
                or_(
                    OrganizationMember.user_id == org.owner_id,
                    OrganizationMember.email == org.contact_email
                )
            )
        )
        existing = result.scalars().first()
        if not existing:
            # Récupérer l'utilisateur
            user_res = await db.execute(select(User).where(User.id == org.owner_id))
            user = user_res.scalars().first()

            # Récupérer le rôle ADM
            role_res = await db.execute(
                select(OrganizationRole).where(
                    OrganizationRole.organization_id == org.id,
                    OrganizationRole.code == "ADM"
                )
            )
            adm_role = role_res.scalars().first()

            owner_member = OrganizationMember(
                organization_id=org.id,
                user_id=org.owner_id,
                email=user.email if user else org.contact_email,
                name=user.full_name if user else org.name,
                role_id=adm_role.id if adm_role else None,
                role_code="ADM",
                is_owner=True,
                roles_list=["Propriétaire", "Administrateur"],
                accessible_projects=["Tous les projets"],
                status=MemberStatus.ACTIF.value,
                mfa_enabled=user.mfa_enabled if user else False,
                joined_at=org.created_at or datetime.now(timezone.utc),
                last_login_at=user.last_login_at if user else datetime.now(timezone.utc),
                last_device="Chrome - macOS"
            )
            db.add(owner_member)
            await db.commit()
        else:
            changed = False
            if not getattr(existing, "is_owner", False):
                existing.is_owner = True
                changed = True
            current_roles = list(existing.roles_list or [])
            if "Propriétaire" not in current_roles:
                existing.roles_list = ["Propriétaire"] + [r for r in current_roles if r != "Propriétaire"]
                changed = True
            if changed:
                await db.commit()

    async def validate_single_organization_rule(
        self,
        db: AsyncSession,
        email: str,
        target_org_id: int
    ):
        """
        Règle stricte de gouvernance multi-tenancy :
        UN UTILISATEUR NE PEUT PAS ÊTRE DANS PLUSIEURS ORGANISATIONS À LA FOIS.
        Vérifie qu'aucun compte ou membre avec cet email n'est rattaché à une autre organisation.
        """
        normalized_email = email.strip().lower()

        # 1. Vérifier si l'email correspond à un User qui possède une AUTRE organisation
        user_res = await db.execute(select(User).where(func.lower(User.email) == normalized_email))
        user = user_res.scalars().first()

        if user:
            org_owned_res = await db.execute(
                select(Organization).where(
                    Organization.owner_id == user.id,
                    Organization.id != target_org_id
                )
            )
            other_org = org_owned_res.scalars().first()
            if other_org:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"L'utilisateur avec l'adresse e-mail '{email}' est déjà propriétaire de l'organisation '{other_org.name}'. Un utilisateur ne peut pas appartenir à plusieurs organisations à la fois."
                )

        # 2. Vérifier si l'email est déjà membre d'une AUTRE organisation
        member_res = await db.execute(
            select(OrganizationMember)
            .options(selectinload(OrganizationMember.organization))
            .where(
                func.lower(OrganizationMember.email) == normalized_email,
                OrganizationMember.organization_id != target_org_id
            )
        )
        other_member = member_res.scalars().first()
        if other_member:
            other_org_name = other_member.organization.name if other_member.organization else "une autre organisation"
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"L'utilisateur avec l'adresse e-mail '{email}' est déjà membre de l'organisation '{other_org_name}'. Un utilisateur ne peut pas appartenir à plusieurs organisations simultanément."
            )

        # 3. Vérifier si l'email est déjà membre de la MÊME organisation
        same_org_res = await db.execute(
            select(OrganizationMember).where(
                func.lower(OrganizationMember.email) == normalized_email,
                OrganizationMember.organization_id == target_org_id
            )
        )
        if same_org_res.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Un membre avec l'adresse e-mail '{email}' existe déjà au sein de cette organisation."
            )

    async def list_members(
        self,
        db: AsyncSession,
        org_id: int,
        status_filter: Optional[str] = None,
        search: Optional[str] = None,
        skip: int = 0,
        limit: int = 50
    ) -> List[OrganizationMember]:
        await self.ensure_roles_seeded(db, org_id)

        # Récupérer l'organisation pour vérifier l'owner
        org_res = await db.execute(select(Organization).where(Organization.id == org_id))
        org = org_res.scalars().first()
        if org:
            await self.ensure_owner_is_member(db, org)

        query = select(OrganizationMember).where(OrganizationMember.organization_id == org_id)

        if status_filter and status_filter.lower() != "tous":
            sf = status_filter.lower()
            if sf == "actifs":
                query = query.where(OrganizationMember.status == "actif")
            elif sf == "suspendus":
                query = query.where(OrganizationMember.status == "suspendu")
            elif sf == "invités" or sf == "invites":
                query = query.where(OrganizationMember.status == "invite")
            else:
                query = query.where(OrganizationMember.status == sf)

        if search:
            s = f"%{search.strip().lower()}%"
            query = query.where(
                or_(
                    func.lower(OrganizationMember.email).like(s),
                    func.lower(OrganizationMember.name).like(s),
                    func.lower(OrganizationMember.role_code).like(s)
                )
            )

        query = query.order_by(OrganizationMember.id.desc()).offset(skip).limit(limit)
        result = await db.execute(query)
        members = result.scalars().all()
        for m in members:
            if org and (m.user_id == org.owner_id or m.email == org.contact_email):
                m.is_owner = True
            elif not getattr(m, "is_owner", False):
                m.is_owner = False
        return members

    async def invite_member(
        self,
        db: AsyncSession,
        org_id: int,
        data: MemberInviteRequest
    ) -> OrganizationMember:
        # Contrôle strict d'appartenance unique
        await self.validate_single_organization_rule(db, data.email, org_id)

        # Vérifier si l'utilisateur existe déjà dans SWT
        user_res = await db.execute(
            select(User).where(func.lower(User.email) == data.email.strip().lower())
        )
        existing_user = user_res.scalars().first()

        primary_role_name = data.roles[0] if data.roles else "Opérateur terrain"
        # Chercher le rôle local
        role_res = await db.execute(
            select(OrganizationRole).where(
                OrganizationRole.organization_id == org_id,
                OrganizationRole.name == primary_role_name
            )
        )
        role = role_res.scalars().first()
        role_code = role.code if role else "OPR"

        member = OrganizationMember(
            organization_id=org_id,
            user_id=existing_user.id if existing_user else None,
            email=data.email.strip().lower(),
            name=data.name or (existing_user.full_name if existing_user else data.email.split("@")[0]),
            role_id=role.id if role else None,
            role_code=role_code,
            is_owner=False,
            roles_list=data.roles if data.roles else [primary_role_name],
            accessible_projects=data.accessible_projects if data.accessible_projects else ["Tous les projets"],
            status=MemberStatus.INVITE.value,
            mfa_enabled=data.mfa_enabled,
            custom_invitation=data.custom_invitation,
            invitation_message=data.message,
            invitation_token=secrets.token_urlsafe(32),
            invited_at=datetime.now(timezone.utc),
            last_device=None,
            last_login_at=None
        )

        db.add(member)

        # Mettre à jour le compteur de membres de l'organisation
        org_res = await db.execute(select(Organization).where(Organization.id == org_id))
        org = org_res.scalars().first()
        if org:
            org.members_count = (org.members_count or 0) + 1

        await db.commit()
        await db.refresh(member)

        # Envoi immédiat de l'e-mail officiel d'invitation avec le lien sécurisé d'activation
        invite_url = f"{settings.FRONTEND_URL}/fr/invitations/accept?token={member.invitation_token}"
        try:
            await email_service.send_member_invitation_email(
                to_email=member.email,
                member_name=member.name,
                org_name=org.name if org else "SenWiseTool",
                role_name=primary_role_name,
                invite_url=invite_url,
                personal_message=data.message if data.custom_invitation else None,
                mfa_required=data.mfa_enabled
            )
        except Exception as exc:
            logging.error(f"[INVITE_MEMBER] Erreur lors de l'envoi de l'email d'invitation à {member.email}: {exc}")

        return member

    async def resend_member_invitation(
        self,
        db: AsyncSession,
        org_id: int,
        member_id: int
    ) -> OrganizationMember:
        """
        Régénère un nouveau jeton d'invitation sécurisé et renvoie l'e-mail d'invitation
        à un collaborateur dont le statut est 'invite' (non encore activé).
        """
        res = await db.execute(
            select(OrganizationMember).where(
                OrganizationMember.id == member_id,
                OrganizationMember.organization_id == org_id
            )
        )
        member = res.scalars().first()
        if not member:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membre introuvable.")

        if member.status != MemberStatus.INVITE.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Ce membre a déjà activé son compte ou n'est plus en attente d'activation."
            )

        # Générer un nouveau jeton d'invitation sécurisé et actualiser l'horodatage
        member.invitation_token = secrets.token_urlsafe(32)
        member.invited_at = datetime.now(timezone.utc)

        db.add(member)
        await db.commit()
        await db.refresh(member)

        # Récupération de l'organisation pour le nom affiché dans l'e-mail
        org_res = await db.execute(select(Organization).where(Organization.id == org_id))
        org = org_res.scalars().first()

        primary_role_name = (member.roles_list[0] if member.roles_list else member.role_code) or "Opérateur terrain"
        invite_url = f"{settings.FRONTEND_URL}/fr/invitations/accept?token={member.invitation_token}"

        try:
            await email_service.send_member_invitation_email(
                to_email=member.email,
                member_name=member.name,
                org_name=org.name if org else "SenWiseTool",
                role_name=primary_role_name,
                invite_url=invite_url,
                personal_message=member.invitation_message if member.custom_invitation else None,
                mfa_required=member.mfa_enabled
            )
        except Exception as exc:
            logging.error(f"[RESEND_INVITE_MEMBER] Erreur lors du renvoi de l'email d'invitation à {member.email}: {exc}")

        return member

    async def update_member(
        self,
        db: AsyncSession,
        org_id: int,
        member_id: int,
        data: MemberUpdateRequest
    ) -> OrganizationMember:
        res = await db.execute(
            select(OrganizationMember).where(
                OrganizationMember.id == member_id,
                OrganizationMember.organization_id == org_id
            )
        )
        member = res.scalars().first()
        if not member:
            raise HTTPException(status_code=404, detail="Membre introuvable.")

        # Protection du propriétaire fondateur contre la suspension
        org_res = await db.execute(select(Organization).where(Organization.id == org_id))
        org = org_res.scalars().first()
        is_org_owner = (org and (member.user_id == org.owner_id or member.email == org.contact_email)) or getattr(member, "is_owner", False)

        if is_org_owner and data.status == "suspendu":
            raise HTTPException(
                status_code=400,
                detail="Impossible de suspendre le compte du Propriétaire Fondateur de l'organisation."
            )

        if data.name is not None:
            member.name = data.name
        if data.roles is not None:
            member.roles_list = data.roles
            if len(data.roles) > 0:
                # Mettre à jour role_code
                primary = data.roles[0]
                role_res = await db.execute(
                    select(OrganizationRole).where(
                        OrganizationRole.organization_id == org_id,
                        OrganizationRole.name == primary
                    )
                )
                r = role_res.scalars().first()
                if r:
                    member.role_id = r.id
                    member.role_code = r.code
        if data.accessible_projects is not None:
            member.accessible_projects = data.accessible_projects
        if data.status is not None:
            member.status = data.status
        if data.mfa_enabled is not None:
            member.mfa_enabled = data.mfa_enabled

        await db.commit()
        await db.refresh(member)
        return member

    async def delete_member(self, db: AsyncSession, org_id: int, member_id: int):
        res = await db.execute(
            select(OrganizationMember).where(
                OrganizationMember.id == member_id,
                OrganizationMember.organization_id == org_id
            )
        )
        member = res.scalars().first()
        if not member:
            raise HTTPException(status_code=404, detail="Membre introuvable.")

        # Protection absolue du propriétaire fondateur
        org_res = await db.execute(select(Organization).where(Organization.id == org_id))
        org = org_res.scalars().first()
        if (org and (member.user_id == org.owner_id or member.email == org.contact_email)) or getattr(member, "is_owner", False):
            raise HTTPException(
                status_code=400,
                detail="Impossible de retirer le Propriétaire Fondateur de l'organisation."
            )

        await db.delete(member)
        if org and org.members_count > 1:
            org.members_count -= 1
        await db.commit()

    async def verify_member_invitation(self, db: AsyncSession, token: str) -> Dict[str, Any]:
        """Vérifie la validité d'un jeton d'invitation de membre."""
        if not token or not token.strip():
            raise HTTPException(status_code=400, detail="Jeton d'invitation manquant.")

        res = await db.execute(
            select(OrganizationMember)
            .options(selectinload(OrganizationMember.organization))
            .where(OrganizationMember.invitation_token == token.strip())
        )
        member = res.scalars().first()
        if not member:
            raise HTTPException(
                status_code=404,
                detail="Lien d'invitation invalide ou expiré."
            )

        if member.status == MemberStatus.ACTIF.value:
            raise HTTPException(
                status_code=400,
                detail="Cette invitation a déjà été acceptée. Vous pouvez vous connecter directement."
            )

        # Vérifier si l'utilisateur existe déjà
        user_res = await db.execute(
            select(User).where(func.lower(User.email) == member.email.lower())
        )
        user = user_res.scalars().first()

        role_display = member.roles_list[0] if member.roles_list else "Opérateur terrain"

        return {
            "valid": True,
            "email": member.email,
            "name": member.name,
            "organization_id": member.organization_id,
            "organization_name": member.organization.name if member.organization else "SenWiseTool",
            "organization_code": member.organization.code if member.organization else None,
            "role_name": role_display,
            "role_code": member.role_code,
            "mfa_required": member.mfa_enabled,
            "user_exists": user is not None
        }

    async def accept_member_invitation(
        self,
        db: AsyncSession,
        token: str,
        password: str,
        full_name: Optional[str] = None,
        ip_address: Optional[str] = None,
        device_info: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Finalise l'acceptation de l'invitation :
        - Crée ou met à jour le compte utilisateur SWT
        - Définit le mot de passe sécurisé
        - Active le statut de membre
        - Génère la session authentifiée (access_token + refresh_token)
        """
        if not token or not token.strip():
            raise HTTPException(status_code=400, detail="Jeton d'invitation manquant.")
        if not password or len(password) < 8:
            raise HTTPException(status_code=400, detail="Le mot de passe doit comporter au moins 8 caractères.")

        res = await db.execute(
            select(OrganizationMember)
            .options(selectinload(OrganizationMember.organization))
            .where(OrganizationMember.invitation_token == token.strip())
        )
        member = res.scalars().first()
        if not member:
            raise HTTPException(
                status_code=404,
                detail="Lien d'invitation invalide ou expiré."
            )

        if member.status == MemberStatus.ACTIF.value:
            raise HTTPException(
                status_code=400,
                detail="Cette invitation a déjà été acceptée. Vous pouvez vous connecter directement."
            )

        now = datetime.now(timezone.utc)

        # 1. Vérifier si un User existe déjà
        user_res = await db.execute(
            select(User).where(func.lower(User.email) == member.email.lower())
        )
        user = user_res.scalars().first()

        # Rôle SWT correspondant
        assigned_role = UserRole.ADMIN_ORG if member.role_code == "ADM" else (
            UserRole.MANAGER if member.role_code == "MGR" else (
                UserRole.OPERATOR if member.role_code == "OPR" else (
                    UserRole.AUDITOR if member.role_code == "AUD" else UserRole.USER
                )
            )
        )

        display_name = full_name.strip() if full_name and full_name.strip() else (member.name or member.email.split("@")[0])

        if user:
            user.hashed_password = security.get_password_hash(password)
            user.is_email_verified = True
            user.status = UserStatus.ACTIVE
            if not user.full_name or user.full_name == user.email.split("@")[0]:
                user.full_name = display_name
            if member.mfa_enabled:
                user.mfa_enabled = True
        else:
            base_username = member.email.split("@")[0][:40]
            candidate_username = base_username
            idx = 1
            while True:
                existing_u = await db.execute(select(User).where(User.username == candidate_username))
                if not existing_u.scalars().first():
                    break
                candidate_username = f"{base_username}_{idx}"
                idx += 1

            user = User(
                email=member.email.lower(),
                username=candidate_username,
                hashed_password=security.get_password_hash(password),
                full_name=display_name,
                role=assigned_role,
                status=UserStatus.ACTIVE,
                is_email_verified=True,
                mfa_enabled=member.mfa_enabled,
                failed_login_attempts=0
            )
            db.add(user)
            await db.flush()

        # Nettoyage et formatage du terminal/appareil
        clean_device = "Navigateur Web"
        if device_info:
            ua_lower = device_info.lower()
            browser = "Navigateur"
            if "edg" in ua_lower:
                browser = "Edge"
            elif "chrome" in ua_lower:
                browser = "Chrome"
            elif "safari" in ua_lower:
                browser = "Safari"
            elif "firefox" in ua_lower:
                browser = "Firefox"

            os_name = "Web"
            if "mac" in ua_lower:
                os_name = "macOS"
            elif "win" in ua_lower:
                os_name = "Windows"
            elif "linux" in ua_lower:
                os_name = "Linux"
            elif "android" in ua_lower:
                os_name = "Android"
            elif "iphone" in ua_lower or "ipad" in ua_lower:
                os_name = "iOS"

            clean_device = f"{browser} - {os_name}"

        # 2. Mettre à jour le membre
        member.user_id = user.id
        member.name = user.full_name or display_name
        member.status = MemberStatus.ACTIF.value
        member.joined_at = now
        member.invitation_token = None
        member.last_login_at = now
        member.last_device = clean_device

        # 3. Créer tokens de session
        access_token = security.create_access_token(
            subject=user.id,
            extra_claims={"email": user.email, "role": user.role.value, "status": user.status.value}
        )
        refresh_token, expires_at = security.create_refresh_token(subject=user.id, remember_me=True)

        db_refresh = RefreshToken(
            user_id=user.id,
            token_hash=security.hash_token(refresh_token),
            device_info=clean_device,
            ip_address=ip_address,
            expires_at=expires_at,
            is_revoked=False
        )
        db.add(db_refresh)


        await db.commit()
        await db.refresh(user)

        is_admin_member = member.role_code == "ADM" or "Administrateur" in (member.roles_list or [])
        redirect_url = "/dashboard/my-organization" if is_admin_member else "/dashboard"

        return {
            "status": "success",
            "message": "Votre compte a été activé avec succès.",
            "access_token": access_token,
            "refresh_token": refresh_token,
            "user": user,
            "redirect_url": redirect_url
        }


    # Gestion des rôles locaux
    async def list_roles(self, db: AsyncSession, org_id: int) -> List[Dict[str, Any]]:
        await self.ensure_roles_seeded(db, org_id)

        result = await db.execute(
            select(OrganizationRole).where(OrganizationRole.organization_id == org_id)
        )
        roles = result.scalars().all()

        # Calculer le nombre de membres par rôle
        roles_with_counts = []
        for role in roles:
            count_res = await db.execute(
                select(func.count(OrganizationMember.id)).where(
                    OrganizationMember.organization_id == org_id,
                    or_(
                        OrganizationMember.role_id == role.id,
                        OrganizationMember.role_code == role.code
                    )
                )
            )
            count = count_res.scalar() or 0
            roles_with_counts.append({
                "id": role.id,
                "organization_id": role.organization_id,
                "name": role.name,
                "code": role.code,
                "description": role.description,
                "is_system": role.is_system,
                "status": role.status,
                "permissions": role.permissions or {},
                "members_count": count,
                "created_at": role.created_at
            })
        return roles_with_counts

    async def create_role(
        self,
        db: AsyncSession,
        org_id: int,
        data: RoleCreateRequest
    ) -> OrganizationRole:
        # Vérifier unicité du code au sein de l'organisation
        res = await db.execute(
            select(OrganizationRole).where(
                OrganizationRole.organization_id == org_id,
                OrganizationRole.code == data.code.strip().upper()
            )
        )
        if res.scalars().first():
            raise HTTPException(
                status_code=400,
                detail=f"Un rôle avec le code '{data.code}' existe déjà."
            )

        role = OrganizationRole(
            organization_id=org_id,
            name=data.name.strip(),
            code=data.code.strip().upper(),
            description=data.description,
            is_system=False,
            status="active",
            permissions=data.permissions
        )
        db.add(role)
        await db.commit()
        await db.refresh(role)
        return role

    async def update_role(
        self,
        db: AsyncSession,
        org_id: int,
        role_id: int,
        data: RoleUpdateRequest
    ) -> OrganizationRole:
        res = await db.execute(
            select(OrganizationRole).where(
                OrganizationRole.id == role_id,
                OrganizationRole.organization_id == org_id
            )
        )
        role = res.scalars().first()
        if not role:
            raise HTTPException(status_code=404, detail="Rôle introuvable.")

        if data.name is not None:
            role.name = data.name.strip()
        if data.description is not None:
            role.description = data.description
        if data.status is not None:
            role.status = data.status
        if data.permissions is not None:
            role.permissions = data.permissions

        await db.commit()
        await db.refresh(role)
        return role

    async def delete_role(self, db: AsyncSession, org_id: int, role_id: int):
        res = await db.execute(
            select(OrganizationRole).where(
                OrganizationRole.id == role_id,
                OrganizationRole.organization_id == org_id
            )
        )
        role = res.scalars().first()
        if not role:
            raise HTTPException(status_code=404, detail="Rôle introuvable.")
        if role.is_system:
            raise HTTPException(
                status_code=400,
                detail="Impossible de supprimer un rôle système standard."
            )

        await db.delete(role)
        await db.commit()

    async def get_permissions_matrix(self, db: AsyncSession, org_id: int) -> Dict[str, Any]:
        """Retourne la grille complète de la matrice des permissions pour l'organisation."""
        roles_list = await self.list_roles(db, org_id)
        
        matrix_definitions = [
            {
                "category": "Gestion des utilisateurs",
                "key": "users",
                "actions": [
                    {"id": "view", "label": "Voir utilisateurs"},
                    {"id": "create_edit", "label": "Créer / Modifier"},
                    {"id": "delete", "label": "Supprimer"},
                ]
            },
            {
                "category": "Gestion des projets",
                "key": "projects",
                "actions": [
                    {"id": "view", "label": "Voir projets"},
                    {"id": "create_edit", "label": "Créer / Modifier"},
                    {"id": "delete", "label": "Supprimer"},
                ]
            },
            {
                "category": "Gestion des campagnes",
                "key": "campaigns",
                "actions": [
                    {"id": "view", "label": "Voir campagnes"},
                    {"id": "create_edit", "label": "Créer / Modifier"},
                    {"id": "delete", "label": "Supprimer"},
                ]
            },
            {
                "category": "Gestion des formulaires",
                "key": "forms",
                "actions": [
                    {"id": "view", "label": "Voir formulaires"},
                    {"id": "create_edit", "label": "Créer / Modifier"},
                    {"id": "delete", "label": "Supprimer"},
                ]
            },
            {
                "category": "Gestion des ressources",
                "key": "resources",
                "actions": [
                    {"id": "view", "label": "Voir ressources"},
                    {"id": "create_edit", "label": "Gérer ressources"},
                ]
            },
        ]

        return {
            "roles": roles_list,
            "definitions": matrix_definitions,
        }

member_service = MemberService()
