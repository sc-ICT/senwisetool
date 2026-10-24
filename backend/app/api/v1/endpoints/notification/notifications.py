from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import get_db
from app.api.deps import get_current_user
from app.models.user.user import User
from app.schemas.notification.notification import NotificationListResponse, NotificationResponse
from app.services.notification.notification_service import notification_service

router = APIRouter()

@router.get(
    "/",
    response_model=NotificationListResponse,
    summary="Récupérer les notifications de l'utilisateur connecté"
)
async def get_my_notifications(
    limit: int = 30,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    items, unread_count, total = await notification_service.get_user_notifications(
        db=db,
        user=current_user,
        limit=limit
    )
    return NotificationListResponse(
        items=[NotificationResponse.model_validate(item) for item in items],
        unread_count=unread_count,
        total=total
    )

@router.patch(
    "/{notification_id}/read",
    summary="Marquer une notification comme lue"
)
async def mark_notification_as_read(
    notification_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    await notification_service.mark_as_read(
        db=db,
        notification_id=notification_id,
        user=current_user
    )
    return {"success": True, "message": "Notification marquée comme lue"}

@router.post(
    "/read-all",
    summary="Marquer toutes les notifications comme lues"
)
async def mark_all_notifications_as_read(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    count = await notification_service.mark_all_as_read(
        db=db,
        user=current_user
    )
    return {"success": True, "count": count, "message": f"{count} notifications marquées comme lues"}
