from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = BACKEND_DIR.parent
FRONTEND_DIR = ROOT_DIR / "frontend"

CONFIG_DIR = BACKEND_DIR / "config"
ENV_FILE_NAME = ".env"
ENV_EXAMPLE_FILE_NAME = ".env.example"
LLM_CONFIG_FILE_NAME = "llm.yaml"
PERSONA_FILE_NAME = "persona.yaml"
ENV_FILE = CONFIG_DIR / ENV_FILE_NAME
LLM_CONFIG_FILE = CONFIG_DIR / LLM_CONFIG_FILE_NAME
PERSONA_FILE = CONFIG_DIR / PERSONA_FILE_NAME

DATA_DIR = BACKEND_DIR / "data"
DB_FILE = DATA_DIR / "pet.db"
LOG_DIR = DATA_DIR / "logs"
EXPORT_DIR = DATA_DIR / "exports"
LOCK_FILE = DATA_DIR / "backend.lock"

MIGRATIONS_DIR = BACKEND_DIR / "infra" / "store" / "migrations"
OPENAPI_FILE = BACKEND_DIR / "openapi.json"
API_CONTRACTS_FILE = FRONTEND_DIR / "src" / "api" / "api-contracts.json"


def resolve_from_root(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT_DIR / path
