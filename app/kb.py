"""База знаний: реестр документов с версиями, индексация и гибридный поиск.

Поиск = BM25 по леммам (точные термины, номера пунктов, аббревиатуры)
       + векторный поиск bge-m3 (смысл, перефразировки «своими словами»).
"""
import datetime as dt
import hashlib
import json
import math
import re
import threading
from collections import Counter
from pathlib import Path

import numpy as np

from . import config, llm, parsers

try:
    import pymorphy3
    _MORPH = pymorphy3.MorphAnalyzer()
except Exception:  # pragma: no cover - работа без морфологии
    _MORPH = None

ROLE_ACCESS = {
    "employee": {"all"},
    "dou": {"all", "dou"},
    "admin": {"all", "dou", "admin"},
}
ACCESS_LABELS = {"all": "Все работники", "dou": "Работники ДОУ", "admin": "Администраторы"}

STOP = set("""и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по только ее мне было вот от
меня еще нет о из ему теперь когда даже ну вдруг ли если уже или ни быть был него до вас нибудь опять уж вам ведь там
потом себя ничего ей может они тут где есть надо ней для мы тебя их чем была сам чтоб без будто чего раз тоже себе под
будет ж тогда кто этот того потому этого какой совсем ним здесь этом один почти мой тем чтобы нее сейчас были куда
зачем всех никогда можно при наконец два об другой хоть после над больше тот через эти нас про всего них какая много
разве три эту моя впрочем хорошо свою этой перед иногда лучше чуть том нельзя такой им более всегда конечно всю между
это который которые настоящий также либо иной указанный соответствующий""".split())

# Синонимы и сокращения из п. 1.4 Инструкции — расширяют запрос в обе стороны.
SYNONYMS = {
    "доу": "документационное обеспечение управления",
    "пд": "проект документа",
    "эп": "электронная подпись",
    "эцп": "электронная подпись",
    "эд": "электронный документ",
    "экд": "электронная копия документа",
    "рк": "регистрационная карточка",
    "рбд": "реестр бумажного документооборота",
    "иод": "информация ограниченного доступа",
    "мэдо": "межведомственный электронный документооборот",
    "сэд": "ИИС ДО электронный документооборот",
    "иис": "ИИС ДО информационная система документационного обеспечения",
    "сп": "структурное подразделение",
    "ро": "региональное отделение",
    "виза": "визирование",
    "согласовать": "согласование визирование",
    "согласование": "рассмотрение визирование",
    "подпись": "подписание",
    "шаблон": "модуль модульный документ",
    "черновик": "проект документа",
}

_lemma_cache: dict[str, str] = {}


def lemma(word: str) -> str:
    w = word.lower().replace("ё", "е")
    if w in _lemma_cache:
        return _lemma_cache[w]
    if _MORPH and re.fullmatch(r"[а-я-]+", w):
        res = _MORPH.parse(w)[0].normal_form.replace("ё", "е")
    else:
        res = w
    _lemma_cache[w] = res
    return res


def tokenize(text: str) -> list[str]:
    toks = re.findall(r"[а-яёa-z]+|\d+(?:\.\d+)*", text.lower())
    return [lemma(t) for t in toks if t not in STOP and (len(t) > 1 or t.isdigit())]


def expand_query(q: str) -> str:
    extra = []
    for w in re.findall(r"[а-яёa-z]+", q.lower()):
        if w in SYNONYMS:
            extra.append(SYNONYMS[w])
        elif lemma(w) in SYNONYMS:
            extra.append(SYNONYMS[lemma(w)])
    return q + (" " + " ".join(extra) if extra else "")


# ---------------------------------------------------------------- Реестр

def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def default_registry() -> dict:
    """Начальный реестр из материалов кейса."""
    now = dt.datetime.now().isoformat(timespec="seconds")
    docs = [
        dict(doc_id="instruction_dou", parser="instruction", access="all",
             title="Инструкция по документационному обеспечению управления и документообороту Организации",
             short="Инструкция по ДОУ",
             version=dict(version="1", file="sources/instruction_dou_v1.pdf",
                          approved="приказ Организации от 15 октября 2026 г. № ОД-184", effective_from="2026-11-01")),
        dict(doc_id="matrix_visas", parser="matrix", access="all",
             title="Матрица обязательного визирования проектов внутренних и внешних документов Организации",
             short="Матрица визирования",
             version=dict(version="1", file="sources/matrix_v1.docx", approved="", effective_from="2026-11-01")),
        dict(doc_id="modules_guide", parser="generic", access="all",
             title="Методические рекомендации по работе с модульными документами",
             short="Метод. рекомендации по модулям",
             version=dict(version="1", file="sources/modules_guide_v1.docx", approved="", effective_from="2026-11-01")),
    ]
    for m in sorted((config.SOURCES_DIR / "modules").glob("*.docx")):
        mid = "module_" + hashlib.md5(m.stem.encode()).hexdigest()[:6]
        docs.append(dict(doc_id=mid, parser="module", access="all",
                         title=m.stem.replace("_", " "), short=m.stem.replace("_", " "),
                         version=dict(version="1", file=f"sources/modules/{m.name}", approved="",
                                      effective_from="2026-11-01")))
    reg = {"documents": []}
    for d in docs:
        v = d.pop("version")
        v.update(status="active", sha256=_sha(config.KB_DIR / v["file"]), added_at=now, added_by="system")
        d["versions"] = [v]
        reg["documents"].append(d)
    return reg


