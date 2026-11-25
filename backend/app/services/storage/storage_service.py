import os
import re
import uuid
import shutil
import mimetypes
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
from app.core.config import settings

class StorageService:
    """
    Service centralisé et souverain de gestion du stockage des médias pour SenWiseTool.
    
    Architecture de stockage :
    - En développement : Système de fichiers local (STORAGE_LOCAL_ROOT).
    - En production : Buckets Amazon S3 ou compatibles (MinIO, Wasabi, OVH, etc.).
    
    Arborescence structurée et cloisonnée (Multi-tenant) :
    storage/
    ├── platform/
    │   ├── branding/
    │   ├── system/
    │   └── templates/
    └── organizations/
        └── {org_id}/
            ├── global/
            │   ├── branding/
            │   └── legal/
            ├── users/
            │   └── {user_id}/
            │       └── avatar/
            └── tools/
                ├── resources/
                │   └── {resource_id}/
                │       ├── images/
                │       ├── audio/
                │       ├── video/
                │       └── files/
                ├── campaigns/
                └── forms/
    """

    def __init__(self):
        self.backend = (settings.STORAGE_BACKEND or "local").lower()
        self.base_url = settings.STORAGE_BASE_URL.rstrip("/")
        
        # Racine locale
        local_root = Path(settings.STORAGE_LOCAL_ROOT)
        if not local_root.is_absolute():
            # Relatif au dossier racine du backend
            backend_root = Path(__file__).resolve().parent.parent.parent.parent
            local_root = backend_root / settings.STORAGE_LOCAL_ROOT
        self.local_root = local_root
        self.local_root.mkdir(parents=True, exist_ok=True)

        # Client S3 si actif
        self._s3_client = None
        if self.backend == "s3":
            self._init_s3_client()

    def _init_s3_client(self):
        try:
            import boto3
            session_kwargs = {
                "aws_access_key_id": settings.S3_ACCESS_KEY_ID or None,
                "aws_secret_access_key": settings.S3_SECRET_ACCESS_KEY or None,
                "region_name": settings.S3_REGION or "eu-west-3",
            }
            client_kwargs = {}
            if settings.S3_ENDPOINT_URL:
                client_kwargs["endpoint_url"] = settings.S3_ENDPOINT_URL

            self._s3_client = boto3.client("s3", **session_kwargs, **client_kwargs)
        except Exception as e:
            # Fallback en mode local si la configuration S3 échoue
            print(f"[StorageService] Avertissement: Impossible d'initialiser le client S3 ({e}). Repli sur le stockage local.")
            self.backend = "local"

    # ==============================================================================
    # MÉTHODES DE CONSTRUCTION DE CHEMINS HIÉRARCHISÉS
    # ==============================================================================
    @staticmethod
    def sanitize_filename(filename: str) -> str:
        """Nettoie le nom de fichier pour éviter les injections de chemin ou caractères interdits."""
        name = os.path.basename(filename)
        name = re.sub(r"[^\w\.\-\(\)]", "_", name)
        return name or "fichier_inconnu"

    def build_resource_media_path(
        self,
        org_id: Any,
        resource_id: int,
        media_type: str,
        filename: str,
        item_key: Optional[str] = None,
        user_id: Optional[int] = None,
    ) -> str:
        """
        Construit le chemin relatif pour un média lié à une ressource et à l'utilisateur :
        organizations/{org_id}/users/{user_id}/resources/{resource_id}/{media_type}/{unique_id}_{filename}
        """
        clean_name = self.sanitize_filename(filename)
        prefix = f"{item_key}_" if item_key else ""
        unique_slug = f"{prefix}{uuid.uuid4().hex[:8]}_{clean_name}"
        clean_media_type = (media_type or "files").lower().replace(" ", "_")
        target_org = org_id or "shared"
        if user_id:
            return f"organizations/{target_org}/users/{user_id}/resources/{resource_id}/{clean_media_type}/{unique_slug}"
        return f"organizations/{target_org}/tools/resources/{resource_id}/{clean_media_type}/{unique_slug}"

    def build_org_global_path(self, org_id: int, category: str, filename: str) -> str:
        """
        Construit le chemin relatif pour les assets globaux d'une organisation :
        organizations/{org_id}/global/{category}/{unique_id}_{filename}
        """
        clean_name = self.sanitize_filename(filename)
        clean_cat = (category or "general").lower().replace(" ", "_")
        unique_slug = f"{uuid.uuid4().hex[:8]}_{clean_name}"
        return f"organizations/{org_id}/global/{clean_cat}/{unique_slug}"

    def build_user_media_path(self, org_id: int, user_id: int, category: str, filename: str) -> str:
        """
        Construit le chemin relatif pour un fichier utilisateur spécifique :
        organizations/{org_id}/users/{user_id}/{category}/{unique_id}_{filename}
        """
        clean_name = self.sanitize_filename(filename)
        clean_cat = (category or "uploads").lower().replace(" ", "_")
        unique_slug = f"{uuid.uuid4().hex[:8]}_{clean_name}"
        return f"organizations/{org_id}/users/{user_id}/{clean_cat}/{unique_slug}"

    def build_platform_path(self, section: str, filename: str) -> str:
        """
        Construit le chemin relatif pour les fichiers plateforme / Super Admin :
        platform/{section}/{unique_id}_{filename}
        """
        clean_name = self.sanitize_filename(filename)
        clean_sec = (section or "system").lower().replace(" ", "_")
        unique_slug = f"{uuid.uuid4().hex[:8]}_{clean_name}"
        return f"platform/{clean_sec}/{unique_slug}"

    # ==============================================================================
    # SAUVEGARDE ET ÉCRITURE DE FICHIERS
    # ==============================================================================
    def save_file(
        self,
        file_bytes: bytes,
        relative_path: str,
        content_type: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Enregistre un fichier de manière transparente selon le backend configuré (local ou S3).
        Renvoie les métadonnées et l'URL publique de consultation.
        """
        relative_path = relative_path.lstrip("/")
        if not content_type:
            content_type, _ = mimetypes.guess_type(relative_path)
            content_type = content_type or "application/octet-stream"

        file_size = len(file_bytes)

        if self.backend == "s3" and self._s3_client:
            self._s3_client.put_object(
                Bucket=settings.S3_BUCKET_NAME,
                Key=relative_path,
                Body=file_bytes,
                ContentType=content_type,
            )
            # URL S3
            if settings.S3_PUBLIC_URL:
                url = f"{settings.S3_PUBLIC_URL.rstrip('/')}/{relative_path}"
            elif settings.S3_ENDPOINT_URL:
                url = f"{settings.S3_ENDPOINT_URL.rstrip('/')}/{settings.S3_BUCKET_NAME}/{relative_path}"
            else:
                url = f"https://{settings.S3_BUCKET_NAME}.s3.{settings.S3_REGION}.amazonaws.com/{relative_path}"
        else:
            # Mode local
            dest_file = self.local_root / relative_path
            dest_file.parent.mkdir(parents=True, exist_ok=True)
            with open(dest_file, "wb") as f:
                f.write(file_bytes)
            url = f"{self.base_url}/{relative_path}"

        return {
            "url": url,
            "relative_path": relative_path,
            "filename": os.path.basename(relative_path),
            "size": file_size,
            "content_type": content_type,
            "storage_backend": self.backend
        }

    # ==============================================================================
    # LECTURE ET EXTRACTION DES FICHIERS
    # ==============================================================================
    def resolve_relative_path(self, url_or_path: str) -> str:
        """
        Extrait le chemin relatif interne à partir d'une URL de stockage ou d'un chemin relatif.
        Ex: '/storage/organizations/1/tools/...' -> 'organizations/1/tools/...'
        """
        if not url_or_path:
            return ""
        path = str(url_or_path).strip()

        # Supprimer le préfixe base_url s'il commence par '/storage'
        if path.startswith(self.base_url):
            path = path[len(self.base_url):]
        elif path.startswith("/storage"):
            path = path[len("/storage"):]

        # Supprimer le domaine public si S3
        if settings.S3_PUBLIC_URL and path.startswith(settings.S3_PUBLIC_URL):
            path = path[len(settings.S3_PUBLIC_URL):]

        return path.lstrip("/")

    def get_file_bytes(self, url_or_path: str) -> Optional[bytes]:
        """
        Récupère le contenu binaire d'un fichier stocké, que ce soit en local ou sur S3.
        Indispensable pour l'export groupé dans une archive ZIP.
        """
        rel_path = self.resolve_relative_path(url_or_path)
        if not rel_path:
            return None

        if self.backend == "s3" and self._s3_client:
            try:
                response = self._s3_client.get_object(
                    Bucket=settings.S3_BUCKET_NAME,
                    Key=rel_path
                )
                return response["Body"].read()
            except Exception as e:
                print(f"[StorageService] Erreur lecture S3 pour '{rel_path}': {e}")
                return None
        else:
            # Local
            local_file = self.local_root / rel_path
            if local_file.is_file():
                try:
                    with open(local_file, "rb") as f:
                        return f.read()
                except Exception as e:
                    print(f"[StorageService] Erreur lecture fichier local '{local_file}': {e}")
                    return None
            return None

    def file_exists(self, url_or_path: str) -> bool:
        """Vérifie l'existence d'un fichier."""
        rel_path = self.resolve_relative_path(url_or_path)
        if not rel_path:
            return False

        if self.backend == "s3" and self._s3_client:
            try:
                self._s3_client.head_object(Bucket=settings.S3_BUCKET_NAME, Key=rel_path)
                return True
            except Exception:
                return False
        else:
            return (self.local_root / rel_path).is_file()

    def delete_file(self, url_or_path: str) -> bool:
        """Supprime un fichier stocké."""
        rel_path = self.resolve_relative_path(url_or_path)
        if not rel_path:
            return False

        if self.backend == "s3" and self._s3_client:
            try:
                self._s3_client.delete_object(Bucket=settings.S3_BUCKET_NAME, Key=rel_path)
                return True
            except Exception:
                return False
        else:
            local_file = self.local_root / rel_path
            if local_file.is_file():
                try:
                    local_file.unlink()
                    return True
                except Exception:
                    return False
            return False

    # ==============================================================================
    # STATISTIQUES DE STOCKAGE PAR PROFONDEUR
    # ==============================================================================
    def get_storage_stats(
        self,
        org_id: Optional[int] = None,
        tool_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Calcule les statistiques précises d'utilisation du stockage :
        - Pour l'ensemble de la plateforme si org_id est None (super admin)
        - Pour une organisation spécifique si org_id est fourni
        - Ventilé par dossiers (global, users, tools, resources, etc.)
        """
        prefix = ""
        if org_id is not None:
            prefix = f"organizations/{org_id}"
            if tool_name:
                prefix = f"{prefix}/tools/{tool_name}"

        total_bytes = 0
        total_files = 0
        breakdown: Dict[str, Dict[str, Any]] = {}

        if self.backend == "local":
            target_dir = self.local_root / prefix if prefix else self.local_root
            if target_dir.exists():
                for root, dirs, files in os.walk(target_dir):
                    rel_to_target = os.path.relpath(root, self.local_root)
                    parts = Path(rel_to_target).parts
                    category_key = parts[0] if parts and parts[0] != "." else "root"
                    if len(parts) >= 2 and parts[0] == "organizations":
                        category_key = "/".join(parts[:3]) # ex: organizations/1/tools

                    if category_key not in breakdown:
                        breakdown[category_key] = {"bytes": 0, "files": 0}

                    for file in files:
                        file_path = Path(root) / file
                        if file_path.is_file():
                            sz = file_path.stat().st_size
                            total_bytes += sz
                            total_files += 1
                            breakdown[category_key]["bytes"] += sz
                            breakdown[category_key]["files"] += 1
        elif self.backend == "s3" and self._s3_client:
            try:
                paginator = self._s3_client.get_paginator("list_objects_v2")
                for page in paginator.paginate(Bucket=settings.S3_BUCKET_NAME, Prefix=prefix):
                    for obj in page.get("Contents", []):
                        sz = obj.get("Size", 0)
                        key = obj.get("Key", "")
                        total_bytes += sz
                        total_files += 1
                        parts = Path(key).parts
                        cat = parts[0] if parts else "root"
                        if len(parts) >= 3 and parts[0] == "organizations":
                            cat = f"{parts[0]}/{parts[1]}/{parts[2]}"
                        if cat not in breakdown:
                            breakdown[cat] = {"bytes": 0, "files": 0}
                        breakdown[cat]["bytes"] += sz
                        breakdown[cat]["files"] += 1
            except Exception as e:
                print(f"[StorageService] Erreur calcul stats S3: {e}")

        return {
            "total_bytes": total_bytes,
            "total_megabytes": round(total_bytes / (1024 * 1024), 2),
            "total_gigabytes": round(total_bytes / (1024 * 1024 * 1024), 4),
            "total_files": total_files,
            "backend": self.backend,
            "prefix": prefix,
            "breakdown": breakdown
        }

storage_service = StorageService()
