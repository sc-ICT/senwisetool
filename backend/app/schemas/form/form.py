from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict
from app.schemas.form.form_version import FormVersionResponse
from app.schemas.form.form_section import FormSectionResponse, FormSectionCreate
from app.schemas.form.form_question import FormQuestionResponse


class FormCreate(BaseModel):
    """
    Schéma de création épuré d'un formulaire :
    Strictement limité au Nom, Code, Visibilité, Version initiale et Description.
    """
    name: str = Field(..., min_length=1, max_length=255, description="Nom officiel du formulaire")
    code: str = Field(..., min_length=1, max_length=100, description="Code unique du formulaire")
    visibility: str = Field(default="public", description="Visibilité : 'public' ou 'private'")
    version: str = Field(default="1.0.0", description="Numéro de version initiale (ex: '1.0.0')")
    description: Optional[str] = Field(None, description="Description ou objectif du formulaire")


class FormUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=255)
    description: Optional[str] = None
    visibility: Optional[str] = None
    status: Optional[str] = None
    version: Optional[str] = None
    settings: Optional[Dict[str, Any]] = None


class FormResponse(BaseModel):
    id: int
    organization_id: Optional[int] = None
    name: str
    code: str
    description: Optional[str] = None
    visibility: str = "public"
    version: str = "1.0.0"
    status: str = "draft"
    created_by_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime
    settings: Optional[Dict[str, Any]] = Field(default_factory=dict)
    resource_binding_config: Optional[Dict[str, Any]] = Field(default_factory=dict)

    versions: List[FormVersionResponse] = Field(default_factory=list)
    sections: List[FormSectionResponse] = Field(default_factory=list)
    questions: List[FormQuestionResponse] = Field(default_factory=list)
    submissions_count: Optional[int] = 0

    model_config = ConfigDict(from_attributes=True)


class FormResourceProjectionRequest(BaseModel):
    form_version_id: Optional[int] = Field(default=None, description="ID de la version de formulaire ciblée pour la projection")
    resource_id: int
    resource_version_id: Optional[int] = Field(default=None, description="ID spécifique de version de la ressource principale")
    resource_version: Optional[str] = Field(default=None, description="Numéro de version de la ressource principale")
    unique_id_field: Optional[str] = Field(default="id", description="Champ servant d'identifiant unique")
    chapter_field: Optional[str] = Field(default="", description="Champ pour le chapitre")
    section_field: Optional[str] = Field(default=None, description="Champ pour la section")
    code_field: Optional[str] = Field(default="code", description="Champ pour le code")
    title_field: str = Field(default="name", description="Champ pour le libellé / intitulé")
    help_field: Optional[str] = Field(default=None, description="Champ d'aide")
    type_field: Optional[str] = Field(default=None, description="Champ type de question")
    critical_field: Optional[str] = Field(default=None, description="Champ criticité")
    target_group_field: Optional[str] = Field(default=None, description="Champ cible")
    target_group_filter: Optional[str] = Field(default="all", description="Filtre cible")
    default_question_type: str = Field(default="audit_compliance", description="Type par défaut")
    is_required_default: bool = Field(default=True, description="Questions obligatoires par défaut")
    response_type_mode: str = Field(default="fixed", description="Mode type réponse")
    structure_mode: str = Field(default="auto", description="Mode structure: 'nested', 'sections', 'direct', 'auto'")
    block_resource_id: Optional[int] = Field(default=None, description="ID ressource bloc/chapitre")
    block_resource_version_id: Optional[int] = Field(default=None, description="ID version ressource bloc/chapitre")
    block_title_field: Optional[str] = Field(default="name")
    block_code_field: Optional[str] = Field(default="code")
    block_description_field: Optional[str] = Field(default=None)
    section_resource_id: Optional[int] = Field(default=None, description="ID ressource section")
    section_resource_version_id: Optional[int] = Field(default=None, description="ID version ressource section")
    section_title_field: Optional[str] = Field(default="name")
    section_code_field: Optional[str] = Field(default="code")
    section_description_field: Optional[str] = Field(default=None)
    section_block_link_field: Optional[str] = Field(default=None)
    question_section_link_field: Optional[str] = Field(default=None)
    sanctuarize_generated: bool = Field(default=False)
    replace_existing: bool = Field(default=False)
    target_parent_section_id: Optional[int] = Field(default=None)
    update_existing_root_section_id: Optional[int] = Field(default=None)
    allow_tenant_select_requirements: bool = Field(default=False)
    allow_tenant_add_unselected_questions: bool = Field(default=True)
    allow_tenant_deselect_questions: bool = Field(default=True)
    allow_tenant_add_questions: bool = Field(default=True, description="Autoriser l'organisation à ajouter des questions")
    allow_tenant_add_sections: bool = Field(default=True, description="Autoriser l'organisation à ajouter des sections")
    allow_tenant_delete_sections: bool = Field(default=True, description="Autoriser l'organisation à supprimer des sections")
    allow_tenant_edit_structure: bool = Field(default=True, description="Autoriser l'organisation à modifier la structure")
    allow_tenant_delete_structure: bool = Field(default=True, description="Autoriser l'organisation à supprimer la structure")
    selected_item_ids: Optional[List[Any]] = Field(default=None)
    default_justification_config: Optional[Dict[str, Any]] = Field(default=None)


class FormTreeImportRequest(BaseModel):
    target_parent_section_id: Optional[int] = Field(default=None)
    replace_existing: bool = Field(default=False)
    sections: List[FormSectionCreate] = Field(default_factory=list)

