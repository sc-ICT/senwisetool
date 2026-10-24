import pytest
from httpx import AsyncClient
from app.core import security
from app.models.user.user import User, UserRole, UserStatus
from app.models.organization.organization import Organization, OrgStatusEnum

@pytest.mark.asyncio
async def test_members_roles_and_strict_single_org_rule(async_client: AsyncClient, setup_db):
    from tests.conftest import TestingSessionLocal

    # 1. Créer deux organisations distinctes (Org A et Org B) avec leurs propriétaires
    async with TestingSessionLocal() as session:
        owner_a = User(
            email="owner_a@forest.sn",
            username="owner_a",
            hashed_password=security.get_password_hash("Pass123!"),
            full_name="Directeur A",
            role=UserRole.ADMIN_ORG,
            status=UserStatus.ACTIVE,
            is_email_verified=True,
        )
        owner_b = User(
            email="owner_b@agro.sn",
            username="owner_b",
            hashed_password=security.get_password_hash("Pass123!"),
            full_name="Directeur B",
            role=UserRole.ADMIN_ORG,
            status=UserStatus.ACTIVE,
            is_email_verified=True,
        )
        session.add_all([owner_a, owner_b])
        await session.commit()
        await session.refresh(owner_a)
        await session.refresh(owner_b)

        org_a = Organization(
            name="Organisation A",
            code="ORG-A-2026",
            country="Sénégal",
            type="Entreprise",
            status=OrgStatusEnum.ACTIVE.value,
            plan="pro",
            owner_id=owner_a.id,
            contact_email="contact@org-a.sn",
            members_count=1,
        )
        org_b = Organization(
            name="Organisation B",
            code="ORG-B-2026",
            country="Sénégal",
            type="ONG",
            status=OrgStatusEnum.ACTIVE.value,
            plan="standard",
            owner_id=owner_b.id,
            contact_email="contact@org-b.sn",
            members_count=1,
        )
        session.add_all([org_a, org_b])
        await session.commit()
        await session.refresh(org_a)
        await session.refresh(org_b)

        org_a_id = org_a.id
        org_b_id = org_b.id
        owner_a_id = owner_a.id
        owner_b_id = owner_b.id

    token_a = security.create_access_token(
        subject=owner_a_id,
        extra_claims={"email": "owner_a@forest.sn", "role": "ADMIN_ORG"}
    )
    headers_a = {"Authorization": f"Bearer {token_a}"}

    token_b = security.create_access_token(
        subject=owner_b_id,
        extra_claims={"email": "owner_b@agro.sn", "role": "ADMIN_ORG"}
    )
    headers_b = {"Authorization": f"Bearer {token_b}"}

    # 2. Lister les membres d'Org A : le propriétaire doit être automatiquement seeded
    res_members_a = await async_client.get(
        f"/api/v1/organizations/{org_a_id}/members",
        headers=headers_a
    )
    assert res_members_a.status_code == 200
    members_list = res_members_a.json()
    assert len(members_list) == 1
    assert members_list[0]["email"] == "owner_a@forest.sn"
    assert members_list[0]["role_code"] == "ADM"

    # 3. Lister les rôles d'Org A : les 6 rôles par défaut (ADM, MGR, OPR, AUD, CNS, LCR) doivent être initialisés
    res_roles = await async_client.get(
        f"/api/v1/organizations/{org_a_id}/roles",
        headers=headers_a
    )
    assert res_roles.status_code == 200
    roles = res_roles.json()
    role_codes = [r["code"] for r in roles]
    for expected_code in ["ADM", "MGR", "OPR", "AUD", "CNS", "LCR"]:
        assert expected_code in role_codes

    # 4. Inviter un membre dans Org A
    new_member_email = "operateur.terrain@forest.sn"
    invite_payload = {
        "email": new_member_email,
        "name": "Moussa Sarr",
        "roles": ["Opérateur terrain"],
        "accessible_projects": ["Projet Reboisement Sud"],
        "message": "Bienvenue dans l'équipe de collecte terrain !",
        "mfa_enabled": True,
        "custom_invitation": True
    }
    res_invite = await async_client.post(
        f"/api/v1/organizations/{org_a_id}/members/invite",
        json=invite_payload,
        headers=headers_a
    )
    assert res_invite.status_code == 201
    invited_data = res_invite.json()
    assert invited_data["email"] == new_member_email
    assert invited_data["status"] == "invite"
    assert invited_data["mfa_enabled"] is True

    # 5. RÈGLE STRICTE : Tentative d'inviter ce même utilisateur dans l'organisation B -> DOIT ÉCHOUER (409)
    res_conflict_invite = await async_client.post(
        f"/api/v1/organizations/{org_b_id}/members/invite",
        json={
            "email": new_member_email,
            "roles": ["Consultant"]
        },
        headers=headers_b
    )
    assert res_conflict_invite.status_code == 409
    assert "plusieurs organisations" in res_conflict_invite.json()["detail"]

    # 6. RÈGLE STRICTE : Tentative d'inviter le propriétaire d'Org A dans Org B -> DOIT ÉCHOUER (409)
    res_owner_conflict = await async_client.post(
        f"/api/v1/organizations/{org_b_id}/members/invite",
        json={
            "email": "owner_a@forest.sn",
            "roles": ["Manager projet"]
        },
        headers=headers_b
    )
    assert res_owner_conflict.status_code == 409
    assert "propriétaire" in res_owner_conflict.json()["detail"] or "plusieurs organisations" in res_owner_conflict.json()["detail"]

    # 7. Créer un rôle personnalisé dans Org A (ORG-04)
    custom_role_payload = {
        "name": "Responsable conformité",
        "code": "RC",
        "description": "Gestion des exigences réglementaires et de la conformité",
        "permissions": {
            "projets": {"voir": "active", "modifier": "active"},
            "campagnes": {"voir": "active"},
            "formulaires": {"voir": "active"}
        }
    }
    res_custom_role = await async_client.post(
        f"/api/v1/organizations/{org_a_id}/roles",
        json=custom_role_payload,
        headers=headers_a
    )
    assert res_custom_role.status_code == 201
    rc_data = res_custom_role.json()
    assert rc_data["code"] == "RC"
    assert rc_data["is_system"] is False

    # 8. Récupérer la matrice des permissions (ORG-05)
    res_matrix = await async_client.get(
        f"/api/v1/organizations/{org_a_id}/permissions/matrix",
        headers=headers_a
    )
    assert res_matrix.status_code == 200
    matrix_data = res_matrix.json()
    assert "roles" in matrix_data
    assert "definitions" in matrix_data
    assert len(matrix_data["definitions"]) >= 4

    # 9. Modifier le statut d'un membre (ex: passer de "invite" à "actif" ou "suspendu") (ORG-06)
    member_id = invited_data["id"]
    res_update_member = await async_client.put(
        f"/api/v1/organizations/{org_a_id}/members/{member_id}",
        json={"status": "actif", "roles": ["Opérateur terrain", "Auditeur"]},
        headers=headers_a
    )
    assert res_update_member.status_code == 200
    assert res_update_member.json()["status"] == "actif"
    assert "Auditeur" in res_update_member.json()["roles"]
