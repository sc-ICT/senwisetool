import asyncio
import pytest
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select, func

from app.db.database import Base, engine, SessionLocal
from app.models.organization.organization import Organization
from app.models.user.user import User, UserRole, UserStatus
from app.models.plugin.plugin import (
    Plugin,
    PluginVersion,
    PluginVersionTool,
    OrganizationPlugin,
    OrganizationPluginVersion,
    PluginStatus,
    PluginVersionStatus,
    OrgPluginStatus,
)
from app.models.resource.resource import Resource, ResourceStatus
from app.models.resource.resource_version import ResourceVersion, ResourceVersionStatus
from app.models.resource.resource_item import ResourceItem
from app.models.organization.organization_resource import (
    OrganizationResource,
    OrganizationResourceVersion,
    OrganizationResourceItem,
)
from app.models.organization.organization_feature import OrganizationFeature
from app.services.plugin.plugin_service import plugin_service


@pytest.mark.asyncio
async def test_organization_resource_download_and_provisioning():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with SessionLocal() as db:
        now = datetime.now(timezone.utc)
        test_suffix = int(now.timestamp())

        # 1. Créer un utilisateur test
        user = User(
            email=f"tester_{test_suffix}@senima.com",
            username=f"tester_{test_suffix}",
            hashed_password="hash",
            full_name="Test Prov User",
            role=UserRole.ADMIN_ORG,
            status=UserStatus.ACTIVE,
        )
        db.add(user)
        await db.flush()

        # 2. Créer une organisation de test
        org = Organization(
            name=f"Org Test Prov {test_suffix}",
            code=f"org_prov_{test_suffix}",
            country="France",
            type="Entreprise",
            owner_id=user.id,
            contact_email=user.email,
            status="active",
        )
        db.add(org)
        await db.flush()

        user.organization_id = org.id
        await db.flush()

        # 3. Créer une Ressource d'origine avec version et items
        source_res = Resource(
            name=f"Catalogue Agricole {test_suffix}",
            code=f"cat_agri_{test_suffix}",
            category="agriculture",
            visibility="public",
            status=ResourceStatus.PUBLISHED.value,
            version="1.0.0",
            schema_fields=[
                {"name": "code_parcelle", "type": "text", "label": "Code Parcelle", "required": True},
                {"name": "superficie", "type": "number", "label": "Superficie Ha", "required": True},
                {"name": "statut", "type": "text", "label": "Statut", "required": False},
            ],
            settings={"exportable": True},
            created_by_id=user.id,
        )
        db.add(source_res)
        await db.flush()

        source_rv = ResourceVersion(
            resource_id=source_res.id,
            version_number="1.0.0",
            status=ResourceVersionStatus.PUBLISHED.value,
            description="Version initiale publiée avec données",
            schema_fields=source_res.schema_fields,
            metrics={"item_count": 2},
            template_governance={
                "allow_edit_schema": True,
                "allow_delete_schema_fields": True,
                "allow_add_records": True,
                "allow_edit_records": True,
                "allow_delete_records": False,
                "excluded_schema_edit_fields": ["code_parcelle"],
                "excluded_schema_delete_fields": ["code_parcelle", "superficie"],
                "excluded_record_edit_fields": ["code_parcelle"],
            },
            created_by_id=user.id,
        )
        db.add(source_rv)
        await db.flush()

        item1 = ResourceItem(
            resource_id=source_res.id,
            resource_version_id=source_rv.id,
            data={"code_parcelle": "PARC-001", "superficie": 5.4, "statut": "Actif"},
            created_by_id=user.id,
        )
        item2 = ResourceItem(
            resource_id=source_res.id,
            resource_version_id=source_rv.id,
            data={"code_parcelle": "PARC-002", "superficie": 12.0, "statut": "En jachère"},
            created_by_id=user.id,
        )
        db.add_all([item1, item2])
        await db.flush()

        # 4. Créer un Plugin et sa PluginVersion
        plugin = Plugin(
            name=f"Plugin Agri {test_suffix}",
            code=f"plugin_agri_{test_suffix}",
            category="agriculture",
            status=PluginStatus.PUBLISHED.value,
            author="SenWise",
            is_public=True,
        )
        db.add(plugin)
        await db.flush()

        pv = PluginVersion(
            plugin_id=plugin.id,
            version_number="1.0.0",
            status=PluginVersionStatus.PUBLISHED.value,
            usage_policy="Politique de test d'usage",
            package_data={"plugin_parameters": {}, "tools": []},
            published_by_id=user.id,
            published_at=now,
        )
        db.add(pv)
        await db.flush()

        # 5. Ajouter le PluginVersionTool de type module pour cette ressource avec include_initial_data = True
        tool = PluginVersionTool(
            plugin_version_id=pv.id,
            item_key=f"resources_{source_res.id}",
            tool_code="resources",
            type="module",
            config_target="tool_resource",
            name=source_res.name,
            selected_resource_id=source_res.id,
            selected_resource_code=source_res.code,
            selected_resource_name=source_res.name,
            selected_resource_status="published",
            distribution_mode="tenant_clone",
            permissions_mode="restricted",
            parameters={
                "selected_resource_id": source_res.id,
                "selected_resource_version_id": source_rv.id,
                "include_initial_data": True,
                "template_governance": source_rv.template_governance,
            },
        )
        db.add(tool)
        await db.commit()

        # 6. Exécuter l'installation du plugin par l'organisation
        inst = await plugin_service.install_plugin_for_org(
            db=db,
            org_id=org.id,
            plugin_id=plugin.id,
            policy_accepted=True,
            user=user,
            version_id=pv.id,
        )

        # 7. Vérifications strictes :
        # A. OrganizationPlugin
        assert inst is not None
        assert inst.organization_id == org.id
        assert inst.installed_version_id == pv.id
        assert inst.status == OrgPluginStatus.INSTALLED.value

        # B. OrganizationPluginVersion
        res_opv = await db.execute(
            select(OrganizationPluginVersion).where(
                OrganizationPluginVersion.organization_id == org.id,
                OrganizationPluginVersion.organization_plugin_id == inst.id,
            )
        )
        opv = res_opv.scalars().first()
        assert opv is not None
        assert opv.version_number == "1.0.0"

        # C. OrganizationFeature automatique pour "resources"
        res_feat = await db.execute(
            select(OrganizationFeature).where(
                OrganizationFeature.organization_id == org.id,
                OrganizationFeature.feature_code == "resources",
                OrganizationFeature.organization_plugin_version_id == opv.id,
            )
        )
        feat = res_feat.scalars().first()
        assert feat is not None, "OrganizationFeature pour 'resources' doit être automatiquement créée !"
        assert feat.status == "active"

        # D. OrganizationResource
        res_org_r = await db.execute(
            select(OrganizationResource).where(
                OrganizationResource.organization_id == org.id,
                OrganizationResource.organization_plugin_id == inst.id,
                OrganizationResource.original_resource_id == source_res.id,
            )
        )
        org_r = res_org_r.scalars().first()
        assert org_r is not None, "OrganizationResource doit être instanciée dans l'organisation !"
        assert org_r.name == source_res.name
        assert org_r.organization_plugin_version_id == opv.id

        # E. OrganizationResourceVersion
        res_org_rv = await db.execute(
            select(OrganizationResourceVersion).where(
                OrganizationResourceVersion.organization_id == org.id,
                OrganizationResourceVersion.organization_resource_id == org_r.id,
                OrganizationResourceVersion.version_number == "1.0.0",
            )
        )
        org_rv = res_org_rv.scalars().first()
        assert org_rv is not None, "OrganizationResourceVersion doit être instanciée !"
        assert org_rv.organization_plugin_version_id == opv.id
        assert org_rv.template_governance["excluded_schema_edit_fields"] == ["code_parcelle"]
        assert org_rv.template_governance["excluded_record_edit_fields"] == ["code_parcelle"]

        # F. OrganizationResourceItem (Données copiées car include_initial_data=True)
        res_items = await db.execute(
            select(OrganizationResourceItem).where(
                OrganizationResourceItem.organization_id == org.id,
                OrganizationResourceItem.organization_resource_id == org_r.id,
                OrganizationResourceItem.organization_resource_version_id == org_rv.id,
            )
        )
        items = list(res_items.scalars().all())
        assert len(items) == 2, f"Les 2 enregistrements de données devaient être clonés, trouvé {len(items)}"
        codes = [it.data.get("code_parcelle") for it in items]
        assert "PARC-001" in codes
        assert "PARC-002" in codes

        print("\nTest réussi : OrganizationResource, Version, Items et Feature validés avec succès !")


if __name__ == "__main__":
    asyncio.run(test_organization_resource_download_and_provisioning())