def load_registry() -> dict:
    if not config.REGISTRY_PATH.exists():
        save_registry(default_registry())
    return json.loads(config.REGISTRY_PATH.read_text(encoding="utf-8"))


def save_registry(reg: dict) -> None:
    config.REGISTRY_PATH.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")


def active_version(doc: dict) -> dict | None:
    return next((v for v in reversed(doc["versions"]) if v["status"] == "active"), None)


def add_version(doc_id: str | None, file_path: Path, title: str, access: str, user: str,
                approved: str = "", effective_from: str = "") -> dict:
    """Добавляет документ или новую редакцию. Прежняя редакция уходит в архив, но сохраняется."""
    reg = load_registry()
    doc = next((d for d in reg["documents"] if d["doc_id"] == doc_id), None) if doc_id else None
    now = dt.datetime.now().isoformat(timespec="seconds")
    if doc is None:
        doc_id = doc_id or "doc_" + hashlib.md5((title + now).encode()).hexdigest()[:8]
        doc = dict(doc_id=doc_id, parser="generic", access=access, title=title, short=title[:60], versions=[])
        reg["documents"].append(doc)
    for v in doc["versions"]:
        if v["status"] == "active":
            v["status"] = "archived"
            v["archived_at"] = now
    if title:
        doc["title"] = title
    doc["access"] = access or doc["access"]
    new_v = dict(version=str(len(doc["versions"]) + 1), file=str(file_path.relative_to(config.KB_DIR)).replace("\\", "/"),
                 approved=approved, effective_from=effective_from or dt.date.today().isoformat(),
                 status="active", sha256=_sha(file_path), added_at=now, added_by=user)
    doc["versions"].append(new_v)
    save_registry(reg)
    return doc


def set_access(doc_id: str, access: str) -> None:
    reg = load_registry()
    for d in reg["documents"]:
        if d["doc_id"] == doc_id:
            d["access"] = access
    save_registry(reg)


def rollback(doc_id: str, version: str) -> None:
    reg = load_registry()
    for d in reg["documents"]:
        if d["doc_id"] == doc_id:
            for v in d["versions"]:
                v["status"] = "active" if v["version"] == version else "archived"
    save_registry(reg)


# ---------------------------------------------------------------- Индекс

def _units_for(doc: dict, ver: dict) -> list[dict]:
    path = config.KB_DIR / ver["file"]
    parser = doc.get("parser", "generic")
    if parser == "instruction":
        return parsers.parse_numbered(parsers.pdf_lines(path)) if path.suffix == ".pdf" else parsers.parse_generic(path)
    if parser == "matrix":
        return parsers.matrix_units(parsers.parse_matrix(path))
    if parser == "module":
        paras = parsers.docx_paragraphs(path)
        try:
            start = next(i for i, p in enumerate(paras) if p.startswith("Примечания по заполнению"))
        except StopIteration:
            start = 0
        title = next((p for p in paras if p.startswith(("О ", "Об "))), doc["title"])
        notes = "\n".join(paras[start + 1:])
        return [{"ref": "примечания по заполнению", "section": doc["title"], "page": 1,
                 "text": f"Шаблон (модуль): {doc['title']}. Заголовок: {title}.\n{notes}"}]
    return parsers.parse_generic(path)


