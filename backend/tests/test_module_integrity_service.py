import pytest
from datetime import datetime
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.user.user import User, UserRole, UserStatus
from app.models.organization.organization import Organization
from app.models.organization.organization_resource import (
    OrganizationResource,
    OrganizationResourceVersion,
    OrganizationResourceItem,
)
from app.models.organization.organization_form import (
    OrganizationForm,
    OrganizationFormVersion,
    OrganizationFormQuestion,
)
from app.models.organization.organization_plugin import (
    OrganizationPlugin,
    OrganizationPluginVersion,
)
from app.services.plugin.module_integrity_service import ModuleIntegrityService


@pytest.mark.asyncio
async def test_module_integrity_protection_rules(db_session: AsyncSession):
    db = db_session
    uid = int(datetime.now().timestamp() * 1000)

    # 1. Setup User and Org
    user = User(
        email=f"integrity_{uid}@test.com",
        username=f"integrity_{uid}",
        hashed_password="hash",
        full_name="Integrity Tester",
        role=UserRole.ADMIN_ORG,
        status=UserStatus.ACTIVE,
    )
    db.add(user)
    await db.flush()

    org = Organization(
        name=f"Org Integrity {uid}",
        code=f"org_int_{uid}",
        owner_id=user.id,
        country="Sénégal",
        region="Dakar",
        city="Dakar",
        address="Almadies",
        postal_code="10000",
        type="ENTERPRISE",
        sector="AGRITECH",
        contact_email=f"contact_{uid}@test.com",
        phone="+221770000000",
        status="active",
    )
    db.add(org)
    await db.flush()

    org_plug = OrganizationPlugin(
        organization_ids=[org.id],
        user_ids=[user.id],
        code=f"plugin_{uid}",
        name=f"Plugin {uid}",
        category="general",
        author="SenWiseTool",
    )
    db.add(org_plug)
    await db.flush()

    opv = OrganizationPluginVersion(
        organization_plugin_id=org_plug.id,
        organization_ids=[org.id],
        user_ids=[user.id],
        version_number="1.0.0",
        status="active",
        changelog="init",
    )
    db.add(opv)
    await db.flush()

    # 2. Setup Resource A ("Planteurs") with fields [code, nom_complet, village]
    res_a = OrganizationResource(
        organization_id=org.id,
        organization_plugin_version_id=opv.id,
        name="Planteurs",
        code="planteurs",
        version="1.0.0",
        status="active",
        schema_fields=[
            {"name": "code", "label": "Code Planteur", "type": "text", "required": True},
            {"name": "nom_complet", "label": "Nom Complet", "type": "text", "required": True},
            {"name": "village", "label": "Village", "type": "text", "required": False},
        ],
    )
    db.add(res_a)
    await db.flush()

    res_a_v1 = OrganizationResourceVersion(
        organization_id=org.id,
        organization_resource_id=res_a.id,
        organization_plugin_version_id=opv.id,
        version_number="1.0.0",
        status="draft",
        schema_fields=[
            {"name": "code", "label": "Code Planteur", "type": "text", "required": True},
            {"name": "nom_complet", "label": "Nom Complet", "type": "text", "required": True},
            {"name": "village", "label": "Village", "type": "text", "required": False},
        ],
    )
    db.add(res_a_v1)
    await db.flush()

    # Item 1 in Resource A
    item_a1 = OrganizationResourceItem(
        organization_id=org.id,
        organization_resource_id=res_a.id,
        organization_resource_version_id=res_a_v1.id,
        organization_plugin_version_id=opv.id,
        data={"code": "PLANT-001", "nom_complet": "Amadou Diallo", "village": "Ndiaye"},
    )
    db.add(item_a1)
    await db.flush()

    # 3. Setup Resource B ("Parcelles") referencing Resource A (planteurs)
    res_b = OrganizationResource(
        organization_id=org.id,
        organization_plugin_version_id=opv.id,
        name="Parcelles",
        code="parcelles",
        version="1.0.0",
        status="active",
        schema_fields=[
            {"name": "superficie", "label": "Superficie (ha)", "type": "number"},
            {
                "name": "planteur_id",
                "label": "Planteur Référent",
                "type": "relation",
                "target_resource_id": res_a.id,
                "target_resource_code": "planteurs",
                "target_resource_version_id": res_a_v1.id,
                "target_field": "code",
                "display_field": "nom_complet",
            },
        ],
    )
    db.add(res_b)
    await db.flush()

    res_b_v1 = OrganizationResourceVersion(
        organization_id=org.id,
        organization_resource_id=res_b.id,
        organization_plugin_version_id=opv.id,
        version_number="1.0.0",
        status="draft",
        schema_fields=[
            {"name": "superficie", "label": "Superficie (ha)", "type": "number"},
            {
                "name": "planteur_id",
                "label": "Planteur Référent",
                "type": "relation",
                "target_resource_id": res_a.id,
                "target_resource_code": "planteurs",
                "target_resource_version_id": res_a_v1.id,
                "target_field": "code",
                "display_field": "nom_complet",
            },
        ],
    )
    db.add(res_b_v1)
    await db.flush()

    # Item 1 in Resource B referencing PLANT-001
    item_b1 = OrganizationResourceItem(
        organization_id=org.id,
        organization_resource_id=res_b.id,
        organization_resource_version_id=res_b_v1.id,
        organization_plugin_version_id=opv.id,
        data={"superficie": 5.2, "planteur_id": "PLANT-001"},
    )
    db.add(item_b1)
    await db.flush()

    # 4. Setup Form 1 referencing Resource A
    form1 = OrganizationForm(
        organization_id=org.id,
        organization_plugin_version_id=opv.id,
        name="Enquête Planteur",
        code="enquete_planteur",
        version="1.0.0",
        status="active",
    )
    db.add(form1)
    await db.flush()

    form1_v1 = OrganizationFormVersion(
        organization_id=org.id,
        organization_form_id=form1.id,
        organization_plugin_version_id=opv.id,
        version_number="1.0.0",
        status="draft",
    )
    db.add(form1_v1)
    await db.flush()

    q1 = OrganizationFormQuestion(
        organization_id=org.id,
        organization_form_id=form1.id,
        organization_form_version_id=form1_v1.id,
        organization_plugin_version_id=opv.id,
        code="select_planteur",
        label="Sélectionner le Planteur",
        question_type="resource_lookup",
        resource_binding={
            "resource_id": res_a.id,
            "resource_version_id": res_a_v1.id,
            "resource_code": "planteurs",
            "value_field": "code",
            "label_field": "nom_complet",
        },
    )
    db.add(q1)
    await db.flush()

    # ---------------------------------------------------------------------
    # TEST RULE 1 : Interdiction de modifier/supprimer un CHAMP en cours d'utilisation
    # ---------------------------------------------------------------------
    # A) Tentative de supprimer le champ 'nom_complet' (utilisé par res_b et q1)
    new_schema_without_nom = [
        {"name": "code", "label": "Code Planteur", "type": "text", "required": True},
        {"name": "village", "label": "Village", "type": "text", "required": False},
    ]
    with pytest.raises(HTTPException) as exc_info:
        await ModuleIntegrityService.validate_resource_field_can_be_modified_or_deleted(
            db=db,
            resource_id=res_a.id,
            version_id=res_a_v1.id,
            schema_fields_new=new_schema_without_nom,
            schema_fields_old=res_a_v1.schema_fields,
            organization_id=org.id,
        )
    assert exc_info.value.status_code == 409
    assert "nom_complet" in exc_info.value.detail

    # B) Un champ non utilisé ('village') peut être supprimé sans erreur
    new_schema_without_village = [
        {"name": "code", "label": "Code Planteur", "type": "text", "required": True},
        {"name": "nom_complet", "label": "Nom Complet", "type": "text", "required": True},
    ]
    # Ne doit pas lever d'exception
    await ModuleIntegrityService.validate_resource_field_can_be_modified_or_deleted(
        db=db,
        resource_id=res_a.id,
        version_id=res_a_v1.id,
        schema_fields_new=new_schema_without_village,
        schema_fields_old=res_a_v1.schema_fields,
        organization_id=org.id,
    )

    # ---------------------------------------------------------------------
    # TEST RULE 2 : Interdiction de modifier/supprimer un ENREGISTREMENT (LIGNE) en cours d'utilisation
    # ---------------------------------------------------------------------
    # item_a1 ("PLANT-001") est référencé par item_b1 dans res_b
    with pytest.raises(HTTPException) as exc_info:
        await ModuleIntegrityService.validate_resource_row_can_be_modified_or_deleted(
            db=db,
            resource_id=res_a.id,
            item_id=item_a1.id,
            organization_id=org.id,
            is_delete=True,
        )
    assert exc_info.value.status_code == 409
    assert "Parcelles" in exc_info.value.detail

    # Créer un item_a2 non référencé : il doit pouvoir être supprimé sans erreur
    item_a2 = OrganizationResourceItem(
        organization_id=org.id,
        organization_resource_id=res_a.id,
        organization_resource_version_id=res_a_v1.id,
        organization_plugin_version_id=opv.id,
        data={"code": "PLANT-002", "nom_complet": "Fatou Sow", "village": "Podor"},
    )
    db.add(item_a2)
    await db.flush()

    await ModuleIntegrityService.validate_resource_row_can_be_modified_or_deleted(
        db=db,
        resource_id=res_a.id,
        item_id=item_a2.id,
        organization_id=org.id,
        is_delete=True,
    )

    # ---------------------------------------------------------------------
    # TEST RULE 3 : Interdiction de supprimer une VERSION DE RESSOURCE en cours d'utilisation
    # ---------------------------------------------------------------------
    # res_a_v1 est ciblée par res_b_v1 et par q1 dans form1_v1
    with pytest.raises(HTTPException) as exc_info:
        await ModuleIntegrityService.validate_resource_version_can_be_deleted(
            db=db,
            resource_id=res_a.id,
            version_id=res_a_v1.id,
            organization_id=org.id,
        )
    assert exc_info.value.status_code == 409

    # ---------------------------------------------------------------------
    # TEST RULE 4 : Interdiction de modifier/supprimer une QUESTION DE FORMULAIRE en cours d'utilisation
    # ---------------------------------------------------------------------
    # Setup Form 2 avec une question cross_form_lookup pointant vers q1 de Form 1
    form2 = OrganizationForm(
        organization_id=org.id,
        organization_plugin_version_id=opv.id,
        name="Audit Planteur",
        code="audit_planteur",
        version="1.0.0",
        status="active",
    )
    db.add(form2)
    await db.flush()

    form2_v1 = OrganizationFormVersion(
        organization_id=org.id,
        organization_form_id=form2.id,
        organization_plugin_version_id=opv.id,
        version_number="1.0.0",
        status="draft",
    )
    db.add(form2_v1)
    await db.flush()

    q2 = OrganizationFormQuestion(
        organization_id=org.id,
        organization_form_id=form2.id,
        organization_form_version_id=form2_v1.id,
        organization_plugin_version_id=opv.id,
        code="cross_planteur",
        label="Planteur Audité",
        question_type="cross_form_lookup",
        settings={
            "cross_form_lookup": {
                "target_form_id": form1.id,
                "target_form_version_id": form1_v1.id,
                "target_question_id": q1.id,
                "target_question_code": "select_planteur",
            }
        },
    )
    db.add(q2)
    await db.flush()

    # Tentative de modifier ou supprimer q1 de form1
    with pytest.raises(HTTPException) as exc_info:
        await ModuleIntegrityService.validate_form_question_can_be_modified_or_deleted(
            db=db,
            question_id=q1.id,
            organization_id=org.id,
            is_delete=True,
        )
    assert exc_info.value.status_code == 409
    assert "select_planteur" in exc_info.value.detail
    assert "Audit Planteur" in exc_info.value.detail

    # Question q2 non utilisée par d'autre formulaire : peut être modifiée/supprimée
    await ModuleIntegrityService.validate_form_question_can_be_modified_or_deleted(
        db=db,
        question_id=q2.id,
        organization_id=org.id,
        is_delete=True,
    )

    # ---------------------------------------------------------------------
    # TEST RULE 5 : Interdiction de supprimer une VERSION DE FORMULAIRE en cours d'utilisation
    # ---------------------------------------------------------------------
    # form1_v1 est ciblée par form2_v1 via cross_form_lookup
    with pytest.raises(HTTPException) as exc_info:
        await ModuleIntegrityService.validate_form_version_can_be_deleted(
            db=db,
            form_id=form1.id,
            version_id=form1_v1.id,
            organization_id=org.id,
        )
    assert exc_info.value.status_code == 409
    assert "Audit Planteur" in exc_info.value.detail
