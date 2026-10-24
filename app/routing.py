"""Маршрут визирования по Матрице и сроки по главе 10 Инструкции.

Логика детерминированная (без LLM): маршрут и сроки — это правила,
в которых модель не должна ошибаться.
"""
import datetime as dt
import re

from . import config, parsers

# Срок каждого первичного и повторного рассмотрения, рабочих дней (п. 10.3).
REVIEW_DAYS = {
    "приказ": 10, "распоряжение": 3, "письмо": 3, "соглашение": 20, "методические рекомендации": 15,
    "положение": 10, "регламент": 10, "порядок": 10, "правила": 10, "протокол": 3, "информационное письмо": 5,
    "официальное разъяснение": 10, "проект модуля": 10, "аналитический документ": 10, "концепция": 10,
    "иной документ": 5,
}
# Виды документов, подлежащие лингвистической экспертизе (п. 10.13).
LINGUISTIC = {"приказ", "распоряжение", "положение", "регламент", "порядок", "правила", "методические рекомендации",
              "официальное разъяснение", "проект модуля"}

DOC_TYPE_PATTERNS = [
    ("информационное письмо", r"информационн\w* письм"),
    ("методические рекомендации", r"методическ\w* рекомендац"),
    ("официальное разъяснение", r"официальн\w* разъяснени"),
    ("проект модуля", r"проект\w* модул"),
    ("приказ", r"приказ"),
    ("распоряжение", r"распоряжени"),
    ("письмо", r"письм"),
    ("соглашение", r"соглашени"),
    ("положение", r"положени"),
    ("регламент", r"регламент"),
    ("порядок", r"\bпорядк|\bпорядок"),
    ("правила", r"\bправил"),
    ("протокол", r"протокол"),
    ("концепция", r"концепци"),
]


def detect_doc_type(text: str) -> str | None:
    t = text.lower()
    for name, pat in DOC_TYPE_PATTERNS:
        if re.search(pat, t):
            return name
    return None


_matrix_cache = None


def matrix() -> dict:
    global _matrix_cache
    if _matrix_cache is None:
        from . import kb
        reg = kb.load_registry()
        doc = next(d for d in reg["documents"] if d["doc_id"] == "matrix_visas")
        _matrix_cache = parsers.parse_matrix(config.KB_DIR / kb.active_version(doc)["file"])
    return _matrix_cache


def reset_cache():
    global _matrix_cache
    _matrix_cache = None


# Ключевые слова для выбора строки Матрицы по описанию документа.
ROW_HINTS = {
    ("internal", 1): r"выходн|праздничн|рабоч\w* календар",
    ("internal", 2): r"(при[её]м\w*|передач\w*).{0,30}дел",
    ("internal", 3): r"информационн\w* систем|эксплуатац|\bис\b|компонент",
    ("internal", 5): r"материальн\w* помощ|выплат|преми",
    ("internal", 6): r"рабоч\w* групп|проектн\w* комитет|комисси|временн\w* объединени",
    ("external", 1): r"орган\w* государствен|администраци\w* президент|совет\w* федерации|депутат|государственн\w* дум|правительств|сч[её]тн\w* палат",
    ("external", 2): r"иностранн|международн|центральн\w* банк",
    ("external", 3): r"юридическ\w* лиц|российск\w* организац|компани",
}


def pick_row(doc_type: str, description: str, uses_module: bool | None) -> tuple[dict | None, list[str]]:
    """Возвращает строку Матрицы и список недостающих сведений (для уточняющих вопросов)."""
    rows = matrix()["rows"]
    desc = description.lower()
    missing = []
    if doc_type == "письмо":
        for (grp, num), pat in ROW_HINTS.items():
            if grp == "external" and re.search(pat, desc):
                return _row(rows, grp, num), missing
        missing.append("addressee")
        return None, missing
    if doc_type == "приказ":
        for num in (1, 2, 3):
            if re.search(ROW_HINTS[("internal", num)], desc):
                if uses_module is None:
                    missing.append("uses_module")
                    return _row(rows, "internal", num), missing
                return _row(rows, "internal", num if uses_module else 4), missing
        return _row(rows, "internal", 4), missing
    if doc_type == "распоряжение":
        for num in (5, 6):
            if re.search(ROW_HINTS[("internal", num)], desc):
                return _row(rows, "internal", num), missing
        return _row(rows, "internal", 7), missing
    return None, missing


def _row(rows, grp, num):
    return next((r for r in rows if r["group"] == grp and r["num"] == num), None)


