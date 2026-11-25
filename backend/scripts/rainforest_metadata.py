"""
Dictionnaire de haute fidélité extrait directement du document officiel
Norme Rainforest Alliance V1.4 - Agriculture Durable (Exigences pour les exploitations agricoles)
"""

# Dictionnaire de métadonnées des exigences : (type_exigence, [petites, grandes, direction, indiv])
EXIGENCES_EXACT_METADATA = {
    # 1.1 Gestion
    "1.1.1": ("Fondamentale", False, True, True, True),
    # 1.2 Administration
    "1.2.1": ("Fondamentale", True, True, True, True),
    "1.2.2": ("Fondamentale", False, True, True, True),
    "1.2.3": ("Fondamentale", False, False, True, False),
    "1.2.4": ("Fondamentale", False, True, True, True),
    "1.2.5": ("Fondamentale", True, False, False, False),
    "1.2.6": ("Fondamentale", False, True, True, True),
    "1.2.7": ("Fondamentale", False, False, True, False),
    "1.2.8": ("Fondamentale", True, True, True, True),
    "1.2.9": ("Fondamentale", False, True, True, True),
    "1.2.10": ("Fondamentale", False, False, True, False),
    "1.2.11": ("Fondamentale", False, True, False, True),
    # 1.3 Évaluation des risques et Plan de gestion
    "1.3.1": ("Fondamentale", False, True, True, True),
    "1.3.2": ("Fondamentale", False, True, True, True),
    "1.3.3": ("Fondamentale", False, False, True, False),
    "1.3.4": ("Fondamentale", False, True, False, True),
    # 1.4 Inspection Interne et Auto-Évaluation
    "1.4.1": ("Fondamentale", False, False, True, False),
    "1.4.2": ("Fondamentale", False, False, True, True),
    "1.4.3": ("Fondamentale", False, False, True, False),
    # 1.5 Mécanisme de Réclamation
    "1.5.1": ("Fondamentale", False, False, True, True),
    # 1.6 Égalité des Genres
    "1.6.1": ("Fondamentale", False, False, True, True),
    "1.6.2": ("Spécialisée", False, False, True, True),
    # 1.7 Les données de l’indicateur
    "1.7.1": ("Fondamentale", False, False, True, True),
    # 2.1 Traçabilité
    "2.1.1": ("Fondamentale", False, False, True, True),
    "2.1.2": ("Fondamentale", False, True, True, True),
    "2.1.3": ("Fondamentale", False, False, True, True),
    "2.1.4": ("Fondamentale", False, False, True, True),
    "2.1.5": ("Fondamentale", False, False, True, True),
    "2.1.6": ("Fondamentale", False, False, True, True),
    "2.1.7": ("Fondamentale", False, False, True, True),
    "2.1.8": ("Fondamentale", True, True, False, False),
    "2.1.9": ("Fondamentale", False, True, True, True),
    "2.1.10": ("Fondamentale", False, True, True, True),
    # 2.2 Traçabilité sur la plateforme en ligne
    "2.2.1": ("Fondamentale", False, False, True, True),
    "2.2.2": ("Fondamentale", False, False, True, True),
    "2.2.3": ("Fondamentale", False, False, True, True),
    "2.2.4": ("Fondamentale", False, False, True, True),
    # 2.3 Bilan Massique
    "2.3.1": ("Fondamentale", False, False, True, True),
    "2.3.2": ("Fondamentale", False, False, True, True),
    "2.3.3": ("Fondamentale", False, False, True, True),
    "2.3.4": ("Fondamentale", False, False, True, True),
    "2.3.5": ("Fondamentale", False, False, True, True),
    # 3.1 Prime
    "3.1.1": ("Fondamentale", False, False, True, True),
    "3.1.2": ("Fondamentale", False, False, True, True),
    "3.1.3": ("Fondamentale", False, False, True, False),
    "3.1.4": ("Fondamentale", False, True, False, True),
    # 4.1 Plantation et Rotation
    "4.1.1": ("Spécialisée", True, True, False, True),
    "4.1.2": ("Spécialisée", True, True, False, True),
    "4.1.3": ("Amélioration continue", True, True, False, True),
    # 4.2 Taille, élagage et rénovation des plantations d’arbres
    "4.2.1": ("Spécialisée", True, True, False, True),
    # 4.3 Organismes Génétiquement Modifiés (OGM)
    "4.3.1": ("Fondamentale", True, True, True, True),
    # 4.4 Conservation et Fertilité des Sols
    "4.4.1": ("Spécialisée", False, True, True, True),
    "4.4.2": ("Spécialisée", False, True, True, True),
    "4.4.3": ("Spécialisée", True, True, False, True),
    "4.4.4": ("Amélioration continue", True, True, False, True),
    "4.4.5": ("Amélioration continue", True, True, False, True),
    "4.4.6": ("Amélioration continue", True, True, False, True),
    # 4.5 Lutte Intégrée contre les Ravageurs (LIR)
    "4.5.1": ("Spécialisée", False, True, True, True),
    "4.5.2": ("Amélioration continue", True, True, False, True),
    # 4.6 Gestion des produits agrochimiques
    "4.6.1": ("Fondamentale", True, True, True, True),
    "4.6.2": ("Fondamentale", True, True, False, True),
    "4.6.3": ("Fondamentale", True, True, True, True),
    "4.6.4": ("Fondamentale", True, True, True, True),
    "4.6.5": ("Fondamentale", True, True, True, True),
    "4.6.6": ("Fondamentale", True, True, True, True),
    "4.6.7": ("Fondamentale", True, True, True, True),
    "4.6.8": ("Fondamentale", True, True, True, True),
    "4.6.9": ("Fondamentale", True, True, True, True),
    "4.6.10": ("Fondamentale", True, False, False, False),
    "4.6.11": ("Fondamentale", False, True, True, True),
    "4.6.12": ("Spécialisée", False, True, True, True),
    "4.6.13": ("Amélioration continue", True, True, True, True),
    # 4.7 Pratiques de Récoltes et Post-Récoltes
    "4.7.1": ("Fondamentale", True, True, True, True),
    "4.7.2": ("Amélioration continue", False, True, True, True),
    # 5.1 Évaluation et Résolution des violations des droits humains
    "5.1.1": ("Fondamentale", False, True, True, True),
    "5.1.2": ("Fondamentale", False, True, True, True),
    "5.1.3": ("Fondamentale", False, True, True, True),
    "5.1.4": ("Fondamentale", False, True, True, True),
    "5.1.5": ("Amélioration continue", False, True, True, True),
    "5.1.6": ("Amélioration continue", False, True, True, True),
    "5.1.7": ("Amélioration continue", False, False, True, False),
    "5.1.8": ("Amélioration continue", False, True, True, True),
    # 5.2 Liberté d’Association et de Convention Collective
    "5.2.1": ("Fondamentale", True, True, True, True),
    "5.2.2": ("Fondamentale", True, True, True, True),
    "5.2.3": ("Spécialisée", True, True, True, True),
    # 5.3 Salaires et Contrats
    "5.3.1": ("Spécialisée", True, True, True, True),
    "5.3.2": ("Spécialisée", True, True, True, True),
    "5.3.3": ("Fondamentale", True, True, True, True),
    "5.3.4": ("Fondamentale", True, True, True, True),
    "5.3.5": ("Fondamentale", True, True, True, True),
    "5.3.6": ("Fondamentale", True, True, True, True),
    "5.3.7": ("Spécialisée", True, True, True, True),
    "5.3.8": ("Amélioration continue", True, True, True, True),
    # 5.4 Salaire Minimum Vital
    "5.4.1": ("Spécialisée", False, False, True, True),
    # 5.5 Conditions de Travail
    "5.5.1": ("Fondamentale", True, True, True, True),
    "5.5.2": ("Fondamentale", True, True, True, True),
    "5.5.3": ("Fondamentale", True, True, True, True),
    "5.5.4": ("Spécialisée", True, True, True, True),
    # 5.6 Santé et Sécurité
    "5.6.1": ("Fondamentale", False, True, True, True),
    "5.6.2": ("Spécialisée", True, True, True, True),
    "5.6.3": ("Fondamentale", True, False, False, False),
    "5.6.4": ("Fondamentale", True, True, True, True),
    "5.6.5": ("Fondamentale", False, False, True, False),
    "5.6.6": ("Fondamentale", True, False, False, False),
    "5.6.7": ("Fondamentale", False, True, True, True),
    "5.6.8": ("Fondamentale", True, True, True, True),
    "5.6.9": ("Fondamentale", True, True, True, True),
    "5.6.10": ("Fondamentale", True, True, True, True),
    "5.6.11": ("Spécialisée", False, True, True, True),
    "5.6.12": ("Spécialisée", False, True, True, True),
    "5.6.13": ("Spécialisée", False, True, True, True),
    # 5.7 Conditions de Vie et de Logement
    "5.7.1": ("Fondamentale", False, True, False, True),
    "5.7.2": ("Fondamentale", False, True, False, True),
    "5.7.3": ("Fondamentale", True, False, True, False),
    "5.7.4": ("Amélioration continue", False, True, False, True),
    # 5.8 Communautés
    "5.8.1": ("Fondamentale", False, True, False, True),
    "5.8.2": ("Fondamentale", True, True, False, True),
    "5.8.3": ("Amélioration continue", False, True, False, True),
    # 6.1 Forêts, autres écosystèmes naturels et aires protégées
    "6.1.1": ("Fondamentale", True, True, False, True),
    "6.1.2": ("Fondamentale", True, True, False, True),
    "6.1.3": ("Spécialisée", False, True, True, True),
    # 6.2 Conservation et Amélioration des Écosystèmes Naturels
    "6.2.1": ("Spécialisée", False, True, True, True),
    "6.2.2": ("Spécialisée", True, True, False, True),
    "6.2.3": ("Amélioration continue", True, True, True, True),
    "6.2.4": ("Amélioration continue", True, True, False, True),
    # 6.3 Zones Ripariennes Tampons
    "6.3.1": ("Spécialisée", True, True, False, True),
    "6.3.2": ("Spécialisée", True, True, False, True),
    "6.3.3": ("Amélioration continue", True, True, False, True),
    # 6.4 Protection de la Faune Sauvage et de la Biodiversité
    "6.4.1": ("Fondamentale", True, True, True, True),
    "6.4.2": ("Fondamentale", True, True, False, True),
    "6.4.3": ("Spécialisée", True, True, False, True),
    "6.4.4": ("Spécialisée", True, True, False, True),
    "6.4.5": ("Spécialisée", True, True, False, True),
    "6.4.6": ("Fondamentale", True, True, False, True),
    # 6.5 Conservation et Gestion de L’Eau
    "6.5.1": ("Fondamentale", False, True, True, True),
    "6.5.2": ("Spécialisée", False, True, True, True),
    "6.5.3": ("Amélioration continue", False, True, True, True),
    "6.5.4": ("Amélioration continue", False, True, True, True),
    "6.5.5": ("Amélioration continue", True, True, False, True),
    "6.5.6": ("Amélioration continue", True, True, False, True),
    # 6.6 Gestion et eaux usées
    "6.6.1": ("Spécialisée", False, True, True, True),
    "6.6.2": ("Spécialisée", True, True, True, True),
    "6.6.3": ("Spécialisée", True, True, True, True),
    # 6.7 Gestion des Déchets
    "6.7.1": ("Fondamentale", True, True, True, True),
    "6.7.2": ("Fondamentale", True, True, False, True),
    "6.7.3": ("Amélioration continue", False, True, True, True),
    # 6.8 Efficacité Énergétique
    "6.8.1": ("Spécialisée", False, True, True, True),
}
