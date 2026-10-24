import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import NullPool

from app.main import app
from app.db.database import Base, get_db

TEST_DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/senwisetool_test_db"

engine_test = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
TestingSessionLocal = async_sessionmaker(autocommit=False, autoflush=False, expire_on_commit=False, bind=engine_test, class_=AsyncSession)

async def override_get_db():
    async with TestingSessionLocal() as session:
        yield session

app.dependency_overrides[get_db] = override_get_db

@pytest.fixture(autouse=True)
async def setup_db():
    async with engine_test.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine_test.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

@pytest.fixture
async def async_client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

@pytest.fixture
async def db_session():
    async with TestingSessionLocal() as session:
        yield session
