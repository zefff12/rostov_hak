"""Проверка загруженного проекта документа на соответствие требованиям.

Правила — из Методических рекомендаций по модульным документам (п. 3.3, 4.1),
«Общих правил» модулей и главы 10 Инструкции. Каждая находка ссылается на источник.
"""
import re
from pathlib import Path

from . import modules, parsers, routing


def _find_module(paras: list[str]) -> modules.Module | None:
    text = " ".join(paras[:15]).lower()
    best, score = None, 0
    for m in modules.MODULES:
        words = [w for w in re.findall(r"[а-яё]{5,}", m.title.lower()) if w not in ("приказ", "распоряжение")]
        s = sum(w[:6] in text for w in words) / max(len(words), 1)
        if s > score:
            best, score = m, s
    return best if score >= 0.6 else None


def check_document(path: Path) -> dict:
    if path.suffix.lower() == ".docx":
        paras = parsers.docx_paragraphs(path)
    elif path.suffix.lower() == ".pdf":
        paras = [s for s, _ in parsers.pdf_lines(path)]
    else:
        paras = [p for p in path.read_text(encoding="utf-8", errors="ignore").splitlines() if p.strip()]
    text = "\n".join(paras)
    issues: list[dict] = []

    def add(level, msg, ref, fragment=""):
        issues.append({"level": level, "message": msg, "ref": ref, "fragment": fragment[:160]})

    # 1. Служебные элементы модуля, которые должны быть удалены (Метод. рек. п. 3.3).
    for p in paras:
        if re.search(r"_{3,}", p):
            add("error", "Остались линии для заполнения «____» — замените их фактическими данными.",
                "Метод. рекомендации, п. 3.3", p)
    for p in paras:
        if re.search(r"\[\d{1,2}\]", p):
            add("error", "Остались номера примечаний вида [N] — удалите их.", "Метод. рекомендации, п. 3.3", p)
    for p in paras:
        if re.search(r"\[[^\]]{3,}\]", p) and not re.search(r"\[\d{1,2}\]", p):
            add("error", "Остались квадратные скобки с необязательным текстом — примите решение и удалите скобки.",
                "Метод. рекомендации, п. 3.3", p)
    for p in paras:
        if re.search(r"[а-яё]{3,}\s*/\s*[а-яё]{3,}", p):  # строчные варианты; «ДАТА / РЕГ. НОМЕР» не задевает
            add("error", "Остался неиспользованный вариант через «/» — оставьте нужный.", "Метод. рекомендации, п. 3.3", p)
    if re.search(r"МОДУЛЬНЫЙ ДОКУМЕНТ", text):
        add("error", "Удалите пометку «МОДУЛЬНЫЙ ДОКУМЕНТ».", "Метод. рекомендации, п. 3.3")
    if re.search(r"^\s*УТВЕРЖДАЮ", text, re.M):
        add("error", "Удалите гриф утверждения вверху первой страницы.", "Метод. рекомендации, п. 3.3")
    if re.search(r"Примечания по заполнению", text):
        add("error", "Удалите «Примечания по заполнению» и пояснения к модулю.", "Метод. рекомендации, п. 3.3")
    if re.search(r"И\. ?О\. Фамилия", text):
        add("error", "Не указаны инициалы и фамилия подписанта (осталось «И. О. Фамилия»).", "Примечание к модулю «Подписант»")

    # 2. Служебные зоны, которые нужно сохранить.
    if not re.search(r"ДАТА\s*/\s*РЕГИСТРАЦИОННЫЙ НОМЕР", text):
        add("error", "Отсутствует служебная зона «ДАТА / РЕГИСТРАЦИОННЫЙ НОМЕР (НЕ УДАЛЯТЬ)».",
            "Метод. рекомендации, п. 3.3")
    if not re.search(r"ВИЗУАЛИЗАЦИЯ ЭП", text):
        add("error", "Отсутствует блок «ВИЗУАЛИЗАЦИЯ ЭП (НЕ УДАЛЯТЬ)».", "Метод. рекомендации, п. 3.3")

    # 3. Вид документа и распорядительное слово.
    kind = "ПРИКАЗ" if re.search(r"^\s*ПРИКАЗ\s*$", text, re.M) else (
        "РАСПОРЯЖЕНИЕ" if re.search(r"^\s*РАСПОРЯЖЕНИЕ\s*$", text, re.M) else None)
    if kind == "ПРИКАЗ" and "ПРИКАЗЫВАЮ" not in text:
        add("warning", "В приказе не найдено распорядительное слово «ПРИКАЗЫВАЮ:».", "Модуль приказа")
    if kind == "РАСПОРЯЖЕНИЕ" and "ОБЯЗЫВАЮ" not in text:
        add("warning", "В распоряжении не найдено распорядительное слово «ОБЯЗЫВАЮ:».", "Модуль распоряжения")
    if not kind:
        add("info", "Не удалось определить вид документа (ПРИКАЗ / РАСПОРЯЖЕНИЕ) по шапке.", "Глава 9 Инструкции")

    # 4. Нумерация пунктов (Метод. рек. п. 3.3 — «проверяет нумерацию пунктов»).
    nums = [int(m.group(1)) for p in paras for m in [re.match(r"^(\d{1,2})\.\s", p)] if m]
    if nums:
        expected = list(range(1, len(nums) + 1))
        if nums != expected:
            add("warning", f"Нарушена последовательная нумерация пунктов: {nums}.", "Метод. рекомендации, п. 3.3")

    # 5. Контроль исполнения.
    if kind and not re.search(r"Контроль за исполнением", text) and not re.search(r"Ответственным за исполнение", text):
        add("warning", "Не найден пункт о контроле за исполнением.", "Модуль документа")

    # 6. Единообразие сокращенного наименования (модуль ввода ИС, примечание 4).
    short = re.search(r"\(далее\s*[–—-]\s*«([^»]+)»\)", text)
    if short:
        name = short.group(1)
        others = set(re.findall(r"«([^»]{1,40})»", text)) - {name}
        similar = [o for o in others if o.lower() != name.lower() and o.lower().replace(" ", "") == name.lower().replace(" ", "")]
        if similar:
            add("warning", f"Сокращенное наименование написано по-разному: «{name}» и {', '.join('«'+s+'»' for s in similar)}.",
                "Примечание 4 к модулю")

    # Пройденные проверки — для счетчика «Соответствует».
    triggered = " ".join(i["message"] for i in issues)
    passed = [title for key, title in PASS_RULES if key not in triggered]

    module = _find_module(paras)
    doc_type = routing.detect_doc_type(kind or "") if kind else None
    summary = {
        "errors": sum(i["level"] == "error" for i in issues),
        "warnings": sum(i["level"] == "warning" for i in issues),
    }
    recs = []
    if module:
        recs.append(f"Документ похож на модуль «{module.title}». Сверьте постоянные формулировки с модулем: при их "
                    f"изменении проект считается отличающимся от модуля и проходит полное согласование по главе 10 "
                    f"(Метод. рекомендации, п. 1.3).")
        recs.append("В поле «Примечание» РК укажите факт применения модуля, его реквизиты и редакцию (п. 2.3).")
    if doc_type:
        recs.append(f"Срок каждого рассмотрения проекта: {routing.days_word(routing.REVIEW_DAYS[doc_type])} "
                    f"(п. 10.3 Инструкции).")
    if not issues:
        recs.append("Формальных нарушений не найдено. Перед направлением по маршруту проверьте грамматическое "
                    "согласование, ссылки на приложения и единообразие наименований (п. 3.3).")
    summary["passed"] = len(passed)
    return {"kind": kind, "module": module.title if module else None, "module_id": module.id if module else None,
            "issues": issues, "passed": passed, "summary": summary, "recommendations": recs,
            "paragraphs": len(paras)}


