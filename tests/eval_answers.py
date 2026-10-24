"""Прогон всех тестовых вопросов через полный конвейер (поиск + LLM).

Запуск: py -m tests.eval_answers  → tests/answers_<модель>.jsonl
Каждый вопрос — в новой сессии, чтобы ответы не влияли друг на друга.
"""
import json
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
from app import assistant, config  # noqa: E402
from app.kb import KB  # noqa: E402

USER = {"id": "eval", "role": "employee"}


def run_one(q: str) -> dict:
    sess = assistant.get_session(None)
    t0 = time.time()
    rec = {"q": q, "answer": "", "sources": [], "used": [], "invalid": [], "buttons": [], "kind": "qa"}
    for ev in assistant.handle(sess, q, USER):
        if ev["type"] == "text":
            rec["answer"] += ev["text"]
        elif ev["type"] == "sources":
            rec["sources"] = [{"n": s["n"], "ref": s["ref"], "doc": s["doc_short"], "rel": s["relevance"]}
                              for s in ev["items"]]
        elif ev["type"] == "citations":
            rec["used"], rec["invalid"] = ev["used"], ev["invalid"]
        elif ev["type"] == "buttons":
            rec["buttons"] = ev["items"]
        elif ev["type"] == "no_answer":
            rec["kind"] = "refusal"
    rec["seconds"] = round(time.time() - t0, 1)
    if sess.get("pending"):
        rec["kind"] = "clarification"
    elif "Строка Матрицы" in rec["answer"]:
        rec["kind"] = "route"
    return rec


def main():
    print(KB.build())
    qs = json.loads((Path(__file__).parent / "questions.json").read_text(encoding="utf-8"))
    out = Path(__file__).parent / f"answers_{config.LLM_MODEL.replace(':', '_')}.jsonl"
    with out.open("w", encoding="utf-8") as f:
        for i, item in enumerate(qs, 1):
            rec = run_one(item["q"])
            rec.update(expect=item["expect"], no_answer=item.get("no_answer", False))
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            print(f"[{i}/{len(qs)}] {rec['seconds']} c {rec['kind']} — {item['q']}", flush=True)
    print("Готово:", out)


if __name__ == "__main__":
    main()
