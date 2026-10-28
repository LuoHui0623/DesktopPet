from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = BACKEND_DIR.parent
FRONTEND_DIR = ROOT_DIR / "frontend"

CONFIG_DIR = BACKEND_DIR / "config"
PERSONAS_DIR = CONFIG_DIR / "personas"
CAPABILITIES_FILE = CONFIG_DIR / "capabilities.md"
DATA_DIR = BACKEND_DIR / "data"
LOG_DIR = DATA_DIR / "logs"
EXPORT_DIR = DATA_DIR / "exports"
MEMORY_DIR = DATA_DIR / "memory"
STRUCTURED_MEMORY_DIR = MEMORY_DIR / "structured"
MEMORY_INDEX_FILE = MEMORY_DIR / "index.json"
RESOURCE_DIR = DATA_DIR / "resources"
DRAFT_PATH = RESOURCE_DIR / "草稿本.md"
EMOJI_DIR = RESOURCE_DIR / "表情包"
MIGRATIONS_DIR = BACKEND_DIR / "infra" / "store" / "migrations"
RESOURCES_DIR = BACKEND_DIR / "resources"
SKILLS_DIR = RESOURCES_DIR / "skills"
PROMPTS_DIR = BACKEND_DIR / "service" / "agent" / "prompts"


def resolve_from_root(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT_DIR / path
