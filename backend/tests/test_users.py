import pytest
from httpx import AsyncClient
from app.models.user.user import User, UserRole, UserStatus
from app.core.security import get_password_hash

@pytest.fixture
async def create_profile_test_user(setup_db):
    from tests.conftest import TestingSessionLocal
    async with TestingSessionLocal() as session:
        user = User(
            email="profile_test@senwisetool.com",
            username="profiletester",
            hashed_password=get_password_hash("Password123!"),
            full_name="Profil Test",
            role=UserRole.SUPER_ADMIN,
            status=UserStatus.ACTIVE,
            is_email_verified=True,
            mfa_enabled=False
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user

@pytest.mark.asyncio
async def test_get_and_update_profile(async_client: AsyncClient, create_profile_test_user: User):
    # 1. Connexion pour obtenir le cookie
    login_res = await async_client.post(
        "/api/v1/auth/login",
        json={
            "identifier": "profile_test@senwisetool.com",
            "password": "Password123!",
            "remember_me": False
        }
    )
    assert login_res.status_code == 200

    # 2. Récupération du profil
    me_res = await async_client.get("/api/v1/users/me")
    assert me_res.status_code == 200
    assert me_res.json()["email"] == "profile_test@senwisetool.com"
    assert me_res.json()["full_name"] == "Profil Test"

    # 3. Mise à jour du profil
    update_res = await async_client.put(
        "/api/v1/users/me",
        json={"full_name": "Jean Kamga Mis à Jour"}
    )
    assert update_res.status_code == 200
    assert update_res.json()["full_name"] == "Jean Kamga Mis à Jour"

@pytest.mark.asyncio
async def test_change_password(async_client: AsyncClient, create_profile_test_user: User):
    # 1. Connexion
    await async_client.post(
        "/api/v1/auth/login",
        json={
            "identifier": "profile_test@senwisetool.com",
            "password": "Password123!",
            "remember_me": False
        }
    )

    # 2. Mauvais mot de passe actuel
    bad_res = await async_client.post(
        "/api/v1/users/me/change-password",
        json={
            "current_password": "WrongPassword!",
            "new_password": "NewSecret2026!"
        }
    )
    assert bad_res.status_code == 400

    # 3. Bon mot de passe
    good_res = await async_client.post(
        "/api/v1/users/me/change-password",
        json={
            "current_password": "Password123!",
            "new_password": "NewSecret2026!"
        }
    )
    assert good_res.status_code == 200
    assert good_res.json()["status"] == "success"
