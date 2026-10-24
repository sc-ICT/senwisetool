from fastapi import APIRouter
from app.api.v1.endpoints.organization.invitations import router as invitations_router
from app.api.v1.endpoints.organization.onboarding import router as onboarding_router
from app.api.v1.endpoints.organization.organizations import router as organizations_router
from app.api.v1.endpoints.organization.members import router as members_router
from app.api.v1.endpoints.organization.roles import router as roles_router

organization_api_router = APIRouter()
organization_api_router.include_router(invitations_router, prefix="/invitations", tags=["organizations-invitations"])
organization_api_router.include_router(organizations_router, prefix="", tags=["organizations"])
organization_api_router.include_router(members_router, prefix="", tags=["organizations-members"])
organization_api_router.include_router(roles_router, prefix="", tags=["organizations-roles"])

__all__ = [
    "invitations_router",
    "onboarding_router",
    "organizations_router",
    "members_router",
    "roles_router",
    "organization_api_router",
]
