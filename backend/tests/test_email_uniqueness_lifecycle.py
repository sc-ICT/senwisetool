import pytest
from httpx import AsyncClient
from app.models.user import User, UserRole, UserStatus
from app.models.organization import Organization, OrganizationInvitation
from app.core.security import get_password_hash


from tests.conftest import TestingSessionLocal


@pytest.mark.asyncio
async def test_email_uniqueness_enforcement(async_client: AsyncClient):
    """
    Vérifie la règle d'unicité stricte et absolue des adresses email :
    - Une adresse email ne peut JAMAIS appartenir à la fois à un compte utilisateur et une organisation.
    - L'email ne peut être ni réinvité ni réutilisé pour l'onboarding s'il existe déjà dans l'un des registres.
    - La vérification doit impérativement bloquer l'émission d'OTP et la création d'invitations.
    """
    # 1. Création d'un Super Admin et d'un utilisateur standard
    super_admin = User(
        email="superadmin.uniqueness@senwise.org",
        username="superadmin_uniq",
        hashed_password=get_password_hash("SuperSecret123!"),
        full_name="Super Admin Uniq",
        role=UserRole.SUPER_ADMIN,
        status=UserStatus.ACTIVE,
        is_email_verified=True,
    )
    user_existing = User(
        email="existing.user@senwise.org",
        username="existing_user",
        hashed_password=get_password_hash("UserPass123!"),
        full_name="Existing User",
        role=UserRole.ADMIN_ORG,
        status=UserStatus.ACTIVE,
        is_email_verified=True,
    )
    async with TestingSessionLocal() as session:
        session.add(super_admin)
        session.add(user_existing)
        await session.commit()
        await session.refresh(super_admin)
        super_admin_id = super_admin.id

    from app.core.security import create_access_token
    token = create_access_token(
        subject=super_admin_id,
        extra_claims={"email": "superadmin.uniqueness@senwise.org", "role": "SUPER_ADMIN"}
    )
    admin_headers = {"Authorization": f"Bearer {token}"}

    # 2. Vérification de l'endpoint public d'unicité d'email
    # Cas email existant chez un User
    res_check_user = await async_client.get(
        "/api/v1/onboarding/contact-email/check?email=existing.user@senwise.org"
    )
    assert res_check_user.status_code == 200
    assert res_check_user.json()["available"] is False
    assert res_check_user.json()["used_as"] == "user"

    # 3. Super Admin tente d'inviter une organisation avec l'email d'un utilisateur existant
    # Doit être REJETÉ STRICTEMENT AVANT l'émission de toute invitation
    res_invite_dup_user = await async_client.post(
        "/api/v1/organizations/invitations/",
        json={"admin_email": "existing.user@senwise.org", "expiration_hours": 48},
        headers=admin_headers
    )
    assert res_invite_dup_user.status_code == 400
    assert "déjà utilisée par un compte utilisateur" in res_invite_dup_user.json()["detail"]

    # 4. Super Admin invite une nouvelle adresse propre
    clean_invite_email = "new.candidate.admin@agroforet.sn"
    res_invite_ok = await async_client.post(
        "/api/v1/organizations/invitations/",
        json={"admin_email": clean_invite_email, "expiration_hours": 48},
        headers=admin_headers
    )
    assert res_invite_ok.status_code == 201

    # 5. L'email propre fait maintenant l'objet d'une invitation en cours
    # Vérification que l'endpoint d'unicité détecte l'invitation
    res_check_inv = await async_client.get(
        f"/api/v1/onboarding/contact-email/check?email={clean_invite_email}"
    )
    assert res_check_inv.status_code == 200
    assert res_check_inv.json()["available"] is False
    assert res_check_inv.json()["used_as"] == "invitation"

    # Tentative de ré-inviter la même adresse -> Rejet strict !
    res_reinvite = await async_client.post(
        "/api/v1/organizations/invitations/",
        json={"admin_email": clean_invite_email, "expiration_hours": 48},
        headers=admin_headers
    )
    assert res_reinvite.status_code == 400
    assert "invitation est déjà en cours" in res_reinvite.json()["detail"]

    # 6. Tentative d'émission d'OTP de contact sur un email d'utilisateur existant -> BLOQUÉ
    res_otp_user_dup = await async_client.post(
        "/api/v1/onboarding/contact-email/send-otp",
        json={"contact_email": "existing.user@senwise.org"}
    )
    assert res_otp_user_dup.status_code == 400
    assert "déjà utilisée par un compte utilisateur" in res_otp_user_dup.json()["detail"]

    # 7. Tentative d'émission d'OTP de contact sur un email d'invitation existant -> BLOQUÉ
    res_otp_inv_dup = await async_client.post(
        "/api/v1/onboarding/contact-email/send-otp",
        json={"contact_email": clean_invite_email}
    )
    assert res_otp_inv_dup.status_code == 400
    assert "fait déjà l'objet d'une invitation" in res_otp_inv_dup.json()["detail"]
