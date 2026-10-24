"""HTTP API и раздача веб-интерфейса.

Запуск: py -m uvicorn app.main:app --host 127.0.0.1 --port 8000
"""
import json
import re
import threading
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import assistant, auth, checker, config, kb, llm, modules, routing
from .kb import KB

for d in (config.INDEX_DIR, config.GENERATED_DIR, config.UPLOADS_DIR):
    d.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Помощник по Инструкции ДОУ", version="1.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"],
                   allow_methods=["*"], allow_headers=["*"])

@app.middleware("http")
async def no_cache_html(request: Request, call_next):
    """HTML не кешируется: после обновления интерфейса пользователи сразу получают новую версию."""
    resp = await call_next(request)
    if resp.headers.get("content-type", "").startswith("text/html"):
        resp.headers["Cache-Control"] = "no-cache"
    elif request.url.path.startswith("/img/") or request.url.query.startswith("v="):
        # Картинки и версионированные CSS/JS кешируются браузером: меньше запросов по сети.
        resp.headers["Cache-Control"] = "public, max-age=604800"
    return resp


STATUS = {"index": "building", "embeddings": False}
MAX_UPLOAD = 10 * 1024 * 1024
ALLOWED_EXT = {".docx", ".pdf", ".txt"}


def rebuild(full: bool = True):
    """Сначала быстрый лексический индекс (бот сразу доступен), затем векторный."""
    STATUS["index"] = "building"
    KB.build(use_embeddings=False)
    STATUS["index"] = "lexical"
    routing.reset_cache()
    if full:
        res = KB.build(use_embeddings=True)
        STATUS["embeddings"] = res["embeddings"]
        STATUS["index"] = "ready"


@app.on_event("startup")
def _startup():
    kb.load_registry()
    threading.Thread(target=rebuild, daemon=True).start()


# ---------------------------------------------------------------- Служебное

@app.get("/api/health")
def health():
    return {"llm": llm.is_available(), "model": config.LLM_MODEL, "embed_model": config.EMBED_MODEL,
            "chunks": len(KB.chunks), **STATUS}


def client_ip(request: Request) -> str:
    # За туннелем Cloudflare реальный адрес приходит в заголовке.
    return request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?")


class LoginIn(BaseModel):
    user_id: str
    password: str = ""


@app.post("/api/login")
def login(body: LoginIn, request: Request):
    ip = client_ip(request)
    if auth.rate_limited(f"login:{ip}", config.RATE_LOGIN_PER_MIN):
        raise HTTPException(429, "Слишком много попыток входа, подождите минуту")
    user = auth.DEMO_USERS.get(body.user_id)
    if not user:
        raise HTTPException(401, "Неизвестный пользователь")
    if not auth.check_password(user, body.password):
        auth.audit(user, "login_failed", ip=ip)
        raise HTTPException(401, "Неверный пароль")
    auth.audit(user, "login", ip=ip)
    return {"token": auth.issue_token(user["id"]), "user": user, "role_name": auth.ROLES[user["role"]]}


@app.get("/api/users")
def users():
    return [dict(u, role_name=auth.ROLES[u["role"]], needs_password=u["role"] in auth.PASSWORD_ROLES)
            for u in auth.DEMO_USERS.values()]


@app.get("/api/me")
def me(user=Depends(auth.current_user)):
    return dict(user, role_name=auth.ROLES[user["role"]])


# ---------------------------------------------------------------- Чат

class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=3000)
    session_id: str | None = None


