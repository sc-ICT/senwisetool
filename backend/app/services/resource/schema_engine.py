import io
import os
import re
import json
import csv
import zipfile
from typing import List, Dict, Any, Tuple, Optional
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

# Ensemble exhaustif des types de données supportés
ALL_RESOURCE_TYPES = [
    "text",
    "textarea",
    "number",
    "integer",
    "decimal",
    "boolean",
    "currency",
    "percentage",
    "date",
    "datetime",
    "time",
    "email",
    "phone",
    "url",
    "select",
    "multiselect",
    "file",
    "image",
    "audio",
    "video",
    "geo_point",
    "geo_polygon",
    "barcode",
    "color",
    "json",
    "relation",
]

# Mapping d'alias et synonymes vers le type standard
TYPE_ALIASES = {
    "string": "text",
    "texte": "text",
    "texte_court": "text",
    "str": "text",
    "texte_long": "textarea",
    "multiline": "textarea",
    "description": "textarea",
    "nombre": "number",
    "float": "number",
    "chiffre": "number",
    "entier": "integer",
    "int": "integer",
    "decimal": "decimal",
    "double": "decimal",
    "bool": "boolean",
    "booleen": "boolean",
    "case": "boolean",
    "devise": "currency",
    "argent": "currency",
    "montant": "currency",
    "monnaie": "currency",
    "pourcentage": "percentage",
    "percent": "percentage",
    "date_heure": "datetime",
    "timestamp": "datetime",
    "heure": "time",
    "mail": "email",
    "courriel": "email",
    "telephone": "phone",
    "tel": "phone",
    "lien": "url",
    "web": "url",
    "choix": "select",
    "liste": "select",
    "tags": "multiselect",
    "etiquettes": "multiselect",
    "choix_multiples": "multiselect",
    "fichier": "file",
    "document": "file",
    "photo": "image",
    "img": "image",
    "son": "audio",
    "vocal": "audio",
    "enregistrement": "audio",
    "clip": "video",
    "film": "video",
    "gps": "geo_point",
    "coordonnees": "geo_point",
    "position": "geo_point",
    "parcelle": "geo_polygon",
    "polygone": "geo_polygon",
    "contour": "geo_polygon",
    "qr": "barcode",
    "qrcode": "barcode",
    "code_barre": "barcode",
    "codebarre": "barcode",
    "couleur": "color",
    "hex": "color",
    "liaison": "relation",
    "relation_ressource": "relation",
}


