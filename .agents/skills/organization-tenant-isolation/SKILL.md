---
name: organization-tenant-isolation
description: >-
  Operational standard and security architectural reference for strict multi-tenant
  isolation and organization scoping across SenWiseTool. Guarantees that every user,
  action, resource (projects, forms, IoT nodes, campaigns, roles, members), API query,
  and UI view is strictly and sovereignly scoped to the user's attached organization,
  preventing cross-organization data leaks, orphan entities, and unprivileged admin access.
---

# SenWiseTool Organization Tenant Isolation Standard

Ce skill établit le **standard opérationnel et architectural universel** régissant le cloisonnement multi-tenant (multi-organisations), l'isolation cryptographique et relationnelle des données, et le rattachement obligatoire de toute action utilisateur à son organisation légitime dans **SenWiseTool**.

---

## 1. Principes Fondamentaux de Sécurité (Les 5 Piliers)

### 1. La Règle d'Or : 1 Utilisateur = 1 Organisation Rattachée
- Dans SenWiseTool, tout utilisateur non Super Admin appartient **obligatoirement et exclusivement** à une seule organisation active.
- Cette appartenance est scellée soit au titre de **Propriétaire Fondateur** (`Organization.owner_id == user.id`), soit au titre de **Collaborateur Membre rattaché** (`OrganizationMember.user_id == user.id`).
- Aucun utilisateur régulier ne peut être orphelin ou appartenir à plusieurs organisations simultanément.

### 2. Cloisonnement Absolu des Données (Zéro Fuite Inter-Tenants)
- Aucune entité métier (projets, capteurs IoT, campagnes, formulaires de collecte, télémesures, rôles, collaborateurs, attestations) ne peut exister sans un `organization_id` intègre et vérifié.
- **Toute requête de lecture ou d'écriture (SELECT, UPDATE, DELETE)** doit obligatoirement inclure un filtre de cloisonnement :  
  `WHERE entity.organization_id == resolved_org_id`.
- Un utilisateur d'une organisation $A$ ne doit **jamais** pouvoir lire, modifier, lister ou deviner les ressources d'une organisation $B$.

### 3. Résolution Serveur Souveraine (Zero Client Trust)
- Ne **jamais** faire confiance à un `org_id` fourni par le client (dans une URL, query param ou payload JSON).
- Le backend résout systématiquement l'organisation légitime de l'utilisateur à partir de son jeton de session sécurisé (`current_user`) via `organization_service.get_user_organization(db, current_user)` ou `resolve_org_id(org_id_or_me, current_user, db)`.
- Si un client transmet un ID explicite différent de son organisation légitime, l'accès est rejeté immédiatement avec un statut `HTTP 403 FORBIDDEN` (ou `HTTP 404`).

### 4. Ségrégation des Privilèges : Admin / Propriétaire vs Collaborateurs
- **Espace « Mon Organisation » (`/dashboard/my-organization`)** : Réservé **exclusivement** au Propriétaire Fondateur et aux Administrateurs délégués (`ADM`). Il donne accès au dossier d'agrément, à la mise à jour des coordonnées, aux documents officiels (Kbis) et au téléchargement de l'attestation officielle de conformité.
- **Collaborateurs Simples (`USER`, `OPERATOR`, `MANAGER`, `AUDITOR`, `LECTEUR`)** :
  - N'ont **pas** accès à `/dashboard/my-organization` (leur accès direct est refoulé et redirigé vers `/dashboard`).
  - L'onglet « Mon Organisation » est **masqué** de leur barre latérale.
  - Le badge sous le logo SenWiseTool affiche leur rôle métier réel (ex. `Lecteur`, `Opérateur terrain`) et l'organisation rattachée, évitant l'usurpation visuelle `Admin Organisation`.

### 5. Interdiction Absolue des Données Factices de Repli (No Mock Fallbacks)
- Il est formellement interdit d'afficher des données par défaut ou des textes codés en dur (`"Mon Organisation"`, `"#ORG"`, `"Sénégal"`, 1420 nœuds, 1 collaborateur, etc.) en cas d'absence d'organisation.
- Si un compte ne dispose pas d'organisation active, l'interface doit afficher un état d'arrêt propre et sécurisé (`"Aucune organisation associée à ce compte"`).

