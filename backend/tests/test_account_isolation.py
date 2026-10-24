import pytest
from httpx import AsyncClient
from datetime import datetime, timezone, timedelta
from app.core import security
from app.models.user.user import User, UserRole, UserStatus
from app.models.organization.organization import Organization, OrgStatusEnum
from app.models.organization.invitation import OrganizationInvitation

@pytest.mark.asyncio
async def test_cross_account_isolation(async_client: AsyncClient, setup_db):
    from tests.conftest import TestingSessionLocal
    
    # 1. Créer deux utilisateurs (User A et User B) et une organisation pour User B
    async with TestingSessionLocal() as session:
        user_a = User(
            email="admin_a@company-a.com",
            username="admin_a",
            hashed_password=security.get_password_hash("PasswordA123!"),
            full_name="Alice Admin",
            role=UserRole.ADMIN_ORG,
            status=UserStatus.ACTIVE,
            is_email_verified=True,
        )
        user_b = User(
            email="admin_b@company-b.com",
            username="admin_b",
            hashed_password=security.get_password_hash("PasswordB123!"),
            full_name="Bob Admin",
            role=UserRole.ADMIN_ORG,
            status=UserStatus.ACTIVE,
            is_email_verified=True,
        )
        session.add_all([user_a, user_b])
        await session.commit()
        await session.refresh(user_a)
        await session.refresh(user_b)

        # Organisation B appartenant à User B
        org_b = Organization(
            name="Organisation B Corp",
            code="SWT-ORGB",
            country="Sénégal",
            type="Entreprise",
            owner_id=user_b.id,
            contact_email="contact@company-b.com",
            status=OrgStatusEnum.REVISION_REQUESTED.value,
            custom_questions=[{"id": "q1", "label": "Question B", "type": "text"}],
            custom_answers={"q1": "Réponse B"},
        )
        session.add(org_b)

        # Invitation destinée à un email invité non encore enregistré
        inv_b = OrganizationInvitation(
            admin_email="target_invited@company-b.com",
            token="token_secret_for_b_12345",
            token_expires_at=datetime.now(timezone.utc) + timedelta(hours=48),
            created_by_id=user_b.id,
            custom_questions=[{"id": "q1", "label": "Question B", "type": "text"}],
        )
        session.add(inv_b)
        await session.commit()
        await session.refresh(org_b)

    token_a = security.create_access_token(
        subject=user_a.id,
        extra_claims={"email": "admin_a@company-a.com", "role": "ADMIN_ORG"}
    )
    headers_a = {"Authorization": f"Bearer {token_a}"}

    # 2. Test 1 : User A tente de consulter le statut du dossier de Org B via son code -> DOIT ÉCHOUER (403)
    res_status = await async_client.get(
        "/api/v1/onboarding/status?code=SWT-ORGB",
        headers=headers_a
    )
    assert res_status.status_code == 403
    assert "Accès non autorisé" in res_status.json()["detail"]

    # 3. Test 2 : User A connecté tente d'activer l'invitation destinée à un autre email -> DOIT ÉCHOUER (403)
    res_verify_token = await async_client.post(
        "/api/v1/onboarding/verify-token",
        json={"token": "token_secret_for_b_12345", "email": "target_invited@company-b.com"},
        headers=headers_a
    )
    assert res_verify_token.status_code == 403
    assert "Conflit de compte" in res_verify_token.json()["detail"]
    assert "Conflit de compte" in res_verify_token.json()["detail"]

    # 4. Test 3 : User A tente de consulter les détails de review de l'Organisation B -> DOIT ÉCHOUER (403)
    res_review = await async_client.get(
        f"/api/v1/onboarding/review-details?org_id={org_b.id}",
        headers=headers_a
    )
    assert res_review.status_code == 403
    assert "Accès non autorisé" in res_review.json()["detail"]

    # 5. Test 4 : User A tente de consulter le brouillon de User B -> DOIT ÉCHOUER (403)
    res_draft = await async_client.get(
        "/api/v1/onboarding/draft?email=admin_b@company-b.com",
        headers=headers_a
    )
    assert res_draft.status_code == 403
    assert "Accès non autorisé" in res_draft.json()["detail"]

    # 6. Test 5 : Un utilisateur anonyme (sans session) tente d'accéder aux détails de review -> DOIT ÉCHOUER (401)
    res_anon_review = await async_client.get(f"/api/v1/onboarding/review-details?org_id={org_b.id}")
    assert res_anon_review.status_code == 401
    assert "Non authentifié" in res_anon_review.json()["detail"]

    # 7. Test 6 : Un utilisateur anonyme tente de soumettre une révision -> DOIT ÉCHOUER (401)
    res_anon_submit = await async_client.post(
        "/api/v1/onboarding/review/submit",
        json={"organization_id": org_b.id, "custom_answers": {"q1": "Hacked response"}}
    )
    assert res_anon_submit.status_code == 401
    assert "Non authentifié" in res_anon_submit.json()["detail"]

    # 8. Test 7 : User A tente de soumettre des réponses pour l'Organisation B -> DOIT ÉCHOUER (403)
    res_a_submit = await async_client.post(
        "/api/v1/onboarding/review/submit",
        json={"organization_id": org_b.id, "custom_answers": {"q1": "Réponse illégitime de A"}},
        headers=headers_a
    )
    assert res_a_submit.status_code == 403
    assert "Accès non autorisé" in res_a_submit.json()["detail"]

    # 9. Test 8 : User B (propriétaire légitime) accède à sa review -> SUCCÈS (200)
    token_b = security.create_access_token(
        subject=user_b.id,
        extra_claims={"email": "admin_b@company-b.com", "role": "ADMIN_ORG"}
    )
    headers_b = {"Authorization": f"Bearer {token_b}"}

    res_b_review = await async_client.get(
        f"/api/v1/onboarding/review-details?org_id={org_b.id}",
        headers=headers_b
    )
    assert res_b_review.status_code == 200
    assert res_b_review.json()["organization_id"] == org_b.id

    # 10. Test 9 : User B (propriétaire légitime) soumet ses réponses -> SUCCÈS (200)
    res_b_submit = await async_client.post(
        "/api/v1/onboarding/review/submit",
        json={"organization_id": org_b.id, "custom_answers": {"q1": "Réponse légitime de B"}},
        headers=headers_b
    )
    assert res_b_submit.status_code == 200
    assert res_b_submit.json()["success"] is True

    # 10b. Règle de non-retransmission de review : User B tente de re-soumettre le review déjà transmis -> ÉCHEC (400)
    res_b_resubmit = await async_client.post(
        "/api/v1/onboarding/review/submit",
        json={"organization_id": org_b.id, "custom_answers": {"q1": "Tentative de ré-expédition"}},
        headers=headers_b
    )
    assert res_b_resubmit.status_code == 400
    assert "déjà été transmis" in res_b_resubmit.json()["detail"]

    # 10c. Règle de non-retransmission d'onboarding : User B tente de re-soumettre la demande alors qu'elle est en cours (PENDING) -> ÉCHEC (400)
    res_b_onboarding_resubmit = await async_client.post(
        "/api/v1/onboarding/submit",
        json={
            "name": "Organisation B Corp",
            "code": "SWT-ORGB",
            "country": "Sénégal",
            "city": "Dakar",
            "type": "Entreprise",
            "plan": "pro",
            "contact_email": "contact@company-b.com",
            "phone": "+221338000000",
            "terms_accepted": True
        },
        headers=headers_b
    )
    assert res_b_onboarding_resubmit.status_code == 400
    assert "déjà été transmis" in res_b_onboarding_resubmit.json()["detail"]

    # 11. Test 10 : Vérification d'état d'invitation avec conflit de compte
    res_inv_conflict = await async_client.get(
        f"/api/v1/onboarding/invitation-status?token=token_secret_for_b_12345",
        headers=headers_a
    )
    assert res_inv_conflict.status_code == 200
    assert res_inv_conflict.json()["status"] == "conflict"

    # 12. Test 11 : L'attestation publique reste accessible pour vérification (pas de fuite de session)
    res_attest = await async_client.get("/api/v1/onboarding/verify-attestation?code=SWT-ORGB")
    assert res_attest.status_code == 200
    assert res_attest.json()["authentic"] is True
    assert res_attest.json()["organization_name"] == "Organisation B Corp"
