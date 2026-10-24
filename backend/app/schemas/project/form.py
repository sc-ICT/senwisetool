from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field, field_validator, ConfigDict
from app.schemas.project.form_section import FormSectionResponse, FormSectionCreate
from app.schemas.project.form_question import FormQuestionResponse


class FormBase(BaseModel):
    name: str = Field(..., min_length=2, max_length=255, description="Nom officiel du formulaire")
    code: str = Field(..., min_length=2, max_length=100, description="Code unique au sein du projet")
    description: Optional[str] = None
    form_type: str = Field(default="audit", description="audit, survey, inspection, checklist, onboarding, custom")
    version: str = Field(default="1.0.0")
    status: str = Field(default="draft", description="draft, published, archived")
    order_index: int = Field(default=0)
    is_template: bool = Field(default=True)
    is_sanctuarized: bool = Field(default=False, description="Formulaire modèle sanctuarisé non supprimable par l'organisation")
    allow_custom_sections: bool = Field(default=True, description="L'organisation peut-elle ajouter des sections personnalisées ?")
    is_mandatory: bool = Field(default=True)
    is_repeatable: bool = Field(default=False)
    allow_draft_save: bool = Field(default=True)
    offline_capable: bool = Field(default=True)

    scoring_enabled: bool = Field(default=True)
    scoring_scale: str = Field(default="percentage_100")
    passing_score_percentage: float = Field(default=80.00)
    critical_fail_threshold: int = Field(default=1)

    resource_binding_config: Dict[str, Any] = Field(default_factory=dict)
    settings: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("resource_binding_config", "settings", mode="before")
    @classmethod
    def ensure_dict(cls, v):
        return v if v is not None else {}


class FormCreate(FormBase):
    project_id: Optional[int] = None
    sections: List[FormSectionCreate] = Field(default_factory=list)


class FormUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=255)
    description: Optional[str] = None
    form_type: Optional[str] = None
    version: Optional[str] = None
    status: Optional[str] = None
    order_index: Optional[int] = None
    is_template: Optional[bool] = None
    is_sanctuarized: Optional[bool] = None
    allow_custom_sections: Optional[bool] = None
    is_mandatory: Optional[bool] = None
    is_repeatable: Optional[bool] = None
    allow_draft_save: Optional[bool] = None
    offline_capable: Optional[bool] = None
    scoring_enabled: Optional[bool] = None
    scoring_scale: Optional[str] = None
    passing_score_percentage: Optional[float] = None
    critical_fail_threshold: Optional[int] = None
    resource_binding_config: Optional[Dict[str, Any]] = None
    settings: Optional[Dict[str, Any]] = None


class FormGovernanceUpdate(BaseModel):
    customization_policy: str = Field(
        default="add_only",
        description="readonly, add_only, modify_non_sanctuarized, full_custom"
    )
    is_sanctuarized: bool = Field(default=False)
    allow_custom_sections: bool = Field(default=True)
    allow_custom_questions: bool = Field(default=True)
    allow_tenant_edit_questions: bool = Field(default=False)
    allow_tenant_delete_questions: bool = Field(default=False)
    allow_tenant_select_requirements: bool = Field(default=False)
    
    # Gouvernance et Permissions des Métadonnées
    metadata_policy: str = Field(
        default="add_only",
        description="readonly, add_only, modify_non_sanctuarized, full_custom"
    )
    allow_tenant_add_metadata: bool = Field(default=True, description="Autoriser l'organisation à ajouter des métadonnées")
    allow_tenant_edit_metadata: bool = Field(default=False, description="Autoriser l'organisation à modifier les métadonnées existantes")
    allow_tenant_delete_metadata: bool = Field(default=False, description="Autoriser l'organisation à supprimer des métadonnées")
    enforce_sanctuarized_metadata: bool = Field(default=True, description="Exiger les métadonnées sanctuarisées dans tous les projets")



class FormResourceProjectionRequest(BaseModel):
    resource_id: int
    unique_id_field: str = Field(default="id", description="Champ servant d'identifiant unique pour la question")
    chapter_field: str = Field(default="chapter", description="Champ ou relation représentant le chapitre/bloc")
    section_field: Optional[str] = Field(default=None, description="Champ ou relation représentant la section")
    code_field: str = Field(default="criterion_code", description="Champ pour le code de l'exigence")
    title_field: str = Field(default="criterion_title", description="Champ pour le libellé / intitulé de la question")
    help_field: Optional[str] = Field(default="verification_guideline", description="Champ pour les directives d'évaluation / aide")
    type_field: Optional[str] = Field(default=None, description="Champ indiquant le type de question (si dynamique)")
    critical_field: Optional[str] = Field(default=None, description="Champ pour la criticité")
    target_group_field: Optional[str] = Field(default=None, description="Champ d'applicabilité")
    target_group_filter: Optional[str] = Field(default="all", description="Filtre sur le groupe cible")
    default_question_type: str = Field(default="audit_compliance", description="Type de question par défaut")
    response_type_mode: str = Field(default="fixed", description="Mode de type de réponse: 'dynamic_from_field' ou 'fixed'")
    
    # Support des structures multi-ressources chaînées (Blocs -> Sections -> Questions)
    structure_mode: str = Field(default="auto", description="Mode de structure: 'blocks_sections_questions', 'sections_questions', ou 'direct_questions'")
    block_resource_id: Optional[int] = Field(default=None, description="ID de la ressource des blocs / chapitres")
    block_title_field: Optional[str] = Field(default="name", description="Champ titre du bloc")
    block_code_field: Optional[str] = Field(default="code", description="Champ identifiant/code du bloc")
    section_resource_id: Optional[int] = Field(default=None, description="ID de la ressource des sections")
    section_title_field: Optional[str] = Field(default="name", description="Champ titre de la section")
    section_code_field: Optional[str] = Field(default="code", description="Champ identifiant/code de la section")
    section_block_link_field: Optional[str] = Field(default=None, description="Champ dans la section liant au bloc")
    question_section_link_field: Optional[str] = Field(default=None, description="Champ dans la question liant à la section")

    sanctuarize_generated: bool = Field(default=True, description="Marquer les questions générées comme sanctuarisées")
    replace_existing: bool = Field(default=True, description="Remplacer les sections existantes du brouillon")
    target_parent_section_id: Optional[int] = Field(default=None, description="Section parente cible pour greffer les éléments")
    allow_tenant_select_requirements: bool = Field(default=False, description="Permettre à l'organisation de sélectionner les exigences")


class FormTreeImportRequest(BaseModel):
    target_parent_section_id: Optional[int] = Field(default=None, description="Section parente cible où greffer les sections (null pour la racine)")
    replace_existing: bool = Field(default=False, description="Remplacer les sections existantes")
    sections: List[FormSectionCreate] = Field(default_factory=list, description="Arborescence récursive de sections et questions à importer")


class FormResponse(FormBase):
    id: int
    project_id: int
    organization_id: Optional[int] = None
    status: str
    metrics: Dict[str, Any] = Field(default_factory=dict)
    created_by_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class FormDetailResponse(FormResponse):
    sections: List[FormSectionResponse] = Field(default_factory=list)
    questions: List[FormQuestionResponse] = Field(default_factory=list, description="Modèles de questions directes au niveau du formulaire (hors section)")

    model_config = ConfigDict(from_attributes=True)

