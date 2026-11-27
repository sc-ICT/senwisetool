import pytest
from datetime import datetime
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.user.user import User, UserRole, UserStatus
from app.models.organization.organization import Organization
from app.models.organization.organization_plugin import (
    OrganizationPlugin,
    OrganizationPluginVersion,
)
from app.models.organization.organization_resource import (
    OrganizationResource,
    OrganizationResourceVersion,
    OrganizationResourceItem,
)
from app.models.organization.organization_form import (
    OrganizationForm,
    OrganizationFormVersion,
    OrganizationFormSection,
    OrganizationFormQuestion,
)
from app.models.organization.organization_project import (
    OrganizationProject,
    OrganizationProjectVersion,
)
from app.models.organization.organization_campaign import (
    OrganizationCampaign,
    OrganizationCampaignVersion,
)
from app.services.plugin.organization_publication_service import OrganizationPublicationService


@pytest.mark.asyncio
async def test_organization_publication_and_cascade(db_session: AsyncSession):
    db = db_session
    uid = int(datetime.now().timestamp() * 1000)

    # 1. Créer User & Organization
    user = User(
        email=f"pub_user_{uid}@test.com",
        username=f"pub_user_{uid}",
        hashed_password="hash",
        full_name="Publication Tester",
        role=UserRole.ADMIN_ORG,
        status=UserStatus.ACTIVE,
    )
    db.add(user)
    await db.flush()

    org = Organization(
        name=f"Org Pub {uid}",
        code=f"org_pub_{uid}",
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
        name="Test Pub Plugin",
        code=f"test_pub_plugin_{uid}",
        category="agriculture",
        status="active",
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

    # 2. Créer une Ressource avec une version NATIF
    res = OrganizationResource(
        organization_id=org.id,
        organization_plugin_version_id=opv.id,
        name="Parcelles Cacao",
        code=f"parcelles_cacao_{uid}",
        status="active",
        schema_fields=[
            {"name": "code_parcelle", "label": "Code Parcelle", "type": "text", "required": True},
            {"name": "superficie", "label": "Superficie (ha)", "type": "number"},
        ],
    )
    db.add(res)
    await db.flush()

    res_v1 = OrganizationResourceVersion(
        organization_id=org.id,
        organization_resource_id=res.id,
        organization_plugin_version_id=opv.id,
        version_number="1.0.0",
        status="native",
        schema_fields=[
            {"name": "code_parcelle", "label": "Code Parcelle", "type": "text", "required": True},
            {"name": "superficie", "label": "Superficie (ha)", "type": "number"},
        ],
        template_governance={"allow_edit": True, "allow_delete": True},
    )
    db.add(res_v1)
    await db.flush()

    res_item = OrganizationResourceItem(
        organization_id=org.id,
        organization_resource_id=res.id,
        organization_resource_version_id=res_v1.id,
        organization_plugin_version_id=opv.id,
        data={"code_parcelle": "PARC-001", "superficie": 2.5},
    )
    db.add(res_item)
    await db.flush()

    # =========================================================================
    # TEST 1 : Publication d'une version de ressource native -> crée copie '1.0.0 / 1'
    # =========================================================================
    pub_res_1 = await OrganizationPublicationService.publish_resource_version(
        db=db,
        resource_id=res.id,
        version_id=res_v1.id,
        organization_id=org.id,
        user_id=user.id,
    )

    assert pub_res_1.status == "published"
    assert pub_res_1.publication_number == 1
    assert pub_res_1.version_number == "1.0.0 / 1"
    assert pub_res_1.origin_version_id == res_v1.id
    assert pub_res_1.published_at is not None

    # La version d'origine res_v1 reste 'native'
    await db.refresh(res_v1)
    assert res_v1.status == "native"
    assert res_v1.version_number == "1.0.0"

    # Vérifier que les items ont été dupliqués sur la version publiée
    await db.refresh(pub_res_1, ["items"])
    assert len(pub_res_1.items) == 1
    assert pub_res_1.items[0].data["code_parcelle"] == "PARC-001"
    assert pub_res_1.items[0].id != res_item.id

    # =========================================================================
    # TEST 2 : Deuxième publication de la même version native -> crée '1.0.0 / 2'
    # =========================================================================
    pub_res_2 = await OrganizationPublicationService.publish_resource_version(
        db=db,
        resource_id=res.id,
        version_id=res_v1.id,
        organization_id=org.id,
        user_id=user.id,
    )

    assert pub_res_2.status == "published"
    assert pub_res_2.publication_number == 2
    assert pub_res_2.version_number == "1.0.0 / 2"
    assert pub_res_2.origin_version_id == res_v1.id

    # =========================================================================
    # TEST 3 : Refus de publier une version déjà publiée / scellée
    # =========================================================================
    with pytest.raises(HTTPException) as exc_info:
        await OrganizationPublicationService.publish_resource_version(
            db=db,
            resource_id=res.id,
            version_id=pub_res_1.id,
            organization_id=org.id,
            user_id=user.id,
        )
    assert exc_info.value.status_code == 400
    assert "déjà une copie scellée" in exc_info.value.detail

    # =========================================================================
    # TEST 4 : Formulaire lié à une ressource -> publication en cascade
    # =========================================================================
    form = OrganizationForm(
        organization_id=org.id,
        organization_plugin_version_id=opv.id,
        name="Formulaire Diagnostic",
        code=f"form_diag_{uid}",
        status="active",
    )
    db.add(form)
    await db.flush()

    form_v1 = OrganizationFormVersion(
        organization_id=org.id,
        organization_form_id=form.id,
        organization_plugin_version_id=opv.id,
        version_number="1.0.0",
        status="native",
    )
    db.add(form_v1)
    await db.flush()

    section = OrganizationFormSection(
        organization_id=org.id,
        organization_form_id=form.id,
        organization_form_version_id=form_v1.id,
        organization_plugin_version_id=opv.id,
        title="Section 1",
        code="sec_1",
        order_index=1,
    )
    db.add(section)
    await db.flush()

    question = OrganizationFormQuestion(
        organization_id=org.id,
        organization_form_id=form.id,
        organization_form_version_id=form_v1.id,
        organization_form_section_id=section.id,
        organization_plugin_version_id=opv.id,
        code="q_parcelle",
        label="Sélectionnez la parcelle",
        question_type="select",
        resource_binding={
            "resource_id": res.id,
            "target_resource_version_id": res_v1.id,
            "field": "code_parcelle",
        },
        order_index=1,
    )
    db.add(question)
    await db.flush()

    # Publier le formulaire -> doit publier en cascade la ressource et re-câbler le binding
    pub_form = await OrganizationPublicationService.publish_form_version(
        db=db,
        form_id=form.id,
        version_id=form_v1.id,
        organization_id=org.id,
        user_id=user.id,
    )

    assert pub_form.status == "published"
    assert pub_form.publication_number == 1
    assert pub_form.version_number == "1.0.0 / 1"
    assert pub_form.origin_version_id == form_v1.id

    # Vérifier que les questions sont dupliquées et que le resource_binding pointe vers une version publiée
    await db.refresh(pub_form, ["sections"])
    pub_sections = pub_form.sections
    assert len(pub_sections) == 1
    await db.refresh(pub_sections[0], ["questions"])
    pub_questions = pub_sections[0].questions
    assert len(pub_questions) == 1

    binding = pub_questions[0].resource_binding
    assert binding is not None
    # Le target_resource_version_id doit pointer vers une version publiée, PAS res_v1 !
    assert binding["target_resource_version_id"] != res_v1.id

    # =========================================================================
    # TEST 5 : Cascade globale Campagne -> Projet -> Formulaire -> Ressource
    # =========================================================================
    proj = OrganizationProject(
        organization_id=org.id,
        organization_plugin_version_id=opv.id,
        name="Projet Durabilité",
        code=f"proj_durab_{uid}",
        status="active",
    )
    db.add(proj)
    await db.flush()

    proj_v1 = OrganizationProjectVersion(
        organization_id=org.id,
        organization_project_id=proj.id,
        organization_plugin_version_id=opv.id,
        version_number="1.0.0",
        status="native",
        settings={"attached_form_versions": {str(form.id): form_v1.id}},
    )
    db.add(proj_v1)
    await db.flush()

    camp = OrganizationCampaign(
        organization_id=org.id,
        organization_plugin_version_id=opv.id,
        name="Campagne Récolte 2026",
        code=f"camp_recolte_{uid}",
        status="active",
    )
    db.add(camp)
    await db.flush()

    camp_v1 = OrganizationCampaignVersion(
        organization_id=org.id,
        organization_campaign_id=camp.id,
        organization_plugin_version_id=opv.id,
        version_number="1.0.0",
        status="native",
        target_projects=[{"project_id": proj.id, "version_id": proj_v1.id}],
        target_forms=[{"form_id": form.id, "version_id": form_v1.id}],
    )
    db.add(camp_v1)
    await db.flush()

    # Publier la Campagne -> cascade Projet -> cascade Formulaire -> cascade Ressource
    pub_camp = await OrganizationPublicationService.publish_campaign_version(
        db=db,
        campaign_id=camp.id,
        version_id=camp_v1.id,
        organization_id=org.id,
        user_id=user.id,
    )

    assert pub_camp.status == "published"
    assert pub_camp.publication_number == 1
    assert pub_camp.version_number == "1.0.0 / 1"
    # Vérifier que target_projects a été mis à jour vers le projet publié
    assert pub_camp.target_projects[0]["version_id"] != proj_v1.id
    # Vérifier que target_forms a été mis à jour vers le formulaire publié
    assert pub_camp.target_forms[0]["version_id"] != form_v1.id
