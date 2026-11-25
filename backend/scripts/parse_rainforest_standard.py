import pypdf
import re
import json
import os
import sys

# Assurer que le dossier racine backend est dans sys.path
backend_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_root not in sys.path:
    sys.path.insert(0, backend_root)

from scripts.rainforest_metadata import EXIGENCES_EXACT_METADATA

PDF_PATH = "/Users/jlove/.gemini/antigravity/brain/4198a57c-a1af-4908-8be1-a6a6954eb69b/.user_uploaded/media_1794916344279_05b8f488.pdf"

CHAPTERS_METADATA = [
    {
        "numero": 1,
        "code": "CHAP-1",
        "titre": "Gestion",
        "description": "L’agriculture n’est pas seulement une manière de vivre, c’est aussi une entreprise, et la réussite en entreprise passe par une bonne gestion. Rainforest Alliance veut voir des exploitations agricoles certifiées gérées de manière efficace, transparente, ouverte et économiquement viable. À cette fin, il est nécessaire que les exploitations agricoles et les groupes mettent en œuvre un système de gestion et de planification intégré, comportant des processus et des mécanismes d’amélioration continue. La qualité des systèmes de planification et de gestion contribue à la productivité et à l’efficience des exploitations agricoles, tout en réduisant l’impact environnemental grâce à une meilleure utilisation des terres, de l’eau, des engrais et des pesticides.",
        "resultats_attendus": "• La direction réalise les évaluations des risques et élabore un plan de gestion.\n• La direction aide les membres et travailleurs du groupe en leur fournissant des services et formations.\n• La direction s'engage à promouvoir l'égalité des genres.\n• Des données fiables (GPS, membres, travailleurs) sont collectées.\n• Système d'inspection interne et auto-évaluations rigoureuses."
    },
    {
        "numero": 2,
        "code": "CHAP-2",
        "titre": "Traçabilité",
        "description": "Le succès et la crédibilité d’un programme de certification pour l’agriculture durable résident dans sa capacité à convaincre les utilisateurs que les produits certifiés sont produits conformément à la Norme. Il faut pour ce faire mettre en place un système à la fois fiable et transparent capable de suivre les produits depuis l’exploitation agricole jusqu’au revendeur en passant par chaque maillon de la chaîne d’approvisionnement.",
        "resultats_attendus": "• Estimation précise, ségrégation physique et documentation intégrale de la production certifiée.\n• Enregistrement systématique des transactions sur la plateforme de traçabilité Rainforest Alliance.\n• Respect rigoureux des règles de bilan massique et d'étiquetage des marques déposées."
    },
    {
        "numero": 3,
        "code": "CHAP-3",
        "titre": "Revenu et Responsabilité Partagée (Prime)",
        "description": "Rainforest Alliance vise à ce que la durabilité soit la norme dans les secteurs dans lesquels elle opère. Une transformation fondamentale des principes de fonctionnement de la chaîne d’approvisionnement est nécessaire pour passer à un système où la durabilité dans la production agricole est valorisée et tarifée comme un service matériel. Les coûts liés à l’amélioration des pratiques durables au niveau de la production sont partagés par le marché et les agriculteurs via le versement de primes dédiées.",
        "resultats_attendus": "• Paiement transparent en espèces de la prime de durabilité.\n• Rétrocession d'au moins 40 % de la prime directement aux producteurs et travailleurs.\n• Dépense vérifiée et tracée des primes au bénéfice des producteurs et ouvriers agricoles."
    },
    {
        "numero": 4,
        "code": "CHAP-4",
        "titre": "Agriculture",
        "description": "Ce chapitre se concentre sur les résultats de l’agriculture durable, y compris la productivité et la rentabilité des cultures, les ressources naturelles et les services écosystémiques. Il comprend également des pratiques qui contribuent aux objectifs plus larges de l'agriculture intelligente face au climat. Les activités portent sur la gestion agronomique, la fertilité des sols, la lutte intégrée contre les ravageurs et la gestion sécurisée des produits agrochimiques.",
        "resultats_attendus": "• Maintien et renforcement de la fertilité des sols et protection des ressources hydriques.\n• Mise en œuvre de la Lutte Intégrée contre les Ravageurs (LIR) et réduction des pesticides.\n• Pratiques de récolte et post-récolte préservant la qualité marchande et la santé humaine."
    },
    {
        "numero": 5,
        "code": "CHAP-5",
        "titre": "Social",
        "description": "Le chapitre social vise à donner aux producteurs et aux travailleurs les moyens d'améliorer leurs conditions de travail et de vie, à promouvoir l'égalité et le respect pour tous, en accordant une attention particulière aux groupes vulnérables tels que les migrants, les enfants, les jeunes et les femmes, et à renforcer la protection des droits de l'homme et des droits du travail dans les exploitations agricoles certifiées.",
        "resultats_attendus": "• Éradication du travail des enfants, du travail forcé, de la violence et de la discrimination.\n• Garantie de la liberté syndicale et négociation collective.\n• Salaires décents progressant vers le Salaire Minimum Vital (SMV) et contrats formels.\n• Sécurité, santé au travail et conditions d'hébergement dignes."
    },
    {
        "numero": 6,
        "code": "CHAP-6",
        "titre": "Environnement",
        "description": "L'agriculture peut avoir un impact positif ou négatif sur l'environnement naturel, en fonction de sa gestion. Ce chapitre présente des voies permettant aux exploitations agricoles certifiées d'avoir un impact positif sur la planète, les forêts, la biodiversité, l'eau et le climat, tout en interdisant formellement la déforestation et la conversion d'écosystèmes naturels.",
        "resultats_attendus": "• Zéro déforestation depuis le 1er janvier 2014 et sanctuarisation des aires protégées.\n• Préservation des zones ripariennes tampons et restauration de la végétation native.\n• Gestion sobre et économe de l'eau, dépollution des eaux usées et transition énergétique."
    }
]

