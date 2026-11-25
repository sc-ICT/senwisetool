import pytest
from httpx import AsyncClient
from datetime import datetime, timezone
from app.core import security
from app.models.user.user import User, UserRole, UserStatus
from app.models.organization.organization import Organization, OrgStatusEnum
from app.models.organization.member import OrganizationMember
from app.models.organization.role import OrganizationRole
from app.models.project.project import Project, ProjectStatus, ProjectVersion
from app.models.form.form import Form, FormStatus
from app.models.form.form_version import FormVersion, FormVersionStatus
from app.models.campaign.campaign import Campaign, CampaignStatus
from app.models.campaign.campaign_version import CampaignVersion, CampaignVersionStatus
from app.services.project.project_service import ProjectService
from app.services.campaign.campaign_service import campaign_service
from fastapi import HTTPException


@pytest.mark.asyncio
async def test_form_version_to_project_version_association(setup_db):
    """
    Vérifie la règle stricte :
    1. Une version de formulaire en brouillon ('draft') NE PEUT PAS être associée à un projet (rejet HTTP 400).
    2. Une version de formulaire publiée ('published' / 'active') peut être associée avec succès.
    """
    from tests.conftest import TestingSessionLocal

    async with TestingSessionLocal() as session:
        # Créer utilisateur
        user = User(
            email="manager@senma.com",
            username="manager_assoc",
            hashed_password=security.get_password_hash("Password123!"),
            full_name="Manager Assoc",
            role=UserRole.ADMIN_ORG,
            status=UserStatus.ACTIVE,
            is_email_verified=True,
        )
        session.add(user)
        await session.flush()

        # Créer projet
        project = Project(
            name="Projet Test Association",
            code="PRJ_TEST_ASSOC_01",
            created_by_id=user.id,
        )
        session.add(project)
        await session.flush()

        proj_version = ProjectVersion(
            project_id=project.id,
            version_number="1.0.0",
            status=ProjectStatus.DRAFT.value,
            settings={},
            created_by_id=user.id,
        )
        session.add(proj_version)

        # Créer formulaire avec version 1.0.0 en DRAFT
        form = Form(
            name="Formulaire Brouillon",
            code="FORM_DRAFT_01",
            version="1.0.0",
            status=FormStatus.DRAFT.value,
            created_by_id=user.id,
        )
        session.add(form)
        await session.flush()

        form_ver_draft = FormVersion(
            form_id=form.id,
            version_number="1.0.0",
            status=FormVersionStatus.DRAFT.value,
            created_by_id=user.id,
        )
        session.add(form_ver_draft)
        await session.commit()

        # Test 1: Tentative d'associer la version draft -> DOIT ÉCHOUER (HTTP 400)
        with pytest.raises(HTTPException) as exc_info:
            await ProjectService.attach_form_to_project(
                db=session,
                project_id=project.id,
                form_id=form.id,
                form_version_id=form_ver_draft.id,
                project_version_id=proj_version.id,
                user=user,
                is_super_admin=True,
            )
        assert exc_info.value.status_code == 400
        assert "brouillon" in exc_info.value.detail.lower()

        # Test 2: Publier la version du formulaire et réessayer -> DOIT RÉUSSIR
        form_ver_draft.status = FormVersionStatus.PUBLISHED.value
        form.status = FormStatus.PUBLISHED.value
        await session.commit()

        result = await ProjectService.attach_form_to_project(
            db=session,
            project_id=project.id,
            form_id=form.id,
            form_version_id=form_ver_draft.id,
            project_version_id=proj_version.id,
            user=user,
            is_super_admin=True,
        )

        assert result["project_id"] == project.id
        assert result["attached_form"]["form_id"] == form.id
        assert result["attached_form"]["version_number"] == "1.0.0"
        assert result["attached_form"]["status"] == FormVersionStatus.PUBLISHED.value

        # Vérifier get_project_forms
        attached_forms = await ProjectService.get_project_forms(
            db=session,
            project_id=project.id,
            project_version_id=proj_version.id,
            is_super_admin=True,
        )
        assert len(attached_forms) == 1
        assert attached_forms[0]["id"] == form.id
        assert attached_forms[0]["attached_version"]["version_number"] == "1.0.0"


@pytest.mark.asyncio
async def test_campaign_project_and_cascading_forms_association(setup_db):
    """
    Vérifie la règle de cascade et d'association totale :
    1. Associer un projet à une version de campagne lie toutes ses versions.
    2. Tous les formulaires associés à ce projet sont AUTOMATIQUEMENT intégrés dans target_forms de la campagne.
    """
    from tests.conftest import TestingSessionLocal

    async with TestingSessionLocal() as session:
        user = User(
            email="camp_admin@senma.com",
            username="camp_admin",
            hashed_password=security.get_password_hash("Password123!"),
            full_name="Camp Admin",
            role=UserRole.SUPER_ADMIN,
            status=UserStatus.ACTIVE,
            is_email_verified=True,
        )
        session.add(user)
        await session.flush()

        # Créer projet avec formulaire attaché
        project = Project(
            name="Projet Cacao 2026",
            code="PRJ_CACAO_2026",
            created_by_id=user.id,
        )
        session.add(project)
        await session.flush()

        form1 = Form(
            name="Grille Audit Parcelle",
            code="FORM_AUDIT_PARCELLE",
            version="1.0.0",
            status=FormStatus.PUBLISHED.value,
            created_by_id=user.id,
        )
        session.add(form1)
        await session.flush()

        form1_ver = FormVersion(
            form_id=form1.id,
            version_number="1.0.0",
            status=FormVersionStatus.PUBLISHED.value,
            created_by_id=user.id,
        )
        session.add(form1_ver)

        proj_ver = ProjectVersion(
            project_id=project.id,
            version_number="1.0.0",
            status=ProjectStatus.PUBLISHED.value,
            settings={
                "attached_form_versions": [
                    {
                        "form_id": form1.id,
                        "form_version_id": form1_ver.id,
                        "form_name": form1.name,
                        "form_code": form1.code,
                        "version_number": "1.0.0",
                        "status": "published",
                    }
                ],
                "attached_form_ids": [form1.id],
            },
            created_by_id=user.id,
        )
        session.add(proj_ver)

        # Créer une campagne
        campaign = Campaign(
            name="Campagne Cacao 2026-2027",
            code="CAMP_CACAO_2026",
            status=CampaignStatus.DRAFT.value,
            version="1.0.0",
            created_by_id=user.id,
        )
        session.add(campaign)
        await session.flush()

        camp_ver = CampaignVersion(
            campaign_id=campaign.id,
            version_number="1.0.0",
            status=CampaignVersionStatus.DRAFT.value,
            target_projects=[],
            target_forms=[],
            created_by_id=user.id,
        )
        session.add(camp_ver)
        await session.commit()

        # Action : Associer le projet à la version de la campagne
        updated_camp = await campaign_service.attach_project_to_campaign_version(
            db=session,
            campaign_id=campaign.id,
            version_id=camp_ver.id,
            project_id=project.id,
            user=user,
        )

        # Vérifications
        assert len(camp_ver.target_projects) == 1
        assert camp_ver.target_projects[0]["project_id"] == project.id

        # RÈGLE D'OR : Les formulaires associés au projet doivent être automatiquement intégrés dans target_forms !
        assert len(camp_ver.target_forms) == 1
        assert camp_ver.target_forms[0]["form_id"] == form1.id
        assert camp_ver.target_forms[0]["source_project_id"] == project.id
