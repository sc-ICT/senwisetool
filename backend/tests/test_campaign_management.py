import pytest
from httpx import AsyncClient
from datetime import datetime, timezone, timedelta
from app.core import security
from app.models.user.user import User, UserRole, UserStatus
from app.models.organization.organization import Organization, OrgStatusEnum
from app.models.organization.member import OrganizationMember
from app.models.organization.role import OrganizationRole
from app.models.campaign.campaign import Campaign, CampaignStatus


@pytest.mark.asyncio
async def test_campaign_lifecycle_and_tenant_isolation(async_client: AsyncClient, setup_db):
    from tests.conftest import TestingSessionLocal

    async with TestingSessionLocal() as session:
        # 1. Créer User A (Admin Org A) et User B (Admin Org B)
        user_a = User(
            email="admin_org_a@senma.com",
            username="admin_org_a",
            hashed_password=security.get_password_hash("PasswordA123!"),
            full_name="Alice Admin",
            role=UserRole.ADMIN_ORG,
            status=UserStatus.ACTIVE,
            is_email_verified=True,
        )
        user_b = User(
            email="admin_org_b@senma.com",
            username="admin_org_b",
            hashed_password=security.get_password_hash("PasswordB123!"),
            full_name="Bob Admin",
            role=UserRole.ADMIN_ORG,
            status=UserStatus.ACTIVE,
            is_email_verified=True,
        )
        # Collaborateur C dans Org A (Opérateur avec permissions campaigns.view seulement)
        user_c = User(
            email="operator_c@senma.com",
            username="operator_c",
            hashed_password=security.get_password_hash("PasswordC123!"),
            full_name="Charlie Operator",
            role=UserRole.OPERATOR,
            status=UserStatus.ACTIVE,
            is_email_verified=True,
        )
        session.add_all([user_a, user_b, user_c])
        await session.commit()
        await session.refresh(user_a)
        await session.refresh(user_b)
        await session.refresh(user_c)

        # 2. Créer Organisation A et Organisation B
        org_a = Organization(
            name="Coopérative Cacao A",
            code="SWT-COOP-A",
            country="Côte d'Ivoire",
            type="Coopérative",
            owner_id=user_a.id,
            contact_email="contact@coop-a.com",
            status=OrgStatusEnum.ACTIVE.value,
        )
        org_b = Organization(
            name="Société Cacao B",
            code="SWT-COOP-B",
            country="Ghana",
            type="Entreprise",
            owner_id=user_b.id,
            contact_email="contact@coop-b.com",
            status=OrgStatusEnum.ACTIVE.value,
        )
        session.add_all([org_a, org_b])
        await session.commit()
        await session.refresh(org_a)
        await session.refresh(org_b)

        # Rôle Opérateur dans Org A
        role_opr = OrganizationRole(
            organization_id=org_a.id,
            name="Opérateur Terrain",
            code="OPR",
            description="Collecte terrain et consultation",
            permissions={
                "campaigns": {"view": "active", "create_edit": "inactive", "delete": "inactive"},
                "forms": {"view": "active", "create_edit": "active", "delete": "inactive"},
            },
            is_system=True,
        )
        session.add(role_opr)
        await session.commit()
        await session.refresh(role_opr)

        # Membre C rattaché à Org A avec rôle Opérateur
        member_c = OrganizationMember(
            organization_id=org_a.id,
            user_id=user_c.id,
            name="Charlie Operator",
            email=user_c.email,
            status="actif",
            role=role_opr,
            role_code=role_opr.code,
        )
        session.add(member_c)
        await session.commit()

    token_a = security.create_access_token(
        subject=user_a.id,
        extra_claims={"email": user_a.email, "role": "ADMIN_ORG"}
    )
    headers_a = {"Authorization": f"Bearer {token_a}"}

    token_b = security.create_access_token(
        subject=user_b.id,
        extra_claims={"email": user_b.email, "role": "ADMIN_ORG"}
    )
    headers_b = {"Authorization": f"Bearer {token_b}"}

    token_c = security.create_access_token(
        subject=user_c.id,
        extra_claims={"email": user_c.email, "role": "OPERATOR"}
    )
    headers_c = {"Authorization": f"Bearer {token_c}"}

    # ==========================================
    # TEST 1 : Création de campagne par Admin Org A
    # ==========================================
    campaign_data = {
        "name": "Campagne Récolte Principale 2026",
        "code": "recolte-principale-2026",
        "campaign_type": "harvest",
        "description": "Campagne de collecte et géolocalisation des parcelles cacao.",
        "objectives": "Atteindre 1500 parcelles géocartographiées.",
        "targets": {"target_farmers": 500, "target_parcels": 1500},
        "budget": 2500000.0,
        "currency": "XOF",
    }
    res_create = await async_client.post(
        "/api/v1/campaigns",
        json=campaign_data,
        headers=headers_a
    )
    assert res_create.status_code == 201
    created_camp = res_create.json()
    camp_id = created_camp["id"]
    assert created_camp["status"] == "draft"
    assert created_camp["organization_id"] == org_a.id
    assert created_camp["code"] == "recolte-principale-2026"

    # ==========================================
    # TEST 2 : Unicité du code au sein d'une organisation
    # ==========================================
    res_dup = await async_client.post(
        "/api/v1/campaigns",
        json=campaign_data,
        headers=headers_a
    )
    assert res_dup.status_code == 400
    assert "existe déjà" in res_dup.json()["detail"]

    # ==========================================
    # TEST 3 : Cloisonnement strict multi-tenant
    # User B (Org B) ne peut pas voir ni modifier la campagne de Org A
    # ==========================================
    res_b_view = await async_client.get(
        f"/api/v1/campaigns/{camp_id}",
        headers=headers_b
    )
    assert res_b_view.status_code == 403

    res_b_list = await async_client.get(
        "/api/v1/campaigns",
        headers=headers_b
    )
    assert res_b_list.status_code == 200
    assert res_b_list.json()["total"] == 0
    assert len(res_b_list.json()["items"]) == 0

    # User B ne peut pas modifier la campagne de Org A
    res_b_update = await async_client.put(
        f"/api/v1/campaigns/{camp_id}",
        json={"name": "Hacked Campaign"},
        headers=headers_b
    )
    assert res_b_update.status_code == 403

    # ==========================================
    # TEST 4 : RBAC - Collaborateur C (Opérateur terrain avec view-only)
    # ==========================================
    # Peut consulter la campagne de son organisation
    res_c_view = await async_client.get(
        f"/api/v1/campaigns/{camp_id}",
        headers=headers_c
    )
    assert res_c_view.status_code == 200
    assert res_c_view.json()["id"] == camp_id

    # Ne peut pas créer de campagne (create_edit inactive)
    res_c_create = await async_client.post(
        "/api/v1/campaigns",
        json={
            "name": "Campagne Non Autorisée",
            "code": "camp-unauthorized",
            "campaign_type": "survey"
        },
        headers=headers_c
    )
    assert res_c_create.status_code == 403
    assert "Permissions insuffisantes" in res_c_create.json()["detail"]

    # ==========================================
    # TEST 5 : Mise à jour et transition de statut
    # ==========================================
    res_update = await async_client.put(
        f"/api/v1/campaigns/{camp_id}",
        json={"objectives": "Nouvel objectif : 2000 parcelles géocartographiées."},
        headers=headers_a
    )
    assert res_update.status_code == 200
    assert res_update.json()["objectives"] == "Nouvel objectif : 2000 parcelles géocartographiées."

    # Ouverture de la campagne (open)
    res_open = await async_client.post(
        f"/api/v1/campaigns/{camp_id}/status",
        json={"status": "open", "reason": "Lancement officiel de la saison"},
        headers=headers_a
    )
    assert res_open.status_code == 200
    assert res_open.json()["status"] == "open"

    # Tentative de suppression alors que la campagne est ouverte -> DOIT ÉCHOUER (400)
    res_del_open = await async_client.delete(
        f"/api/v1/campaigns/{camp_id}",
        headers=headers_a
    )
    assert res_del_open.status_code == 400
    assert "Seule une campagne au statut 'brouillon' peut être supprimée" in res_del_open.json()["detail"]

    # Clôture de la campagne (closed)
    res_close = await async_client.post(
        f"/api/v1/campaigns/{camp_id}/status",
        json={"status": "closed", "reason": "Fin de récolte atteinte"},
        headers=headers_a
    )
    assert res_close.status_code == 200
    assert res_close.json()["status"] == "closed"
    assert res_close.json()["closed_at"] is not None

    # Modification impossible d'une campagne clôturée (scellée)
    res_update_closed = await async_client.put(
        f"/api/v1/campaigns/{camp_id}",
        json={"name": "Tentative de modification après clôture"},
        headers=headers_a
    )
    assert res_update_closed.status_code == 400
    assert "scellée" in res_update_closed.json()["detail"]

    # ==========================================
    # TEST 6 : Suppression légitime d'un brouillon
    # ==========================================
    res_draft_camp = await async_client.post(
        "/api/v1/campaigns",
        json={
            "name": "Campagne Test Brouillon",
            "code": "test-brouillon-temp",
            "campaign_type": "survey"
        },
        headers=headers_a
    )
    assert res_draft_camp.status_code == 201
    draft_id = res_draft_camp.json()["id"]

    res_del_draft = await async_client.delete(
        f"/api/v1/campaigns/{draft_id}",
        headers=headers_a
    )
    assert res_del_draft.status_code == 200
    assert "supprimée avec succès" in res_del_draft.json()["message"]


