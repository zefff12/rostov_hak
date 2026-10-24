"""Настройки помощника. Любой параметр переопределяется переменной окружения."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KB_DIR = ROOT / "kb"
SOURCES_DIR = KB_DIR / "sources"
INDEX_DIR = KB_DIR / "index"
GENERATED_DIR = KB_DIR / "generated"
UPLOADS_DIR = KB_DIR / "uploads"
REGISTRY_PATH = KB_DIR / "registry.json"
AUDIT_LOG = KB_DIR / "audit.log.jsonl"
WEB_DIR = ROOT / "web"

# Локальная LLM через Ollama: данные не покидают контур организации.
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
# 7b заметно точнее в выводах (проверено на tests/questions.json); 3b — вдвое быстрее на CPU.
LLM_MODEL = os.getenv("LLM_MODEL", "qwen2.5:7b")
EMBED_MODEL = os.getenv("EMBED_MODEL", "bge-m3")
# 0 — воспроизводимые ответы: при 0.1 повторные прогоны одного вопроса давали разные формулировки.
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0"))
LLM_NUM_CTX = int(os.getenv("LLM_NUM_CTX", "6144"))
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "300"))
# Сколько держать модель в памяти после запроса: иначе первый ответ после паузы ждет загрузки 7B (~40 с).
LLM_KEEP_ALIVE = os.getenv("LLM_KEEP_ALIVE", "2h")

# Поиск
TOP_K = int(os.getenv("TOP_K", "6"))
# Ниже этого порога релевантности бот честно сообщает, что информации нет.
MIN_RELEVANCE = float(os.getenv("MIN_RELEVANCE", "0.40"))

def _persistent_secret(env: str, filename: str, nbytes: int) -> str:
    """Значение из переменной окружения, иначе — сгенерированное один раз и сохраненное в kb/."""
    if os.getenv(env):
        return os.environ[env]
    path = KB_DIR / filename
    if not path.exists():
        import secrets
        path.write_text(secrets.token_urlsafe(nbytes), encoding="utf-8")
    return path.read_text(encoding="utf-8").strip()


# Секрет для подписи токенов сессий (в проде — из хранилища секретов).
SECRET_KEY = _persistent_secret("SECRET_KEY", ".secret_key", 32)
# Пароль роли «Администратор»: файл kb/admin_password.txt или переменная ADMIN_PASSWORD.
ADMIN_PASSWORD = _persistent_secret("ADMIN_PASSWORD", "admin_password.txt", 9)

# Ограничение частоты запросов с одного IP (защита публичной ссылки от перегрузки).
RATE_CHAT_PER_MIN = int(os.getenv("RATE_CHAT_PER_MIN", "12"))
RATE_LOGIN_PER_MIN = int(os.getenv("RATE_LOGIN_PER_MIN", "10"))

# Куда направлять сотрудника, если ответа нет (п. 12.4, 12.10 Инструкции).
SUPPORT_CONTACT = os.getenv(
    "SUPPORT_CONTACT",
    "Департамент документационного обеспечения (методологическое руководство и разъяснения, "
    "п. 12.4 Инструкции). Актуальные материалы — в разделе Департамента ДОУ на внутреннем портале (п. 12.10).",
)
