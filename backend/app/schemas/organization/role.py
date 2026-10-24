from typing import Optional, Dict, Any
from datetime import datetime
from pydantic import BaseModel, ConfigDict

class RoleCreateRequest(BaseModel):
    name: str
    code: str
    description: Optional[str] = None
    permissions: Dict[str, Any] = {}

class RoleUpdateRequest(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    permissions: Optional[Dict[str, Any]] = None

class RoleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    organization_id: Optional[int] = None
    name: str
    code: str
    description: Optional[str] = None
    members_count: int = 0
    status: str = "active"
    is_system: bool = False
    permissions: Dict[str, Any] = {}
    created_at: datetime
