import asyncio
from app.db.database import SessionLocal
from app.models.user.user import User, UserRole, UserStatus
from app.core.security import get_password_hash
from sqlalchemy import select

async def seed_superadmin():
    async with SessionLocal() as db:
        result = await db.execute(
            select(User).where(
                (User.email == "admin@senwisetool.com") | (User.username == "superadmin")
            )
        )
        existing = result.scalars().first()
        if existing:
            existing.status = UserStatus.ACTIVE
            existing.is_email_verified = True
            existing.hashed_password = get_password_hash("Admin@SenWise2026!")
            existing.failed_login_attempts = 0
            existing.locked_until = None
            await db.commit()
            print("Super-Admin user updated successfully!")
            return

        admin_user = User(
            email="admin@senwisetool.com",
            username="superadmin",
            hashed_password=get_password_hash("Admin@SenWise2026!"),
            full_name="Jean Kamga",
            role=UserRole.SUPER_ADMIN,
            status=UserStatus.ACTIVE,
            is_email_verified=True,
            mfa_enabled=False
        )
        db.add(admin_user)
        await db.commit()
        print("Super-Admin user seeded successfully!")

if __name__ == "__main__":
    asyncio.run(seed_superadmin())