def normalize_field_name(raw_name: str) -> str:
    """Normalise un nom technique de champ (slugifié, minuscules, sans espaces)."""
    s = str(raw_name or "").strip().lower()
    s = re.sub(r"[^a-z0-9_]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s


def normalize_field_type(raw_type: str) -> str:
    """Normalise le type de données vers l'un des types autorisés."""
    t = str(raw_type or "").strip().lower()
    if t in ALL_RESOURCE_TYPES:
        return t
    return TYPE_ALIASES.get(t, "text")


def parse_boolean(val: Any) -> bool:
    """Interprète diverses représentations de booléens."""
    if isinstance(val, bool):
        return val
    s = str(val or "").strip().lower()
    return s in ["true", "1", "yes", "oui", "vrai", "o", "y"]


def parse_schema_from_bytes(file_bytes: bytes, filename: str) -> List[Dict[str, Any]]:
    """
    Parse un fichier Excel (.xlsx, .xls), CSV (.csv) ou JSON (.json)
    et extrait une liste de définitions de champs normalisées.
    """
    fname = filename.lower()
    fields: List[Dict[str, Any]] = []

    if fname.endswith(".json"):
        content_str = file_bytes.decode("utf-8")
        data = json.loads(content_str)
        raw_list = data
        if isinstance(data, dict):
            raw_list = data.get("schema_fields") or data.get("fields") or data.get("items") or []
        
        if not isinstance(raw_list, list):
            raise ValueError("Le fichier JSON doit contenir une liste de champs.")

        for item in raw_list:
            if not isinstance(item, dict):
                continue
            name = normalize_field_name(item.get("name") or item.get("code") or item.get("key") or "")
            label = str(item.get("label") or item.get("libelle") or name).strip()
            if not name:
                continue
            
            ftype = normalize_field_type(item.get("type") or "text")
            options = item.get("options")
            if isinstance(options, str):
                options = [o.strip() for o in options.split(",") if o.strip()]

            fields.append({
                "name": name,
                "label": label or name,
                "type": ftype,
                "required": bool(item.get("required")),
                "unique": bool(item.get("unique")),
                "options": options if ftype in ["select", "multiselect"] else None,
                "default_value": item.get("default_value"),
                "target_resource_id": item.get("target_resource_id"),
                "target_resource_code": item.get("target_resource_code"),
                "relation_type": item.get("relation_type", "many_to_one"),
                "display_field": item.get("display_field"),
            })

    elif fname.endswith(".xlsx") or fname.endswith(".xls"):
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
        sheet = wb.active
        rows = list(sheet.iter_rows(values_only=True))
        if not rows:
            return []

        # En-têtes (ligne 1)
        headers = [str(h or "").strip().lower() for h in rows[0]]
        
        # Détection des index de colonnes
        def find_col_idx(aliases: List[str]) -> Optional[int]:
            for alias in aliases:
                for idx, h in enumerate(headers):
                    if alias in h:
                        return idx
            return None

        idx_name = find_col_idx(["name", "code", "cle", "champ", "technique"])
        idx_label = find_col_idx(["label", "libelle", "titre", "nom"])
        idx_type = find_col_idx(["type", "format", "genre"])
        idx_required = find_col_idx(["required", "obligatoire", "requis", "oblig"])
        idx_unique = find_col_idx(["unique", "unicite", "anti-doublon", "doublon"])
        idx_default = find_col_idx(["default", "defaut", "valeur_defaut"])
        idx_options = find_col_idx(["option", "choix", "valeurs"])
        idx_target = find_col_idx(["cible", "target", "relation", "ressource_liee"])

        for row in rows[1:]:
            if not row:
                continue
            raw_name = str(row[idx_name] or "") if idx_name is not None and idx_name < len(row) else ""
            raw_label = str(row[idx_label] or "") if idx_label is not None and idx_label < len(row) else ""
            
            name = normalize_field_name(raw_name or raw_label)
            if not name:
                continue

            label = raw_label.strip() or name.replace("_", " ").title()
            raw_type = str(row[idx_type] or "") if idx_type is not None and idx_type < len(row) else "text"
            ftype = normalize_field_type(raw_type)

            req = parse_boolean(row[idx_required]) if idx_required is not None and idx_required < len(row) else False
            uniq = parse_boolean(row[idx_unique]) if idx_unique is not None and idx_unique < len(row) else False
            def_val = row[idx_default] if idx_default is not None and idx_default < len(row) else None
            
            opts = None
            if idx_options is not None and idx_options < len(row) and row[idx_options]:
                opts = [o.strip() for o in str(row[idx_options]).split(",") if o.strip()]

            target_res = str(row[idx_target]).strip() if idx_target is not None and idx_target < len(row) and row[idx_target] else None

            fields.append({
                "name": name,
                "label": label,
                "type": ftype,
                "required": req,
                "unique": uniq,
                "options": opts if ftype in ["select", "multiselect"] else None,
                "default_value": def_val,
                "target_resource_code": target_res,
                "relation_type": "many_to_one" if ftype == "relation" else None,
                "display_field": None,
            })

    elif fname.endswith(".csv"):
        # Détection automatique de l'encodage et délimiteur
        for encoding in ["utf-8-sig", "utf-8", "latin-1"]:
            try:
                decoded = file_bytes.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            decoded = file_bytes.decode("utf-8", errors="ignore")

        # Délimiteur : virgule ou point-virgule
        first_line = decoded.splitlines()[0] if decoded.splitlines() else ""
        delim = ";" if ";" in first_line else ","

        reader = csv.DictReader(decoded.splitlines(), delimiter=delim)
        for row in reader:
            # Nettoyer les clés du dict
            cleaned_row = {k.strip().lower(): v for k, v in row.items() if k}
            raw_name = (
                cleaned_row.get("name")
                or cleaned_row.get("code")
                or cleaned_row.get("cle")
                or cleaned_row.get("cle technique")
                or ""
            )
            raw_label = (
                cleaned_row.get("label")
                or cleaned_row.get("libelle")
                or cleaned_row.get("nom")
                or raw_name
            )

            name = normalize_field_name(raw_name or raw_label)
            if not name:
                continue

            ftype = normalize_field_type(cleaned_row.get("type") or "text")
            req = parse_boolean(cleaned_row.get("required") or cleaned_row.get("obligatoire"))
            uniq = parse_boolean(cleaned_row.get("unique") or cleaned_row.get("unicite"))
            def_val = cleaned_row.get("default_value") or cleaned_row.get("defaut")
            raw_opts = cleaned_row.get("options") or cleaned_row.get("choix")
            opts = [o.strip() for o in raw_opts.split(",") if o.strip()] if raw_opts else None

            fields.append({
                "name": name,
                "label": raw_label.strip() or name,
                "type": ftype,
                "required": req,
                "unique": uniq,
                "options": opts,
                "default_value": def_val,
                "target_resource_code": cleaned_row.get("target_resource") or cleaned_row.get("cible"),
                "relation_type": "many_to_one",
                "display_field": None,
            })

    else:
        raise ValueError("Format de fichier non supporté. Veuillez importer un fichier .xlsx, .csv ou .json.")

    return fields


def generate_schema_excel_template() -> bytes:
    """Génère un classeur Excel modèle prêt à l'emploi avec explications et exemples concrets."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Gabarit Schéma Ressource"

    # En-têtes
    headers = [
        "Clé Technique (name) *",
        "Libellé Affiché (label) *",
        "Type de Données (type) *",
        "Obligatoire (required)",
        "Valeur Unique (unique)",
        "Valeur par Défaut (default_value)",
        "Options (pour select/multiselect)",
        "Ressource Cible (pour relation)",
    ]

    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="10B981", end_color="10B981", fill_type="solid")
    center_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
    thin_border = Border(
        left=Side(style="thin", color="E2E8F0"),
        right=Side(style="thin", color="E2E8F0"),
        top=Side(style="thin", color="E2E8F0"),
        bottom=Side(style="thin", color="E2E8F0"),
    )

    ws.append(headers)
    ws.row_dimensions[1].height = 28

    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = center_align

    # Lignes d'exemples réalistes couvrant différents types
    sample_rows = [
        ["matricule", "Matricule Planteur", "text", "OUI", "OUI", "", "", ""],
        ["nom_complet", "Nom et Prénoms", "text", "OUI", "NON", "", "", ""],
        ["superficie_ha", "Superficie (Hectares)", "decimal", "OUI", "NON", "0.0", "", ""],
        ["date_adhesion", "Date d'Adhésion", "date", "NON", "NON", "", "", ""],
        ["statut_certification", "Statut Certification", "select", "NON", "NON", "En cours", "En cours, Certifié Fairtrade, Certifié Bio, Non certifié", ""],
        ["est_actif", "Compte Actif", "boolean", "OUI", "NON", "VRAI", "", ""],
        ["contact_phone", "Numéro de Téléphone", "phone", "NON", "OUI", "", "", ""],
        ["photo_planteur", "Photo d'Identité", "image", "NON", "NON", "", "", ""],
        ["enregistrement_audio", "Accord Vocal Audio", "audio", "NON", "NON", "", "", ""],
        ["video_inspection", "Vidéo d'Inspection", "video", "NON", "NON", "", "", ""],
        ["coordonnees_gps", "Position GPS Ferme", "geo_point", "NON", "NON", "", "", ""],
        ["contour_parcelle", "Contour Polygone GPS", "geo_polygon", "NON", "NON", "", "", ""],
        ["qrcode_parcelle", "Code QR / Traçabilité", "barcode", "NON", "OUI", "", "", ""],
        ["cooperative_id", "Coopérative Rattachement", "relation", "NON", "NON", "", "", "cooperatives"],
    ]

    for row_data in sample_rows:
        ws.append(row_data)

    # Style des lignes de données
    for r_idx in range(2, len(sample_rows) + 2):
        ws.row_dimensions[r_idx].height = 20
        for c_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=r_idx, column=c_idx)
            cell.border = thin_border
            if c_idx in [3, 4, 5]:
                cell.alignment = Alignment(horizontal="center", vertical="center")
            else:
                cell.alignment = Alignment(vertical="center")

    # Ajustement automatique des largeurs de colonnes
    col_widths = [26, 28, 22, 18, 18, 26, 45, 26]
    for idx, width in enumerate(col_widths, start=1):
        col_letter = get_column_letter(idx)
        ws.column_dimensions[col_letter].width = width

    # Feuille 2 : Documentation des types acceptés
    ws_doc = wb.create_sheet(title="Types Autorisés")
    doc_headers = ["Type Technique", "Description Métier", "Exemples d'Usage"]
    ws_doc.append(doc_headers)
    ws_doc.row_dimensions[1].height = 26
    for c in range(1, 4):
        ws_doc.cell(row=1, column=c).font = header_font
        ws_doc.cell(row=1, column=c).fill = PatternFill(start_color="3B82F6", end_color="3B82F6", fill_type="solid")

    type_doc = [
        ["text", "Texte court simple", "Nom, code matricule, référence"],
        ["textarea", "Texte multi-lignes", "Observations, description, commentaires"],
        ["number", "Nombre générique", "Quantité, capacité"],
        ["integer", "Entier strict", "Nombre d'enfants, sacs, arbres"],
        ["decimal", "Nombre décimal à virgule", "Superficie en ha, poids en kg, rendement"],
        ["boolean", "Booléen Oui/Non", "Certifié, Actif, Validé"],
        ["currency", "Montant financier avec devise", "Prix d'achat, avance, prime"],
        ["percentage", "Pourcentage", "Taux d'humidité, pureté"],
        ["date", "Date au format AAAA-MM-JJ", "Date de naissance, date de récolte"],
        ["datetime", "Date et heure complète", "Horodatage de pesée"],
        ["time", "Heure au format HH:MM", "Heure d'ouverture, tournée"],
        ["email", "Adresse électronique", "contact@coop.ci"],
        ["phone", "Téléphone", "+225 0700000000"],
        ["url", "Lien Internet", "Fiche technique web"],
        ["select", "Choix unique parmi une liste", "Statut, grade de cacao (I, II)"],
        ["multiselect", "Choix multiples", "Cultures associées (Café, Cacao, Hévéa)"],
        ["file", "Document joint", "Contrat PDF, reçu"],
        ["image", "Photo", "Photo du planteur, état du champ"],
        ["audio", "Fichier audio / Enregistrement vocal", "Accord verbal, interview"],
        ["video", "Fichier vidéo / Enregistrement visuel", "Inspection visuelle par drone"],
        ["geo_point", "Coordonnées GPS (Lat, Long)", "Localisation exacte du magasin"],
        ["geo_polygon", "Contour géographique parcelle", "Polygone GeoJSON de la parcelle"],
        ["barcode", "Code-barres / QR Code", "QR code sur le sac de cacao"],
        ["color", "Code couleur HEX", "Code visuel catégorie (#10B981)"],
        ["json", "Objet structuré JSON libre", "Paramètres bruts IoT"],
        ["relation", "Liaison vers une autre ressource", "Lien Planteur -> Coopérative"],
    ]

    for d in type_doc:
        ws_doc.append(d)

    ws_doc.column_dimensions["A"].width = 20
    ws_doc.column_dimensions["B"].width = 38
    ws_doc.column_dimensions["C"].width = 45

    output = io.BytesIO()
    wb.save(output)
    return output.getvalue()


def reconcile_schema_coherence(
    existing_fields: List[Dict[str, Any]],
    incoming_fields: List[Dict[str, Any]],
    existing_items: Optional[List[Dict[str, Any]]] = None,
    current_version: str = "1.0.0",
    is_active: bool = False,
) -> Dict[str, Any]:
    """
    Rapproche un schéma existant et des champs entrants (Excel/JSON)
    en garantissant l'intégrité, l'authenticité et la cohérence absolue avec les données existantes.
    """
    items = existing_items or []
    item_count = len(items)

    existing_map = {f["name"]: dict(f) for f in existing_fields}
    incoming_seen = set()

    added_fields: List[Dict[str, Any]] = []
    updated_fields: List[Dict[str, Any]] = []
    identical_fields: List[str] = []
    ignored_fields: List[Dict[str, Any]] = []

    warnings: List[str] = []
    errors: List[str] = []

    resulting_map = dict(existing_map)

    for incoming in incoming_fields:
        name = incoming.get("name")
        if not name:
            continue

        if name in incoming_seen:
            warnings.append(f"Champ en double dans le fichier importé: '{name}'. La dernière occurrence a été prise en compte.")
        incoming_seen.add(name)

        # 1. Nouveau champ (AJOUT)
        if name not in existing_map:
            # Vérification de cohérence avec les données existantes
            is_req = incoming.get("required", False)
            def_val = incoming.get("default_value")
            is_uniq = incoming.get("unique", False)

            if is_req and item_count > 0:
                if def_val is None or def_val == "":
                    errors.append(
                        f"Incohérence: Le nouveau champ '{incoming.get('label') or name}' est marqué 'Obligatoire', "
                        f"mais la ressource contient déjà {item_count} enregistrement(s) sans valeur par défaut définie. "
                        f"Veuillez spécifier une valeur par défaut pour combler les enregistrements existants."
                    )
                else:
                    warnings.append(
                        f"Le champ obligatoire '{incoming.get('label') or name}' sera injecté avec la valeur par défaut "
                        f"'{def_val}' pour les {item_count} enregistrement(s) existant(s)."
                    )

            if is_uniq and item_count > 1 and (def_val is not None and def_val != ""):
                errors.append(
                    f"Incohérence: Le champ '{name}' est marqué 'Unique' mais a une valeur par défaut statique ('{def_val}'). "
                    f"Cela créerait {item_count} doublons dans les données existantes."
                )

            added_fields.append(incoming)
            resulting_map[name] = incoming

        # 2. Champ existant (MISE À JOUR ou IDENTIQUE)
        else:
            old_f = existing_map[name]
            # Comparer si quelque chose a changé
            diffs = {}
            for k in ["label", "type", "required", "unique", "options", "default_value", "target_resource_code", "display_field"]:
                old_val = old_f.get(k)
                new_val = incoming.get(k)
                # Tolérance pour listes d'options
                if isinstance(old_val, list) and isinstance(new_val, list):
                    if sorted(old_val) != sorted(new_val):
                        diffs[k] = (old_val, new_val)
                elif old_val != new_val and new_val is not None:
                    diffs[k] = (old_val, new_val)

            if not diffs:
                identical_fields.append(name)
            else:
                # Vérifier si les modifications préservent la cohérence absolue des données existantes
                is_coherent = True

                # Changement de type ?
                if "type" in diffs and item_count > 0:
                    old_t, new_t = diffs["type"]
                    # Tester si les données existantes peuvent être converties
                    incompatible_samples = []
                    for item in items:
                        val = item.get("data", {}).get(name)
                        if val is not None and val != "":
                            if new_t in ["number", "integer", "decimal"]:
                                try:
                                    float(str(val).replace(",", "."))
                                except ValueError:
                                    incompatible_samples.append(str(val))
                                    break
                            elif new_t == "boolean":
                                if str(val).lower() not in ["true", "false", "0", "1", "oui", "non"]:
                                    incompatible_samples.append(str(val))
                                    break

                    if incompatible_samples:
                        is_coherent = False
                        errors.append(
                            f"Incohérence type: Impossible de convertir le champ '{name}' de '{old_t}' vers '{new_t}'. "
                            f"Des données existantes incompatibles ont été détectées (ex: '{incompatible_samples[0]}')."
                        )

                # Passage à Unique = True ?
                if "unique" in diffs and diffs["unique"][1] is True and item_count > 0:
                    seen_values = {}
                    duplicates = []
                    for item in items:
                        val = item.get("data", {}).get(name)
                        if val is not None and val != "":
                            sval = str(val).strip().lower()
                            if sval in seen_values:
                                duplicates.append(str(val))
                            seen_values[sval] = item.get("id")

                    if duplicates:
                        is_coherent = False
                        errors.append(
                            f"Incohérence unicité: Impossible de rendre le champ '{name}' unique car {len(duplicates)} "
                            f"doublon(s) existe(nt) déjà dans les enregistrements existants (ex: '{duplicates[0]}')."
                        )

                # Passage à Obligatoire = True ?
                if "required" in diffs and diffs["required"][1] is True and item_count > 0:
                    missing_count = sum(
                        1 for it in items
                        if it.get("data", {}).get(name) is None or it.get("data", {}).get(name) == ""
                    )
                    if missing_count > 0:
                        new_def = incoming.get("default_value")
                        if new_def is None or new_def == "":
                            is_coherent = False
                            errors.append(
                                f"Incohérence obligation: Le champ '{name}' contient {missing_count} valeur(s) vide(s) "
                                f"dans les données actuelles. Veuillez fournir une valeur par défaut pour combler les vides."
                            )
                        else:
                            warnings.append(
                                f"Le champ '{name}' rendu obligatoire sera complété par '{new_def}' pour {missing_count} ligne(s) vide(s)."
                            )

                if is_coherent:
                    # Mettre à jour le champ en fusionnant les propriétés
                    merged = dict(old_f)
                    merged.update(incoming)
                    updated_fields.append(merged)
                    resulting_map[name] = merged
                else:
                    ignored_fields.append({
                        "field": incoming,
                        "reason": "Modifications rejetées pour préserver la cohérence des données existantes."
                    })

    # Calcul de la nouvelle version si active et modifications effectives
    new_version = current_version
    has_changes = bool(added_fields or updated_fields)
    if is_active and has_changes:
        try:
            parts = current_version.split(".")
            if len(parts) >= 2:
                minor = int(parts[1]) + 1
                new_version = f"{parts[0]}.{minor}.0"
            else:
                new_version = f"{current_version}.1"
        except Exception:
            new_version = f"{current_version}-v2"

    resulting_schema = list(resulting_map.values())

    return {
        "is_coherent": len(errors) == 0,
        "added_count": len(added_fields),
        "updated_count": len(updated_fields),
        "identical_count": len(identical_fields),
        "ignored_count": len(ignored_fields),
        "added_fields": added_fields,
        "updated_fields": updated_fields,
        "identical_fields": identical_fields,
        "ignored_fields": ignored_fields,
        "warnings": warnings,
        "errors": errors,
        "resulting_schema": resulting_schema,
        "new_version": new_version if has_changes else current_version,
    }


def generate_resource_data_template(resource_name: str, schema_fields: List[Dict[str, Any]]) -> bytes:
    """
    Génère un classeur Excel (.xlsx) pré-formaté spécifique aux champs de la ressource.
    Idéal pour préparer les données avant import.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Import Données"

    # Ligne 1 : Clés techniques des champs
    headers = [str(f.get("name") or "") for f in schema_fields]
    ws.append(headers)

    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="10B981", end_color="10B981", fill_type="solid")
    center_align = Alignment(horizontal="center", vertical="center")

    ws.row_dimensions[1].height = 26
    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = center_align

    # Ligne 2 : Libellé et indications de type (obligatoire, unique, auto-incrément)
    subtitle_font = Font(name="Calibri", size=9, italic=True, color="475569")
    subtitle_fill = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")
    
    subtitles = []
    for f in schema_fields:
        parts = [f.get("label") or f.get("name", "")]
        tags = [f.get("type", "text")]
        if f.get("auto_increment"):
            tags.append("AUTO-INC")
        if f.get("required"):
            tags.append("REQUIS")
        if f.get("unique"):
            tags.append("UNIQUE")
        parts.append(f"({', '.join(tags)})")
        subtitles.append(" ".join(parts))
        
    ws.append(subtitles)
    ws.row_dimensions[2].height = 20
    for col_idx in range(1, len(subtitles) + 1):
        cell = ws.cell(row=2, column=col_idx)
        cell.font = subtitle_font
        cell.fill = subtitle_fill
        cell.alignment = center_align

    # Ligne 3 : Ligne d'exemple réaliste
    sample_row = []
    for f in schema_fields:
        ftype = f.get("type", "text")
        if f.get("auto_increment"):
            sample_row.append(1)
        elif ftype in ["integer", "number"]:
            sample_row.append(f.get("default_value") if f.get("default_value") is not None else 100)
        elif ftype in ["decimal", "currency", "percentage"]:
            sample_row.append(f.get("default_value") if f.get("default_value") is not None else 18.5)
        elif ftype == "boolean":
            sample_row.append("OUI" if f.get("default_value") else "NON")
        elif ftype == "date":
            sample_row.append("2026-10-19")
        elif ftype == "datetime":
            sample_row.append("2026-10-19 14:30:00")
        elif ftype == "time":
            sample_row.append("14:30")
        elif ftype == "email":
            sample_row.append("contact@exemple.com")
        elif ftype == "phone":
            sample_row.append("+225 0700000000")
        elif ftype == "select" and f.get("options"):
            sample_row.append(f["options"][0])
        elif ftype == "barcode":
            sample_row.append("7613034928123")
        elif ftype == "color":
            sample_row.append("#10B981")
        elif ftype == "geo_point":
            sample_row.append("5.345,-4.024")
        else:
            sample_row.append(f"Exemple {f.get('label') or f.get('name')}")

    ws.append(sample_row)
    ws.row_dimensions[3].height = 20

    for col_idx in range(1, len(headers) + 1):
        col_letter = get_column_letter(col_idx)
        ws.column_dimensions[col_letter].width = max(len(headers[col_idx - 1]) + 6, 20)

    output = io.BytesIO()
    wb.save(output)
    return output.getvalue()


