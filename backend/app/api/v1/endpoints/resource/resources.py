from typing import Optional, Dict, Any, List
from fastapi import APIRouter, Depends, Query, status, UploadFile, File, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.api.deps import get_current_user
from app.models.user.user import User, UserRole
from app.schemas.resource.resource import (
    ResourceCreate,
    ResourceUpdate,
    ResourceResponse,
    ResourceListResponse,
    ResourceFieldSchema,
    SchemaReconciliationReport,
    SchemaApplyPayload,
    ResourceItemCreate,
    ResourceItemUpdate,
    ResourceItemResponse,
    ResourceItemListResponse,
    ResourceBulkImportPayload,
    ResourceCreateNewVersion,
    DataImportReport,
)
from app.services.resource.resource_service import resource_service

router = APIRouter()


@router.get("/schema/template", summary="Télécharger le gabarit Excel officiel de schéma")
async def download_schema_template():
    """
    Génère et télécharge le gabarit Excel (.xlsx) pré-rempli avec des exemples et la liste
    exhaustive de tous les types de données supportés.
    """
    excel_bytes = resource_service.get_schema_excel_template()
    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=gabarit_schema_ressource.xlsx"},
    )


@router.post("/schema/parse-file", response_model=List[ResourceFieldSchema], summary="Parser un fichier de schéma (Excel, CSV ou JSON)")
async def parse_schema_file(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    """
    Lit un fichier Excel (.xlsx), CSV (.csv) ou JSON (.json) et extrait
    la liste normalisée des définitions de champs avant création ou mise à jour de ressource.
    """
    file_bytes = await file.read()
    fields = resource_service.parse_schema_file(file_bytes, file.filename or "schema.xlsx")
    return fields


@router.post("/{resource_id}/schema/reconcile", response_model=SchemaReconciliationReport, summary="Rapprocher et réconcilier le schéma d'une ressource")
async def reconcile_resource_schema(
    resource_id: int,
    payload: SchemaApplyPayload,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Rapproche les champs soumis avec le schéma existant.
    Vérifie la compatibilité absolue avec les données existantes.
    Si dry_run=False, applique les modifications, incrémente la version et archive l'historique.
    """
    raw_fields = [f.model_dump() for f in payload.fields]
    report = await resource_service.reconcile_schema(
        db=db,
        resource_id=resource_id,
        incoming_fields=raw_fields,
        user=current_user,
        apply_changes=not payload.dry_run,
    )
    return report


@router.get("", response_model=ResourceListResponse, summary="Lister les ressources")
async def list_resources(
    status: Optional[str] = Query(None, description="Filtre par statut (draft, active, archived)"),
    category: Optional[str] = Query(None, description="Filtre par catégorie"),
    search: Optional[str] = Query(None, description="Recherche textuelle par nom, code ou description"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    org_id: Optional[int] = Query(None, description="Organisation ciblée (Super Admin uniquement)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Retourne la liste paginée des ressources disponibles pour l'utilisateur
    selon son organisation ou son rôle Super Admin.
    """
    result = await resource_service.list_resources(
        db=db,
        user=current_user,
        status_filter=status,
        category=category,
        search=search,
        skip=skip,
        limit=limit,
        org_id=org_id,
    )
    return result


@router.post("", response_model=ResourceResponse, status_code=status.HTTP_201_CREATED, summary="Créer une ressource")
async def create_resource(
    payload: ResourceCreate,
    org_id: Optional[int] = Query(None, description="Organisation ciblée (Super Admin uniquement)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Crée une nouvelle structure de ressource dynamique avec ses champs et schémas.
    """
    resource = await resource_service.create_resource(
        db=db,
        data=payload,
        user=current_user,
        org_id=org_id,
    )
    return resource


@router.get("/{resource_id}", response_model=ResourceResponse, summary="Obtenir les détails d'une ressource")
async def get_resource(
    resource_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Retourne la configuration complète et le schéma d'une ressource.
    """
    resource = await resource_service.get_resource_by_id(
        db=db,
        resource_id=resource_id,
        user=current_user,
    )
    return resource


@router.put("/{resource_id}", response_model=ResourceResponse, summary="Mettre à jour une ressource")
async def update_resource(
    resource_id: int,
    payload: ResourceUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Met à jour la définition d'une ressource en respectant les verrous d'immuabilité.
    """
    resource = await resource_service.update_resource(
        db=db,
        resource_id=resource_id,
        data=payload,
        user=current_user,
    )
    return resource


@router.delete("/{resource_id}", summary="Supprimer ou archiver une ressource")
async def delete_resource(
    resource_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Supprime la ressource si elle est sans données ou en brouillon, sinon l'archive.
    """
    return await resource_service.delete_resource(
        db=db,
        resource_id=resource_id,
        user=current_user,
    )


# ---------------------------------------------------------------------------
# ITEMS / ENREGISTREMENTS DYNAMIQUES
# ---------------------------------------------------------------------------


@router.get("/{resource_id}/items", response_model=ResourceItemListResponse, summary="Lister les données d'une ressource")
async def list_resource_items(
    resource_id: int,
    search: Optional[str] = Query(None, description="Recherche textuelle dans les données"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=5000),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Retourne la liste paginée des enregistrements de données d'une ressource.
    """
    result = await resource_service.list_items(
        db=db,
        resource_id=resource_id,
        user=current_user,
        search=search,
        skip=skip,
        limit=limit,
    )
    return result


@router.post("/{resource_id}/items", response_model=ResourceItemResponse, status_code=status.HTTP_201_CREATED, summary="Ajouter un enregistrement")
async def create_resource_item(
    resource_id: int,
    payload: ResourceItemCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Ajoute un enregistrement dynamique conforme au schéma défini sur la ressource.
    """
    item = await resource_service.create_item(
        db=db,
        resource_id=resource_id,
        payload=payload,
        user=current_user,
    )
    return item


@router.post("/{resource_id}/items/bulk", summary="Import en masse de données")
async def bulk_import_resource_items(
    resource_id: int,
    payload: ResourceBulkImportPayload,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Pré-charge des enregistrements de données dans la ressource en masse.
    Idéal pour le Super Admin configurant un jeu de données initial.
    """
    result = await resource_service.bulk_import_items(
        db=db,
        resource_id=resource_id,
        payload=payload,
        user=current_user,
    )
    return result


@router.put("/{resource_id}/items/{item_id}", response_model=ResourceItemResponse, summary="Modifier un enregistrement")
async def update_resource_item(
    resource_id: int,
    item_id: int,
    payload: ResourceItemUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Met à jour un enregistrement spécifique.
    """
    item = await resource_service.update_item(
        db=db,
        resource_id=resource_id,
        item_id=item_id,
        payload=payload,
        user=current_user,
    )
    return item


@router.delete("/{resource_id}/items/{item_id}", summary="Supprimer un enregistrement")
async def delete_resource_item(
    resource_id: int,
    item_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Supprime un enregistrement spécifique.
    """
    return await resource_service.delete_item(
        db=db,
        resource_id=resource_id,
        item_id=item_id,
        user=current_user,
    )


@router.get("/{resource_id}/items/{item_id}/related", summary="Obtenir les données reliées d'autres ressources")
async def get_related_items(
    resource_id: int,
    item_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Retourne les enregistrements liés dans d'autres ressources pointant vers cet élément
    (ex: toutes les plantations appartenant à ce planteur).
    """
    return await resource_service.get_related_items(
        db=db,
        resource_id=resource_id,
        item_id=item_id,
        user=current_user,
    )


@router.get("/{resource_id}/relation-options", summary="Lister les options pour sélecteur de relation")
async def get_relation_options(
    resource_id: int,
    display_field: Optional[str] = Query(None, description="Champ de référence pour le libellé"),
    search: Optional[str] = Query(None, description="Recherche dans les données"),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Retourne la liste simplifiée des options (id, label) de cette ressource
    pour alimenter les menus déroulants de liaison dans d'autres formulaires.
    """
    return await resource_service.get_relation_options(
        db=db,
        resource_id=resource_id,
        user=current_user,
        display_field=display_field,
        search=search,
        limit=limit,
    )


@router.post("/{resource_id}/new-version", response_model=ResourceResponse, summary="Créer une nouvelle version (Brouillon) à partir d'une version active")
async def create_new_version(
    resource_id: int,
    payload: ResourceCreateNewVersion,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Crée une nouvelle version au statut draft modifiable en archivant la version active actuelle.
    """
    return await resource_service.create_new_version(
        db=db,
        resource_id=resource_id,
        new_version_str=payload.new_version,
        description=payload.description,
        is_lts=bool(payload.is_lts),
        user=current_user,
    )


@router.put("/{resource_id}/lts", response_model=ResourceResponse, summary="Désigner ou retirer le statut LTS d'une ressource")
async def toggle_resource_lts(
    resource_id: int,
    is_lts: bool = Query(..., description="Activer ou désactiver le statut LTS"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Active ou désactive le statut LTS (Long Term Support) sur la version actuelle de la ressource.
    """
    return await resource_service.set_lts(
        db=db,
        resource_id=resource_id,
        is_lts=is_lts,
        user=current_user,
    )


@router.get("/{resource_id}/data/template", summary="Télécharger le modèle Excel de données pour cette ressource")
async def download_resource_data_template(
    resource_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    Génère et télécharge le fichier Excel modèle spécifiquement structuré avec les colonnes
    et contraintes de la ressource ciblée.
    """
    excel_bytes = await resource_service.get_resource_data_template(db, resource_id, None)
    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=modele_donnees_ressource_{resource_id}.xlsx"},
    )


@router.post("/{resource_id}/data/validate-import", response_model=DataImportReport, summary="Analyser strictement et importer un fichier de données (Excel, CSV, JSON)")
async def validate_and_import_resource_data(
    resource_id: int,
    file: UploadFile = File(...),
    update_existing: bool = Query(True, description="Mettre à jour les enregistrements existants si la clé correspond (Upsert)"),
    dry_run: bool = Query(True, description="Si True, analyse sans modifier la base de données"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Analyse de manière stricte l'intégralité d'un fichier de données (Excel, CSV ou JSON).
    Ne modifie la base que si toutes les lignes sont valides et dry_run=False.
    """
    file_bytes = await file.read()
    report = await resource_service.validate_and_import_data_file(
        db=db,
        resource_id=resource_id,
        file_bytes=file_bytes,
        filename=file.filename or "data.xlsx",
        update_existing=update_existing,
        dry_run=dry_run,
        user=current_user,
    )
    return report


@router.post("/{resource_id}/media", summary="Téléverser un média pour la ressource")
async def upload_resource_media(
    resource_id: int,
    file: UploadFile = File(...),
    media_type: str = Query("files", description="Type de média: image, audio, video, files"),
    item_key: Optional[str] = Query(None, description="Clé unique ou identifiant de l'enregistrement lié"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Téléverse un fichier média (photo, document, audio, vidéo) dans l'arborescence
    structurée et sécurisée de l'organisation :
    organizations/{org_id}/tools/resources/{resource_id}/{media_type}/{uuid}_{filename}
    """
    file_bytes = await file.read()
    return await resource_service.upload_resource_media(
        db=db,
        resource_id=resource_id,
        file_bytes=file_bytes,
        filename=file.filename or "media_upload",
        media_type=media_type,
        user=current_user,
        item_key=item_key,
    )


@router.get("/{resource_id}/export/data", summary="Exporter les données d'une ressource (Excel, CSV, JSON, ZIP)")
async def export_resource_data(
    resource_id: int,
    format: str = Query("xlsx", description="Format d'export : xlsx, csv ou json"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Exporte les données de la ressource au format spécifié (Excel, CSV ou JSON).
    Si des médias sont référencés dans les enregistrements, génère automatiquement une
    archive .ZIP autonome contenant le fichier de données et le sous-dossier 'media/'
    avec les liens réécrits.
    """
    file_bytes, filename, media_type = await resource_service.export_resource_data(
        db=db,
        resource_id=resource_id,
        format=format,
        user=current_user,
    )
    return Response(
        content=file_bytes,
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/{resource_id}/export/schema", summary="Exporter la spécification du schéma (JSON ou Excel)")
async def export_resource_schema(
    resource_id: int,
    format: str = Query("json", description="Format d'export du schéma : json ou xlsx"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Exporte la spécification technique du schéma d'une ressource au format JSON ou Excel.
    """
    file_bytes, filename, media_type = await resource_service.export_resource_schema(
        db=db,
        resource_id=resource_id,
        format=format,
        user=current_user,
    )
    return Response(
        content=file_bytes,
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/storage/stats", summary="Statistiques de stockage par profondeur")
async def get_storage_statistics(
    org_id: Optional[int] = Query(None, description="Identifiant organisationnel (optionnel si super admin)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Retourne les statistiques de stockage (taille totale, nombre de fichiers, ventilation par dossier/profondeur).
    Accessible pour l'organisation de l'utilisateur ou pour toute la plateforme si Super Admin.
    """
    from app.services.storage.storage_service import storage_service
    from app.services.organization.organization_service import organization_service

    target_org_id = None
    if current_user.role == UserRole.SUPER_ADMIN:
        target_org_id = org_id
    else:
        org = await organization_service.get_user_organization(db, current_user)
        target_org_id = org.id if org else None

    return storage_service.get_storage_stats(org_id=target_org_id)


