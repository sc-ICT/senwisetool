import io
import json
import zipfile
import pytest
from httpx import AsyncClient
import openpyxl

from app.core import security
from app.models.user.user import User, UserRole, UserStatus
from app.models.organization.organization import Organization, OrgStatusEnum
from app.models.resource.resource import Resource, ResourceStatus
from app.models.resource.resource_item import ResourceItem
from app.services.storage.storage_service import storage_service


@pytest.mark.asyncio
async def test_storage_media_and_export_bundle(async_client: AsyncClient, setup_db):
    from tests.conftest import TestingSessionLocal

    async with TestingSessionLocal() as session:
        # 1. Création de l'utilisateur Admin
        admin_user = User(
            email="admin_storage@swt.com",
            username="admin_storage",
            hashed_password=security.get_password_hash("Secret123!"),
            full_name="Admin Storage",
            role=UserRole.ADMIN_ORG,
            status=UserStatus.ACTIVE,
            is_email_verified=True,
        )
        session.add(admin_user)
        await session.commit()
        await session.refresh(admin_user)

        # 2. Organisation
        org = Organization(
            name="Coopérative Cacao Ivoire",
            code="SWT-CACAO-IVOIRE",
            country="Côte d'Ivoire",
            type="Coopérative",
            owner_id=admin_user.id,
            contact_email="contact@cacao-ivoire.ci",
            status=OrgStatusEnum.ACTIVE.value,
        )
        session.add(org)
        await session.commit()
        await session.refresh(org)

        # 3. Ressource avec champs standards et champ média
        resource = Resource(
            name="Recensement Planteurs",
            code="recensement_planteurs",
            description="Fiche planteurs avec photo et coordonnées",
            category="agriculture",
            organization_id=org.id,
            status=ResourceStatus.ACTIVE.value,
            version="1.0.0",
            is_lts=True,
            created_by_id=admin_user.id,
            schema_fields=[
                {"name": "code_planteur", "label": "Code Planteur", "type": "text", "required": True, "unique": True},
                {"name": "nom_planteur", "label": "Nom du Planteur", "type": "text", "required": True},
                {"name": "superficie", "label": "Superficie (ha)", "type": "decimal"},
                {"name": "photo_profil", "label": "Photo d'Identité", "type": "image"},
            ],
        )
        session.add(resource)
        await session.commit()
        await session.refresh(resource)

    # Token d'authentification
    auth_token = security.create_access_token(
        subject=admin_user.id,
        extra_claims={"email": admin_user.email, "role": "ADMIN_ORG"}
    )
    headers = {"Authorization": f"Bearer {auth_token}"}

    # 4. Test upload d'un média pour la ressource
    sample_img_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"
    upload_res = await async_client.post(
        f"/api/v1/resources/{resource.id}/media?media_type=images&item_key=PLANT_001",
        files={"file": ("planteur_photo.png", sample_img_bytes, "image/png")},
        headers=headers,
    )
    assert upload_res.status_code == 200, f"Upload media failed: {upload_res.text}"
    media_info = upload_res.json()
    assert "url" in media_info
    assert "/storage/organizations/" in media_info["url"]
    assert "tools/resources" in media_info["url"]
    assert "images" in media_info["url"]
    media_url = media_info["url"]

    # 5. Création de deux enregistrements : un avec média, un sans média
    item1_res = await async_client.post(
        f"/api/v1/resources/{resource.id}/items",
        json={
            "data": {
                "code_planteur": "PLANT-001",
                "nom_planteur": "Kouassi Yao",
                "superficie": 4.5,
                "photo_profil": media_url,
            }
        },
        headers=headers,
    )
    assert item1_res.status_code == 201

    item2_res = await async_client.post(
        f"/api/v1/resources/{resource.id}/items",
        json={
            "data": {
                "code_planteur": "PLANT-002",
                "nom_planteur": "Traoré Fatou",
                "superficie": 6.2,
                "photo_profil": None,
            }
        },
        headers=headers,
    )
    assert item2_res.status_code == 201

    # 6. Test Export Données en Excel avec Médias -> Doit retourner un ZIP contenant data.xlsx et media/
    export_zip_res = await async_client.get(
        f"/api/v1/resources/{resource.id}/export/data?format=xlsx",
        headers=headers,
    )
    assert export_zip_res.status_code == 200
    assert export_zip_res.headers.get("content-type") == "application/zip"
    disposition = export_zip_res.headers.get("content-disposition", "")
    assert ".zip" in disposition

    # Inspecter le contenu du ZIP
    zip_bytes = export_zip_res.content
    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
        file_list = zf.namelist()
        assert any(name.startswith("donnees_") and name.endswith(".xlsx") for name in file_list)
        # Vérifier présence du dossier media/
        media_entries = [name for name in file_list if name.startswith("media/")]
        assert len(media_entries) >= 1
        # Vérifier que le fichier média extrait a bien le contenu binaire envoyé
        extracted_media = zf.read(media_entries[0])
        assert extracted_media == sample_img_bytes

        # Vérifier le fichier Excel dans le ZIP : le lien de la photo doit être réécrit vers 'media/...'
        excel_filename = next(name for name in file_list if name.startswith("donnees_") and name.endswith(".xlsx"))
        excel_bytes = zf.read(excel_filename)
        wb = openpyxl.load_workbook(io.BytesIO(excel_bytes))
        ws = wb.active
        # Trouver la colonne photo_profil (index 4)
        photo_val_row2 = ws.cell(row=2, column=4).value
        assert photo_val_row2.startswith("media/")
        assert "photo" in photo_val_row2

    # 7. Test Export Données en JSON avec Médias -> Doit retourner un ZIP avec donnees_*.json et media/
    export_json_zip_res = await async_client.get(
        f"/api/v1/resources/{resource.id}/export/data?format=json",
        headers=headers,
    )
    assert export_json_zip_res.status_code == 200
    assert export_json_zip_res.headers.get("content-type") == "application/zip"
    with zipfile.ZipFile(io.BytesIO(export_json_zip_res.content), "r") as zf:
        json_filename = next(name for name in zf.namelist() if name.startswith("donnees_") and name.endswith(".json"))
        json_data = json.loads(zf.read(json_filename).decode("utf-8"))
        assert len(json_data) == 2
        assert json_data[0]["code_planteur"] == "PLANT-001"
        assert json_data[0]["photo_profil"].startswith("media/")

    # 8. Test Export Schéma en JSON
    schema_json_res = await async_client.get(
        f"/api/v1/resources/{resource.id}/export/schema?format=json",
        headers=headers,
    )
    assert schema_json_res.status_code == 200
    assert "application/json" in schema_json_res.headers.get("content-type", "")
    schema_data = schema_json_res.json()
    assert schema_data["code"] == "recensement_planteurs"
    assert schema_data["is_lts"] is True
    assert len(schema_data["schema_fields"]) == 4

    # 9. Test Export Schéma en Excel
    schema_xlsx_res = await async_client.get(
        f"/api/v1/resources/{resource.id}/export/schema?format=xlsx",
        headers=headers,
    )
    assert schema_xlsx_res.status_code == 200
    assert "openxmlformats" in schema_xlsx_res.headers.get("content-type", "")
    wb_schema = openpyxl.load_workbook(io.BytesIO(schema_xlsx_res.content))
    ws_schema = wb_schema.active
    assert ws_schema.cell(row=1, column=1).value == "Clé Technique (name) *"
    # row 2: code_planteur
    assert ws_schema.cell(row=2, column=1).value == "code_planteur"

    # 10. Test Statistiques de Stockage
    stats_res = await async_client.get("/api/v1/resources/storage/stats", headers=headers)
    assert stats_res.status_code == 200
    stats = stats_res.json()
    assert "total_bytes" in stats
    assert stats["total_bytes"] > 0
    assert "breakdown" in stats