SECTION_DEFINITIONS = [
    # Chapitre 1
    ("1.1", "CHAP-1", "Gestion", "Engagement de la direction du groupe en faveur d'une agriculture durable et comités de gouvernance."),
    ("1.2", "CHAP-1", "Administration", "Conformité légale, contrats de prestations, registres des membres et travailleurs, cartographie GPS polygonale."),
    ("1.3", "CHAP-1", "Évaluation des risques et Plan de gestion", "Évaluation systématique des risques de durabilité et mise en œuvre du plan de gestion pluriannuel."),
    ("1.4", "CHAP-1", "Inspection Interne et Auto-Évaluation", "Mécanismes de contrôle annuel interne, audits terrain et système de sanctions."),
    ("1.5", "CHAP-1", "Mécanisme de Réclamation", "Procédure impartiale et accessible de traitement des plaintes et litiges."),
    ("1.6", "CHAP-1", "Égalité des Genres", "Promotion active de l'égalité femmes-hommes et autonomisation des femmes."),
    ("1.7", "CHAP-1", "Les données de l’indicateur", "Collecte et transmission des indicateurs obligatoires de performance de la norme."),
    # Chapitre 2
    ("2.1", "CHAP-2", "Traçabilité", "Ségrégation physique des récoltes certifiées, pesées, bordereaux de livraison et réconciliation des volumes."),
    ("2.2", "CHAP-2", "Traçabilité sur la plateforme en ligne", "Enregistrement numérique de toutes les transactions et volumes sur la plateforme Rainforest Alliance."),
    ("2.3", "CHAP-2", "Bilan Massique", "Règles strictes de comptabilité matière et de gestion des bilans de masse autorisés."),
    # Chapitre 3
    ("3.1", "CHAP-3", "Prime", "Versement, traçabilité bancaire et affectation transparente de la Prime de durabilité pour les exploitations et ouvriers."),
    # Chapitre 4
    ("4.1", "CHAP-4", "Plantation et Rotation", "Sélection des variétés culturales adaptées, systèmes agroforestiers et rotation des cultures."),
    ("4.2", "CHAP-4", "Taille, élagage et rénovation des plantations d’arbres", "Pratiques sylvicoles et arboricoles pour maintenir la productivité et la santé du verger."),
    ("4.3", "CHAP-4", "Organismes Génétiquement Modifiés (OGM)", "Interdiction stricte des cultures et semences génétiquement modifiées."),
    ("4.4", "CHAP-4", "Conservation et Fertilité des Sols", "Lutte anti-érosive, couverture végétale, gestion des nutriments et fertilisation raisonnée."),
    ("4.5", "CHAP-4", "Lutte Intégrée contre les Ravageurs (LIR)", "Surveillance des populations nuisibles et primauté des méthodes biologiques et physiques."),
    ("4.6", "CHAP-4", "Gestion des produits agrochimiques", "Stockage sécurisé, EPI conformes, interdiction des pesticides obsolètes et gestion des emballages vides."),
    ("4.7", "CHAP-4", "Pratiques de Récoltes et Post-Récoltes", "Hygiène lors de la récolte, séchage, triage et protection contre les contaminations."),
    # Chapitre 5
    ("5.1", "CHAP-5", "Évaluation et Résolution des violations des droits humains", "Interdiction stricte du travail des enfants, travail forcé, discrimination, violences et protocole de remédiation."),
    ("5.2", "CHAP-5", "Liberté d’Association et de Convention Collective", "Droit d'organisation syndicale et représentation autonome des travailleurs."),
    ("5.3", "CHAP-5", "Salaires et Contrats", "Contrats écrits, rémunération transparente et égalité de salaire pour un travail égal."),
    ("5.4", "CHAP-5", "Salaire Minimum Vital", "Mesure et progression continue des rémunérations vers le niveau du salaire décent."),
    ("5.5", "CHAP-5", "Conditions de Travail", "Durée maximale de travail, pauses légales, congés payés et repos hebdomadaire."),
    ("5.6", "CHAP-5", "Santé et Sécurité", "Prévention des accidents du travail, eau potable, trousses de premiers secours et formations SST."),
    ("5.7", "CHAP-5", "Conditions de Vie et de Logement", "Salubrité des logements fournis, assainissement, intimité et protection des familles."),
    ("5.8", "CHAP-5", "Communautés", "Consentement libre, informé et préalable (CLIP) et relations harmonieuses de bon voisinage."),
    # Chapitre 6
    ("6.1", "CHAP-6", "Forêts, autres écosystèmes naturels et aires protégées", "Zéro déforestation, interdiction d'empiétement dans les parcs nationaux et réserves protégées."),
    ("6.2", "CHAP-6", "Conservation et Amélioration des Écosystèmes Naturels", "Restauration des corridors écologiques et plantation d'arbres indigènes."),
    ("6.3", "CHAP-6", "Zones Ripariennes Tampons", "Préservation et reboisement des bandes végétales le long des cours d'eau et berges."),
    ("6.4", "CHAP-6", "Protection de la Faune Sauvage et de la Biodiversité", "Interdiction du braconnage, protection des espèces menacées et gestion pacifique des conflits faune-homme."),
    ("6.5", "CHAP-6", "Conservation et Gestion de L’Eau", "Optimisation de l'irrigation, interdiction d'épuisement des nappes et préservation des sources."),
    ("6.6", "CHAP-6", "Gestion et eaux usées", "Traitement des effluents de lavage/transformation et interdiction des rejets directs non traités."),
    ("6.7", "CHAP-6", "Gestion des Déchets", "Tri, recyclage, élimination contrôlée et interdiction formelle du brûlage à l'air libre."),
    ("6.8", "CHAP-6", "Efficacité Énergétique", "Mesure des consommations d'énergie et réduction de l'empreinte carbone des équipements.")
]

