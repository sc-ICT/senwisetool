"""
Seed script to create a reference EUDR standard resource with chapters, sections, and criteria.
"""

import asyncio
from app.db.database import SessionLocal
from sqlalchemy import select
from app.models.resource import Resource, ResourceItem, ResourceStatus


EUDR_SCHEMA = [
    {"name": "chapter", "label": "Chapitre de la Norme", "type": "text", "required": True},
    {"name": "section", "label": "Sous-section / Thématique", "type": "text", "required": True},
    {"name": "criterion_code", "label": "Code du Critère", "type": "text", "required": True, "unique": True},
    {"name": "criterion_title", "label": "Intitulé du Critère d'Audit", "type": "text", "required": True},
    {"name": "verification_guideline", "label": "Directives de Vérification", "type": "textarea", "required": False},
    {"name": "verification_type", "label": "Type d'Évaluation", "type": "select", "options": ["audit_compliance", "document_file", "gps_polygon", "text_long"], "required": True},
    {"name": "target_group", "label": "Applicabilité / Cible", "type": "select", "options": ["tous", "planteur", "cooperative", "exportateur"], "required": True},
    {"name": "is_mandatory", "label": "Exigence Critique / Bloquante", "type": "boolean", "required": True},
]

EUDR_ITEMS = [
    # Chapitre I : Légalité de la Production & Droits Fonciers
    {
        "chapter": "Chapitre I : Légalité de la Production et Droits Fonciers",
        "section": "Section 1.1 : Droit d'Usage et Titre Foncier",
        "criterion_code": "EUDR-LEG-01",
        "criterion_title": "Droit légal d'utilisation et d'exploitation de la terre agricole",
        "verification_guideline": "Vérifier le titre foncier, certificat coutumier ou attestation villageoise d'exploitation légale.",
        "verification_type": "audit_compliance",
        "target_group": "planteur",
        "is_mandatory": True,
    },
    {
        "chapter": "Chapitre I : Légalité de la Production et Droits Fonciers",
        "section": "Section 1.1 : Droit d'Usage et Titre Foncier",
        "criterion_code": "EUDR-LEG-02",
        "criterion_title": "Absence de litige foncier en cours sur la parcelle auditée",
        "verification_guideline": "Interroger le comité villageois ou les riverains pour s'assurer de l'absence de conflit territorial.",
        "verification_type": "audit_compliance",
        "target_group": "planteur",
        "is_mandatory": True,
    },
    {
        "chapter": "Chapitre I : Légalité de la Production et Droits Fonciers",
        "section": "Section 1.2 : Enregistrement Réglementaire",
        "criterion_code": "EUDR-LEG-03",
        "criterion_title": "Immatriculation officielle du producteur au registre national",
        "verification_guideline": "Vérifier la carte de planteur ou le numéro d'enregistrement au conseil de régulation de la filière.",
        "verification_type": "audit_compliance",
        "target_group": "planteur",
        "is_mandatory": False,
    },

    # Chapitre II : Déforestation Zéro & Dégradation des Forêts
    {
        "chapter": "Chapitre II : Zéro Déforestation et Préservation Forestière",
        "section": "Section 2.1 : Date Butoir du 31 Décembre 2020",
        "criterion_code": "EUDR-DEF-01",
        "criterion_title": "Absence de déforestation après le 31 décembre 2020",
        "verification_guideline": "Croisement obligatoire des coordonnées GPS avec l'observatoire européen des forêts (Copernicus JRC).",
        "verification_type": "audit_compliance",
        "target_group": "tous",
        "is_mandatory": True,
    },
    {
        "chapter": "Chapitre II : Zéro Déforestation et Préservation Forestière",
        "section": "Section 2.1 : Date Butoir du 31 Décembre 2020",
        "criterion_code": "EUDR-DEF-02",
        "criterion_title": "Non-empiétement sur une aire protégée ou parc national",
        "verification_guideline": "Contrôle géospatial du polygone de la plantation par rapport aux limites officielles des forêts classées.",
        "verification_type": "audit_compliance",
        "target_group": "tous",
        "is_mandatory": True,
    },
    {
        "chapter": "Chapitre II : Zéro Déforestation et Préservation Forestière",
        "section": "Section 2.2 : Dégradation Forestière & Couvert Végétal",
        "criterion_code": "EUDR-DEF-03",
        "criterion_title": "Maintien des arbres d'ombrage et des zones tampons ripariennes",
        "verification_guideline": "Vérifier la présence d'au moins 15 arbres d'ombrage indigènes par hectare et respect de la bande riveraine.",
        "verification_type": "audit_compliance",
        "target_group": "planteur",
        "is_mandatory": False,
    },

    # Chapitre III : Géolocalisation & Traçabilité des Parcelles
    {
        "chapter": "Chapitre III : Traçabilité et Géolocalisation des Parcelles",
        "section": "Section 3.1 : Données Géospatiales Précises",
        "criterion_code": "EUDR-GEO-01",
        "criterion_title": "Relevé polygone GPS complet pour toute parcelle > 4 hectares",
        "verification_guideline": "Vérifier que le tracé polygonal comporte au moins 6 points et une précision d'au moins 5 mètres.",
        "verification_type": "audit_compliance",
        "target_group": "planteur",
        "is_mandatory": True,
    },
    {
        "chapter": "Chapitre III : Traçabilité et Géolocalisation des Parcelles",
        "section": "Section 3.1 : Données Géospatiales Précises",
        "criterion_code": "EUDR-GEO-02",
        "criterion_title": "Point GPS unique centré pour parcelles < 4 hectares",
        "verification_guideline": "Relever les coordonnées latitude/longitude avec au moins 6 décimales de précision.",
        "verification_type": "audit_compliance",
        "target_group": "planteur",
        "is_mandatory": True,
    },
    {
        "chapter": "Chapitre III : Traçabilité et Géolocalisation des Parcelles",
        "section": "Section 3.2 : Traçabilité des Flux Physiques",
        "criterion_code": "EUDR-TRAC-01",
        "criterion_title": "Tenue d'un registre de récolte et de livraison par lot",
        "verification_guideline": "Vérifier les bordereaux de pesée et tickets de livraison reliant le lot à la parcelle identifiée.",
        "verification_type": "audit_compliance",
        "target_group": "cooperative",
        "is_mandatory": True,
    },

    # Chapitre IV : Droits Humains & Droit du Travail
    {
        "chapter": "Chapitre IV : Respect des Droits Humains et du Droit du Travail",
        "section": "Section 4.1 : Protection des Enfants & Travail Décent",
        "criterion_code": "EUDR-HUM-01",
        "criterion_title": "Interdiction absolue du travail des enfants (< 15 ans)",
        "verification_guideline": "Contrôle visuel sur la parcelle et vérification des livrets scolaires des enfants du ménage.",
        "verification_type": "audit_compliance",
        "target_group": "planteur",
        "is_mandatory": True,
    },
    {
        "chapter": "Chapitre IV : Respect des Droits Humains et du Droit du Travail",
        "section": "Section 4.1 : Protection des Enfants & Travail Décent",
        "criterion_code": "EUDR-HUM-02",
        "criterion_title": "Absence de travail forcé, servage pour dette ou rétention de documents",
        "verification_guideline": "Entretien confidentiel avec les ouvriers agricoles temporaires ou permanents.",
        "verification_type": "audit_compliance",
        "target_group": "tous",
        "is_mandatory": True,
    },
    {
        "chapter": "Chapitre IV : Respect des Droits Humains et du Droit du Travail",
        "section": "Section 4.2 : Sécurité et Santé au Travail",
        "criterion_code": "EUDR-HUM-03",
        "criterion_title": "Mise à disposition d'Équipements de Protection Individuelle (EPI)",
        "verification_guideline": "Vérifier la présence et le port de bottes, gants et masques lors de l'application de traitements phytosanitaires.",
        "verification_type": "audit_compliance",
        "target_group": "planteur",
        "is_mandatory": False,
    },

    # Chapitre V : Chaîne de Contrôle & Diligence Raisonnée
    {
        "chapter": "Chapitre V : Déclaration de Diligence Raisonnée (DDS)",
        "section": "Section 5.1 : Système de Diligence Raisonnée",
        "criterion_code": "EUDR-DIL-01",
        "criterion_title": "Existence d'une procédure documentée d'évaluation des risques",
        "verification_guideline": "Vérifier le manuel interne d'assurance qualité et d'analyse de risque déforestation de l'organisation.",
        "verification_type": "audit_compliance",
        "target_group": "cooperative",
        "is_mandatory": True,
    },
    {
        "chapter": "Chapitre V : Déclaration de Diligence Raisonnée (DDS)",
        "section": "Section 5.2 : Archivage et Rétention des Preuves",
        "criterion_code": "EUDR-DIL-02",
        "criterion_title": "Archivage sécurisé des preuves d'inspection pendant au moins 5 ans",
        "verification_guideline": "Vérifier le protocole de sauvegarde numérique et physique des fiches d'audit et coordonnées GPS.",
        "verification_type": "audit_compliance",
        "target_group": "cooperative",
        "is_mandatory": True,
    },
]