@pytest.mark.asyncio
async def test_shared_and_immutable_tools_governance(async_client: AsyncClient, setup_db):
    from tests.conftest import TestingSessionLocal
    from app.models.plugin.plugin import Plugin, PluginVersion, PluginStatus, PluginVersionStatus, OrgPluginStatus
    from app.models.organization.organization_plugin import OrganizationPlugin

    async with TestingSessionLocal() as session:
        # 1. Super Admin, User Org A et User Org B
        super_admin = User(
            email="super_admin_tools@senma.com",
            username="super_admin_tools",
            hashed_password=security.get_password_hash("SuperAdmin123!"),
            full_name="Super Admin Platform",
            role=UserRole.SUPER_ADMIN,
            status=UserStatus.ACTIVE,
            is_email_verified=True,
        )
        user_org_a = User(
            email="admin_org_cocoa@senma.com",
            username="admin_org_cocoa",
            hashed_password=security.get_password_hash("PassCocoa123!"),
            full_name="Admin Cocoa Org",
            role=UserRole.ADMIN_ORG,
            status=UserStatus.ACTIVE,
            is_email_verified=True,
        )
        user_org_b = User(
            email="admin_org_mining@senma.com",
            username="admin_org_mining",
            hashed_password=security.get_password_hash("PassMining123!"),
            full_name="Admin Mining Org",
            role=UserRole.ADMIN_ORG,
            status=UserStatus.ACTIVE,
            is_email_verified=True,
        )
        session.add_all([super_admin, user_org_a, user_org_b])
        await session.commit()
        await session.refresh(super_admin)
        await session.refresh(user_org_a)
        await session.refresh(user_org_b)

        # 2. Organisations
        org_a = Organization(
            name="Coopérative Cacao Ivoire",
            code="SWT-COOP-IV",
            country="Côte d'Ivoire",
            type="Coopérative",
            owner_id=user_org_a.id,
            contact_email="contact@cocoa-iv.com",
            status=OrgStatusEnum.ACTIVE.value,
        )
        org_b = Organization(
            name="Compagnie Minière Sahel",
            code="SWT-MINE-SA",
            country="Mali",
            type="Entreprise",
            owner_id=user_org_b.id,
            contact_email="contact@mining-sa.com",
            status=OrgStatusEnum.ACTIVE.value,
        )
        session.add_all([org_a, org_b])
        await session.commit()
        await session.refresh(org_a)
        await session.refresh(org_b)

        # 3. Créer un Plugin "Cacao & Forêt EUDR" avec un template préconfiguré immuable
        plugin_eudr = Plugin(
            code="cocoa-eudr",
            name="Cacao & Forêt EUDR",
            category="Agro-alimentaire",
            status=PluginStatus.PUBLISHED.value,
            author="SenWise Standard"
        )
        session.add(plugin_eudr)
        await session.commit()
        await session.refresh(plugin_eudr)

        version_eudr = PluginVersion(
            plugin_id=plugin_eudr.id,
            version_number="1.0.0",
            status=PluginVersionStatus.PUBLISHED.value,
            usage_policy="Usage policy EUDR",
            package_data={
                "tools": {
                    "campaigns": {
                        "mode": "preconfigured",
                        "templates": [
                            {
                                "id": 101,
                                "name": "Audit Réglementaire EUDR 2026",
                                "code": "audit-eudr-2026",
                                "campaign_type": "audit",
                                "is_immutable": True,
                                "immutable_fields": ["campaign_type", "name", "target_forms"],
                                "description": "Modèle officiel immuable conforme à la réglementation européenne.",
                                "targets": {"target_parcels": 3000}
                            }
                        ]
                    }
                }
            }
        )
        session.add(version_eudr)
        await session.commit()
        await session.refresh(version_eudr)

        # Org A installe le plugin EUDR
        inst_a = OrganizationPlugin(
            organization_id=org_a.id,
            plugin_id=plugin_eudr.id,
            installed_version_id=version_eudr.id,
            status=OrgPluginStatus.INSTALLED.value,
            policy_version="1.0.0",
            policy_accepted_at=datetime.now(timezone.utc),
            policy_accepted_by_user_id=user_org_a.id,
        )
        session.add(inst_a)
        await session.commit()

    token_super = security.create_access_token(
        subject=super_admin.id,
        extra_claims={"email": super_admin.email, "role": "SUPER_ADMIN"}
    )
    headers_super = {"Authorization": f"Bearer {token_super}"}

    token_org_a = security.create_access_token(
        subject=user_org_a.id,
        extra_claims={"email": user_org_a.email, "role": "ADMIN_ORG"}
    )
    headers_org_a = {"Authorization": f"Bearer {token_org_a}"}

    token_org_b = security.create_access_token(
        subject=user_org_b.id,
        extra_claims={"email": user_org_b.email, "role": "ADMIN_ORG"}
    )
    headers_org_b = {"Authorization": f"Bearer {token_org_b}"}

    # ==========================================
    # CAS 1 : Non-Super Admin tente de créer une campagne partagée -> 403
    # ==========================================
    res_fake_shared = await async_client.post(
        "/api/v1/campaigns",
        json={
            "name": "Tentative Campagne Partagée",
            "code": "tentative-partagee",
            "is_shared": True,
            "scope": "plugin_shared",
            "plugin_id": plugin_eudr.id
        },
        headers=headers_org_a
    )
    assert res_fake_shared.status_code == 403
    assert "Seul le Super Administrateur" in res_fake_shared.json()["detail"]

    # ==========================================
    # CAS 2 : Super Admin crée une campagne partagée pour le plugin EUDR
    # ==========================================
    res_shared_create = await async_client.post(
        "/api/v1/campaigns",
        json={
            "name": "Campagne Sectorielle Cacao 2026-2027",
            "code": "campagne-sectorielle-cacao-2026",
            "campaign_type": "harvest",
            "is_shared": True,
            "scope": "plugin_shared",
            "plugin_id": plugin_eudr.id,
            "description": "Campagne globale partagée entre tous les adhérents du plugin EUDR."
        },
        headers=headers_super
    )
    assert res_shared_create.status_code == 201
    shared_camp = res_shared_create.json()
    assert shared_camp["is_shared"] is True
    assert shared_camp["scope"] == "plugin_shared"
    shared_camp_id = shared_camp["id"]

    # ==========================================
    # CAS 3 : Visibilité selon l'installation du plugin
    # Org A (ayant le plugin installé) voit la campagne partagée
    # ==========================================
    res_org_a_list = await async_client.get(
        "/api/v1/campaigns",
        headers=headers_org_a
    )
    assert res_org_a_list.status_code == 200
    camp_ids_a = [c["id"] for c in res_org_a_list.json()["items"]]
    assert shared_camp_id in camp_ids_a

    # Org B (n'ayant PAS le plugin installé) NE voit PAS la campagne partagée
    res_org_b_list = await async_client.get(
        "/api/v1/campaigns",
        headers=headers_org_b
    )
    assert res_org_b_list.status_code == 200
    camp_ids_b = [c["id"] for c in res_org_b_list.json()["items"]]
    assert shared_camp_id not in camp_ids_b

    # ==========================================
    # CAS 4 : Protection de la campagne partagée
    # L'admin d'organisation ne peut pas modifier la campagne partagée
    # ==========================================
    res_org_a_edit_shared = await async_client.put(
        f"/api/v1/campaigns/{shared_camp_id}",
        json={"name": "Nom modifié illégalement"},
        headers=headers_org_a
    )
    assert res_org_a_edit_shared.status_code == 403
    assert "gérée exclusivement par le Super Administrateur" in res_org_a_edit_shared.json()["detail"]

    # ==========================================
    # CAS 5 : Campagnes préconfigurées et Gouvernance de l'immuabilité
    # ==========================================
    # Provisionner le template de version pour Org A
    from app.services.campaign.campaign_service import campaign_service
    async with TestingSessionLocal() as session:
        plug_loaded = await session.get(Plugin, plugin_eudr.id)
        vers_loaded = await session.get(PluginVersion, version_eudr.id)
        u_loaded = await session.get(User, user_org_a.id)
        await campaign_service.provision_plugin_templates(
            db=session,
            org_id=org_a.id,
            plugin=plug_loaded,
            version=vers_loaded,
            user=u_loaded
        )

    # Récupérer la campagne préconfigurée
    res_list_after_prov = await async_client.get(
        "/api/v1/campaigns",
        headers=headers_org_a
    )
    prov_camp = next((c for c in res_list_after_prov.json()["items"] if c["code"] == "audit-eudr-2026"), None)
    assert prov_camp is not None
    assert prov_camp["is_immutable"] is True
    prov_camp_id = prov_camp["id"]

    # Org A tente de modifier le nom verrouillé de la campagne immuable -> 400 Bad Request
    res_edit_immutable = await async_client.put(
        f"/api/v1/campaigns/{prov_camp_id}",
        json={"name": "Nouveau Nom Audit Modifié"},
        headers=headers_org_a
    )
    assert res_edit_immutable.status_code == 400
    assert "immuable" in res_edit_immutable.json()["detail"]

    # Org A peut en revanche adapter les champs personnalisables (ex: budget)
    res_edit_customizable = await async_client.put(
        f"/api/v1/campaigns/{prov_camp_id}",
        json={"budget": 7500000.0},
        headers=headers_org_a
    )
    assert res_edit_customizable.status_code == 200
    assert float(res_edit_customizable.json()["budget"]) == 7500000.0

