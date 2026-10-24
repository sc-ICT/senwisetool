import uuid
from pathlib import Path
from fastapi import APIRouter, Depends, status, Request, Response, UploadFile, File, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.api.deps import get_current_user, get_optional_current_user
from app.core.config import settings
from app.core.security import set_auth_cookies
from app.models.user.user import User
from app.schemas.organization.invitation import (
    VerifyInvitationRequest,
    VerifyInvitationResponse,
    OTPVerifyRequest,
    OTPVerifyResponse,
    SetupFirstPasswordRequest,
    SetupFirstPasswordResponse,
)
from app.schemas.organization.onboarding import (
    OnboardingDraftSaveRequest,
    OnboardingDraftResponse,
    OnboardingSubmissionRequest,
    OnboardingSubmissionResponse,
    OrganizationStatusCheckResponse,
    ContactEmailOTPRequest,
    ContactEmailOTPResponse,
    ContactEmailVerifyRequest,
    ContactEmailVerifyResponse,
    EmailAvailabilityCheckResponse,
    DocumentUploadResponse,
    ReviewDetailsResponse,
    CandidateReviewSubmitRequest,
    AttestationVerificationResponse,
)
from app.services.organization.invitation_service import invitation_service
from app.services.organization.onboarding_service import onboarding_service
from app.services.notification.notification_service import notification_service

router = APIRouter()

@router.post(
    "/verify-token",
    response_model=VerifyInvitationResponse,
    summary="Vérifier le lien d'invitation et déclencher l'envoi de l'OTP par email"
)
async def verify_token_and_send_otp(
    req: VerifyInvitationRequest,
    current_user: User | None = Depends(get_optional_current_user),
    db: AsyncSession = Depends(get_db)
):
    res = await invitation_service.verify_token_and_send_otp(
        db=db,
        token=req.token,
        email=req.email,
        current_user=current_user
    )
    return VerifyInvitationResponse(**res)

@router.post(
    "/verify-otp",
    response_model=OTPVerifyResponse,
    summary="Valider le code OTP à 6 chiffres reçu par email"
)
async def verify_otp(
    req: OTPVerifyRequest,
    response: Response,
    db: AsyncSession = Depends(get_db)
):
    res = await invitation_service.verify_otp(
        db=db,
        token=req.token,
        email=req.email,
        otp=req.otp
    )
    # Injection du ticket temporaire dans un cookie HttpOnly sécurisé
    response.set_cookie(
        key="onboarding_ticket",
        value=res["session_ticket"],
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
        max_age=900,  # 15 minutes
        path="/"
    )
    return OTPVerifyResponse(**res)

@router.post(
    "/setup-password",
    response_model=SetupFirstPasswordResponse,
    summary="Définir le mot de passe initial et créer le compte Org Admin (1 compte = 1 organisation)"
)
async def setup_first_password(
    req: SetupFirstPasswordRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db)
):
    # Récupérer le ticket depuis le cookie si non transmis dans le corps
    if not req.session_ticket:
        req.session_ticket = request.cookies.get("onboarding_ticket")

    res = await invitation_service.setup_first_password(
        db=db,
        req=req
    )

    # INJECTION SÉCURISÉE DES TOKENS DANS LES COOKIES HTTPONLY (ANTI-XSS)
    set_auth_cookies(
        response=response,
        access_token=res["access_token"],
        refresh_token=res.get("refresh_token") or "",
        remember_me=False
    )
    # Nettoyage du cookie de ticket temporaire
    response.delete_cookie(key="onboarding_ticket", path="/")

    return SetupFirstPasswordResponse(**res)

@router.get(
    "/draft",
    response_model=OnboardingDraftResponse,
    summary="Récupérer le brouillon de saisie pas à pas sauvegardé"
)
async def get_draft(
    email: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    from app.models.user.user import UserRole
    if current_user.role != UserRole.SUPER_ADMIN:
        if email.strip().lower() != current_user.email.strip().lower():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Accès non autorisé : vous ne pouvez pas consulter le brouillon d'un autre compte utilisateur."
            )

    draft = await onboarding_service.get_draft(db=db, email=email)
    if not draft:
        return OnboardingDraftResponse(
            email=email,
            step=1,
            draft_data={},
            updated_at=None
        )
    return OnboardingDraftResponse(
        email=draft.admin_email,
        step=draft.current_step,
        draft_data=draft.draft_data or {},
        updated_at=draft.updated_at
    )