def parse_data_file(file_bytes: bytes, filename: str, schema_fields: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Extrait les lignes d'un fichier de données (.xlsx, .xls, .csv, .json) en mappant
    chaque colonne sur les champs du schéma de la ressource.
    """
    fname = filename.lower()
    field_map = {}
    for f in schema_fields:
        name = f.get("name", "").strip().lower()
        label = (f.get("label") or "").strip().lower()
        field_map[name] = f.get("name")
        if label:
            field_map[label] = f.get("name")

    rows_data: List[Dict[str, Any]] = []

    if fname.endswith(".json"):
        raw = json.loads(file_bytes.decode("utf-8"))
        if isinstance(raw, list):
            rows_data = [r for r in raw if isinstance(r, dict)]
        elif isinstance(raw, dict) and "items" in raw and isinstance(raw["items"], list):
            rows_data = [r for r in raw["items"] if isinstance(r, dict)]

    elif fname.endswith(".xlsx") or fname.endswith(".xls"):
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
        ws = wb.active
        all_rows = list(ws.iter_rows(values_only=True))
        if not all_rows:
            return []

        raw_headers = [str(h or "").strip() for h in all_rows[0]]
        mapped_headers: List[Optional[str]] = []
        for h in raw_headers:
            clean_h = h.lower()
            mapped = field_map.get(clean_h)
            if not mapped:
                # Recherche partielle
                for k, target in field_map.items():
                    if k in clean_h or clean_h in k:
                        mapped = target
                        break
            mapped_headers.append(mapped)

        # Vérifier si la ligne 2 est une ligne d'indications (ex: sous-titres avec types)
        start_row = 1
        if len(all_rows) > 1:
            r1_str = " ".join(str(cell or "").lower() for cell in all_rows[1])
            if any(t in r1_str for t in ["text", "integer", "decimal", "requis", "auto-inc", "unique"]):
                start_row = 2

        for row in all_rows[start_row:]:
            if not row or all(v is None or str(v).strip() == "" for v in row):
                continue
            row_dict = {}
            for idx, val in enumerate(row):
                if idx < len(mapped_headers) and mapped_headers[idx]:
                    row_dict[mapped_headers[idx]] = val
            if row_dict:
                rows_data.append(row_dict)

    elif fname.endswith(".csv"):
        for encoding in ["utf-8-sig", "utf-8", "latin-1"]:
            try:
                decoded = file_bytes.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            decoded = file_bytes.decode("utf-8", errors="ignore")

        first_line = decoded.splitlines()[0] if decoded.splitlines() else ""
        delim = ";" if ";" in first_line else ","
        reader = csv.reader(io.StringIO(decoded), delimiter=delim)
        raw_rows = list(reader)
        if not raw_rows:
            return []

        raw_headers = [str(h or "").strip() for h in raw_rows[0]]
        mapped_headers = []
        for h in raw_headers:
            clean_h = h.lower()
            mapped = field_map.get(clean_h)
            mapped_headers.append(mapped)

        start_row = 1
        if len(raw_rows) > 1:
            r1_str = " ".join(str(cell or "").lower() for cell in raw_rows[1])
            if any(t in r1_str for t in ["text", "integer", "decimal", "requis", "auto-inc", "unique"]):
                start_row = 2

        for row in raw_rows[start_row:]:
            if not row or all(str(v or "").strip() == "" for v in row):
                continue
            row_dict = {}
            for idx, val in enumerate(row):
                if idx < len(mapped_headers) and mapped_headers[idx]:
                    row_dict[mapped_headers[idx]] = val.strip()
            if row_dict:
                rows_data.append(row_dict)

    return rows_data


def validate_and_reconcile_data_rows(
    schema_fields: List[Dict[str, Any]],
    incoming_rows: List[Dict[str, Any]],
    existing_items: List[Dict[str, Any]],
    update_existing: bool = True,
) -> Dict[str, Any]:
    """
    Analyse de manière stricte l'intégralité d'un fichier de données avant insertion.
    Garantit l'intégrité, l'authenticité et applique la logique d'Upsert conditionnel.
    """
    errors: List[str] = []
    warnings: List[str] = []

    if not incoming_rows:
        return {
            "is_valid": False,
            "can_import": False,
            "total_rows": 0,
            "to_insert_count": 0,
            "to_update_count": 0,
            "ignored_count": 0,
            "key_field": None,
            "update_existing": update_existing,
            "errors": ["Le fichier importé ne contient aucune ligne de données exploitable."],
            "warnings": [],
            "sample_preview": [],
            "to_insert": [],
            "to_update": [],
            "ignored": [],
        }

    # Identifier la clé unique de référence
    key_field = None
    for f in schema_fields:
        if f.get("auto_increment"):
            key_field = f.get("name")
            break
        if f.get("unique") and not key_field:
            key_field = f.get("name")

    if not key_field and schema_fields:
        key_field = schema_fields[0].get("name")

    # Index des enregistrements existants par clé
    existing_by_key = {}
    if key_field:
        for it in existing_items:
            k_val = it.get("data", {}).get(key_field)
            if k_val is not None and str(k_val).strip() != "":
                existing_by_key[str(k_val).strip().lower()] = it

    seen_keys_in_file = set()
    to_insert: List[Dict[str, Any]] = []
    to_update: List[Dict[str, Any]] = []
    ignored: List[Dict[str, Any]] = []

    for row_idx, raw_row in enumerate(incoming_rows, start=1):
        line_num = row_idx + 1  # Ligne 1 = en-tête
        coerced_row: Dict[str, Any] = {}

        # Validation de chaque champ par rapport au schéma
        for f in schema_fields:
            fname = f.get("name")
            ftype = f.get("type", "text")
            is_req = bool(f.get("required"))
            is_auto = bool(f.get("auto_increment"))
            default_val = f.get("default_value")
            val = raw_row.get(fname)

            # Valeur vide ou omise
            if val is None or str(val).strip() == "":
                if is_auto:
                    # L'auto-incrément sera pris en charge par le système
                    coerced_row[fname] = None
                    continue
                if is_req:
                    if default_val is not None and default_val != "":
                        val = default_val
                    else:
                        errors.append(
                            f"Ligne {line_num} : Le champ obligatoire « {f.get('label') or fname} » est vide ou absent."
                        )
                        continue
                else:
                    coerced_row[fname] = default_val if default_val is not None else None
                    continue

            # Coercition et validation de type
            sval = str(val).strip()
            try:
                if ftype in ["integer"]:
                    coerced_row[fname] = int(float(sval.replace(",", ".")))
                elif ftype in ["number", "decimal", "currency", "percentage"]:
                    coerced_row[fname] = float(sval.replace(",", "."))
                elif ftype == "boolean":
                    if isinstance(val, bool):
                        coerced_row[fname] = val
                    else:
                        coerced_row[fname] = sval.lower() in ["true", "1", "oui", "vrai", "yes"]
                elif ftype in ["date", "datetime"]:
                    coerced_row[fname] = sval
                elif ftype == "select" and f.get("options"):
                    valid_opts = [str(o).strip().lower() for o in f["options"]]
                    if sval.lower() not in valid_opts:
                        errors.append(
                            f"Ligne {line_num} : « {sval} » n'est pas une option valide pour « {f.get('label')} ». "
                            f"Options valides : {', '.join(f['options'])}."
                        )
                    else:
                        # Rétablir la casse d'origine
                        match_opt = next(o for o in f["options"] if str(o).strip().lower() == sval.lower())
                        coerced_row[fname] = match_opt
                else:
                    coerced_row[fname] = val
            except (ValueError, TypeError):
                errors.append(
                    f"Ligne {line_num} : Format invalide pour le champ « {f.get('label')} » ({fname}). "
                    f"Valeur reçue : « {val} », type attendu : « {ftype} »."
                )

        # Contrôle des doublons internes au fichier
        if key_field and coerced_row.get(key_field) is not None:
            clean_k = str(coerced_row[key_field]).strip().lower()
            if clean_k in seen_keys_in_file:
                errors.append(
                    f"Ligne {line_num} : Clé en double « {coerced_row[key_field]} » détectée plusieurs fois dans le même fichier."
                )
            seen_keys_in_file.add(clean_k)

        # Réconciliation avec la base existante si aucune erreur sur cette ligne
        if key_field:
            row_k = coerced_row.get(key_field)
            clean_k = str(row_k).strip().lower() if row_k is not None else None
            if clean_k and clean_k in existing_by_key:
                existing_item = existing_by_key[clean_k]
                if update_existing:
                    # Identifier les champs modifiés
                    diffs = {}
                    for k, v in coerced_row.items():
                        if existing_item.get("data", {}).get(k) != v and v is not None:
                            diffs[k] = v
                    if diffs:
                        merged = dict(existing_item.get("data", {}))
                        merged.update(diffs)
                        to_update.append({
                            "item_id": existing_item["id"],
                            "data": merged,
                            "changed_fields": list(diffs.keys()),
                        })
                    else:
                        ignored.append({"line": line_num, "key": row_k, "reason": "Ligne strictement identique"})
                else:
                    ignored.append({"line": line_num, "key": row_k, "reason": "Clé déjà existante (mise à jour désactivée)"})
            else:
                to_insert.append(coerced_row)
        else:
            to_insert.append(coerced_row)

    is_valid = len(errors) == 0

    return {
        "is_valid": is_valid,
        "can_import": is_valid,
        "total_rows": len(incoming_rows),
        "to_insert_count": len(to_insert) if is_valid else 0,
        "to_update_count": len(to_update) if is_valid else 0,
        "ignored_count": len(ignored) if is_valid else 0,
        "key_field": key_field,
        "update_existing": update_existing,
        "errors": errors,
        "warnings": warnings,
        "sample_preview": (to_insert[:5] + [u["data"] for u in to_update[:5]])[:5],
        "to_insert": to_insert if is_valid else [],
        "to_update": to_update if is_valid else [],
        "ignored": ignored if is_valid else [],
    }


# ==============================================================================
# FONCTIONS D'EXPORTATION DE DONNÉES ET GESTION DES MÉDIAS (ZIP BUNDLE)
# ==============================================================================
MEDIA_FIELD_TYPES = {"file", "image", "audio", "video"}


def is_media_field(field_type: str) -> bool:
    """Vérifie si un type de champ correspond à un média binaire."""
    return (field_type or "").lower().strip() in MEDIA_FIELD_TYPES


def generate_resource_data_export(
    resource_name: str,
    schema_fields: List[Dict[str, Any]],
    rows: List[Dict[str, Any]],
    format: str = "xlsx"
) -> bytes:
    """
    Exporte les données tabulaires d'une ressource au format spécifié (xlsx, csv, json).
    Les valeurs des champs médias peuvent avoir été préalablement réécrites
    (ex: 'media/planteur_123.jpg') pour pointer vers le sous-dossier de l'archive ZIP.
    """
    fmt = (format or "xlsx").lower().strip()
    field_names = [f["name"] for f in schema_fields]
    field_labels = [f.get("label") or f["name"] for f in schema_fields]

    if fmt == "json":
        # Format JSON structuré
        ordered_rows = []
        for r in rows:
            obj = {}
            for f in schema_fields:
                fname = f["name"]
                obj[fname] = r.get(fname)
            ordered_rows.append(obj)
        return json.dumps(ordered_rows, ensure_ascii=False, indent=2, default=str).encode("utf-8")

    elif fmt == "csv":
        # Format CSV avec encodage utf-8-sig (BOM) pour compatibilité Excel multi-plateforme
        output = io.StringIO()
        writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL)
        # En-têtes : Libellés des champs
        writer.writerow(field_labels)
        # Lignes de données
        for r in rows:
            row_vals = []
            for fname in field_names:
                v = r.get(fname)
                if isinstance(v, (dict, list)):
                    v = json.dumps(v, ensure_ascii=False)
                elif v is None:
                    v = ""
                row_vals.append(v)
            writer.writerow(row_vals)
        return output.getvalue().encode("utf-8-sig")

    else:
        # Format Excel (.xlsx) stylisé SenWiseTool
        wb = openpyxl.Workbook()
        ws = wb.active
        clean_title = re.sub(r'[\\/*?:\[\]]', '_', resource_name or "Données")[:30]
        ws.title = clean_title

        # En-têtes (Ligne 1 : Libellés des champs)
        header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
        header_fill = PatternFill(start_color="10B981", end_color="10B981", fill_type="solid")
        center_align = Alignment(horizontal="center", vertical="center", wrap_text=True)

        ws.append(field_labels)
        ws.row_dimensions[1].height = 28
        for col_idx in range(1, len(field_labels) + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = center_align

        # Données
        for r in rows:
            row_cells = []
            for fname in field_names:
                val = r.get(fname)
                if isinstance(val, (dict, list)):
                    val = json.dumps(val, ensure_ascii=False)
                elif val is None:
                    val = ""
                row_cells.append(val)
            ws.append(row_cells)

        # Ajustement des largeurs de colonnes
        for col in ws.columns:
            max_len = 0
            col_letter = get_column_letter(col[0].column)
            for cell in col:
                val_str = str(cell.value or "")
                if len(val_str) > max_len:
                    max_len = len(val_str)
            ws.column_dimensions[col_letter].width = min(max(max_len + 3, 12), 45)

        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()


def bundle_export_zip(
    data_filename: str,
    data_bytes: bytes,
    media_files: Dict[str, bytes]
) -> bytes:
    """
    Génère une archive .ZIP autonome contenant :
    - Le fichier de données (excel, csv ou json) à la racine de l'archive.
    - Le sous-dossier 'media/' contenant tous les médias binaires réels.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # 1. Écrire le fichier de données principal
        zf.writestr(data_filename, data_bytes)
        # 2. Écrire chaque fichier média dans son chemin relatif (ex: 'media/photo.jpg')
        for zip_relpath, m_bytes in media_files.items():
            zf.writestr(zip_relpath, m_bytes)
    return buf.getvalue()


def generate_resource_schema_export(
    resource: Any,
    format: str = "json"
) -> Tuple[bytes, str, str]:
    """
    Exporte la définition complète du schéma d'une ressource (JSON ou Excel).
    Renvoie (contenu_binaire, nom_fichier, content_type).
    """
    fmt = (format or "json").lower().strip()
    code = getattr(resource, "code", "ressource")
    version = getattr(resource, "version", "1.0.0")

    if fmt == "xlsx":
        # Générer classeur Excel pré-rempli avec les champs réels
        fields = getattr(resource, "schema_fields", []) or []
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = f"Schéma {code}"[:30]

        headers = [
            "Clé Technique (name) *",
            "Libellé Affiché (label) *",
            "Type de Données (type) *",
            "Obligatoire (required)",
            "Valeur Unique (unique)",
            "Auto-incrémenté (auto_increment)",
            "Valeur par Défaut (default_value)",
            "Options (pour select/multiselect)",
            "Ressource Cible (pour relation)",
        ]
        ws.append(headers)
        ws.row_dimensions[1].height = 28
        header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
        header_fill = PatternFill(start_color="10B981", end_color="10B981", fill_type="solid")
        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center", vertical="center")

        for f in fields:
            ws.append([
                f.get("name", ""),
                f.get("label", ""),
                f.get("type", "text"),
                "OUI" if f.get("required") else "NON",
                "OUI" if f.get("unique") else "NON",
                "OUI" if f.get("auto_increment") else "NON",
                str(f.get("default_value") if f.get("default_value") is not None else ""),
                ", ".join(f.get("options", [])) if isinstance(f.get("options"), list) else str(f.get("options") or ""),
                str(f.get("target_resource_id") or ""),
            ])

        for col in ws.columns:
            max_len = max(len(str(cell.value or "")) for cell in col)
            col_letter = get_column_letter(col[0].column)
            ws.column_dimensions[col_letter].width = min(max(max_len + 3, 14), 45)

        buf = io.BytesIO()
        wb.save(buf)
        filename = f"schema_{code}_v{version}.xlsx"
        return buf.getvalue(), filename, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        # JSON standard de haute précision
        schema_dict = {
            "name": getattr(resource, "name", ""),
            "code": getattr(resource, "code", ""),
            "description": getattr(resource, "description", ""),
            "category": getattr(resource, "category", "general"),
            "version": version,
            "is_lts": getattr(resource, "is_lts", False),
            "status": getattr(resource, "status", "draft"),
            "schema_fields": getattr(resource, "schema_fields", []) or [],
        }
        json_bytes = json.dumps(schema_dict, ensure_ascii=False, indent=2).encode("utf-8")
        filename = f"schema_{code}_v{version}.json"
        return json_bytes, filename, "application/json"


