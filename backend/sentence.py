import asyncio
import hashlib
import json
import re
from datetime import datetime, timezone
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


class WordSequenceMismatch(ValueError):
    def __init__(self, marked: str, index: int, expected: str | None, actual: str | None):
        super().__init__("word sequence mismatch")
        self.marked = marked
        self.index = index
        self.expected = expected
        self.actual = actual


def _marked_tokens(marked: str) -> tuple[list[str], set[int]]:
    tokens, breaks = [], set()
    for token in marked.replace("<br>", " <br> ").split():
        if token == "<br>":
            if tokens:
                breaks.add(len(tokens))
        else:
            tokens.append(token)
    return tokens, breaks


def break_groups(words: list[dict], marked: str) -> list[list[dict]]:
    groups, current = [], []
    tokens, breaks = _marked_tokens(marked)
    for index, token in enumerate(tokens):
        if index >= len(words) or token != words[index]["text"]:
            expected = words[index]["text"] if index < len(words) else None
            raise WordSequenceMismatch(marked, index, expected, token)
        current.append(words[index])
        if index + 1 in breaks:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    if len(tokens) != len(words) or not groups:
        expected = words[len(tokens)]["text"] if len(tokens) < len(words) else None
        actual = tokens[len(words)] if len(tokens) > len(words) else None
        raise WordSequenceMismatch(marked, min(len(tokens), len(words)), expected, actual)
    return groups


def _repair_region(words: list[dict], marked: str) -> tuple[int, int, int, int]:
    tokens, _ = _marked_tokens(marked)
    prefix = 0
    while prefix < min(len(words), len(tokens)) and words[prefix]["text"] == tokens[prefix]:
        prefix += 1
    suffix = 0
    while (
        suffix < min(len(words) - prefix, len(tokens) - prefix)
        and words[len(words) - suffix - 1]["text"] == tokens[len(tokens) - suffix - 1]
    ):
        suffix += 1

    # A little matching context gives the model enough text to make a useful cut.
    context = 12
    start = max(0, prefix - context)
    end = min(len(words), len(words) - suffix + context)
    token_start = max(0, prefix - context)
    token_end = min(len(tokens), len(tokens) - suffix + context)
    if start == end:
        start = max(0, start - 1)
        end = min(len(words), end + 1)
    return start, end, token_start, token_end


def _marked_with_breaks(words: list[dict], breaks: set[int]) -> str:
    parts = []
    for index, word in enumerate(words):
        if index:
            parts.append("<br>" if index in breaks else " ")
        parts.append(word["text"])
    return " ".join(parts)


def _record_word_mismatch(
    checkpoint: Path,
    batch_index: int,
    batch_count: int,
    feedback: str,
    exc: WordSequenceMismatch,
) -> None:
    path = checkpoint.with_name(f"{checkpoint.stem}.mismatches.json")
    try:
        entries = json.loads(path.read_text("utf-8")) if path.exists() else []
        entries.append({
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "batch": batch_index + 1,
            "batch_count": batch_count,
            "feedback_retry": bool(feedback),
            "word_index": exc.index,
            "expected_word": exc.expected,
            "actual_word": exc.actual,
            "llm_text": exc.marked,
        })
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(entries[-20:], ensure_ascii=False, indent=2), "utf-8")
        temp.replace(path)
        log.info("sentence mismatch details saved file=%s batch=%s", path.name, batch_index + 1)
    except (OSError, ValueError, TypeError) as save_error:
        log.warning("could not save sentence mismatch details: %s", summarize_error(save_error))


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


async def _mark_groups(
    client,
    words: list[dict],
    prompt: str,
    settings: dict,
    config: dict,
    feedback: str = "",
    on_mismatch=None,
) -> list[list[dict]]:
    text = " ".join(word["text"] for word in words)
    marked = await call_llm(client, text, prompt, settings, config, feedback)
    try:
        return break_groups(words, marked)
    except WordSequenceMismatch as exc:
        if on_mismatch:
            on_mismatch(exc, feedback)
        start, end, token_start, token_end = _repair_region(words, marked)
        repair_words = words[start:end]
        if not repair_words:
            raise
        repair_feedback = (
            "The previous output changed or omitted words. Re-segment only this exact excerpt. "
            "Keep every word unchanged and in order; only insert <br>. Output the excerpt text only."
        )
        repair_text = " ".join(word["text"] for word in repair_words)
        repair_marked = await call_llm(
            client, repair_text, prompt, settings, config, repair_feedback
        )
        try:
            break_groups(repair_words, repair_marked)
        except WordSequenceMismatch as repair_exc:
            if on_mismatch:
                on_mismatch(repair_exc, repair_feedback)
            raise

        _, original_breaks = _marked_tokens(marked)
        _, repair_breaks = _marked_tokens(repair_marked)
        offset = len(words) - len(_marked_tokens(marked)[0])
        breaks = {position for position in original_breaks if position <= token_start}
        breaks.update(position + offset for position in original_breaks if position >= token_end)
        breaks.update(start + position for position in repair_breaks)
        repaired_marked = _marked_with_breaks(words, breaks)
        repaired_groups = break_groups(words, repaired_marked)
        log.info("sentence mismatch repaired locally words=%s:%s of %s", start, end, len(words))
        return repaired_groups


async def _enforce_lengths(
    client,
    groups: list[list[dict]],
    prompt: str,
    settings: dict,
    config: dict,
    on_mismatch=None,
) -> list[list[dict]]:
    result = []
    for group in groups:
        exceeded = _limit_exceeded(group, config) if len(group) >= 2 else None
        if exceeded is None:
            result.append(group)
            continue
        length, limit = exceeded
        feedback = f"This sentence is too long: {length} characters, limit {limit}. Insert <br> to split it into shorter sentences. Keep every word unchanged and only add <br>."
        sub = await _mark_groups(client, group, prompt, settings, config, feedback, on_mismatch)
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

            def record_mismatch(exc: WordSequenceMismatch, feedback: str) -> None:
                _record_word_mismatch(checkpoint, i, len(batches), feedback, exc)

            for feedback in ("", _word_feedback()):
                try:
                    groups = await _mark_groups(client, group, prompt, settings, config, feedback, record_mismatch)
                    break
                except ValueError as exc:
                    last_error = str(exc)
                except RuntimeError as exc:
                    raise RuntimeError(f"sentence batch {i + 1}/{len(batches)} failed: {exc}") from exc
            if groups is None:
                raise RuntimeError(f"sentence batch {i + 1}/{len(batches)} failed: {last_error}")
            try:
                entry["sentences"] = validate_lengths(
                    [render(g) for g in await _enforce_lengths(client, groups, prompt, settings, config, record_mismatch)], config
                )
            except (ValueError, RuntimeError) as exc:
                raise RuntimeError(f"sentence batch {i + 1}/{len(batches)} failed: {exc}") from exc
            entry["done"] = True
            save_checkpoint(checkpoint, state)
    return [sentence for batch in state["batches"] for sentence in batch["sentences"]]
