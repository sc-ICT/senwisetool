from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, ConfigDict, computed_field

class NotificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: int
    user_id: Optional[int] = None
    target_role: Optional[str] = None
    title: str
    message: str
    link: Optional[str] = None
    type: str
    is_read: bool
    created_at: datetime

    @computed_field
    @property
    def userId(self) -> Optional[int]:
        return self.user_id

    @computed_field
    @property
    def targetRole(self) -> Optional[str]:
        return self.target_role

    @computed_field
    @property
    def isRead(self) -> bool:
        return self.is_read

    @computed_field
    @property
    def createdAt(self) -> datetime:
        return self.created_at

class NotificationListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    items: List[NotificationResponse]
    unread_count: int
    total: int

    @computed_field
    @property
    def notifications(self) -> List[NotificationResponse]:
        return self.items

    @computed_field
    @property
    def unreadCount(self) -> int:
        return self.unread_count
