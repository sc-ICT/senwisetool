import copy
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status, Body
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_, delete
from sqlalchemy.orm import selectinload

from app.db.database import get_db
from app.api.deps import get_current_user, get_optional_current_user
from app.models.user.user import User, UserRole
from app.models.organization.organization import Organization
from app.models.organization.member import OrganizationMember
from app.models.plugin.plugin import (
    Plugin,
    PluginVersion,
    PluginVersionTool,
    PluginVersionStatus,
    PluginStatus,
    OrgPluginStatus,
)
from app.models.organization.organization_plugin import OrganizationPlugin, OrganizationPluginVersion
from app.models.organization.organization_project import OrganizationProject, OrganizationProjectVersion
from app.models.organization.organization_resource import (
    OrganizationResource,
    OrganizationResourceVersion,
    OrganizationResourceItem,
)
from sqlalchemy.orm.attributes import flag_modified
from app.models.organization.organization_form import (
    OrganizationForm,
    OrganizationFormVersion,
    OrganizationFormSection,
    OrganizationFormQuestion,
)
from app.models.organization.organization_campaign import OrganizationCampaign, OrganizationCampaignVersion
from app.models.organization.organization_feature import OrganizationFeature
from app.schemas.plugin.plugin import (
    MarketplaceCatalogResponse,
    MarketplacePluginItem,
    PluginInstallRequest,
    PluginUpgradeRequest,
    PluginUninstallRequest,
    PluginVersionSwitchRequest,
    PluginInstallModuleRequest,
    PluginResponse,
    PluginVersionResponse,
    OrganizationPluginResponse,
)
from app.services.plugin.plugin_service import plugin_service
from app.services.organization.organization_service import organization_service

router = APIRouter()


async def _resolve_user_organization(
    db: AsyncSession,
    user: User,
    requested_org_id: Optional[int] = None
) -> Optional[Organization]:
    """
    Identifie l'organisation active du demandeur.
    Si Super Admin, peut cibler une organisation spécifique si requested_org_id est spécifié.
    Fallback pour Super Admin sur la première organisation active pour permettre l'administration et les tests.
    """
    is_super = bool(
        user.role == UserRole.SUPER_ADMIN or
        getattr(user, "is_superuser", False) or
        str(getattr(user, "role", "")).upper() == "SUPER_ADMIN"
    )

    if requested_org_id:
        res = await db.execute(select(Organization).where(Organization.id == requested_org_id))
        org = res.scalars().first()
        if org:
            return org

    user_org = await organization_service.get_user_organization(db, user)
    if user_org:
        return user_org

    # Pour un Super Admin sans organisation rattachée directe, fallback sur la première organisation active
    if is_super:
        res = await db.execute(
            select(Organization)
            .where(Organization.status == "active")
            .order_by(Organization.id.asc())
            .limit(1)
        )
        return res.scalars().first()

    return None


async def _verify_org_admin_permission(
    db: AsyncSession,
    org: Organization,
    user: User
) -> None:
    """
    Vérifie formellement que l'utilisateur est soit Super Admin, soit Propriétaire ou Administrateur
    d'organisation qualifié pour installer, mettre à niveau ou désinstaller un plugin pour le compte du tenant.
    """
    if user.role == UserRole.SUPER_ADMIN:
        return

    # Propriétaire fondateur ou contact légal
    if org.owner_id == user.id or org.contact_email.lower() == user.email.lower():
        return

    # Collaborateur membre avec privilèges administrateur
    mem_res = await db.execute(
        select(OrganizationMember).where(
            OrganizationMember.organization_id == org.id,
            or_(
                OrganizationMember.user_id == user.id,
                func.lower(OrganizationMember.email) == user.email.lower()
            )
        )
    )
    member = mem_res.scalars().first()
    if member:
        if member.is_owner:
            return
        if member.role_code and any(k in member.role_code.upper() for k in ["ADM", "ADMIN", "OWNER", "DIRECT"]):
            return
        if member.roles_list and any("admin" in str(r).lower() for r in member.roles_list):
            return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Action réservée aux administrateurs de l'organisation. Vous n'avez pas les droits nécessaires pour modifier les plugins de cette organisation."
    )


from app.models.project.project import Project
from app.models.form.form import Form
from app.models.campaign.campaign import Campaign
from app.models.resource.resource import Resource

FEATURE_DEFAULT_METADATA = {
    "campaigns": {
        "name": "Gestion des Campagnes",
        "description": "Planification temporelle, objectifs de collecte, suivi d'avancement et encadrement des saisons opérationnelles."
    },
    "projects": {
        "name": "Gestion des Projets & Audits",
        "description": "Modèles de projets, programmes d'inspection et audits terrain opérationnels."
    },
    "forms": {
        "name": "Moteur de Formulaires Dynamiques",
        "description": "Moteur de formulaires dynamiques, questionnaires d'inspection et collecte de données."
    },
    "resources": {
        "name": "Gestion des Ressources Dynamiques",
        "description": "Tables de référence, catalogues métiers, nomenclatures et grilles d'audit centralisées."
    },
    "iot_telemetry": {
        "name": "Télémétrie & Capteurs IoT",
        "description": "Supervision en direct, relevés capteurs, alertes de seuils et cartographie d'objets connectés."
    }
}


async def _enrich_versions_with_referenced_entities(db: AsyncSession, versions: List[Any]) -> None:
    """
    Parcourt les outils et modules d'un ensemble de versions de plugins.
    Récupère en base de données les vrais noms, descriptions riches, types, statuts et versions
    des entités liées (projects, forms, campaigns, resources) et injecte ces métadonnées complètes
    dans package_data['tools'] pour la présentation exhaustive sur le Store.
    """
    if not versions:
        return

    # 1. Identifier tous les IDs référencés par table depuis package_data ET PluginVersionTool (table BD)
    proj_ids = set()
    form_ids = set()
    camp_ids = set()
    res_ids = set()

    # Charger de façon sécurisée tous les outils relationnels sans déclencher de lazy loading SQL
    v_ids = [v.id for v in versions if getattr(v, "id", None)]
    db_tools_by_ver: Dict[int, list] = {}
    if v_ids:
        vt_rows = (await db.execute(
            select(PluginVersionTool).where(PluginVersionTool.plugin_version_id.in_(v_ids))
        )).scalars().all()
        for vt in vt_rows:
            db_tools_by_ver.setdefault(vt.plugin_version_id, []).append(vt)

    for v in versions:
        if not v:
            continue
        pkg = getattr(v, "package_data", {}) or {}
        tools = list(pkg.get("tools", []) or [])

        # Intégrer les PluginVersionTool stockés en table relationnelle
        db_tools = db_tools_by_ver.get(v.id, [])
        for dt in db_tools:
            if not dt:
                continue
            t_code = getattr(dt, "tool_code", "")
            r_id = getattr(dt, "selected_resource_id", None)
            if r_id:
                try:
                    r_id_int = int(r_id)
                    if t_code == "projects":
                        proj_ids.add(r_id_int)
                    elif t_code == "forms":
                        form_ids.add(r_id_int)
                    elif t_code == "campaigns":
                        camp_ids.add(r_id_int)
                    elif t_code == "resources":
                        res_ids.add(r_id_int)
                except (ValueError, TypeError):
                    pass

        if isinstance(tools, list):
            for t in tools:
                if not isinstance(t, dict):
                    continue
                decl = t.get("declaration") or {}
                ref_table = decl.get("ref_table") or t.get("ref_table") or t.get("tool_code")
                ref_id = decl.get("ref_id") or t.get("ref_id") or t.get("selected_resource_id")
                if not ref_id or not ref_table:
                    continue
                try:
                    ref_id_int = int(ref_id)
                except (ValueError, TypeError):
                    continue

                if ref_table == "projects":
                    proj_ids.add(ref_id_int)
                elif ref_table == "forms":
                    form_ids.add(ref_id_int)
                elif ref_table == "campaigns":
                    camp_ids.add(ref_id_int)
                elif ref_table == "resources":
                    res_ids.add(ref_id_int)

    # 2. Requêter les entités en base avec leurs relations (versions, formulaires)
    projs_map: Dict[int, Any] = {}
    forms_map: Dict[int, Any] = {}
    camps_map: Dict[int, Any] = {}
    res_map: Dict[int, Any] = {}

    if proj_ids:
        rows = (await db.execute(
            select(Project)
            .options(
                selectinload(Project.versions),
            )
            .where(Project.id.in_(list(proj_ids)))
        )).scalars().all()
        for p in rows:
            projs_map[p.id] = p
            # Collecter les formulaires rattachés aux versions de ce projet pour enrichir form_ids
            for pv in (p.versions or []):
                p_settings = getattr(pv, "settings", {}) or {}
                for fid in p_settings.get("attached_form_ids", []):
                    if str(fid).isdigit():
                        form_ids.add(int(fid))
                for af in p_settings.get("attached_form_versions", []):
                    if isinstance(af, dict) and af.get("form_id") and str(af["form_id"]).isdigit():
                        form_ids.add(int(af["form_id"]))

    if form_ids:
        rows = (await db.execute(select(Form).where(Form.id.in_(list(form_ids))))).scalars().all()
        for f in rows:
            forms_map[f.id] = f

    if camp_ids:
        rows = (await db.execute(select(Campaign).where(Campaign.id.in_(list(camp_ids))))).scalars().all()
        for c in rows:
            camps_map[c.id] = c

    if res_ids:
        rows = (await db.execute(select(Resource).where(Resource.id.in_(list(res_ids))))).scalars().all()
        for r in rows:
            res_map[r.id] = r

    # 3. Enrichir package_data pour chaque version
    for v in versions:
        if not v:
            continue
        raw_pkg = getattr(v, "package_data", {}) or {}
        pkg = dict(raw_pkg)
        tools = list(pkg.get("tools", []) or [])

        # Fusionner les outils issus de la base relationnelle (PluginVersionTool) s'ils ne sont pas déjà dans tools
        existing_keys = {t.get("item_key") for t in tools if isinstance(t, dict) and t.get("item_key")}
        db_tools = db_tools_by_ver.get(v.id, [])
        for dt in db_tools:
            item_k = getattr(dt, "item_key", "")
            if item_k and item_k not in existing_keys:
                tools.append({
                    "id": getattr(dt, "id", None),
                    "item_key": item_k,
                    "tool_code": getattr(dt, "tool_code", ""),
                    "code": getattr(dt, "tool_code", ""),
                    "type": getattr(dt, "type", "module"),
                    "tool_type": getattr(dt, "type", "module"),
                    "config_target": getattr(dt, "config_target", "tool_resource"),
                    "name": getattr(dt, "name", ""),
                    "description": getattr(dt, "description", ""),
                    "selected_resource_id": getattr(dt, "selected_resource_id", None),
                    "ref_id": getattr(dt, "selected_resource_id", None),
                    "ref_table": getattr(dt, "tool_code", ""),
                    "selected_resource_code": getattr(dt, "selected_resource_code", None),
                    "selected_resource_name": getattr(dt, "selected_resource_name", None),
                    "selected_resource_status": getattr(dt, "selected_resource_status", None),
                    "distribution_mode": getattr(dt, "distribution_mode", "sovereign"),
                    "permissions_mode": getattr(dt, "permissions_mode", "shared"),
                    "parameters": getattr(dt, "parameters", {}) or {},
                    "settings": getattr(dt, "parameters", {}) or {},
                    "declaration": {
                        "name": getattr(dt, "name", ""),
                        "title": getattr(dt, "name", ""),
                        "description": getattr(dt, "description", ""),
                        "ref_id": getattr(dt, "selected_resource_id", None),
                        "ref_table": getattr(dt, "tool_code", ""),
                        "feature_code": getattr(dt, "tool_code", ""),
                    }
                })
                existing_keys.add(item_k)

        enriched_tools = []
        for t in tools:
            if not isinstance(t, dict):
                enriched_tools.append(t)
                continue

            tool_copy = dict(t)
            decl = dict(tool_copy.get("declaration") or {})
            ref_table = decl.get("ref_table") or tool_copy.get("ref_table") or tool_copy.get("tool_code")
            ref_id = decl.get("ref_id") or tool_copy.get("ref_id") or tool_copy.get("selected_resource_id")
            tool_code = tool_copy.get("tool_code") or tool_copy.get("code") or decl.get("feature_code")
            params = tool_copy.get("parameters") or tool_copy.get("settings") or {}

            # Enrichissement pour un module spécifique
            if ref_table and ref_id:
                try:
                    ref_id_int = int(ref_id)
                except (ValueError, TypeError):
                    ref_id_int = None

                entity = None
                if ref_table == "projects":
                    entity = projs_map.get(ref_id_int)
                elif ref_table == "forms":
                    entity = forms_map.get(ref_id_int)
                elif ref_table == "campaigns":
                    entity = camps_map.get(ref_id_int)
                elif ref_table == "resources":
                    entity = res_map.get(ref_id_int)

                if entity:
                    # Ne pas écraser une description personnalisée définie lors du build si existante
                    ent_desc = getattr(entity, "description", "") or ""
                    custom_desc = tool_copy.get("description") or decl.get("description")
                    final_desc = custom_desc if custom_desc and str(custom_desc).strip() else ent_desc

                    resolved_name = getattr(entity, "name", decl.get("name") or decl.get("title") or decl.get("code"))
                    decl["name"] = resolved_name
                    decl["title"] = resolved_name
                    decl["description"] = final_desc
                    decl["category"] = getattr(entity, "category", None) or getattr(entity, "project_type", None) or getattr(entity, "form_type", None) or getattr(entity, "campaign_type", None)
                    decl["code"] = getattr(entity, "code", decl.get("code"))

                    # Spécificité Module Projet : résolution de la version ciblée et des formulaires liés
                    if ref_table == "projects":
                        target_v_id = params.get("selected_project_version_id")
                        target_v_num = params.get("selected_project_version_number")
                        proj_versions = getattr(entity, "versions", []) or []
                        target_version = None
                        if target_v_id:
                            target_version = next((ver for ver in proj_versions if ver.id == target_v_id), None)
                        if not target_version and target_v_num:
                            target_version = next((ver for ver in proj_versions if ver.version_number == str(target_v_num).lstrip("v")), None)
                        if not target_version and proj_versions:
                            pub_v = [ver for ver in proj_versions if getattr(ver, "status", "") == "published"]
                            target_version = pub_v[-1] if pub_v else proj_versions[-1]

                        resolved_v_num = target_version.version_number if target_version else getattr(entity, "version", "1.0.0")
                        resolved_v_status = target_version.status if target_version else "published"
                        resolved_v_desc = target_version.description if (target_version and target_version.description) else final_desc

                        decl["version"] = resolved_v_num
                        decl["version_number"] = resolved_v_num
                        decl["version_status"] = resolved_v_status
                        tool_copy["version"] = resolved_v_num
                        tool_copy["project_version_number"] = resolved_v_num
                        tool_copy["project_version_status"] = resolved_v_status

                        # Formulaires rattachés spécifiquement à cette version de projet (via settings)
                        attached_fids = set()
                        if target_version:
                            tv_settings = getattr(target_version, "settings", {}) or {}
                            for fid in tv_settings.get("attached_form_ids", []):
                                if str(fid).isdigit():
                                    attached_fids.add(int(fid))
                            for af in tv_settings.get("attached_form_versions", []):
                                if isinstance(af, dict) and af.get("form_id") and str(af["form_id"]).isdigit():
                                    attached_fids.add(int(af["form_id"]))

                        linked_forms = [forms_map[fid] for fid in attached_fids if fid in forms_map]

                        forms_summary = [
                            {
                                "id": f.id,
                                "name": getattr(f, "name", "Formulaire"),
                                "code": getattr(f, "code", ""),
                                "version": getattr(f, "version", "1.0.0"),
                                "status": getattr(f, "status", "published"),
                                "description": getattr(f, "description", ""),
                            }
                            for f in linked_forms
                        ]
                        decl["forms"] = forms_summary
                        tool_copy["forms"] = forms_summary

                        tool_copy["selected_resource_data"] = {
                            "id": entity.id,
                            "name": resolved_name,
                            "code": entity.code,
                            "version": resolved_v_num,
                            "version_status": resolved_v_status,
                            "project_type": getattr(entity, "project_type", "custom"),
                            "category": getattr(entity, "category", "general"),
                            "status": getattr(entity, "status", "published"),
                            "description": resolved_v_desc,
                            "forms": forms_summary,
                            "distribution_mode": tool_copy.get("distribution_mode", "sovereign"),
                            "template_governance": params.get("template_governance", {}),
                        }

                    elif ref_table == "campaigns":
                        decl["objectives"] = getattr(entity, "objectives", None)
                        decl["targets"] = getattr(entity, "targets", None)
                        decl["status"] = getattr(entity, "status", None)
                        decl["version"] = getattr(entity, "version", None)
                        tool_copy["selected_resource_data"] = {
                            "id": entity.id,
                            "name": resolved_name,
                            "code": entity.code,
                            "status": getattr(entity, "status", "draft"),
                            "description": final_desc,
                            "visibility": getattr(entity, "visibility", "public"),
                        }
                    else:
                        decl["version"] = getattr(entity, "version", None)

                    tool_copy["name"] = resolved_name
                    tool_copy["description"] = final_desc
                    tool_copy["selected_resource_name"] = resolved_name
                    if getattr(entity, "version", None):
                        tool_copy["version"] = getattr(entity, "version", None)

            # Enrichissement pour une fonctionnalité globale
            else:
                default_meta = FEATURE_DEFAULT_METADATA.get(tool_code, {})
                f_name = decl.get("name") or tool_copy.get("name") or default_meta.get("name") or tool_code
                f_desc = tool_copy.get("description") or decl.get("description") or default_meta.get("description") or ""
                decl["name"] = f_name
                decl["title"] = f_name
                decl["description"] = f_desc
                tool_copy["name"] = f_name
                tool_copy["description"] = f_desc

            tool_copy["declaration"] = decl
            enriched_tools.append(tool_copy)

        pkg["tools"] = enriched_tools
        v.package_data = pkg


def _format_tool_item_dict(t) -> dict:
    if isinstance(t, dict):
        t_dict = dict(t)
        tool_code = t_dict.get("tool_code") or t_dict.get("code") or t_dict.get("tool") or ""
        t_dict["tool_code"] = tool_code
        t_dict["code"] = tool_code
        if "item_key" not in t_dict or not t_dict["item_key"]:
            t_dict["item_key"] = str(t_dict.get("id") or f"tool_{tool_code}")
        if "name" not in t_dict or not t_dict["name"]:
            t_dict["name"] = t_dict.get("title") or t_dict.get("item_key") or tool_code
        if "type" not in t_dict:
            t_dict["type"] = "feature" if t_dict.get("config_target") == "tool_global" or t_dict.get("is_global") else "module"
        if "config_target" not in t_dict:
            t_dict["config_target"] = "tool_global" if t_dict.get("type") == "feature" else "tool_resource"
        if "distribution_mode" not in t_dict:
            t_dict["distribution_mode"] = "tenant"
        if "permissions_mode" not in t_dict:
            t_dict["permissions_mode"] = "restricted"
        params = t_dict.get("parameters") if isinstance(t_dict.get("parameters"), dict) else {}
        t_dict["parameters"] = params
        t_dict["settings"] = t_dict.get("settings") if isinstance(t_dict.get("settings"), dict) else params
        return t_dict

    params = getattr(t, "parameters", {}) or {}
    t_code = getattr(t, "tool_code", "") or getattr(t, "code", "")
    return {
        "id": getattr(t, "id", None),
        "plugin_version_id": getattr(t, "plugin_version_id", None),
        "item_key": getattr(t, "item_key", ""),
        "tool_code": t_code,
        "code": t_code,
        "type": getattr(t, "type", "module"),
        "config_target": getattr(t, "config_target", "tool_resource"),
        "name": getattr(t, "name", ""),
        "description": getattr(t, "description", None),
        "selected_resource_id": getattr(t, "selected_resource_id", None),
        "selected_resource_code": getattr(t, "selected_resource_code", None),
        "selected_resource_name": getattr(t, "selected_resource_name", None),
        "selected_resource_status": getattr(t, "selected_resource_status", None),
        "distribution_mode": getattr(t, "distribution_mode", "tenant"),
        "permissions_mode": getattr(t, "permissions_mode", "restricted"),
        "parameters": params,
        "settings": params,
        "created_at": getattr(t, "created_at", None),
        "updated_at": getattr(t, "updated_at", None),
    }


def _format_version_dict(v, plugin_id: Optional[int] = None) -> Optional[dict]:
    if not v:
        return None
    if isinstance(v, dict):
        res = dict(v)
        if (not res.get("plugin_id")) and plugin_id:
            res["plugin_id"] = plugin_id
        return res

    v_dict = getattr(v, "__dict__", {})
    raw_tools = []
    if isinstance(v_dict, dict) and "tools" in v_dict:
        val = v_dict.get("tools")
        if isinstance(val, (list, tuple)):
            raw_tools = list(val)

    pkg = dict(getattr(v, "package_data", {}) or {})
    pkg_tools = pkg.get("tools")
    if isinstance(pkg_tools, list) and len(pkg_tools) > 0:
        raw_tools = pkg_tools

    formatted_tools = [_format_tool_item_dict(t) for t in raw_tools]
    pkg["tools"] = formatted_tools

    v_num = getattr(v, "version_number", "")
    policy = getattr(v, "usage_policy", "") or ""

    resolved_pid = (
        plugin_id or
        getattr(v, "plugin_id", None) or
        getattr(getattr(v, "organization_plugin", None), "source_plugin_id", None) or
        getattr(getattr(v, "source_plugin_version", None), "plugin_id", None) or
        getattr(v, "source_plugin_id", None)
    )

    return {
        "id": getattr(v, "id", None),
        "plugin_id": resolved_pid,
        "version_number": v_num,
        "versionNumber": v_num,
        "changelog": getattr(v, "changelog", None),
        "package_data": pkg,
        "packageData": pkg,
        "usage_policy": policy,
        "usagePolicy": policy,
        "status": getattr(v, "status", "draft"),
        "tools": formatted_tools,
        "is_blocked": getattr(v, "is_blocked", False),
        "created_at": getattr(v, "created_at", None),
        "updated_at": getattr(v, "updated_at", None),
    }