---

## 2. Cycle de Résolution du Tenant

```mermaid
flowchart TD
    Req[Requête Utilisateur Authentifié] --> RoleCheck{Rôle Global ?}
    RoleCheck -- SUPER_ADMIN --> SuperAdminAccess[Accès Transversal / Supervision Globale]
    RoleCheck -- Utilisateur Régulier --> ResolveOrg[organization_service.get_user_organization]
    
    ResolveOrg --> CheckOwner{Est Propriétaire ?}
    CheckOwner -- Oui (owner_id == user.id) --> OrgFound[Organisation Résolue (Rôle: Propriétaire Fondateur)]
    CheckOwner -- Non --> CheckMember{Est dans OrganizationMember ?}
    
    CheckMember -- Oui (user_id == user.id) --> MemberFound[Organisation Résolue (Rôle: Selon role_code)]
    CheckMember -- Non --> Forbidden[HTTP 403 : Aucune organisation légitime]
    
    OrgFound --> RBACCheck{Action demandée ?}
    MemberFound --> RBACCheck
    
    RBACCheck -- Admin / Agrément (my-organization) --> CheckAdmin{is_org_admin ?}
    CheckAdmin -- Oui --> AllowAdmin[Autoriser accès gestion org]
    CheckAdmin -- Non --> DenyAdmin[Refuser / Rediriger vers /dashboard]
    
    RBACCheck -- Opérations métier (projets, formulaires, membres) --> ScopeQuery[Injecter WHERE organization_id = org.id]
```

---

## 3. Standard d'Implémentation Backend (FastAPI / SQLAlchemy)

### A. Méthode Centrale de Résolution (`organization_service.py`)
Toute résolution de tenant dans le backend doit s'appuyer sur la méthode souveraine :

```python
async def get_user_organization(self, db: AsyncSession, user: User) -> Optional[Organization]:
    """
    Résout de façon souveraine l'organisation à laquelle appartient l'utilisateur :
    1. En tant que Propriétaire Fondateur (Organization.owner_id == user.id ou contact_email == user.email)
    2. En tant que Membre / Collaborateur rattaché (OrganizationMember.user_id ou email)
    """
    if not user:
        return None

    # 1. Vérifier si l'utilisateur est propriétaire ou email de contact officiel
    res = await db.execute(
        select(Organization)
        .options(selectinload(Organization.owner))
        .where(
            or_(
                Organization.owner_id == user.id,
                func.lower(Organization.contact_email) == user.email.lower()
            )
        )
    )
    org = res.scalars().first()
    if org:
        return org

    # 2. Vérifier si l'utilisateur est membre d'une organisation
    from app.models.organization.member import OrganizationMember
    mem_res = await db.execute(
        select(OrganizationMember)
        .options(selectinload(OrganizationMember.organization))
        .where(
            or_(
                OrganizationMember.user_id == user.id,
                func.lower(OrganizationMember.email) == user.email.lower()
            )
        )
    )
    mem = mem_res.scalars().first()
    if mem:
        if mem.organization:
            return mem.organization
        if mem.organization_id:
            org_res = await db.execute(
                select(Organization)
                .options(selectinload(Organization.owner))
                .where(Organization.id == mem.organization_id)
            )
            return org_res.scalars().first()

    return None
```

### B. Résolution d'identifiant d'organisation pour les endpoints (`resolve_org_id`)
Tout endpoint acceptant `{org_id}` (ou la valeur virtuelle `"me"`) doit valider les droits :