def parse_full_pdf():
    reader = pypdf.PdfReader(PDF_PATH)
    all_pages = []
    for i, p in enumerate(reader.pages):
        all_pages.append((i + 1, p.extract_text()))

    # Build map of sections
    sec_map = {}
    for num, chap_code, title, desc in SECTION_DEFINITIONS:
        sec_map[num] = {
            "numero": num,
            "code": f"SEC-{num}",
            "chapitre_code": chap_code,
            "titre": title,
            "description": desc
        }

    # Extract requirements
    full_text = "\n".join([t for _, t in all_pages])
    # Remove header stamps
    cleaned = re.sub(r'A-1-S-B-F V1\.4FR Rainforest Alliance[^\n]+', '', full_text)

    pattern = re.compile(r'(?:\n|\A)(\d+\.\d+\.\d+)\s+([\s\S]*?)(?=(?:\n\d+\.\d+\.\d+\s+)|\Z)')
    matches = list(pattern.finditer(cleaned))
    print(f"Total raw requirement blocks matched: {len(matches)}")

    requirements = []
    seen_nums = set()

    for m in matches:
        num = m.group(1)
        if num in seen_nums:
            continue
        seen_nums.add(num)

        raw_body = m.group(2).strip()
        parts = num.split(".")
        sec_num = f"{parts[0]}.{parts[1]}"
        chap_num = int(parts[0])
        chap_code = f"CHAP-{chap_num}"

        # Clean body lines
        lines = [l.strip() for l in raw_body.split("\n") if l.strip()]
        # Remove table header noise if caught inside
        filtered_lines = []
        for line in lines:
            if line.startswith("A-1-S-B-F") or "Rainforest Alliance" in line and ("Norme" in line or "Chapitre" in line):
                continue
            if line.startswith("Petites/ Grandes") or line.startswith("Direction du groupe") or line.startswith("N°  Exigence"):
                continue
            filtered_lines.append(line)

        clean_text = "\n".join(filtered_lines)

        # Detect annex citations
        annexes = []
        for match_annex in re.finditer(r'(?:Voir|consulter)\s+([^\n\.]+(?:Annexe|Document d’orientation|Glossaire|Outil)[^\n\.]*)', clean_text, re.IGNORECASE):
            annexes.append(match_annex.group(0).strip())
        ref_annexes = " ; ".join(annexes) if annexes else ""

        # Intitulé court (first sentence)
        first_line = filtered_lines[0] if filtered_lines else f"Exigence {num}"
        dot_idx = first_line.find(". ")
        if dot_idx > 15 and dot_idx < 140:
            intitule = first_line[:dot_idx].strip()
        else:
            intitule = first_line[:120].strip()

        # Detect requirement type and exact applicability from official standard metadata
        if num in EXIGENCES_EXACT_METADATA:
            req_type, small_farms, large_farms, group_mgmt, indiv_cert = EXIGENCES_EXACT_METADATA[num]
        else:
            req_type = "Fondamentale"
            lower_txt = clean_text.lower()
            if "amélioration continue" in lower_txt or "d'amélioration continue" in lower_txt or "niveau d'amélioration" in lower_txt:
                req_type = "Amélioration continue"
            elif "spécialisée" in lower_txt or "spécialisé" in lower_txt:
                req_type = "Spécialisée"
            small_farms = True
            large_farms = True
            group_mgmt = True
            indiv_cert = True

        requirements.append({
            "numero": num,
            "code": f"EX-{num}",
            "intitule_court": intitule,
            "type_exigence": req_type,
            "texte_exigence": clean_text,
            "section_code": f"SEC-{sec_num}",
            "section_numero": sec_num,
            "chapitre_code": chap_code,
            "petites_exp_agricoles": small_farms,
            "grandes_exp_agricoles": large_farms,
            "direction_groupe": group_mgmt,
            "certif_individuelle": indiv_cert,
            "references_annexes": ref_annexes,
        })

    print(f"Extracted unique requirements: {len(requirements)}")
    return CHAPTERS_METADATA, list(sec_map.values()), requirements

if __name__ == "__main__":
    chaps, secs, reqs = parse_full_pdf()
    print("Done. Sample req 1:", reqs[0]["numero"], reqs[0]["intitule_court"])
    print("Sample req 50:", reqs[49]["numero"], reqs[49]["intitule_court"])
    print("Sample req 100:", reqs[99]["numero"], reqs[99]["intitule_court"])
    print("Sample req 147:", reqs[-1]["numero"], reqs[-1]["intitule_court"])
