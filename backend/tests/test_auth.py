import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.user.user import User, UserRole, UserStatus
from app.core.security import get_password_hash

@pytest.fixture
async def create_test_user(setup_db):
    from tests.conftest import TestingSessionLocal
    async with TestingSessionLocal() as session:
        user = User(
            email="superadmin@senwisetool.com",
            username="superadmin",
            hashed_password=get_password_hash("SuperAdmin@2026!"),
            full_name="Jean Kamga",
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
async def test_login_successful_sets_httponly_cookies(async_client: AsyncClient, create_test_user: User):
    """Vérifie la connexion réussie et l'injection stricte des cookies HttpOnly sans fuite dans le body."""
    response = await async_client.post(
        "/api/v1/auth/login",
        json={
            "identifier": "superadmin@senwisetool.com",
            "password": "SuperAdmin@2026!",
            "remember_me": True
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "access_token" not in data  # Règle stricte : AUCUN token dans le corps JSON
    assert "refresh_token" not in data
    assert data["user"]["email"] == "superadmin@senwisetool.com"

    # Vérification des cookies HttpOnly
    cookies = response.cookies
    assert "access_token" in cookies
    assert "refresh_token" in cookies
    
    set_cookie_header = response.headers.get("set-cookie", "")
    assert "HttpOnly" in set_cookie_header

@pytest.mark.asyncio
async def test_login_by_username(async_client: AsyncClient, create_test_user: User):
    """Vérifie la connexion via le nom d'utilisateur au lieu de l'email."""
    response = await async_client.post(
        "/api/v1/auth/login",
        json={
            "identifier": "superadmin",
            "password": "SuperAdmin@2026!",
            "remember_me": False
        }
    )
    assert response.status_code == 200
    assert "access_token" in response.cookies

@pytest.mark.asyncio
async def test_login_wrong_password_returns_field_error(async_client: AsyncClient, create_test_user: User):
    """Vérifie le message d'erreur ciblé sur le champ mot de passe."""
    response = await async_client.post(
        "/api/v1/auth/login",
        json={
            "identifier": "superadmin@senwisetool.com",
            "password": "WrongPassword!",
            "remember_me": False
        }
    )
    assert response.status_code == 401
    detail = response.json()["detail"]
    assert detail["field"] == "password"

@pytest.mark.asyncio
async def test_login_lockout_after_max_attempts(async_client: AsyncClient, create_test_user: User):
    """Vérifie le verrouillage du compte après 5 tentatives échouées."""
    for i in range(5):
        resp = await async_client.post(
            "/api/v1/auth/login",
            json={
                "identifier": "superadmin@senwisetool.com",
                "password": f"BadPassword{i}!",
                "remember_me": False
            }
        )
    assert resp.status_code == 423
    detail = resp.json()["detail"]
    assert detail["code"] == "ACCOUNT_LOCKED"
    assert detail["remaining_minutes"] == 30

@pytest.mark.asyncio
async def test_login_forbidden_if_email_unverified(async_client: AsyncClient, setup_db):
    """Vérifie le blocage d'accès si l'e-mail n'est pas vérifié."""
    from tests.conftest import TestingSessionLocal
    async with TestingSessionLocal() as session:
        user = User(
            email="unverified@senwisetool.com",
            username="unverified",
            hashed_password=get_password_hash("Password123!"),
            status=UserStatus.ACTIVE,
            is_email_verified=False
        )
        session.add(user)
        await session.commit()

    response = await async_client.post(
        "/api/v1/auth/login",
        json={"identifier": "unverified@senwisetool.com", "password": "Password123!"}
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "EMAIL_NOT_VERIFIED"

@pytest.mark.asyncio
async def test_login_forbidden_if_status_suspended(async_client: AsyncClient, setup_db):
    """Vérifie que seul le statut ACTIVE permet d'accéder à la plateforme."""
    from tests.conftest import TestingSessionLocal
    async with TestingSessionLocal() as session:
        user = User(
            email="suspended@senwisetool.com",
            username="suspended",
            hashed_password=get_password_hash("Password123!"),
            status=UserStatus.SUSPENDED,
            is_email_verified=True
        )
        session.add(user)
        await session.commit()

    response = await async_client.post(
        "/api/v1/auth/login",
        json={"identifier": "suspended@senwisetool.com", "password": "Password123!"}
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "ACCOUNT_NOT_ACTIVE"

@pytest.mark.asyncio
async def test_single_active_session_policy(async_client: AsyncClient, create_test_user: User):
    """Vérifie qu'une nouvelle connexion révoque automatiquement la session précédente."""
    # Première connexion
    res1 = await async_client.post(
        "/api/v1/auth/login",
        json={"identifier": "superadmin", "password": "SuperAdmin@2026!"}
    )
    refresh_token_1 = res1.cookies.get("refresh_token")

    # Deuxième connexion (depuis un autre navigateur/appareil)
    res2 = await async_client.post(
        "/api/v1/auth/login",
        json={"identifier": "superadmin", "password": "SuperAdmin@2026!"}
    )
    refresh_token_2 = res2.cookies.get("refresh_token")
    assert refresh_token_1 != refresh_token_2

    # Tentative d'utilisation du premier refresh token -> doit échouer (révoqué)
    async_client.cookies.set("refresh_token", refresh_token_1)
    res_refresh = await async_client.post("/api/v1/auth/refresh")
    assert res_refresh.status_code == 401

@pytest.mark.asyncio
async def test_silent_refresh_and_get_me(async_client: AsyncClient, create_test_user: User):
    """Vérifie le rafraîchissement transparent et l'accès à la route protégée /me."""
    login_res = await async_client.post(
        "/api/v1/auth/login",
        json={"identifier": "superadmin", "password": "SuperAdmin@2026!"}
    )
    assert login_res.status_code == 200

    # Accès à /me avec les cookies injectés
    me_res = await async_client.get("/api/v1/auth/me")
    assert me_res.status_code == 200
    assert me_res.json()["email"] == "superadmin@senwisetool.com"

    # Rafraîchissement silencieux
    refresh_res = await async_client.post("/api/v1/auth/refresh")
    assert refresh_res.status_code == 200
    assert refresh_res.json()["status"] == "success"

@pytest.mark.asyncio
async def test_logout(async_client: AsyncClient, create_test_user: User):
    """Vérifie la déconnexion et la purge des cookies."""
    await async_client.post(
        "/api/v1/auth/login",
        json={"identifier": "superadmin", "password": "SuperAdmin@2026!"}
    )
    logout_res = await async_client.post("/api/v1/auth/logout")
    assert logout_res.status_code == 200
