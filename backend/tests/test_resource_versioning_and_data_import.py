import io
import json
import pytest
from httpx import AsyncClient
import openpyxl
from app.core import security
from app.models.user.user import User, UserRole, UserStatus
from app.models.organization.organization import Organization, OrgStatusEnum
from app.models.resource.resource import Resource, ResourceStatus
from app.models.resource.resource_item import ResourceItem


@pytest.mark.asyncio
async def test_resource_versioning_auto_increment_and_strict_data_import(async_client: AsyncClient, setup_db):
    from tests.conftest import TestingSessionLocal

    async with TestingSessionLocal() as session:
        admin_user = User(
            email="admin_versioning@swt.com",
            username="admin_versioning",
            hashed_password=security.get_password_hash("Secret123!"),
            full_name="Admin Versioning",
            role=UserRole.ADMIN_ORG,
            status=UserStatus.ACTIVE,
            is_email_verified=True,
        )
        session.add(admin_user)
        await session.commit()
        await session.refresh(admin_user)

        org = Organization(
            name="Société Cacao Durable",
            code="SWT-CACAO-DURABLE",
            country="Côte d'Ivoire",
            type="Coopérative",
            owner_id=admin_user.id,
            contact_email="contact@cacao-durable.ci",
            status=OrgStatusEnum.ACTIVE.value,
        )
        session.add(org)
        await session.commit()
        await session.refresh(org)

    auth_token = security.create_access_token(
        subject=admin_user.id,
        extra_claims={"email": admin_user.email, "role": "ADMIN_ORG"}
    )
    headers = {"Authorization": f"Bearer {auth_token}"}

    # 1. Créer une ressource en brouillon avec champ clé auto-incrémenté
    schema_fields = [
        {
            "name": "id_parcelle",
            "label": "ID Parcelle Auto",
            "type": "integer",
            "required": True,
            "unique": True,
            "auto_increment": True,
        },
        {
            "name": "code_matricule",
            "label": "Code Matricule",
            "type": "text",
            "required": True,
            "unique": True,
        },
        {
            "name": "superficie",
            "label": "Superficie en Hectares",
            "type": "decimal",
            "required": True,
        },
        {
            "name": "certifie",
            "label": "Certifié Bio",
            "type": "boolean",
            "required": False,
            "default_value": False,
        },
    ]

    create_res = await async_client.post(
        "/api/v1/resources",
        json={
            "name": "Parcelles de Cacao",
            "code": "parcelles_cacao",
            "description": "Suivi des parcelles de cacao",
            "category": "agriculture",
            "schema_fields": schema_fields,
            "is_lts": False,
        },
        headers=headers,
    )
    assert create_res.status_code in [200, 201], create_res.text
    res_data = create_res.json()
    resource_id = res_data["id"]
    assert res_data["status"] == "draft"
    assert res_data["version"] == "1.0.0"
    assert res_data["is_lts"] is False

    # 2. Tant que c'est en brouillon, tester la modification directe
    update_res = await async_client.put(
        f"/api/v1/resources/{resource_id}",
        json={
            "description": "Nouvelle description en brouillon",
            "category": "cacao_certifie",
        },
        headers=headers,
    )
    assert update_res.status_code == 200
    assert update_res.json()["description"] == "Nouvelle description en brouillon"

    # 3. Ajouter une ligne unitaire avec auto-incrément (ne pas fournir id_parcelle)
    item_res = await async_client.post(
        f"/api/v1/resources/{resource_id}/items",
        json={
            "data": {
                "code_matricule": "MAT-001",
                "superficie": 4.5,
                "certifie": True,
            }
        },
        headers=headers,
    )
    assert item_res.status_code in [200, 201], item_res.text
    item_data = item_res.json()["data"]
    assert item_data["id_parcelle"] == 1, f"Expected auto_increment=1, got {item_data.get('id_parcelle')}"
    assert item_data["code_matricule"] == "MAT-001"

    # 4. Ajouter une 2e ligne unitaire sans id_parcelle -> doit être 2
    item2_res = await async_client.post(
        f"/api/v1/resources/{resource_id}/items",
        json={
            "data": {
                "code_matricule": "MAT-002",
                "superficie": 2.8,
            }
        },
        headers=headers,
    )
    assert item2_res.status_code in [200, 201]
    assert item2_res.json()["data"]["id_parcelle"] == 2

    # 5. Activer la ressource
    activate_res = await async_client.put(
        f"/api/v1/resources/{resource_id}",
        json={"status": "active"},
        headers=headers,
    )
    assert activate_res.status_code == 200
    assert activate_res.json()["status"] == "active"

    # 6. Définir LTS sur la version active
    lts_res = await async_client.put(
        f"/api/v1/resources/{resource_id}/lts?is_lts=true",
        headers=headers,
    )
    assert lts_res.status_code == 200
    assert lts_res.json()["is_lts"] is True

    # 7. Créer une nouvelle version (Brouillon) à partir de la version active
    new_version_res = await async_client.post(
        f"/api/v1/resources/{resource_id}/new-version",
        json={
            "new_version": "1.1.0",
            "description": "Version 1.1.0 avec nouveaux contrôles",
            "is_lts": False,
        },
        headers=headers,
    )
    assert new_version_res.status_code == 200, new_version_res.text
    new_ver_data = new_version_res.json()
    assert new_ver_data["version"] == "1.1.0"
    assert new_ver_data["status"] == "draft"
    assert new_ver_data["is_lts"] is False
    assert len(new_ver_data["schema_history"]) == 1
    assert new_ver_data["schema_history"][0]["version"] == "1.0.0"

    # 8. Télécharger le modèle Excel de données
    tpl_res = await async_client.get(
        f"/api/v1/resources/{resource_id}/data/template",
        headers=headers,
    )
    assert tpl_res.status_code == 200
    assert len(tpl_res.content) > 100

    # 9. Tester l'analyse stricte et l'import de données via fichier JSON
    # A. Fichier avec une erreur de type : 'superficie' texte invalide
    invalid_json_content = json.dumps([
        {"id_parcelle": 3, "code_matricule": "MAT-003", "superficie": "INVALID_FLOAT"},
    ]).encode("utf-8")

    dry_run_err = await async_client.post(
        f"/api/v1/resources/{resource_id}/data/validate-import?update_existing=true&dry_run=true",
        files={"file": ("test.json", invalid_json_content, "application/json")},
        headers=headers,
    )
    assert dry_run_err.status_code == 200
    report_err = dry_run_err.json()
    assert report_err["is_valid"] is False
    assert report_err["can_import"] is False
    assert len(report_err["errors"]) > 0

    # B. Fichier valide avec :
    # - MAT-001 (existant, id_parcelle=1) avec superficie modifiée (de 4.5 à 5.0) -> to_update
    # - MAT-004 (nouveau, sans id_parcelle) -> to_insert avec auto_increment
    valid_json_content = json.dumps([
        {"id_parcelle": 1, "code_matricule": "MAT-001", "superficie": 5.0, "certifie": True},
        {"code_matricule": "MAT-004", "superficie": 7.2, "certifie": False},
    ]).encode("utf-8")

    # Simulation (Dry Run) avec update_existing = True
    dry_run_valid = await async_client.post(
        f"/api/v1/resources/{resource_id}/data/validate-import?update_existing=true&dry_run=true",
        files={"file": ("test.json", valid_json_content, "application/json")},
        headers=headers,
    )
    assert dry_run_valid.status_code == 200
    report_valid = dry_run_valid.json()
    assert report_valid["is_valid"] is True
    assert report_valid["can_import"] is True
    assert report_valid["to_update_count"] == 1
    assert report_valid["to_insert_count"] == 1

    # Exécution réelle de l'import
    commit_res = await async_client.post(
        f"/api/v1/resources/{resource_id}/data/validate-import?update_existing=true&dry_run=false",
        files={"file": ("test.json", valid_json_content, "application/json")},
        headers=headers,
    )
    assert commit_res.status_code == 200
    commit_data = commit_res.json()
    assert commit_data["imported_count"] == 2

    # Vérifier que MAT-001 a bien été mis à jour à 5.0
    items_list_res = await async_client.get(f"/api/v1/resources/{resource_id}/items", headers=headers)
    assert items_list_res.status_code == 200
    items_list = items_list_res.json()["items"]
    mat1 = next(it for it in items_list if it["data"].get("code_matricule") == "MAT-001")
    assert mat1["data"]["superficie"] == 5.0

    # C. Tester avec update_existing = False (doit ignorer MAT-001)
    ignore_test_json = json.dumps([
        {"id_parcelle": 1, "code_matricule": "MAT-001", "superficie": 99.9},
        {"code_matricule": "MAT-005", "superficie": 3.3},
    ]).encode("utf-8")

    ignore_res = await async_client.post(
        f"/api/v1/resources/{resource_id}/data/validate-import?update_existing=false&dry_run=false",
        files={"file": ("test_ignore.json", ignore_test_json, "application/json")},
        headers=headers,
    )
    assert ignore_res.status_code == 200
    ignore_report = ignore_res.json()
    assert ignore_report["ignored_count"] == 1
    assert ignore_report["to_insert_count"] == 1

    # Vérifier que MAT-001 n'a PAS été modifié et garde 5.0
    items_list_res2 = await async_client.get(f"/api/v1/resources/{resource_id}/items", headers=headers)
    items_list2 = items_list_res2.json()["items"]
    mat1_check = next(it for it in items_list2 if it["data"].get("code_matricule") == "MAT-001")
    assert mat1_check["data"]["superficie"] == 5.0
