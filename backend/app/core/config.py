from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    PROJECT_NAME: str = "SenWiseTool Backend"
    API_V1_STR: str = "/api/v1"
    ENVIRONMENT: str = "development"
    FRONTEND_URL: str = "http://localhost:3000"
    
    # Database
    DATABASE_URL: str
    
    # Security & JWT
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 1
    REFRESH_TOKEN_EXPIRE_DAYS_REMEMBER_ME: int = 30
    MFA_TOKEN_EXPIRE_MINUTES: int = 5
    PASSWORD_RESET_TOKEN_EXPIRE_MINUTES: int = 15
    
    # Security Policies
    MAX_FAILED_LOGIN_ATTEMPTS: int = 5
    ACCOUNT_LOCKOUT_MINUTES: int = 30
    
    # Cookie Configuration
    COOKIE_SECURE: bool = False
    COOKIE_SAMESITE: str = "lax"
    
    # Configuration Service Email / SMTP
    SMTP_ENABLED: bool = True
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_TLS: bool = True
    SMTP_SSL: bool = False
    EMAILS_FROM_EMAIL: str = "noreply@senwisetool.com"
    EMAILS_FROM_NAME: str = "SenWiseTool Platform"

    # ==============================================================================
    # CONFIGURATION DU STOCKAGE DES MÉDIAS (LOCAL DEV & S3 PROD)
    # ==============================================================================
    # "local" pour stockage disque en développement, "s3" pour Amazon S3 / MinIO en production
    STORAGE_BACKEND: str = "local"
    # Chemin racine pour le stockage local (relatif à la racine de backend ou absolu)
    STORAGE_LOCAL_ROOT: str = "storage"
    # Préfixe d'accès URL pour servir les fichiers en local
    STORAGE_BASE_URL: str = "/storage"
    
    # Paramètres S3 (actifs quand STORAGE_BACKEND = "s3")
    S3_BUCKET_NAME: str = "senwisetool-media"
    S3_REGION: str = "eu-west-3"
    S3_ACCESS_KEY_ID: str = ""
    S3_SECRET_ACCESS_KEY: str = ""
    S3_ENDPOINT_URL: str | None = None  # Utile pour MinIO, Wasabi, OVH ou LocalStack
    S3_PUBLIC_URL: str | None = None    # URL de CDN ou de domaine personnalisé si applicable

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

settings = Settings()
