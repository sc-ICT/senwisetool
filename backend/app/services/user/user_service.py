from datetime import datetime, timezone
from fastapi import HTTPException, status
from sqlalchemy import select, or_, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.user.user import User, UserRole, UserStatus
from app.models.organization.organization import Organization
from app.schemas.user.user import UserCreate, UserUpdate
from app.core import security

class UserService:
    @staticmethod
    async def get_by_id(db: AsyncSession, user_id: int) -> User | None:
        result = await db.execute(select(User).where(User.id == user_id))
        return result.scalars().first()

    @staticmethod
    async def get_by_email(db: AsyncSession, email: str) -> User | None:
        result = await db.execute(select(User).where(User.email == email.lower().strip()))
        return result.scalars().first()

    @staticmethod
    async def get_by_username(db: AsyncSession, username: str) -> User | None:
        result = await db.execute(select(User).where(User.username == username.strip()))
        return result.scalars().first()

    @staticmethod
    async def get_by_identifier(db: AsyncSession, identifier: str) -> User | None:
        """
        Recherche un utilisateur par :
        1. Son adresse e-mail de compte (User.email)
        2. Son nom d'utilisateur (User.username)
        3. L'adresse e-mail officielle de contact de son organisation (Organization.contact_email -> owner)
        4. L'adresse e-mail de membre d'organisation (OrganizationMember.email -> user)
        """
        clean_ident = identifier.strip().lower()
        result = await db.execute(
            select(User).where(
                or_(
                    func.lower(User.email) == clean_ident,
                    User.username == identifier.strip()
                )
            )
        )
        user = result.scalars().first()
        if user:
            return user

        # 2. Recherche par adresse e-mail officielle de contact d'organisation
        from app.models.organization.organization import Organization
        res_org = await db.execute(
            select(Organization).where(func.lower(Organization.contact_email) == clean_ident)
        )
        org = res_org.scalars().first()
        if org and org.owner_id:
            res_owner = await db.execute(select(User).where(User.id == org.owner_id))
            owner = res_owner.scalars().first()
            if owner:
                return owner

        # 3. Recherche par adresse e-mail de membre
        from app.models.organization.member import OrganizationMember
        res_mem = await db.execute(
            select(OrganizationMember).where(func.lower(OrganizationMember.email) == clean_ident)
        )
        mem = res_mem.scalars().first()
        if mem and mem.user_id:
            res_mem_user = await db.execute(select(User).where(User.id == mem.user_id))
            mem_user = res_mem_user.scalars().first()
            if mem_user:
                return mem_user

        return None

    @staticmethod
    async def create_user(
        db: AsyncSession, 
        user_in: UserCreate, 
        status: UserStatus = UserStatus.PENDING_VERIFICATION,
        is_email_verified: bool = False
    ) -> User:
        """Crée un nouvel utilisateur avec hachage de mot de passe Argon2."""
        clean_email = user_in.email.lower().strip()

        # Règle d'unicité globale stricte : l'email ne doit pas être déjà utilisé par une organisation
        res_org = await db.execute(
            select(Organization).where(func.lower(Organization.contact_email) == clean_email)
        )
        if res_org.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"L'adresse e-mail '{clean_email}' est déjà enregistrée comme contact officiel d'une organisation dans le système. L'email doit être strictement unique."
            )

        db_user = User(
            email=clean_email,
            username=user_in.username.strip(),
            hashed_password=security.get_password_hash(user_in.password),
            full_name=user_in.full_name,
            role=user_in.role,
            status=status,
            is_email_verified=is_email_verified,
        )
        db.add(db_user)
        await db.commit()
        await db.refresh(db_user)
        return db_user


    @staticmethod
    async def update_profile(db: AsyncSession, user: User, user_in: UserUpdate) -> User:
        """Met à jour les informations du profil utilisateur."""
        if user_in.full_name is not None:
            user.full_name = user_in.full_name.strip()
        if user_in.email is not None and user_in.email.lower().strip() != user.email:
            existing = await UserService.get_by_email(db, user_in.email)
            if existing and existing.id != user.id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cette adresse e-mail est déjà utilisée par un autre compte."
                )
            user.email = user_in.email.lower().strip()
        await db.commit()
        await db.refresh(user)
        return user

    @staticmethod
    async def change_password(
        db: AsyncSession, 
        user: User, 
        current_password: str, 
        new_password: str
    ) -> None:
        """Modifie le mot de passe après vérification stricte de l'ancien mot de passe."""
        if not security.verify_password(current_password, user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Le mot de passe actuel est incorrect."
            )
        if len(new_password) < 8:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Le nouveau mot de passe doit comporter au moins 8 caractères."
            )
        user.hashed_password = security.get_password_hash(new_password)
        await db.commit()

    @staticmethod
    async def update_last_login(db: AsyncSession, user: User) -> None:
        user.last_login_at = datetime.now(timezone.utc)
        await db.commit()

user_service = UserService()
