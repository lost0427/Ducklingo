import asyncio
import json
import re
from functools import lru_cache
from pathlib import Path

import httpx

from .asr import headers, load_checkpoint, save_checkpoint

PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "sentence_split.md"

CJK_RUN = re.compile(r"(?<=[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]) (?=[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af])")


@lru_cache(maxsize=1)
def _template() -> str:
    return PROMPT_PATH.read_text("utf-8")


def system_prompt(config: dict) -> str:
    return _template().format(
        max_chars_cjk=int(config.get("max_chars_cjk", 21)),
        max_chars_latin=int(config.get("max_chars_latin", 42)),
    )


def batch_words(words: list[dict], size: int) -> list[list[dict]]:
    return [words[i:i + size] for i in range(0, len(words), size)]


def join_text(group: list[dict]) -> str:
    return CJK_RUN.sub("", " ".join(w["text"] for w in group))


def apply_breaks(words: list[dict], marked: str) -> list[dict]:
    sentences, current = [], []
    index = 0
    for token in marked.replace("<br>", " <br> ").split():
        if token == "<br>":
            if current:
                sentences.append(current)
                current = []
            continue
        if index >= len(words) or token != words[index]["text"]:
            raise ValueError("word sequence mismatch")
        current.append(words[index])
        index += 1
    if current:
        sentences.append(current)
    if index != len(words) or not sentences:
        raise ValueError("word sequence mismatch")
    return [
        {"text": join_text(group), "start": group[0]["start"], "end": group[-1]["end"]}
        for group in sentences
    ]


def endpoint(settings: dict) -> str:
    base = settings["base_url"].rstrip("/")
    return f"{base}/chat/completions" if base.endswith("/v1") else f"{base}/v1/chat/completions"


async def call_llm(client: httpx.AsyncClient, text: str, prompt: str, settings: dict, config: dict) -> str:
    retries = int(config.get("retries", 2))
    body = {
        "model": settings["model"],
        "temperature": 0.1,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": f"<text>{text}</text>"},
        ],
    }
    response_format = config.get("response_format")
    if response_format:
        body["response_format"] = {"type": response_format}
    for attempt in range(retries + 1):
        try:
            response = await client.post(endpoint(settings), headers=headers(settings), json=body)
        except httpx.TransportError as exc:
            if attempt == retries:
                raise RuntimeError(f"LLM call failed: {exc}") from exc
        else:
            if response.status_code < 400:
                try:
                    content = response.json()["choices"][0]["message"]["content"]
                except (ValueError, KeyError, IndexError) as exc:
                    raise RuntimeError("LLM returned an unexpected response") from exc
                if response_format:
                    try:
                        content = json.loads(content)["text"]
                    except ValueError as exc:
                        raise RuntimeError("LLM returned invalid JSON content") from exc
                return content
            if (response.status_code < 500 and response.status_code != 429) or attempt == retries:
                raise RuntimeError(
                    f"LLM call failed: HTTP {response.status_code}: {response.text[:200]}"
                )
        import asyncio

        await asyncio.sleep(2 * (attempt + 1))
    raise RuntimeError("unreachable")


async def split_sentences(
    report,
    words: list[dict],
    settings: dict,
    config: dict,
    checkpoint: Path,
    timeout: float = 300.0,
) -> list[dict]:
    size = int(config.get("batch_words", 800))
    batches = batch_words(words, size)
    state = load_checkpoint(checkpoint)
    if not state or state.get("word_count") != len(words) or len(state.get("batches", [])) != len(batches):
        state = {"word_count": len(words), "batches": [{"done": False, "sentences": []} for _ in batches]}
        save_checkpoint(checkpoint, state)
    prompt = system_prompt(config)
    report("sentence")
    async with httpx.AsyncClient(timeout=timeout) as client:
        for i, group in enumerate(batches):
            entry = state["batches"][i]
            if entry["done"]:
                continue
            text = " ".join(w["text"] for w in group)
            last_error = ""
            for attempt in range(2):
                try:
                    marked = await call_llm(client, text, prompt, settings, config)
                    entry["sentences"] = apply_breaks(group, marked)
                    last_error = ""
                    break
                except ValueError as exc:
                    last_error = str(exc)
                except RuntimeError as exc:
                    raise RuntimeError(f"sentence batch {i + 1}/{len(batches)} failed: {exc}") from exc
            if last_error:
                raise RuntimeError(
                    f"sentence batch {i + 1}/{len(batches)} failed: {last_error}"
                )
            entry["done"] = True
            save_checkpoint(checkpoint, state)
    return [sentence for batch in state["batches"] for sentence in batch["sentences"]]
