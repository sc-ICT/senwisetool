---
name: notification-management
description: >-
  Operational standard and architectural reference for real-time in-app notifications
  across SenWiseTool. Covers event-driven dispatch (onboarding submissions, review requests,
  responses, validation), role-based and user-targeted routing, unread badge state management,
  and deep-link click navigation.
---

# SenWiseTool Notification Management Standard

This skill establishes the universal standard for notification dispatch, recipient routing, unread state tracking, and user navigation across SenWiseTool backend and frontend modules.

---

## 1. Core Principles

1. **Deterministic Event Triggers**:
   - Every key lifecycle change (e.g. onboarding dossier submission, review request, review answer submission, organisation activation) **must** dispatch an in-app notification in addition to any transactional email.
2. **Flexible Dual Targeting**:
   - Notifications can target an individual `user_id` (e.g., specific organisation administrator) or a broader `target_role` (e.g., `superadmin`, `SUPER_ADMIN`, `AUDITOR`).
   - Role matching in queries must be case-insensitive to ensure robust cross-platform compatibility.
3. **Actionable Deep Linking**:
   - Every notification should include a relative `link` (e.g. `/dashboard/organizations/42` or `/onboarding/review?org_id=42`).
   - Clicking a notification marks it as read and redirects the user directly to the targeted entity.
4. **Resilient & Non-Blocking**:
   - Notification dispatch should never abort the primary business transaction. Always wrap notification calls in safe error handling to protect the main request workflow.

---

## 2. Event Dispatch Matrix

| Event | Trigger Location | Target | Title | Deep Link |
| :--- | :--- | :--- | :--- | :--- |
| **Dossier Onboarding Soumis** | `submit_onboarding` | `superadmin` | Nouvelle demande d'onboarding | `/dashboard/organizations/{id}` |
| **Demande de Révision Émise** | `review (action="revision_requested")` | `user_id` (Owner) | Complément d'information requis | `/onboarding/review?org_id={id}` |
| **Réponses du Candidat Transmises** | `submit_candidate_review` | `superadmin` | Réponses au complément d'information reçues | `/dashboard/organizations/{id}` |
| **Organisation Validée & Activée** | `review (action="active")` | `user_id` (Owner) | Organisation activée avec succès | `/dashboard` |
| **Organisation Suspendue** | `review (action="suspended")` | `user_id` (Owner) | Organisation suspendue | `/onboarding/pending` |
| **Organisation Rejetée** | `review (action="rejected")` | `user_id` (Owner) | Dossier non retenu | `/onboarding/pending` |

---

## 3. Backend Implementation Standard

### A. SQLAlchemy Model (`app.models.notification.Notification`)
```python
class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    target_role = Column(String(50), nullable=True, index=True)
    title = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)
    link = Column(String(500), nullable=True)
    type = Column(String(50), default="info", nullable=False)  # info, warning, success, error
    is_read = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
```

### B. Dispatching a Notification (`notification_service.create_notification`)
```python
from app.services.notification.notification_service import notification_service

# Pour alerter le Super Admin :
await notification_service.create_notification(
    db=db,
    title="Nouvelle demande d'onboarding",
    message=f"L'organisation '{org.name}' ({org.code}) a soumis son dossier complet.",
    link=f"/dashboard/organizations/{org.id}",
    type="info",
    target_role="superadmin"
)

# Pour alerter un utilisateur spécifique :
await notification_service.create_notification(
    db=db,
    title="Dossier validé",
    message=f"Félicitations, l'organisation '{org.name}' est désormais active.",
    link="/dashboard",
    type="success",
    user_id=org.owner_id
)
```

### C. Available API Endpoints
- `GET /api/v1/notifications/?limit=30` : Fetches user-targeted + role-targeted notifications with `unread_count` and `total`.
- `PATCH /api/v1/notifications/{id}/read` : Marks an individual notification as read.
- `POST /api/v1/notifications/read-all` : Marks all unread notifications as read.

---

## 4. Frontend Integration Pattern

### A. Notification API Service (`notification.service.ts`)
```typescript
import { ENDPOINTS } from "@/constants/endpoints";
import { apiClient } from "./client";

export const notificationApiService = {
  async getNotifications(unreadOnly = false) {
    const url = unreadOnly ? `${ENDPOINTS.NOTIFICATIONS.LIST}?unread_only=true` : ENDPOINTS.NOTIFICATIONS.LIST;
    return apiClient(url, { method: "GET" });
  },

  async markAsRead(id: number | string) {
    return apiClient(ENDPOINTS.NOTIFICATIONS.MARK_READ(id), { method: "PATCH" });
  },

  async markAllAsRead() {
    return apiClient(ENDPOINTS.NOTIFICATIONS.MARK_ALL_READ, { method: "POST" });
  },
};
```

### B. Header Bell Component Pattern (`Header.tsx`)
1. **Periodic Refresh**: Polling interval every 25–30 seconds ensures near real-time updates without WebSockets overhead.
2. **Unread Counter Badge**: Dynamically display red pulse badge with unread count (`9+` when count > 9).
3. **Click Navigation**:
```typescript
const handleNotificationClick = async (notif: AppNotification) => {
  if (!notif.is_read) {
    await notificationApiService.markAsRead(notif.id);
    setNotifications((prev) =>
      prev.map((n) => (n.id === notif.id ? { ...n, is_read: true } : n))
    );
    setUnreadCount((c) => Math.max(0, c - 1));
  }
  setNotificationsOpen(false);
  if (notif.link) {
    router.push(notif.link);
  }
};
```

---

## 5. Security & Multi-Tenant Isolation

1. **Role Enforcement**:
   - Notifications destined for `superadmin` are never queryable by standard tenant `ADMIN_ORG` or `USER` roles.
2. **User Scope**:
   - Regular users only receive notifications where `Notification.user_id == user.id` or `Notification.target_role == user.role.value`.
3. **Data Hygiene**:
   - Clean up notifications older than 90 days via maintenance cron or background tasks.