# (фрагмент сообщения о нарушении, формулировка пройденной проверки)
PASS_RULES = [
    ("линии для заполнения", "Нет незаполненных линий «____»"),
    ("номера примечаний", "Удалены номера примечаний"),
    ("квадратные скобки", "Нет квадратных скобок с вариантами"),
    ("через «/»", "Выбраны варианты вместо «…/…»"),
    ("МОДУЛЬНЫЙ ДОКУМЕНТ", "Удалена пометка «МОДУЛЬНЫЙ ДОКУМЕНТ»"),
    ("гриф утверждения", "Удален гриф утверждения"),
    ("Примечания по заполнению", "Удалены примечания к модулю"),
    ("И. О. Фамилия", "Указан подписант"),
    ("ДАТА / РЕГИСТРАЦИОННЫЙ", "Сохранена зона даты и регистрационного номера"),
    ("ВИЗУАЛИЗАЦИЯ ЭП", "Сохранен блок визуализации ЭП"),
    ("распорядительное слово", "Есть распорядительное слово"),
    ("нумерация", "Последовательная нумерация пунктов"),
    ("контроле за исполнением", "Есть пункт о контроле исполнения"),
    ("Сокращенное наименование", "Единообразные наименования"),
]


def report_markdown(r: dict) -> str:
    head = f"**Результат проверки** ({r['kind'] or 'вид не определен'}"
    head += f", модуль: {r['module']})" if r["module"] else ")"
    lines = [head, f"\nОшибок: **{r['summary']['errors']}**, предупреждений: **{r['summary']['warnings']}**\n"]
    icon = {"error": "❌", "warning": "⚠️", "info": "ℹ️"}
    groups: dict[str, list[dict]] = {}
    for i in r["issues"]:
        groups.setdefault(i["message"], []).append(i)
    for msg, items in groups.items():
        cnt = f" — **{len(items)} мест(а)**" if len(items) > 1 else ""
        lines.append(f"- {icon[items[0]['level']]} {msg}{cnt} *({items[0]['ref']})*")
        for i in [x for x in items if x["fragment"]][:2]:
            lines.append(f"  > {i['fragment']}")
    if r["recommendations"]:
        lines.append("\n**Рекомендации:**")
        lines += [f"- {x}" for x in r["recommendations"]]
    return "\n".join(lines)
