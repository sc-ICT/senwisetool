import asyncio
import os
import sys

# Ensure backend root is in PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import text, select
from app.db.database import engine, SessionLocal
from app.models.resource.resource import Resource, ResourceStatus
from app.models.resource.resource_version import ResourceVersion, ResourceVersionStatus
from app.models.resource.resource_item import ResourceItem
from scripts.parse_rainforest_standard import parse_full_pdf


TABLES_TO_TRUNCATE = [
    # Tables locales de téléchargement (Store / Multi-tenant)
    "organization_plugin_versions",
    "organization_plugins",
    "organization_campaign_versions",
    "organization_campaigns",
    "organization_project_versions",
    "organization_projects",
    "organization_form_questions",
    "organization_form_sections",
    "organization_form_versions",
    "organization_forms",
    "organization_resource_items",
    "organization_resource_versions",
    "organization_resources",
    "organization_features",
    # Tables sources (Builder)
    "plugin_version_tools",
    "plugin_versions",
    "plugins",
    "campaign_versions",
    "campaigns",
    "project_versions",
    "projects",
    "form_questions",
    "form_sections",
    "form_versions",
    "forms",
    "resource_items",
    "resource_versions",
    "resources",
]


async def reset_database():
    print("=" * 80)
    print("1. PURGE COMPLÈTE DU STORE, DES PLUGINS, PROJETS, FORMULAIRES, CAMPAGNES, RESSOURCES")
    print("=" * 80)

    tables_sql = ", ".join(TABLES_TO_TRUNCATE)
    truncate_query = f"TRUNCATE TABLE {tables_sql} RESTART IDENTITY CASCADE;"

    async with engine.begin() as conn:
        print(f"Exécution du TRUNCATE CASCADE sur {len(TABLES_TO_TRUNCATE)} tables...")
        await conn.execute(text(truncate_query))

    # Vérification des comptes à 0
    print("\nVérification de la purge de chaque table :")
    async with engine.connect() as conn:
        all_empty = True
        for tbl in TABLES_TO_TRUNCATE:
            res = await conn.execute(text(f"SELECT COUNT(*) FROM {tbl};"))
            cnt = res.scalar()
            if cnt != 0:
                print(f"  ❌ {tbl}: {cnt} lignes restantes !")
                all_empty = False
            else:
                print(f"  ✓ {tbl}: 0")
        
        if all_empty:
            print("\n✅ Toutes les tables ont été réinitialisées avec succès (0 enregistrements).")
        else:
            raise RuntimeError("Certaines tables ne sont pas vides après le truncate !")