```python
async def resolve_org_id(org_id_or_me: str, current_user: User, db: AsyncSession) -> int:
    """Résout l'ID d'organisation ('me' ou entier) et valide strictement les droits d'accès."""
    if org_id_or_me == "me":
        org = await organization_service.get_user_organization(db=db, user=current_user)
        if org:
            return org.id

        if current_user.role == UserRole.SUPER_ADMIN:
            first_org_res = await db.execute(select(Organization).limit(1))
            first_org = first_org_res.scalars().first()
            if first_org:
                return first_org.id

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aucune organisation active associée à ce compte."
        )

    try:
        target_id = int(org_id_or_me)
    except ValueError:
        raise HTTPException(status_code=400, detail="Identifiant d'organisation invalide.")

    # Contrôle RBAC : si non Super Admin, interdire l'accès à une autre organisation
    if current_user.role != UserRole.SUPER_ADMIN:
        own_org = await organization_service.get_user_organization(db=db, user=current_user)
        if not own_org or own_org.id != target_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Accès interdit aux données d'une autre organisation."
            )

    return target_id
```

### C. Modèle de Données Standard pour toute Entité Métier
Toute table métier liée à une organisation doit déclarer la clé étrangère avec cascade et index :

```python
class BusinessEntity(Base):
    __tablename__ = "business_entities"

    id = Column(Integer, primary_key=True, index=True)
    # RÈGLE OBLIGATOIRE : Rattachement au tenant
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # ... autres colonnes métier ...

    organization = relationship("Organization", back_populates="business_entities")
```

### D. Scoping Strict dans les Requêtes SQL
Toujours filtrer explicitement :

```python
# CORRECT :
stmt = (
    select(Project)
    .where(Project.organization_id == resolved_org_id)
    .where(Project.status == "active")
)

# INTERDIT (Fuite inter-organisations) :
stmt = select(Project).where(Project.status == "active")  # ❌ DANGEREUX !
```

### E. Session `/api/v1/auth/me` Enrichie
L'endpoint `/api/v1/auth/me` doit toujours retourner les métadonnées de tenant :
- `organization_id` (int)
- `organization_name` (str)
- `organization_code` (str)
- `is_org_owner` (bool)
- `is_org_admin` (bool)
- `org_role_name` (str)

---

## 4. Standard d'Implémentation Frontend (Next.js / TypeScript)

### A. Typage Utilisateur (`types/auth.ts`)
```typescript
export interface User {
  id: number;
  email: string;
  username: string;
  full_name: string | null;
  role: UserRole;
  status: UserStatus;
  is_email_verified: boolean;
  mfa_enabled: boolean;
  last_login_at: string | null;
  created_at: string;
  // Contexte d'organisation rattachée
  organization_id?: number | null;
  organization_name?: string | null;
  organization_code?: string | null;
  is_org_owner?: boolean;
  is_org_admin?: boolean;
  org_role_name?: string | null;
}
```

### B. Filtrage des Accès dans la Barre Latérale (`Sidebar.tsx`)
```typescript
const isSuperAdmin = currentUser?.role === "SUPER_ADMIN" || currentUser?.role?.toUpperCase() === "SUPER_ADMIN";

const isOrgAdmin = Boolean(
  currentUser?.is_org_admin ||
  currentUser?.is_org_owner ||
  currentUser?.role === "ADMIN_ORG" ||
  currentUser?.role?.toUpperCase() === "ADMIN_ORG"
);

const allNavItems = [
  { id: "dashboard", href: "/dashboard", label: t("sidebar.dashboard"), icon: LayoutDashboard },
  // Réservé à l'administrateur d'organisation UNIQUEMENT
  { id: "myOrganization", href: "/dashboard/my-organization", label: t("sidebar.myOrganization"), icon: Building2, orgAdminOnly: true },
  { id: "users", href: "/dashboard/users", label: t("sidebar.users"), icon: Users },
  // ...
];

const navItems = allNavItems.filter((item) => {
  if (item.superAdminOnly && !isSuperAdmin) return false;
  // Ne montrer "Mon Organisation" qu'aux vrais administrateurs d'organisation
  if (item.orgAdminOnly && (!isOrgAdmin || isSuperAdmin)) return false;
  return true;
});
```

