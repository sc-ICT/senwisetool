from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict


class ResourceFieldSchema(BaseModel):
    name: str = Field(..., description="Clé technique unique du champ (ex: surface_ha, code_planteur)")
    label: str = Field(..., description="Libellé lisible affiché dans l'interface (ex: Surface en hectares)")
    type: str = Field(
        ...,
        description="Type de données: text, textarea, number, integer, decimal, boolean, currency, percentage, date, datetime, time, email, phone, url, select, multiselect, file, image, audio, video, geo_point, geo_polygon, barcode, color, json, relation"
    )
    required: bool = Field(False, description="Champ obligatoire")
    unique: bool = Field(False, description="Unicité de la valeur dans la ressource pour garantir l'authenticité")
    auto_increment: bool = Field(False, description="Auto-incrémentation automatique par le système pour ce champ")
    options: Optional[List[str]] = Field(default=None, description="Liste des options si type == select ou multiselect")
    default_value: Optional[Any] = Field(default=None, description="Valeur par défaut optionnelle")
    
    # Paramètres de relation inter-ressources (si type == relation)
    target_resource_id: Optional[int] = Field(default=None, description="ID de la ressource cible liée")
    target_resource_code: Optional[str] = Field(default=None, description="Code / slug de la ressource cible liée")
    target_version_id: Optional[int] = Field(default=None, description="ID de la version spécifique scellée liée")
    target_version_number: Optional[str] = Field(default=None, description="Numéro de la version scellée liée (ex: 1.0.0)")
    relation_type: Optional[str] = Field(default="many_to_one", description="many_to_one, one_to_many, many_to_many")
    display_field: Optional[str] = Field(default=None, description="Nom du champ de la ressource cible à afficher comme libellé")


# =====================================================================
# SCHÉMAS DE VERSION DE RESSOURCE
# =====================================================================

class ResourceVersionBase(BaseModel):
    version_number: str = Field(..., min_length=1, max_length=50, description="Numéro de version sémantique (ex: 1.0.0)")
    status: str = Field(default="draft", description="Statut (draft, published, active, archived)")
    description: Optional[str] = Field(None, description="Présentation enrichie de la ressource en Markdown")
    changelog: Optional[str] = Field(None, description="Notes de version / changelog")
    schema_fields: List[Dict[str, Any]] = Field(default_factory=list, description="Schéma dynamique des champs de cette version")
    settings: Dict[str, Any] = Field(default_factory=dict, description="Paramètres et options")
    template_governance: Dict[str, Any] = Field(default_factory=dict, description="Règles de gouvernance et permissions")


class ResourceVersionCreate(ResourceVersionBase):
    pass


class ResourceVersionCloneRequest(BaseModel):
    source_version_number: Optional[str] = Field(None, description="Version source à cloner. Si non fournie, clone la plus récente.")
    new_version_number: str = Field(..., min_length=1, max_length=50, description="Nouveau numéro de version (ex: 1.1.0)")
    changelog: Optional[str] = Field(None, description="Description des évolutions / notes de version")


class ResourceVersionUpdate(BaseModel):
    status: Optional[str] = None
    description: Optional[str] = None
    changelog: Optional[str] = None
    schema_fields: Optional[List[Dict[str, Any]]] = None
    settings: Optional[Dict[str, Any]] = None
    template_governance: Optional[Dict[str, Any]] = None


class ResourceVersionResponse(ResourceVersionBase):
    id: int
    resource_id: int
    metrics: Dict[str, Any] = Field(default_factory=dict)
    created_by_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ResourceCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=255, description="Nom complet de la ressource")
    code: str = Field(..., min_length=2, max_length=100, description="Slug / Code technique unique")
    category: Optional[str] = Field("general", description="Catégorie métier (ex: agriculture, foncier, flotte)")
    visibility: Optional[str] = Field("public", description="Visibilité multi-tenant (public, private)")
    authorized_organization_ids: Optional[List[str]] = Field(default_factory=list, description="IDs des organisations autorisées")
    cover_image_url: Optional[str] = Field(None, description="Image de couverture")
    description: Optional[str] = Field(None, description="Description opérationnelle de la ressource")
    changelog: Optional[str] = Field(None, description="Notes de version initiales")
    icon: Optional[str] = Field("Database", description="Nom de l'icône Lucide")
    status: Optional[str] = Field("draft", description="Statut initial: draft uniquement par défaut")
    version: Optional[str] = Field("1.0.0", description="Version initiale du schéma")
    is_lts: Optional[bool] = Field(False, description="Désigner cette version comme LTS")
    schema_fields: List[ResourceFieldSchema] = Field(default_factory=list, description="Liste des champs dynamiques")
    settings: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Paramètres spécifiques")
    template_governance: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Gouvernance")
    plugin_id: Optional[int] = Field(None, description="ID du plugin hôte")
    plugin_code: Optional[str] = Field(None, description="Code du plugin hôte")
    scope: Optional[str] = Field("tenant", description="Portée: tenant ou plugin_shared")
    is_shared: Optional[bool] = Field(False, description="Ressource partagée globalement via plugin")
    is_immutable: Optional[bool] = Field(False, description="Immuabilité activée")
    immutable_fields: Optional[List[str]] = Field(default_factory=list, description="Champs scellés immuables")


class ResourceUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=255)
    category: Optional[str] = None
    visibility: Optional[str] = None
    authorized_organization_ids: Optional[List[str]] = None
    cover_image_url: Optional[str] = None
    description: Optional[str] = None
    icon: Optional[str] = None
    status: Optional[str] = None
    version: Optional[str] = None
    is_lts: Optional[bool] = None
    schema_fields: Optional[List[ResourceFieldSchema]] = None
    unique_population_strategies: Optional[Dict[str, Dict[str, Any]]] = None
    schema_history: Optional[List[Dict[str, Any]]] = None
    settings: Optional[Dict[str, Any]] = None
    template_governance: Optional[Dict[str, Any]] = None
    is_immutable: Optional[bool] = None
    immutable_fields: Optional[List[str]] = None


class ResourceStatusUpdate(BaseModel):
    status: str = Field(..., description="Nouveau statut: draft, active, archived")


class ResourceCreateNewVersion(BaseModel):
    new_version: Optional[str] = Field(None, description="Version ciblée (ex: 1.1.0, 2.0.0). Auto-calculée si non renseignée")
    description: Optional[str] = Field(None, description="Commentaire de version")
    is_lts: Optional[bool] = Field(False, description="Marquer comme LTS")


class ResourceResponse(BaseModel):
    id: int
    organization_id: Optional[int] = None
    plugin_id: Optional[int] = None
    plugin_code: Optional[str] = None
    scope: str = "tenant"
    is_shared: bool = False
    is_immutable: bool = False
    immutable_fields: List[str] = []
    parent_template_id: Optional[int] = None

    name: str
    code: str
    category: str = "general"
    visibility: str = "public"
    authorized_organization_ids: List[str] = []
    cover_image_url: Optional[str] = None
    description: Optional[str] = None
    icon: str = "Database"
    status: str = "draft"
    version: str = "1.0.0"
    is_lts: bool = False
    schema_history: Optional[List[Dict[str, Any]]] = []

    schema_fields: List[Dict[str, Any]] = []
    settings: Optional[Dict[str, Any]] = None
    metrics: Optional[Dict[str, Any]] = None

    created_by_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    versions: List[ResourceVersionResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class ResourceListResponse(BaseModel):
    items: List[ResourceResponse]
    total: int
    counts: Dict[str, int]


# --- SCHÉMAS DE RÉCONCILIATION ET IMPORT DE SCHÉMA DYNAMIQUE ---

class SchemaReconciliationReport(BaseModel):
    is_coherent: bool = True
    added_count: int = 0
    updated_count: int = 0
    deleted_count: int = 0
    identical_count: int = 0
    ignored_count: int = 0
    added_fields: List[Dict[str, Any]] = []
    updated_fields: List[Dict[str, Any]] = []
    deleted_fields: List[Dict[str, Any]] = []
    identical_fields: List[str] = []
    ignored_fields: List[Dict[str, Any]] = []
    unique_population_actions: Dict[str, Any] = {}
    warnings: List[str] = []
    errors: List[str] = []
    resulting_schema: List[Dict[str, Any]] = []
    new_version: Optional[str] = None


class SchemaApplyPayload(BaseModel):
    fields: List[ResourceFieldSchema]
    dry_run: bool = False
    create_new_version: bool = True
    unique_population_strategies: Optional[Dict[str, Dict[str, Any]]] = None


# --- SCHEMAS POUR LES DONNÉES / ITEMS DE LA RESSOURCE ---

class ResourceItemCreate(BaseModel):
    data: Dict[str, Any] = Field(..., description="Données de l'enregistrement correspondant au schéma")
    resource_version_id: Optional[int] = Field(None, description="ID de la version de la ressource à laquelle appartient l'enregistrement")


class ResourceItemUpdate(BaseModel):
    data: Dict[str, Any] = Field(..., description="Données modifiées de l'enregistrement")
    resource_version_id: Optional[int] = Field(None, description="ID de la version")


class ResourceItemResponse(BaseModel):
    id: int
    resource_id: int
    resource_version_id: Optional[int] = None
    organization_id: Optional[int] = None
    data: Dict[str, Any]
    created_by_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ResourceItemListResponse(BaseModel):
    items: List[ResourceItemResponse]
    total: int
    page: int = 1
    limit: int = 50


class ResourceBulkImportPayload(BaseModel):
    items: List[Dict[str, Any]] = Field(..., min_length=1, max_length=1000, description="Liste des données à insérer")


class ResourceBulkDeletePayload(BaseModel):
    item_ids: List[int] = Field(..., min_length=1, description="Liste des IDs d'enregistrements à supprimer")


class DataImportReport(BaseModel):
    is_valid: bool = True
    can_import: bool = True
    total_rows: int = 0
    to_insert_count: int = 0
    to_update_count: int = 0
    ignored_count: int = 0
    key_field: Optional[str] = None
    update_existing: bool = True
    errors: List[str] = []
    warnings: List[str] = []
    sample_preview: List[Dict[str, Any]] = []
    imported_count: Optional[int] = None


# --- SCHEMAS POUR LES RELATIONS INTER-RESSOURCES ---

class RelatedResourceGroup(BaseModel):
    resource_id: int
    resource_name: str
    resource_code: str
    field_name: str
    field_label: str
    items: List[ResourceItemResponse]
    total: int


class RelatedItemsResponse(BaseModel):
    item_id: int
    resource_id: int
    relations: List[RelatedResourceGroup]
