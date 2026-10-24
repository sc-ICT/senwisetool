import pytest
from httpx import AsyncClient
from app.core import security
from app.models.user.user import User, UserRole, UserStatus
from tests.conftest import TestingSessionLocal


@pytest.mark.asyncio
async def test_plugin_rules_7_8_9(async_client: AsyncClient, setup_db):
    # 1. Setup Super Admin
    async with TestingSessionLocal() as session:
        super_admin = User(
            email="superadmin_rules@test.com",
            username="superadmin_rules",
            hashed_password=security.get_password_hash("Secret123!"),
            full_name="Super Admin Rules",
            role=UserRole.SUPER_ADMIN,
            status=UserStatus.ACTIVE,
            is_email_verified=True,
        )
        session.add(super_admin)
        await session.commit()
        await session.refresh(super_admin)
        super_admin_id = super_admin.id

    token = security.create_access_token(
        subject=super_admin_id,
        extra_claims={"email": "superadmin_rules@test.com", "role": "SUPER_ADMIN"},
    )
    headers = {"Authorization": f"Bearer {token}"}

    # 2. Create Plugin (comes with draft version 1.0.0 by default)
    create_plugin_res = await async_client.post(
        "/api/v1/plugins/",
        json={
            "name": "Plugin EUDR Test",
            "code": "plugin-eudr-rules",
            "category": "Agro-alimentaire",
            "description": "Test de gestion du cycle de vie",
            "initial_version_number": "1.0.0",
            "initial_usage_policy": "# Usage Policy v1",
            "initial_changelog": "Initial draft",
        },
        headers=headers,
    )
    assert create_plugin_res.status_code == 201
    plugin_data = create_plugin_res.json()
    plugin_id = plugin_data["id"]
    draft_v1_id = plugin_data["latestVersion"]["id"]

    # 3. RÈGLE 7 : Tentative de créer une 2ème version alors que v1.0.0 est encore en brouillon
    second_version_res = await async_client.post(
        f"/api/v1/plugins/{plugin_id}/versions",
        json={
            "version_number": "1.1.0",
            "changelog": "Tentative de deuxième brouillon",
            "usage_policy": "# Policy v1.1",
        },
        headers=headers,
    )
    # DOIT ÉCHOUER car un brouillon existe déjà !
    assert second_version_res.status_code == 400
    assert "brouillon" in second_version_res.json()["detail"].lower()

    # 4. RÈGLE 7 : Suppression d'une version brouillon autorisée
    temp_plugin_res = await async_client.post(
        "/api/v1/plugins/",
        json={
            "name": "Plugin Draft Delete Test",
            "code": "plugin-draft-del",
            "category": "Test",
            "initial_version_number": "0.9.0",
            "initial_usage_policy": "# Temp Policy",
        },
        headers=headers,
    )
    temp_p = temp_plugin_res.json()
    temp_v_id = temp_p["latestVersion"]["id"]

    delete_draft_res = await async_client.delete(
        f"/api/v1/plugins/{temp_p['id']}/versions/{temp_v_id}",
        headers=headers,
    )
    assert delete_draft_res.status_code == 200
    assert "supprimée avec succès" in delete_draft_res.json()["message"]

    # 5. RÈGLE 8 : Publication de la première version avec détails exhaustifs du contenu
    # Si détails trop courts (< 10 caractères), doit échouer
    fail_pub_res = await async_client.post(
        f"/api/v1/plugins/{plugin_id}/versions/{draft_v1_id}/publish",
        json={"details_or_changelog": "court"},
        headers=headers,
    )
    assert fail_pub_res.status_code == 422 or fail_pub_res.status_code == 400

    # Publication valide pour première version
    pub_res = await async_client.post(
        f"/api/v1/plugins/{plugin_id}/versions/{draft_v1_id}/publish",
        json={
            "details_or_changelog": "Contenu complet : 3 Projets de traçabilité, 5 formulaires GPS et connecteur IoT.",
            "usage_policy": "# Politique officielle acceptée",
        },
        headers=headers,
    )
    assert pub_res.status_code == 200
    assert pub_res.json()["status"] == "published"

    # 6. RÈGLE 7 : Une version publiée ne peut PLUS JAMAIS être supprimée
    delete_pub_res = await async_client.delete(
        f"/api/v1/plugins/{plugin_id}/versions/{draft_v1_id}",
        headers=headers,
    )
    assert delete_pub_res.status_code == 400
    assert "ne peut pas être supprimée" in delete_pub_res.json()["detail"]

    # 7. RÈGLE 10 : Impossible de déprécier l'unique version publiée (pas de version cible)
    single_ver_deprecate_res = await async_client.post(
        f"/api/v1/plugins/{plugin_id}/versions/{draft_v1_id}/deprecate",
        json={
            "days_until_eol": 30,
            "reason": "Tentative de dépréciation sans version cible",
        },
        headers=headers,
    )
    assert single_ver_deprecate_res.status_code == 400
    assert "unique version publiée" in single_ver_deprecate_res.json()["detail"]

    # 8. RÈGLE 7 : Maintenant que v1.0.0 est publiée, on PEUT créer v1.1.0 en brouillon
    create_v2_res = await async_client.post(
        f"/api/v1/plugins/{plugin_id}/versions",
        json={
            "version_number": "1.1.0",
            "changelog": "Notes de préparation",
            "usage_policy": "# Policy v1.1",
        },
        headers=headers,
    )
    assert create_v2_res.status_code == 201
    v2_id = create_v2_res.json()["id"]

    # Publication de v1.1.0 avec précision des nouveautés
    pub_v2_res = await async_client.post(
        f"/api/v1/plugins/{plugin_id}/versions/{v2_id}/publish",
        json={
            "details_or_changelog": "Nouveautés : Analyse par satellite Sentinel-2 et export Shapefile.",
        },
        headers=headers,
    )
    assert pub_v2_res.status_code == 200
    assert pub_v2_res.json()["status"] == "published"

    # Création et publication d'une 3ème version v1.2.0
    create_v3_res = await async_client.post(
        f"/api/v1/plugins/{plugin_id}/versions",
        json={
            "version_number": "1.2.0",
            "changelog": "Notes v1.2",
            "usage_policy": "# Policy v1.2",
        },
        headers=headers,
    )
    assert create_v3_res.status_code == 201
    v3_id = create_v3_res.json()["id"]

    pub_v3_res = await async_client.post(
        f"/api/v1/plugins/{plugin_id}/versions/{v3_id}/publish",
        json={
            "details_or_changelog": "Nouveautés : Intégration blockchain et audit carbone.",
        },
        headers=headers,
    )
    assert pub_v3_res.status_code == 200

    # 9. RÈGLE 10 : Tentative de déprécier v1.1.0 alors que v1.0.0 est plus ancienne
    fail_order_res = await async_client.post(
        f"/api/v1/plugins/{plugin_id}/versions/{v2_id}/deprecate",
        json={
            "days_until_eol": 30,
            "reason": "Tentative de déprécier v1.1.0 avant v1.0.0",
        },
        headers=headers,
    )
    assert fail_order_res.status_code == 400
    assert "plus ancienne" in fail_order_res.json()["detail"].lower()

    # 10. RÈGLE 9 & 10 : Dépréciation de la version la plus ancienne (v1.0.0) -> SUCCÈS
    deprecate_res = await async_client.post(
        f"/api/v1/plugins/{plugin_id}/versions/{draft_v1_id}/deprecate",
        json={
            "days_until_eol": 30,
            "reason": "Cette version v1.0.0 est dépréciée au profit de v1.1.0. Exportez vos données et migrez sous 30 jours.",
        },
        headers=headers,
    )
    assert deprecate_res.status_code == 200
    dep_data = deprecate_res.json()
    assert dep_data["status"] == "deprecated"
    assert dep_data["daysUntilEol"] is not None
    assert dep_data["daysUntilEol"] >= 29

    # 11. RÈGLE 10 : Tentative de déprécier v1.1.0 alors que le processus de v1.0.0 est EN COURS
    double_deprecate_res = await async_client.post(
        f"/api/v1/plugins/{plugin_id}/versions/{v2_id}/deprecate",
        json={
            "days_until_eol": 15,
            "reason": "Tentative de deuxième dépréciation simultanée",
        },
        headers=headers,
    )
    assert double_deprecate_res.status_code == 400
    assert "déjà en cours" in double_deprecate_res.json()["detail"].lower()