### C. Identité de l'Organisation sous le Logo
Ne jamais afficher "Admin Organisation" pour un collaborateur régulier :
```tsx
<div className="flex flex-col gap-0.5 mt-0.5">
  <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20 w-fit">
    {isSuperAdmin
      ? "Super Admin"
      : isOrgAdmin
      ? currentUser?.is_org_owner
        ? "Propriétaire Admin"
        : "Admin Organisation"
      : currentUser?.org_role_name || "Collaborateur"}
  </span>
  {!isSuperAdmin && currentUser?.organization_name && (
    <span
      className="text-[10px] text-slate-500 dark:text-slate-400 font-medium truncate max-w-[150px]"
      title={currentUser.organization_name}
    >
      {currentUser.organization_name}
    </span>
  )}
</div>
```

### D. Protection de la Page « Mon Organisation » (`my-organization/page.tsx`)
```typescript
useEffect(() => {
  if (!isCheckingAuth && user) {
    const isSuper = user.role === "SUPER_ADMIN" || user.role?.toUpperCase() === "SUPER_ADMIN";
    const isOrgAdmin = Boolean(
      user.is_org_admin ||
      user.is_org_owner ||
      user.role === "ADMIN_ORG" ||
      user.role?.toUpperCase() === "ADMIN_ORG"
    );
    if (isSuper) {
      router.push("/dashboard/organizations");
    } else if (!isOrgAdmin) {
      // Rediriger immédiatement les collaborateurs simples vers le tableau de bord opérationnel
      router.push("/dashboard");
    }
  }
}, [user, isCheckingAuth, router]);
```

---

## 5. Matrice des Rôles et Droits d'Accès

| Espace / Action | Super Admin | Propriétaire Fondateur | Admin Délégué (ADM) | Manager Projet (MGR) | Opérateur / Lecteur |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **Gestion globale des organisations** (`/dashboard/organizations`) | ✅ | ❌ | ❌ | ❌ | ❌ |
| **Mon Organisation (Agrément & Profil)** (`/dashboard/my-organization`) | ❌ (redirigé) | ✅ | ✅ | ❌ (redirigé) | ❌ (redirigé) |
| **Téléchargement Attestation & Sceau officiel** | ✅ (supervision) | ✅ | ✅ | ❌ | ❌ |
| **Gestion des Membres & Invitations** (`/dashboard/users`) | ✅ | ✅ | ✅ | Consultation | Consultation (selon matrice) |
| **Gestion des Rôles & Permissions** (`/dashboard/roles`) | ✅ | ✅ | ✅ | ❌ | ❌ |
| **Projets & Campagnes Terrain** | ✅ | ✅ | ✅ | ✅ | Selon assignation |
| **Collecte & Formulaires** | ✅ | ✅ | ✅ | ✅ | ✅ |

---

## 6. Checklist de Contrôle & Audit de Sécurité (10 Points)

Avant de valider ou déployer toute nouvelle fonctionnalité, vérifier :

1. [ ] **Clé étrangère obligatoire** : La table SQL contient-elle `organization_id NOT NULL REFERENCES organizations(id)` ?
2. [ ] **Résolution côté serveur** : L'`organization_id` est-il extrait de `current_user` ou validé via `resolve_org_id` ?
3. [ ] **Scoping SELECT** : Toutes les requêtes filtrent-elles explicitement sur `organization_id` ?
4. [ ] **Scoping UPDATE/DELETE** : Est-il impossible de modifier une ressource appartenant à une autre organisation ?
5. [ ] **Filtrage Sidebar** : Les onglets administrateur (`myOrganization`) sont-ils masqués pour les non-admins ?
6. [ ] **Redirection de sécurité UI** : Les composants d'administration refoulent-ils les collaborateurs simples vers `/dashboard` ?
7. [ ] **Zéro Mock Fallback** : Aucun nom d'organisation factice (`"Mon Organisation"`, `"#ORG"`) n'est codé en dur en cas d'erreur.
8. [ ] **Identité Tenant affichée** : L'interface affiche-t-elle clairement le nom de l'organisation rattachée et le rôle exact de l'utilisateur ?
9. [ ] **Redirection post-invitation** : Les collaborateurs invités sont-ils redirigés vers `/dashboard` et non vers `/dashboard/my-organization` ?
10. [ ] **Protection Propriétaire Fondateur** : Le compte propriétaire fondateur est-il protégé contre toute suspension ou révocation par d'autres membres ?
