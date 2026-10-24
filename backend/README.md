# SenWiseTool Backend

Ceci est le backend de l'application **SenWiseTool**. Il est construit avec [FastAPI](https://fastapi.tiangolo.com/), [SQLAlchemy (Async)](https://docs.sqlalchemy.org/), et géré par [Poetry](https://python-poetry.org/). La base de données utilisée est **PostgreSQL**.

## Prérequis

- **Python 3.11+**
- **Poetry** pour la gestion des dépendances (`pip install poetry`)
- **PostgreSQL** (installé localement ou via Docker)

## Configuration Initiale

1. **Cloner et installer les dépendances :**
   ```bash
   cd applications/backend
   poetry install
   ```

2. **Configuration de l'environnement :**
   Le projet utilise un fichier `.env` à la racine du dossier `backend`. Vous pouvez le modifier pour pointer vers votre base de données locale.
   
   Exemple de `.env` :
   ```env
   DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/senwisetool_db
   ```

3. **Base de données :**
   Assurez-vous d'avoir créé la base de données `senwisetool_db` dans votre instance locale PostgreSQL.

## Lancer l'application (Développement)

Pour lancer le serveur de développement avec le rechargement automatique (hot-reload) :

```bash
poetry run uvicorn app.main:app --reload
```

L'API sera disponible à l'adresse : `http://127.0.0.1:8000`.
La documentation interactive de l'API (Swagger UI) sera accessible sur : `http://127.0.0.1:8000/docs`.

## Lancer les tests

Les tests sont écrits avec `pytest` et `pytest-asyncio`. Une base de données de test (par exemple `senwisetool_test_db`) est requise.

Pour lancer tous les tests :

```bash
poetry run pytest
```

## Déploiement (Production)

En environnement de production, vous ne devez pas utiliser le mode `--reload`. Vous pouvez utiliser `uvicorn` avec un gestionnaire de processus (comme `Gunicorn` ou directement dans un conteneur Docker).

Commande de lancement recommandée pour la production :

```bash
poetry run uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

> **Note :** N'oubliez pas de configurer les variables d'environnement appropriées (comme `DATABASE_URL`) de manière sécurisée (par exemple via les secrets de votre plateforme d'hébergement ou un coffre-fort de secrets) et non pas via le fichier `.env` en production.
