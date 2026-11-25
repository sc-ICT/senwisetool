import io
import os
import re
import json
import csv
import zipfile
import uuid
import secrets
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


DOC_SHEET_KEYWORDS = [
    "guide",
    "regle",
    "règle",
    "regles",
    "règles",
    "instruction",
    "instructions",
    "types autorises",
    "types autorisés",
    "types",
    "documentation",
    "aide",
    "notice",
]


def validate_fields_integrity(fields: List[Dict[str, Any]], resource_name: str = "Ressource") -> None:
    """
    Valide l'intégrité, l'unicité stricte des clés techniques et les règles métier selon les types.
    Lève un ValueError explicite en cas d'incohérence.
    """
    if not fields:
        raise ValueError(f"Le schéma de la ressource « {resource_name} » ne contient aucun champ valide.")

    seen_names = set()
    for idx, f in enumerate(fields, start=1):
        name = f.get("name")
        label = f.get("label") or name
        ftype = f.get("type") or "text"

        if not name:
            raise ValueError(f"Champ #{idx} dans « {resource_name} » : la clé technique (name) est obligatoire.")

        # Règle d'unicité absolue de la clé technique au sein de la ressource
        if name in seen_names:
            raise ValueError(
                f"Doublon critique dans « {resource_name} » : la clé technique « {name} » (champ « {label} ») "
                f"est définie plusieurs fois. Chaque clé technique doit être strictement unique au sein de la ressource."
            )
        seen_names.add(name)

        # Vérification du type
        if ftype not in ALL_RESOURCE_TYPES:
            raise ValueError(
                f"Champ « {name} » dans « {resource_name} » : le type de données « {ftype} » n'est pas autorisé. "
                f"Types valides : {', '.join(ALL_RESOURCE_TYPES[:8])}..."
            )

        # Règle spécifique pour select / multiselect : options obligatoires
        if ftype in ["select", "multiselect"]:
            options = f.get("options")
            if not options or not isinstance(options, list) or len(options) == 0:
                raise ValueError(
                    f"Champ « {name} » (type {ftype}) dans « {resource_name} » : vous devez renseigner au moins "
                    f"une option dans la liste des choix possibles."
                )

        # Règle spécifique pour relation : ressource cible obligatoire
        if ftype == "relation":
            target = f.get("target_resource_code") or f.get("target_resource_id")
            if not target:
                raise ValueError(
                    f"Champ « {name} » (type relation) dans « {resource_name} » : vous devez obligatoirement "
                    f"spécifier la ressource cible liée (colonne 'Ressource Cible')."
                )

        # Règle numérique : validation de la valeur par défaut si présente
        if ftype in ["integer", "number", "decimal", "currency", "percentage"]:
            def_val = f.get("default_value")
            if def_val is not None and str(def_val).strip() != "":
                try:
                    float(str(def_val).replace(",", "."))
                except (ValueError, TypeError):
                    raise ValueError(
                        f"Champ « {name} » (type {ftype}) dans « {resource_name} » : la valeur par défaut "
                        f"« {def_val} » n'est pas un nombre valide."
                    )


def parse_single_sheet_rows(rows: List[Tuple[Any, ...]], sheet_name: str) -> List[Dict[str, Any]]:
    """Parse les lignes d'une feuille Excel spécifique et extrait ses champs."""
    if not rows:
        return []

    headers = [str(h or "").strip().lower() for h in rows[0]]

    def find_col_idx(aliases: List[str]) -> Optional[int]:
        for alias in aliases:
            for idx, h in enumerate(headers):
                if alias in h:
                    return idx
        return None

    idx_name = find_col_idx(["name", "code", "cle", "clé", "champ", "technique"])
    idx_label = find_col_idx(["label", "libelle", "libellé", "titre", "nom"])
    idx_type = find_col_idx(["type", "format", "genre"])
    idx_required = find_col_idx(["required", "obligatoire", "requis", "oblig"])
    idx_unique = find_col_idx(["unique", "unicite", "unicité", "anti-doublon", "doublon"])
    idx_default = find_col_idx(["default", "defaut", "défaut", "valeur_defaut"])
    idx_options = find_col_idx(["option", "choix", "valeurs"])
    idx_target = find_col_idx(["cible", "target", "relation", "ressource_liee", "ressource liée"])
    idx_display = find_col_idx(["affiche", "affiché", "display", "colonne_affichage", "champ_affiche"])

    fields: List[Dict[str, Any]] = []

    for row in rows[1:]:
        if not row or all(v is None or str(v).strip() == "" for v in row):
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
        display_col = str(row[idx_display]).strip() if idx_display is not None and idx_display < len(row) and row[idx_display] else None

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
            "display_field": display_col or ("nom_complet" if target_res else None),
        })

    validate_fields_integrity(fields, resource_name=sheet_name)
    return fields