async def seed_rainforest():
    print("\n" + "=" * 80)
    print("2. CHARGEMENT DES EXIGENCES DE LA NORME RAINFOREST ALLIANCE (V1.4)")
    print("=" * 80)

    # 1. Parsing du PDF officiel
    print("\n[A] Extraction et structuration des données depuis le référentiel PDF...")
    chaps_data, secs_data, reqs_data = parse_full_pdf()
    print(f"  -> {len(chaps_data)} Chapitres")
    print(f"  -> {len(secs_data)} Sections")
    print(f"  -> {len(reqs_data)} Exigences certifiées")

    async with SessionLocal() as db:
        # Trouver un utilisateur admin pour created_by_id
        admin_res = await db.execute(text("SELECT id FROM users ORDER BY id ASC LIMIT 1;"))
        admin_id = admin_res.scalar() or 1
        print(f"  -> Utilisation de l'utilisateur ID {admin_id} comme créateur des ressources.")

        # ---------------------------------------------------------------------
        # Table 1 : ra_chapitres
        # ---------------------------------------------------------------------
        print("\n[B] Création de la ressource 'ra_chapitres'...")
        chapitres_schema = [
            {"name": "numero", "label": "Numéro de Chapitre", "type": "integer", "required": True, "unique": True},
            {"name": "code", "label": "Code Chapitre", "type": "text", "required": True, "unique": True},
            {"name": "titre", "label": "Titre du Chapitre", "type": "text", "required": True},
            {"name": "description", "label": "Description Opérationnelle", "type": "textarea", "required": False},
            {"name": "resultats_attendus", "label": "Résultats Attendus & Objectifs", "type": "textarea", "required": False},
        ]

        res_chapitres = Resource(
            organization_id=None,
            name="Chapitres Rainforest Alliance",
            code="ra_chapitres",
            category="certification",
            visibility="public",
            scope="tenant",
            is_shared=True,
            icon="BookOpen",
            version="1.0.0",
            status="published",
            description="Référentiel officiel des 6 grands chapitres thématiques de la Norme pour l’Agriculture Durable Rainforest Alliance (Version 1.4).",
            schema_fields=chapitres_schema,
            metrics={"item_count": len(chaps_data)},
            created_by_id=admin_id,
        )
        db.add(res_chapitres)
        await db.flush()

        ver_chapitres = ResourceVersion(
            resource_id=res_chapitres.id,
            version_number="1.0.0",
            status="published",
            description="Version initiale officielle des 6 chapitres Rainforest Alliance V1.4.",
            schema_fields=chapitres_schema,
            metrics={"item_count": len(chaps_data)},
            created_by_id=admin_id,
        )
        db.add(ver_chapitres)
        await db.flush()

        chap_id_map = {}
        for chap in chaps_data:
            item_data = {
                "numero": chap["numero"],
                "code": chap["code"],
                "titre": chap["titre"],
                "description": chap["description"],
                "resultats_attendus": chap["resultats_attendus"],
            }
            c_item = ResourceItem(
                resource_id=res_chapitres.id,
                resource_version_id=ver_chapitres.id,
                organization_id=None,
                data=item_data,
                created_by_id=admin_id,
            )
            db.add(c_item)
            await db.flush()
            chap_id_map[chap["code"]] = c_item.id
            chap_id_map[str(chap["numero"])] = c_item.id
            print(f"  ✓ Chapitre {chap['numero']}: {chap['titre']} (Item ID: {c_item.id})")

        await db.commit()

        # ---------------------------------------------------------------------
        # Table 2 : ra_sections (avec liaison vers ra_chapitres)
        # ---------------------------------------------------------------------
        print(f"\n[C] Création de la ressource 'ra_sections' reliée à 'ra_chapitres' (ID: {res_chapitres.id})...")
        sections_schema = [
            {"name": "numero", "label": "Numéro de Section", "type": "text", "required": True, "unique": True},
            {"name": "code", "label": "Code Section", "type": "text", "required": True, "unique": True},
            {"name": "titre", "label": "Titre de la Section", "type": "text", "required": True},
            {
                "name": "chapitre_id",
                "label": "Chapitre Associé",
                "type": "relation",
                "relation_type": "many_to_one",
                "target_resource_id": res_chapitres.id,
                "target_resource_code": "ra_chapitres",
                "display_field": "titre",
                "required": True,
            },
            {"name": "description", "label": "Description de la Section", "type": "textarea", "required": False},
        ]

        res_sections = Resource(
            organization_id=None,
            name="Sections Rainforest Alliance",
            code="ra_sections",
            category="certification",
            visibility="public",
            scope="tenant",
            is_shared=True,
            icon="Layers",
            version="1.0.0",
            status="published",
            description="Découpage en 34 sections opérationnelles par thématique métier de la Norme Rainforest Alliance.",
            schema_fields=sections_schema,
            metrics={"item_count": len(secs_data)},
            created_by_id=admin_id,
        )
        db.add(res_sections)
        await db.flush()

        ver_sections = ResourceVersion(
            resource_id=res_sections.id,
            version_number="1.0.0",
            status="published",
            description="Version initiale officielle des 34 sections Rainforest Alliance V1.4.",
            schema_fields=sections_schema,
            metrics={"item_count": len(secs_data)},
            created_by_id=admin_id,
        )
        db.add(ver_sections)
        await db.flush()

        sec_id_map = {}
        for sec in secs_data:
            target_chap_id = chap_id_map[sec["chapitre_code"]]
            item_data = {
                "numero": sec["numero"],
                "code": sec["code"],
                "titre": sec["titre"],
                "chapitre_id": target_chap_id,
                "description": sec["description"],
            }
            s_item = ResourceItem(
                resource_id=res_sections.id,
                resource_version_id=ver_sections.id,
                organization_id=None,
                data=item_data,
                created_by_id=admin_id,
            )
            db.add(s_item)
            await db.flush()
            sec_id_map[sec["numero"]] = s_item.id
            sec_id_map[sec["code"]] = s_item.id
            print(f"  ✓ Section {sec['numero']}: {sec['titre']} (Liaison Chapitre ID: {target_chap_id} -> Item ID: {s_item.id})")

        await db.commit()

        # ---------------------------------------------------------------------
        # Table 3 : ra_exigences (avec liaison vers ra_sections et ra_chapitres)
        # ---------------------------------------------------------------------
        print(f"\n[D] Création de la ressource 'ra_exigences' reliée à 'ra_sections' (ID: {res_sections.id}) et 'ra_chapitres' (ID: {res_chapitres.id})...")
        exigences_schema = [
            {"name": "numero", "label": "Numéro d'Exigence", "type": "text", "required": True, "unique": True},
            {"name": "code", "label": "Code Exigence", "type": "text", "required": True, "unique": True},
            {"name": "intitule_court", "label": "Intitulé Court", "type": "text", "required": True},
            {
                "name": "type_exigence",
                "label": "Type d'Exigence",
                "type": "select",
                "options": ["Fondamentale", "Spécialisée", "Amélioration continue"],
                "required": True,
            },
            {"name": "texte_exigence", "label": "Texte Intégral de l'Exigence", "type": "textarea", "required": True},
            {
                "name": "section_id",
                "label": "Section Associée",
                "type": "relation",
                "relation_type": "many_to_one",
                "target_resource_id": res_sections.id,
                "target_resource_code": "ra_sections",
                "display_field": "titre",
                "required": True,
            },
            {
                "name": "chapitre_id",
                "label": "Chapitre Associé",
                "type": "relation",
                "relation_type": "many_to_one",
                "target_resource_id": res_chapitres.id,
                "target_resource_code": "ra_chapitres",
                "display_field": "titre",
                "required": True,
            },
            {"name": "petites_exp_agricoles", "label": "Petites Exploitations (Groupe)", "type": "boolean", "required": False},
            {"name": "grandes_exp_agricoles", "label": "Grandes Exploitations (Groupe)", "type": "boolean", "required": False},
            {"name": "direction_groupe", "label": "Direction du Groupe", "type": "boolean", "required": False},
            {"name": "certif_individuelle", "label": "Certification Individuelle", "type": "boolean", "required": False},
            {"name": "references_annexes", "label": "Directives & Annexes Citées", "type": "text", "required": False},
        ]

        res_exigences = Resource(
            organization_id=None,
            name="Exigences Rainforest Alliance",
            code="ra_exigences",
            category="certification",
            visibility="public",
            scope="tenant",
            is_shared=True,
            icon="ShieldCheck",
            version="1.0.0",
            status="published",
            description="Ensemble exhaustif des 147 exigences fondamentales, d'amélioration continue et spécialisées de la Norme pour l’Agriculture Durable Rainforest Alliance (V1.4).",
            schema_fields=exigences_schema,
            metrics={"item_count": len(reqs_data)},
            created_by_id=admin_id,
        )
        db.add(res_exigences)
        await db.flush()

        ver_exigences = ResourceVersion(
            resource_id=res_exigences.id,
            version_number="1.0.0",
            status="published",
            description="Version initiale officielle des 147 exigences Rainforest Alliance V1.4.",
            schema_fields=exigences_schema,
            metrics={"item_count": len(reqs_data)},
            created_by_id=admin_id,
        )
        db.add(ver_exigences)
        await db.flush()

        inserted_ex_count = 0
        for req in reqs_data:
            sec_num = req["section_numero"]
            target_sec_id = sec_id_map.get(sec_num)
            target_chap_id = chap_id_map.get(req["chapitre_code"])

            if not target_sec_id:
                print(f"  ⚠️ Section {sec_num} non trouvée pour Req {req['numero']}")
                continue

            item_data = {
                "numero": req["numero"],
                "code": req["code"],
                "intitule_court": req["intitule_court"],
                "type_exigence": req["type_exigence"],
                "texte_exigence": req["texte_exigence"],
                "section_id": target_sec_id,
                "chapitre_id": target_chap_id,
                "petites_exp_agricoles": req["petites_exp_agricoles"],
                "grandes_exp_agricoles": req["grandes_exp_agricoles"],
                "direction_groupe": req["direction_groupe"],
                "certif_individuelle": req["certif_individuelle"],
                "references_annexes": req["references_annexes"],
            }

            e_item = ResourceItem(
                resource_id=res_exigences.id,
                resource_version_id=ver_exigences.id,
                organization_id=None,
                data=item_data,
                created_by_id=admin_id,
            )
            db.add(e_item)
            inserted_ex_count += 1
            if inserted_ex_count % 30 == 0 or inserted_ex_count == len(reqs_data):
                await db.flush()
                print(f"  ... {inserted_ex_count}/{len(reqs_data)} exigences insérées")

        await db.commit()

        print("\n" + "=" * 80)
        print("RÉSUMÉ DU CHARGEMENT RAINFOREST ALLIANCE")
        print("=" * 80)
        print(f"✓ Ressource 'ra_chapitres' (ID: {res_chapitres.id}) : {len(chaps_data)} chapitres insérés (Status: PUBLISHED)")
        print(f"✓ Ressource 'ra_sections' (ID: {res_sections.id}) : {len(secs_data)} sections liées insérées (Status: PUBLISHED)")
        print(f"✓ Ressource 'ra_exigences' (ID: {res_exigences.id}) : {inserted_ex_count} exigences liées insérées (Status: PUBLISHED)")
        print("\n🎉 BASE DE DONNÉES PRÊTE POUR LES TESTS !")


async def main():
    await reset_database()
    await seed_rainforest()


if __name__ == "__main__":
    asyncio.run(main())