def _format_plugin_dict(plug, org_map: Optional[Dict[int, str]] = None) -> dict:
    raw_versions = plug.__dict__.get("versions") or []
    pub_versions = [v for v in raw_versions if v.status == PluginVersionStatus.PUBLISHED.value]
    pub_versions.sort(key=lambda x: x.created_at, reverse=True)
    latest_version = pub_versions[0] if pub_versions else None

    raw_installs = plug.__dict__.get("installations") or []
    inst_count = len(raw_installs)

    allowed_ids = getattr(plug, "allowed_organization_ids", []) or []
    allowed_orgs_list = []
    if org_map and allowed_ids:
        for oid in allowed_ids:
            name = org_map.get(oid, f"Organisation #{oid}")
            allowed_orgs_list.append({"id": oid, "name": name})
    elif allowed_ids:
        for oid in allowed_ids:
            allowed_orgs_list.append({"id": oid, "name": f"Organisation #{oid}"})

    return {
        "id": plug.id,
        "code": plug.code,
        "name": plug.name,
        "category": plug.category,
        "description": plug.description,
        "short_description": plug.short_description,
        "icon_url": plug.icon_url,
        "banner_url": plug.banner_url,
        "author": plug.author,
        "status": plug.status,
        "tags": plug.tags or [],
        "is_public": getattr(plug, "is_public", True),
        "allowed_organization_ids": allowed_ids,
        "allowed_organizations": allowed_orgs_list,
        "created_at": plug.created_at,
        "updated_at": plug.updated_at,
        "versions": [_format_version_dict(v) for v in pub_versions],
        "latest_version": _format_version_dict(latest_version),
        "versions_count": len(pub_versions),
        "installations_count": inst_count,
    }


def _format_installation_dict(inst, plug=None, installed_version=None, latest_version=None, has_update=False, org_map: Optional[Dict[int, str]] = None) -> dict:
    if isinstance(inst, dict):
        res = dict(inst)
        p = plug or res.get("plugin")
        if p and not isinstance(p, dict):
            res["plugin"] = _format_plugin_dict(p, org_map=org_map)
        elif p and "plugin" not in res:
            res["plugin"] = p

        pid = getattr(p, "id", None) if p else (res.get("plugin_id") or res.get("source_plugin_id") or 1)
        iv = installed_version or res.get("installed_version")
        if iv and not isinstance(iv, dict):
            res["installed_version"] = _format_version_dict(iv, plugin_id=pid)
        elif iv:
            if isinstance(iv, dict):
                if not iv.get("plugin_id") and pid:
                    iv["plugin_id"] = pid
            res["installed_version"] = iv

        lv = latest_version or res.get("latest_version") or iv
        if lv and not isinstance(lv, dict):
            res["latest_version"] = _format_version_dict(lv, plugin_id=pid)
        elif lv:
            if isinstance(lv, dict):
                if not lv.get("plugin_id") and pid:
                    lv["plugin_id"] = pid
            res["latest_version"] = lv

        if "has_update_available" not in res:
            res["has_update_available"] = has_update

        if "downloaded_versions" in res and "downloadedVersions" not in res:
            res["downloadedVersions"] = res["downloaded_versions"]

        return res

    p = plug or getattr(inst, "plugin", None)
    pid = getattr(p, "id", None) if p else (getattr(inst, "source_plugin_id", None) or getattr(inst, "plugin_id", None) or 1)
    iv = installed_version or getattr(inst, "installed_version", None)
    lv = latest_version or iv

    raw_dl = inst.__dict__.get("downloaded_versions") if hasattr(inst, "__dict__") else (getattr(inst, "downloaded_versions", []) or [])
    raw_dl = raw_dl or []
    downloaded_versions = [
        {
            "id": dv.id if hasattr(dv, "id") else dv.get("id"),
            "organization_plugin_id": dv.organization_plugin_id if hasattr(dv, "organization_plugin_id") else dv.get("organization_plugin_id"),
            "plugin_id": (dv.plugin_id if hasattr(dv, "plugin_id") else dv.get("plugin_id")) or pid,
            "plugin_version_id": dv.plugin_version_id if hasattr(dv, "plugin_version_id") else dv.get("plugin_version_id"),
            "version_number": dv.version_number if hasattr(dv, "version_number") else dv.get("version_number"),
            "status": dv.status if hasattr(dv, "status") else dv.get("status"),
            "changelog": dv.changelog if hasattr(dv, "changelog") else dv.get("changelog"),
            "parameters": dv.parameters if hasattr(dv, "parameters") else dv.get("parameters", {}),
            "created_at": dv.created_at if hasattr(dv, "created_at") else dv.get("created_at"),
        }
        for dv in raw_dl
    ]

    formatted_iv = None
    if iv:
        if not isinstance(iv, dict):
            formatted_iv = _format_version_dict(iv, plugin_id=pid)
        else:
            formatted_iv = dict(iv)
            if not formatted_iv.get("plugin_id"):
                formatted_iv["plugin_id"] = pid

    formatted_lv = None
    if lv:
        if not isinstance(lv, dict):
            formatted_lv = _format_version_dict(lv, plugin_id=pid)
        else:
            formatted_lv = dict(lv)
            if not formatted_lv.get("plugin_id"):
                formatted_lv["plugin_id"] = pid

    return {
        "id": inst.id,
        "organization_id": inst.organization_id,
        "plugin_id": pid,
        "installed_version_id": getattr(inst, "installed_version_id", None) or (iv.id if hasattr(iv, "id") else (iv.get("id") if isinstance(iv, dict) else None)),
        "code": getattr(inst, "code", None) or (getattr(p, "code", "") if p else ""),
        "name": getattr(inst, "name", None) or (getattr(p, "name", "") if p else ""),
        "category": getattr(inst, "category", None) or (getattr(p, "category", "") if p else ""),
        "description": getattr(inst, "description", None) or (getattr(p, "description", None) if p else None),
        "short_description": getattr(inst, "short_description", None) or (getattr(p, "short_description", None) if p else None),
        "icon_url": getattr(inst, "icon_url", None) or (getattr(p, "icon_url", None) if p else None),
        "banner_url": getattr(inst, "banner_url", None) or (getattr(p, "banner_url", None) if p else None),
        "author": getattr(inst, "author", None) or (getattr(p, "author", "SenWiseTool") if p else "SenWiseTool"),
        "status": getattr(inst, "status", "installed"),
        "policy_accepted_at": getattr(inst, "policy_accepted_at", None),
        "policy_accepted_by_user_id": getattr(inst, "policy_accepted_by_user_id", None),
        "policy_version": getattr(inst, "policy_version", ""),
        "local_settings": getattr(inst, "settings", None) or getattr(inst, "local_settings", {}) or {},
        "installed_at": getattr(inst, "installed_at", None) or getattr(inst, "created_at", None),
        "updated_at": getattr(inst, "updated_at", None),
        "plugin": _format_plugin_dict(p, org_map=org_map) if (p and not isinstance(p, dict)) else p,
        "installed_version": formatted_iv,
        "latest_version": formatted_lv,
        "downloaded_versions": downloaded_versions,
        "downloadedVersions": downloaded_versions,
        "has_update_available": has_update,
    }


@router.get("/catalog", response_model=MarketplaceCatalogResponse)
async def get_marketplace_catalog(
    category: Optional[str] = Query(None, description="Filtrer par catégorie"),
    search: Optional[str] = Query(None, description="Recherche textuelle"),
    org_id: Optional[int] = Query(None, description="Contexte organisationnel spécifique"),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_current_user),
):
    """
    [Public / Connecté] Catalogue complet des plugins officiels publiés.
    Si l'utilisateur est connecté et rattaché à une organisation, chaque item intègre
    son statut d'installation et signale si une mise à jour est disponible.
    Le Super Admin peut inspecter l'ensemble du catalogue (publics et privés).
    """
    resolved_org = None
    if current_user:
        resolved_org = await _resolve_user_organization(db, current_user, org_id)

    is_super_admin = bool(
        current_user and (
            getattr(current_user, "is_superuser", False) or
            getattr(current_user, "role", "") == UserRole.SUPER_ADMIN or
            str(getattr(current_user, "role", "")).upper() == "SUPER_ADMIN"
        )
    )

    catalog_data = await plugin_service.get_marketplace_catalog(
        db=db,
        organization_id=resolved_org.id if resolved_org else None,
        category=category,
        search=search,
        is_super_admin=is_super_admin,
    )

    # Enrichir les versions avec les détails des entités BD référencées (projets, formulaires, etc.)
    all_versions_to_enrich = []
    for item in catalog_data["items"]:
        if item.get("latest_version"):
            all_versions_to_enrich.append(item["latest_version"])
        p = item.get("plugin")
        if p and "versions" in getattr(p, "__dict__", {}) and p.versions:
            all_versions_to_enrich.extend(p.versions)
        inst = item.get("installed_instance")
        if inst:
            inst_v = inst.get("installed_version") if isinstance(inst, dict) else getattr(inst, "installed_version", None)
            if inst_v and not isinstance(inst_v, dict):
                all_versions_to_enrich.append(inst_v)

    await _enrich_versions_with_referenced_entities(db, all_versions_to_enrich)

    # Récupérer les noms des organisations autorisées pour les plugins privés
    all_allowed_ids = set()
    for item in catalog_data["items"]:
        ids = getattr(item["plugin"], "allowed_organization_ids", None) or []
        all_allowed_ids.update(ids)

    org_map: Dict[int, str] = {}
    if all_allowed_ids:
        org_rows = await db.execute(
            select(Organization.id, Organization.name).where(Organization.id.in_(list(all_allowed_ids)))
        )
        for row in org_rows.all():
            org_map[row[0]] = row[1]

    formatted_items = []
    for item in catalog_data["items"]:
        plug_dict = _format_plugin_dict(item["plugin"], org_map=org_map)
        inst_dict = None
        if item.get("installed_instance"):
            inst_obj = item["installed_instance"]
            iv = inst_obj.get("installed_version") if isinstance(inst_obj, dict) else getattr(inst_obj, "installed_version", None)
            inst_dict = _format_installation_dict(
                inst=inst_obj,
                plug=item.get("plugin"),
                installed_version=iv,
                latest_version=item.get("latest_version"),
                has_update=item.get("has_update_available", False),
                org_map=org_map,
            )

        formatted_items.append({
            "plugin": plug_dict,
            "latest_version": _format_version_dict(item["latest_version"]),
            "is_installed": item["is_installed"],
            "installed_instance": inst_dict,
            "has_update_available": item["has_update_available"]
        })

    return {
        "items": formatted_items,
        "categories": catalog_data["categories"],
        "total": len(formatted_items)
    }


@router.get("/plugins/{code}", response_model=Dict[str, Any])
async def get_marketplace_plugin_detail(
    code: str,
    org_id: Optional[int] = Query(None, description="Contexte organisationnel spécifique"),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_current_user),
):
    """
    [Public / Connecté] Fiche descriptive complète d'un plugin, incluant la politique d'utilisation (EULA)
    de chaque version pour lecture et accord préalable.
    """
    resolved_org = None
    if current_user:
        resolved_org = await _resolve_user_organization(db, current_user, org_id)

    is_super_admin = bool(
        current_user and (
            getattr(current_user, "is_superuser", False) or
            getattr(current_user, "role", "") == UserRole.SUPER_ADMIN or
            str(getattr(current_user, "role", "")).upper() == "SUPER_ADMIN"
        )
    )

    detail_data = await plugin_service.get_marketplace_plugin_detail(
        db=db,
        code=code,
        organization_id=resolved_org.id if resolved_org else None,
        is_super_admin=is_super_admin,
    )

    # Enrichir les versions avec les détails des entités BD référencées (projets, formulaires, etc.)
    all_versions_to_enrich = []
    if detail_data.get("latest_version"):
        all_versions_to_enrich.append(detail_data["latest_version"])
    if detail_data.get("versions"):
        all_versions_to_enrich.extend(detail_data["versions"])
    p = detail_data.get("plugin")
    if p and "versions" in getattr(p, "__dict__", {}) and p.versions:
        all_versions_to_enrich.extend(p.versions)
    inst = detail_data.get("installed_instance")
    if inst:
        inst_v = inst.get("installed_version") if isinstance(inst, dict) else getattr(inst, "installed_version", None)
        if inst_v and not isinstance(inst_v, dict):
            all_versions_to_enrich.append(inst_v)

    await _enrich_versions_with_referenced_entities(db, all_versions_to_enrich)

    # Récupérer les noms des organisations autorisées si plugin privé
    plug = detail_data["plugin"]
    allowed_ids = getattr(plug, "allowed_organization_ids", None) or []
    org_map: Dict[int, str] = {}
    if allowed_ids:
        org_rows = await db.execute(
            select(Organization.id, Organization.name).where(Organization.id.in_(allowed_ids))
        )
        for row in org_rows.all():
            org_map[row[0]] = row[1]

    plug_dict = _format_plugin_dict(plug, org_map=org_map)
    inst_dict = None
    if detail_data.get("installed_instance"):
        inst_obj = detail_data["installed_instance"]
        iv = inst_obj.get("installed_version") if isinstance(inst_obj, dict) else getattr(inst_obj, "installed_version", None)
        inst_dict = _format_installation_dict(
            inst=inst_obj,
            plug=plug,
            installed_version=iv,
            latest_version=detail_data.get("latest_version"),
            has_update=detail_data.get("has_update_available", False),
            org_map=org_map,
        )

    return {
        "plugin": plug_dict,
        "versions": [_format_version_dict(v) for v in detail_data["versions"]],
        "latest_version": _format_version_dict(detail_data["latest_version"]),
        "is_installed": detail_data["is_installed"],
        "installed_instance": inst_dict,
        "has_update_available": detail_data["has_update_available"]
    }