def visas_from_row(row: dict, conditions: dict | None = None) -> list[dict]:
    """Обязательные визирующие по строке Матрицы. conditions: {'is_access': bool, 'it_project': bool}."""
    conditions = conditions or {}
    out = []
    for col, mark in row["marks"].items():
        m = (mark or "").strip()
        if col != "Прочие департаменты":
            if m.lower() == "да":
                out.append({"unit": col, "why": "отметка «Да» в Матрице"})
            continue
        if not m or m.lower() == "нет":
            continue
        cond = re.search(r"визирует проект,? если (.+)", m)
        if cond:
            unit = m[:cond.start()].strip()
            key = "is_access" if "доступ" in m else "it_project"
            val = conditions.get(key)
            out.append({"unit": unit, "why": f"при условии: {cond.group(1).rstrip('.')}",
                        "conditional": True, "condition_key": key, "applies": val})
        else:
            for unit in re.split(r"\n|(?<=[а-я])\s(?=Департамент)", m):
                if unit.strip():
                    out.append({"unit": unit.strip(), "why": "указан в Матрице"})
    return out


def days_word(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} рабочий день"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return f"{n} рабочих дня"
    return f"{n} рабочих дней"


def add_workdays(start: dt.date, days: int) -> dt.date:
    d = start
    while days > 0:
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            days -= 1
    return d


def build_route(doc_type: str, row: dict | None, conditions: dict | None = None, modular: bool = False,
                start: dt.date | None = None) -> dict:
    """Полный маршрут: последовательность визирования (п. 10.6), сроки (п. 10.3, 10.15), экспертиза (п. 10.13)."""
    start = start or dt.date.today()
    mandatory = visas_from_row(row, conditions) if row else []
    applies = [v for v in mandatory if not v.get("conditional") or v.get("applies")]
    unknown = [v for v in mandatory if v.get("conditional") and v.get("applies") is None]
    mand_other = [v["unit"] for v in applies if v["unit"] not in ("Юридическое подразделение", "Департамент ДОУ")]
    steps = [
        ("Ответственный исполнитель", "п. 10.6, 1)"),
        ("Работники ответственного подразделения, участвовавшие в разработке", "п. 10.6, 2)"),
        ("Руководитель ответственного подразделения", "п. 10.6, 3)"),
        ("Руководители (заместители) заинтересованных подразделений — чья компетенция затронута", "п. 10.6, 4); Матрица, п. 3"),
    ]
    for u in mand_other:
        steps.append((f"{u} — обязательное визирование по Матрице", "п. 10.6, 5)"))
    if any(v["unit"] == "Юридическое подразделение" for v in applies):
        steps.append(("Юридическое подразделение", "п. 10.6, 6)"))
    if doc_type in LINGUISTIC:
        steps.append(("Лингвистическая экспертиза (корректор Департамента ДОУ)",
                      "Метод. рекомендации, п. 4.2" if modular else "п. 10.13"))
    if any(v["unit"] == "Департамент ДОУ" for v in applies):
        steps.append(("Департамент ДОУ", "п. 10.6, 7)"))
    steps.append(("Должностное лицо, координирующее работу подразделения (если не подписант)", "п. 10.6, 8)"))
    days = REVIEW_DAYS.get(doc_type, REVIEW_DAYS["иной документ"])
    return {
        "doc_type": doc_type,
        "matrix_row": row,
        "mandatory": mandatory,
        "unknown_conditions": unknown,
        "steps": [{"who": w, "ref": r} for w, r in steps],
        "review_days": days,
        "review_deadline": add_workdays(start, days).strftime("%d.%m.%Y"),
        "sign_days": 3,
        "linguistic": doc_type in LINGUISTIC,
        "modular_note": modular,
    }


def route_markdown(route: dict) -> str:
    row = route["matrix_row"]
    lines = []
    if row:
        grp = "внутренние" if row["group"] == "internal" else "внешние"
        lines.append(f"**Строка Матрицы:** {grp} документы, п. {row['num']} — {row['title']}")
    if route["mandatory"]:
        lines.append("\n**Обязательное визирование по Матрице:**")
        for v in route["mandatory"]:
            flag = ""
            if v.get("conditional"):
                flag = {True: " — **требуется**", False: " — не требуется", None: " — *уточните условие*"}[v.get("applies")]
            lines.append(f"- {v['unit']} ({v['why']}){flag}")
    lines.append("\n**Последовательность визирования:**")
    for i, s in enumerate(route["steps"], 1):
        lines.append(f"{i}. {s['who']} — *{s['ref']}*")
    lines.append(f"\n**Сроки:** каждое первичное и повторное рассмотрение — до **{days_word(route['review_days'])}** "
                 f"(п. 10.3); если направить сегодня — ориентировочно до {route['review_deadline']} "
                 f"(без учета праздничных дней). Подписание — как правило, не более 3 рабочих дней (п. 10.15).")
    if route["modular_note"]:
        lines.append("\n*Проект, строго соответствующий модулю, не направляется на повторное полное согласование "
                     "подразделениям, согласовавшим модуль; лингвистическая экспертиза и визирование Департаментом ДОУ "
                     "сохраняются (Метод. рекомендации, п. 1.3).*")
    return "\n".join(lines)