class KnowledgeBase:
    def __init__(self):
        self.lock = threading.RLock()
        self.chunks: list[dict] = []
        self.emb: np.ndarray | None = None
        self.embed_ok = False

    # -- построение
    def build(self, use_embeddings: bool = True) -> dict:
        with self.lock:
            reg = load_registry()
            chunks = []
            for doc in reg["documents"]:
                ver = active_version(doc)
                if not ver:
                    continue
                for n, u in enumerate(_units_for(doc, ver)):
                    for part_i, part in enumerate(parsers.split_long(u["text"])):
                        ref = u["ref"] + (f" (часть {part_i + 1})" if len(u["text"]) > parsers.MAX_CHUNK else "")
                        chunks.append(dict(
                            id=f"{doc['doc_id']}@{ver['version']}#{n}.{part_i}",
                            doc_id=doc["doc_id"], doc_title=doc["title"], doc_short=doc["short"],
                            kind=doc.get("parser"), version=ver["version"], approved=ver.get("approved", ""),
                            access=doc.get("access", "all"), ref=u["ref"], ref_full=ref,
                            section=u.get("section", ""), page=u.get("page", 1), text=part))
            self.chunks = chunks
            self._build_bm25()
            self.embed_ok = False
            if use_embeddings:
                self._build_embeddings()
            (config.INDEX_DIR / "chunks.json").write_text(json.dumps(chunks, ensure_ascii=False, indent=1),
                                                          encoding="utf-8")
            return {"chunks": len(chunks), "embeddings": self.embed_ok}

    def _doc_text(self, c: dict) -> str:
        return f"{c['section']} {c['ref']} {c['text']}"

    def _build_bm25(self):
        self.tf = [Counter(tokenize(self._doc_text(c))) for c in self.chunks]
        self.dl = np.array([sum(t.values()) for t in self.tf], dtype=float)
        self.avgdl = float(self.dl.mean()) if len(self.dl) else 1.0
        df = Counter()
        for t in self.tf:
            df.update(t.keys())
        n = len(self.chunks)
        self.idf = {w: math.log(1 + (n - f + 0.5) / (f + 0.5)) for w, f in df.items()}

    def _build_embeddings(self):
        cache_path = config.INDEX_DIR / "emb_cache.json"
        cache = {}
        if cache_path.exists():
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
            if cache.get("_model") != config.EMBED_MODEL:
                cache = {}
        cache["_model"] = config.EMBED_MODEL
        texts = [self._doc_text(c) for c in self.chunks]
        keys = [hashlib.sha1(t.encode()).hexdigest() for t in texts]
        missing = [i for i, k in enumerate(keys) if k not in cache]
        try:
            if missing:
                vecs = llm.embed([texts[i] for i in missing])
                for i, v in zip(missing, vecs):
                    cache[keys[i]] = v
                cache_path.write_text(json.dumps(cache), encoding="utf-8")
        except llm.LLMUnavailable:
            return
        m = np.array([cache[k] for k in keys], dtype=np.float32)
        m /= np.linalg.norm(m, axis=1, keepdims=True) + 1e-9
        self.emb = m
        self.embed_ok = True

    # -- поиск
    def bm25(self, query: str) -> np.ndarray:
        q = tokenize(expand_query(query))
        scores = np.zeros(len(self.chunks))
        k1, b = 1.5, 0.75
        for w in set(q):
            idf = self.idf.get(w)
            if not idf:
                continue
            for i, tf in enumerate(self.tf):
                f = tf.get(w)
                if f:
                    scores[i] += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * self.dl[i] / self.avgdl))
        return scores

    def search(self, query: str, role: str = "employee", k: int = config.TOP_K,
               kinds: set[str] | None = None) -> list[dict]:
        with self.lock:
            if not self.chunks:
                return []
            allowed = ROLE_ACCESS.get(role, {"all"})
            mask = np.array([c["access"] in allowed and (not kinds or c["kind"] in kinds) for c in self.chunks])
            bm = self.bm25(query)
            bm_max = bm.max() if bm.max() > 0 else 1.0
            bm_rel = bm / bm_max
            if self.embed_ok:
                try:
                    qv = np.array(llm.embed([expand_query(query)])[0], dtype=np.float32)
                    qv /= np.linalg.norm(qv) + 1e-9
                    cos = self.emb @ qv
                except llm.LLMUnavailable:
                    cos = None
            else:
                cos = None
            if cos is not None:
                # Калибровка bge-m3 на tests/questions.json: вопросы вне базы дают косинус ≤ 0.54,
                # вопросы по базе — ≥ 0.60. Шкала 0.45→0 %, 0.75→100 %; порог отказа 0.40 ≈ косинус 0.57.
                sem = np.clip((cos - 0.45) / 0.30, 0, 1)
                score = 0.65 * sem + 0.35 * bm_rel * sem ** 0.5
                relevance = np.clip(sem * (0.85 + 0.15 * bm_rel), 0, 1)
            else:
                cover = self._coverage(query)
                score = bm_rel * cover
                relevance = np.clip(cover * (0.5 + 0.5 * bm_rel), 0, 1)
            # Примечания модулей длинные и «перетягивают» общие вопросы — понижаем их,
            # если пользователь не спрашивает, как заполнить конкретный шаблон. Слово «модуль» само по себе
            # не исключение: общие правила работы с модулями — в Метод. рекомендациях, а не в примечаниях.
            if not re.search(r"шаблон|бланк|заполн|поле|примечани\w* к модул", query.lower()):
                is_mod = np.array([c["kind"] == "module" for c in self.chunks])
                score = np.where(is_mod, score * 0.6, score)
                relevance = np.where(is_mod, relevance * 0.8, relevance)
            score = np.where(mask, score, -1)
            order = np.argsort(-score)[:k]
            res = []
            for i in order:
                if score[i] <= 0:
                    continue
                c = dict(self.chunks[i])
                c["score"] = float(score[i])
                c["relevance"] = round(float(relevance[i]) * 100)
                c["bm25"] = float(bm[i])
                c["cos"] = float(cos[i]) if cos is not None else None
                res.append(c)
            return res

    def _coverage(self, query: str) -> np.ndarray:
        q = set(tokenize(query))
        if not q:
            return np.zeros(len(self.chunks))
        return np.array([len(q & set(tf)) / len(q) for tf in self.tf])

    def get_by_ref(self, doc_id: str, ref: str) -> list[dict]:
        return [c for c in self.chunks if c["doc_id"] == doc_id and c["ref"] == ref]


KB = KnowledgeBase()
