import asyncio
import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

import httpx

from .asr import headers, load_checkpoint, save_checkpoint
from .logging_config import get_logger, summarize_error

PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "sentence_split.md"

CJK_RUN = re.compile(r"(?<=[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]) (?=[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af])")
CJK_CHAR = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")
log = get_logger("sentence")


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


def break_groups(words: list[dict], marked: str) -> list[list[dict]]:
    groups, current = [], []
    index = 0
    for token in marked.replace("<br>", " <br> ").split():
        if token == "<br>":
            if current:
                groups.append(current)
                current = []
            continue
        if index >= len(words) or token != words[index]["text"]:
            raise ValueError("word sequence mismatch")
        current.append(words[index])
        index += 1
    if current:
        groups.append(current)
    if index != len(words) or not groups:
        raise ValueError("word sequence mismatch")
    return groups


def render(group: list[dict]) -> dict:
    return {"text": join_text(group), "start": group[0]["start"], "end": group[-1]["end"]}


def apply_breaks(words: list[dict], marked: str) -> list[dict]:
    return [render(group) for group in break_groups(words, marked)]


def _length_limit(text: str, config: dict) -> tuple[int, int]:
    length = len(re.sub(r"\s+", "", text))
    limit = int(config.get("max_chars_cjk", 21)) if CJK_CHAR.search(text) else int(config.get("max_chars_latin", 42))
    return length, limit


def validate_lengths(sentences: list[dict], config: dict) -> list[dict]:
    for sentence in sentences:
        length, limit = _length_limit(sentence["text"], config)
        if length > limit:
            raise ValueError(f"sentence too long: {length}>{limit}: {sentence['text'][:80]}")
    return sentences


def _limit_exceeded(group: list[dict], config: dict) -> tuple[int, int] | None:
    length, limit = _length_limit(join_text(group), config)
    return (length, limit) if length > limit else None


def _word_feedback() -> str:
    return "Previous output changed the words. Only insert <br> between the given words; never add, remove, modify, or reorder words, and output the text only."


def endpoint(settings: dict) -> str:
    base = settings["base_url"].rstrip("/")
    return f"{base}/chat/completions" if base.endswith("/v1") else f"{base}/v1/chat/completions"


async def call_llm(client: httpx.AsyncClient, text: str, prompt: str, settings: dict, config: dict, feedback: str = "") -> str:
    retries = int(config.get("retries", 2))
    content = f"<text>{text}</text>"
    if feedback:
        content += f"\n<feedback>{feedback}</feedback>"
    body = {
        "model": settings["model"],
        "temperature": 0.1,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": content},
        ],
    }
    response_format = config.get("response_format")
    if response_format:
        body["response_format"] = {"type": response_format}
    for attempt in range(retries + 1):
        try:
            response = await client.post(endpoint(settings), headers=headers(settings), json=body)
        except httpx.TransportError as exc:
            log.warning("LLM sentence call transport error attempt=%s error=%s", attempt + 1, summarize_error(exc))
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
        await asyncio.sleep(2 * (attempt + 1))
    raise RuntimeError("unreachable")


async def _mark_groups(client, words: list[dict], prompt: str, settings: dict, config: dict, feedback: str = "") -> list[list[dict]]:
    text = " ".join(word["text"] for word in words)
    return break_groups(words, await call_llm(client, text, prompt, settings, config, feedback))


async def _enforce_lengths(client, groups: list[list[dict]], prompt: str, settings: dict, config: dict) -> list[list[dict]]:
    result = []
    for group in groups:
        exceeded = _limit_exceeded(group, config) if len(group) >= 2 else None
        if exceeded is None:
            result.append(group)
            continue
        length, limit = exceeded
        feedback = f"This sentence is too long: {length} characters, limit {limit}. Insert <br> to split it into shorter sentences. Keep every word unchanged and only add <br>."
        sub = await _mark_groups(client, group, prompt, settings, config, feedback)
        if any(_limit_exceeded(part, config) for part in sub):
            raise ValueError(f"sentence too long: {length}>{limit}: {join_text(group)[:80]}")
        result.extend(sub)
    return result


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
    source_hash = hashlib.sha256(json.dumps(words, sort_keys=True).encode()).hexdigest()
    state = load_checkpoint(checkpoint)
    if not state or state.get("source_hash") != source_hash or len(state.get("batches", [])) != len(batches):
        state = {"source_hash": source_hash, "word_count": len(words), "batches": [{"done": False, "sentences": []} for _ in batches]}
        save_checkpoint(checkpoint, state)
    prompt = system_prompt(config)
    report("sentence")
    async with httpx.AsyncClient(timeout=timeout) as client:
        for i, group in enumerate(batches):
            entry = state["batches"][i]
            if entry["done"]:
                continue
            last_error = ""
            groups = None
            for feedback in ("", _word_feedback()):
                try:
                    groups = await _mark_groups(client, group, prompt, settings, config, feedback)
                    break
                except ValueError as exc:
                    last_error = str(exc)
                except RuntimeError as exc:
                    raise RuntimeError(f"sentence batch {i + 1}/{len(batches)} failed: {exc}") from exc
            if groups is None:
                raise RuntimeError(f"sentence batch {i + 1}/{len(batches)} failed: {last_error}")
            try:
                entry["sentences"] = validate_lengths(
                    [render(g) for g in await _enforce_lengths(client, groups, prompt, settings, config)], config
                )
            except (ValueError, RuntimeError) as exc:
                raise RuntimeError(f"sentence batch {i + 1}/{len(batches)} failed: {exc}") from exc
            entry["done"] = True
            save_checkpoint(checkpoint, state)
    return [sentence for batch in state["batches"] for sentence in batch["sentences"]]