@app.post("/api/chat")
def chat(body: ChatIn, request: Request, user=Depends(auth.current_user)):
    if auth.rate_limited(f"chat:{client_ip(request)}", config.RATE_CHAT_PER_MIN):
        raise HTTPException(429, "Слишком много запросов, подождите минуту")
    sess = assistant.get_session(body.session_id)
    if sess.get("user") and sess["user"] != user["id"]:
        raise HTTPException(403, "Чужая сессия")
    sess["user"] = user["id"]

    def gen():
        yield json.dumps({"type": "session", "session_id": sess["id"]}, ensure_ascii=False) + "\n"
        refs, no_answer = [], False
        try:
            for ev in assistant.handle(sess, body.message, user):
                if ev["type"] == "sources":
                    refs = [f"{s['doc_short']} {s['ref']}" for s in ev["items"]]
                if ev["type"] == "no_answer":
                    no_answer = True
                yield json.dumps(ev, ensure_ascii=False) + "\n"
        except Exception as e:  # не роняем диалог из-за ошибки одного шага
            yield json.dumps({"type": "text", "text": f"\n\n⚠️ Внутренняя ошибка: {e}"}, ensure_ascii=False) + "\n"
            raise
        finally:
            auth.audit(user, "chat", session=sess["id"], query=body.message[:300], sources=refs, no_answer=no_answer)
        yield json.dumps({"type": "done"}) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


class FeedbackIn(BaseModel):
    session_id: str
    rating: int
    comment: str = ""


@app.post("/api/feedback")
def feedback(body: FeedbackIn, user=Depends(auth.current_user)):
    auth.audit(user, "feedback", session=body.session_id, rating=body.rating, comment=body.comment[:500])
    return {"ok": True}


@app.get("/api/search")
def search(q: str, user=Depends(auth.current_user)):
    keys = ("doc_title", "doc_short", "ref_full", "section", "page", "version", "approved", "relevance", "score",
            "bm25", "cos", "text")
    return [{k: c.get(k) for k in keys} for c in KB.search(q[:300], role=user["role"], k=10)]


@app.get("/api/documents")
def documents(user=Depends(auth.current_user)):
    """Документы базы знаний, доступные роли пользователя, и последние обновления."""
    allowed = kb.ROLE_ACCESS.get(user["role"], {"all"})
    counts = {}
    for c in KB.chunks:
        counts[c["doc_id"]] = counts.get(c["doc_id"], 0) + 1
    docs, updates = [], []
    for d in kb.load_registry()["documents"]:
        if d.get("access", "all") not in allowed:
            continue
        v = kb.active_version(d)
        if not v:
            continue
        mid = next((m.id for m in modules.MODULES if m.file == Path(v["file"]).name), None)
        docs.append({"doc_id": d["doc_id"], "title": d["title"], "short": d["short"], "kind": d.get("parser"),
                     "version": v["version"], "approved": v.get("approved", ""),
                     "effective_from": v.get("effective_from", ""), "added_at": v.get("added_at", ""),
                     "chunks": counts.get(d["doc_id"], 0), "module_id": mid})
        updates.append({"title": d["short"], "version": v["version"], "effective_from": v.get("effective_from", ""),
                        "added_at": v.get("added_at", "")})
    updates.sort(key=lambda u: u["added_at"], reverse=True)
    return {"documents": docs, "updates": updates[:6]}


@app.get("/api/my/files")
def my_files(user=Depends(auth.current_user)):
    return assistant.user_files(user["id"])


# ---------------------------------------------------------------- Модули и файлы

@app.get("/api/modules")
def list_modules():
    return [{"id": m.id, "title": m.title, "doc_type": m.doc_type, "matrix_row": m.matrix_row,
             "fields": len(m.fields), "file": m.file} for m in modules.MODULES]


@app.get("/api/modules/{mid}/file")
def module_file(mid: str):
    m = modules.BY_ID.get(mid)
    if not m:
        raise HTTPException(404)
    return FileResponse(config.SOURCES_DIR / "modules" / m.file, filename=m.file)


@app.get("/api/files/{name}")
def generated_file(name: str):
    if not re.fullmatch(r"[\w.-]+\.docx", name):
        raise HTTPException(400)
    path = config.GENERATED_DIR / name
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, filename=name)