@router.get("/installed", response_model=List[OrganizationPluginResponse])
async def get_organization_installed_plugins(
    org_id: Optional[int] = Query(None, description="ID organisation pour Super Admin"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    [Organisation] Récupère la liste souveraine des plugins installés et actifs pour l'organisation de l'utilisateur.
    """
    org = await _resolve_user_organization(db, current_user, org_id)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Aucune organisation active n'est associée à votre compte."
        )

    raw_items = await plugin_service.list_installed_plugins_for_org(db=db, org_id=org.id)
    formatted = []
    for item in raw_items:
        formatted.append(
            _format_installation_dict(
                inst=item["installation"],
                plug=item.get("plugin"),
                installed_version=item.get("installed_version"),
                latest_version=item.get("latest_version"),
                has_update=item.get("has_update_available", False),
            )
        )
    return formatted


@router.post("/install", response_model=OrganizationPluginResponse, status_code=status.HTTP_201_CREATED)
async def install_plugin(
    payload: PluginInstallRequest,
    org_id: Optional[int] = Query(None, description="ID organisation pour Super Admin"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    [Organisation] Télécharge et installe un plugin et sa version pour l'organisation cliente.
    """
    org = await _resolve_user_organization(db, current_user, org_id)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Aucune organisation active n'est associée à votre compte."
        )

    return await plugin_service.install_plugin_for_org(
        db=db,
        org_id=org.id,
        plugin_id=payload.plugin_id,
        policy_accepted=payload.policy_accepted,
        user=current_user,
        version_id=payload.version_id,
        local_settings=payload.local_settings
    )


@router.post("/upgrade", response_model=OrganizationPluginResponse)
async def upgrade_plugin(
    payload: PluginUpgradeRequest,
    org_id: Optional[int] = Query(None, description="ID organisation pour Super Admin"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    [Organisation] Met à niveau un plugin vers une version supérieure sans perte de données.
    """
    org = await _resolve_user_organization(db, current_user, org_id)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Aucune organisation active n'est associée à votre compte."
        )

    return await plugin_service.upgrade_plugin_for_org(
        db=db,
        org_id=org.id,
        plugin_id=payload.plugin_id,
        policy_accepted=payload.policy_accepted,
        user=current_user,
        target_version_id=payload.target_version_id
    )


@router.post("/switch-version", response_model=OrganizationPluginResponse)
async def switch_plugin_version(
    payload: PluginVersionSwitchRequest,
    org_id: Optional[int] = Query(None, description="ID organisation pour Super Admin"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    [Organisation] Bascule vers une version spécifique du plugin.
    """
    org = await _resolve_user_organization(db, current_user, org_id)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Aucune organisation active n'est associée à votre compte."
        )

    return await plugin_service.install_plugin_for_org(
        db=db,
        org_id=org.id,
        plugin_id=payload.plugin_id,
        policy_accepted=True,
        user=current_user,
        version_id=payload.version_id
    )


@router.post("/uninstall")
async def uninstall_plugin(
    payload: PluginUninstallRequest,
    org_id: Optional[int] = Query(None, description="ID organisation pour Super Admin"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    [Organisation] Désinstalle un plugin pour l'organisation cliente.
    """
    org = await _resolve_user_organization(db, current_user, org_id)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Aucune organisation active n'est associée à votre compte."
        )

    return await plugin_service.uninstall_plugin_for_org(
        db=db,
        org_id=org.id,
        plugin_id=payload.plugin_id,
        user=current_user,
        reason=payload.reason
    )


@router.get("/organization/downloaded-projects")
async def get_downloaded_projects(
    org_id: Optional[int] = Query(None, description="ID organisation"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    org = await _resolve_user_organization(db, current_user, org_id)
    if not org:
        return []
    stmt = select(OrganizationProject).where(OrganizationProject.organization_id == org.id).order_by(OrganizationProject.id.desc())
    return (await db.execute(stmt)).scalars().all()


@router.get("/organization/downloaded-features/projects")
async def get_downloaded_project_features(
    org_id: Optional[int] = Query(None, description="ID organisation"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    org = await _resolve_user_organization(db, current_user, org_id)
    if not org:
        return None
    stmt = select(OrganizationFeature).where(
        OrganizationFeature.feature_code == "projects",
        OrganizationFeature.status == "active"
    ).order_by(OrganizationFeature.id.desc())
    all_f = (await db.execute(stmt)).scalars().all()
    for f in all_f:
        if org.id in (f.organization_ids or []):
            return f
    return None


@router.get("/organization/downloaded-versions")
async def get_downloaded_versions(
    plugin_id: Optional[int] = Query(None),
    org_id: Optional[int] = Query(None, description="ID organisation"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    org = await _resolve_user_organization(db, current_user, org_id)
    if not org:
        return []
    stmt = select(OrganizationPluginVersion).order_by(OrganizationPluginVersion.id.desc())
    if plugin_id:
        stmt = stmt.join(OrganizationPlugin, OrganizationPlugin.id == OrganizationPluginVersion.organization_plugin_id).where(OrganizationPlugin.source_plugin_id == plugin_id)
    all_v = (await db.execute(stmt)).scalars().all()
    return [v for v in all_v if org.id in (v.organization_ids or [])]


@router.get("/organization/workspace-summary")
async def get_workspace_summary(
    org_id: Optional[int] = Query(None, description="ID organisation"),
    plugin_code: Optional[str] = Query(None, description="Code du plugin actif"),
    version_id: Optional[int] = Query(None, description="ID de la version active du plugin"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    org = await _resolve_user_organization(db, current_user, org_id)
    if not org:
        return {
            "plugins": [],
            "projects": [],
            "resources": [],
            "forms": [],
            "campaigns": [],
            "features": []
        }

    p_stmt = select(OrganizationProject).options(selectinload(OrganizationProject.versions)).where(OrganizationProject.organization_id == org.id).order_by(OrganizationProject.id.desc())
    projs = (await db.execute(p_stmt)).scalars().all()

    r_stmt = select(OrganizationResource).options(selectinload(OrganizationResource.versions)).where(OrganizationResource.organization_id == org.id).order_by(OrganizationResource.id.desc())
    ress = (await db.execute(r_stmt)).scalars().all()

    f_stmt = select(OrganizationForm).options(selectinload(OrganizationForm.versions)).where(OrganizationForm.organization_id == org.id).order_by(OrganizationForm.id.desc())
    forms = (await db.execute(f_stmt)).scalars().all()

    c_stmt = select(OrganizationCampaign).options(selectinload(OrganizationCampaign.versions)).where(OrganizationCampaign.organization_id == org.id).order_by(OrganizationCampaign.id.desc())
    camps = (await db.execute(c_stmt)).scalars().all()

    feat_stmt = select(OrganizationFeature).order_by(OrganizationFeature.id.desc())
    all_feats = (await db.execute(feat_stmt)).scalars().all()
    org_feats = [feat for feat in all_feats if org.id in (feat.organization_ids or [])]

    raw_installs = await plugin_service.list_installed_plugins_for_org(db=db, org_id=org.id)

    def _is_pub(v) -> bool:
        return getattr(v, "status", "") == "published" or getattr(v, "publication_number", None) is not None or "/" in str(getattr(v, "version_number", ""))

    def _serialize_res_version(rv):
        return {
            "id": rv.id,
            "version_number": rv.version_number,
            "status": rv.status,
            "description": rv.description,
            "schema_fields": rv.schema_fields or [],
            "metrics": rv.metrics or {},
            "template_governance": rv.template_governance or {},
            "parameters": rv.parameters or {},
            "settings": rv.settings or {},
            "publication_number": rv.publication_number,
            "origin_version_id": rv.origin_version_id,
            "organization_plugin_version_id": rv.organization_plugin_version_id,
            "published_at": rv.published_at.isoformat() if rv.published_at else None,
        }

    def _serialize_proj_version(pv):
        return {
            "id": pv.id,
            "version_number": pv.version_number,
            "status": pv.status,
            "description": pv.description,
            "template_governance": pv.template_governance or {},
            "parameters": pv.parameters or {},
            "settings": pv.settings or {},
            "publication_number": pv.publication_number,
            "origin_version_id": pv.origin_version_id,
            "organization_plugin_version_id": pv.organization_plugin_version_id,
            "published_at": pv.published_at.isoformat() if pv.published_at else None,
        }

    def _serialize_form_version(fv):
        return {
            "id": fv.id,
            "version_number": fv.version_number,
            "status": fv.status,
            "description": fv.description,
            "form_type": fv.form_type,
            "template_governance": fv.template_governance or {},
            "parameters": fv.parameters or {},
            "settings": fv.settings or {},
            "publication_number": fv.publication_number,
            "origin_version_id": fv.origin_version_id,
            "organization_plugin_version_id": fv.organization_plugin_version_id,
            "published_at": fv.published_at.isoformat() if fv.published_at else None,
        }

    def _serialize_camp_version(cv):
        return {
            "id": cv.id,
            "version_number": cv.version_number,
            "status": cv.status,
            "description": cv.description,
            "start_date": cv.start_date.isoformat() if cv.start_date else None,
            "end_date": cv.end_date.isoformat() if cv.end_date else None,
            "budget": cv.budget,
            "currency": cv.currency,
            "target_forms": cv.target_forms or [],
            "target_projects": cv.target_projects or [],
            "template_governance": cv.template_governance or {},
            "parameters": cv.parameters or {},
            "settings": cv.settings or {},
            "publication_number": cv.publication_number,
            "origin_version_id": cv.origin_version_id,
            "organization_plugin_version_id": cv.organization_plugin_version_id,
            "published_at": cv.published_at.isoformat() if cv.published_at else None,
        }

    serialized_projects = []
    for p in projs:
        all_p_vers = p.versions or []
        nat_vers = [pv for pv in all_p_vers if not _is_pub(pv)]
        pub_vers = [pv for pv in all_p_vers if _is_pub(pv)]
        # Classer natifs en premier par version / id décroissant
        sorted_vers = sorted(nat_vers, key=lambda x: x.id, reverse=True) + sorted(pub_vers, key=lambda x: (x.publication_number or 0, x.id), reverse=True)
        serialized_projects.append({
            "id": p.id,
            "source_project_id": p.source_project_id,
            "name": p.name,
            "code": p.code,
            "project_type": p.project_type,
            "category": p.category,
            "status": p.status,
            "parameters": p.parameters or {},
            "settings": p.settings or {},
            "versions": [_serialize_proj_version(pv) for pv in sorted_vers],
            "native_versions": [_serialize_proj_version(pv) for pv in sorted(nat_vers, key=lambda x: x.id, reverse=True)],
            "published_versions": [_serialize_proj_version(pv) for pv in sorted(pub_vers, key=lambda x: (x.publication_number or 0, x.id), reverse=True)],
        })

    serialized_resources = []
    for r in ress:
        all_r_vers = r.versions or []
        nat_vers = [rv for rv in all_r_vers if not _is_pub(rv)]
        pub_vers = [rv for rv in all_r_vers if _is_pub(rv)]
        sorted_vers = sorted(nat_vers, key=lambda x: x.id, reverse=True) + sorted(pub_vers, key=lambda x: (x.publication_number or 0, x.id), reverse=True)
        serialized_resources.append({
            "id": r.id,
            "source_resource_id": r.source_resource_id,
            "name": r.name,
            "code": r.code,
            "category": r.category,
            "status": r.status,
            "version": r.version,
            "icon": r.icon,
            "cover_image_url": r.cover_image_url,
            "schema_fields": r.schema_fields or [],
            "metrics": r.metrics or {},
            "parameters": r.parameters or {},
            "settings": r.settings or {},
            "versions": [_serialize_res_version(rv) for rv in sorted_vers],
            "native_versions": [_serialize_res_version(rv) for rv in sorted(nat_vers, key=lambda x: x.id, reverse=True)],
            "published_versions": [_serialize_res_version(rv) for rv in sorted(pub_vers, key=lambda x: (x.publication_number or 0, x.id), reverse=True)],
        })

    serialized_forms = []
    for f in forms:
        all_f_vers = f.versions or []
        nat_vers = [fv for fv in all_f_vers if not _is_pub(fv)]
        pub_vers = [fv for fv in all_f_vers if _is_pub(fv)]
        sorted_vers = sorted(nat_vers, key=lambda x: x.id, reverse=True) + sorted(pub_vers, key=lambda x: (x.publication_number or 0, x.id), reverse=True)
        serialized_forms.append({
            "id": f.id,
            "source_form_id": f.source_form_id,
            "name": f.name,
            "code": f.code,
            "description": f.description,
            "status": f.status,
            "version": f.version,
            "parameters": f.parameters or {},
            "settings": f.settings or {},
            "versions": [_serialize_form_version(fv) for fv in sorted_vers],
            "native_versions": [_serialize_form_version(fv) for fv in sorted(nat_vers, key=lambda x: x.id, reverse=True)],
            "published_versions": [_serialize_form_version(fv) for fv in sorted(pub_vers, key=lambda x: (x.publication_number or 0, x.id), reverse=True)],
        })

    serialized_campaigns = []
    for c in camps:
        all_c_vers = c.versions or []
        nat_vers = [cv for cv in all_c_vers if not _is_pub(cv)]
        pub_vers = [cv for cv in all_c_vers if _is_pub(cv)]
        sorted_vers = sorted(nat_vers, key=lambda x: x.id, reverse=True) + sorted(pub_vers, key=lambda x: (x.publication_number or 0, x.id), reverse=True)
        serialized_campaigns.append({
            "id": c.id,
            "source_campaign_id": c.source_campaign_id,
            "name": c.name,
            "code": c.code,
            "description": c.description,
            "status": c.status,
            "version": c.version,
            "parameters": c.parameters or {},
            "settings": c.settings or {},
            "target_forms": (c.versions[0].target_forms if c.versions else []) or [],
            "target_projects": (c.versions[0].target_projects if c.versions else []) or [],
            "versions": [_serialize_camp_version(cv) for cv in sorted_vers],
            "native_versions": [_serialize_camp_version(cv) for cv in sorted(nat_vers, key=lambda x: x.id, reverse=True)],
            "published_versions": [_serialize_camp_version(cv) for cv in sorted(pub_vers, key=lambda x: (x.publication_number or 0, x.id), reverse=True)],
        })

    return {
        "plugins": [it["installation"] for it in raw_installs],
        "projects": serialized_projects,
        "resources": serialized_resources,
        "forms": serialized_forms,
        "campaigns": serialized_campaigns,
        "features": [
            {
                "id": feat.id,
                "feature_code": feat.feature_code,
                "name": feat.name,
                "description": feat.description,
                "parameters": feat.parameters or {},
                "permissions": feat.permissions or {},
                "status": feat.status,
            }
            for feat in org_feats
        ]
    }


@router.get("/organization/published-plugins")
async def get_published_plugins(
    org_id: Optional[int] = Query(None, description="ID organisation"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    [Espace Organisation] Retourne la liste des plugins ayant au moins une version
    avec des modules publiés (pour alimenter la sidebar de l'organisation).
    """
    # Si Super Admin sans organisation spécifique demandée, ne pas charger d'organisation arbitraire
    is_super = bool(
        current_user.role == UserRole.SUPER_ADMIN or
        getattr(current_user, "is_superuser", False) or
        str(getattr(current_user, "role", "")).upper() == "SUPER_ADMIN"
    )
    if is_super and not org_id:
        return {
            "organization": None,
            "plugins": [],
            "plugins_with_publications": [],
            "total_published_modules": 0,
        }

    org = await _resolve_user_organization(db, current_user, org_id)
    if not org:
        return {
            "organization": None,
            "plugins": [],
            "plugins_with_publications": [],
            "total_published_modules": 0,
        }

    raw_installs = await plugin_service.list_installed_plugins_for_org(db=db, org_id=org.id)
    plugins_data = [it["installation"] for it in raw_installs]

    r_stmt = select(OrganizationResource).options(selectinload(OrganizationResource.versions)).where(
        OrganizationResource.organization_id == org.id
    )
    all_ress = (await db.execute(r_stmt)).scalars().all()

    f_stmt = select(OrganizationForm).options(selectinload(OrganizationForm.versions)).where(
        OrganizationForm.organization_id == org.id
    )
    all_forms = (await db.execute(f_stmt)).scalars().all()

    p_stmt = select(OrganizationProject).options(selectinload(OrganizationProject.versions)).where(
        OrganizationProject.organization_id == org.id
    )
    all_projs = (await db.execute(p_stmt)).scalars().all()

    c_stmt = select(OrganizationCampaign).options(selectinload(OrganizationCampaign.versions)).where(
        OrganizationCampaign.organization_id == org.id
    )
    all_camps = (await db.execute(c_stmt)).scalars().all()

    def _is_pub(v) -> bool:
        return getattr(v, "status", "") == "published" or getattr(v, "publication_number", None) is not None or "/" in str(getattr(v, "version_number", ""))

    pub_counts_by_opv_id: Dict[int, int] = {}
    for r in all_ress:
        for v in (r.versions or []):
            if _is_pub(v) and v.organization_plugin_version_id:
                pub_counts_by_opv_id[v.organization_plugin_version_id] = pub_counts_by_opv_id.get(v.organization_plugin_version_id, 0) + 1
    for f in all_forms:
        for v in (f.versions or []):
            if _is_pub(v) and v.organization_plugin_version_id:
                pub_counts_by_opv_id[v.organization_plugin_version_id] = pub_counts_by_opv_id.get(v.organization_plugin_version_id, 0) + 1
    for p in all_projs:
        for v in (p.versions or []):
            if _is_pub(v) and v.organization_plugin_version_id:
                pub_counts_by_opv_id[v.organization_plugin_version_id] = pub_counts_by_opv_id.get(v.organization_plugin_version_id, 0) + 1
    for c in all_camps:
        for v in (c.versions or []):
            if _is_pub(v) and v.organization_plugin_version_id:
                pub_counts_by_opv_id[v.organization_plugin_version_id] = pub_counts_by_opv_id.get(v.organization_plugin_version_id, 0) + 1

    plugins_with_publications = []
    for plug in plugins_data:
        downloaded = plug.get("downloaded_versions") or []
        vers_with_pubs = [
            {
                "id": dv.get("id"),
                "version_number": dv.get("version_number"),
                "published_count": pub_counts_by_opv_id.get(dv.get("id"), 0),
            }
            for dv in downloaded
            if pub_counts_by_opv_id.get(dv.get("id"), 0) > 0
        ]
        total_pub = sum(v["published_count"] for v in vers_with_pubs)
        if total_pub > 0:
            plugins_with_publications.append({
                "id": plug.get("id"),
                "code": plug.get("code"),
                "name": plug.get("name"),
                "icon_url": plug.get("icon_url"),
                "banner_url": plug.get("banner_url"),
                "category": plug.get("category"),
                "total_published_count": total_pub,
                "total_published_modules": total_pub,
                "versions_with_publications": vers_with_pubs,
            })

    total_published_modules = sum(p["total_published_count"] for p in plugins_with_publications)

    return {
        "organization": {"id": org.id, "name": org.name},
        "plugins": plugins_with_publications,
        "plugins_with_publications": plugins_with_publications,
        "total_published_modules": total_published_modules,
    }


@router.get("/organization/published-modules-summary")
async def get_published_modules_summary(
    org_id: Optional[int] = Query(None, description="ID organisation"),
    plugin_code: Optional[str] = Query(None, description="Code du plugin actif"),
    version_id: Optional[int] = Query(None, description="ID de la version du plugin active"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    [Espace Organisation] Retourne l'ensemble des modules publiés et scellés
    (ressources, formulaires, projets, campagnes) pour le plugin et la version sélectionnés.
    Permet de basculer entre les différentes versions d'un plugin pour explorer leurs publications scellées.
    """
    org = await _resolve_user_organization(db, current_user, org_id)
    if not org:
        return {
            "plugins": [],
            "plugins_with_publications": [],
            "selected_plugin": None,
            "selected_version": None,
            "available_versions": [],
            "published_resources": [],
            "published_forms": [],
            "published_projects": [],
            "published_campaigns": [],
            "total_count": 0,
        }

    raw_installs = await plugin_service.list_installed_plugins_for_org(db=db, org_id=org.id)
    plugins_data = [it["installation"] for it in raw_installs]

    # Requêtes pour tous les modules
    r_stmt = select(OrganizationResource).options(selectinload(OrganizationResource.versions)).where(
        OrganizationResource.organization_id == org.id
    ).order_by(OrganizationResource.id.asc())
    all_ress = (await db.execute(r_stmt)).scalars().all()

    f_stmt = select(OrganizationForm).options(selectinload(OrganizationForm.versions)).where(
        OrganizationForm.organization_id == org.id
    ).order_by(OrganizationForm.id.asc())
    all_forms = (await db.execute(f_stmt)).scalars().all()

    p_stmt = select(OrganizationProject).options(selectinload(OrganizationProject.versions)).where(
        OrganizationProject.organization_id == org.id
    ).order_by(OrganizationProject.id.asc())
    all_projs = (await db.execute(p_stmt)).scalars().all()

    c_stmt = select(OrganizationCampaign).options(selectinload(OrganizationCampaign.versions)).where(
        OrganizationCampaign.organization_id == org.id
    ).order_by(OrganizationCampaign.id.asc())
    all_camps = (await db.execute(c_stmt)).scalars().all()

    def _is_pub(v) -> bool:
        return getattr(v, "status", "") == "published" or getattr(v, "publication_number", None) is not None or "/" in str(getattr(v, "version_number", ""))

    # Calcul des publications par organization_plugin_version_id
    pub_counts_by_opv_id: Dict[int, int] = {}
    for r in all_ress:
        for v in (r.versions or []):
            if _is_pub(v) and v.organization_plugin_version_id:
                pub_counts_by_opv_id[v.organization_plugin_version_id] = pub_counts_by_opv_id.get(v.organization_plugin_version_id, 0) + 1
    for f in all_forms:
        for v in (f.versions or []):
            if _is_pub(v) and v.organization_plugin_version_id:
                pub_counts_by_opv_id[v.organization_plugin_version_id] = pub_counts_by_opv_id.get(v.organization_plugin_version_id, 0) + 1
    for p in all_projs:
        for v in (p.versions or []):
            if _is_pub(v) and v.organization_plugin_version_id:
                pub_counts_by_opv_id[v.organization_plugin_version_id] = pub_counts_by_opv_id.get(v.organization_plugin_version_id, 0) + 1
    for c in all_camps:
        for v in (c.versions or []):
            if _is_pub(v) and v.organization_plugin_version_id:
                pub_counts_by_opv_id[v.organization_plugin_version_id] = pub_counts_by_opv_id.get(v.organization_plugin_version_id, 0) + 1

    plugins_with_publications = []
    for plug in plugins_data:
        downloaded = plug.get("downloaded_versions") or []
        vers_with_pubs = [
            {
                "id": dv.get("id"),
                "version_number": dv.get("version_number"),
                "published_count": pub_counts_by_opv_id.get(dv.get("id"), 0),
            }
            for dv in downloaded
            if pub_counts_by_opv_id.get(dv.get("id"), 0) > 0
        ]
        total_pub = sum(v["published_count"] for v in vers_with_pubs)
        if total_pub > 0:
            plugins_with_publications.append({
                "id": plug.get("id"),
                "code": plug.get("code"),
                "name": plug.get("name"),
                "icon_url": plug.get("icon_url"),
                "banner_url": plug.get("banner_url"),
                "category": plug.get("category"),
                "total_published_count": total_pub,
                "versions_with_publications": vers_with_pubs,
            })

    # Résoudre le plugin sélectionné
    selected_plugin = None
    if plugin_code:
        for p in plugins_data:
            if p.get("code") == plugin_code:
                selected_plugin = p
                break
    # Si pas de plugin_code spécifique, privilégier le premier plugin ayant des publications
    if not selected_plugin and plugins_with_publications:
        target_code = plugins_with_publications[0]["code"]
        for p in plugins_data:
            if p.get("code") == target_code:
                selected_plugin = p
                break
    if not selected_plugin and plugins_data:
        selected_plugin = plugins_data[0]

    # Versions ayant au moins 1 publication pour ce plugin sélectionné
    available_versions = []
    if selected_plugin:
        downloaded = selected_plugin.get("downloaded_versions") or []
        available_versions = [
            dv for dv in downloaded
            if pub_counts_by_opv_id.get(dv.get("id"), 0) > 0
        ]

    # Résoudre la version sélectionnée : privilégier une version qui a des publications !
    selected_version = None
    target_org_plugin_version_id = None
    if selected_plugin:
        downloaded = selected_plugin.get("downloaded_versions") or []
        if version_id:
            for dv in downloaded:
                if dv.get("id") == version_id or dv.get("plugin_version_id") == version_id:
                    selected_version = dv
                    target_org_plugin_version_id = dv.get("id")
                    break

        if not selected_version:
            # Privilégier la première version ayant des publications
            if available_versions:
                selected_version = available_versions[0]
                target_org_plugin_version_id = selected_version.get("id")
            elif downloaded:
                selected_version = downloaded[0]
                target_org_plugin_version_id = selected_version.get("id")

    published_resources = []
    for r in all_ress:
        pub_vers = [v for v in (r.versions or []) if _is_pub(v)]
        if target_org_plugin_version_id:
            pub_vers = [v for v in pub_vers if v.organization_plugin_version_id == target_org_plugin_version_id]

        if pub_vers:
            published_resources.append({
                "id": r.id,
                "name": r.name,
                "code": r.code,
                "category": r.category,
                "icon": r.icon,
                "cover_image_url": r.cover_image_url,
                "published_versions": [
                    {
                        "id": pv.id,
                        "version_number": pv.version_number,
                        "status": pv.status,
                        "publication_number": pv.publication_number,
                        "origin_version_id": pv.origin_version_id,
                        "published_at": pv.published_at.isoformat() if pv.published_at else None,
                        "description": pv.description,
                        "changelog": pv.changelog,
                        "schema_fields": pv.schema_fields or [],
                        "fields_count": len(pv.schema_fields or []),
                        "metrics": pv.metrics or {},
                        "item_count": (pv.metrics or {}).get("item_count", 0),
                        "template_governance": pv.template_governance or {},
                        "organization_plugin_version_id": pv.organization_plugin_version_id,
                    }
                    for pv in sorted(pub_vers, key=lambda x: (x.publication_number or 0, x.id), reverse=True)
                ]
            })

    published_forms = []
    for f in all_forms:
        pub_vers = [v for v in (f.versions or []) if _is_pub(v)]
        if target_org_plugin_version_id:
            pub_vers = [v for v in pub_vers if v.organization_plugin_version_id == target_org_plugin_version_id]

        if pub_vers:
            published_forms.append({
                "id": f.id,
                "name": f.name,
                "code": f.code,
                "published_versions": [
                    {
                        "id": pv.id,
                        "version_number": pv.version_number,
                        "status": pv.status,
                        "publication_number": pv.publication_number,
                        "origin_version_id": pv.origin_version_id,
                        "published_at": pv.published_at.isoformat() if pv.published_at else None,
                        "description": pv.description,
                        "form_type": pv.form_type,
                        "template_governance": pv.template_governance or {},
                        "organization_plugin_version_id": pv.organization_plugin_version_id,
                    }
                    for pv in sorted(pub_vers, key=lambda x: (x.publication_number or 0, x.id), reverse=True)
                ]
            })

    published_projects = []
    for p in all_projs:
        pub_vers = [v for v in (p.versions or []) if _is_pub(v)]
        if target_org_plugin_version_id:
            pub_vers = [v for v in pub_vers if v.organization_plugin_version_id == target_org_plugin_version_id]

        if pub_vers:
            published_projects.append({
                "id": p.id,
                "name": p.name,
                "code": p.code,
                "project_type": p.project_type,
                "category": p.category,
                "published_versions": [
                    {
                        "id": pv.id,
                        "version_number": pv.version_number,
                        "status": pv.status,
                        "publication_number": pv.publication_number,
                        "origin_version_id": pv.origin_version_id,
                        "published_at": pv.published_at.isoformat() if pv.published_at else None,
                        "description": pv.description,
                        "template_governance": pv.template_governance or {},
                        "organization_plugin_version_id": pv.organization_plugin_version_id,
                    }
                    for pv in sorted(pub_vers, key=lambda x: (x.publication_number or 0, x.id), reverse=True)
                ]
            })

    published_campaigns = []
    for c in all_camps:
        pub_vers = [v for v in (c.versions or []) if _is_pub(v)]
        if target_org_plugin_version_id:
            pub_vers = [v for v in pub_vers if v.organization_plugin_version_id == target_org_plugin_version_id]

        if pub_vers:
            published_campaigns.append({
                "id": c.id,
                "name": c.name,
                "code": c.code,
                "published_versions": [
                    {
                        "id": pv.id,
                        "version_number": pv.version_number,
                        "status": pv.status,
                        "publication_number": pv.publication_number,
                        "origin_version_id": pv.origin_version_id,
                        "published_at": pv.published_at.isoformat() if pv.published_at else None,
                        "description": pv.description,
                        "start_date": pv.start_date.isoformat() if pv.start_date else None,
                        "end_date": pv.end_date.isoformat() if pv.end_date else None,
                        "budget": pv.budget,
                        "currency": pv.currency,
                        "template_governance": pv.template_governance or {},
                        "organization_plugin_version_id": pv.organization_plugin_version_id,
                    }
                    for pv in sorted(pub_vers, key=lambda x: (x.publication_number or 0, x.id), reverse=True)
                ]
            })

    total_count = (
        sum(len(r["published_versions"]) for r in published_resources)
        + sum(len(f["published_versions"]) for f in published_forms)
        + sum(len(p["published_versions"]) for p in published_projects)
        + sum(len(c["published_versions"]) for c in published_campaigns)
    )

    return {
        "organization": {"id": org.id, "name": org.name},
        "plugins": plugins_data,
        "plugins_with_publications": plugins_with_publications,
        "selected_plugin": selected_plugin,
        "selected_version": selected_version,
        "available_versions": available_versions,
        "published_resources": published_resources,
        "published_forms": published_forms,
        "published_projects": published_projects,
        "published_campaigns": published_campaigns,
        "total_count": total_count,
    }


@router.get("/organization/workspace/features/{feature_code}")
async def get_workspace_feature(
    feature_code: str,
    org_id: Optional[int] = Query(None, description="ID organisation"),
    plugin_id: Optional[int] = Query(None),
    plugin_code: Optional[str] = Query(None),
    version_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    [Espace Organisation] Retourne le détail d'une fonctionnalité souveraine
    avec ses modules opérationnels rattachés (projets, formulaires, campagnes, ressources).
    """
    org = await _resolve_user_organization(db, current_user, org_id)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organisation introuvable."
        )

    stmt = select(OrganizationFeature).where(
        OrganizationFeature.feature_code == feature_code,
        OrganizationFeature.status == "active"
    ).order_by(OrganizationFeature.id.desc())
    feats = (await db.execute(stmt)).scalars().all()
    target_feat = None
    for f in feats:
        if org.id in (f.organization_ids or []):
            target_feat = f
            break
    if not target_feat and feats:
        target_feat = feats[0]

    plug_info = None
    inst_stmt = select(OrganizationPlugin).where(
        OrganizationPlugin.status == "active"
    ).order_by(OrganizationPlugin.id.desc())
    inst_plugs = (await db.execute(inst_stmt)).scalars().all()
    inst_plug = None
    for p in inst_plugs:
        if org.id in (p.organization_ids or []):
            inst_plug = p
            break
    if not inst_plug and inst_plugs:
        inst_plug = inst_plugs[0]
    if inst_plug:
        plug_info = {
            "id": inst_plug.id,
            "name": inst_plug.name,
            "code": inst_plug.code,
            "category": inst_plug.category,
            "description": inst_plug.description,
            "icon_url": inst_plug.icon_url,
            "policy_version": "1.0.0",
        }

    projs_stmt = select(OrganizationProject).options(selectinload(OrganizationProject.versions)).where(
        OrganizationProject.organization_id == org.id
    ).order_by(OrganizationProject.id.desc())
    projs = (await db.execute(projs_stmt)).scalars().all()

    ress_stmt = select(OrganizationResource).options(selectinload(OrganizationResource.versions)).where(
        OrganizationResource.organization_id == org.id
    ).order_by(OrganizationResource.id.desc())
    ress = (await db.execute(ress_stmt)).scalars().all()

    forms_stmt = select(OrganizationForm).options(selectinload(OrganizationForm.versions)).where(
        OrganizationForm.organization_id == org.id
    ).order_by(OrganizationForm.id.desc())
    forms = (await db.execute(forms_stmt)).scalars().all()

    camps_stmt = select(OrganizationCampaign).options(selectinload(OrganizationCampaign.versions)).where(
        OrganizationCampaign.organization_id == org.id
    ).order_by(OrganizationCampaign.id.desc())
    camps = (await db.execute(camps_stmt)).scalars().all()

    feature_dict = {}
    if target_feat:
        feature_dict = {
            "id": target_feat.id,
            "name": target_feat.name,
            "feature_code": target_feat.feature_code,
            "description": target_feat.description,
            "parameters": target_feat.parameters or {},
            "permissions": target_feat.permissions or {},
            "status": target_feat.status,
        }
    else:
        feature_dict = {
            "id": 0,
            "name": feature_code.capitalize(),
            "feature_code": feature_code,
            "description": "",
            "parameters": {},
            "permissions": {},
            "status": "active",
        }

    return {
        "feature": feature_dict,
        "plugin": plug_info,
        "projects": [
            {
                "id": p.id,
                "name": p.name,
                "code": p.code,
                "project_type": p.project_type,
                "category": p.category,
                "status": p.status,
                "parameters": p.parameters or {},
                "settings": p.settings or {},
                "versions": [
                    {
                        "id": pv.id,
                        "version_number": pv.version_number,
                        "status": pv.status,
                        "description": pv.description,
                        "template_governance": pv.template_governance or {},
                        "parameters": pv.parameters or {},
                        "settings": pv.settings or {},
                    }
                    for pv in (p.versions or [])
                ],
            }
            for p in projs
        ],
        "resources": [
            {
                "id": r.id,
                "source_resource_id": r.source_resource_id,
                "name": r.name,
                "code": r.code,
                "category": r.category,
                "status": r.status,
                "version": r.version,
                "icon": r.icon,
                "cover_image_url": r.cover_image_url,
                "schema_fields": r.schema_fields or [],
                "metrics": r.metrics or {},
                "parameters": r.parameters or {},
                "settings": r.settings or {},
                "versions": [
                    {
                        "id": rv.id,
                        "version_number": rv.version_number,
                        "status": rv.status,
                        "description": rv.description,
                        "schema_fields": rv.schema_fields or [],
                        "metrics": rv.metrics or {},
                        "template_governance": rv.template_governance or {},
                        "parameters": rv.parameters or {},
                        "settings": rv.settings or {},
                        "publication_number": rv.publication_number,
                        "origin_version_id": rv.origin_version_id,
                        "published_at": rv.published_at.isoformat() if rv.published_at else None,
                    }
                    for rv in (r.versions or [])
                ],
            }
            for r in ress
        ],
        "forms": [
            {
                "id": f.id,
                "source_form_id": f.source_form_id,
                "name": f.name,
                "code": f.code,
                "description": f.description,
                "status": f.status,
                "version": f.version,
                "parameters": f.parameters or {},
                "settings": f.settings or {},
                "versions": [
                    {
                        "id": fv.id,
                        "version_number": fv.version_number,
                        "status": fv.status,
                        "description": fv.description,
                        "form_type": fv.form_type,
                        "template_governance": fv.template_governance or {},
                        "parameters": fv.parameters or {},
                        "settings": fv.settings or {},
                    }
                    for fv in (f.versions or [])
                ],
            }
            for f in forms
        ],
        "campaigns": [
            {
                "id": c.id,
                "source_campaign_id": c.source_campaign_id,
                "name": c.name,
                "code": c.code,
                "description": c.description,
                "status": c.status,
                "version": c.version,
                "parameters": c.parameters or {},
                "settings": c.settings or {},
                "target_forms": (c.versions[0].target_forms if c.versions else []) or [],
                "target_projects": (c.versions[0].target_projects if c.versions else []) or [],
                "versions": [
                    {
                        "id": cv.id,
                        "version_number": cv.version_number,
                        "status": cv.status,
                        "description": cv.description,
                        "start_date": cv.start_date.isoformat() if cv.start_date else None,
                        "end_date": cv.end_date.isoformat() if cv.end_date else None,
                        "budget": cv.budget,
                        "currency": cv.currency,
                        "target_forms": cv.target_forms or [],
                        "target_projects": cv.target_projects or [],
                        "template_governance": cv.template_governance or {},
                        "parameters": cv.parameters or {},
                        "settings": cv.settings or {},
                    }
                    for cv in (c.versions or [])
                ],
            }
            for c in camps
        ],
    }


# =========================================================================
# ESPACE RESSOURCES SOUVERAINES DU WORKSPACE ORGANISATION
# =========================================================================

@router.get("/organization/workspace/resources/{resource_id}")
async def get_workspace_resource_detail(
    resource_id: int,
    version_id: Optional[int] = Query(None, description="Identifiant optionnel de la version spécifique"),
    org_id: Optional[int] = Query(None, description="ID organisation"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    [Espace Organisation] Retourne le détail d'une ressource souveraine
    avec ses versions et ses règles de gouvernance scellées.
    """
    org = await _resolve_user_organization(db, current_user, org_id)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organisation introuvable."
        )

    stmt = select(OrganizationResource).options(selectinload(OrganizationResource.versions)).where(
        OrganizationResource.id == resource_id,
        OrganizationResource.organization_id == org.id
    )
    res = (await db.execute(stmt)).scalars().first()
    if not res:
        stmt_src = select(OrganizationResource).options(selectinload(OrganizationResource.versions)).where(
            OrganizationResource.source_resource_id == resource_id,
            OrganizationResource.organization_id == org.id
        )
        res = (await db.execute(stmt_src)).scalars().first()
    if not res:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ressource souveraine introuvable pour votre organisation."
        )

    feat_stmt = select(OrganizationFeature).where(
        OrganizationFeature.feature_code == "resources",
        OrganizationFeature.status == "active"
    ).order_by(OrganizationFeature.id.desc())
    feats = (await db.execute(feat_stmt)).scalars().all()
    target_feat = None
    for f in feats:
        if org.id in (f.organization_ids or []):
            target_feat = f
            break
    if not target_feat and feats:
        target_feat = feats[0]

    feature_perms = dict((target_feat.permissions if target_feat else {}) or {})
    feature_params = dict((target_feat.parameters if target_feat else {}) or {})

    # Résoudre souverainement toutes les versions de cette ressource
    v_stmt = select(OrganizationResourceVersion).where(
        OrganizationResourceVersion.organization_resource_id == res.id
    ).order_by(OrganizationResourceVersion.id.desc())
    all_versions = (await db.execute(v_stmt)).scalars().all()
    target_ver = None
    if version_id and isinstance(version_id, int):
        target_ver = next((v for v in all_versions if v.id == version_id), None)
    if not target_ver:
        target_ver = next((v for v in all_versions if v.version_number == res.version), None)
    if not target_ver and all_versions:
        target_ver = next((v for v in all_versions if v.status in ("published", "active")), all_versions[0])

    res_gov = (target_ver.template_governance if target_ver else {}) or res.parameters.get("template_governance", {})
    if res_gov:
        feature_perms.update(res_gov)
    if target_ver and target_ver.parameters:
        feature_params.update(target_ver.parameters)

    effective_schema = target_ver.schema_fields if target_ver and target_ver.schema_fields else (res.schema_fields or [])

    # Récupérer cover_image_url depuis res ou sa ressource source
    cover_image = res.cover_image_url
    if not cover_image and res.source_resource_id:
        src_r = await db.get(Resource, res.source_resource_id)
        if src_r and src_r.cover_image_url:
            cover_image = src_r.cover_image_url
            res.cover_image_url = cover_image
            await db.commit()

    return {
        "resource": {
            "id": res.id,
            "source_resource_id": res.source_resource_id,
            "organization_id": res.organization_id,
            "name": res.name,
            "code": res.code,
            "category": res.category,
            "visibility": res.visibility,
            "icon": res.icon,
            "cover_image_url": cover_image,
            "status": res.status,
            "version": res.version,
            "scope": res.scope,
            "is_shared": res.is_shared,
            "is_immutable": res.is_immutable,
            "immutable_fields": res.immutable_fields or [],
            "description": target_ver.description if target_ver else res.description,
            "schema_fields": effective_schema,
            "settings": target_ver.settings if target_ver and target_ver.settings else (res.settings or {}),
            "parameters": target_ver.parameters if target_ver and target_ver.parameters else (res.parameters or {}),
            "metrics": res.metrics or {},
            "created_at": res.created_at.isoformat() if res.created_at else None,
            "versions": [
                {
                    "id": rv.id,
                    "version_number": rv.version_number,
                    "status": rv.status,
                    "description": rv.description,
                    "changelog": rv.changelog,
                    "schema_fields": rv.schema_fields or [],
                    "metrics": rv.metrics or {},
                    "template_governance": rv.template_governance or {},
                    "settings": rv.settings or {},
                    "parameters": rv.parameters or {},
                    "publication_number": rv.publication_number,
                    "origin_version_id": rv.origin_version_id,
                    "published_at": rv.published_at.isoformat() if rv.published_at else None,
                    "created_at": rv.created_at.isoformat() if rv.created_at else None,
                }
                for rv in all_versions
            ]
        },
        "feature_permissions": feature_perms,
        "feature_parameters": feature_params,
    }


# ---------------------------------------------------------------------------
# GESTION DES VERSIONS DE RESSOURCE DANS LE WORKSPACE DE L'ORGANISATION
# ---------------------------------------------------------------------------

@router.post("/organization/workspace/resources/{resource_id}/versions/clone")
async def clone_workspace_resource_version(
    resource_id: int,
    payload: Dict[str, Any],
    org_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    [Espace Organisation] Clone une version existante pour créer une nouvelle version souveraine en brouillon.
    Clone l'intégralité du schéma, des configurations et de tous les enregistrements de données.
    """
    org = await _resolve_user_organization(db, current_user, org_id)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organisation introuvable."
        )

    stmt_res = select(OrganizationResource).options(selectinload(OrganizationResource.versions)).where(
        OrganizationResource.id == resource_id,
        OrganizationResource.organization_id == org.id
    )
    res = (await db.execute(stmt_res)).scalars().first()
    if not res:
        stmt_src = select(OrganizationResource).options(selectinload(OrganizationResource.versions)).where(
            OrganizationResource.source_resource_id == resource_id,
            OrganizationResource.organization_id == org.id
        )
        res = (await db.execute(stmt_src)).scalars().first()
    if not res:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ressource introuvable."
        )

    # Règle Métier 1 : Interdiction d'avoir plusieurs brouillons simultanés
    existing_draft = next((v for v in (res.versions or []) if v.status == "draft"), None)
    if existing_draft:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Sécurité de versionnage : Une version en brouillon existe déjà (« v{existing_draft.version_number} »). Vous devez obligatoirement publier ou supprimer ce brouillon avant de pouvoir cloner ou créer une nouvelle version."
        )

    new_v_num = str(payload.get("new_version_number") or "").strip()
    if not new_v_num:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le numéro de version est obligatoire (ex: 1.1.0)."
        )

    if any(v.version_number == new_v_num for v in (res.versions or [])):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"La version '{new_v_num}' existe déjà pour cette ressource."
        )

    # Résoudre la version source
    src_v_num = str(payload.get("source_version_number") or "").strip()
    source_ver = None
    if src_v_num:
        source_ver = next((v for v in (res.versions or []) if v.version_number == src_v_num), None)
    if not source_ver:
        source_ver = next((v for v in (res.versions or []) if v.version_number == res.version), None)
    if not source_ver and res.versions:
        source_ver = res.versions[0]

    if source_ver and source_ver.status == "draft":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Action non autorisée : Impossible de cloner la version source « v{source_ver.version_number} » car elle est encore en brouillon."
        )

    # Contrôle de gouvernance
    gov = (source_ver.template_governance if source_ver else {}) or res.parameters.get("template_governance", {})
    if gov.get("allow_creation") is False or gov.get("allow_new_versions") is False:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La création ou le clonage de versions est verrouillé par la gouvernance."
        )

    new_ver = OrganizationResourceVersion(
        organization_resource_id=res.id,
        organization_plugin_version_id=res.organization_plugin_version_id,
        source_resource_version_id=source_ver.source_resource_version_id if source_ver else None,
        organization_id=org.id,
        downloaded_by_user_id=current_user.id,
        version_number=new_v_num,
        status="draft",
        description=source_ver.description if source_ver else (res.description or ""),
        changelog=payload.get("changelog") or f"Cloné depuis la version {source_ver.version_number if source_ver else 'initiale'}",
        schema_fields=copy.deepcopy(source_ver.schema_fields or []) if source_ver else copy.deepcopy(res.schema_fields or []),
        metrics={"item_count": 0},
        template_governance=copy.deepcopy(source_ver.template_governance or {}) if source_ver else {},
        settings=copy.deepcopy(source_ver.settings or {}) if source_ver else copy.deepcopy(res.settings or {}),
        parameters=copy.deepcopy(source_ver.parameters or {}) if source_ver else copy.deepcopy(res.parameters or {}),
    )
    db.add(new_ver)
    await db.flush()

    # Cloner tous les enregistrements de la version source vers la nouvelle version
    cloned_count = 0
    if source_ver:
        src_items_stmt = select(OrganizationResourceItem).where(
            OrganizationResourceItem.organization_resource_id == res.id,
            OrganizationResourceItem.organization_resource_version_id == source_ver.id,
            OrganizationResourceItem.organization_id == org.id,
        )
        src_items = (await db.execute(src_items_stmt)).scalars().all()
        for s_it in src_items:
            cloned_it = OrganizationResourceItem(
                organization_resource_id=res.id,
                organization_resource_version_id=new_ver.id,
                organization_plugin_version_id=res.organization_plugin_version_id,
                source_resource_item_id=s_it.source_resource_item_id,
                organization_id=org.id,
                downloaded_by_user_id=current_user.id,
                data=copy.deepcopy(s_it.data or {}),
                settings=copy.deepcopy(s_it.settings or {}),
                parameters=copy.deepcopy(s_it.parameters or {}),
            )
            db.add(cloned_it)
        cloned_count = len(src_items)

    new_ver.metrics = {"item_count": cloned_count}
    await db.commit()

    return await get_workspace_resource_detail(
        resource_id=res.id,
        version_id=new_ver.id,
        org_id=org.id,
        db=db,
        current_user=current_user,
    )