def parse_workbook_sheets(file_bytes: bytes) -> Dict[str, List[Dict[str, Any]]]:
    """
    Parse toutes les feuilles de données d'un classeur Excel (ignore les feuilles d'instructions).
    Retourne un dictionnaire {nom_ressource_ou_feuille: liste_champs}.
    """
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    all_sheets: Dict[str, List[Dict[str, Any]]] = {}

    data_sheet_names = []
    for s_name in wb.sheetnames:
        clean_name = s_name.strip().lower()
        if any(kw in clean_name for kw in DOC_SHEET_KEYWORDS):
            continue
        data_sheet_names.append(s_name)

    # Si aucune feuille de données identifiée, on prend la feuille active
    if not data_sheet_names and wb.sheetnames:
        data_sheet_names = [wb.active.title]

    for s_name in data_sheet_names:
        sheet = wb[s_name]
        rows = list(sheet.iter_rows(values_only=True))
        if not rows:
            continue
        parsed_fields = parse_single_sheet_rows(rows, sheet_name=s_name)
        if parsed_fields:
            normalized_key = normalize_field_name(s_name) or s_name.strip().lower()
            all_sheets[normalized_key] = parsed_fields

    return all_sheets


def compute_resource_creation_order(
    sheets_dict: Dict[str, List[Dict[str, Any]]],
    existing_db_codes: Optional[List[str]] = None,
) -> Tuple[List[str], Dict[str, List[str]]]:
    """
    Calcule l'ordre topologique de création des ressources selon les dépendances de type 'relation'.
    Garantit que la ressource maîtresse (ex: Planteurs) est créée AVANT la ressource dépendante (ex: Plantations).
    Vérifie qu'aucune dépendance circulaire n'existe.
    """
    existing_codes = set((existing_db_codes or []))
    available_in_file = set(sheets_dict.keys())
    
    # dependencies: ressource -> list of resources it depends on
    dependencies: Dict[str, List[str]] = {k: [] for k in sheets_dict.keys()}

    for sheet_name, fields in sheets_dict.items():
        for f in fields:
            if f.get("type") == "relation":
                target = normalize_field_name(f.get("target_resource_code") or "")
                if not target:
                    continue
                # Vérifier si la cible existe dans le fichier ou en BDD
                if target not in available_in_file and target not in existing_codes:
                    # Permettre aussi correspondance sans préfixe
                    target_match = any(target in s for s in available_in_file) or any(target in c.lower() for c in existing_codes)
                    if not target_match:
                        raise ValueError(
                            f"Incohérence de liaison dans la ressource « {sheet_name} » : "
                            f"Le champ « {f.get('name')} » référence la ressource cible « {target} », "
                            f"mais celle-ci n'existe ni dans le classeur Excel ni parmi les ressources existantes."
                        )
                
                if target in available_in_file and target != sheet_name:
                    if target not in dependencies[sheet_name]:
                        dependencies[sheet_name].append(target)

    # Tri topologique (Kahn's algorithm)
    in_degree = {k: len(deps) for k, deps in dependencies.items()}
    queue = [k for k, deg in in_degree.items() if deg == 0]
    ordered: List[str] = []

    while queue:
        curr = queue.pop(0)
        ordered.append(curr)

        for other, deps in dependencies.items():
            if curr in deps:
                in_degree[other] -= 1
                if in_degree[other] == 0:
                    queue.append(other)

    if len(ordered) < len(sheets_dict):
        cyclic = [k for k, deg in in_degree.items() if deg > 0]
        raise ValueError(
            f"Dépendance circulaire détectée entre les ressources du fichier : {', '.join(cyclic)}. "
            f"Deux ressources ne peuvent pas dépendre mutuellement l'une de l'autre lors de la création initiale."
        )

    return ordered, dependencies