@router.post(
    "/draft",
    response_model=OnboardingDraftResponse,
    summary="Sauvegarde automatique pas à pas du formulaire d'onboarding"
)
async def save_draft(
    draft_in: OnboardingDraftSaveRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    from app.models.user.user import UserRole
    if current_user.role != UserRole.SUPER_ADMIN:
        if draft_in.email.strip().lower() != current_user.email.strip().lower():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Accès non autorisé : vous ne pouvez pas modifier le brouillon d'un autre compte utilisateur."
            )

    draft = await onboarding_service.save_draft(db=db, draft_in=draft_in)
    return OnboardingDraftResponse(
        email=draft.admin_email,
        step=draft.current_step,
        draft_data=draft.draft_data or {},
        updated_at=draft.updated_at
    )

@router.post(
    "/submit",
    response_model=OnboardingSubmissionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Soumission définitive du dossier d'inscription pour examen par l'équipe SenWiseTool"
)
async def submit_onboarding(
    submission: OnboardingSubmissionRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    res = await onboarding_service.submit_onboarding(
        db=db,
        user=current_user,
        submission=submission
    )
    return OnboardingSubmissionResponse(**res)

@router.get(
    "/status",
    response_model=OrganizationStatusCheckResponse,
    summary="Vérifier l'état d'agrément et les retours WYSIWYG de l'équipe SenWiseTool"
)
async def check_organization_status(
    code: str = Query(None, description="Matricule d'organisation optionnel"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    return await onboarding_service.get_status_for_user(db=db, user=current_user, code=code)

@router.get(
    "/verify-attestation",
    response_model=AttestationVerificationResponse,
    summary="Vérifier publiquement l'authenticité d'un récépissé de dépôt d'onboarding"
)
async def verify_attestation(
    code: str = Query(..., description="Matricule unique de l'organisation"),
    db: AsyncSession = Depends(get_db)
):
    return await onboarding_service.verify_attestation(db=db, code=code)

UPLOAD_DOCS_DIR = Path(__file__).resolve().parents[5] / "uploads" / "onboarding_documents"
UPLOAD_DOCS_DIR.mkdir(parents=True, exist_ok=True)
ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".doc", ".docx"}

@router.post(
    "/upload-document",
    response_model=DocumentUploadResponse,
    summary="Téléversement d'un document justificatif officiel (Kbis, statuts, avis journal officiel)"
)
async def upload_document(
    file: UploadFile = File(...)
):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Format de fichier non supporté '{ext}'. Formats acceptés : PDF, PNG, JPG, DOCX."
        )

    content = await file.read()
    size_bytes = len(content)
    if size_bytes > 15 * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le fichier dépasse la taille maximale autorisée (15 Mo)."
        )

    unique_prefix = uuid.uuid4().hex[:10]
    safe_name = f"{unique_prefix}_{Path(file.filename or 'document').name.replace(' ', '_')}"
    target_path = UPLOAD_DOCS_DIR / safe_name
    target_path.write_bytes(content)

    size_human = f"{size_bytes / (1024 * 1024):.2f} Mo" if size_bytes >= 1024 * 1024 else f"{size_bytes / 1024:.1f} Ko"
    url = f"/uploads/onboarding_documents/{safe_name}"

    return DocumentUploadResponse(
        filename=safe_name,
        original_name=file.filename or safe_name,
        url=url,
        size_bytes=size_bytes,
        size_human=size_human,
        content_type=file.content_type or "application/octet-stream"
    )

