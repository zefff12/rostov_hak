"""Оценка качества поиска: Hit@1, Hit@3, Hit@6 и корректность отказа.

Запуск: py -m tests.eval_retrieval [--no-emb]
"""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
from app import config  # noqa: E402
from app.kb import KB  # noqa: E402


def main():
    use_emb = "--no-emb" not in sys.argv
    print(KB.build(use_embeddings=use_emb))
    qs = json.loads((Path(__file__).parent / "questions.json").read_text(encoding="utf-8"))
    hits = {1: 0, 3: 0, 6: 0}
    answerable = refusals_ok = refusals_total = false_refusals = 0
    for item in qs:
        res = KB.search(item["q"], k=6)
        best = res[0]["relevance"] / 100 if res else 0
        refused = best < config.MIN_RELEVANCE
        refs = [r["ref"] for r in res]
        if item.get("no_answer"):
            refusals_total += 1
            refusals_ok += refused
            mark = "OK " if refused else "ERR"
            print(f"{mark} [нет ответа] {item['q']} → {best:.2f} {refs[:2]}")
            continue
        answerable += 1
        false_refusals += refused
        rank = next((i + 1 for i, r in enumerate(refs) if r in item["expect"]), None)
        for k in hits:
            hits[k] += bool(rank and rank <= k)
        mark = "OK " if rank == 1 else ("~  " if rank else "ERR")
        print(f"{mark} rank={rank} rel={best:.2f} {item['q']} → {refs[:3]}")
    print(f"\nВопросов с ответом: {answerable}")
    for k, v in hits.items():
        print(f"Hit@{k}: {v / answerable:.0%}")
    print(f"Ложные отказы: {false_refusals}/{answerable}")
    print(f"Корректные отказы: {refusals_ok}/{refusals_total}")


if __name__ == "__main__":
    main()
