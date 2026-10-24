import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.core import security
from app.models.user.user import User, UserRole, UserStatus
from app.models.organization.organization import Organization, OrgStatusEnum
from app.services.notification.notification_service import notification_service

@pytest.mark.asyncio
async def test_review_and_notifications_flow(db_session):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Création d'un superadmin
        superadmin = User(
            email="superadmin_notif@senwisetool.com",
            username="superadmin_notif",
            hashed_password=security.get_password_hash("SuperAdmin123!"),
            full_name="Super Admin Notif",
            role=UserRole.SUPER_ADMIN,
            status=UserStatus.ACTIVE,
            is_email_verified=True,
        )
        db_session.add(superadmin)
        await db_session.commit()
        await db_session.refresh(superadmin)

        token = security.create_access_token(
            subject=superadmin.id,
            extra_claims={"email": superadmin.email, "role": "SUPER_ADMIN"}
        )
        headers = {"Authorization": f"Bearer {token}"}

        # 2. Création d'une organisation en revision_requested
        org = Organization(
            name="Alpha Biotech Test",
            code="ORG-ALPHA-NOTIF",
            status=OrgStatusEnum.REVISION_REQUESTED.value,
            country="Sénégal",
            type="company",
            owner_id=superadmin.id,
            contact_email="contact@alphabiotech.com",
            phone="+33100000000",
            review_notes="<p>Merci de préciser le protocole LoRaWAN.</p>",
            custom_questions=[
                {
                    "label": "Quelle fréquence radio utilisez-vous ?",
                    "type": "text",
                    "isRequired": True
                }
            ]
        )
        db_session.add(org)
        await db_session.commit()
        await db_session.refresh(org)

        # 3. Test GET /api/v1/onboarding/review-details (accès sécurisé authentifié)
        resp_details = await ac.get(f"/api/v1/onboarding/review-details?org_id={org.id}", headers=headers)
        assert resp_details.status_code == 200
        data_details = resp_details.json()
        assert data_details["name"] == "Alpha Biotech Test"
        assert len(data_details["customQuestions"]) == 1
        assert "Merci de préciser le protocole" in data_details["reviewNotes"]

        # 4. Test POST /api/v1/onboarding/review/submit (soumission sécurisée authentifiée)
        resp_submit = await ac.post("/api/v1/onboarding/review/submit", json={
            "organization_id": org.id,
            "custom_answers": {
                "Quelle fréquence radio utilisez-vous ?": "EU868 MHz"
            },
            "comments": "Réponse transmise dans les délais."
        }, headers=headers)
        assert resp_submit.status_code == 200
        data_submit = resp_submit.json()
        assert data_submit["success"] is True
        assert data_submit["status"] == OrgStatusEnum.PENDING.value

        # Vérifier que le statut en DB est bien repassé à PENDING
        await db_session.refresh(org)
        assert org.status == OrgStatusEnum.PENDING.value
        assert org.custom_answers == {"Quelle fréquence radio utilisez-vous ?": "EU868 MHz"}

        # 5. Test des notifications pour le superadmin
        resp_notifs = await ac.get("/api/v1/notifications/", headers=headers)
        assert resp_notifs.status_code == 200
        notifs_data = resp_notifs.json()
        assert notifs_data["unread_count"] >= 1
        assert any("Alpha Biotech Test" in n["message"] for n in notifs_data["notifications"])

        # Marquer une notification comme lue
        notif_id = notifs_data["notifications"][0]["id"]
        resp_read = await ac.patch(f"/api/v1/notifications/{notif_id}/read", headers=headers)
        assert resp_read.status_code == 200

        # Tout marquer comme lu
        resp_all = await ac.post("/api/v1/notifications/read-all", headers=headers)
        assert resp_all.status_code == 200
        assert resp_all.json()["success"] is True