@router.post(
    "/contact-email/send-otp",
    response_model=ContactEmailOTPResponse,
    summary="Émettre un code OTP pour vérifier l'adresse email officielle de contact"
)
async def send_contact_email_otp(
    req: ContactEmailOTPRequest,
    db: AsyncSession = Depends(get_db)
):
    res = await onboarding_service.send_contact_email_otp(db=db, contact_email=req.contact_email)
    return ContactEmailOTPResponse(**res)

@router.post(
    "/contact-email/verify-otp",
    response_model=ContactEmailVerifyResponse,
    summary="Valider le code OTP à 6 chiffres de l'email officiel de contact"
)
async def verify_contact_email_otp(
    req: ContactEmailVerifyRequest,
    db: AsyncSession = Depends(get_db)
):
    res = await onboarding_service.verify_contact_email_otp(db=db, contact_email=req.contact_email, otp=req.otp)
    return ContactEmailVerifyResponse(**res)

@router.get(
    "/contact-email/check",
    response_model=EmailAvailabilityCheckResponse,
    summary="Vérifier la disponibilité et l'unicité stricte d'un email dès sa saisie dans l'onboarding"
)
async def check_contact_email(
    email: str = Query(..., min_length=3),
    db: AsyncSession = Depends(get_db)
):
    res = await onboarding_service.check_email_availability(db=db, email=email)
    return EmailAvailabilityCheckResponse(**res)

@router.get(
    "/review-details",
    response_model=ReviewDetailsResponse,
    summary="Récupérer les directives d'instruction et questions posées par l'équipe SenWiseTool"
)
async def get_review_details(
    org_id: int | None = Query(None, description="ID de l'organisation"),
    token: str | None = Query(None, description="Jeton d'invitation éventuel"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    from sqlalchemy import select
    from app.models.organization import Organization, OrganizationInvitation, OnboardingDraft
    from app.models.user.user import UserRole

    org = None
    draft = None

    if org_id is not None:
        res = await db.execute(select(Organization).where(Organization.id == org_id))
        org = res.scalars().first()
        if not org:
            res_draft = await db.execute(select(OnboardingDraft).where(OnboardingDraft.id == org_id))
            draft = res_draft.scalars().first()

    if not org and not draft and token:
        res_inv = await db.execute(select(OrganizationInvitation).where(OrganizationInvitation.token == token))
        inv = res_inv.scalars().first()
        if inv:
            if inv.organization_id:
                res = await db.execute(select(Organization).where(Organization.id == inv.organization_id))
                org = res.scalars().first()
            if not org and inv.admin_email:
                res_draft = await db.execute(select(OnboardingDraft).where(OnboardingDraft.admin_email == inv.admin_email.lower()))
                draft = res_draft.scalars().first()

    if not org and not draft and current_user:
        res = await db.execute(select(Organization).where(Organization.owner_id == current_user.id))
        org = res.scalars().first()

    if not org and not draft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organisation introuvable ou accès non autorisé."
        )

    # Règle impérative de sécurité : vérification stricte de propriété du dossier
    if current_user.role != UserRole.SUPER_ADMIN:
        if org:
            is_owner = (org.owner_id == current_user.id) or (
                bool(org.contact_email) and org.contact_email.strip().lower() == current_user.email.strip().lower()
            )
            if not is_owner:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Accès non autorisé : Vous êtes actuellement connecté avec le compte '{current_user.email}'. Ce dossier de révision appartient à l'organisation '{org.name}' ({org.code}) et ne peut être consulté que par son propriétaire légitime."
                )
        elif draft:
            is_owner = bool(draft.admin_email) and draft.admin_email.strip().lower() == current_user.email.strip().lower()
            if not is_owner:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Accès non autorisé : Vous êtes actuellement connecté avec le compte '{current_user.email}'. Ce brouillon d'instruction ne correspond pas à votre compte."
                )

    if draft and not org:
        d_data = draft.draft_data or {}
        return ReviewDetailsResponse(
            organization_id=draft.id,
            name=d_data.get("name", "Organisation en cours"),
            code=d_data.get("code", "DRAFT"),
            status=d_data.get("status", "pending"),
            logo_url=d_data.get("logoUrl") or d_data.get("logo_url"),
            review_notes=d_data.get("reviewNotes") or d_data.get("review_notes"),
            custom_questions=d_data.get("customQuestions") or d_data.get("custom_questions") or [],
            existing_answers=d_data.get("customAnswers") or d_data.get("custom_answers") or {}
        )

    return ReviewDetailsResponse(
        organization_id=org.id,
        name=org.name,
        code=org.code,
        status=org.status,
        logo_url=org.logo_url,
        review_notes=org.review_notes,
        custom_questions=org.custom_questions or [],
        existing_answers=org.custom_answers or {}
    )

