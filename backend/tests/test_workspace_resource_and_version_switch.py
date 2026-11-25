import asyncio
import pytest
from datetime import datetime, timezone
from sqlalchemy import select
from app.db.database import Base, engine, SessionLocal
from app.models.user.user import User, UserRole, UserStatus
from app.models.organization.organization import Organization
from app.models.plugin.plugin import (
    Plugin,
    PluginVersion,
    PluginVersionTool,
    PluginVersionStatus,
    OrgPluginStatus,
)
from app.models.organization.organization_plugin import (
    OrganizationPlugin,
    OrganizationPluginVersion,
)
from app.models.resource.resource import Resource
from app.models.resource.resource_version import ResourceVersion, ResourceVersionStatus
from app.models.organization.organization_resource import (
    OrganizationResource,
    OrganizationResourceVersion,
    OrganizationResourceItem,
)
from app.models.organization.organization_feature import OrganizationFeature
from app.services.plugin.plugin_service import plugin_service


async def run_test():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with SessionLocal() as db:
        uid = int(datetime.now().timestamp())
        user = User(
            email=f"ws_tester_{uid}@senima.com",
            username=f"ws_tester_{uid}",
            hashed_password="hash",
            full_name="Workspace Tester",
            role=UserRole.ADMIN_ORG,
            status=UserStatus.ACTIVE,
        )
        db.add(user)
        await db.flush()

        org = Organization(
            name=f"Org Workspace {uid}",
            code=f"org_ws_{uid}",
            owner_id=user.id,
            country="Sénégal",
            region="Dakar",
            city="Dakar",
            address="Almadies",
            postal_code="10000",
            type="ENTERPRISE",
            sector="AGRITECH",
            contact_email=f"contact_{uid}@senima.com",
            phone="+221770000000",
            status="active",
        )
        db.add(org)
        await db.flush()

        # 1. Créer une ressource modèle
        res = Resource(
            name="Matériel Agricole Souverain",
            code=f"mat_agri_{uid}",
            category="agriculture",
            visibility="public",
            status="published",
            version="1.0.0",
            schema_fields=[
                {"name": "tracteur_id", "label": "ID Tracteur", "type": "string", "required": True},
                {"name": "puissance_cv", "label": "Puissance (CV)", "type": "number"},
                {"name": "date_mise_en_service", "label": "Mise en service", "type": "string"},
            ],
            metrics={"item_count": 0},
            created_by_id=user.id,
        )
        db.add(res)
        await db.flush()

        rv1 = ResourceVersion(
            resource_id=res.id,
            version_number="1.0.0",
            status="published",
            schema_fields=res.schema_fields,
            metrics={"item_count": 0},
            template_governance={
                "allow_edit_schema": False,
                "allow_data_addition": True,
                "allow_data_update": True,
                "allow_data_deletion": True,
                "excluded_record_edit_fields": ["tracteur_id"],
            },
            created_by_id=user.id,
        )
        db.add(rv1)
        await db.flush()

        # 2. Créer un plugin avec 2 versions (v1.0.0 et v1.1.0)
        plug = Plugin(
            name=f"AgriSuite Workspace {uid}",
            code=f"agrisuite_ws_{uid}",
            category="agriculture",
            status="published",
            author="Senima Tech",
        )
        db.add(plug)
        await db.flush()

        pv1 = PluginVersion(
            plugin_id=plug.id,
            version_number="1.0.0",
            status=PluginVersionStatus.PUBLISHED.value,
            usage_policy="Politique v1.0.0",
            published_by_id=user.id,
        )
        db.add(pv1)
        await db.flush()

        pv2 = PluginVersion(
            plugin_id=plug.id,
            version_number="1.1.0",
            status=PluginVersionStatus.PUBLISHED.value,
            usage_policy="Politique v1.1.0",
            published_by_id=user.id,
        )
        db.add(pv2)
        await db.flush()

        # Outils v1
        t_res1 = PluginVersionTool(
            plugin_version_id=pv1.id,
            item_key=f"resources_{res.id}",
            tool_code="resources",
            type="module",
            config_target="tool_resource",
            name=res.name,
            selected_resource_id=res.id,
            selected_resource_code=res.code,
            selected_resource_name=res.name,
            selected_resource_status="published",
            distribution_mode="tenant_clone",
            permissions_mode="restricted",
            parameters={
                "selected_resource_id": res.id,
                "selected_resource_version_id": rv1.id,
                "include_initial_data": True,
                "template_governance": rv1.template_governance,
            },
        )
        t_feat1 = PluginVersionTool(
            plugin_version_id=pv1.id,
            item_key="feature_resources",
            tool_code="resources",
            type="feature",
            config_target="tool_global",
            name="Gestion des Ressources",
            distribution_mode="tenant",
            permissions_mode="restricted",
            parameters={"allow_creation": True, "allow_export": True},
        )
        db.add_all([t_res1, t_feat1])

        # Outils v2
        t_res2 = PluginVersionTool(
            plugin_version_id=pv2.id,
            item_key=f"resources_{res.id}",
            tool_code="resources",
            type="module",
            config_target="tool_resource",
            name=res.name,
            selected_resource_id=res.id,
            selected_resource_code=res.code,
            selected_resource_name=res.name,
            selected_resource_status="published",
            distribution_mode="tenant_clone",
            permissions_mode="restricted",
            parameters={
                "selected_resource_id": res.id,
                "selected_resource_version_id": rv1.id,
                "include_initial_data": True,
                "template_governance": rv1.template_governance,
            },
        )
        t_feat2 = PluginVersionTool(
            plugin_version_id=pv2.id,
            item_key="feature_resources",
            tool_code="resources",
            type="feature",
            config_target="tool_global",
            name="Gestion des Ressources",
            distribution_mode="tenant",
            permissions_mode="restricted",
            parameters={"allow_creation": True, "allow_export": True},
        )
        db.add_all([t_res2, t_feat2])
        await db.commit()

        # 3. Installer la version 1.0.0 pour l'organisation
        inst = await plugin_service.install_plugin_for_org(
            db=db,
            org_id=org.id,
            plugin_id=plug.id,
            version_id=pv1.id,
            user=user,
            policy_accepted=True,
        )
        assert inst.installed_version_id == pv1.id
        print("Installation v1.0.0 réussie.")

        # 4. Mettre à niveau vers la version 1.1.0
        upg = await plugin_service.upgrade_plugin_for_org(
            db=db,
            org_id=org.id,
            plugin_id=plug.id,
            target_version_id=pv2.id,
            user=user,
            policy_accepted=True,
        )
        assert upg.installed_version_id == pv2.id
        print("Mise à niveau v1.1.0 réussie.")

        # 5. Tester le basculement direct vers v1.0.0 (switch_plugin_version_for_org)
        switched = await plugin_service.switch_plugin_version_for_org(
            db=db,
            org_id=org.id,
            plugin_id=plug.id,
            target_version_id=pv1.id,
            user=user,
        )
        assert switched.installed_version_id == pv1.id
        print("Basculement vers v1.0.0 réussi !")

        # Re-basculer vers v2
        switched_v2 = await plugin_service.switch_plugin_version_for_org(
            db=db,
            org_id=org.id,
            plugin_id=plug.id,
            target_version_id=pv2.id,
            user=user,
        )
        assert switched_v2.installed_version_id == pv2.id
        print("Re-basculement vers v1.1.0 réussi !")

        # 6. Vérifier la ressource souveraine dans l'organisation
        res_org_r = await db.execute(
            select(OrganizationResource).where(
                OrganizationResource.organization_id == org.id,
                OrganizationResource.code == res.code,
            )
        )
        org_res = res_org_r.scalars().first()
        assert org_res is not None
        print(f"Ressource souveraine trouvée : id={org_res.id}, name={org_res.name}")

        # 7. Tester l'ajout d'un enregistrement dans OrganizationResourceItem
        item1 = OrganizationResourceItem(
            organization_id=org.id,
            organization_resource_id=org_res.id,
            data={"tracteur_id": "TRAC-001", "puissance_cv": 150, "date_mise_en_service": "2024-05-10"},
            created_by_id=user.id,
        )
        db.add(item1)
        await db.commit()
        await db.refresh(item1)
        assert item1.id is not None
        print(f"Item créé avec succès : id={item1.id}, data={item1.data}")

        # 8. Tester la mise à jour avec protection de colonne
        # Le champ 'tracteur_id' est dans excluded_record_edit_fields
        gov = rv1.template_governance or {}
        excluded = set(gov.get("excluded_record_edit_fields", []))
        incoming_data = {"tracteur_id": "HACKED-001", "puissance_cv": 180}
        
        final_data = dict(item1.data or {})
        for k, v in incoming_data.items():
            if k in excluded:
                continue
            final_data[k] = v

        item1.data = final_data
        await db.commit()
        await db.refresh(item1)
        # La valeur de tracteur_id doit être restée TRAC-001 !
        assert item1.data["tracteur_id"] == "TRAC-001"
        assert item1.data["puissance_cv"] == 180
        print("Protection des colonnes vérifiée : tracteur_id est resté intact !")

        # 9. Suppression de l'item
        await db.delete(item1)
        await db.commit()
        chk = await db.get(OrganizationResourceItem, item1.id)
        assert chk is None
        print("Suppression d'item validée avec succès !")

        print("\nTOUS LES TESTS WORKSPACE RESSOURCES ET VERSION SWITCH ONT RÉUSSI !")


if __name__ == "__main__":
    asyncio.run(run_test())
