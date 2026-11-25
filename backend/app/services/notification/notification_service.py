from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, desc, func, or_
from app.models.notification.notification import Notification
from app.models.user.user import User

class NotificationService:
    async def create_notification(
        self,
        db: AsyncSession,
        title: str,
        message: str,
        link: Optional[str] = None,
        type: str = "info",
        user_id: Optional[int] = None,
        target_role: Optional[str] = None,
        commit: bool = True
    ) -> Notification:
        """
        Crée une notification en base de données pour un utilisateur spécifique ou un rôle (ex: superadmin).
        """
        notification = Notification(
            title=title,
            message=message,
            link=link,
            type=type,
            user_id=user_id,
            target_role=target_role,
            is_read=False
        )
        db.add(notification)
        if commit:
            await db.commit()
            await db.refresh(notification)
        else:
            await db.flush()
        return notification

    async def get_user_notifications(
        self,
        db: AsyncSession,
        user: User,
        limit: int = 30
    ) -> tuple[List[Notification], int, int]:
        """
        Récupère les notifications destinées à l'utilisateur ou à son rôle (ex: superadmin).
        Retourne (items, unread_count, total).
        """
        # Filtre : notification pour user.id OU pour le rôle de user (insensible à la casse)
        role_vals = ["all"]
        if hasattr(user, "role") and user.role:
            raw_r = user.role.value if hasattr(user.role, "value") else str(user.role)
            role_vals.extend([raw_r.lower(), raw_r.upper()])
            if "super" in raw_r.lower():
                role_vals.extend(["superadmin", "super_admin", "super-admin", "super_admin_role"])

        condition = or_(
            Notification.user_id == user.id,
            func.lower(Notification.target_role).in_([r.lower() for r in role_vals])
        )

        # Compte total
        res_total = await db.execute(select(func.count(Notification.id)).where(condition))
        total = res_total.scalar() or 0

        # Compte non lues
        res_unread = await db.execute(
            select(func.count(Notification.id)).where(condition, Notification.is_read.is_(False))
        )
        unread_count = res_unread.scalar() or 0

        # Liste ordonnée
        stmt = (
            select(Notification)
            .where(condition)
            .order_by(desc(Notification.created_at))
            .limit(limit)
        )
        res_items = await db.execute(stmt)
        items = list(res_items.scalars().all())

        return items, unread_count, total

    async def mark_as_read(
        self,
        db: AsyncSession,
        notification_id: int,
        user: User
    ) -> bool:
        """
        Marque une notification spécifique comme lue.
        """
        stmt = (
            update(Notification)
            .where(Notification.id == notification_id)
            .values(is_read=True)
        )
        await db.execute(stmt)
        await db.commit()
        return True

    async def mark_all_as_read(
        self,
        db: AsyncSession,
        user: User
    ) -> int:
        """
        Marque toutes les notifications de l'utilisateur ou de son rôle comme lues.
        """
        role_vals = ["all"]
        if hasattr(user, "role") and user.role:
            raw_r = user.role.value if hasattr(user.role, "value") else str(user.role)
            role_vals.extend([raw_r.lower(), raw_r.upper()])
            if "super" in raw_r.lower():
                role_vals.extend(["superadmin", "super_admin", "super-admin", "super_admin_role"])

        condition = or_(
            Notification.user_id == user.id,
            func.lower(Notification.target_role).in_([r.lower() for r in role_vals])
        )
        stmt = (
            update(Notification)
            .where(condition, Notification.is_read.is_(False))
            .values(is_read=True)
        )
        res = await db.execute(stmt)
        await db.commit()
        return res.rowcount

notification_service = NotificationService()
