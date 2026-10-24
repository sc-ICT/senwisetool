import pytest
from httpx import AsyncClient
from app.core import security
from app.models.user.user import User, UserRole, UserStatus
from app.models.organization.organization import Organization, OrgStatusEnum
from app.models.resource.resource import Resource, ResourceStatus
from app.models.resource.resource_item import ResourceItem


@pytest.mark.asyncio
async def test_resource_tool_crud_and_bulk_data_preloading(async_client: AsyncClient, setup_db):
    from tests.conftest import TestingSessionLocal

    async with TestingSessionLocal() as session:
        # 1. Super Admin
        super_admin = User(
            email="super_res_admin@senma.com",
            username="super_res_admin",
            hashed_password=security.get_password_hash("SuperPass123!"),
            full_name="Super Admin Res",
            role=UserRole.SUPER_ADMIN,
            status=UserStatus.ACTIVE,
            is_email_verified=True,
        )
        # Admin Org A
        admin_org_a = User(
            email="admin_org_res_a@senma.com",
            username="admin_org_res_a",
            hashed_password=security.get_password_hash("AdminPassA123!"),
            full_name="Alice Org A",
            role=UserRole.ADMIN_ORG,
            status=UserStatus.ACTIVE,
            is_email_verified=True,
        )
        session.add_all([super_admin, admin_org_a])
        await session.commit()
        await session.refresh(super_admin)
        await session.refresh(admin_org_a)

        org_a = Organization(
            name="Coopérative Café A",
            code="SWT-RES-COOP-A",
            country="Côte d'Ivoire",
            type="Coopérative",
            owner_id=admin_org_a.id,
            contact_email="contact@res-coop-a.com",
            status=OrgStatusEnum.ACTIVE.value,
        )
        session.add(org_a)
        await session.commit()
        await session.refresh(org_a)

    super_token = security.create_access_token(
        subject=super_admin.id,
        extra_claims={"email": super_admin.email, "role": "SUPER_ADMIN"}
    )
    super_headers = {"Authorization": f"Bearer {super_token}"}

    admin_a_token = security.create_access_token(
        subject=admin_org_a.id,
        extra_claims={"email": admin_org_a.email, "role": "ADMIN_ORG"}
    )
    admin_a_headers = {"Authorization": f"Bearer {admin_a_token}"}

    # 1. Super Admin crée une ressource dynamique avec schéma
    schema_fields = [
        {"name": "code_parcelle", "label": "Code Parcelle", "type": "text", "required": True, "unique": True},
        {"name": "superficie", "label": "Superficie en Ha", "type": "number", "required": True},
        {"name": "variete", "label": "Variété", "type": "select", "required": False, "options": ["Arabica", "Robusta"]},
    ]
    create_payload = {
        "name": "Parcelles Agricoles Nationales",
        "code": "parcelles_agricoles_nat",
        "description": "Référentiel certifié des parcelles de café",
        "category": "agriculture",
        "icon": "Database",
        "status": "active",
        "schema_fields": schema_fields,
        "is_shared": True,
        "is_immutable": True,
        "immutable_fields": ["schema_fields", "code"],
    }

    res = await async_client.post("/api/v1/resources", json=create_payload, headers=super_headers)
    assert res.status_code == 201, res.text
    created_res = res.json()
    resource_id = created_res["id"]
    assert created_res["name"] == "Parcelles Agricoles Nationales"
    assert created_res["code"] == "parcelles_agricoles_nat"
    # Vérification stricte : par défaut le statut est TOUJOURS 'draft' et la version '1.0.0'
    assert created_res["status"] == "draft"
    assert created_res["version"] == "1.0.0"
    assert len(created_res["schema_fields"]) == 3

    # Activation de la ressource une fois validée
    act_res = await async_client.put(
        f"/api/v1/resources/{resource_id}",
        json={"status": "active"},
        headers=super_headers,
    )
    assert act_res.status_code == 200
    assert act_res.json()["status"] == "active"

    # 2. Super Admin pré-charge des données (Bulk import)
    bulk_data = {
        "items": [
            {"code_parcelle": "PARC-001", "superficie": 4.5, "variete": "Arabica"},
            {"code_parcelle": "PARC-002", "superficie": 12.0, "variete": "Robusta"},
            {"code_parcelle": "PARC-003", "superficie": 2.8, "variete": "Arabica"},
        ]
    }
    bulk_res = await async_client.post(
        f"/api/v1/resources/{resource_id}/items/bulk",
        json=bulk_data,
        headers=super_headers,
    )
    assert bulk_res.status_code == 200, bulk_res.text
    assert bulk_res.json()["imported_count"] == 3

    # 3. Test de l'unicité stricte : insertion d'un doublon 'PARC-001' doit être rejeté (HTTP 409)
    duplicate_payload = {
        "data": {"code_parcelle": "PARC-001", "superficie": 9.9, "variete": "Robusta"}
    }
    dup_res = await async_client.post(
        f"/api/v1/resources/{resource_id}/items",
        json=duplicate_payload,
        headers=super_headers,
    )
    assert dup_res.status_code == 409
    assert "existe déjà" in dup_res.json()["detail"]

    # 4. Super Admin ajoute une ligne manuelle unique
    single_item_payload = {
        "data": {"code_parcelle": "PARC-004", "superficie": 7.2, "variete": "Robusta"}
    }
    item_res = await async_client.post(
        f"/api/v1/resources/{resource_id}/items",
        json=single_item_payload,
        headers=super_headers,
    )
    assert item_res.status_code == 201, item_res.text
    item_id = item_res.json()["id"]

    # 5. Validation de schéma : insertion sans champ requis 'code_parcelle' doit lever 422
    bad_payload = {"data": {"variete": "Robusta"}}
    bad_res = await async_client.post(
        f"/api/v1/resources/{resource_id}/items",
        json=bad_payload,
        headers=super_headers,
    )
    assert bad_res.status_code == 422

    # 6. Lister les enregistrements
    list_items_res = await async_client.get(f"/api/v1/resources/{resource_id}/items", headers=super_headers)
    assert list_items_res.status_code == 200
    assert list_items_res.json()["total"] == 4

    # 7. Modifier un enregistrement
    update_res = await async_client.put(
        f"/api/v1/resources/{resource_id}/items/{item_id}",
        json={"data": {"superficie": 8.0}},
        headers=super_headers,
    )
    assert update_res.status_code == 200
    assert update_res.json()["data"]["superficie"] == 8.0

    # 8. Supprimer un enregistrement
    del_res = await async_client.delete(
        f"/api/v1/resources/{resource_id}/items/{item_id}",
        headers=super_headers,
    )
    assert del_res.status_code == 200
    assert del_res.json()["deleted"] is True

    # 9. Test de la liaison inter-ressources (Relations: Planteurs <-> Plantations)
    # A. Créer ressource Planteurs
    planteurs_payload = {
        "name": "Planteurs Référencés",
        "code": "planteurs_ref",
        "category": "agriculture",
        "status": "active",
        "schema_fields": [
            {"name": "nom_complet", "label": "Nom du Planteur", "type": "text", "required": True},
            {"name": "code_planteur", "label": "Code Planteur", "type": "text", "required": True, "unique": True},
        ],
    }
    planteurs_res = await async_client.post("/api/v1/resources", json=planteurs_payload, headers=super_headers)
    assert planteurs_res.status_code == 201
    planteur_resource_id = planteurs_res.json()["id"]

    # Insérer le Planteur 1
    p1_res = await async_client.post(
        f"/api/v1/resources/{planteur_resource_id}/items",
        json={"data": {"nom_complet": "Jean Kouassi", "code_planteur": "PL-001"}},
        headers=super_headers,
    )
    assert p1_res.status_code == 201
    p1_id = p1_res.json()["id"]

    # B. Créer ressource Plantations avec champ 'planteur_id' de type relation
    plantations_payload = {
        "name": "Plantations Liées",
        "code": "plantations_liees",
        "category": "agriculture",
        "status": "active",
        "schema_fields": [
            {"name": "titre_plantation", "label": "Nom Plantation", "type": "text", "required": True},
            {
                "name": "planteur_id",
                "label": "Planteur Propriétaire",
                "type": "relation",
                "required": True,
                "target_resource_id": planteur_resource_id,
                "target_resource_code": "planteurs_ref",
                "display_field": "nom_complet",
            },
        ],
    }
    plantations_res = await async_client.post("/api/v1/resources", json=plantations_payload, headers=super_headers)
    assert plantations_res.status_code == 201
    plantation_resource_id = plantations_res.json()["id"]

    # Insérer 2 plantations reliées à Jean Kouassi (p1_id)
    await async_client.post(
        f"/api/v1/resources/{plantation_resource_id}/items",
        json={"data": {"titre_plantation": "Plantation Est", "planteur_id": p1_id}},
        headers=super_headers,
    )
    await async_client.post(
        f"/api/v1/resources/{plantation_resource_id}/items",
        json={"data": {"titre_plantation": "Plantation Ouest", "planteur_id": p1_id}},
        headers=super_headers,
    )

    # C. Tester l'endpoint des options de relation pour le sélecteur
    opts_res = await async_client.get(
        f"/api/v1/resources/{planteur_resource_id}/relation-options",
        headers=super_headers,
    )
    assert opts_res.status_code == 200
    options = opts_res.json()
    assert len(options) >= 1
    assert any(opt["id"] == p1_id and opt["label"] == "Jean Kouassi" for opt in options)

    # D. Tester la navigation bidirectionnelle : trouver toutes les plantations d'un planteur
    related_res = await async_client.get(
        f"/api/v1/resources/{planteur_resource_id}/items/{p1_id}/related",
        headers=super_headers,
    )
    assert related_res.status_code == 200
    related_data = related_res.json()
    assert related_data["item_id"] == p1_id
    assert len(related_data["relations"]) == 1
    assert related_data["relations"][0]["resource_name"] == "Plantations Liées"
    assert related_data["relations"][0]["total"] == 2

    # 10. Téléchargement du gabarit Excel officiel
    tpl_res = await async_client.get("/api/v1/resources/schema/template", headers=super_headers)
    assert tpl_res.status_code == 200
    assert len(tpl_res.content) > 1000
    assert "spreadsheetml" in tpl_res.headers.get("content-type", "")

    # 11. Parsing d'un fichier de schéma JSON avec les nouveaux types (audio, video, etc.)
    import json
    sample_schema_json = [
        {"name": "audio_interview", "label": "Accord Audio", "type": "audio", "required": False},
        {"name": "video_survol", "label": "Vidéo Drone", "type": "video", "required": False},
        {"name": "montant_avance", "label": "Montant Avance", "type": "currency", "required": False},
    ]
    files = {"file": ("schema.json", json.dumps(sample_schema_json).encode("utf-8"), "application/json")}
    parse_res = await async_client.post("/api/v1/resources/schema/parse-file", files=files, headers=super_headers)
    assert parse_res.status_code == 200
    parsed_fields = parse_res.json()
    assert len(parsed_fields) == 3
    assert parsed_fields[0]["type"] == "audio"
    assert parsed_fields[1]["type"] == "video"
    assert parsed_fields[2]["type"] == "currency"

    # 12. Test de la réconciliation de schéma sur ressource active contenant des données
    # Cas A : Ajout d'un champ obligatoire SANS valeur par défaut -> Doit être marqué incohérent
    incoherent_payload = {
        "fields": [
            {"name": "code_parcelle", "label": "Code Parcelle", "type": "text", "required": True, "unique": True},
            {"name": "superficie", "label": "Superficie en Ha", "type": "number", "required": True},
            {"name": "variete", "label": "Variété", "type": "select", "required": False},
            {"name": "champ_bloquant", "label": "Champ Requis Vide", "type": "text", "required": True},
        ],
        "dry_run": True,
    }
    rec_res_a = await async_client.post(
        f"/api/v1/resources/{resource_id}/schema/reconcile",
        json=incoherent_payload,
        headers=super_headers,
    )
    assert rec_res_a.status_code == 200
    report_a = rec_res_a.json()
    assert report_a["is_coherent"] is False
    assert len(report_a["errors"]) >= 1

    # Cas B : Ajout d'un champ avec valeur par défaut -> Cohérent, applique les modifications et incrémente la version
    coherent_payload = {
        "fields": [
            {"name": "code_parcelle", "label": "Code Parcelle", "type": "text", "required": True, "unique": True},
            {"name": "superficie", "label": "Superficie en Ha", "type": "number", "required": True},
            {"name": "variete", "label": "Variété", "type": "select", "required": False},
            {"name": "pays_origine", "label": "Pays d'Origine", "type": "text", "required": True, "default_value": "Côte d'Ivoire"},
        ],
        "dry_run": False,
    }
    rec_res_b = await async_client.post(
        f"/api/v1/resources/{resource_id}/schema/reconcile",
        json=coherent_payload,
        headers=super_headers,
    )
    assert rec_res_b.status_code == 200
    report_b = rec_res_b.json()
    assert report_b["is_coherent"] is True
    assert report_b["new_version"] == "1.1.0"

    # Vérifier que la ressource a bien été incrémentée en version 1.1.0 et que les données ont été backfillées
    res_updated = await async_client.get(f"/api/v1/resources/{resource_id}", headers=super_headers)
    assert res_updated.status_code == 200
    assert res_updated.json()["version"] == "1.1.0"
    assert len(res_updated.json()["schema_fields"]) == 4

    # Vérifier que les enregistrements existants ont reçu la valeur par défaut 'Côte d'Ivoire'
    items_backfilled = await async_client.get(f"/api/v1/resources/{resource_id}/items", headers=super_headers)
    assert items_backfilled.status_code == 200
    first_item_data = items_backfilled.json()["items"][0]["data"]
    assert first_item_data.get("pays_origine") == "Côte d'Ivoire"