@router.post(
    "/review/submit",
    summary="Soumettre les réponses au complément d'information demandé par l'équipe SenWiseTool"
)
async def submit_candidate_review(
    req: CandidateReviewSubmitRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    from sqlalchemy import select
    from app.models.organization import Organization, OrgStatusEnum, OnboardingDraft
    from app.models.user.user import UserRole

    res = await db.execute(select(Organization).where(Organization.id == req.organization_id))
    org = res.scalars().first()
    
    if org:
        if current_user.role != UserRole.SUPER_ADMIN:
            is_owner = (org.owner_id == current_user.id) or (
                bool(org.contact_email) and org.contact_email.strip().lower() == current_user.email.strip().lower()
            )
            if not is_owner:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Accès non autorisé : Vous êtes actuellement connecté avec le compte '{current_user.email}'. Vous ne pouvez pas soumettre de réponses pour une organisation qui ne vous appartient pas."
                )

        # Règle de non-retransmission : un review déjà transmis ne peut plus être soumis à nouveau
        if org.status != OrgStatusEnum.REVISION_REQUESTED.value and current_user.role != UserRole.SUPER_ADMIN:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Ce complément d'information a déjà été transmis et est actuellement en cours d'instruction par l'équipe SenWiseTool. Aucune nouvelle soumission n'est permise pour le moment."
            )

        # Fusionner les réponses existantes avec les nouvelles réponses fournies
        answers = dict(org.custom_answers or {})
        answers.update(req.custom_answers)
        org.custom_answers = answers

        # Repasser le statut de l'organisation en PENDING pour ré-instruction
        org.status = OrgStatusEnum.PENDING.value
        await db.commit()
        await db.refresh(org)

        # Émettre la notification pour l'équipe SenWiseTool
        try:
            await notification_service.create_notification(
                db=db,
                title="Réponses au complément d'information reçues",
                message=f"L'organisation '{org.name}' ({org.code}) a transmis ses réponses aux questions d'instruction.",
                link=f"/dashboard/organizations/{org.id}",
                type="info",
                target_role="superadmin"
            )
        except Exception as e:
            print(f"[NOTIFICATION WARNING] Échec de la notification : {e}")

        return {
            "success": True,
            "message": "Vos réponses ont bien été transmises à l'équipe SenWiseTool. Le dossier est en cours de ré-instruction.",
            "status": org.status
        }

    # Vérifier si c'est un OnboardingDraft
    res_draft = await db.execute(select(OnboardingDraft).where(OnboardingDraft.id == req.organization_id))
    draft = res_draft.scalars().first()
    if draft:
        if current_user.role != UserRole.SUPER_ADMIN:
            is_owner = bool(draft.admin_email) and draft.admin_email.strip().lower() == current_user.email.strip().lower()
            if not is_owner:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Accès non autorisé : Vous êtes actuellement connecté avec le compte '{current_user.email}'. Vous ne pouvez pas soumettre de réponses pour ce brouillon."
                )

        draft_data = dict(draft.draft_data or {})
        draft_status = draft_data.get("status", "pending")
        if draft_status != OrgStatusEnum.REVISION_REQUESTED.value and current_user.role != UserRole.SUPER_ADMIN:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Ce complément d'information a déjà été transmis et est actuellement en cours d'instruction."
            )

        draft_data = dict(draft.draft_data or {})
        answers = dict(draft_data.get("customAnswers", {}))
        answers.update(req.custom_answers)
        draft_data["customAnswers"] = answers
        draft_data["status"] = OrgStatusEnum.PENDING.value
        draft.draft_data = draft_data
        await db.commit()

        org_name = draft_data.get("name", "Organisation en cours")
        org_code = draft_data.get("code", "DRAFT")
        try:
            await notification_service.create_notification(
                db=db,
                title="Réponses au complément d'information reçues",
                message=f"L'organisation '{org_name}' ({org_code}) a transmis ses réponses aux questions d'instruction.",
                link=f"/dashboard/organizations/{draft.id}",
                type="info",
                target_role="superadmin"
            )
        except Exception as e:
            print(f"[NOTIFICATION WARNING] Échec de la notification : {e}")

        return {
            "success": True,
            "message": "Vos réponses ont bien été transmises au Super Administrateur. Le dossier est en cours de ré-instruction.",
            "status": OrgStatusEnum.PENDING.value
        }

    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organisation introuvable.")

