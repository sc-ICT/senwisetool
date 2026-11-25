"""
SenWiseTool Modular Tools Registry
Universal Registry of Platform Tools available to be packaged into plugins.
"""

from typing import Dict, Any, List, Optional


TOOL_CATALOG: List[Dict[str, Any]] = [
    {
        "code": "campaigns",
        "name": "Gestion des Campagnes",
        "category": "operations",
        "resource_endpoint": "/api/v1/campaigns",
        "description": "Planification temporelle, objectifs de collecte, suivi d'avancement et encadrement des saisons opérationnelles.",
        "icon": "CalendarRange",
        "is_available": True,
        "capabilities": [
            "lifecycle_management",
            "target_forms_binding",
            "target_projects_binding",
            "realtime_progress_tracking",
            "immutable_locking",
            "shared_cross_tenant",
        ],
        "supported_distribution_modes": [
            {
                "mode": "tenant",
                "label": "Mode A : Outil Indépendant & Souverain",
                "description": "L'administrateur de l'organisation créera et gérera ses propres campagnes dans son espace de travail."
            },
            {
                "mode": "universal",
                "label": "Mode B (Universel) : Miroir Centralisé Super Admin",
                "description": "Ressource de référence partagée telle quelle. Les mises à jour du Super Admin se répercutent automatiquement chez tous les abonnés."
            },
            {
                "mode": "tenant_clone",
                "label": "Mode B (Clone) : Déploiement Dédié par Organisation",
                "description": "Une instance locale autonome est créée pour chaque organisation à partir de la campagne du Super Admin."
            }
        ],
        "permissions_modes": [
            {
                "mode": "restricted",
                "label": "Restreint (Gouvernance)",
                "description": "Création et modification réservées aux administrateurs et managers de l'organisation."
            },
            {
                "mode": "shared",
                "label": "Partagé (Collaboratif)",
                "description": "Accessible aux membres opérationnels, opérateurs terrain et auditeurs pour participation et suivi."
            }
        ],
        "available_immutable_fields": [
            {"field": "name", "label": "Nom de la campagne"},
            {"field": "code", "label": "Slug / Code technique"},
            {"field": "visibility", "label": "Visibilité (Publique / Privée)"},
            {"field": "dates", "label": "Période (Dates de début et fin)"},
        ],
        "default_config": {
            "distribution_mode": "tenant",
            "permissions_mode": "restricted",
            "selected_resource_id": None,
            "selected_resource_name": None,
            "include_initial_data": True,
            "allow_creation": True,
            "allow_data_addition": True,
            "data_addition_scope": "isolated",
            "allow_data_update": False,
            "data_update_scope": "tenant_local",
            "allow_data_deletion": False,
            "allow_schema_modification": False,
            "allow_export": True,
            "allow_import": False,
            "allow_archive": True,
            "is_immutable": False,
            "immutable_fields": ["code"]
        }
    },
    {
        "code": "resources",
        "name": "Gestion des Ressources Dynamiques",
        "category": "data_management",
        "resource_endpoint": "/api/v1/resources",
        "description": "Définition de structures de données personnalisées, schémas dynamiques, pré-chargement de données certifiées et gouvernance d'accès.",
        "icon": "Database",
        "is_available": True,
        "capabilities": [
            "dynamic_schema_definition",
            "custom_fields",
            "dataset_preloading",
            "bulk_data_import",
            "multi_tenant_isolation",
            "read_only_protection",
            "shared_cross_tenant",
            "immutable_locking",
        ],
        "supported_distribution_modes": [
            {
                "mode": "tenant",
                "label": "Mode A : Outil Indépendant & Souverain",
                "description": "L'administrateur de l'organisation créera et gérera ses propres ressources et structures de données dans son espace."
            },
            {
                "mode": "universal",
                "label": "Mode B (Universel) : Miroir Centralisé Super Admin",
                "description": "Ressource de référence universelle. Toute modification ou mise à jour de schéma par le Super Admin s'applique immédiatement à tous les utilisateurs."
            },
            {
                "mode": "tenant_clone",
                "label": "Mode B (Clone) : Copie Locale par Organisation",
                "description": "Une copie locale autonome est instanciée pour chaque organisation lors de l'installation du plugin."
            }
        ],
        "permissions_modes": [
            {
                "mode": "restricted",
                "label": "Restreint (Gouvernance)",
                "description": "Création, modification du schéma et des données réservées aux administrateurs."
            },
            {
                "mode": "shared",
                "label": "Partagé (Consultation / Exploitation)",
                "description": "Lecture et exploitation des données ouvertes aux membres de l'organisation."
            }
        ],
        "available_immutable_fields": [
            {"field": "schema_fields", "label": "Structure des champs (Schéma dynamique)"},
            {"field": "name", "label": "Nom de la ressource"},
            {"field": "code", "label": "Code technique unique"},
            {"field": "category", "label": "Catégorie métier"},
        ],
        "default_config": {
            "distribution_mode": "tenant",
            "permissions_mode": "restricted",
            "selected_resource_id": None,
            "selected_resource_name": None,
            "include_initial_data": True,
            "allow_creation": True,
            "allow_data_addition": True,
            "data_addition_scope": "isolated",
            "allow_data_update": False,
            "data_update_scope": "tenant_local",
            "allow_data_deletion": False,
            "allow_schema_modification": False,
            "mandatory_fields": [],
            "locked_rows_mode": "all_initial",
            "mandatory_row_keys": [],
            "allow_export": True,
            "allow_import": False,
            "allow_archive": True,
            "is_immutable": False,
            "immutable_fields": ["schema_fields", "code"]
        }
    },
    {
        "code": "projects",
        "name": "Gestion des Projets & Audits",
        "category": "operations",
        "resource_endpoint": "/api/v1/projects",
        "description": "Pilotage des projets opérationnels, programmes de certification (EUDR, Bio, RSE), enquêtes et traçabilité.",
        "icon": "FolderKanban",
        "is_available": True,
        "capabilities": [
            "project_lifecycle",
            "form_orchestration",
            "compliance_rollups",
            "multi_tenant_isolation",
            "immutable_locking",
            "shared_cross_tenant",
        ],
        "supported_distribution_modes": [
            {
                "mode": "tenant",
                "label": "Mode A : Outil Indépendant & Souverain",
                "description": "L'organisation créera et gérera ses propres projets et audits dans son espace."
            },
            {
                "mode": "universal",
                "label": "Mode B (Universel) : Modèle Centralisé Super Admin",
                "description": "Projet modèle de référence partagé avec ses formulaires et grilles de conformité pré-configurés."
            },
            {
                "mode": "tenant_clone",
                "label": "Mode B (Clone) : Instanciation Dédiée par Organisation",
                "description": "Une copie complète du projet, de ses formulaires et critères est instanciée pour chaque organisation."
            }
        ],
        "permissions_modes": [
            {
                "mode": "restricted",
                "label": "Restreint (Gouvernance)",
                "description": "Création et modification des projets réservées aux administrateurs."
            },
            {
                "mode": "shared",
                "label": "Partagé (Opérationnel)",
                "description": "Ouvert aux chefs de projets, auditeurs et coordinateurs de zone."
            }
        ],
        "available_immutable_fields": [
            {"field": "name", "label": "Nom du projet"},
            {"field": "code", "label": "Code technique unique"},
            {"field": "category", "label": "Norme / Catégorie de conformité"},
            {"field": "project_type", "label": "Typologie opérationnelle"},
            {"field": "targets", "label": "Cibles et seuils réglementaires"},
        ],
        "default_config": {
            "distribution_mode": "tenant",
            "permissions_mode": "restricted",
            "selected_resource_id": None,
            "selected_resource_name": None,
            "include_initial_data": True,
            "allow_creation": True,
            "allow_data_addition": True,
            "data_addition_scope": "isolated",
            "allow_data_update": False,
            "data_update_scope": "tenant_local",
            "allow_data_deletion": False,
            "allow_schema_modification": False,
            "allow_export": True,
            "allow_import": False,
            "allow_archive": True,
            "is_immutable": False,
            "immutable_fields": ["category", "code"]
        }
    },
    {
        "code": "forms",
        "name": "Moteur de Formulaires Dynamiques",
        "category": "data_collection",
        "resource_endpoint": "/api/v1/forms",
        "description": "Arborescence récursive de sections, conditions et dépendances universelles, formules de calcul, scoring et liaison ressources.",
        "icon": "FileText",
        "is_available": True,
        "capabilities": [
            "dynamic_schemas",
            "recursive_sections",
            "universal_conditions",
            "formula_calculations",
            "compliance_scoring",
            "resource_binding",
            "gps_mapping",
            "offline_storage",
            "signature_capture",
            "photo_attachments"
        ],
        "supported_distribution_modes": [
            {
                "mode": "tenant",
                "label": "Mode A : Outil Indépendant & Souverain",
                "description": "L'organisation conçoit et gère ses propres formulaires sur mesure."
            },
            {
                "mode": "universal",
                "label": "Mode B (Universel) : Formulaire Centralisé Super Admin",
                "description": "L'organisation utilise la version officielle du formulaire certifié par le Super Admin."
            },
            {
                "mode": "tenant_clone",
                "label": "Mode B (Clone) : Formulaire Modèle Déployé",
                "description": "Une copie éditable locale est injectée dans l'organisation lors de l'installation du plugin."
            }
        ],
        "permissions_modes": [
            {
                "mode": "restricted",
                "label": "Restreint (Édition gouvernance)",
                "description": "Conception et modification réservées aux administrateurs."
            },
            {
                "mode": "shared",
                "label": "Partagé (Collecte terrain)",
                "description": "Saisie et soumission ouvertes aux auditeurs et agents de terrain."
            }
        ],
        "available_immutable_fields": [
            {"field": "schema", "label": "Structure des sections et questions"},
            {"field": "validation_rules", "label": "Règles d'intégrité et formules"},
            {"field": "passing_score_percentage", "label": "Seuil minimal d'audit"},
        ],
        "default_config": {
            "distribution_mode": "tenant",
            "permissions_mode": "shared",
            "selected_resource_id": None,
            "selected_resource_name": None,
            "include_initial_data": True,
            "allow_creation": True,
            "allow_data_addition": True,
            "data_addition_scope": "isolated",
            "allow_data_update": True,
            "allow_schema_modification": False,
            "is_immutable": False,
            "immutable_fields": ["schema", "validation_rules"]
        }
    },
    {
        "code": "iot_telemetry",
        "name": "Télémétrie IoT & Capteurs",
        "category": "telemetry",
        "resource_endpoint": "/api/v1/iot/devices",
        "description": "Acquisition en direct de flux de capteurs, stations météo, sondes d'humidité et pesage connecté.",
        "icon": "Cpu",
        "is_available": False,
        "capabilities": [
            "realtime_mqtt",
            "threshold_alerts",
            "device_fleet_monitoring"
        ],
        "supported_distribution_modes": [
            {
                "mode": "tenant",
                "label": "Mode A : Flotte Dédiée Indépendante",
                "description": "L'organisation administre ses capteurs en toute indépendance."
            },
            {
                "mode": "plugin_shared",
                "label": "Mode B : Réseau IoT Mutualisé Super Admin",
                "description": "L'organisation accède aux flux de capteurs mutualisés du Super Admin."
            }
        ],
        "permissions_modes": [
            {
                "mode": "restricted",
                "label": "Restreint",
                "description": "Configuration de seuils réservée aux managers."
            }
        ],
        "available_immutable_fields": [
            {"field": "thresholds", "label": "Seuils d'alerte"},
            {"field": "protocols", "label": "Protocoles de communication"}
        ],
        "default_config": {
            "distribution_mode": "tenant",
            "permissions_mode": "restricted",
            "selected_resource_id": None,
            "selected_resource_name": None,
            "include_initial_data": True,
            "allow_data_addition": True,
            "data_sharing_scope": "shared",
            "allow_schema_modification": False,
            "is_immutable": False,
            "immutable_fields": []
        }
    }
]


def get_tool_catalog(only_available: bool = True) -> List[Dict[str, Any]]:
    """
    Retourne le catalogue des outils.
    Par défaut, ne retourne que les outils activement disponibles sur la plateforme.
    """
    if only_available:
        return [tool for tool in TOOL_CATALOG if tool.get("is_available", False)]
    return TOOL_CATALOG


def get_tool_by_code(code: str) -> Optional[Dict[str, Any]]:
    for tool in TOOL_CATALOG:
        if tool["code"] == code:
            return tool
    return None