@router.post("/organization/workspace/resources/{resource_id}/versions/{version_id}/publish")
async def publish_workspace_resource_version(
    resource_id: int,
    version_id: int,
    org_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    [Espace Organisation] Crée une copie publiée immuable d'une version de ressource
    (numérotée v{version} / {n}) avec cascade sur les relations.
    """
    org = await _resolve_user_organization(db, current_user, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organisation introuvable.")

    from app.services.plugin.organization_publication_service import OrganizationPublicationService
    pub_ver = await OrganizationPublicationService.publish_resource_version(
        db=db,
        resource_id=resource_id,
        version_id=version_id,
        organization_id=org.id,
        user_id=current_user.id,
    )
    await db.commit()

    return await get_workspace_resource_detail(
        resource_id=pub_ver.organization_resource_id,
        version_id=pub_ver.id,
        org_id=org.id,
        db=db,
        current_user=current_user,
    )


@router.post("/organization/workspace/forms/{form_id}/versions/{version_id}/publish")
async def publish_workspace_form_version(
    form_id: int,
    version_id: int,
    org_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    [Espace Organisation] Crée une copie publiée immuable d'une version de formulaire
    (numérotée v{version} / {n}) avec cascade sur toutes les ressources et formulaires liés.
    """
    org = await _resolve_user_organization(db, current_user, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organisation introuvable.")

    from app.services.plugin.organization_publication_service import OrganizationPublicationService
    pub_ver = await OrganizationPublicationService.publish_form_version(
        db=db,
        form_id=form_id,
        version_id=version_id,
        organization_id=org.id,
        user_id=current_user.id,
    )
    await db.commit()

    return await get_workspace_form_detail(
        form_id=pub_ver.organization_form_id,
        version_id=pub_ver.id,
        org_id=org.id,
        db=db,
        current_user=current_user,
    )


@router.post("/organization/workspace/projects/{project_id}/versions/{version_id}/publish")
async def publish_workspace_project_version(
    project_id: int,
    version_id: int,
    org_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    [Espace Organisation] Crée une copie publiée immuable d'une version de projet
    (numérotée v{version} / {n}) avec cascade sur tous les formulaires rattachés.
    """
    org = await _resolve_user_organization(db, current_user, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organisation introuvable.")

    from app.services.plugin.organization_publication_service import OrganizationPublicationService
    pub_ver = await OrganizationPublicationService.publish_project_version(
        db=db,
        project_id=project_id,
        version_id=version_id,
        organization_id=org.id,
        user_id=current_user.id,
    )
    await db.commit()

    return await get_workspace_project_detail(
        project_id=pub_ver.organization_project_id,
        org_id=org.id,
        db=db,
        current_user=current_user,
    )


@router.get("/organization/workspace/{module_type}/{module_id}/versions/{version_id}/publication-tree")
async def get_workspace_module_publication_tree(
    module_type: str,
    module_id: int,
    version_id: int,
    org_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    [Espace Organisation] Inspecte l'arbre de dépendances d'une version native
    en vue de la configuration du draft de publication interactif.
    """
    org = await _resolve_user_organization(db, current_user, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organisation introuvable.")

    from app.services.plugin.organization_publication_service import OrganizationPublicationService
    return await OrganizationPublicationService.get_publication_dependency_tree(
        db=db,
        module_type=module_type,
        module_id=module_id,
        version_id=version_id,
        organization_id=org.id,
    )


@router.post("/organization/workspace/{module_type}/{module_id}/versions/{version_id}/publish-tree")
async def publish_workspace_module_tree(
    module_type: str,
    module_id: int,
    version_id: int,
    plan: Dict[str, Any] = Body(...),
    org_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    [Espace Organisation] Exécute le plan de publication configuré sous forme d'arbre.
    Ordonne les publications de bas en haut (feuilles d'abord), réutilise les publications
    existantes spécifiées et lie certifié l'ensemble des modules.
    """
    org = await _resolve_user_organization(db, current_user, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organisation introuvable.")

    from app.services.plugin.organization_publication_service import OrganizationPublicationService
    return await OrganizationPublicationService.execute_publication_tree(
        db=db,
        module_type=module_type,
        module_id=module_id,
        version_id=version_id,
        organization_id=org.id,
        user_id=current_user.id,
        tree_plan=plan,
    )


@router.post("/organization/workspace/resources/{resource_id}/versions/{version_id}/activate")
async def activate_workspace_resource_version(
    resource_id: int,
    version_id: int,
    org_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    [Espace Organisation] Définit cette version comme la version active courante de la ressource.
    """
    org = await _resolve_user_organization(db, current_user, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organisation introuvable.")

    ver_stmt = select(OrganizationResourceVersion).options(selectinload(OrganizationResourceVersion.items)).where(
        OrganizationResourceVersion.id == version_id,
        OrganizationResourceVersion.organization_id == org.id,
    )
    ver = (await db.execute(ver_stmt)).scalars().first()
    if not ver:
        raise HTTPException(status_code=404, detail="Version introuvable.")

    res_stmt = select(OrganizationResource).where(OrganizationResource.id == ver.organization_resource_id)
    res = (await db.execute(res_stmt)).scalars().first()
    if not res:
        raise HTTPException(status_code=404, detail="Ressource introuvable.")

    res.version = ver.version_number
    res.status = "active"
    ver.status = "active"
    flag_modified(res, "version")
    flag_modified(res, "status")
    flag_modified(ver, "status")
    await db.commit()

    return await get_workspace_resource_detail(
        resource_id=res.id,
        version_id=ver.id,
        org_id=org.id,
        db=db,
        current_user=current_user,
    )


@router.put("/organization/workspace/resources/{resource_id}/versions/{version_id}")
async def update_workspace_resource_version(
    resource_id: int,
    version_id: int,
    payload: Dict[str, Any],
    org_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    [Espace Organisation] Met à jour description, changelog, paramètres ou schéma d'une version brouillon.
    """
    org = await _resolve_user_organization(db, current_user, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organisation introuvable.")

    ver_stmt = select(OrganizationResourceVersion).options(selectinload(OrganizationResourceVersion.items)).where(
        OrganizationResourceVersion.id == version_id,
        OrganizationResourceVersion.organization_id == org.id,
    )
    ver = (await db.execute(ver_stmt)).scalars().first()
    if not ver:
        raise HTTPException(status_code=404, detail="Version introuvable.")

    if ver.status in ("published", "active"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Action refusée : La version v{ver.version_number} est scellée ({ver.status}). Son schéma et ses configurations ne peuvent plus être modifiés."
        )

    gov = (ver.template_governance or {}) or (ver.parameters.get("template_governance") if ver.parameters else {})
    if "description" in payload:
        ver.description = payload["description"]
    if "changelog" in payload:
        ver.changelog = payload["changelog"]
    if "schema_fields" in payload:
        allow_schema = gov.get("allow_schema_modification", gov.get("allow_edit_schema", False))
        if allow_schema is False:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="La modification de structure (schéma) est verrouillée par la gouvernance."
            )
        mandatory_fields = gov.get("mandatory_fields", [])
        if mandatory_fields:
            new_field_names = {f.get("name") for f in payload["schema_fields"] if isinstance(f, dict)}
            missing = [mf for mf in mandatory_fields if mf not in new_field_names]
            if missing:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Les champs sanctuarisés suivants sont obligatoires dans le schéma : {', '.join(missing)}"
                )

        # Contrôle strict d'intégrité référentielle : aucun champ utilisé par une autre ressource ou formulaire ne peut être modifié/supprimé
        from app.services.plugin.module_integrity_service import ModuleIntegrityService
        await ModuleIntegrityService.validate_resource_field_can_be_modified_or_deleted(
            db=db,
            resource_id=ver.organization_resource_id,
            version_id=ver.id,
            schema_fields_new=payload["schema_fields"],
            schema_fields_old=ver.schema_fields or [],
            organization_id=org.id,
        )

        ver.schema_fields = payload["schema_fields"]
        flag_modified(ver, "schema_fields")
    if "settings" in payload:
        ver.settings = payload["settings"]
        flag_modified(ver, "settings")
    if "parameters" in payload:
        ver.parameters = payload["parameters"]
        flag_modified(ver, "parameters")
    if "template_governance" in payload:
        ver.template_governance = payload["template_governance"]
        flag_modified(ver, "template_governance")

    await db.commit()
    return await get_workspace_resource_detail(
        resource_id=ver.organization_resource_id,
        version_id=ver.id,
        org_id=org.id,
        db=db,
        current_user=current_user,
    )


@router.delete("/organization/workspace/resources/{resource_id}/versions/{version_id}")
async def delete_workspace_resource_version(
    resource_id: int,
    version_id: int,
    org_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    [Espace Organisation] Supprime une version brouillon et tous ses enregistrements associés.
    Si c'est la seule version de la ressource, supprime la ressource entière.
    Sinon, supprime uniquement la version et bascule la ressource sur une version restante.
    """
    org = await _resolve_user_organization(db, current_user, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organisation introuvable.")

    res_stmt = select(OrganizationResource).options(selectinload(OrganizationResource.versions)).where(
        OrganizationResource.id == resource_id,
        OrganizationResource.organization_id == org.id,
    )
    res_obj = (await db.execute(res_stmt)).scalars().first()
    if not res_obj:
        raise HTTPException(status_code=404, detail="Ressource introuvable.")

    ver = next((v for v in (res_obj.versions or []) if v.id == version_id), None)
    if not ver:
        raise HTTPException(status_code=404, detail="Version introuvable.")

    if ver.status in ("published", "active"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Action refusée : Impossible de supprimer la version v{ver.version_number} car elle est {ver.status} (scellée). Seuls les brouillons peuvent être supprimés pour préserver l'intégrité."
        )

    # Contrôle strict d'intégrité : vérifier qu'aucune autre ressource ou formulaire n'utilise cette version
    from app.services.plugin.module_integrity_service import ModuleIntegrityService
    await ModuleIntegrityService.validate_resource_version_can_be_deleted(
        db=db,
        resource_id=resource_id,
        version_id=version_id,
        organization_id=org.id,
    )

    # 1. Supprimer tous les items associés à cette version directement en base
    await db.execute(
        delete(OrganizationResourceItem).where(
            OrganizationResourceItem.organization_resource_version_id == ver.id,
            OrganizationResourceItem.organization_id == org.id,
        )
    )

    remaining_versions = [v for v in (res_obj.versions or []) if v.id != ver.id]

    if not remaining_versions:
        # C'était l'unique version restante : suppression complète de la ressource
        try:
            db.expunge(ver)
            db.expunge(res_obj)
        except Exception:
            pass
        await db.execute(delete(OrganizationResourceVersion).where(OrganizationResourceVersion.id == ver.id))
        await db.execute(delete(OrganizationResource).where(OrganizationResource.id == res_obj.id))
        await db.commit()
        return {
            "success": True,
            "resource_deleted": True,
            "deleted_version_id": version_id,
            "message": f"Dernière version supprimée. La ressource « {res_obj.name} » a été supprimée."
        }

    # Plusieurs versions existent : supprimer uniquement la version
    try:
        db.expunge(ver)
    except Exception:
        pass
    await db.execute(delete(OrganizationResourceVersion).where(OrganizationResourceVersion.id == ver.id))

    # Basculer la ressource sur la version restante la plus pertinente (priorité aux publiées/actives)
    best_fallback = next((v for v in remaining_versions if v.status in ("published", "active")), remaining_versions[-1])
    res_obj.version = best_fallback.version_number
    res_obj.schema_fields = best_fallback.schema_fields
    if best_fallback.settings:
        res_obj.settings = best_fallback.settings
    if hasattr(res_obj, "status"):
        res_obj.status = best_fallback.status

    # Recalculer le total d'enregistrements restants pour cette ressource
    rem_items_count_stmt = select(func.count(OrganizationResourceItem.id)).where(
        OrganizationResourceItem.organization_resource_id == res_obj.id,
        OrganizationResourceItem.organization_resource_version_id == best_fallback.id,
        OrganizationResourceItem.organization_id == org.id,
    )
    rem_items_count = (await db.execute(rem_items_count_stmt)).scalar() or 0
    cur_metrics = dict(res_obj.metrics or {})
    cur_metrics["item_count"] = rem_items_count
    res_obj.metrics = cur_metrics

    await db.commit()

    return {
        "success": True,
        "resource_deleted": False,
        "deleted_version_id": version_id,
        "active_version": best_fallback.version_number,
        "active_version_id": best_fallback.id,
        "message": f"Version v{ver.version_number} ainsi que ses données et son schéma ont été supprimés avec succès."
    }


# ---------------------------------------------------------------------------
# ITEMS DE RESSOURCE DU WORKSPACE (STRICTEMENT VERSIONNÉS)
# ---------------------------------------------------------------------------

@router.get("/organization/workspace/resources/{resource_id}/items")
async def get_workspace_resource_items(
    resource_id: int,
    org_id: Optional[int] = Query(None),
    version_id: Optional[int] = Query(None),
    q: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    [Espace Organisation] Récupère la liste des enregistrements de données dynamiques de la ressource.
    Charge UNIQUEMENT les données de la version sélectionnée (ou active par défaut).
    """
    org = await _resolve_user_organization(db, current_user, org_id)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organisation introuvable."
        )

    stmt_res = select(OrganizationResource).options(selectinload(OrganizationResource.versions)).where(
        OrganizationResource.id == resource_id,
        OrganizationResource.organization_id == org.id
    )
    res = (await db.execute(stmt_res)).scalars().first()
    if not res:
        stmt_src = select(OrganizationResource).options(selectinload(OrganizationResource.versions)).where(
            OrganizationResource.source_resource_id == resource_id,
            OrganizationResource.organization_id == org.id
        )
        res = (await db.execute(stmt_src)).scalars().first()
    if not res:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ressource introuvable."
        )

    # Déterminer strictement la version ciblée
    target_version_id = version_id if isinstance(version_id, int) else None
    if not target_version_id:
        all_v = res.versions or []
        active_ver = next((v for v in all_v if v.version_number == res.version), None)
        if not active_ver and all_v:
            active_ver = next((v for v in all_v if v.status in ("published", "active")), all_v[0])
        if active_ver:
            target_version_id = active_ver.id

    stmt_items = select(OrganizationResourceItem).where(
        OrganizationResourceItem.organization_resource_id == res.id,
        OrganizationResourceItem.organization_id == org.id
    ).order_by(OrganizationResourceItem.id.asc())

    if target_version_id and isinstance(target_version_id, int):
        stmt_items = stmt_items.where(OrganizationResourceItem.organization_resource_version_id == target_version_id)

    items = (await db.execute(stmt_items)).scalars().all()

    # Filtrage textuel si query de recherche
    search_term = q.strip().lower() if isinstance(q, str) else ""

    result = []
    for it in items:
        d = it.data or {}
        if search_term:
            match = any(search_term in str(v).lower() for v in d.values() if v is not None)
            if not match:
                continue

        result.append({
            "id": it.id,
            "organization_resource_id": it.organization_resource_id,
            "organization_resource_version_id": it.organization_resource_version_id,
            "source_resource_item_id": it.source_resource_item_id,
            "data": d,
            "settings": it.settings or {},
            "parameters": it.parameters or {},
            "created_at": it.created_at.isoformat() if it.created_at else None,
            "updated_at": it.updated_at.isoformat() if it.updated_at else None,
        })
    return result


@router.post("/organization/workspace/resources/{resource_id}/items")
async def create_workspace_resource_item(
    resource_id: int,
    payload: Dict[str, Any],
    org_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    [Espace Organisation] Crée un nouvel enregistrement dans la ressource souveraine
    en validant l'unicité strictement bornée à la version ciblée.
    """
    org = await _resolve_user_organization(db, current_user, org_id)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organisation introuvable."
        )

    stmt_res = select(OrganizationResource).options(selectinload(OrganizationResource.versions)).where(
        OrganizationResource.id == resource_id,
        OrganizationResource.organization_id == org.id
    )
    res = (await db.execute(stmt_res)).scalars().first()
    if not res:
        stmt_src = select(OrganizationResource).options(selectinload(OrganizationResource.versions)).where(
            OrganizationResource.source_resource_id == resource_id,
            OrganizationResource.organization_id == org.id
        )
        res = (await db.execute(stmt_src)).scalars().first()
    if not res:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ressource introuvable."
        )

    # Déterminer la version cible
    req_v_id = payload.get("version_id")
    target_ver = None
    if req_v_id:
        target_ver = next((v for v in (res.versions or []) if v.id == req_v_id), None)
    if not target_ver:
        target_ver = next((v for v in (res.versions or []) if v.version_number == res.version), None)
    if not target_ver and res.versions:
        target_ver = res.versions[0]

    target_v_id = target_ver.id if target_ver else None

    # Contrôle de gouvernance
    gov = (target_ver.template_governance if target_ver else {}) or res.parameters.get("template_governance", {})
    allow_creation = (
        gov.get("allow_creation") is not False
        and gov.get("allow_data_addition") is not False
        and gov.get("allow_add_records") is not False
    )
    if not allow_creation:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="L'ajout d'enregistrements est verrouillé pour cette ressource souveraine."
        )

    item_data = payload.get("data") if isinstance(payload.get("data"), dict) else payload

    # Validation d'unicité stricte pour cette version
    schema_fields = (target_ver.schema_fields if target_ver and target_ver.schema_fields else None) or (res.schema_fields or [])
    for field in schema_fields:
        fname = field.get("name")
        val = item_data.get(fname)
        if field.get("unique") and val is not None and val != "":
            uniq_stmt = select(OrganizationResourceItem.id).where(
                OrganizationResourceItem.organization_resource_id == res.id,
                func.json_extract_path_text(OrganizationResourceItem.data, fname) == str(val),
            )
            if target_v_id:
                uniq_stmt = uniq_stmt.where(OrganizationResourceItem.organization_resource_version_id == target_v_id)
            if (await db.execute(uniq_stmt)).scalars().first():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"La valeur '{val}' pour le champ '{field.get('label') or fname}' existe déjà dans cette version. Ce champ exige une valeur unique pour assurer l'authenticité des données."
                )

    new_item = OrganizationResourceItem(
        organization_resource_id=res.id,
        organization_resource_version_id=target_v_id,
        organization_plugin_version_id=res.organization_plugin_version_id,
        organization_id=org.id,
        downloaded_by_user_id=current_user.id,
        data=item_data or {},
        settings={},
        parameters={},
    )
    db.add(new_item)

    if target_ver:
        v_m = dict(target_ver.metrics or {})
        v_m["item_count"] = (v_m.get("item_count") or 0) + 1
        target_ver.metrics = v_m
        flag_modified(target_ver, "metrics")

    await db.commit()
    await db.refresh(new_item)

    return {
        "id": new_item.id,
        "organization_resource_id": new_item.organization_resource_id,
        "organization_resource_version_id": new_item.organization_resource_version_id,
        "data": new_item.data or {},
        "created_at": new_item.created_at.isoformat() if new_item.created_at else None,
    }


@router.put("/organization/workspace/resources/{resource_id}/items/{item_id}")
async def update_workspace_resource_item(
    resource_id: int,
    item_id: int,
    payload: Dict[str, Any],
    org_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    [Espace Organisation] Modifie un enregistrement existant dans la ressource souveraine.
    Valide l'unicité strictement bornée à la version de cet enregistrement en excluant l'item lui-même.
    """
    org = await _resolve_user_organization(db, current_user, org_id)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organisation introuvable."
        )

    stmt_res = select(OrganizationResource).options(selectinload(OrganizationResource.versions)).where(
        OrganizationResource.id == resource_id,
        OrganizationResource.organization_id == org.id
    )
    res = (await db.execute(stmt_res)).scalars().first()
    if not res:
        stmt_src = select(OrganizationResource).options(selectinload(OrganizationResource.versions)).where(
            OrganizationResource.source_resource_id == resource_id,
            OrganizationResource.organization_id == org.id
        )
        res = (await db.execute(stmt_src)).scalars().first()
    if not res:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ressource introuvable."
        )

    stmt_item = select(OrganizationResourceItem).where(
        OrganizationResourceItem.id == item_id,
        OrganizationResourceItem.organization_resource_id == res.id,
        OrganizationResourceItem.organization_id == org.id
    )
    item = (await db.execute(stmt_item)).scalars().first()
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Enregistrement introuvable."
        )

    # Récupérer la version de l'item
    item_ver = None
    if item.organization_resource_version_id:
        item_ver = next((v for v in (res.versions or []) if v.id == item.organization_resource_version_id), None)
    if not item_ver and res.versions:
        item_ver = res.versions[0]

    gov = (item_ver.template_governance if item_ver else {}) or res.parameters.get("template_governance", {})

    # Règle d'immuabilité absolue des publications scellées
    if item_ver and (item_ver.status == "published" or getattr(item_ver, "publication_number", None) is not None or "/" in str(item_ver.version_number)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Action refusée : La version « {item_ver.version_number} » est une publication scellée et strictement immuable. Les enregistrements ne peuvent pas être modifiés."
        )
    allow_update = (
        gov.get("allow_data_update") is not False
        and gov.get("allow_edit") is not False
        and gov.get("allow_edit_records") is not False
    )
    if not allow_update:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La modification d'enregistrements est verrouillée par la gouvernance."
        )

    is_initial_item = bool(item.source_resource_item_id)
    if gov.get("locked_rows_mode") == "all_initial" and is_initial_item:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cet enregistrement initial certifié est verrouillé et ne peut pas être modifié."
        )
    if gov.get("editable_rows_mode") == "none":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La modification d'enregistrements est désactivée par la gouvernance."
        )

    # Contrôle strict d'intégrité référentielle : aucun enregistrement utilisé par une autre ressource ou formulaire ne peut être modifié
    from app.services.plugin.module_integrity_service import ModuleIntegrityService
    await ModuleIntegrityService.validate_resource_row_can_be_modified_or_deleted(
        db=db,
        resource_id=res.id,
        item_id=item.id,
        organization_id=org.id,
        is_delete=False,
    )

    excluded_fields = set(gov.get("excluded_record_edit_fields", []))
    incoming = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    current_data = dict(item.data or {})

    for k, v in incoming.items():
        if k not in excluded_fields:
            current_data[k] = v

    # Validation d'unicité pour cette version en excluant cet enregistrement
    schema_fields = (item_ver.schema_fields if item_ver and item_ver.schema_fields else None) or (res.schema_fields or [])
    for field in schema_fields:
        fname = field.get("name")
        val = current_data.get(fname)
        if field.get("unique") and val is not None and val != "":
            uniq_stmt = select(OrganizationResourceItem.id).where(
                OrganizationResourceItem.organization_resource_id == res.id,
                OrganizationResourceItem.id != item.id,
                func.json_extract_path_text(OrganizationResourceItem.data, fname) == str(val),
            )
            if item.organization_resource_version_id:
                uniq_stmt = uniq_stmt.where(OrganizationResourceItem.organization_resource_version_id == item.organization_resource_version_id)
            if (await db.execute(uniq_stmt)).scalars().first():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"La valeur '{val}' pour le champ '{field.get('label') or fname}' existe déjà dans cette version. Ce champ exige une valeur unique pour assurer l'authenticité des données."
                )

    item.data = current_data
    flag_modified(item, "data")
    await db.commit()
    await db.refresh(item)

    return {
        "id": item.id,
        "organization_resource_id": item.organization_resource_id,
        "organization_resource_version_id": item.organization_resource_version_id,
        "data": item.data or {},
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


@router.delete("/organization/workspace/resources/{resource_id}/items/{item_id}")
async def delete_workspace_resource_item(
    resource_id: int,
    item_id: int,
    org_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    [Espace Organisation] Supprime un enregistrement dans la ressource souveraine.
    """
    org = await _resolve_user_organization(db, current_user, org_id)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organisation introuvable."
        )

    stmt_res = select(OrganizationResource).options(selectinload(OrganizationResource.versions)).where(
        OrganizationResource.id == resource_id,
        OrganizationResource.organization_id == org.id
    )
    res = (await db.execute(stmt_res)).scalars().first()
    if not res:
        stmt_src = select(OrganizationResource).options(selectinload(OrganizationResource.versions)).where(
            OrganizationResource.source_resource_id == resource_id,
            OrganizationResource.organization_id == org.id
        )
        res = (await db.execute(stmt_src)).scalars().first()
    if not res:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ressource introuvable."
        )

    stmt_item = select(OrganizationResourceItem).where(
        OrganizationResourceItem.id == item_id,
        OrganizationResourceItem.organization_resource_id == res.id,
        OrganizationResourceItem.organization_id == org.id
    )
    item = (await db.execute(stmt_item)).scalars().first()
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Enregistrement introuvable."
        )

    item_ver = None
    if item.organization_resource_version_id:
        item_ver = next((v for v in (res.versions or []) if v.id == item.organization_resource_version_id), None)

    gov = (item_ver.template_governance if item_ver else {}) or res.parameters.get("template_governance", {})

    # Règle d'immuabilité absolue des publications scellées
    if item_ver and (item_ver.status == "published" or getattr(item_ver, "publication_number", None) is not None or "/" in str(item_ver.version_number)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Action refusée : La version « {item_ver.version_number} » est une publication scellée et strictement immuable. Les enregistrements ne peuvent pas être supprimés."
        )
    allow_deletion = (
        gov.get("allow_data_deletion") is True
        or gov.get("allow_delete") is True
        or gov.get("allow_delete_records") is True
        or gov.get("allow_resource_deletion") is True
    ) and (
        gov.get("allow_data_deletion") is not False
        and gov.get("allow_delete_records") is not False
    )
    if not allow_deletion:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La suppression d'enregistrements est interdite par la gouvernance."
        )

    is_initial_item = bool(item.source_resource_item_id)
    if gov.get("locked_rows_mode") == "all_initial" and is_initial_item:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cet enregistrement initial certifié est verrouillé et ne peut pas être supprimé."
        )
    if gov.get("deletable_rows_mode") == "none":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La suppression d'enregistrements est désactivée par la gouvernance."
        )

    # Contrôle strict d'intégrité référentielle : aucun enregistrement utilisé par une autre ressource ou formulaire ne peut être supprimé
    from app.services.plugin.module_integrity_service import ModuleIntegrityService
    await ModuleIntegrityService.validate_resource_row_can_be_modified_or_deleted(
        db=db,
        resource_id=res.id,
        item_id=item.id,
        organization_id=org.id,
        is_delete=True,
    )

    if item_ver:
        v_m = dict(item_ver.metrics or {})
        v_m["item_count"] = max(0, (v_m.get("item_count") or 1) - 1)
        item_ver.metrics = v_m
        flag_modified(item_ver, "metrics")

    await db.delete(item)
    await db.commit()

    return {"success": True, "deleted_id": item_id}


# ---------------------------------------------------------------------------
# OPTIONS DE LIAISONS RELATIONS (STRICTEMENT ENTRE VERSIONS)
# ---------------------------------------------------------------------------

@router.get("/organization/workspace/resources/{resource_id}/relations")
async def get_workspace_resource_relations(
    resource_id: int,
    target_version_id: Optional[int] = Query(None, description="Version ciblée pour la relation"),
    org_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    [Espace Organisation] Résout les options de relations pour les champs de liaison
    (clés étrangères vers d'autres ressources de l'organisation).
    Garantit que les enregistrements proposés appartiennent strictement à la version active ou ciblée.
    """
    org = await _resolve_user_organization(db, current_user, org_id)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organisation introuvable."
        )

    all_res_stmt = select(OrganizationResource).options(selectinload(OrganizationResource.versions)).where(
        OrganizationResource.organization_id == org.id
    )
    all_ress = (await db.execute(all_res_stmt)).scalars().all()

    relations_map: Dict[str, Any] = {}
    for r in all_ress:
        r_versions = r.versions or []
        # Trouver toutes les versions publiées ou actives scellées de cette ressource
        published_vers = [v for v in r_versions if v.status in ("published", "active")]

        # Si aucune version n'est explicitement enregistrée comme publiée
        if not published_vers:
            continue

        for v in published_vers:
            items_stmt = select(OrganizationResourceItem).where(
                OrganizationResourceItem.organization_resource_id == r.id,
                OrganizationResourceItem.organization_id == org.id,
                OrganizationResourceItem.organization_resource_version_id == v.id
            )
            r_items = (await db.execute(items_stmt)).scalars().all()
            options = []
            for it in r_items:
                d = it.data or {}
                label = d.get("titre") or d.get("name") or d.get("intitule_court") or d.get("code") or d.get("numero") or f"#{it.id}"
                if d.get("numero") and d.get("titre"):
                    label = f"{d.get('numero')} - {d.get('titre')}"
                elif d.get("code") and d.get("titre"):
                    label = f"{d.get('code')}: {d.get('titre')}"

                options.append({
                    "value": it.id,
                    "label": label,
                    "item_id": it.id,
                    "source_item_id": it.source_resource_item_id,
                    "resource_id": r.id,
                    "resource_code": r.code,
                    "resource_name": r.name,
                    "version_id": v.id,
                    "version_number": v.version_number,
                    "data": d,
                })

            # Clés spécifiques à la version scellée
            relations_map[f"{r.code}_{v.version_number}"] = options
            relations_map[f"{r.id}_{v.id}"] = options
            if r.source_resource_id:
                relations_map[f"{r.source_resource_id}_{v.id}"] = options
                relations_map[f"{r.source_resource_id}_{v.version_number}"] = options

            # Clés de repli globales (priorité à la version active de la ressource)
            if v.version_number == r.version or str(r.id) not in relations_map:
                relations_map[r.code] = options
                relations_map[str(r.id)] = options
                if r.source_resource_id:
                    relations_map[str(r.source_resource_id)] = options

    return relations_map


# =========================================================================
# ESPACE FORMULAIRES SOUVERAINS DU WORKSPACE ORGANISATION
# =========================================================================

@router.get("/organization/workspace/forms")
async def get_workspace_forms(
    org_id: Optional[int] = Query(None, description="ID organisation"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    [Espace Organisation] Retourne la liste des formulaires souverains de l'organisation.
    """
    org = await _resolve_user_organization(db, current_user, org_id)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organisation introuvable."
        )

    stmt = select(OrganizationForm).options(
        selectinload(OrganizationForm.versions)
    ).where(OrganizationForm.organization_id == org.id).order_by(OrganizationForm.id.desc())
    forms = (await db.execute(stmt)).scalars().all()

    return [
        {
            "id": f.id,
            "source_form_id": f.source_form_id,
            "organization_id": f.organization_id,
            "name": f.name,
            "code": f.code,
            "description": f.description,
            "visibility": f.visibility,
            "version": f.version,
            "status": f.status,
            "settings": f.settings or {},
            "parameters": f.parameters or {},
            "created_at": f.created_at.isoformat() if f.created_at else None,
            "versions": [
                {
                    "id": v.id,
                    "version_number": v.version_number,
                    "status": v.status,
                    "description": v.description,
                    "form_type": v.form_type,
                    "template_governance": v.template_governance or {},
                    "published_at": v.published_at.isoformat() if v.published_at else None,
                    "settings": v.settings or {},
                    "parameters": v.parameters or {},
                }
                for v in (f.versions or [])
            ]
        }
        for f in forms
    ]


@router.get("/organization/workspace/forms/{form_id}")
async def get_workspace_form_detail(
    form_id: int,
    org_id: Optional[int] = Query(None, description="ID organisation"),
    version_id: Optional[int] = Query(None, description="ID de version spécifique"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    [Espace Organisation] Retourne le détail complet d'un formulaire souverain
    avec son arborescence de sections et questions, et les règles de gouvernance scellées.
    """
    org = await _resolve_user_organization(db, current_user, org_id)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organisation introuvable."
        )

    stmt = select(OrganizationForm).options(
        selectinload(OrganizationForm.versions)
        .selectinload(OrganizationFormVersion.sections),
        selectinload(OrganizationForm.versions)
        .selectinload(OrganizationFormVersion.questions)
    ).where(
        (OrganizationForm.id == form_id) | (OrganizationForm.source_form_id == form_id),
        OrganizationForm.organization_id == org.id
    )
    frm = (await db.execute(stmt)).scalars().first()
    if not frm:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Formulaire souverain introuvable pour votre organisation."
        )

    # Récupérer la version ciblée
    target_ver = None
    if version_id:
        for v in (frm.versions or []):
            if v.id == version_id:
                target_ver = v
                break
    if not target_ver and frm.versions:
        target_ver = frm.versions[0]

    # Récupérer la feature forms ou audits pour les permissions et paramètres transverses
    feat_stmt = select(OrganizationFeature).where(
        OrganizationFeature.feature_code.in_(["forms", "audits"]),
        OrganizationFeature.status == "active"
    ).order_by(OrganizationFeature.id.desc())
    feats = (await db.execute(feat_stmt)).scalars().all()
    target_feat = None
    for f in feats:
        if org.id in (f.organization_ids or []):
            target_feat = f
            break
    if not target_feat and feats:
        target_feat = feats[0]

    feature_perms = dict((target_feat.permissions if target_feat else {}) or {})
    feature_params = dict((target_feat.parameters if target_feat else {}) or {})

    # Intégrer la gouvernance de la version du formulaire (priorité à la gouvernance scellée de la version)
    ver_gov = (target_ver.template_governance if target_ver else {}) or frm.parameters.get("template_governance", {})
    if ver_gov:
        feature_perms.update(ver_gov)
    if target_ver and target_ver.parameters:
        feature_params.update(target_ver.parameters)

    # Construire l'arborescence des sections et questions de la version
    all_sections = target_ver.sections if target_ver else []
    all_questions = target_ver.questions if target_ver else []

    # Map des questions par section
    questions_by_section: Dict[int, List[Dict[str, Any]]] = {}
    root_questions: List[Dict[str, Any]] = []

    for q in sorted(all_questions, key=lambda x: x.order_index):
        q_dict = {
            "id": q.id,
            "source_form_question_id": q.source_form_question_id,
            "code": q.code,
            "label": q.label,
            "help_text": q.help_text,
            "placeholder": q.placeholder,
            "question_type": q.question_type,
            "order_index": q.order_index,
            "is_required": q.is_required,
            "is_readonly": q.is_readonly,
            "is_sanctuarized": q.is_sanctuarized,
            "is_critical": q.is_critical,
            "weight": float(q.weight) if q.weight is not None else 1.0,
            "scoring_config": q.scoring_config or {},
            "options": q.options or [],
            "resource_binding": q.resource_binding or {},
            "formula_expression": q.formula_expression,
            "validation_rules": q.validation_rules or {},
            "dependency_rules": q.dependency_rules or {},
            "settings": q.settings or {},
            "parameters": q.parameters or {},
            "section_id": q.organization_form_section_id,
        }
        if q.organization_form_section_id:
            questions_by_section.setdefault(q.organization_form_section_id, []).append(q_dict)
        else:
            root_questions.append(q_dict)

    # Construction récursive de l'arborescence des sections
    def build_section_node(sec: OrganizationFormSection) -> Dict[str, Any]:
        node = {
            "id": sec.id,
            "source_form_section_id": sec.source_form_section_id,
            "title": sec.title,
            "code": sec.code,
            "description": sec.description,
            "order_index": sec.order_index,
            "is_repeatable": sec.is_repeatable,
            "min_repeats": sec.min_repeats,
            "max_repeats": sec.max_repeats,
            "is_sanctuarized": sec.is_sanctuarized,
            "allow_custom_questions": sec.allow_custom_questions,
            "weight": float(sec.weight) if sec.weight is not None else 1.0,
            "scoring_category": sec.scoring_category,
            "is_critical": sec.is_critical,
            "min_passing_score": float(sec.min_passing_score) if sec.min_passing_score is not None else None,
            "dependency_rules": sec.dependency_rules or {},
            "settings": sec.settings or {},
            "parameters": sec.parameters or {},
            "parent_section_id": sec.parent_section_id,
            "questions": questions_by_section.get(sec.id, []),
            "subsections": []
        }
        child_secs = [s for s in all_sections if s.parent_section_id == sec.id]
        child_secs.sort(key=lambda s: s.order_index)
        for child in child_secs:
            node["subsections"].append(build_section_node(child))
        return node

    root_secs = [s for s in all_sections if s.parent_section_id is None]
    root_secs.sort(key=lambda s: s.order_index)
    tree_sections = [build_section_node(s) for s in root_secs]

    # Construire les settings enrichis avec la gouvernance scellée
    merged_settings = dict(frm.settings or {})
    if target_ver and target_ver.settings:
        merged_settings.update(target_ver.settings)
    if feature_params:
        for k, v in feature_params.items():
            if k not in merged_settings or not merged_settings[k]:
                merged_settings[k] = v

    # Règles de gouvernance scellées
    merged_settings["governance"] = {
        "allow_tenant_add_metadata": ver_gov.get("allow_add_metadata", feature_perms.get("allow_add_metadata", False)),
        "allow_tenant_edit_metadata": ver_gov.get("allow_edit_metadata", feature_perms.get("allow_edit_metadata", False)),
        "allow_tenant_delete_metadata": ver_gov.get("allow_delete_metadata", feature_perms.get("allow_delete_metadata", False)),
        "allow_custom_sections": ver_gov.get("allow_custom_sections", feature_perms.get("allow_custom_sections", False)),
        "allow_tenant_edit_sections": ver_gov.get("allow_edit_sections", feature_perms.get("allow_edit_sections", False)),
        "allow_tenant_delete_sections": ver_gov.get("allow_delete_sections", feature_perms.get("allow_delete_sections", False)),
        "allow_custom_questions": ver_gov.get("allow_custom_questions", feature_perms.get("allow_custom_questions", False)),
        "allow_tenant_edit_questions": ver_gov.get("allow_edit_questions", feature_perms.get("allow_edit_questions", False)),
        "allow_tenant_delete_questions": ver_gov.get("allow_delete_questions", feature_perms.get("allow_delete_questions", False)),
        "allow_export": ver_gov.get("allow_export", feature_perms.get("allow_export", True)),
    }
    if ver_gov.get("allow_edit") is False or feature_perms.get("allow_edit") is False:
        for gkey in merged_settings["governance"]:
            if gkey != "allow_export":
                merged_settings["governance"][gkey] = False

    merged_settings["param_locks"] = {
        "name": True,
        "code": True,
        "version": True,
        "header": True,
        "watermark": True,
        "gps": True,
        "agent_monitoring": True,
    }

    # S'assurer que header et watermark sont présents
    if "header" not in merged_settings and "header" in feature_params:
        merged_settings["header"] = feature_params["header"]
    if "watermark" not in merged_settings and "watermark" in feature_params:
        merged_settings["watermark"] = feature_params["watermark"]
    if "agent_monitoring" not in merged_settings and "agent_monitoring" in feature_params:
        merged_settings["agent_monitoring"] = feature_params["agent_monitoring"]

    target_status = target_ver.status if target_ver else frm.status
    target_is_pub = bool(
        target_ver
        and (
            target_ver.status == "published"
            or getattr(target_ver, "publication_number", None) is not None
            or "/" in str(target_ver.version_number)
        )
    )

    return {
        "form": {
            "id": frm.id,
            "source_form_id": frm.source_form_id,
            "organization_id": frm.organization_id,
            "name": frm.name,
            "code": frm.code,
            "description": frm.description,
            "visibility": frm.visibility,
            "version": target_ver.version_number if target_ver else frm.version,
            "status": target_status,
            "is_published": target_is_pub,
            "scoring_enabled": merged_settings.get("scoring_enabled", True),
            "scoring_scale": merged_settings.get("scoring_scale", "percentage_100"),
            "passing_score_percentage": merged_settings.get("passing_score_percentage", 80),
            "critical_fail_threshold": merged_settings.get("critical_fail_threshold", 1),
            "offline_capable": merged_settings.get("offline_capable", True),
            "is_mandatory": merged_settings.get("is_mandatory", True),
            "is_repeatable": merged_settings.get("is_repeatable", False),
            "allow_draft_save": merged_settings.get("allow_draft_save", True),
            "allow_custom_sections": merged_settings["governance"]["allow_custom_sections"],
            "allow_custom_questions": merged_settings["governance"]["allow_custom_questions"],
            "settings": merged_settings,
            "parameters": frm.parameters or {},
            "created_at": frm.created_at.isoformat() if frm.created_at else None,
            "versions": [
                {
                    "id": v.id,
                    "version_number": v.version_number,
                    "status": v.status,
                    "publication_number": getattr(v, "publication_number", None),
                    "is_published": bool(
                        v.status == "published"
                        or getattr(v, "publication_number", None) is not None
                        or "/" in str(v.version_number)
                    ),
                    "description": v.description,
                    "form_type": v.form_type,
                    "template_governance": v.template_governance or {},
                    "published_at": v.published_at.isoformat() if v.published_at else (v.created_at.isoformat() if v.created_at else None),
                    "settings": v.settings or {},
                    "parameters": v.parameters or {},
                }
                for v in (frm.versions or [])
            ],
            "sections": tree_sections,
            "questions": root_questions,
            "current_version": {
                "id": target_ver.id if target_ver else None,
                "version_number": target_ver.version_number if target_ver else frm.version,
                "status": target_status,
                "publication_number": getattr(target_ver, "publication_number", None) if target_ver else None,
                "is_published": target_is_pub,
                "description": target_ver.description if target_ver else None,
                "form_type": target_ver.form_type if target_ver else "standard",
                "template_governance": target_ver.template_governance if target_ver else {},
                "settings": target_ver.settings if target_ver else {},
                "parameters": target_ver.parameters if target_ver else {},
            } if target_ver else None,
        },
        "feature_permissions": feature_perms,
        "feature_parameters": feature_params,
        "organization": {
            "id": org.id,
            "name": org.name,
            "code": getattr(org, "code", None),
            "logo_url": org.logo_url,
        }
    }


# =========================================================================
# GESTION DES PROJETS SOUVERAINS DE L'ESPACE ORGANISATION
# =========================================================================

@router.get("/organization/workspace/projects")
async def get_workspace_projects(
    org_id: Optional[int] = Query(None, description="ID organisation pour Super Admin"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    [Espace Organisation] Récupère la liste souveraine des projets de l'organisation
    (OrganizationProject) avec leurs versions et métriques.
    """
    org = await _resolve_user_organization(db, current_user, org_id)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organisation introuvable."
        )

    stmt = select(OrganizationProject).options(
        selectinload(OrganizationProject.versions)
    ).where(
        OrganizationProject.organization_id == org.id
    ).order_by(OrganizationProject.id.desc())

    projects = (await db.execute(stmt)).scalars().all()
    results = []
    for p in projects:
        vers = p.versions or []
        latest_ver = vers[0] if vers else None
        attached_fvs = (latest_ver.settings or {}).get("attached_form_versions", []) if latest_ver else []
        results.append({
            "id": p.id,
            "source_project_id": p.source_project_id,
            "name": p.name,
            "code": p.code,
            "project_type": p.project_type,
            "category": p.category,
            "visibility": p.visibility,
            "status": p.status,
            "cover_image_url": p.cover_image_url,
            "is_template": p.project_type == "template" or (p.settings or {}).get("is_template", False),
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "updated_at": p.updated_at.isoformat() if p.updated_at else None,
            "forms_count": len(attached_fvs),
            "versions": [
                {
                    "id": v.id,
                    "version_number": v.version_number,
                    "status": v.status,
                    "description": v.description,
                    "template_governance": v.template_governance or {},
                    "metrics": v.metrics or {},
                }
                for v in vers
            ],
            "current_version": {
                "id": latest_ver.id if latest_ver else None,
                "version_number": latest_ver.version_number if latest_ver else "1.0.0",
                "status": latest_ver.status if latest_ver else "published",
                "description": latest_ver.description if latest_ver else None,
                "template_governance": latest_ver.template_governance if latest_ver else {},
            } if latest_ver else None,
        })
    return results


@router.get("/organization/workspace/projects/{project_id}")
async def get_workspace_project_detail(
    project_id: int,
    org_id: Optional[int] = Query(None, description="ID organisation pour Super Admin"),
    version_id: Optional[int] = Query(None, description="ID de version spécifique"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    [Espace Organisation] Récupère le détail souverain d'un projet de l'organisation
    (OrganizationProject) avec sa gouvernance scellée et ses paramètres.
    """
    org = await _resolve_user_organization(db, current_user, org_id)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organisation introuvable."
        )

    stmt = select(OrganizationProject).options(
        selectinload(OrganizationProject.versions)
    ).where(
        (OrganizationProject.id == project_id) | (OrganizationProject.source_project_id == project_id),
        OrganizationProject.organization_id == org.id
    )
    proj = (await db.execute(stmt)).scalars().first()
    if not proj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Projet souverain introuvable pour votre organisation."
        )

    vers = proj.versions or []
    target_ver: Optional[OrganizationProjectVersion] = None
    if version_id:
        for v in vers:
            if v.id == version_id:
                target_ver = v
                break
    if not target_ver and vers:
        target_ver = next((v for v in vers if v.status in ("published", "active")), vers[0])

    # Permissions et paramètres transverses de la feature projects
    feat_stmt = select(OrganizationFeature).where(
        OrganizationFeature.feature_code.in_(["projects", "project"]),
        OrganizationFeature.status == "active"
    ).order_by(OrganizationFeature.id.desc())
    feats = (await db.execute(feat_stmt)).scalars().all()
    target_feat = None
    for f in feats:
        if org.id in (f.organization_ids or []):
            target_feat = f
            break
    if not target_feat and feats:
        target_feat = feats[0]

    feature_perms = dict((target_feat.permissions if target_feat else {}) or {})
    feature_params = dict((target_feat.parameters if target_feat else {}) or {})

    # Gouvernance scellée de la version
    ver_gov = dict(
        (target_ver.template_governance if target_ver else {}) or
        (target_ver.settings.get("template_governance") if target_ver and target_ver.settings else {}) or
        (proj.parameters.get("template_governance", {}))
    )
    if not ver_gov:
        ver_gov = {
            "allow_add_version": True,
            "allow_edit": True,
            "allow_delete_version": True,
            "allow_delete_project": True,
            "allow_export": True,
            "allow_attach_form": True,
            "allow_detach_form": True,
        }

    feature_perms.update(ver_gov)
    if target_ver and target_ver.parameters:
        feature_params.update(target_ver.parameters)

    attached_fvs = (target_ver.settings or {}).get("attached_form_versions", []) if target_ver else []

    formatted_project = {
        "id": proj.id,
        "source_project_id": proj.source_project_id,
        "organization_id": proj.organization_id,
        "name": proj.name,
        "code": proj.code,
        "description": (target_ver.description if target_ver and target_ver.description else None) or (proj.settings or {}).get("description") or "",
        "project_type": proj.project_type,
        "category": proj.category,
        "visibility": proj.visibility,
        "status": proj.status,
        "cover_image_url": proj.cover_image_url,
        "is_template": proj.project_type == "template" or (proj.settings or {}).get("is_template", False),
        "plugin_code": getattr(target_feat, "plugin_code", None) or "WORKSPACE",
        "created_at": proj.created_at.isoformat() if proj.created_at else None,
        "updated_at": proj.updated_at.isoformat() if proj.updated_at else None,
        "template_governance": ver_gov,
        "settings": target_ver.settings if target_ver else proj.settings,
        "parameters": target_ver.parameters if target_ver else proj.parameters,
        "forms_count": len(attached_fvs),
        "versions": [
            {
                "id": v.id,
                "version_number": v.version_number,
                "status": v.status,
                "publication_number": getattr(v, "publication_number", None),
                "is_published": bool(
                    v.status == "published"
                    or getattr(v, "publication_number", None) is not None
                    or "/" in str(v.version_number)
                ),
                "description": v.description,
                "template_governance": v.template_governance or {},
                "settings": v.settings or {},
                "parameters": v.parameters or {},
                "metrics": v.metrics or {},
                "forms_count": len((v.settings or {}).get("attached_form_versions", [])),
                "created_at": v.created_at.isoformat() if v.created_at else None,
            }
            for v in vers
        ],
        "current_version": {
            "id": target_ver.id if target_ver else None,
            "version_number": target_ver.version_number if target_ver else "1.0.0",
            "status": target_ver.status if target_ver else "draft",
            "publication_number": getattr(target_ver, "publication_number", None) if target_ver else None,
            "is_published": bool(
                target_ver
                and (
                    target_ver.status == "published"
                    or getattr(target_ver, "publication_number", None) is not None
                    or "/" in str(target_ver.version_number)
                )
            ),
            "description": target_ver.description if target_ver else None,
            "template_governance": ver_gov,
            "settings": target_ver.settings if target_ver else {},
            "parameters": target_ver.parameters if target_ver else {},
            "metrics": target_ver.metrics if target_ver else {},
        } if target_ver else None,
    }

    return {
        "project": formatted_project,
        "feature_permissions": feature_perms,
        "feature_parameters": feature_params,
        "organization": {
            "id": org.id,
            "name": org.name,
            "code": getattr(org, "code", None),
            "logo_url": org.logo_url,
        }
    }


@router.get("/organization/workspace/projects/{project_id}/forms")
async def get_workspace_project_forms(
    project_id: int,
    project_version_id: Optional[int] = Query(None, description="ID de version du projet"),
    org_id: Optional[int] = Query(None, description="ID organisation pour Super Admin"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    [Espace Organisation] Récupère la liste souveraine des formulaires rattachés à la version
    du projet souverain pour l'organisation.
    """
    org = await _resolve_user_organization(db, current_user, org_id)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organisation introuvable."
        )

    stmt = select(OrganizationProject).options(
        selectinload(OrganizationProject.versions)
    ).where(
        (OrganizationProject.id == project_id) | (OrganizationProject.source_project_id == project_id),
        OrganizationProject.organization_id == org.id
    )
    proj = (await db.execute(stmt)).scalars().first()
    if not proj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Projet introuvable pour votre organisation."
        )

    vers = proj.versions or []
    target_ver: Optional[OrganizationProjectVersion] = None
    if project_version_id:
        for v in vers:
            if v.id == project_version_id:
                target_ver = v
                break
    if not target_ver and vers:
        target_ver = next((v for v in vers if v.status in ("published", "active")), vers[0])

    if not target_ver:
        return []

    ver_settings = target_ver.settings or {}
    attached_fvs = list(ver_settings.get("attached_form_versions") or [])
    attached_ids = list(ver_settings.get("attached_form_ids") or [])

    # Map pour retrouver les infos de version attachée
    att_map = {}
    for af in attached_fvs:
        fid = af.get("form_id")
        if fid:
            att_map[int(fid)] = af

    all_target_fids = set([int(af["form_id"]) for af in attached_fvs if af.get("form_id")])
    for fid in attached_ids:
        if str(fid).isdigit():
            all_target_fids.add(int(fid))

    if not all_target_fids:
        return []

    # Charger les OrganizationForms correspondants
    forms_stmt = select(OrganizationForm).options(
        selectinload(OrganizationForm.versions)
    ).where(
        OrganizationForm.organization_id == org.id,
        (OrganizationForm.id.in_(list(all_target_fids))) | (OrganizationForm.source_form_id.in_(list(all_target_fids)))
    )
    loaded_forms = (await db.execute(forms_stmt)).scalars().all()

    results = []
    for f in loaded_forms:
        f_vers = f.versions or []
        f_ver = f_vers[0] if f_vers else None
        att_info = att_map.get(f.id) or att_map.get(f.source_form_id) or {}
        v_num = att_info.get("version_number") or (f_ver.version_number if f_ver else f.version)
        v_status = att_info.get("status") or (f_ver.status if f_ver else "published")

        s = dict(f.settings or {})
        if f_ver and f_ver.settings:
            s.update(f_ver.settings)

        results.append({
            "id": f.id,
            "source_form_id": f.source_form_id,
            "code": f.code,
            "name": f.name,
            "description": f.description or "",
            "visibility": f.visibility,
            "version": v_num,
            "status": "published",
            "is_published": True,
            "scoring_enabled": s.get("scoring_enabled", True),
            "scoring_scale": s.get("scoring_scale", "percentage_100"),
            "passing_score_percentage": s.get("passing_score_percentage", 80),
            "offline_capable": s.get("offline_capable", True),
            "is_repeatable": s.get("is_repeatable", False),
            "settings": s,
            "attached_version": {
                "version_number": v_num,
                "status": v_status,
                "attached_at": att_info.get("attached_at"),
            },
            "created_at": f.created_at.isoformat() if f.created_at else None,
        })

    return results


@router.get("/organization/workspace/projects/{project_id}/campaigns")
async def get_workspace_project_campaigns(
    project_id: int,
    org_id: Optional[int] = Query(None, description="ID organisation pour Super Admin"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    [Espace Organisation] Récupère la liste des campagnes souveraines de l'organisation
    associées à ce projet.
    """
    org = await _resolve_user_organization(db, current_user, org_id)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organisation introuvable."
        )

    # Récupérer les campagnes de l'organisation
    camp_stmt = select(OrganizationCampaign).options(
        selectinload(OrganizationCampaign.versions)
    ).where(
        OrganizationCampaign.organization_id == org.id
    ).order_by(OrganizationCampaign.id.desc())
    camps = (await db.execute(camp_stmt)).scalars().all()

    associated = []
    for c in camps:
        is_linked = False
        target_version_id = None
        for v in (c.versions or []):
            t_projs = (v.settings or {}).get("target_projects", [])
            for tp in t_projs:
                tp_id = tp.get("project_id") if isinstance(tp, dict) else tp
                if tp_id in (project_id,):
                    is_linked = True
                    target_version_id = v.id
                    break
            if is_linked:
                break

        # Vérifier aussi dans les settings globaux de la campagne
        if not is_linked:
            g_projs = (c.settings or {}).get("target_projects", [])
            for tp in g_projs:
                tp_id = tp.get("project_id") if isinstance(tp, dict) else tp
                if tp_id in (project_id,):
                    is_linked = True
                    break

        if is_linked:
            active_ver = (c.versions or [])[0] if c.versions else None
            associated.append({
                "id": c.id,
                "source_campaign_id": c.source_campaign_id,
                "name": c.name,
                "code": c.code,
                "description": c.description or "",
                "status": c.status,
                "start_date": (c.settings or {}).get("start_date") or (active_ver.settings or {}).get("start_date") if active_ver else None,
                "end_date": (c.settings or {}).get("end_date") or (active_ver.settings or {}).get("end_date") if active_ver else None,
                "versions": [
                    {
                        "id": v.id,
                        "version_number": v.version_number,
                        "status": v.status,
                    }
                    for v in (c.versions or [])
                ],
                "active_version": {
                    "id": target_version_id or (active_ver.id if active_ver else None),
                    "version_number": active_ver.version_number if active_ver else "1.0.0",
                } if active_ver else None,
            })

    return associated


@router.post("/organization/workspace/projects/{project_id}/forms/attach")
async def attach_workspace_form_to_project(
    project_id: int,
    payload: Dict[str, Any] = Body(...),
    org_id: Optional[int] = Query(None, description="ID organisation pour Super Admin"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    [Espace Organisation] Associe un formulaire souverain publié à une version de projet souverain.
    Respecte scrupuleusement la règle de gouvernance allow_attach_form.
    """
    org = await _resolve_user_organization(db, current_user, org_id)
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organisation introuvable.")

    form_id = payload.get("form_id")
    form_version_id = payload.get("form_version_id")
    project_version_id = payload.get("project_version_id")

    if not form_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Le form_id est requis.")

    # Récupérer le projet
    stmt = select(OrganizationProject).options(
        selectinload(OrganizationProject.versions)
    ).where(
        (OrganizationProject.id == project_id) | (OrganizationProject.source_project_id == project_id),
        OrganizationProject.organization_id == org.id
    )
    proj = (await db.execute(stmt)).scalars().first()
    if not proj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Projet introuvable.")

    vers = proj.versions or []
    target_ver: Optional[OrganizationProjectVersion] = None
    if project_version_id:
        target_ver = next((v for v in vers if v.id == project_version_id), None)
    if not target_ver and vers:
        target_ver = next((v for v in vers if v.status in ("published", "active")), vers[0])

    if not target_ver:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Aucune version de projet disponible.")

    # RÈGLE MÉTIER ABSOLUE : Interdiction formelle d'associer un formulaire à une version publiée
    if target_ver.status in ("published", "active"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Action interdite : La version v{target_ver.version_number} de ce projet est déjà publiée et scellée. Impossible d'associer un formulaire à une version publiée. Créez un nouveau brouillon pour effectuer des modifications."
        )

    # Vérification gouvernance
    gov = dict((target_ver.template_governance or {}) or (target_ver.settings or {}).get("template_governance", {}))
    if gov.get("allow_attach_form") is False:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Action interdite : la gouvernance scellée de ce projet verrouille l'association de formulaires."
        )

    # Récupérer le formulaire souverain
    f_stmt = select(OrganizationForm).options(
        selectinload(OrganizationForm.versions)
    ).where(
        OrganizationForm.id == form_id,
        OrganizationForm.organization_id == org.id
    )
    frm = (await db.execute(f_stmt)).scalars().first()
    if not frm:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Formulaire #{form_id} introuvable.")

    f_vers = frm.versions or []
    target_f_ver = None
    if form_version_id:
        target_f_ver = next((v for v in f_vers if v.id == form_version_id), None)
    if not target_f_ver and f_vers:
        target_f_ver = next((v for v in f_vers if v.status in ("published", "active")), f_vers[0])

    v_num = target_f_ver.version_number if target_f_ver else frm.version
    v_status = target_f_ver.status if target_f_ver else "published"

    from datetime import datetime, timezone
    new_entry = {
        "form_id": frm.id,
        "form_version_id": target_f_ver.id if target_f_ver else 1,
        "form_code": frm.code,
        "form_name": frm.name,
        "version_number": v_num,
        "status": v_status,
        "attached_at": datetime.now(timezone.utc).isoformat(),
    }

    v_settings = dict(target_ver.settings or {})
    attached_fvs = list(v_settings.get("attached_form_versions") or [])
    attached_ids = set(v_settings.get("attached_form_ids") or [])

    # Remplacer si existant ou ajouter
    existing_idx = next((i for i, x in enumerate(attached_fvs) if x.get("form_id") == frm.id), None)
    if existing_idx is not None:
        attached_fvs[existing_idx] = new_entry
    else:
        attached_fvs.append(new_entry)
    attached_ids.add(frm.id)

    v_settings["attached_form_versions"] = attached_fvs
    v_settings["attached_form_ids"] = list(attached_ids)
    target_ver.settings = v_settings
    target_ver.parameters = v_settings
    flag_modified(target_ver, "settings")
    flag_modified(target_ver, "parameters")

    await db.commit()
    return {"success": True, "message": f"Formulaire « {frm.name} » associé au projet.", "entry": new_entry}


@router.post("/organization/workspace/projects/{project_id}/forms/detach")
async def detach_workspace_form_from_project(
    project_id: int,
    payload: Dict[str, Any] = Body(...),
    org_id: Optional[int] = Query(None, description="ID organisation pour Super Admin"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    [Espace Organisation] Dissocie un formulaire souverain d'une version de projet souverain.
    Respecte la règle de gouvernance allow_detach_form.
    """
    org = await _resolve_user_organization(db, current_user, org_id)
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organisation introuvable.")

    form_id = payload.get("form_id")
    project_version_id = payload.get("project_version_id")

    if not form_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Le form_id est requis.")

    stmt = select(OrganizationProject).options(
        selectinload(OrganizationProject.versions)
    ).where(
        (OrganizationProject.id == project_id) | (OrganizationProject.source_project_id == project_id),
        OrganizationProject.organization_id == org.id
    )
    proj = (await db.execute(stmt)).scalars().first()
    if not proj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Projet introuvable.")

    vers = proj.versions or []
    target_ver: Optional[OrganizationProjectVersion] = None
    if project_version_id:
        target_ver = next((v for v in vers if v.id == project_version_id), None)
    if not target_ver and vers:
        target_ver = next((v for v in vers if v.status in ("published", "active")), vers[0])

    if not target_ver:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Aucune version de projet.")

    # RÈGLE MÉTIER ABSOLUE : Interdiction formelle de dissocier un formulaire d'une version publiée
    if target_ver.status in ("published", "active"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Action interdite : La version v{target_ver.version_number} de ce projet est déjà publiée et scellée. Impossible de dissocier un formulaire d'une version de projet publiée. Créez un nouveau brouillon pour effectuer des modifications."
        )

    gov = dict((target_ver.template_governance or {}) or (target_ver.settings or {}).get("template_governance", {}))
    if gov.get("allow_detach_form") is False:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Action interdite : la gouvernance scellée de ce projet verrouille la dissociation de formulaires."
        )

    v_settings = dict(target_ver.settings or {})
    attached_fvs = [x for x in (v_settings.get("attached_form_versions") or []) if x.get("form_id") != form_id]
    attached_ids = [x for x in (v_settings.get("attached_form_ids") or []) if x != form_id]

    v_settings["attached_form_versions"] = attached_fvs
    v_settings["attached_form_ids"] = attached_ids
    target_ver.settings = v_settings
    target_ver.parameters = v_settings
    flag_modified(target_ver, "settings")
    flag_modified(target_ver, "parameters")

    await db.commit()
    return {"success": True, "message": "Formulaire dissocié du projet."}


@router.post("/organization/workspace/projects/{project_id}/campaigns/attach")
async def attach_workspace_project_to_campaign(
    project_id: int,
    payload: Dict[str, Any] = Body(...),
    org_id: Optional[int] = Query(None, description="ID organisation pour Super Admin"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    [Espace Organisation] Associe un projet souverain et tous ses formulaires à une campagne souveraine.
    """
    org = await _resolve_user_organization(db, current_user, org_id)
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organisation introuvable.")

    campaign_id = payload.get("campaign_id")
    campaign_version_id = payload.get("campaign_version_id")
    if not campaign_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="campaign_id est requis.")

    # Récupérer la campagne
    c_stmt = select(OrganizationCampaign).options(
        selectinload(OrganizationCampaign.versions)
    ).where(
        OrganizationCampaign.id == campaign_id,
        OrganizationCampaign.organization_id == org.id
    )
    camp = (await db.execute(c_stmt)).scalars().first()
    if not camp:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campagne souveraine introuvable.")

    c_vers = camp.versions or []
    target_c_ver = None
    if campaign_version_id:
        target_c_ver = next((v for v in c_vers if v.id == campaign_version_id), None)
    if not target_c_ver and c_vers:
        target_c_ver = next((v for v in c_vers if v.status in ("published", "active", "open")), c_vers[0])

    if not target_c_ver:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Aucune version de campagne active.")

    c_settings = dict(target_c_ver.settings or {})
    t_projs = list(c_settings.get("target_projects") or [])
    if not any(tp.get("project_id") == project_id if isinstance(tp, dict) else tp == project_id for tp in t_projs):
        t_projs.append({"project_id": project_id})
        c_settings["target_projects"] = t_projs
        target_c_ver.settings = c_settings
        flag_modified(target_c_ver, "settings")
        await db.commit()

    return {"success": True, "message": "Projet associé à la campagne souveraine avec succès."}


@router.post("/organization/workspace/projects/{project_id}/campaigns/detach")
async def detach_workspace_project_from_campaign(
    project_id: int,
    payload: Dict[str, Any] = Body(...),
    org_id: Optional[int] = Query(None, description="ID organisation pour Super Admin"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    [Espace Organisation] Dissocie un projet souverain d'une campagne souveraine.
    """
    org = await _resolve_user_organization(db, current_user, org_id)
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organisation introuvable.")

    campaign_id = payload.get("campaign_id")
    campaign_version_id = payload.get("campaign_version_id")
    if not campaign_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="campaign_id est requis.")

    c_stmt = select(OrganizationCampaign).options(
        selectinload(OrganizationCampaign.versions)
    ).where(
        OrganizationCampaign.id == campaign_id,
        OrganizationCampaign.organization_id == org.id
    )
    camp = (await db.execute(c_stmt)).scalars().first()
    if not camp:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campagne souveraine introuvable.")

    c_vers = camp.versions or []
    target_c_ver = None
    if campaign_version_id:
        target_c_ver = next((v for v in c_vers if v.id == campaign_version_id), None)
        if not target_c_ver:
            res_cv = await db.execute(select(CampaignVersion).where(CampaignVersion.id == campaign_version_id))
            target_c_ver = res_cv.scalars().first()
    if not target_c_ver and c_vers:
        target_c_ver = next((v for v in c_vers if v.status in ("draft", "open", "published", "active")), c_vers[0])

    # RÈGLE MÉTIER ABSOLUE : Dans une version en brouillon, la modification est libre.
    # Seules les versions scellées/publiées sont immuables.
    if target_c_ver:
        if target_c_ver.status in ("published", "active", "in_progress", "completed", "closed", "archived"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Action interdite : La version « v{target_c_ver.version_number} » de la campagne « {camp.name} » est déjà scellée ({target_c_ver.status}). Impossible de dissocier un projet d'une version scellée afin de préserver l'intégrité et la traçabilité des collectes de données."
            )
    elif not campaign_version_id and camp.status in ("published", "active", "in_progress", "completed", "closed", "archived"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Action interdite : La campagne « {camp.name} » est déjà publiée ({camp.status}). Impossible de dissocier un projet d'une campagne publiée afin de préserver l'intégrité et la traçabilité des collectes de données."
        )

    if target_c_ver:
        c_settings = dict(target_c_ver.settings or {})
        t_projs = [tp for tp in (c_settings.get("target_projects") or []) if (tp.get("project_id") if isinstance(tp, dict) else tp) != project_id]
        c_settings["target_projects"] = t_projs
        target_c_ver.settings = c_settings
        flag_modified(target_c_ver, "settings")
        await db.commit()

    return {"success": True, "message": "Projet dissocié de la campagne."}




# =========================================================================
# GESTION DES CAMPAGNES SOUVERAINES DE L'ESPACE ORGANISATION
# =========================================================================

@router.get("/organization/workspace/campaigns")
async def get_workspace_campaigns(
    org_id: Optional[int] = Query(None, description="ID organisation pour Super Admin"),
    status_filter: Optional[str] = Query(None, alias="status", description="Filtre par statut"),
    search: Optional[str] = Query(None, description="Recherche par nom ou code"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    [Espace Organisation] Liste toutes les campagnes souveraines de l'organisation
    avec leurs versions et statistiques opérationnelles.
    """
    org = await _resolve_user_organization(db, current_user, org_id)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organisation introuvable."
        )

    stmt = select(OrganizationCampaign).options(
        selectinload(OrganizationCampaign.versions)
    ).where(
        OrganizationCampaign.organization_id == org.id
    ).order_by(OrganizationCampaign.id.desc())

    if status_filter:
        stmt = stmt.where(OrganizationCampaign.status == status_filter)

    camps = (await db.execute(stmt)).scalars().all()

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)

    results = []
    for c in camps:
        if search:
            q = search.lower()
            if q not in (c.name or "").lower() and q not in (c.code or "").lower():
                continue

        vers = c.versions or []
        active_ver = next((v for v in vers if v.version_number == c.version), None)
        if not active_ver and vers:
            active_ver = next((v for v in vers if v.status in ("open", "active", "published")), vers[0])

        days_remaining = None
        if active_ver and active_ver.end_date:
            ed = active_ver.end_date
            if ed.tzinfo is None:
                ed = ed.replace(tzinfo=timezone.utc)
            days_remaining = max(0, (ed - now).days)

        progress_pct = 0
        if active_ver and active_ver.metrics:
            progress_pct = active_ver.metrics.get("progress_percentage", 0)

        t_projects = (active_ver.target_projects if active_ver else []) or []
        t_forms = (active_ver.target_forms if active_ver else []) or []

        results.append({
            "id": c.id,
            "source_campaign_id": c.source_campaign_id,
            "organization_id": c.organization_id,
            "name": c.name,
            "code": c.code,
            "description": c.description,
            "visibility": c.visibility,
            "status": c.status,
            "version": c.version or "1.0.0",
            "plugin_code": (c.settings or {}).get("plugin_code") or "WORKSPACE",
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "updated_at": c.updated_at.isoformat() if c.updated_at else None,
            "startDate": active_ver.start_date.isoformat() if active_ver and active_ver.start_date else None,
            "endDate": active_ver.end_date.isoformat() if active_ver and active_ver.end_date else None,
            "budget": active_ver.budget if active_ver else None,
            "currency": active_ver.currency if active_ver else "EUR",
            "daysRemaining": days_remaining,
            "progressPercentage": progress_pct,
            "target_projects": t_projects,
            "target_forms": t_forms,
            "projects_count": len(t_projects),
            "forms_count": len(t_forms),
            "versions_count": len(vers),
            "parameters": c.parameters or {},
            "settings": c.settings or {},
            "versions": [
                {
                    "id": v.id,
                    "version_number": v.version_number,
                    "status": v.status,
                    "description": v.description,
                    "start_date": v.start_date.isoformat() if v.start_date else None,
                    "end_date": v.end_date.isoformat() if v.end_date else None,
                    "budget": v.budget,
                    "currency": v.currency,
                    "target_projects": v.target_projects or [],
                    "target_forms": v.target_forms or [],
                    "template_governance": v.template_governance or {},
                    "changelog": (v.settings or {}).get("changelog") or getattr(v, "changelog", None),
                    "created_at": v.created_at.isoformat() if v.created_at else None,
                }
                for v in vers
            ],
            "active_version": {
                "id": active_ver.id if active_ver else None,
                "version_number": active_ver.version_number if active_ver else "1.0.0",
                "status": active_ver.status if active_ver else "open",
                "start_date": active_ver.start_date.isoformat() if active_ver and active_ver.start_date else None,
                "end_date": active_ver.end_date.isoformat() if active_ver and active_ver.end_date else None,
                "budget": active_ver.budget if active_ver else None,
                "currency": active_ver.currency if active_ver else "EUR",
                "target_projects": t_projects,
                "target_forms": t_forms,
                "template_governance": active_ver.template_governance or {} if active_ver else {},
            } if active_ver else None,
        })
    return results


@router.get("/organization/workspace/campaigns/{campaign_id}")
async def get_workspace_campaign_detail(
    campaign_id: int,
    org_id: Optional[int] = Query(None, description="ID organisation pour Super Admin"),
    version_id: Optional[int] = Query(None, description="ID de version spécifique"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    [Espace Organisation] Récupère le détail complet d'une campagne souveraine
    avec sa gouvernance scellée, ses projets et formulaires cibles.
    """
    org = await _resolve_user_organization(db, current_user, org_id)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organisation introuvable."
        )

    stmt = select(OrganizationCampaign).options(
        selectinload(OrganizationCampaign.versions)
    ).where(
        (OrganizationCampaign.id == campaign_id) | (OrganizationCampaign.source_campaign_id == campaign_id),
        OrganizationCampaign.organization_id == org.id
    )
    camp = (await db.execute(stmt)).scalars().first()
    if not camp:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campagne souveraine introuvable pour votre organisation."
        )

    vers = camp.versions or []
    target_ver: Optional[OrganizationCampaignVersion] = None
    if version_id:
        for v in vers:
            if v.id == version_id:
                target_ver = v
                break
    if not target_ver and vers:
        target_ver = next((v for v in vers if v.version_number == camp.version), None)
        if not target_ver:
            target_ver = next((v for v in vers if v.status in ("open", "active", "published")), vers[0])

    # Permissions et paramètres transverses de la feature campaigns
    feat_stmt = select(OrganizationFeature).where(
        OrganizationFeature.feature_code.in_(["campaigns", "campaign"]),
        OrganizationFeature.status == "active"
    ).order_by(OrganizationFeature.id.desc())
    feats = (await db.execute(feat_stmt)).scalars().all()
    target_feat = None
    for f in feats:
        if org.id in (f.organization_ids or []):
            target_feat = f
            break
    if not target_feat and feats:
        target_feat = feats[0]

    feature_perms = dict((target_feat.permissions if target_feat else {}) or {})
    feature_params = dict((target_feat.parameters if target_feat else {}) or {})

    ver_gov = dict(
        (target_ver.template_governance if target_ver else {}) or
        (target_ver.settings.get("template_governance") if target_ver and target_ver.settings else {}) or
        (camp.parameters.get("template_governance", {}))
    )
    if not ver_gov:
        ver_gov = {
            "allow_add_version": True,
            "allow_edit": True,
            "allow_delete_version": True,
            "allow_delete_campaign": True,
            "allow_export": True,
            "allow_attach_project": True,
            "allow_detach_project": True,
            "allow_attach_form": True,
            "allow_detach_form": True,
        }

    feature_perms.update(ver_gov)
    if target_ver and target_ver.parameters:
        feature_params.update(target_ver.parameters)

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    days_remaining = None
    if target_ver and target_ver.end_date:
        ed = target_ver.end_date
        if ed.tzinfo is None:
            ed = ed.replace(tzinfo=timezone.utc)
        days_remaining = max(0, (ed - now).days)

    progress_pct = 0
    if target_ver and target_ver.metrics:
        progress_pct = target_ver.metrics.get("progress_percentage", 0)

    # Récupération des projets cibles et enrichissement
    raw_t_projects = (target_ver.target_projects if target_ver else []) or []
    enriched_projects = []
    p_ids = []
    for tp in raw_t_projects:
        pid = tp.get("id") or tp.get("project_id") if isinstance(tp, dict) else tp
        if pid:
            p_ids.append(pid)

    if p_ids:
        p_stmt = select(OrganizationProject).options(
            selectinload(OrganizationProject.versions)
        ).where(
            OrganizationProject.id.in_(p_ids),
            OrganizationProject.organization_id == org.id
        )
        found_projs = {p.id: p for p in (await db.execute(p_stmt)).scalars().all()}
        for tp in raw_t_projects:
            pid = tp.get("id") or tp.get("project_id") if isinstance(tp, dict) else tp
            p_obj = found_projs.get(pid)
            if p_obj:
                p_active_ver = next((v for v in (p_obj.versions or []) if v.status in ("published", "active")), None)
                if not p_active_ver and p_obj.versions:
                    p_active_ver = p_obj.versions[0]
                enriched_projects.append({
                    "id": p_obj.id,
                    "project_id": p_obj.id,
                    "name": p_obj.name,
                    "code": p_obj.code,
                    "project_type": p_obj.project_type,
                    "category": p_obj.category,
                    "status": p_obj.status,
                    "version": p_active_ver.version_number if p_active_ver else "1.0.0",
                    "description": (p_active_ver.description if p_active_ver and p_active_ver.description else None) or (p_obj.settings or {}).get("description") or "",
                    "forms_count": len((p_active_ver.settings or {}).get("attached_form_versions", [])) if p_active_ver else 0,
                })
            elif isinstance(tp, dict):
                enriched_projects.append(tp)

    # Récupération des formulaires cibles (directs + en cascade)
    raw_t_forms = (target_ver.target_forms if target_ver else []) or []
    enriched_forms = []
    f_ids = []
    for tf in raw_t_forms:
        fid = tf.get("id") or tf.get("form_id") if isinstance(tf, dict) else tf
        if fid:
            f_ids.append(fid)

    if f_ids:
        f_stmt = select(OrganizationForm).options(
            selectinload(OrganizationForm.versions)
        ).where(
            OrganizationForm.id.in_(f_ids),
            OrganizationForm.organization_id == org.id
        )
        found_forms = {f.id: f for f in (await db.execute(f_stmt)).scalars().all()}
        for tf in raw_t_forms:
            fid = tf.get("id") or tf.get("form_id") if isinstance(tf, dict) else tf
            f_obj = found_forms.get(fid)
            if f_obj:
                f_ver = f_obj.versions[0] if f_obj.versions else None
                enriched_forms.append({
                    "id": f_obj.id,
                    "form_id": f_obj.id,
                    "name": f_obj.name,
                    "code": f_obj.code,
                    "status": f_obj.status,
                    "version": f_ver.version_number if f_ver else "1.0.0",
                    "description": f_obj.description,
                    "cascaded_from_project_id": tf.get("cascaded_from_project_id") if isinstance(tf, dict) else None,
                })
            elif isinstance(tf, dict):
                enriched_forms.append(tf)

    formatted_campaign = {
        "id": camp.id,
        "source_campaign_id": camp.source_campaign_id,
        "organization_id": camp.organization_id,
        "name": camp.name,
        "code": camp.code,
        "description": (target_ver.description if target_ver and target_ver.description else None) or camp.description or "",
        "visibility": camp.visibility,
        "status": camp.status,
        "version": camp.version or "1.0.0",
        "startDate": target_ver.start_date.isoformat() if target_ver and target_ver.start_date else None,
        "endDate": target_ver.end_date.isoformat() if target_ver and target_ver.end_date else None,
        "budget": target_ver.budget if target_ver else None,
        "currency": target_ver.currency if target_ver else "EUR",
        "daysRemaining": days_remaining,
        "progressPercentage": progress_pct,
        "plugin_code": (camp.settings or {}).get("plugin_code") or "WORKSPACE",
        "created_at": camp.created_at.isoformat() if camp.created_at else None,
        "updated_at": camp.updated_at.isoformat() if camp.updated_at else None,
        "template_governance": ver_gov,
        "settings": target_ver.settings if target_ver else camp.settings,
        "parameters": target_ver.parameters if target_ver else camp.parameters,
        "target_projects": enriched_projects,
        "target_forms": enriched_forms,
        "projects_count": len(enriched_projects),
        "forms_count": len(enriched_forms),
        "versions": [
            {
                "id": v.id,
                "version_number": v.version_number,
                "status": v.status,
                "description": v.description,
                "start_date": v.start_date.isoformat() if v.start_date else None,
                "end_date": v.end_date.isoformat() if v.end_date else None,
                "budget": v.budget,
                "currency": v.currency,
                "target_projects": v.target_projects or [],
                "target_forms": v.target_forms or [],
                "template_governance": v.template_governance or {},
                "settings": v.settings or {},
                "parameters": v.parameters or {},
                "metrics": v.metrics or {},
                "changelog": (v.settings or {}).get("changelog") or getattr(v, "changelog", None),
                "created_at": v.created_at.isoformat() if v.created_at else None,
            }
            for v in vers
        ],
        "current_version": {
            "id": target_ver.id if target_ver else None,
            "version_number": target_ver.version_number if target_ver else "1.0.0",
            "status": target_ver.status if target_ver else "open",
            "description": target_ver.description if target_ver else None,
            "start_date": target_ver.start_date.isoformat() if target_ver and target_ver.start_date else None,
            "end_date": target_ver.end_date.isoformat() if target_ver and target_ver.end_date else None,
            "budget": target_ver.budget if target_ver else None,
            "currency": target_ver.currency if target_ver else "EUR",
            "template_governance": ver_gov,
            "settings": target_ver.settings if target_ver else {},
            "parameters": target_ver.parameters if target_ver else {},
            "metrics": target_ver.metrics if target_ver else {},
            "target_projects": enriched_projects,
            "target_forms": enriched_forms,
        } if target_ver else None,
    }

    return {
        "campaign": formatted_campaign,
        "feature_permissions": feature_perms,
        "feature_parameters": feature_params,
        "organization": {
            "id": org.id,
            "name": org.name,
            "code": getattr(org, "code", None),
            "logo_url": org.logo_url,
        }
    }


@router.get("/organization/workspace/campaigns/{campaign_id}/projects")
async def get_workspace_campaign_projects(
    campaign_id: int,
    campaign_version_id: Optional[int] = Query(None, description="ID de version de la campagne"),
    org_id: Optional[int] = Query(None, description="ID organisation pour Super Admin"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    [Espace Organisation] Liste les projets souverains attachés à la campagne
    ainsi que tous les projets souverains disponibles de l'organisation pour association.
    """
    org = await _resolve_user_organization(db, current_user, org_id)
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organisation introuvable.")

    stmt = select(OrganizationCampaign).options(
        selectinload(OrganizationCampaign.versions)
    ).where(
        (OrganizationCampaign.id == campaign_id) | (OrganizationCampaign.source_campaign_id == campaign_id),
        OrganizationCampaign.organization_id == org.id
    )
    camp = (await db.execute(stmt)).scalars().first()
    if not camp:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campagne souveraine introuvable.")

    vers = camp.versions or []
    target_ver = None
    if campaign_version_id:
        target_ver = next((v for v in vers if v.id == campaign_version_id), None)
    if not target_ver and vers:
        target_ver = next((v for v in vers if v.version_number == camp.version), None)
        if not target_ver:
            target_ver = next((v for v in vers if v.status in ("open", "active", "published")), vers[0])

    t_projs = (target_ver.target_projects if target_ver else []) or []
    attached_pids = set()
    for tp in t_projs:
        pid = tp.get("id") or tp.get("project_id") if isinstance(tp, dict) else tp
        if pid:
            attached_pids.add(int(pid))

    p_all_stmt = select(OrganizationProject).options(
        selectinload(OrganizationProject.versions)
    ).where(
        OrganizationProject.organization_id == org.id
    ).order_by(OrganizationProject.id.desc())
    all_projs = (await db.execute(p_all_stmt)).scalars().all()

    attached = []
    available = []

    for p in all_projs:
        active_p_ver = next((v for v in (p.versions or []) if v.status in ("published", "active")), None)
        if not active_p_ver and p.versions:
            active_p_ver = p.versions[0]

        p_dict = {
            "id": p.id,
            "project_id": p.id,
            "name": p.name,
            "code": p.code,
            "project_type": p.project_type,
            "category": p.category,
            "status": p.status,
            "version": active_p_ver.version_number if active_p_ver else "1.0.0",
            "description": (active_p_ver.description if active_p_ver and active_p_ver.description else None) or (p.settings or {}).get("description") or "",
            "forms_count": len((active_p_ver.settings or {}).get("attached_form_versions", [])) if active_p_ver else 0,
        }

        if p.id in attached_pids:
            attached.append(p_dict)
        else:
            available.append(p_dict)

    return {
        "attached_projects": attached,
        "available_projects": available,
    }


@router.get("/organization/workspace/campaigns/{campaign_id}/forms")
async def get_workspace_campaign_forms(
    campaign_id: int,
    campaign_version_id: Optional[int] = Query(None, description="ID de version de la campagne"),
    org_id: Optional[int] = Query(None, description="ID organisation pour Super Admin"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    [Espace Organisation] Liste les formulaires souverains attachés à la campagne (directs + en cascade)
    ainsi que tous les formulaires disponibles de l'organisation pour association directe.
    """
    org = await _resolve_user_organization(db, current_user, org_id)
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organisation introuvable.")

    stmt = select(OrganizationCampaign).options(
        selectinload(OrganizationCampaign.versions)
    ).where(
        (OrganizationCampaign.id == campaign_id) | (OrganizationCampaign.source_campaign_id == campaign_id),
        OrganizationCampaign.organization_id == org.id
    )
    camp = (await db.execute(stmt)).scalars().first()
    if not camp:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campagne souveraine introuvable.")

    vers = camp.versions or []
    target_ver = None
    if campaign_version_id:
        target_ver = next((v for v in vers if v.id == campaign_version_id), None)
    if not target_ver and vers:
        target_ver = next((v for v in vers if v.version_number == camp.version), None)
        if not target_ver:
            target_ver = next((v for v in vers if v.status in ("open", "active", "published")), vers[0])

    t_forms = (target_ver.target_forms if target_ver else []) or []
    attached_fids_map = {}
    for tf in t_forms:
        fid = tf.get("id") or tf.get("form_id") if isinstance(tf, dict) else tf
        if fid:
            attached_fids_map[int(fid)] = tf

    f_all_stmt = select(OrganizationForm).options(
        selectinload(OrganizationForm.versions)
    ).where(
        OrganizationForm.organization_id == org.id
    ).order_by(OrganizationForm.id.desc())
    all_forms = (await db.execute(f_all_stmt)).scalars().all()

    attached = []
    available = []

    for f in all_forms:
        f_ver = f.versions[0] if f.versions else None
        f_dict = {
            "id": f.id,
            "form_id": f.id,
            "name": f.name,
            "code": f.code,
            "status": f.status,
            "version": f_ver.version_number if f_ver else "1.0.0",
            "description": f.description,
            "cascaded_from_project_id": attached_fids_map.get(f.id, {}).get("cascaded_from_project_id") if isinstance(attached_fids_map.get(f.id), dict) else None,
        }

        if f.id in attached_fids_map:
            attached.append(f_dict)
        else:
            available.append(f_dict)

    return {
        "attached_forms": attached,
        "available_forms": available,
    }


@router.post("/organization/workspace/campaigns/{campaign_id}/projects/attach")
async def attach_workspace_project_to_campaign_v2(
    campaign_id: int,
    payload: Dict[str, Any] = Body(...),
    org_id: Optional[int] = Query(None, description="ID organisation pour Super Admin"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    [Espace Organisation] Associe un projet souverain à une version de campagne
    et cascade automatiquement ses formulaires.
    """
    org = await _resolve_user_organization(db, current_user, org_id)
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organisation introuvable.")

    project_id = payload.get("project_id")
    campaign_version_id = payload.get("campaign_version_id")
    if not project_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="project_id est requis.")

    c_stmt = select(OrganizationCampaign).options(
        selectinload(OrganizationCampaign.versions)
    ).where(
        (OrganizationCampaign.id == campaign_id) | (OrganizationCampaign.source_campaign_id == campaign_id),
        OrganizationCampaign.organization_id == org.id
    )
    camp = (await db.execute(c_stmt)).scalars().first()
    if not camp:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campagne souveraine introuvable.")

    c_vers = camp.versions or []
    target_ver = None
    if campaign_version_id:
        target_ver = next((v for v in c_vers if v.id == campaign_version_id), None)
    if not target_ver and c_vers:
        target_ver = next((v for v in c_vers if v.version_number == camp.version), None)
        if not target_ver:
            target_ver = next((v for v in c_vers if v.status in ("open", "active", "published")), c_vers[0])

    if not target_ver:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version de campagne introuvable.")

    p_stmt = select(OrganizationProject).options(
        selectinload(OrganizationProject.versions)
    ).where(
        OrganizationProject.id == project_id,
        OrganizationProject.organization_id == org.id
    )
    proj = (await db.execute(p_stmt)).scalars().first()
    if not proj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Projet souverain introuvable.")

    cur_projs = list(target_ver.target_projects or [])
    if not any((p.get("id") or p.get("project_id")) == project_id if isinstance(p, dict) else p == project_id for p in cur_projs):
        cur_projs.append({
            "id": proj.id,
            "project_id": proj.id,
            "name": proj.name,
            "code": proj.code,
        })
        target_ver.target_projects = cur_projs
        c_settings = dict(target_ver.settings or {})
        c_settings["target_projects"] = cur_projs
        target_ver.settings = c_settings
        flag_modified(target_ver, "target_projects")
        flag_modified(target_ver, "settings")

    proj_active_ver = next((v for v in (proj.versions or []) if v.status in ("published", "active")), None)
    if not proj_active_ver and proj.versions:
        proj_active_ver = proj.versions[0]

    if proj_active_ver:
        attached_fvs = (proj_active_ver.settings or {}).get("attached_form_versions", [])
        cur_forms = list(target_ver.target_forms or [])
        cur_form_ids = {
            (f.get("id") or f.get("form_id")) if isinstance(f, dict) else f
            for f in cur_forms
        }

        for fv in attached_fvs:
            fid = fv.get("form_id") or fv.get("id")
            if fid and fid not in cur_form_ids:
                cur_forms.append({
                    "id": fid,
                    "form_id": fid,
                    "name": fv.get("form_name") or fv.get("name") or f"Formulaire #{fid}",
                    "code": fv.get("form_code") or fv.get("code") or "",
                    "cascaded_from_project_id": proj.id,
                })
                cur_form_ids.add(fid)

        target_ver.target_forms = cur_forms
        c_settings = dict(target_ver.settings or {})
        c_settings["target_forms"] = cur_forms
        target_ver.settings = c_settings
        flag_modified(target_ver, "target_forms")
        flag_modified(target_ver, "settings")

    await db.commit()
    return {"success": True, "message": f"Projet « {proj.name} » associé avec succès à la campagne."}


@router.post("/organization/workspace/campaigns/{campaign_id}/projects/detach")
async def detach_workspace_project_from_campaign_v2(
    campaign_id: int,
    payload: Dict[str, Any] = Body(...),
    org_id: Optional[int] = Query(None, description="ID organisation pour Super Admin"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    [Espace Organisation] Dissocie un projet souverain d'une version de campagne
    et retire les formulaires cascaded correspondants.
    """
    org = await _resolve_user_organization(db, current_user, org_id)
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organisation introuvable.")

    project_id = payload.get("project_id")
    campaign_version_id = payload.get("campaign_version_id")
    if not project_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="project_id est requis.")

    c_stmt = select(OrganizationCampaign).options(
        selectinload(OrganizationCampaign.versions)
    ).where(
        (OrganizationCampaign.id == campaign_id) | (OrganizationCampaign.source_campaign_id == campaign_id),
        OrganizationCampaign.organization_id == org.id
    )
    camp = (await db.execute(c_stmt)).scalars().first()
    if not camp:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campagne souveraine introuvable.")

    c_vers = camp.versions or []
    target_ver = None
    if campaign_version_id:
        target_ver = next((v for v in c_vers if v.id == campaign_version_id), None)
        if not target_ver:
            res_cv = await db.execute(select(CampaignVersion).where(CampaignVersion.id == campaign_version_id))
            target_ver = res_cv.scalars().first()
    if not target_ver and c_vers:
        target_ver = next((v for v in c_vers if v.version_number == camp.version), None)
        if not target_ver:
            target_ver = next((v for v in c_vers if v.status in ("draft", "open", "active", "published")), c_vers[0])

    # RÈGLE MÉTIER ABSOLUE : Dans une version en brouillon, la modification est libre.
    # Seules les versions scellées/publiées sont immuables.
    if target_ver:
        if target_ver.status in ("published", "active", "in_progress", "completed", "closed", "archived"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Action interdite : La version « v{target_ver.version_number} » de la campagne « {camp.name} » est déjà scellée ({target_ver.status}). Impossible de dissocier un formulaire d'une version scellée afin de préserver l'intégrité et la traçabilité des collectes de données. Créez une nouvelle version en brouillon pour ajuster le périmètre."
            )
    elif not campaign_version_id and camp.status in ("published", "active", "in_progress", "completed", "closed", "archived"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Action interdite : La campagne « {camp.name} » est déjà publiée ({camp.status}). Impossible de dissocier un formulaire d'une campagne publiée afin de préserver l'intégrité et la traçabilité des collectes de données."
        )

    if target_ver:
        cur_projs = [
            p for p in (target_ver.target_projects or [])
            if ((p.get("id") or p.get("project_id")) if isinstance(p, dict) else p) != project_id
        ]
        target_ver.target_projects = cur_projs

        cur_forms = [
            f for f in (target_ver.target_forms or [])
            if not (isinstance(f, dict) and f.get("cascaded_from_project_id") == project_id)
        ]
        target_ver.target_forms = cur_forms

        c_settings = dict(target_ver.settings or {})
        c_settings["target_projects"] = cur_projs
        c_settings["target_forms"] = cur_forms
        target_ver.settings = c_settings

        flag_modified(target_ver, "target_projects")
        flag_modified(target_ver, "target_forms")
        flag_modified(target_ver, "settings")
        await db.commit()

    return {"success": True, "message": "Projet dissocié de la campagne avec succès."}


@router.post("/organization/workspace/campaigns/{campaign_id}/forms/attach")
async def attach_workspace_form_to_campaign_v2(
    campaign_id: int,
    payload: Dict[str, Any] = Body(...),
    org_id: Optional[int] = Query(None, description="ID organisation pour Super Admin"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    [Espace Organisation] Associe directement un formulaire souverain à une version de campagne.
    """
    org = await _resolve_user_organization(db, current_user, org_id)
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organisation introuvable.")

    form_id = payload.get("form_id")
    campaign_version_id = payload.get("campaign_version_id")
    if not form_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="form_id est requis.")

    c_stmt = select(OrganizationCampaign).options(
        selectinload(OrganizationCampaign.versions)
    ).where(
        (OrganizationCampaign.id == campaign_id) | (OrganizationCampaign.source_campaign_id == campaign_id),
        OrganizationCampaign.organization_id == org.id
    )
    camp = (await db.execute(c_stmt)).scalars().first()
    if not camp:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campagne souveraine introuvable.")

    c_vers = camp.versions or []
    target_ver = None
    if campaign_version_id:
        target_ver = next((v for v in c_vers if v.id == campaign_version_id), None)
    if not target_ver and c_vers:
        target_ver = next((v for v in c_vers if v.version_number == camp.version), None)
        if not target_ver:
            target_ver = next((v for v in c_vers if v.status in ("open", "active", "published")), c_vers[0])

    if not target_ver:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version de campagne introuvable.")

    f_stmt = select(OrganizationForm).where(
        OrganizationForm.id == form_id,
        OrganizationForm.organization_id == org.id
    )
    frm = (await db.execute(f_stmt)).scalars().first()
    if not frm:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Formulaire souverain introuvable.")

    cur_forms = list(target_ver.target_forms or [])
    if not any((f.get("id") or f.get("form_id")) == form_id if isinstance(f, dict) else f == form_id for f in cur_forms):
        cur_forms.append({
            "id": frm.id,
            "form_id": frm.id,
            "name": frm.name,
            "code": frm.code,
        })
        target_ver.target_forms = cur_forms
        c_settings = dict(target_ver.settings or {})
        c_settings["target_forms"] = cur_forms
        target_ver.settings = c_settings
        flag_modified(target_ver, "target_forms")
        flag_modified(target_ver, "settings")
        await db.commit()

    return {"success": True, "message": f"Formulaire « {frm.name} » associé avec succès à la campagne."}


@router.post("/organization/workspace/campaigns/{campaign_id}/forms/detach")
async def detach_workspace_form_from_campaign_v2(
    campaign_id: int,
    payload: Dict[str, Any] = Body(...),
    org_id: Optional[int] = Query(None, description="ID organisation pour Super Admin"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    [Espace Organisation] Dissocie un formulaire d'une version de campagne.
    """
    org = await _resolve_user_organization(db, current_user, org_id)
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organisation introuvable.")

    form_id = payload.get("form_id")
    campaign_version_id = payload.get("campaign_version_id")
    if not form_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="form_id est requis.")

    c_stmt = select(OrganizationCampaign).options(
        selectinload(OrganizationCampaign.versions)
    ).where(
        (OrganizationCampaign.id == campaign_id) | (OrganizationCampaign.source_campaign_id == campaign_id),
        OrganizationCampaign.organization_id == org.id
    )
    camp = (await db.execute(c_stmt)).scalars().first()
    if not camp:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campagne souveraine introuvable.")

    c_vers = camp.versions or []
    target_ver = None
    if campaign_version_id:
        target_ver = next((v for v in c_vers if v.id == campaign_version_id), None)
        if not target_ver:
            res_cv = await db.execute(select(CampaignVersion).where(CampaignVersion.id == campaign_version_id))
            target_ver = res_cv.scalars().first()
    if not target_ver and c_vers:
        target_ver = next((v for v in c_vers if v.version_number == camp.version), None)
        if not target_ver:
            target_ver = next((v for v in c_vers if v.status in ("draft", "open", "active", "published")), c_vers[0])

    # RÈGLE MÉTIER ABSOLUE : Dans une version en brouillon, la modification est libre.
    # Seules les versions scellées/publiées sont immuables.
    if target_ver:
        if target_ver.status in ("published", "active", "in_progress", "completed", "closed", "archived"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Action interdite : La version « v{target_ver.version_number} » de la campagne « {camp.name} » est déjà scellée ({target_ver.status}). Impossible de dissocier un formulaire d'une version scellée afin de préserver l'intégrité et la traçabilité des collectes de données. Créez une nouvelle version en brouillon pour ajuster le périmètre."
            )
    elif not campaign_version_id and camp.status in ("published", "active", "in_progress", "completed", "closed", "archived"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Action interdite : La campagne « {camp.name} » est déjà publiée ({camp.status}). Impossible de dissocier un formulaire d'une campagne publiée afin de préserver l'intégrité et la traçabilité des collectes de données."
        )

    if target_ver:
        cur_forms = [
            f for f in (target_ver.target_forms or [])
            if ((f.get("id") or f.get("form_id")) if isinstance(f, dict) else f) != form_id
        ]
        target_ver.target_forms = cur_forms
        c_settings = dict(target_ver.settings or {})
        c_settings["target_forms"] = cur_forms
        target_ver.settings = c_settings
        flag_modified(target_ver, "target_forms")
        flag_modified(target_ver, "settings")
        await db.commit()

    return {"success": True, "message": "Formulaire dissocié de la campagne."}


@router.post("/organization/workspace/campaigns/{campaign_id}/versions")
async def create_workspace_campaign_version(
    campaign_id: int,
    payload: Dict[str, Any] = Body(...),
    org_id: Optional[int] = Query(None, description="ID organisation pour Super Admin"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    [Espace Organisation] Crée ou clone une version pour une campagne souveraine.
    """
    org = await _resolve_user_organization(db, current_user, org_id)
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organisation introuvable.")

    version_number = payload.get("version_number")
    if not version_number:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="version_number est requis.")

    c_stmt = select(OrganizationCampaign).options(
        selectinload(OrganizationCampaign.versions)
    ).where(
        (OrganizationCampaign.id == campaign_id) | (OrganizationCampaign.source_campaign_id == campaign_id),
        OrganizationCampaign.organization_id == org.id
    )
    camp = (await db.execute(c_stmt)).scalars().first()
    if not camp:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campagne souveraine introuvable.")

    for v in (camp.versions or []):
        if v.version_number == version_number:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"La version v{version_number} existe déjà pour cette campagne."
            )

    source_ver = None
    source_ver_id = payload.get("source_version_id")
    if source_ver_id:
        source_ver = next((v for v in (camp.versions or []) if v.id == source_ver_id), None)

    new_ver = OrganizationCampaignVersion(
        organization_campaign_id=camp.id,
        source_campaign_version_id=source_ver.source_campaign_version_id if source_ver else None,
        version_number=version_number,
        status="draft",
        description=payload.get("description") or (source_ver.description if source_ver else camp.description),
        start_date=source_ver.start_date if source_ver else None,
        end_date=source_ver.end_date if source_ver else None,
        budget=source_ver.budget if source_ver else None,
        currency=source_ver.currency if source_ver else "EUR",
        target_projects=list(source_ver.target_projects or []) if source_ver else [],
        target_forms=list(source_ver.target_forms or []) if source_ver else [],
        template_governance=dict(source_ver.template_governance or {}) if source_ver else {
            "allow_edit_version": True,
            "allow_clone_version": True,
            "allow_publish_version": True,
            "allow_delete_version": True,
            "allow_export_version": True,
        },
        settings={"changelog": payload.get("changelog") or "Nouvelle version"},
        parameters={},
    )
    db.add(new_ver)
    await db.commit()
    await db.refresh(new_ver)

    return {"success": True, "message": f"Version v{version_number} créée en brouillon.", "version": {
        "id": new_ver.id,
        "version_number": new_ver.version_number,
        "status": new_ver.status,
    }}


@router.post("/organization/workspace/campaigns/{campaign_id}/versions/{version_id}/publish")
async def publish_workspace_campaign_version(
    campaign_id: int,
    version_id: int,
    org_id: Optional[int] = Query(None, description="ID organisation pour Super Admin"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    [Espace Organisation] Crée une copie publiée immuable d'une version de campagne
    (numérotée v{version} / {n}) avec cascade sur tous les projets et formulaires dépendants.
    """
    org = await _resolve_user_organization(db, current_user, org_id)
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organisation introuvable.")

    from app.services.plugin.organization_publication_service import OrganizationPublicationService
    pub_ver = await OrganizationPublicationService.publish_campaign_version(
        db=db,
        campaign_id=campaign_id,
        version_id=version_id,
        organization_id=org.id,
        user_id=current_user.id,
    )
    await db.commit()

    return {"success": True, "message": f"Version {pub_ver.version_number} publiée avec succès.", "version": {
        "id": pub_ver.id,
        "version_number": pub_ver.version_number,
        "status": pub_ver.status,
    }}


@router.post("/organization/workspace/campaigns/{campaign_id}/versions/{version_id}/activate")
async def activate_workspace_campaign_version(
    campaign_id: int,
    version_id: int,
    org_id: Optional[int] = Query(None, description="ID organisation pour Super Admin"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    [Espace Organisation] Définit une version comme version active courante de la campagne.
    """
    org = await _resolve_user_organization(db, current_user, org_id)
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organisation introuvable.")

    c_stmt = select(OrganizationCampaign).options(
        selectinload(OrganizationCampaign.versions)
    ).where(
        (OrganizationCampaign.id == campaign_id) | (OrganizationCampaign.source_campaign_id == campaign_id),
        OrganizationCampaign.organization_id == org.id
    )
    camp = (await db.execute(c_stmt)).scalars().first()
    if not camp:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campagne souveraine introuvable.")

    target_ver = next((v for v in (camp.versions or []) if v.id == version_id), None)
    if not target_ver:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version introuvable.")

    camp.version = target_ver.version_number
    flag_modified(camp, "version")
    await db.commit()

    return {"success": True, "message": f"Version v{target_ver.version_number} définie comme version active."}