@router.get(
    "/invitation-status",
    summary="Vérifier la validité et le statut de propriété d'un lien d'invitation"
)
async def check_invitation_status(
    token: str = Query(..., description="Jeton d'invitation"),
    current_user: User | None = Depends(get_optional_current_user),
    db: AsyncSession = Depends(get_db)
):
    from sqlalchemy import select, func
    from app.models.organization import OrganizationInvitation
    from app.models.user.user import UserRole
    from datetime import datetime, timezone

    res = await db.execute(select(OrganizationInvitation).where(OrganizationInvitation.token == token))
    invitation = res.scalars().first()

    if not invitation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ce lien d'invitation est invalide ou introuvable."
        )

    now = datetime.now(timezone.utc)
    if invitation.token_expires_at < now:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Ce lien d'invitation a expiré. Veuillez solliciter une nouvelle invitation auprès du Super Administrateur."
        )

    # Vérifier si l'invitation a déjà été utilisée ou si le compte existe déjà
    res_user = await db.execute(
        select(User).where(func.lower(User.email) == invitation.admin_email.strip().lower())
    )
    existing_user = res_user.scalars().first()
    is_already_activated = invitation.is_used or (existing_user is not None)

    if is_already_activated:
        if current_user:
            if current_user.role == UserRole.SUPER_ADMIN or current_user.email.strip().lower() == invitation.admin_email.strip().lower():
                return {
                    "valid": False,
                    "status": "already_activated_authenticated",
                    "admin_email": invitation.admin_email,
                    "is_owner": True,
                    "message": "Cette invitation a déjà été validée. Vous êtes actuellement connecté avec le compte propriétaire."
                }
            else:
                return {
                    "valid": False,
                    "status": "conflict",
                    "admin_email": invitation.admin_email,
                    "current_email": current_user.email,
                    "is_owner": False,
                    "message": f"Conflit de compte : Vous êtes connecté avec le compte '{current_user.email}', alors que cette invitation appartient à '{invitation.admin_email}'."
                }
        else:
            return {
                "valid": False,
                "status": "already_activated_unauthenticated",
                "admin_email": invitation.admin_email,
                "is_owner": False,
                "message": "Cette invitation a déjà été validée par OTP et le compte administrateur est configuré. Veuillez vous connecter avec vos identifiants pour accéder à votre espace."
            }

    # L'invitation est encore en attente d'activation
    if current_user and current_user.role != UserRole.SUPER_ADMIN:
        if current_user.email.strip().lower() != invitation.admin_email.strip().lower():
            return {
                "valid": False,
                "status": "conflict",
                "admin_email": invitation.admin_email,
                "current_email": current_user.email,
                "is_owner": False,
                "message": f"Conflit de compte : Vous êtes actuellement connecté avec le compte '{current_user.email}'. Cette invitation est exclusivement réservée à '{invitation.admin_email}'. Veuillez vous déconnecter pour continuer."
            }

    return {
        "valid": True,
        "status": "pending_activation",
        "admin_email": invitation.admin_email,
        "is_owner": True,
        "message": "Invitation valide."
    }