def parse_schema_from_bytes(file_bytes: bytes, filename: str) -> List[Dict[str, Any]]:
    """
    Parse un fichier Excel (.xlsx, .xls), CSV (.csv) ou JSON (.json)
    et extrait une liste de définitions de champs normalisées et strictement vérifiées.
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
            raise ValueError("Le fichier JSON doit contenir une liste de champs sous 'schema_fields' ou 'fields'.")

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

        validate_fields_integrity(fields, resource_name=filename)

    elif fname.endswith(".xlsx") or fname.endswith(".xls"):
        # Parsing multi-feuilles avec détection d'ordre et validation de cohérence
        sheets = parse_workbook_sheets(file_bytes)
        if not sheets:
            raise ValueError("Le classeur Excel ne contient aucune feuille de données exploitable.")

        # Si le fichier a plusieurs feuilles interdépendantes, calculer l'ordre et valider
        ordered_keys, _ = compute_resource_creation_order(sheets)

        # Si l'utilisateur importe pour une ressource spécifique, on prend la feuille prioritaire (ou 1ère feuille ordonnée)
        primary_key = ordered_keys[0] if ordered_keys else list(sheets.keys())[0]
        fields = sheets[primary_key]

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

        reader = csv.DictReader(decoded.splitlines(), delimiter=delim)
        for row in reader:
            cleaned_row = {k.strip().lower(): v for k, v in row.items() if k}
            raw_name = (
                cleaned_row.get("name")
                or cleaned_row.get("code")
                or cleaned_row.get("cle")
                or cleaned_row.get("clé")
                or cleaned_row.get("cle technique")
                or ""
            )
            raw_label = (
                cleaned_row.get("label")
                or cleaned_row.get("libelle")
                or cleaned_row.get("libellé")
                or cleaned_row.get("nom")
                or raw_name
            )

            name = normalize_field_name(raw_name or raw_label)
            if not name:
                continue

            ftype = normalize_field_type(cleaned_row.get("type") or "text")
            req = parse_boolean(cleaned_row.get("required") or cleaned_row.get("obligatoire"))
            uniq = parse_boolean(cleaned_row.get("unique") or cleaned_row.get("unicite") or cleaned_row.get("unicité"))
            def_val = cleaned_row.get("default_value") or cleaned_row.get("defaut") or cleaned_row.get("défaut")
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
                "target_resource_code": cleaned_row.get("target_resource") or cleaned_row.get("cible") or cleaned_row.get("ressource_liee"),
                "relation_type": "many_to_one",
                "display_field": cleaned_row.get("display_field") or cleaned_row.get("champ_affiche"),
            })

        validate_fields_integrity(fields, resource_name=filename)

    else:
        raise ValueError("Format de fichier non supporté. Veuillez importer un fichier .xlsx, .csv ou .json.")

    return fields


def generate_schema_excel_template() -> bytes:
    """
    Génère un classeur Excel modèle officiel multi-feuilles :
    - Feuille 1 : 📘 Guide & Règles (Pédagogique, contraintes strictes, gestion des relations et dépendances)
    - Feuille 2 : Planteurs (Exemple de ressource racine maîtresse)
    - Feuille 3 : Plantations (Exemple de ressource liée à Planteurs avec dépendance et ordre de création)
    - Feuille 4 : Types Autorisés (Guide exhaustif des 26 types avec descriptions et exemples)
    """
    wb = openpyxl.Workbook()

    # Styles réutilisables
    font_title = Font(name="Calibri", size=15, bold=True, color="1E293B")
    font_subtitle = Font(name="Calibri", size=10, italic=True, color="64748B")
    font_sec = Font(name="Calibri", size=11, bold=True, color="0F172A")
    font_text = Font(name="Calibri", size=10, color="334155")
    font_bold = Font(name="Calibri", size=10, bold=True, color="1E293B")
    font_code = Font(name="Consolas", size=9, bold=True, color="047857")

    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    fill_header_green = PatternFill(start_color="059669", end_color="059669", fill_type="solid")
    fill_header_blue = PatternFill(start_color="2563EB", end_color="2563EB", fill_type="solid")
    fill_header_purple = PatternFill(start_color="7C3AED", end_color="7C3AED", fill_type="solid")
    fill_subtle_gray = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")

    align_center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    align_left = Alignment(horizontal="left", vertical="center")
    align_top_left = Alignment(horizontal="left", vertical="top", wrap_text=True)

    thin_border = Border(
        left=Side(style="thin", color="E2E8F0"),
        right=Side(style="thin", color="E2E8F0"),
        top=Side(style="thin", color="E2E8F0"),
        bottom=Side(style="thin", color="E2E8F0"),
    )

    # =========================================================================
    # FEUILLE 1 : GUIDE & RÈGLES DE CONCEPTION
    # =========================================================================
    ws_guide = wb.active
    ws_guide.title = "📘 Guide & Règles"
    ws_guide.views.sheetView[0].showGridLines = True

    guide_lines = [
        ("GUIDE ET RÈGLES DE DÉFINITION D'UN SCHÉMA DE RESSOURCE SENWISETOOL", font_title),
        ("Ce document détaille les règles impératives à respecter lors de la création ou l'import de ressources de données.", font_subtitle),
        ("", font_text),
        ("1. RÈGLE D'UNICITÉ STRICTE DES CLÉS TECHNIQUES (name) :", font_sec),
        ("• Chaque colonne d'une ressource doit posséder une « Clé Technique » unique en minuscules (ex: nom_complet, contact_phone).", font_text),
        ("• Deux champs ne peuvent en aucun cas partager la même clé technique au sein d'une même ressource.", font_text),
        ("• Les clés techniques sont immuables : elles servent d'identifiants informatiques pour les formulaires, exports et requêtes.", font_text),
        ("", font_text),
        ("2. GESTION DES RELATIONS ET DÉPENDANCES INTER-RESSOURCES (type = 'relation') :", font_sec),
        ("• Si un champ est de type 'relation', il fait référence à une autre ressource existante ou définie dans le même classeur.", font_text),
        ("• Colonne « Ressource Cible » : Indiquez le code ou le nom de la feuille cible (ex: 'planteurs').", font_text),
        ("• Colonne « Champ Affiché Cible » : Indiquez le champ utilisé pour afficher la relation (ex: 'nom_complet' ou 'matricule').", font_text),
        ("• ORDRE DE CRÉATION AUTOMATIQUE : Si votre fichier contient plusieurs feuilles interdépendantes :", font_bold),
        ("    - La feuille racine maîtresse (ex: 'Planteurs') doit être créée d'abord.", font_text),
        ("    - La feuille dépendante (ex: 'Plantations') est créée ensuite car elle a besoin de relier chaque plantation à son planteur.", font_text),
        ("    - Notre moteur SenWiseTool détecte automatiquement cette arborescence et ordonne la création sans erreur.", font_text),
        ("", font_text),
        ("3. RÈGLES PAR TYPE DE DONNÉES :", font_sec),
        ("• Listes déroulantes (select & multiselect) : La colonne « Options » est OBLIGATOIRE, séparée par des virgules (ex: A, B, C).", font_text),
        ("• Numériques (integer, decimal, currency, percentage) : La valeur par défaut éventuelle doit être un nombre valide.", font_text),
        ("• Booléens (boolean) : Utilisez 'VRAI' ou 'FAUX' (ou 'OUI'/'NON').", font_text),
        ("• Géolocalisation (geo_point, geo_polygon) : Pour coordonnées GPS ou polygones de parcelles agricoles.", font_text),
        ("• Traçabilité (barcode) : Pour codes QR et codes-barres de lots ou de sacs.", font_text),
        ("", font_text),
        ("Consultez la feuille « Types Autorisés » pour la documentation exhaustive des 26 types supportés.", font_bold),
    ]

    for r_idx, (text, fnt) in enumerate(guide_lines, start=1):
        ws_guide.cell(row=r_idx, column=1, value=text).font = fnt
        ws_guide.row_dimensions[r_idx].height = 22 if fnt in [font_title, font_sec] else 18

    ws_guide.column_dimensions["A"].width = 110

    # Colonnes standards pour les feuilles de données
    headers_data = [
        "Clé Technique (name) *",
        "Libellé Affiché (label) *",
        "Type de Données (type) *",
        "Obligatoire (required)",
        "Valeur Unique (unique)",
        "Valeur par Défaut (default_value)",
        "Options (pour select / multiselect)",
        "Ressource Cible (pour relation)",
        "Champ Affiché Cible (display_field)",
    ]

    def apply_data_sheet_styling(ws, header_fill):
        ws.append(headers_data)
        ws.row_dimensions[1].height = 28
        for col_idx in range(1, len(headers_data) + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = align_center

        widths = [26, 28, 22, 18, 18, 26, 45, 26, 26]
        for idx, w in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(idx)].width = w

    # =========================================================================
    # FEUILLE 2 : EXEMPLE 1 - PLANTEURS (Ressource Racine Maîtresse)
    # =========================================================================
    ws_planteurs = wb.create_sheet(title="Planteurs")
    apply_data_sheet_styling(ws_planteurs, fill_header_green)

    planteurs_rows = [
        ["matricule", "Matricule Planteur", "text", "OUI", "OUI", "", "", "", ""],
        ["nom_complet", "Nom et Prénoms", "text", "OUI", "NON", "", "", "", ""],
        ["contact_phone", "Numéro de Téléphone", "phone", "OUI", "OUI", "", "", "", ""],
        ["date_adhesion", "Date d'Adhésion Coopérative", "date", "OUI", "NON", "", "", "", ""],
        ["statut_certification", "Statut Certification", "select", "NON", "NON", "En cours", "En cours, Certifié Fairtrade, Certifié Rainforest, Non certifié", "", ""],
        ["photo_identite", "Photo d'Identité", "image", "NON", "NON", "", "", "", ""],
        ["est_actif", "Compte Actif", "boolean", "OUI", "NON", "VRAI", "", "", ""],
    ]

    for r_data in planteurs_rows:
        ws_planteurs.append(r_data)

    for r_idx in range(2, len(planteurs_rows) + 2):
        ws_planteurs.row_dimensions[r_idx].height = 20
        for c_idx in range(1, len(headers_data) + 1):
            cell = ws_planteurs.cell(row=r_idx, column=c_idx)
            cell.border = thin_border
            if c_idx in [3, 4, 5]:
                cell.alignment = align_center
            elif c_idx == 1:
                cell.font = font_code
                cell.alignment = align_left
            else:
                cell.alignment = align_left

    # =========================================================================
    # FEUILLE 3 : EXEMPLE 2 - PLANTATIONS (Ressource Liée à Planteurs)
    # =========================================================================
    ws_plantations = wb.create_sheet(title="Plantations")
    apply_data_sheet_styling(ws_plantations, fill_header_blue)

    plantations_rows = [
        ["code_plantation", "Code de la Parcelle", "text", "OUI", "OUI", "", "", "", ""],
        ["nom_parcelle", "Désignation de la Parcelle", "text", "OUI", "NON", "", "", "", ""],
        ["planteur_referent", "Planteur Référent", "relation", "OUI", "NON", "", "", "planteurs", "nom_complet"],
        ["superficie_ha", "Superficie Estimée (ha)", "decimal", "OUI", "NON", "0.0", "", "", ""],
        ["annee_creation", "Année de Création", "integer", "NON", "NON", "", "", "", ""],
        ["cultures_associees", "Cultures Associées", "multiselect", "NON", "NON", "Cacao", "Cacao, Café, Hévéa, Banane, Palmier à huile", "", ""],
        ["coordonnees_gps", "Position GPS du Centre", "geo_point", "NON", "NON", "", "", "", ""],
        ["contour_parcelle", "Tracé Polygone Délimitation", "geo_polygon", "NON", "NON", "", "", "", ""],
        ["qrcode_tracabilite", "Code QR de Traçabilité", "barcode", "NON", "OUI", "", "", "", ""],
    ]

    for r_data in plantations_rows:
        ws_plantations.append(r_data)

    for r_idx in range(2, len(plantations_rows) + 2):
        ws_plantations.row_dimensions[r_idx].height = 20
        for c_idx in range(1, len(headers_data) + 1):
            cell = ws_plantations.cell(row=r_idx, column=c_idx)
            cell.border = thin_border
            if c_idx in [3, 4, 5]:
                cell.alignment = align_center
            elif c_idx == 1:
                cell.font = font_code
                cell.alignment = align_left
            elif c_idx == 3 and cell.value == "relation":
                cell.font = Font(name="Calibri", size=10, bold=True, color="2563EB")
                cell.alignment = align_center
            else:
                cell.alignment = align_left

    # =========================================================================
    # FEUILLE 4 : TYPES AUTORISÉS (Documentation exhaustive des 26 types)
    # =========================================================================
    ws_doc = wb.create_sheet(title="Types Autorisés")
    doc_headers = ["Type Technique *", "Catégorie", "Description Métier", "Exemples Concrets d'Usage"]
    ws_doc.append(doc_headers)
    ws_doc.row_dimensions[1].height = 26
    for c in range(1, 5):
        cell = ws_doc.cell(row=1, column=c)
        cell.font = header_font
        cell.fill = fill_header_purple
        cell.alignment = align_center

    type_doc = [
        ["text", "Basique", "Texte court sur ligne unique", "Nom, prénom, matricule, référence"],
        ["textarea", "Basique", "Texte long multi-lignes", "Observations d'audit, descriptions, notes"],
        ["number", "Numérique", "Nombre générique (entier ou décimal)", "Quantité récoltée, score global"],
        ["integer", "Numérique", "Nombre entier strict sans virgule", "Nombre d'arbres, sacs de cacao, ouvriers"],
        ["decimal", "Numérique", "Nombre décimal avec précision à virgule", "Superficie en hectares, poids en kg"],
        ["boolean", "Basique", "Booléen Vrai/Faux (Case à cocher)", "Compte actif, Parcelle certifiée EUDR"],
        ["currency", "Numérique & Finance", "Montant financier avec devise", "Prix unitaire, prime Fairtrade, avance"],
        ["percentage", "Numérique & Finance", "Valeur en pourcentage (0 à 100%)", "Taux d'humidité du cacao, taux de pureté"],
        ["date", "Date & Heure", "Date au format AAAA-MM-JJ", "Date de naissance, date d'inspection"],
        ["datetime", "Date & Heure", "Date et heure complètes avec horodatage", "Horodatage de la pesée en magasin"],
        ["time", "Date & Heure", "Heure au format HH:MM", "Heure d'ouverture, heure de rotation"],
        ["email", "Contact", "Adresse électronique vérifiée", "contact@cooperative.ci"],
        ["phone", "Contact", "Numéro de téléphone avec indicatif", "+225 0708091011"],
        ["url", "Web", "Lien Internet / URL valide", "https://agriculture.gouv.ci/cadastre/124"],
        ["select", "Choix", "Liste déroulante (un seul choix)", "Statut, Grade cacao (Grade I, Grade II)"],
        ["multiselect", "Choix", "Choix multiples (Étiquettes / Tags)", "Cultures associées (Cacao, Café, Hévéa)"],
        ["file", "Médias", "Document ou fichier joint (PDF, Doc)", "Contrat de fermage, titre foncier numérisé"],
        ["image", "Médias", "Photographie ou image (JPG, PNG)", "Photo du planteur, photo du plant infecté"],
        ["audio", "Médias", "Enregistrement audio / vocal", "Accord verbal enregistré, note vocale agent"],
        ["video", "Médias", "Enregistrement vidéo (MP4, WebM)", "Survol drone de la plantation, audit visuel"],
        ["geo_point", "Géographie", "Coordonnées GPS précises (Lat, Long)", "Localisation exacte du hangar de stockage"],
        ["geo_polygon", "Géographie", "Contour géographique d'une parcelle", "Polygone GeoJSON délimitant la plantation"],
        ["barcode", "Traçabilité", "Code-barres ou QR Code scannable", "QR code apposé sur chaque sac scellé"],
        ["color", "Avancé", "Code couleur hexadécimal (#RRGGBB)", "Code couleur d'identification de parcelle"],
        ["json", "Avancé", "Objet structuré JSON libre", "Trames brutes de capteurs IoT, météo locale"],
        ["relation", "Relations", "Liaison vers une autre ressource liée", "Lien Plantation -> Planteur référent"],
    ]

    for d in type_doc:
        ws_doc.append(d)

    for r_idx in range(2, len(type_doc) + 2):
        ws_doc.row_dimensions[r_idx].height = 20
        for c_idx in range(1, 5):
            cell = ws_doc.cell(row=r_idx, column=c_idx)
            cell.border = thin_border
            if c_idx == 1:
                cell.font = font_code
                cell.alignment = align_center
            else:
                cell.alignment = align_left

    ws_doc.column_dimensions["A"].width = 22
    ws_doc.column_dimensions["B"].width = 22
    ws_doc.column_dimensions["C"].width = 42
    ws_doc.column_dimensions["D"].width = 50

    output = io.BytesIO()
    wb.save(output)
    return output.getvalue()


NON_DISPLAYABLE_TYPES = {
    "relation",
    "json",
    "file",
    "signature",
    "geo_polygon",
    "rich_text",
    "audio",
}


def generate_unique_values(
    strategy_cfg: Dict[str, Any],
    count: int,
    field_name: str,
    field_type: str,
) -> Tuple[Optional[List[Any]], Optional[str]]:
    """
    Génère une séquence de valeurs uniques pour combler les enregistrements existants
    selon la stratégie choisie (auto-incrément, code séquentiel, token unique ou tableau distinct).
    """
    strategy = strategy_cfg.get("strategy")
    if not strategy:
        return None, "Stratégie non spécifiée."

    if strategy in ["auto_increment", "sequential_number"]:
        try:
            start = int(strategy_cfg.get("start", 1))
            step = int(strategy_cfg.get("step", 1))
        except (ValueError, TypeError):
            start, step = 1, 1
        return [start + i * step for i in range(count)], None

    elif strategy in ["sequential_code", "auto_code"]:
        prefix = str(strategy_cfg.get("prefix") or f"{field_name.upper()}-")
        try:
            start = int(strategy_cfg.get("start", 1))
            pad = max(1, int(strategy_cfg.get("pad_length", 4)))
        except (ValueError, TypeError):
            start, pad = 1, 4
        suffix = str(strategy_cfg.get("suffix") or "")
        return [f"{prefix}{start + i:0{pad}d}{suffix}" for i in range(count)], None

    elif strategy in ["unique_token", "uuid"]:
        prefix = str(strategy_cfg.get("prefix") or "")
        seen = set()
        vals = []
        for _ in range(count):
            tok = secrets.token_hex(4).upper()
            while tok in seen:
                tok = secrets.token_hex(4).upper()
            seen.add(tok)
            vals.append(f"{prefix}{tok}")
        return vals, None

    elif strategy == "distinct_values":
        raw_vals = strategy_cfg.get("values")
        if not isinstance(raw_vals, list):
            return None, "La stratégie 'distinct_values' requiert une liste de valeurs distinctes."
        if len(raw_vals) < count:
            return None, f"Le tableau fourni ne contient que {len(raw_vals)} valeur(s) pour {count} enregistrements existants."
        distinct_slice = raw_vals[:count]
        if len(set(distinct_slice)) < count:
            return None, "Le tableau fourni contient des valeurs en doublon. Chaque entrée doit être strictement unique."
        return distinct_slice, None

    return None, f"Stratégie d'unicité « {strategy} » non supportée."


def reconcile_schema_coherence(
    existing_fields: List[Dict[str, Any]],
    incoming_fields: List[Dict[str, Any]],
    existing_items: Optional[List[Dict[str, Any]]] = None,
    current_version: str = "1.0.0",
    is_active: bool = False,
    other_resources: Optional[List[Dict[str, Any]]] = None,
    current_resource_id: Optional[int] = None,
    current_resource_code: Optional[str] = None,
    current_resource_name: Optional[str] = None,
    unique_population_strategies: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Rapproche un schéma existant et des champs entrants (Excel/JSON/Formulaire)
    en garantissant l'intégrité, l'authenticité et la cohérence absolue avec les données existantes
    et avec les autres ressources dépendantes :
    1. Vérification inter-ressources : bloque la suppression ou la modification de type incompatible
       si une autre ressource dépend d'un champ comme cible de liaison ou champ d'affichage.
    2. Ajout de champs obligatoires : exige une valeur par défaut si des enregistrements existent.
    3. Gestion stricte de l'unicité : interdit l'assignation statique de valeurs identiques et applique
       les stratégies de valorisation unique (auto-incrément, code séquentiel, tokens, tableau distinct).
    """
    items = existing_items or []
    item_count = len(items)

    existing_map = {f["name"]: dict(f) for f in existing_fields}
    incoming_map = {f["name"]: dict(f) for f in incoming_fields if f.get("name")}
    incoming_seen = set()

    added_fields: List[Dict[str, Any]] = []
    updated_fields: List[Dict[str, Any]] = []
    identical_fields: List[str] = []
    ignored_fields: List[Dict[str, Any]] = []
    deleted_fields: List[Dict[str, Any]] = []
    unique_population_actions: Dict[str, Any] = {}

    warnings: List[str] = []
    errors: List[str] = []

    res_code_norm = normalize_field_name(current_resource_code or "")
    res_name_norm = normalize_field_name(current_resource_name or "")

    # -------------------------------------------------------------------------
    # 1. DÉTECTION ET VÉRIFICATION DES CHAMPS SUPPRIMÉS (existing - incoming)
    # -------------------------------------------------------------------------
    incoming_names = set(incoming_map.keys())
    for old_name, old_field in existing_map.items():
        if old_name not in incoming_names:
            del_label = old_field.get("label") or old_name
            # Vérifier si une autre ressource dépend de ce champ
            is_dependent = False
            for other in (other_resources or []):
                other_id = other.get("id")
                other_name = other.get("name") or other.get("code") or "Autre ressource"
                for o_field in other.get("schema_fields") or []:
                    if o_field.get("type") == "relation":
                        target_id = o_field.get("target_resource_id")
                        target_code = normalize_field_name(o_field.get("target_resource_code") or "")
                        targets_current = (
                            (current_resource_id is not None and target_id == current_resource_id)
                            or (target_code and res_code_norm and target_code == res_code_norm)
                            or (target_code and res_name_norm and target_code == res_name_norm)
                        )
                        if targets_current:
                            disp_f = o_field.get("display_field")
                            tgt_f = o_field.get("target_field")
                            rel_label = o_field.get("label") or o_field.get("name")
                            if disp_f == old_name:
                                is_dependent = True
                                errors.append(
                                    f"Impossible de supprimer le champ « {del_label} » (clé: '{old_name}') : "
                                    f"La ressource « {other_name} » en dépend directement car son champ de liaison « {rel_label} » "
                                    f"l'utilise comme libellé d'affichage cible."
                                )
                            elif tgt_f == old_name:
                                is_dependent = True
                                errors.append(
                                    f"Impossible de supprimer le champ « {del_label} » (clé: '{old_name}') : "
                                    f"La ressource « {other_name} » en dépend directement car son champ de liaison « {rel_label} » "
                                    f"cible spécifiquement cette colonne."
                                )

            if not is_dependent:
                deleted_fields.append(old_field)
                if item_count > 0:
                    warnings.append(
                        f"Le champ « {del_label} » ({old_name}) sera supprimé et ses données seront définitivement purgées "
                        f"des {item_count} enregistrement(s) existant(s)."
                    )

    # -------------------------------------------------------------------------
    # 2. PARCOURS DES CHAMPS ENTRANTS (AJOUTS ET MISES À JOUR)
    # -------------------------------------------------------------------------
    resulting_map: Dict[str, Dict[str, Any]] = {}

    for incoming in incoming_fields:
        name = incoming.get("name")
        if not name:
            continue

        label = incoming.get("label") or name
        if name in incoming_seen:
            warnings.append(f"Champ en double dans le lot soumis: '{name}'. La dernière occurrence a été conservée.")
        incoming_seen.add(name)

        # ---------------------------------------------------------------------
        # CAS A : NOUVEAU CHAMP (AJOUT)
        # ---------------------------------------------------------------------
        if name not in existing_map:
            is_req = incoming.get("required", False)
            def_val = incoming.get("default_value")
            is_uniq = incoming.get("unique", False)
            ftype = incoming.get("type", "text")

            # Règle obligatoire avec données existantes
            if is_req and item_count > 0:
                if def_val is None or str(def_val).strip() == "":
                    errors.append(
                        f"Incohérence : Le nouveau champ « {label} » est marqué 'Obligatoire', "
                        f"mais la ressource contient déjà {item_count} enregistrement(s) sans valeur par défaut définie. "
                        f"Veuillez obligatoirement spécifier une valeur par défaut pour alimenter les enregistrements existants."
                    )
                else:
                    warnings.append(
                        f"Le champ obligatoire « {label} » sera injecté avec la valeur par défaut « {def_val} » "
                        f"pour les {item_count} enregistrement(s) existant(s)."
                    )

            # Règle unicité avec données existantes
            if is_uniq and item_count > 1:
                # Si valeur statique unique demandée sans stratégie
                strat_cfg = (unique_population_strategies or {}).get(name)
                if strat_cfg:
                    gen_vals, err = generate_unique_values(strat_cfg, item_count, name, ftype)
                    if err:
                        errors.append(f"Champ unique « {label} » : {err}")
                    else:
                        mapping = {items[idx]["id"]: gen_vals[idx] for idx in range(item_count)}
                        unique_population_actions[name] = mapping
                        warnings.append(
                            f"Le champ unique « {label} » sera initialisé avec des valeurs distinctes pour les "
                            f"{item_count} enregistrements via la stratégie '{strat_cfg.get('strategy')}'."
                        )
                else:
                    errors.append(
                        f"Conflit d'unicité : Le champ « {label} » ({name}) ne peut pas être ajouté comme unique "
                        f"car la ressource contient déjà {item_count} enregistrements. Une valeur statique créerait des collisions. "
                        f"Veuillez définir une stratégie de valorisation unique (auto-incrément séquentiel, code numéroté, "
                        f"tokens uniques ou tableau de valeurs distinctes)."
                    )

            added_fields.append(incoming)
            resulting_map[name] = incoming

        # ---------------------------------------------------------------------
        # CAS B : CHAMP EXISTANT (MISE À JOUR OU INCHANGÉ)
        # ---------------------------------------------------------------------
        else:
            old_f = existing_map[name]
            diffs = {}
            for k in ["label", "type", "required", "unique", "options", "default_value", "target_resource_code", "display_field"]:
                old_val = old_f.get(k)
                new_val = incoming.get(k)
                if isinstance(old_val, list) and isinstance(new_val, list):
                    if sorted(old_val) != sorted(new_val):
                        diffs[k] = (old_val, new_val)
                elif old_val != new_val and new_val is not None:
                    diffs[k] = (old_val, new_val)

            if not diffs:
                identical_fields.append(name)
                resulting_map[name] = old_f
            else:
                is_coherent = True

                # 1. Vérification inter-ressources sur modification de type
                if "type" in diffs:
                    old_t, new_t = diffs["type"]
                    if new_t in NON_DISPLAYABLE_TYPES:
                        for other in (other_resources or []):
                            other_name = other.get("name") or other.get("code") or "Autre ressource"
                            for o_field in other.get("schema_fields") or []:
                                if o_field.get("type") == "relation":
                                    target_id = o_field.get("target_resource_id")
                                    target_code = normalize_field_name(o_field.get("target_resource_code") or "")
                                    targets_current = (
                                        (current_resource_id is not None and target_id == current_resource_id)
                                        or (target_code and res_code_norm and target_code == res_code_norm)
                                        or (target_code and res_name_norm and target_code == res_name_norm)
                                    )
                                    if targets_current and o_field.get("display_field") == name:
                                        is_coherent = False
                                        rel_label = o_field.get("label") or o_field.get("name")
                                        errors.append(
                                            f"Impossible de modifier le type du champ « {label} » en « {new_t} » : "
                                            f"La ressource « {other_name} » l'utilise comme libellé d'affichage pour sa relation « {rel_label} ». "
                                            f"Un libellé d'affichage doit rester un format représentable (ex: text, number, date, badge)."
                                        )

                    # Vérification compatibilité avec les données existantes
                    if item_count > 0:
                        incompatible_samples = []
                        for item in items:
                            val = item.get("data", {}).get(name)
                            if val is not None and str(val).strip() != "":
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
                                f"Incohérence type : Impossible de convertir le champ « {name} » de « {old_t} » vers « {new_t} ». "
                                f"Des données existantes incompatibles ont été détectées (ex: « {incompatible_samples[0]} »)."
                            )

                # 2. Passage à Unique = True
                if "unique" in diffs and diffs["unique"][1] is True and item_count > 0:
                    vals = [it.get("data", {}).get(name) for it in items]
                    non_empty = [v for v in vals if v is not None and str(v).strip() != ""]
                    has_duplicates = len(set(non_empty)) < len(non_empty)
                    has_empty = len(non_empty) < item_count

                    if has_duplicates or has_empty:
                        strat_cfg = (unique_population_strategies or {}).get(name)
                        if strat_cfg:
                            gen_vals, err = generate_unique_values(strat_cfg, item_count, name, incoming.get("type", "text"))
                            if err:
                                is_coherent = False
                                errors.append(f"Champ unique « {name} » : {err}")
                            else:
                                mapping = {items[idx]["id"]: gen_vals[idx] for idx in range(item_count)}
                                unique_population_actions[name] = mapping
                                warnings.append(
                                    f"Les doublons/vides du champ « {name} » seront résolus pour les {item_count} enregistrements "
                                    f"via la stratégie '{strat_cfg.get('strategy')}'."
                                )
                        else:
                            is_coherent = False
                            errors.append(
                                f"Incohérence unicité : Impossible de rendre le champ « {name} » unique car des doublons ou valeurs vides "
                                f"existent parmi les {item_count} enregistrements actuels. Veuillez fournir une stratégie de valorisation "
                                f"unique (auto-incrément, code séquentiel, tokens uniques ou tableau de valeurs distinctes) pour régénérer des identifiants distincts."
                            )

                # 3. Passage à Obligatoire = True
                if "required" in diffs and diffs["required"][1] is True and item_count > 0:
                    missing_count = sum(
                        1 for it in items
                        if it.get("data", {}).get(name) is None or str(it.get("data", {}).get(name)).strip() == ""
                    )
                    if missing_count > 0:
                        new_def = incoming.get("default_value")
                        if new_def is None or str(new_def).strip() == "":
                            is_coherent = False
                            errors.append(
                                f"Incohérence obligation : Le champ « {name} » contient {missing_count} valeur(s) vide(s) "
                                f"dans les données actuelles. Veuillez fournir une valeur par défaut pour combler les enregistrements existants."
                            )
                        else:
                            warnings.append(
                                f"Le champ « {name} » rendu obligatoire sera complété par « {new_def} » pour {missing_count} ligne(s) vide(s)."
                            )

                if is_coherent:
                    merged = dict(old_f)
                    merged.update(incoming)
                    updated_fields.append(merged)
                    resulting_map[name] = merged
                else:
                    ignored_fields.append({
                        "field": incoming,
                        "reason": "Modifications rejetées pour préserver la cohérence des données existantes."
                    })
                    # Conserver l'ancien champ
                    resulting_map[name] = old_f

    # Ordonner le schéma résultant selon l'ordre des champs entrants
    resulting_schema = [resulting_map[inc["name"]] for inc in incoming_fields if inc.get("name") in resulting_map]

    # Calcul de la nouvelle version si active et modifications effectives
    new_version = current_version
    has_changes = bool(added_fields or updated_fields or deleted_fields)
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

    return {
        "is_coherent": len(errors) == 0,
        "added_count": len(added_fields),
        "updated_count": len(updated_fields),
        "deleted_count": len(deleted_fields),
        "identical_count": len(identical_fields),
        "ignored_count": len(ignored_fields),
        "added_fields": added_fields,
        "updated_fields": updated_fields,
        "deleted_fields": deleted_fields,
        "identical_fields": identical_fields,
        "ignored_fields": ignored_fields,
        "unique_population_actions": unique_population_actions,
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


