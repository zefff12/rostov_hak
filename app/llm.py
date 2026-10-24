"""Клиент локальной LLM (Ollama). Запросы уходят только на локальный сервер."""
import json
from typing import Iterator

import httpx

from . import config


class LLMUnavailable(RuntimeError):
    pass


def _options(**extra):
    opts = {"temperature": config.LLM_TEMPERATURE, "num_ctx": config.LLM_NUM_CTX}
    opts.update(extra)
    return opts


def is_available() -> bool:
    try:
        r = httpx.get(f"{config.OLLAMA_URL}/api/tags", timeout=3)
        return r.status_code == 200
    except httpx.HTTPError:
        return False


def chat(messages: list[dict], fmt: str | dict | None = None, **opts) -> str:
    payload = {"model": config.LLM_MODEL, "messages": messages, "stream": False, "options": _options(**opts),
               "keep_alive": config.LLM_KEEP_ALIVE}
    if fmt:
        payload["format"] = fmt
    try:
        r = httpx.post(f"{config.OLLAMA_URL}/api/chat", json=payload, timeout=config.LLM_TIMEOUT)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise LLMUnavailable(str(e)) from e
    return r.json()["message"]["content"]


def chat_stream(messages: list[dict], **opts) -> Iterator[str]:
    payload = {"model": config.LLM_MODEL, "messages": messages, "stream": True, "options": _options(**opts),
               "keep_alive": config.LLM_KEEP_ALIVE}
    try:
        with httpx.stream("POST", f"{config.OLLAMA_URL}/api/chat", json=payload, timeout=config.LLM_TIMEOUT) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                data = json.loads(line)
                piece = data.get("message", {}).get("content", "")
                if piece:
                    yield piece
                if data.get("done"):
                    break
    except httpx.HTTPError as e:
        raise LLMUnavailable(str(e)) from e


def embed(texts: list[str], batch: int = 16) -> list[list[float]]:
    out = []
    for i in range(0, len(texts), batch):
        try:
            r = httpx.post(f"{config.OLLAMA_URL}/api/embed",
                           json={"model": config.EMBED_MODEL, "input": texts[i:i + batch], "keep_alive": config.LLM_KEEP_ALIVE},
                           timeout=config.LLM_TIMEOUT)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise LLMUnavailable(str(e)) from e
        out.extend(r.json()["embeddings"])
    return out
