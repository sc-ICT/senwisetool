from fastapi import APIRouter
from app.api.v1.endpoints import auth, users
from app.api.v1.endpoints.organization import organization_api_router, onboarding_router
from app.api.v1.endpoints.notification.notifications import router as notifications_router
from app.api.v1.endpoints.plugin import plugins_router, marketplace_router
from app.api.v1.endpoints.campaign import campaigns_router
from app.api.v1.endpoints.resource import resources_router
from app.api.v1.endpoints.project import projects_router
from app.api.v1.endpoints.form import forms_router

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(organization_api_router, prefix="/organizations", tags=["organizations"])
api_router.include_router(onboarding_router, prefix="/onboarding", tags=["onboarding"])
api_router.include_router(notifications_router, prefix="/notifications", tags=["notifications"])
api_router.include_router(plugins_router, prefix="/plugins", tags=["plugins"])
api_router.include_router(marketplace_router, prefix="/marketplace", tags=["marketplace"])
api_router.include_router(campaigns_router, prefix="/campaigns", tags=["campaigns"])
api_router.include_router(resources_router, prefix="/resources", tags=["resources"])
api_router.include_router(projects_router, prefix="/projects", tags=["projects"])
api_router.include_router(forms_router, prefix="", tags=["forms"])


