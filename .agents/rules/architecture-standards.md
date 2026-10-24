# Standards d'Architecture et de Conception - SenWiseTool

Ce document définit les règles architecturales impératives pour l'ensemble du projet SenWiseTool (Backend & Frontend). Ces règles doivent être strictement respectées lors de toute création ou modification de code.

---

## 1. Architecture Backend (FastAPI / SQLAlchemy / Python)

### A. Structure des Dossiers par Responsabilité et Domaine
Chaque concept dispose de son propre dossier racine, découpé en sous-dossiers par domaine fonctionnel :

```
backend/app/
├── core/                  # Configuration, sécurité centrale, exceptions globales
│   ├── config.py          # Validation Pydantic Settings (variables issues de .env)
│   └── security.py        # Hashing Argon2, tokens JWT, TOTP
├── models/                # Modèles de base de données SQLAlchemy (uniquement DB)
│   ├── auth/              # Modèles liés à l'authentification (tokens, sessions, devices)
│   └── user/              # Modèle User, rôles, statuts
├── schemas/               # Schémas de validation Pydantic (DTOs d'entrée et sortie)
│   ├── auth/              # Schémas Login, MFA, Refresh, ResetPassword
│   └── user/              # Schémas UserRead, UserCreate, UserUpdate
├── services/              # Logique métier pure (Business Logic, découplée des routes HTTP)
│   ├── auth/              # AuthService, TokenService, MfaService
│   └── user/              # UserService
├── api/                   # Couche transport HTTP / Endpoints FastAPI
│   ├── deps.py            # Dépendances FastAPI (get_db, get_current_user, require_active_user)
│   └── v1/
│       ├── router.py      # Routeur principal v1
│       ├── endpoints/
│       │   ├── auth.py    # Routes /api/v1/auth/*
│       │   └── users.py   # Routes /api/v1/users/*
└── db/                    # Connexion DB, session maker, base déclarative
    ├── database.py
    └── base.py
```

### B. Règles de Sécurité et Politiques Backend
1. **Variables d'Environnement** :
   - Toute clé sensible (`SECRET_KEY`, `ALGORITHM`, `ACCESS_TOKEN_EXPIRE_MINUTES`, etc.) doit obligatoirement être déclarée dans `.env` avant d'être validée dans `app/core/config.py`. Aucune valeur hardcodée.
2. **Protection des Tokens (Cookies HttpOnly)** :
   - Les tokens de session (`access_token`, `refresh_token`) ne doivent JAMAIS transiter dans le corps de réponse JSON exploitable par le JavaScript client.
   - Ils sont directement injectés dans les cookies de réponse avec les attributs : `httponly=True`, `secure=True` (ou conditionnel dev), `samesite="lax"`, `path="/"`.
3. **Condition d'Accès Stricte au Dashboard** :
   - Le statut de l'utilisateur doit être strictement `ACTIVE` (`status == UserStatus.ACTIVE`).
   - L'email doit être vérifié (`is_email_verified == True`).
   - Tout autre statut (`PENDING`, `SUSPENDED`, `LOCKED`) interdit l'accès et renvoie une erreur explicite.
4. **Session Active Unique (Single Active Session)** :
   - Un utilisateur ne peut avoir qu'une seule session active à la fois.
   - Toute nouvelle connexion réussie invalide/révoque automatiquement la ou les sessions antérieures de cet utilisateur.
5. **Gestion Anti-Brute-Force & Verrouillage** :
   - Compteur `failed_login_attempts` incrémenté à chaque tentative invalide.
   - À partir de 5 échecs consécutifs, le compte est verrouillé pour 30 minutes (`locked_until`).
   - Tout compte verrouillé est immédiatement éjecté et bloqué.

---

## 2. Architecture Frontend (Next.js / TypeScript / React)

### A. Séparation Stricte Logique / Présentation
- **Aucune logique métier dans le JSX/HTML** : Les composants React sont purement présentationnels ou délèguent leur comportement à des Custom Hooks (`hooks/useAuth.ts`, `hooks/useLoginForm.ts`).
- **Gestion des Formulaires & Erreurs en Temps Réel** :
  - Les erreurs de validation ou de retour API doivent cibler précisément le champ incriminé (`identifier`, `password`, `code`).
  - Affichage visuel immédiat sous le champ concerné avec texte explicite et mise en évidence de la bordure.

### B. Centralisation des Types, Endpoints et Services API
```
web/src/
├── types/                 # Interfaces et types TypeScript stricts
│   ├── auth.ts            # Types LoginCredentials, User, AuthState, Session
│   └── api.ts             # ApiResponse<T>, ApiError
├── constants/
│   └── endpoints.ts       # URLs et endpoints centralisés en UN SEUL fichier
├── services/
│   └── api/
│       ├── client.ts      # Client fetch/axios configuré (credentials: 'include')
│       └── auth.service.ts # Appels API auth (login, logout, mfa, forgotPassword, me)
└── hooks/
    └── useAuth.ts         # Hook d'authentification centralisé
```

### C. Refresh Automatique et Éjection de Session
- Un intercepteur API écoute les réponses `401 Unauthorized`.
- Lorsqu'une requête échoue sur expiration de l'accès, le client tente un appel silencieux à `/api/v1/auth/refresh`.
- Si le rafraîchissement réussit, la requête initiale est relancée sans interruption visible pour l'utilisateur.
- Si le rafraîchissement échoue (token révoqué, session expirée ou compte bloqué) : l'utilisateur est immédiatement déconnecté et redirigé vers la page `/login`.

### D. Identité Visuelle et Assets Officiels (Logo SenWiseTool)
- **Emplacement impératif** : Le logo officiel de SenWiseTool réside dans le dossier `applications/web/public/` (fichiers `/logo.png` et `/logo_icon.png`).
- **Interdiction formelle** : Ne JAMAIS générer ni concevoir de faux logos, sceaux SVG inventés ou symboles mockés pour représenter SenWiseTool.
- **Usage standard** : Dans l'ensemble de l'interface, des composants (Header, Sidebar, Login, Attestations, Bordereaux officiels), utiliser systématiquement le vrai logo (`/logo.png`).

