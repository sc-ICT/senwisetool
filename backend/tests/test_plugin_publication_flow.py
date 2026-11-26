import asyncio
import sys
from datetime import datetime, timezone

from sqlalchemy import select, delete
from app.db.database import SessionLocal
from app.models.user.user import User, UserRole
from app.models.organization.organization import Organization
from app.models.organization.organization_plugin import (
    OrganizationPlugin,
    OrganizationPluginVersion,
)
from app.models.plugin.plugin import (
    Plugin,
    PluginVersion,
    PluginVersionTool,
    PluginStatus,
    PluginVersionStatus,
    OrgPluginStatus,
)
from app.models.project.project import Project, ProjectVersion
from app.models.notification.notification import Notification
from app.schemas.plugin.plugin import PluginCreate, PluginVersionPublish
from app.services.plugin.plugin_service import plugin_service

async def run_tests():
    async with SessionLocal() as db:
        print("=== DEBUT DES TESTS DE PUBLICATION DE VERSION & STORE ===")

        # 0. Trouver ou créer un super admin et une organisation de test
        res_user = await db.execute(select(User).limit(1))
        super_admin = res_user.scalars().first()
        assert super_admin, "Super admin introuvable"

        # Organisation A (Abonnée) et Organisation B (Non abonnée)
        res_orgs = await db.execute(select(Organization).limit(2))
        orgs = res_orgs.scalars().all()
        assert len(orgs) >= 1, "Au moins une organisation requise"
        org_a = orgs[0]

        # Nettoyer d'anciens plugins de test
        plugs_to_del = (await db.execute(select(Plugin.id).where(Plugin.code.in_(["test-pub-plug", "test-pub-private"])))).scalars().all()
        if plugs_to_del:
            await db.execute(delete(OrganizationPlugin).where(OrganizationPlugin.source_plugin_id.in_(plugs_to_del)))
            await db.execute(delete(Plugin).where(Plugin.id.in_(plugs_to_del)))
            await db.commit()

        # 1. Créer un plugin de test en brouillon
        plugin = Plugin(
            code="test-pub-plug",
            name="Plugin Test Publication",
            category="agro",
            description="Description de test",
            short_description="Court résumé",
            status=PluginStatus.DRAFT.value,
            is_public=True,
        )
        db.add(plugin)
        await db.flush()

        v1 = PluginVersion(
            plugin_id=plugin.id,
            version_number="1.0.0",
            changelog="",
            usage_policy="",
            package_data={},
            status=PluginVersionStatus.DRAFT.value,
        )
        db.add(v1)
        await db.commit()
        await db.refresh(plugin)
        await db.refresh(v1)

        print(f"✓ Plugin #{plugin.id} et Version v1.0.0 créés en brouillon")

        # Test A : Tentative de publication SANS module/fonctionnalité -> Doit échouer avec HTTP 400
        try:
            await plugin_service.publish_version(
                db=db,
                plugin_id=plugin.id,
                version_id=v1.id,
                publisher_user=super_admin,
                details_or_changelog="Détails des nouveautés v1.0.0 complets",
                usage_policy="Politique d'utilisation obligatoire EULA officielle",
            )
            assert False, "La publication aurait dû échouer car aucun outil n'est configuré"
        except Exception as e:
            assert "ajouter au moins un module ou une fonctionnalité" in str(e).lower() or "400" in str(e), f"Erreur inattendue: {e}"
            print("✓ Test A validé : Publication bloquée car 0 module/fonctionnalité")

        # Test B : Tentative de publication SANS changelog -> Doit échouer avec HTTP 400
        # Ajouter un outil projet
        tool1 = PluginVersionTool(
            plugin_version_id=v1.id,
            item_key="tool_projects",
            tool_code="projects",
            type="feature",
            config_target="tool_global",
            name="Gestion des Projets & Audits",
            description="Module de gestion de projets et audits opérationnels",
        )
        db.add(tool1)
        await db.commit()

        try:
            await plugin_service.publish_version(
                db=db,
                plugin_id=plugin.id,
                version_id=v1.id,
                publisher_user=super_admin,
                details_or_changelog="",
                usage_policy="Politique d'utilisation obligatoire EULA officielle",
            )
            assert False, "La publication aurait dû échouer car changelog vide"
        except Exception as e:
            assert "détailler" in str(e).lower() or "400" in str(e), f"Erreur inattendue: {e}"
            print("✓ Test B validé : Publication bloquée car changelog vide")

        # Test C : Tentative de publication SANS politique d'utilisation -> Doit échouer avec HTTP 400
        try:
            await plugin_service.publish_version(
                db=db,
                plugin_id=plugin.id,
                version_id=v1.id,
                publisher_user=super_admin,
                details_or_changelog="Détails du contenu de la première version avec modules opérationnels",
                usage_policy="",
            )
            assert False, "La publication aurait dû échouer car politique vide"
        except Exception as e:
            assert "politique d'utilisation" in str(e).lower() or "400" in str(e), f"Erreur inattendue: {e}"
            print("✓ Test C validé : Publication bloquée car politique d'utilisation vide")

        # Test D : Publication réussie de la version v1.0.0
        v1_pub = await plugin_service.publish_version(
            db=db,
            plugin_id=plugin.id,
            version_id=v1.id,
            publisher_user=super_admin,
            details_or_changelog="Détails du contenu de la première version v1.0.0 avec briques et modules.",
            usage_policy="Politique d'utilisation officielle et obligatoire pour SenWiseStore.",
        )
        assert v1_pub.status == PluginVersionStatus.PUBLISHED.value
        await db.refresh(plugin)
        assert plugin.status == PluginStatus.PUBLISHED.value
        print(f"✓ Test D validé : Version v1.0.0 scellée avec succès, Plugin #{plugin.id} passé à 'published'")

        # Test E : Visibilité sur le Store
        catalog_res = await plugin_service.get_marketplace_catalog(
            db=db,
            organization_id=org_a.id,
            is_super_admin=False,
        )
        items = catalog_res["items"]
        item_test = next((it for it in items if it["plugin"].id == plugin.id), None)
        assert item_test is not None, "Le plugin publié doit être visible dans le catalogue du Store"
        assert item_test["is_installed"] is False, "L'organisation ne l'a pas encore installé"
        assert item_test["has_update_available"] is False
        print("✓ Test E validé : Plugin v1.0.0 visible sur le Store pour l'organisation A (non installé, télécharger)")

        # Simuler l'installation de v1.0.0 par Org A via le service officiel
        inst_a = await plugin_service.install_plugin_for_org(
            db=db,
            plugin_id=plugin.id,
            org_id=org_a.id,
            user=super_admin,
            policy_accepted=True,
            version_id=v1.id
        )

        # Vérifier que le store affiche maintenant 'is_installed == True', 'has_update_available == False'
        cat_inst = await plugin_service.get_marketplace_catalog(
            db=db,
            organization_id=org_a.id,
            is_super_admin=False,
        )
        item_inst = next((it for it in cat_inst["items"] if it["plugin"].id == plugin.id), None)
        assert item_inst["is_installed"] is True
        assert item_inst["has_update_available"] is False
        print("✓ Test F validé : Org A est à jour sur v1.0.0 (Déjà installé)")

        # Test G : Création et publication d'une nouvelle version v1.1.0
        v2 = PluginVersion(
            plugin_id=plugin.id,
            version_number="1.1.0",
            changelog="",
            usage_policy="Politique v1.1.0",
            package_data={},
            status=PluginVersionStatus.DRAFT.value,
        )
        db.add(v2)
        await db.flush()

        tool2 = PluginVersionTool(
            plugin_version_id=v2.id,
            item_key="tool_projects",
            tool_code="projects",
            type="feature",
            config_target="tool_global",
            name="Gestion des Projets & Audits",
            description="Nouvelles fonctionnalités de reporting avancées",
        )
        db.add(tool2)
        await db.commit()

        # Compter les notifications avant publication
        notif_before = (await db.execute(select(Notification))).scalars().all()

        v2_pub = await plugin_service.publish_version(
            db=db,
            plugin_id=plugin.id,
            version_id=v2.id,
            publisher_user=super_admin,
            details_or_changelog="Notes de version v1.1.0 : Ajout du moteur d'audit avancé et métriques.",
            usage_policy="Politique v1.1.0 officielle.",
        )
        assert v2_pub.status == PluginVersionStatus.PUBLISHED.value

        # Vérifier la notification in-app envoyée au propriétaire d'Org A
        notif_after = (await db.execute(select(Notification))).scalars().all()
        assert len(notif_after) > len(notif_before), "Une notification de mise à jour aurait dû être émise"
        latest_notif = notif_after[-1]
        assert "Mise à jour disponible" in latest_notif.title
        print(f"✓ Test G validé : Notification in-app envoyée avec succès ('{latest_notif.title}')")

        org_a_id = org_a.id
        db.expire_all()
        # Test H : Le Store signale 'has_update_available == True' pour Org A
        cat_update = await plugin_service.get_marketplace_catalog(
            db=db,
            organization_id=org_a_id,
            is_super_admin=False,
        )
        item_update = next((it for it in cat_update["items"] if it["plugin"].id == plugin.id), None)
        inst_obj = item_update["installed_instance"]
        inst_ver_id = inst_obj.get("installed_version_id") if isinstance(inst_obj, dict) else (getattr(inst_obj, "installed_version_id", None) if inst_obj else None)
        print("DEBUG item_update:", {
            "is_installed": item_update["is_installed"],
            "has_update": item_update["has_update_available"],
            "installed_version_id": inst_ver_id,
            "latest_version_id": item_update["latest_version"].id,
            "latest_version_num": item_update["latest_version"].version_number,
            "plugin_versions": [(v.id, v.version_number, v.status) for v in item_update["plugin"].versions],
        })
        assert item_update["is_installed"] is True
        assert item_update["has_update_available"] is True
        assert item_update["latest_version"].version_number == "1.1.0"
        print("✓ Test H validé : Le Store signale 'Mise à jour disponible (v1.1.0)' pour Org A")

        # Pour une organisation qui n'a jamais installé (ex: id fictif 99999)
        cat_new_org = await plugin_service.get_marketplace_catalog(
            db=db,
            organization_id=99999,
            is_super_admin=False,
        )
        item_new = next((it for it in cat_new_org["items"] if it["plugin"].id == plugin.id), None)
        assert item_new["is_installed"] is False
        assert item_new["has_update_available"] is False
        print("✓ Test I validé : Une nouvelle organisation voit le bouton 'Télécharger' pour v1.1.0")

        # Nettoyage
        await db.execute(delete(OrganizationPlugin).where(OrganizationPlugin.source_plugin_id == plugin.id))
        await db.execute(delete(Plugin).where(Plugin.id == plugin.id))
        await db.commit()
        print("✓ Nettoyage effectué avec succès")
        print("\n=== TOUS LES TESTS SONT PASSÉS AVEC SUCCÈS (100%) ===")

if __name__ == "__main__":
    asyncio.run(run_tests())