async def _save_upload(file: UploadFile, folder: Path) -> Path:
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f"Допустимые форматы: {', '.join(sorted(ALLOWED_EXT))}")
    data = await file.read()
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "Файл больше 10 МБ")
    safe_name = re.sub(r"[^\w.-]", "_", Path(file.filename).name)
    path = folder / f"{uuid.uuid4().hex[:8]}_{safe_name}"
    path.write_bytes(data)
    return path


@app.post("/api/check")
async def check(file: UploadFile = File(...), user=Depends(auth.current_user)):
    path = await _save_upload(file, config.UPLOADS_DIR)
    try:
        report = checker.check_document(path)
    except Exception as e:
        raise HTTPException(400, f"Не удалось прочитать файл: {e}")
    finally:
        path.unlink(missing_ok=True)  # проверяемые проекты не храним
    auth.audit(user, "check", file=file.filename, errors=report["summary"]["errors"])
    return {"report": report, "markdown": checker.report_markdown(report)}


# ---------------------------------------------------------------- Администрирование БЗ

@app.get("/api/admin/documents")
def admin_docs(user=Depends(auth.current_user)):
    auth.require_role(user, "admin", "dou")
    reg = kb.load_registry()
    counts = {}
    for c in KB.chunks:
        counts[c["doc_id"]] = counts.get(c["doc_id"], 0) + 1
    return {"documents": [dict(d, chunks=counts.get(d["doc_id"], 0)) for d in reg["documents"]],
            "access_levels": kb.ACCESS_LABELS, "status": STATUS}


@app.post("/api/admin/documents")
async def admin_upload(file: UploadFile = File(...), title: str = Form(""), doc_id: str = Form(""),
                       access: str = Form("all"), approved: str = Form(""), effective_from: str = Form(""),
                       user=Depends(auth.current_user)):
    auth.require_role(user, "admin")
    if access not in kb.ACCESS_LABELS:
        raise HTTPException(400, "Неизвестный уровень доступа")
    path = await _save_upload(file, config.SOURCES_DIR)
    doc = kb.add_version(doc_id or None, path, title or Path(file.filename).stem, access, user["id"], approved,
                         effective_from)
    auth.audit(user, "kb_upload", doc_id=doc["doc_id"], version=doc["versions"][-1]["version"])
    threading.Thread(target=rebuild, daemon=True).start()
    return {"ok": True, "doc": doc}


class AccessIn(BaseModel):
    access: str


@app.post("/api/admin/documents/{doc_id}/access")
def admin_access(doc_id: str, body: AccessIn, user=Depends(auth.current_user)):
    auth.require_role(user, "admin")
    if body.access not in kb.ACCESS_LABELS:
        raise HTTPException(400)
    kb.set_access(doc_id, body.access)
    auth.audit(user, "kb_access", doc_id=doc_id, access=body.access)
    threading.Thread(target=rebuild, daemon=True).start()
    return {"ok": True}


class RollbackIn(BaseModel):
    version: str


@app.post("/api/admin/documents/{doc_id}/rollback")
def admin_rollback(doc_id: str, body: RollbackIn, user=Depends(auth.current_user)):
    auth.require_role(user, "admin")
    kb.rollback(doc_id, body.version)
    auth.audit(user, "kb_rollback", doc_id=doc_id, version=body.version)
    threading.Thread(target=rebuild, daemon=True).start()
    return {"ok": True}


@app.get("/api/admin/audit")
def admin_audit(user=Depends(auth.current_user), limit: int = 100):
    auth.require_role(user, "admin")
    if not config.AUDIT_LOG.exists():
        return []
    lines = config.AUDIT_LOG.read_text(encoding="utf-8").splitlines()[-limit:]
    return [json.loads(x) for x in reversed(lines)]


# ---------------------------------------------------------------- Веб

app.mount("/", StaticFiles(directory=config.WEB_DIR, html=True), name="web")
