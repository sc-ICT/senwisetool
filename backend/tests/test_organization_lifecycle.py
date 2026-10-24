import pytest
from httpx import AsyncClient
from datetime import datetime, timezone, timedelta
from app.core import security
from app.models.user.user import User, UserRole, UserStatus
from app.models.organization.organization import Organization, OrgStatusEnum
from app.models.organization.invitation import OrganizationInvitation

@pytest.mark.asyncio
async def test_full_organization_onboarding_and_review_lifecycle(async_client: AsyncClient, setup_db):
    from tests.conftest import TestingSessionLocal
    
    # 1. Création d'un Super Admin en base de test
    async with TestingSessionLocal() as session:
        super_admin = User(
            email="superadmin@senwisetool.com",
            username="super_admin_test",
            hashed_password=security.get_password_hash("SuperAdminSecure123!"),
            full_name="Jean SuperAdmin",
            role=UserRole.SUPER_ADMIN,
            status=UserStatus.ACTIVE,
            is_email_verified=True,
        )
        session.add(super_admin)
        await session.commit()
        await session.refresh(super_admin)
        super_admin_id = super_admin.id

    super_admin_token = security.create_access_token(
        subject=super_admin_id,
        extra_claims={"email": "superadmin@senwisetool.com", "role": "SUPER_ADMIN"}
    )
    admin_headers = {"Authorization": f"Bearer {super_admin_token}"}

    # 2. Super Admin émet une invitation avec Form Builder (questionnaire spécifique)
    target_email = "directeur@agro-senegal.sn"
    invite_payload = {
        "admin_email": target_email,
        "expiration_hours": 48,
        "custom_questions": [
            {
                "id": "q1",
                "label": "Attestation de conformité PIRE 14 dBm",
                "type": "file",
                "is_required": True,
                "help_text": "PDF certifié des passerelles Kerlink"
            },
            {
                "id": "q2",
                "label": "Zone forestière d'intervention",
                "type": "select",
                "is_required": True,
                "options": ["Zone Nord", "Basse Casamance", "Niokolo Koba"]
            }
        ]
    }

    res_invite = await async_client.post(
        "/api/v1/organizations/invitations/",
        json=invite_payload,
        headers=admin_headers
    )
    assert res_invite.status_code == 201
    inv_data = res_invite.json()
    token = inv_data["token"]
    assert token is not None
    assert inv_data["admin_email"] == target_email
    assert len(inv_data["custom_questions"]) == 2

    # 3. Première connexion candidat : Vérification du token et de l'email
    # Test avec mauvais email
    res_wrong_email = await async_client.post(
        "/api/v1/onboarding/verify-token",
        json={"token": token, "email": "wrong@email.com"}
    )
    assert res_wrong_email.status_code == 400

    # Vérification avec le bon email
    res_verify = await async_client.post(
        "/api/v1/onboarding/verify-token",
        json={"token": token, "email": target_email}
    )
    assert res_verify.status_code == 200
    verify_data = res_verify.json()
    assert verify_data["valid"] is True
    assert verify_data["otp_sent"] is True
    dev_otp = verify_data["dev_otp"]
    assert len(dev_otp) == 6

    # 4. Validation OTP
    # Erreur sur mauvais OTP
    res_wrong_otp = await async_client.post(
        "/api/v1/onboarding/verify-otp",
        json={"token": token, "email": target_email, "otp": "000000"}
    )
    assert res_wrong_otp.status_code == 400

    # Bon OTP
    res_valid_otp = await async_client.post(
        "/api/v1/onboarding/verify-otp",
        json={"token": token, "email": target_email, "otp": dev_otp}
    )
    assert res_valid_otp.status_code == 200
    session_ticket = res_valid_otp.json()["session_ticket"]
    assert session_ticket is not None
    assert "onboarding_ticket" in res_valid_otp.cookies

    # 5. Définition du mot de passe initial et création du compte Org Admin
    res_setup_pwd = await async_client.post(
        "/api/v1/onboarding/setup-password",
        json={
            "token": token,
            "email": target_email,
            "session_ticket": session_ticket,
            "password": "PasswordOrgAdmin123!",
            "full_name": "Amadou Diallo"
        }
    )
    assert res_setup_pwd.status_code == 200
    pwd_data = res_setup_pwd.json()
    org_admin_token = pwd_data["access_token"]
    assert "access_token" in res_setup_pwd.cookies
    assert "refresh_token" in res_setup_pwd.cookies
    org_admin_headers = {"Authorization": f"Bearer {org_admin_token}"}
    org_user_id = pwd_data["user_id"]

    # 6. Sauvegarde automatique pas à pas du brouillon (Auto-save draft)
    draft_step2 = {
        "orgType": "Entreprise",
        "legalDocName": "Kbis_AgroSenegal_2026.pdf",
        "legalDocUploaded": True,
        "name": "Agro-Forestière du Sénégal",
        "code": "ORG-AFS-2026",
        "country": "Sénégal",
        "city": "Dakar",
        "plan": "pro"
    }
    res_draft_save = await async_client.post(
        "/api/v1/onboarding/draft",
        json={
            "email": target_email,
            "step": 2,
            "draft_data": draft_step2
        }
    )
    assert res_draft_save.status_code == 200
    assert res_draft_save.json()["step"] == 2

    # Récupération du brouillon
    res_draft_get = await async_client.get(f"/api/v1/onboarding/draft?email={target_email}")
    assert res_draft_get.status_code == 200
    assert res_draft_get.json()["draft_data"]["name"] == "Agro-Forestière du Sénégal"

    # 7. Soumission définitive du dossier d'onboarding
    submission_payload = {
        "type": "Entreprise",
        "legal_doc_name": "Kbis_AgroSenegal_2026.pdf",
        "legal_doc_url": "https://storage.senwisetool.com/docs/kbis.pdf",
        "name": "Agro-Forestière du Sénégal",
        "code": "ORG-AFS-2026",
        "country": "Sénégal",
        "region": "Dakar",
        "city": "Dakar",
        "address": "Route des Almadies",
        "postal_code": "10000",
        "plan": "pro",
        "sector": "Agroforesterie & Télémétrie",
        "contact_email": "contact.officiel@agro-senegal.sn",
        "phone": "+221 33 800 00 00",
        "website": "https://agro-senegal.sn",
        "custom_answers": {
            "q1": "attestation_pire_kerlink_signee.pdf",
            "q2": "Basse Casamance"
        }
    }
    res_submit = await async_client.post(
        "/api/v1/onboarding/submit",
        json=submission_payload,
        headers=org_admin_headers
    )
    assert res_submit.status_code == 201
    submit_data = res_submit.json()
    org_id = submit_data["organization_id"]
    assert submit_data["status"] == "pending"
    assert submit_data["waiting_url"] == "/onboarding/pending"

    # 8. Règle stricte 1 compte = 1 organisation : Tentative de soumission d'une seconde organisation
    res_duplicate_attempt = await async_client.post(
        "/api/v1/onboarding/submit",
        json={**submission_payload, "code": "ORG-AFS-2027", "name": "Autre Entite"},
        headers=org_admin_headers
    )
    assert res_duplicate_attempt.status_code == 400
    assert "déjà soumis" in res_duplicate_attempt.json()["detail"] or "1 organisation" in res_duplicate_attempt.json()["detail"]


    # 9. Vérification du statut bouclier (Security Shield) côté Org Admin
    res_status = await async_client.get(
        "/api/v1/onboarding/status",
        headers=org_admin_headers
    )
    assert res_status.status_code == 200
    st_data = res_status.json()
    assert st_data["status"] == "pending"
    assert st_data["can_access_dashboard"] is False  # Plateforme scellée !

    # 10. Super Admin instruit le dossier : Demande de révision avec directives WYSIWYG
    review_wysiwyg_notes = """## Examen de conformité - Département Agronomie SWT
> **Attention :** Votre attestation PIRE n'indique pas la bande de fréquence 868 MHz.
- Veuillez faire viser la puissance par l'autorité locale ARTP.
- Merci de recharger le document actualisé sous 48h."""

    res_review_rev = await async_client.post(
        f"/api/v1/organizations/{org_id}/review",
        json={
            "action": "revision_requested",
            "notes": review_wysiwyg_notes
        },
        headers=admin_headers
    )
    assert res_review_rev.status_code == 200
    assert res_review_rev.json()["status"] == "revision_requested"
    assert "868 MHz" in res_review_rev.json()["review_notes"]

    # 11. Org Admin consulte le bouclier : il voit la demande de révision et peut modifier
    res_status_rev = await async_client.get(
        "/api/v1/onboarding/status",
        headers=org_admin_headers
    )
    assert res_status_rev.status_code == 200
    assert res_status_rev.json()["status"] == "revision_requested"
    assert res_status_rev.json()["can_edit"] is True
    assert res_status_rev.json()["can_access_dashboard"] is False

    # 12. Org Admin met à jour et re-soumet le dossier
    updated_payload = {
        **submission_payload,
        "legal_doc_name": "Kbis_AgroSenegal_2026_ARTP_Vise.pdf",
        "custom_answers": {
            "q1": "attestation_pire_kerlink_868mhz_artp.pdf",
            "q2": "Basse Casamance"
        }
    }
    res_resubmit = await async_client.post(
        "/api/v1/onboarding/submit",
        json=updated_payload,
        headers=org_admin_headers
    )
    assert res_resubmit.status_code in [200, 201]
    assert res_resubmit.json()["status"] == "pending"


    # 13. Super Admin valide définitivement l'organisation
    res_review_act = await async_client.post(
        f"/api/v1/organizations/{org_id}/review",
        json={
            "action": "active",
            "notes": "Agrément technique délivré avec plein succès. Bienvenue sur le réseau SWT."
        },
        headers=admin_headers
    )
    assert res_review_act.status_code == 200
    assert res_review_act.json()["status"] == "active"

    # 14. Org Admin consulte le statut : plateforme déverrouillée !
    res_status_act = await async_client.get(
        "/api/v1/onboarding/status",
        headers=org_admin_headers
    )
    assert res_status_act.status_code == 200
    assert res_status_act.json()["status"] == "active"
    assert res_status_act.json()["can_access_dashboard"] is True

    # 15. Org Admin met à jour le profil de son organisation
    res_update_prof = await async_client.put(
        f"/api/v1/organizations/{org_id}/profile",
        json={
            "phone": "+221 77 123 45 67",
            "website": "https://www.agro-senegal.sn",
            "city": "Dakar Plateau"
        },
        headers=org_admin_headers
    )
    assert res_update_prof.status_code == 200
    assert res_update_prof.json()["city"] == "Dakar Plateau"
    assert res_update_prof.json()["phone"] == "+221 77 123 45 67"
