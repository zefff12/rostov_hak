"""Диалоговый менеджер: определяет намерение и ведет сценарий.

Сценарии:
  * вопрос по Инструкции → гибридный поиск → ответ LLM строго по найденным пунктам со ссылками;
  * «подготовить приказ/распоряжение…» → подбор модуля → мастер с уточняющими вопросами → DOCX;
  * «с кем согласовать / маршрут» → Матрица визирования + сроки (детерминированно);
  * нехватка сведений → уточняющий вопрос с кнопками; нет информации в базе → честный отказ + куда обратиться.
"""
import datetime as dt
import json
import re
import uuid
from typing import Iterator

from . import config, llm, modules, routing
from .kb import KB, tokenize

Event = dict

SESSIONS: dict[str, dict] = {}


def get_session(sid: str | None) -> dict:
    if not sid or sid not in SESSIONS:
        sid = sid or uuid.uuid4().hex
        SESSIONS[sid] = {"id": sid, "history": [], "wizard": None, "pending": None}
    return SESSIONS[sid]


FILES_INDEX = config.GENERATED_DIR / "index.json"


def _files() -> dict:
    try:
        return json.loads(FILES_INDEX.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def record_file(user_id: str, meta: dict) -> None:
    """Реестр сформированных проектов по пользователям (блок «Недавно созданные»)."""
    data = _files()
    data.setdefault(user_id, []).insert(0, meta)
    data[user_id] = data[user_id][:50]
    FILES_INDEX.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def user_files(user_id: str) -> list[dict]:
    return [f for f in _files().get(user_id, []) if (config.GENERATED_DIR / f["name"]).exists()]


def text(t: str) -> Event:
    return {"type": "text", "text": t}


def buttons(items: list[str]) -> Event:
    return {"type": "buttons", "items": items}


# ---------------------------------------------------------------- Намерения

CREATE_RE = re.compile(r"(подготов|созда|состав|сформир|оформ|сдела|напиш|написа|нуж[ен|на|но]|черновик|заполн|"
                       r"шаблон|бланк|модул|издать|проект\w* (приказ|распоряж))", re.I)
DOCNAME_RE = re.compile(r"(приказ|распоряжени|черновик|шаблон|модул|бланк|документ)", re.I)
ROUTE_RE = re.compile(r"(с кем .*согласов|кто .*(визир|согласов)|маршрут(?!изац)\w*|по матриц\w*|обязательн\w* визир|"
                      r"чьи визы|какие визы|кому .*на (визу|согласование)|кто долж\w* визир|нуж\w* ли .{0,30}виз)", re.I)
LIST_RE = re.compile(r"(какие|список|перечень|покажи).{0,20}(шаблон|модул|бланк)", re.I)
HELP_RE = re.compile(r"^(привет|здравств|добрый|что ты умеешь|помощь|help|start|/start|начать)", re.I)
DEADLINE_RE = re.compile(r"(срок|сколько (дней|времени)|как долго)", re.I)
APPROVAL_RE = re.compile(r"(согласов|рассмотр|визир)", re.I)

GENERIC_WORDS = set(tokenize("приказ распоряжение создание создать проект документ модуль шаблон подготовить "
                             "нужно сделать черновик о об по организация"))


def match_modules(q: str) -> list[tuple[float, modules.Module]]:
    qt = set(tokenize(q)) - GENERIC_WORDS
    res = []
    for m in modules.MODULES:
        mt = set(tokenize(m.title + " " + m.keywords)) - GENERIC_WORDS
        if not qt:
            continue
        hit = len(qt & mt)
        if hit:
            res.append((hit / (len(qt) ** 0.5), m))
    res.sort(key=lambda x: -x[0])
    return res


def handle(sess: dict, message: str, user: dict) -> Iterator[Event]:
    msg = message.strip()
    sess["history"].append({"role": "user", "content": msg})
    out_text = []

    def emit(ev):
        if ev["type"] == "text":
            out_text.append(ev["text"])
        return ev

    for ev in _route(sess, msg, user):
        yield emit(ev)
    sess["history"].append({"role": "assistant", "content": "".join(out_text)[:2000]})
    sess["history"] = sess["history"][-12:]


def _route(sess, msg, user) -> Iterator[Event]:
    low = msg.lower()
    if sess.get("wizard"):
        yield from wizard_step(sess, msg, user)
        return
    pending = sess.get("pending")
    if pending:
        sess["pending"] = None
        if pending["type"] == "pick_module":
            m = next((m for m in modules.MODULES if m.title.lower() == low or m.id == low), None)
            if m:
                yield from start_wizard(sess, m, pending.get("initial", ""), user)
                return
        if pending["type"] == "qa_doc_type" and len(msg) < 40:
            yield from answer(sess, f"{pending['question']} (вид документа: {msg})", user, doc_type=msg.lower())
            return
        if pending["type"] == "route" and len(msg) < 80:
            yield from route_flow(sess, pending["text"] + " " + msg, user, dict(pending, answer=msg))
            return
    if HELP_RE.search(low):
        yield from help_message()
        return
    if LIST_RE.search(low):
        sess["pending"] = {"type": "pick_module"}
        yield from list_modules()
        return
    if ROUTE_RE.search(low):
        yield from route_flow(sess, msg, user, {})
        return
    is_question = (re.match(r"^(как|что|можно|почему|когда|какой|какая|какие|каков|сколько|кто|где|зачем|нужн\w* ли|"
                            r"обязан|должен|есть ли|в как)", low) or low.endswith("?"))
    explicit = re.search(r"\b(подготовь|создай|сделай|составь|оформи|сформируй|напиши|заполни|помоги (подготовить|"
                         r"составить|оформить))\b", low)
    if CREATE_RE.search(low) and DOCNAME_RE.search(low) and (not is_question or
                                                             (explicit and not re.match(r"^(как|что|можно|какие|какой|сколько|кто)", low))):
        yield from create_flow(sess, msg, user)
        return
    yield from answer(sess, msg, user)


def help_message() -> Iterator[Event]:
    yield text("Здравствуйте! Я помощник по Инструкции по документационному обеспечению управления и "
               "документообороту. Могу:\n\n"
               "- **ответить на вопрос** по Инструкции, Матрице визирования и методическим рекомендациям — "
               "со ссылками на конкретные пункты;\n"
               "- **объяснить простым языком**, что требуется в вашей ситуации;\n"
               "- **подготовить черновик** приказа или распоряжения по модульному шаблону — задам уточняющие вопросы и "
               "сформирую DOCX;\n"
               "- **подсказать маршрут визирования** и сроки рассмотрения;\n"
               "- **проверить готовый проект** на соответствие требованиям (кнопка 📎).\n\n"
               "Если ответа в базе знаний нет — скажу об этом честно и подскажу, куда обратиться.")
    yield buttons(["Какие сроки рассмотрения проекта приказа?", "Подготовить распоряжение о создании рабочей группы",
                   "С кем согласовать приказ о вводе в эксплуатацию ИС?", "Какие шаблоны есть?"])


def list_modules() -> Iterator[Event]:
    lines = ["Доступные модульные документы (шаблоны):\n"]
    for m in modules.MODULES:
        lines.append(f"- **{m.title}** — строка {m.matrix_row} Матрицы визирования")
    lines.append("\nВыберите шаблон — я задам вопросы по каждому полю и соберу готовый проект.")
    yield text("\n".join(lines))
    yield buttons([m.title for m in modules.MODULES])


# ---------------------------------------------------------------- Ответ на вопрос (RAG)

SYSTEM_PROMPT = """Ты — помощник работников Организации по внутренним документам о документообороте.
Отвечай ТОЛЬКО на основе пронумерованных фрагментов из базы знаний ниже. Не используй внешние знания.
Правила:
1. Первое предложение — прямой ответ на вопрос со ссылкой [N]. Он должен точно передавать смысл нормы и не противоречить ей.
2. Затем, если нужно, 1–4 коротких пункта: что конкретно сделать работнику. Без общих советов и рассуждений.
3. После каждого утверждения ставь ссылку на номер фрагмента в квадратных скобках: [1] или [2][3]. Ссылайся только на фрагменты из списка.
4. Пиши простым языком, но сохраняй точные сроки, числа, названия.
5. Если во фрагментах нет ответа — напиши только: «В базе знаний нет информации по этому вопросу». Не додумывай.
6. Уточняющий вопрос задавай ТОЛЬКО если норма дает разные ответы для разных ситуаций, а ситуация работника неизвестна (например, срок зависит от вида документа, а вид не назван). Тогда ответь для типовых случаев и в конце задай один вопрос о ситуации работника. В остальных случаях вопросов не задавай.
7. Отвечай на русском языке. Не используй слово «фрагмент»."""


def _label(c: dict) -> str:
    return f"{c['doc_short']}, {c['ref']}"


def _context_block(chunks: list[dict]) -> str:
    parts = []
    for i, c in enumerate(chunks, 1):
        sec = f" ({c['section']})" if c.get("section") and c["kind"] == "instruction" else ""
        parts.append(f"[{i}] {_label(c)}{sec}:\n{c['text']}")
    return "\n\n".join(parts)


def _source_items(chunks):
    return [{"n": i, "doc": c["doc_title"], "doc_short": c["doc_short"], "ref": c["ref_full"],
             "section": c.get("section", ""), "page": c.get("page"), "version": c["version"],
             "approved": c.get("approved", ""), "relevance": c["relevance"], "text": c["text"], "doc_id": c["doc_id"]}
            for i, c in enumerate(chunks, 1)]


def _retrieval_query(sess: dict, msg: str) -> str:
    """Короткие уточнения («а для распоряжения?») дополняются предыдущим вопросом."""
    prev = [h["content"] for h in sess["history"][:-1] if h["role"] == "user"]
    if prev and (len(msg.split()) <= 5 or re.match(r"^(а |и |еще|ещё|тогда|а если|а для|а как)", msg.lower())):
        return prev[-1] + " " + msg
    return msg


def answer(sess: dict, msg: str, user: dict, doc_type: str | None = None) -> Iterator[Event]:
    low = msg.lower()
    # Уточняющий вопрос: срок/согласование без вида документа.
    if (DEADLINE_RE.search(low) and APPROVAL_RE.search(low) and not doc_type
            and not routing.detect_doc_type(_retrieval_query(sess, msg))):
        sess["pending"] = {"type": "qa_doc_type", "question": msg}
        yield text("Сроки рассмотрения зависят от вида документа (п. 10.3 Инструкции). "
                   "**Уточните, пожалуйста: о каком документе идет речь?**")
        yield buttons(["Приказ", "Распоряжение", "Письмо", "Положение", "Регламент", "Соглашение",
                       "Методические рекомендации", "Иной документ"])
        return

    query = _retrieval_query(sess, msg)
    chunks = KB.search(query, role=user["role"], k=config.TOP_K)
    best = chunks[0]["relevance"] / 100 if chunks else 0
    if best < config.MIN_RELEVANCE:
        yield from not_found(chunks)
        return
    # Отсекаем «хвост» слабо релевантных фрагментов, чтобы не путать модель.
    chunks = [c for c in chunks if c["relevance"] >= max(25, chunks[0]["relevance"] - 35)][:config.TOP_K]
    yield {"type": "sources", "items": _source_items(chunks)}
    yield {"type": "status", "text": "Нашел подходящие положения, формулирую ответ…"}

    messages =[{"role": "system", "content": SYSTEM_PROMPT}]
    for h in sess["history"][-5:-1]:
        messages.append({"role": h["role"], "content": h["content"][:600]})
    messages.append({"role": "user", "content": f"Фрагменты базы знаний:\n\n{_context_block(chunks)}\n\n"
                                                f"Вопрос работника: {msg}"})
    full = ""
    try:
        for piece in llm.chat_stream(messages):
            full += piece
            yield text(piece)
    except llm.LLMUnavailable:
        yield from extractive_answer(chunks)
        return
    yield from verify_citations(full, chunks)


def verify_citations(answer_text: str, chunks: list[dict]) -> Iterator[Event]:
    """Защита от «галлюцинаций»: ссылки только на реально переданные фрагменты."""
    cited = {int(n) for n in re.findall(r"\[(\d+)\]", answer_text)}
    bad = sorted(n for n in cited if n < 1 or n > len(chunks))
    used = sorted(n for n in cited if 1 <= n <= len(chunks))
    # Отказ — только если ответ целиком сводится к «нет информации», а не содержит эту фразу среди текста.
    refusal = "нет информации" in answer_text.lower() and len(answer_text.strip()) < 160
    if not refusal and "нет информации" in answer_text.lower():
        yield text("\n\n> ⚠️ В ответе есть противоречивая оговорка об отсутствии информации — сверьте ответ "
                   "с положениями в блоке «Источники».")
    yield {"type": "citations", "used": used or ([1] if refusal and chunks[0]["relevance"] >= 55 else []), "invalid": bad}
    if refusal and chunks[0]["relevance"] >= 55:
        # Модель отказалась, хотя найдено сильно релевантное положение — показываем его, а не прячем.
        c = chunks[0]
        body = c["text"] if len(c["text"]) < 900 else c["text"][:900] + "…"
        yield text(f"\n\nВместе с тем найдено положение с высокой релевантностью ({c['relevance']}%) — "
                   f"ознакомьтесь с ним напрямую:\n\n> **{_label(c)}** [1]: {body}")
        return
    if bad:
        yield text(f"\n\n> ⚠️ Ссылки {', '.join(f'[{n}]' for n in bad)} не соответствуют найденным положениям — "
                   f"эту часть ответа перепроверьте по первоисточнику.")
    elif not used and not refusal:
        yield text("\n\n> ⚠️ Ответ сформирован без явных ссылок — сверьте его с положениями в блоке «Источники».")


def extractive_answer(chunks: list[dict]) -> Iterator[Event]:
    """Режим без LLM: показываем найденные положения как есть."""
    yield text("Языковая модель сейчас недоступна, поэтому привожу найденные положения без пересказа:\n\n")
    for i, c in enumerate(chunks[:3], 1):
        body = c["text"] if len(c["text"]) < 700 else c["text"][:700] + "…"
        yield text(f"**{_label(c)}** [{i}]\n\n{body}\n\n")
    yield {"type": "citations", "used": list(range(1, min(3, len(chunks)) + 1)), "invalid": []}


def not_found(chunks: list[dict]) -> Iterator[Event]:
    yield text("В базе знаний нет информации, которая достоверно отвечает на этот вопрос, поэтому я не буду "
               "предполагать ответ.\n\n"
               f"**Куда обратиться:** {config.SUPPORT_CONTACT}")
    near = [c for c in chunks[:3] if c["relevance"] >= 32]
    if near:
        yield text("\n\nВозможно, связанные положения:\n" +
                   "\n".join(f"- {_label(c)} — {c['text'][:140]}…" for c in near))
    yield {"type": "no_answer"}
    yield buttons(["Какие шаблоны есть?", "Что ты умеешь?"])


# ---------------------------------------------------------------- Маршрут визирования

def route_flow(sess: dict, msg: str, user: dict, pending: dict) -> Iterator[Event]:
    doc_type = pending.get("doc_type") or routing.detect_doc_type(msg)
    found = match_modules(msg)
    module = found[0][1] if found and found[0][0] >= 0.5 else None
    if module and not doc_type:
        doc_type = module.doc_type
    if not doc_type:
        sess["pending"] = {"type": "route", "text": msg}
        yield text("Чтобы определить маршрут по Матрице обязательного визирования, **уточните вид документа:**")
        yield buttons(["Приказ", "Распоряжение", "Письмо"])
        return
    uses_module = pending.get("uses_module")
    low = msg.lower()
    answer_yes = bool(re.match(r"^(да|связан|требуется|конечно)", pending.get("answer", "").lower().strip()))
    if pending.get("asked") == "uses_module":
        uses_module = answer_yes
    elif re.search(r"без модул|не по модул|без шаблон", low):
        uses_module = False
    elif re.search(r"\bмодул\w*|по шаблону", low):
        uses_module = True
    if module and uses_module is None and doc_type == "приказ":
        uses_module = True
    row, missing = routing.pick_row(doc_type, msg, uses_module)
    if "uses_module" in missing:
        sess["pending"] = {"type": "route", "text": msg, "doc_type": doc_type, "asked": "uses_module"}
        yield text(f"Для приказов этой тематики строка Матрицы зависит от того, **подготовлен ли проект с "
                   f"использованием модульного документа** (Матрица, внутренние документы, строки 1–4). Проект готовится "
                   f"по модулю?")
        yield buttons(["Да, по модулю", "Нет, без модуля"])
        return
    if "addressee" in missing:
        sess["pending"] = {"type": "route", "text": msg, "doc_type": doc_type}
        yield text("Для писем маршрут зависит от адресата. **Кому адресовано письмо?**")
        yield buttons(["Органу государственной власти", "Международной организации / иностранному банку",
                       "Российскому юридическому лицу"])
        return
    conditions = dict(pending.get("conditions") or {})
    if pending.get("asked") in ("is_access", "it_project"):
        conditions[pending["asked"]] = answer_yes
    if row and "is_access" not in conditions and re.search(r"доступ", low):
        conditions["is_access"] = not re.search(r"без доступ|не связан", low)
    if row and "it_project" not in conditions and re.search(r"\bит\b|ит-|информационн", low):
        conditions["it_project"] = True
    route = routing.build_route(doc_type, row, conditions, modular=bool(uses_module))
    md = routing.route_markdown(route)
    if module and uses_module and not pending.get("uses_module"):
        md = f"*Исхожу из того, что проект готовится по модулю «{module.title}».*\n\n" + md
    yield text(md)
    if route["unknown_conditions"]:
        u = route["unknown_conditions"][0]
        sess["pending"] = {"type": "route", "text": msg, "doc_type": doc_type, "uses_module": uses_module,
                           "asked": u["condition_key"], "conditions": conditions}
        yield text(f"\n\n❓ **Уточните:** {u['why'][len('при условии: '):]}? От этого зависит, нужна ли виза "
                   f"подразделения «{u['unit']}».")
        yield buttons(["Да", "Нет"])
    srcs = [c for c in KB.chunks if (c["doc_id"] == "instruction_dou" and c["ref"] in ("п. 10.3", "п. 10.6", "п. 10.13"))
            or (c["doc_id"] == "matrix_visas" and row and c["ref"] == f"{'внутренние' if row['group'] == 'internal' else 'внешние'}, строка {row['num']}")]
    yield {"type": "sources", "items": _source_items([dict(c, relevance=100) for c in srcs])}


# ---------------------------------------------------------------- Мастер модулей

def create_flow(sess: dict, msg: str, user: dict) -> Iterator[Event]:
    found = match_modules(msg)
    doc_type = routing.detect_doc_type(msg)
    if found:
        top = found[0]
        close = [m for s, m in found if s >= top[0] * 0.8]
        if doc_type in ("приказ", "распоряжение"):
            close = [m for m in close if m.doc_type == doc_type] or close
        if len(close) == 1 or top[0] >= 1.0 and (len(found) == 1 or found[1][0] < top[0] * 0.8):
            yield from start_wizard(sess, close[0], msg, user)
            return
        sess["pending"] = {"type": "pick_module", "initial": msg}
        yield text("Нашел несколько подходящих шаблонов. **Какой вам нужен?**")
        yield buttons([m.title for m in close[:5]])
        return
    # Шаблона нет — честно говорим и даем ориентиры по Инструкции.
    if doc_type in ("приказ", "распоряжение"):
        cands = [m for m in modules.MODULES if m.doc_type == doc_type]
        sess["pending"] = {"type": "pick_module", "initial": msg}
        yield text(f"Не нашел модуля, который точно подходит под ваш запрос. Для вида «{doc_type}» есть такие шаблоны — "
                   f"выберите подходящий или задайте вопрос о требованиях к оформлению:")
        yield buttons([m.title for m in cands])
        return
    yield text("Для такого документа в базе нет модульного шаблона. Ниже — требования Инструкции, которые помогут "
               "подготовить его самостоятельно.\n\n")
    yield from answer(sess, f"требования к подготовке и оформлению: {msg}", user)


def start_wizard(sess: dict, m: modules.Module, initial: str, user: dict) -> Iterator[Event]:
    sess["wizard"] = {"module": m.id, "values": {}, "idx": 0, "editing": None, "started": dt.datetime.now().isoformat()}
    yield {"type": "module", "module": {"id": m.id, "title": m.title, "file": m.file}}
    yield text(f"Подготовим проект: **{m.title}**.\n\n"
               f"Я задам {len(m.fields)} коротких вопросов по полям модуля (подсказки взяты из «Примечаний по "
               f"заполнению»). Можно отвечать своими словами; «пропустить» — для необязательных полей, «назад» — "
               f"вернуться, «отмена» — выйти. Исходный модуль можно скачать: [{m.file}](/api/modules/{m.id}/file)\n\n")
    extracted = {}
    if len(initial.split()) > 7:
        yield {"type": "status", "text": "Извлекаю данные из вашего сообщения…"}
        extracted = extract_fields(m, initial)
    if extracted:
        sess["wizard"]["values"].update(extracted)
        yield text("Из вашего сообщения я уже понял:\n" +
                   "\n".join(f"- {_field(m, k).label}: **{_show(_field(m, k), v)}**"
                             for k, v in extracted.items()) + "\n\n")
    yield from ask_next(sess)


def _field(m: modules.Module, key: str) -> modules.Field:
    return next(f for f in m.fields if f.key == key)


def _show(f: modules.Field, v) -> str:
    if v in ("", None):
        return "—"
    if f.kind == "date":
        return modules.fmt_date(dt.date.fromisoformat(v))
    if f.kind == "list":
        return "; ".join(v)
    if f.kind == "yesno":
        return "да" if v else "нет"
    return str(v)


def extract_fields(m: modules.Module, msg: str) -> dict:
    """LLM извлекает из свободного текста значения, которые пользователь назвал явно."""
    props = {f.key: {"type": ["string", "null"]} for f in m.fields if f.kind not in ("yesno",)}
    desc = "\n".join(f"- {f.key}: {f.question} Пример: {f.example}" for f in m.fields if f.key in props)
    prompt = (f"Из сообщения работника извлеки значения полей документа «{m.title}». Заполняй поле ТОЛЬКО если "
              f"значение прямо названо в сообщении, иначе null. Ничего не придумывай. Даты — в формате ДД.ММ.ГГГГ.\n"
              f"Поля:\n{desc}\n\nСообщение: {msg}\n\nОтвет — JSON-объект с этими ключами.")
    try:
        raw = llm.chat([{"role": "user", "content": prompt}],
                       fmt={"type": "object", "properties": props}, temperature=0)
        data = json.loads(raw)
    except (llm.LLMUnavailable, json.JSONDecodeError):
        return {}
    out = {}
    low = msg.lower()
    msg_starts = {w[:5] for w in re.findall(r"[а-яёa-z0-9]+", low)}
    for k, v in data.items():
        if not v or k not in props or not isinstance(v, str):
            continue
        # Если модель обрезала начало слова («готовке» вместо «подготовке») — берем слово из сообщения.
        pos = low.find(v.lower())
        if pos > 0 and low[pos - 1].isalpha():
            start = pos
            while start > 0 and low[start - 1].isalpha():
                start -= 1
            v = msg[start:pos] + v
        # Значение должно действительно присутствовать в сообщении (защита от выдумки).
        words = re.findall(r"[а-яёa-z0-9]{4,}", v.lower())
        if not words or not all(w[:5] in msg_starts for w in words):
            continue
        v = re.sub(r"^(о|об)\s+(вводе в эксплуатацию|создании|проведении)\s+", "", v.strip(), flags=re.I)
        val, err = modules.normalize(_field(m, k), v)
        if err is None and val not in ("", None):
            out[k] = val
    return out


def ask_next(sess: dict) -> Iterator[Event]:
    w = sess["wizard"]
    m = modules.BY_ID[w["module"]]
    while w["idx"] < len(m.fields) and m.fields[w["idx"]].key in w["values"]:
        w["idx"] += 1
    if w["idx"] >= len(m.fields):
        yield from finish_wizard(sess)
        return
    f = m.fields[w["idx"]]
    total = len(m.fields)
    q = f"**Вопрос {w['idx'] + 1} из {total}.** {f.question}"
    if f.hint:
        q += f"\n\n💡 {f.hint}"
    if f.example:
        q += f"\n\n*Например:* {f.example}"
    yield text(q)
    btns = list(f.options)
    if not f.required:
        btns.append("Пропустить")
    if w["idx"] > 0:
        btns.append("Назад")
    btns.append("Отмена")
    yield buttons(btns)
    yield {"type": "progress", "done": w["idx"], "total": total}


def wizard_step(sess: dict, msg: str, user: dict) -> Iterator[Event]:
    w = sess["wizard"]
    m = modules.BY_ID[w["module"]]
    low = msg.strip().lower()
    if low in ("отмена", "отменить", "выход", "стоп"):
        sess["wizard"] = None
        yield text("Подготовку проекта отменил. Чем еще помочь?")
        return
    if w.get("review"):
        if low in ("сформировать", "готово", "да", "сформировать документ"):
            yield from generate(sess, user)
            return
        mm = re.match(r"^(изменить|исправить)\s*(\d+)", low)
        if mm and 1 <= int(mm.group(2)) <= len(m.fields):
            w["idx"] = int(mm.group(2)) - 1
            w["values"].pop(m.fields[w["idx"]].key, None)
            w["review"] = False
            w["editing"] = True
            yield from ask_next(sess)
            return
        yield text("Напишите «Сформировать» или «Изменить N», где N — номер поля.")
        return
    if low == "назад" and w["idx"] > 0:
        w["idx"] -= 1
        w["values"].pop(m.fields[w["idx"]].key, None)
        yield from ask_next(sess)
        return
    f = m.fields[w["idx"]]
    val, err = modules.normalize(f, msg)
    if err:
        yield text(f"⚠️ {err}")
        yield buttons(list(f.options) + (["Пропустить"] if not f.required else []) + ["Отмена"])
        return
    w["values"][f.key] = val
    w["idx"] += 1
    if w.get("editing"):
        w["idx"] = len(m.fields)
        w["editing"] = None
    yield from ask_next(sess)


def finish_wizard(sess: dict) -> Iterator[Event]:
    w = sess["wizard"]
    m = modules.BY_ID[w["module"]]
    w["review"] = True
    rows = [f"{i}. {f.label}: **{_show(f, w['values'].get(f.key))}**"
            for i, f in enumerate(m.fields, 1)]
    warns = modules.semantic_checks(m, w["values"])
    md = "Все поля заполнены. Проверьте данные:\n\n" + "\n".join(rows)
    if warns:
        md += "\n\n**Обратите внимание:**\n" + "\n".join(f"- ⚠️ {x}" for x in warns)
    try:
        preview = modules.to_text(modules.render(m, w["values"]))
        md += "\n\n**Предпросмотр текста:**\n\n```\n" + preview.replace("**", "") + "\n```"
    except (KeyError, ValueError) as e:
        md += f"\n\n⚠️ Не удалось собрать предпросмотр: {e}"
    yield text(md + "\n\nЕсли все верно — нажмите «Сформировать». Чтобы исправить поле — «Изменить N».")
    yield buttons(["Сформировать"] + [f"Изменить {i}" for i in range(1, len(m.fields) + 1)][:6] + ["Отмена"])


def generate(sess: dict, user: dict) -> Iterator[Event]:
    w = sess["wizard"]
    m = modules.BY_ID[w["module"]]
    r = modules.render(m, w["values"])
    fname = modules.build_docx(m, r)
    conditions = {cond: w["values"].get(key) for key, cond in m.conditions.items()}
    row = routing._row(routing.matrix()["rows"], "internal", m.matrix_row)
    route = routing.build_route(m.doc_type, row, conditions, modular=True)
    sess["wizard"] = None
    record_file(user["id"], {"name": fname, "title": r["title"], "module": m.title, "doc_type": m.doc_type,
                             "created": dt.datetime.now().isoformat(timespec="seconds")})
    yield {"type": "file", "name": fname, "url": f"/api/files/{fname}", "title": r["title"]}
    yield text(f"✅ Проект сформирован: **{r['title']}**.\n\n"
               f"Из файла удалены гриф утверждения, пометка «МОДУЛЬНЫЙ ДОКУМЕНТ», линии и номера примечаний; "
               f"служебные зоны даты, регистрационного номера и визуализации ЭП сохранены (Метод. рекомендации, п. 3.3).\n\n"
               f"**Дальнейшие шаги:**\n"
               f"1. Создайте РК проекта в ИИС ДО; в поле «Примечание» укажите (п. 2.3 Метод. рекомендаций):\n"
               f"   > {modules.card_note(m)}\n"
               f"   Регистрационные данные и редакцию модуля возьмите из реестра модулей (п. 1.2).\n"
               f"2. Установите тематическую связь между РК проекта и РК модуля (п. 2.4).\n"
               f"3. Сверьте проект с модулем и завизируйте как ответственный исполнитель (п. 4.1).\n\n"
               f"---\n**Маршрут визирования:**\n\n{routing.route_markdown(route)}")
    srcs = [c for c in KB.chunks if (c["doc_id"] == "modules_guide" and c["ref"] in ("п. 2.3", "п. 3.3", "п. 4.2"))
            or (c["doc_id"] == "matrix_visas" and c["ref"] == f"внутренние, строка {m.matrix_row}")
            or (c["doc_id"] == "instruction_dou" and c["ref"] == "п. 10.3")]
    yield {"type": "sources", "items": _source_items([dict(c, relevance=100) for c in srcs])}
