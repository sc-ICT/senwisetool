import asyncio
import sys
import os

# Ensure backend root is in PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select, delete
from sqlalchemy.orm.attributes import flag_modified
from app.db.database import SessionLocal
from app.models.resource.resource import Resource, ResourceStatus
from app.models.resource.resource_version import ResourceVersion, ResourceVersionStatus
from app.models.resource.resource_item import ResourceItem
from scripts.parse_rainforest_standard import parse_full_pdf, CHAPTERS_METADATA, SECTION_DEFINITIONS


async def seed_rainforest_resources():
    print("=" * 70)
    print("SEEDING RAINFOREST ALLIANCE RESOURCES (V1.4 AGRICULTURE DURABLE)")
    print("=" * 70)

    # 1. Parse standard document
    print("\n[1/4] Parsing Rainforest Alliance Standard PDF...")
    chaps_data, secs_data, reqs_data = parse_full_pdf()
    print(f"Extracted: {len(chaps_data)} chapitres, {len(secs_data)} sections, {len(reqs_data)} exigences.")

    async with SessionLocal() as db:
        # 2. Cleanup existing Rainforest resources to guarantee idempotence
        print("\n[2/4] Nettoyage préalable des ressources Rainforest existantes...")
        existing_res = await db.execute(
            select(Resource).where(Resource.code.in_(["ra_chapitres", "ra_sections", "ra_exigences"]))
        )
        for res in existing_res.scalars().all():
            print(f"  Suppression ancienne ressource: {res.code} (ID: {res.id})")
            await db.delete(res)
        await db.commit()

        # 3. Step A: Resource 1 - ra_chapitres
        print("\n[3/4 - A] Création de la ressource 'ra_chapitres'...")
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
            status=ResourceStatus.ACTIVE.value,
            description="Référentiel officiel des 6 grands chapitres thématiques de la Norme pour l’Agriculture Durable Rainforest Alliance (Version 1.4).",
            schema_fields=chapitres_schema,
            metrics={"item_count": len(chaps_data)},
            created_by_id=1,
        )
        db.add(res_chapitres)
        await db.flush()

        ver_chapitres = ResourceVersion(
            resource_id=res_chapitres.id,
            version_number="1.0.0",
            status=ResourceVersionStatus.ACTIVE.value,
            description="Version initiale officielle des chapitres Rainforest Alliance V1.4.",
            schema_fields=chapitres_schema,
            metrics={"item_count": len(chaps_data)},
            created_by_id=1,
        )
        db.add(ver_chapitres)
        await db.flush()

        # Insert Chapitres Items
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
                created_by_id=1,
            )
            db.add(c_item)
            await db.flush()
            chap_id_map[chap["code"]] = c_item.id
            chap_id_map[str(chap["numero"])] = c_item.id
            print(f"  ✓ Chapitre {chap['numero']}: {chap['titre']} -> Item ID {c_item.id}")

        await db.commit()

        # 4. Step B: Resource 2 - ra_sections
        print(f"\n[3/4 - B] Création de la ressource 'ra_sections' liée à 'ra_chapitres' (ID: {res_chapitres.id})...")
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
            status=ResourceStatus.ACTIVE.value,
            description="Découpage en sections opérationnelles par thématique métier de la Norme Rainforest Alliance.",
            schema_fields=sections_schema,
            metrics={"item_count": len(secs_data)},
            created_by_id=1,
        )
        db.add(res_sections)
        await db.flush()

        ver_sections = ResourceVersion(
            resource_id=res_sections.id,
            version_number="1.0.0",
            status=ResourceVersionStatus.ACTIVE.value,
            description="Version initiale officielle des 34 sections Rainforest Alliance V1.4.",
            schema_fields=sections_schema,
            metrics={"item_count": len(secs_data)},
            created_by_id=1,
        )
        db.add(ver_sections)
        await db.flush()

        # Insert Sections Items
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
                created_by_id=1,
            )
            db.add(s_item)
            await db.flush()
            sec_id_map[sec["numero"]] = s_item.id
            sec_id_map[sec["code"]] = s_item.id
            print(f"  ✓ Section {sec['numero']}: {sec['titre']} (Chapitre Item ID: {target_chap_id}) -> Item ID {s_item.id}")

        await db.commit()

        # 5. Step C: Resource 3 - ra_exigences
        print(f"\n[3/4 - C] Création de la ressource 'ra_exigences' liée à 'ra_sections' (ID: {res_sections.id}) et 'ra_chapitres' (ID: {res_chapitres.id})...")
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
            {"name": "petites_exp_agricoles", "label": "Petites Exploitations (Groupe)", "type": "boolean", "required": False, "default_value": True},
            {"name": "grandes_exp_agricoles", "label": "Grandes Exploitations (Groupe)", "type": "boolean", "required": False, "default_value": True},
            {"name": "direction_groupe", "label": "Direction du Groupe", "type": "boolean", "required": False, "default_value": True},
            {"name": "certif_individuelle", "label": "Certification Individuelle", "type": "boolean", "required": False, "default_value": True},
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
            status=ResourceStatus.ACTIVE.value,
            description="Ensemble exhaustif des exigences fondamentales, d'amélioration continue et spécialisées de la Norme pour l’Agriculture Durable Rainforest Alliance (V1.4).",
            schema_fields=exigences_schema,
            metrics={"item_count": len(reqs_data)},
            created_by_id=1,
        )
        db.add(res_exigences)
        await db.flush()

        ver_exigences = ResourceVersion(
            resource_id=res_exigences.id,
            version_number="1.0.0",
            status=ResourceVersionStatus.ACTIVE.value,
            description="Version initiale officielle des 147 exigences Rainforest Alliance V1.4.",
            schema_fields=exigences_schema,
            metrics={"item_count": len(reqs_data)},
            created_by_id=1,
        )
        db.add(ver_exigences)
        await db.flush()

        # Insert Exigences Items
        count_ex = 0
        for req in reqs_data:
            sec_num = req["section_numero"]
            target_sec_id = sec_id_map.get(sec_num)
            target_chap_id = chap_id_map.get(req["chapitre_code"])

            if not target_sec_id:
                print(f"  ⚠️ Warning: Section {sec_num} not found in sec_id_map for Req {req['numero']}")
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
                created_by_id=1,
            )
            db.add(e_item)
            count_ex += 1
            if count_ex % 25 == 0 or count_ex == len(reqs_data):
                await db.flush()
                print(f"  ... {count_ex}/{len(reqs_data)} exigences insérées")

        await db.commit()

        print("\n[4/4] Validation des relations et des métriques...")
        print(f"  • Ressource 'ra_chapitres' (ID: {res_chapitres.id}) : {len(chaps_data)} enregistrements insérés.")
        print(f"  • Ressource 'ra_sections' (ID: {res_sections.id}) : {len(secs_data)} enregistrements liés insérés.")
        print(f"  • Ressource 'ra_exigences' (ID: {res_exigences.id}) : {count_ex} enregistrements liés insérés.")
        print("\n✅ SEEDING TERMINÉ AVEC SUCCÈS !")


if __name__ == "__main__":
    asyncio.run(seed_rainforest_resources())