async def seed():
    async with SessionLocal() as db:
        # Check if already exists
        res = await db.execute(select(Resource).where(Resource.code == "norme_eudr"))
        existing = res.scalars().first()
        if existing:
            print(f"Ressource Norme EUDR existe déjà (ID {existing.id}).")
            return existing.id

        resource = Resource(
            name="Norme EUDR - Règlement Déforestation UE",
            code="norme_eudr",
            description="Référentiel officiel des exigences et critères de contrôle du Règlement Déforestation de l'Union Européenne (RDUE / EUDR). Structuré en chapitres, sections et exigences d'audit.",
            category="norme_reglementaire",
            icon="ShieldCheck",
            scope="plugin_shared",
            is_shared=True,
            is_immutable=True,
            status=ResourceStatus.ACTIVE.value,
            version="1.0.0",
            schema_fields=EUDR_SCHEMA,
            settings={
                "standard": "EUDR",
                "authority": "Commission Européenne",
                "applicable_sectors": ["cacao", "cafe", "soja", "palme", "bois", "bovins", "caoutchouc"],
            },
            metrics={"item_count": len(EUDR_ITEMS)},
        )
        db.add(resource)
        await db.flush()

        for item_data in EUDR_ITEMS:
            item = ResourceItem(
                resource_id=resource.id,
                data=item_data,
            )
            db.add(item)

        await db.commit()
        print(f"Ressource Norme EUDR créée avec succès avec ID {resource.id} et {len(EUDR_ITEMS)} critères !")
        return resource.id


if __name__ == "__main__":
    asyncio.run(seed())
