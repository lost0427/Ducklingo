import asyncio
import hashlib
import json
import logging
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import httpx

from .asr import build_srt, headers, load_checkpoint, save_checkpoint

log = logging.getLogger("ducklingo.translation")
ROOT = Path(__file__).resolve().parents[1]
PROMPTS = {
    "standard": ROOT / "prompts" / "translation_standard.md",
    "reflect": ROOT / "prompts" / "translation_reflect.md",
}
EFFORTS = {"off", "low", "medium", "high"}
DEFAULT_CONTEXT_WINDOW = 131072
DEFAULT_CONTEXT_RATIO = 0.8
CONTEXT_ITEMS = 3


class TranslationResponseError(RuntimeError):
    def __init__(self, message: str, *, split: bool = False):
        super().__init__(message)
        self.split = split


def context_ratio(value: object) -> float:
    ratio = float(value)
    if not 0.1 <= ratio <= 0.95:
        raise ValueError("translate.max_context_ratio must be between 0.1 and 0.95")
    return ratio


def context_budget(config: dict) -> int:
    window = int(config.get("context_window_tokens", DEFAULT_CONTEXT_WINDOW))
    if window < 1:
        raise ValueError("translate.context_window_tokens must be positive")
    return int(window * context_ratio(config.get("max_context_ratio", DEFAULT_CONTEXT_RATIO)))


translation_budget = context_budget


_encodings: dict[str, object] = {}
_missing_encodings: set[str] = set()


def _encoding(name: str, model: str = ""):
    try:
        import tiktoken
    except ImportError:
        return None
    requested = name or "auto"
    if requested == "auto":
        try:
            requested = tiktoken.encoding_name_for_model(model) if model else "o200k_base"
        except KeyError:
            log.warning("translation tokenizer could not match model %s; using o200k_base", model)
            requested = "o200k_base"
    if requested in _encodings:
        return _encodings[requested]
    cache_dir = os.environ.get("TIKTOKEN_CACHE_DIR") or os.environ.get("DATA_GYM_CACHE_DIR") or os.path.join(tempfile.gettempdir(), "data-gym-cache")
    try:
        cached = os.path.isdir(cache_dir) and any(Path(cache_dir).iterdir())
    except OSError:
        cached = False
    if not cached:
        if requested not in _missing_encodings:
            log.warning("translation tokenizer %s is not cached; using conservative character estimate", requested)
            _missing_encodings.add(requested)
        return None
    try:
        encoding = tiktoken.get_encoding(requested)
    except Exception:
        if requested != "o200k_base":
            log.warning("translation tokenizer %s unavailable; using o200k_base", requested)
            encoding = tiktoken.get_encoding("o200k_base")
        else:
            log.warning("translation tokenizer unavailable; using conservative character estimate")
            return None
    _encodings[requested] = encoding
    return encoding


def token_count(value: str, encoding: str = "auto", model: str = "") -> int:
    encoder = _encoding(encoding, model)
    if encoder is None:
        # ponytail: conservative offline estimate; use cached tiktoken data for exact model counts.
        return max(1, (len(value) + 1) // 2)
    return len(encoder.encode(value))


count_tokens = token_count


def _prompt(mode: str) -> str:
    try:
        return PROMPTS[mode].read_text("utf-8")
    except KeyError as exc:
        raise ValueError(f"unknown translation mode: {mode}") from exc


def system_prompt(mode: str = "standard") -> str:
    return _prompt(mode)


def prompt_hash(mode: str = "standard") -> str:
    return hashlib.sha256(system_prompt(mode).encode("utf-8")).hexdigest()


def _item(value: dict, index: int) -> dict:
    return {"id": str(value.get("id", index + 1)), "source": str(value.get("source", value.get("text", "")))}


def _source_text(value: dict) -> str:
    return str(value.get("source", value.get("text", "")))


def _payload(target_language: str, before: list[dict], items: list[dict], after: list[dict]) -> dict:
    return {
        "target_language": target_language,
        "context_before": before,
        "items": items,
        "context_after": after,
    }


def payload_text(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _request_tokens(prompt: str, payload: dict, encoding: str, model: str = "") -> int:
    # The small fixed allowance covers message role/JSON framing sent by the API client.
    return token_count(prompt, encoding, model) + token_count(payload_text(payload), encoding, model) + 16


def _trim_context(prompt: str, payload: dict, budget: int, encoding: str, model: str = "") -> dict:
    before = list(payload["context_before"])
    after = list(payload["context_after"])
    while _request_tokens(prompt, _payload(payload["target_language"], before, payload["items"], after), encoding, model) > budget and (before or after):
        if after:
            after.pop()
        else:
            before.pop(0)
    return _payload(payload["target_language"], before, payload["items"], after)


def plan_batches(
    sentences: list[dict],
    target_language: str,
    mode: str = "standard",
    config: dict | None = None,
) -> list[dict]:
    config = config or {}
    prompt = system_prompt(mode)
    budget = context_budget(config)
    encoding = str(config.get("tokenizer_encoding", "auto"))
    model = str(config.get("model", ""))
    normalized = [_item(sentence, i) for i, sentence in enumerate(sentences)]
    batches = []
    start = 0
    while start < len(normalized):
        before = normalized[max(0, start - CONTEXT_ITEMS):start]
        selected: list[dict] = []
        end = start
        while end < len(normalized):
            candidate = selected + [normalized[end]]
            after = normalized[end + 1:end + 1 + CONTEXT_ITEMS]
            payload = _trim_context(prompt, _payload(target_language, before, candidate, after), budget, encoding, model)
            if selected and _request_tokens(prompt, payload, encoding, model) > budget:
                break
            selected = candidate
            end += 1
            if _request_tokens(prompt, payload, encoding, model) > budget:
                break
        if not selected:
            selected = [normalized[start]]
            end = start + 1
        payload = _trim_context(
            prompt,
            _payload(target_language, before, selected, normalized[end:end + CONTEXT_ITEMS]),
            budget,
            encoding,
            model,
        )
        batches.append({"start": start, "end": end, "items": selected, "context_before": payload["context_before"], "context_after": payload["context_after"]})
        start = end
    return batches


build_translation_batches = plan_batches


def translation_cache_key(settings: dict, mode: str, target_language: str, reasoning_effort: str, payload: dict, config: dict | None = None) -> str:
    stable = {
        "provider": settings.get("base_url", "").rstrip("/"),
        "model": settings.get("model", ""),
        "prompt": prompt_hash(mode),
        "target_language": target_language,
        "mode": mode,
        "reasoning_effort": reasoning_effort,
        "context": {
            "window": int((config or {}).get("context_window_tokens", DEFAULT_CONTEXT_WINDOW)),
            "ratio": float((config or {}).get("max_context_ratio", DEFAULT_CONTEXT_RATIO)),
            "encoding": str((config or {}).get("tokenizer_encoding", "auto")),
        },
        "payload": payload,
    }
    return hashlib.sha256(json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _validate_mapping(value: object, ids: list[str]) -> dict[str, str]:
    if not isinstance(value, dict):
        raise TranslationResponseError("translation response must be a JSON object", split=True)
    result = {str(key): text for key, text in value.items()}
    if set(result) != set(ids):
        raise TranslationResponseError("translation response subtitle ids do not match the request", split=True)
    if any(not isinstance(text, str) or not text.strip() for text in result.values()):
        raise TranslationResponseError("translation response contains an empty subtitle", split=True)
    return {key: text.strip() for key, text in result.items()}


def validate_translation_response(content: str | dict, ids: list[str]) -> dict[str, str]:
    if isinstance(content, str):
        value = content.strip()
        value = re.sub(r"^```(?:json)?\s*|\s*```$", "", value, flags=re.IGNORECASE)
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise TranslationResponseError("translation response is invalid or truncated JSON", split=True) from exc
    else:
        value = content
    return _validate_mapping(value, ids)


parse_translation_response = validate_translation_response


def _cache_path(cache_dir: Path | None, key: str) -> Path | None:
    if cache_dir is None:
        return None
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{key}.json"


def _load_cached(path: Path | None, ids: list[str]) -> dict[str, str] | None:
    if path is None or not path.exists():
        return None
    try:
        return validate_translation_response(json.loads(path.read_text("utf-8")), ids)
    except (OSError, ValueError, TranslationResponseError):
        return None


def _save_cached(path: Path | None, result: dict[str, str]) -> None:
    if path is None:
        return
    temp = path.with_name(path.name + ".part")
    temp.write_text(json.dumps(result, ensure_ascii=False), "utf-8")
    temp.replace(path)


def _endpoint(settings: dict) -> str:
    base = settings["base_url"].rstrip("/")
    return f"{base}/chat/completions" if base.endswith("/v1") else f"{base}/v1/chat/completions"


async def _request_batch(
    client: httpx.AsyncClient,
    payload: dict,
    settings: dict,
    mode: str,
    reasoning_effort: str,
    config: dict,
    cache_dir: Path | None,
) -> dict[str, str]:
    ids = [str(item["id"]) for item in payload["items"]]
    key = translation_cache_key(settings, mode, payload["target_language"], reasoning_effort, payload, config)
    cached = _load_cached(_cache_path(cache_dir, key), ids)
    if cached is not None:
        return cached
    body = {
        "model": settings["model"],
        "temperature": 0.1,
        "messages": [
            {"role": "system", "content": system_prompt(mode)},
            {"role": "user", "content": payload_text(payload)},
        ],
    }
    if reasoning_effort != "off":
        body["reasoning_effort"] = reasoning_effort
    retries = max(0, int(config.get("batch_retries", 2)))
    delay = float(config.get("retry_delay_seconds", 0))
    for attempt in range(retries + 1):
        try:
            response = await client.post(_endpoint(settings), headers=headers(settings), json=body)
        except httpx.TransportError as exc:
            if attempt == retries:
                raise TranslationResponseError(f"translation request failed: {exc}") from exc
        else:
            if response.status_code < 400:
                try:
                    content = response.json()["choices"][0]["message"]["content"]
                    result = validate_translation_response(content, ids)
                    if token_count(str(content), str(config.get("tokenizer_encoding", "auto")), settings.get("model", "")) > context_budget(config):
                        raise TranslationResponseError("translation response is too long", split=True)
                except TranslationResponseError:
                    if attempt == retries:
                        raise
                except (ValueError, KeyError, IndexError, TypeError) as exc:
                    if attempt == retries:
                        raise TranslationResponseError("translation response has no message content", split=True) from exc
                else:
                    _save_cached(_cache_path(cache_dir, key), result)
                    return result
                if delay:
                    await asyncio.sleep(delay * (attempt + 1))
                continue
            if (response.status_code < 500 and response.status_code != 429) or attempt == retries:
                if response.status_code == 400 and "reasoning" in response.text.lower() and reasoning_effort != "off":
                    raise TranslationResponseError("translation provider does not support reasoning_effort; choose Off")
                raise TranslationResponseError(f"translation request failed: HTTP {response.status_code}: {response.text[:200]}")
        if delay:
            await asyncio.sleep(delay * (attempt + 1))
    raise TranslationResponseError("translation request failed")


async def translate_sentences(
    report,
    sentences: list[dict],
    settings: dict,
    config: dict,
    checkpoint: Path,
    target_language: str = "简体中文",
    mode: str = "standard",
    reasoning_effort: str = "off",
    cache_dir: Path | None = None,
    timeout: float = 300.0,
) -> list[dict]:
    if mode not in PROMPTS:
        raise ValueError("translation mode must be standard or reflect")
    if reasoning_effort not in EFFORTS:
        raise ValueError("reasoning_effort must be off, low, medium, or high")
    if not target_language.strip():
        raise ValueError("target_language is required")
    batches = plan_batches(sentences, target_language, mode, {**config, "model": settings.get("model", "")})
    source_hash = hashlib.sha256(json.dumps(sentences, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    metadata = {
        "source_hash": source_hash,
        "source_subtitle_hash": source_hash,
        "target_language": target_language,
        "mode": mode,
        "reasoning_effort": reasoning_effort,
        "context_window_tokens": int(config.get("context_window_tokens", DEFAULT_CONTEXT_WINDOW)),
        "max_context_ratio": context_ratio(config.get("max_context_ratio", DEFAULT_CONTEXT_RATIO)),
        "tokenizer_encoding": str(config.get("tokenizer_encoding", "auto")),
        "prompt_hash": prompt_hash(mode),
    }
    state = load_checkpoint(checkpoint)
    if not state or any(state.get(key) != value for key, value in metadata.items()) or len(state.get("batches", [])) != len(batches):
        state = {**metadata, "updated_at": datetime.now(timezone.utc).isoformat(), "batches": [{"done": False, "translations": {}, "results": {}} for _ in batches]}
        save_checkpoint(checkpoint, state)
    report("translate")
    translated: dict[str, str] = {}
    item_ids = [str(sentence.get("id", i + 1)) for i, sentence in enumerate(sentences)]
    async with httpx.AsyncClient(timeout=timeout) as client:
        for index, batch in enumerate(batches):
            entry = state["batches"][index]
            if entry.get("done"):
                translated.update(entry.get("translations") or entry.get("results", {}))
                continue
            completed = [
                {"id": item_ids[i], "source": _source_text(sentence), "translation": translated[item_ids[i]]}
                for i, sentence in enumerate(sentences[:batch["start"]])
                if item_ids[i] in translated
            ][-CONTEXT_ITEMS:]
            before = [{"id": item["id"], "source": item["source"], "translation": item.get("translation", "")} for item in completed]
            after = [{"id": item_ids[i], "source": _source_text(sentence)} for i, sentence in enumerate(sentences[batch["end"]:batch["end"] + CONTEXT_ITEMS], batch["end"])]
            payload = _trim_context(system_prompt(mode), _payload(target_language, before, batch["items"], after), context_budget(config), str(config.get("tokenizer_encoding", "auto")), settings.get("model", ""))

            async def send(group: list[dict], group_payload: dict) -> dict[str, str]:
                try:
                    return await _request_batch(client, group_payload, settings, mode, reasoning_effort, config, cache_dir)
                except TranslationResponseError as exc:
                    if len(group) < 2 or not exc.split:
                        raise
                    middle = len(group) // 2
                    left = group[:middle]
                    right = group[middle:]
                    left_payload = {**group_payload, "items": left}
                    right_payload = {**group_payload, "items": right}
                    return {**await send(left, left_payload), **await send(right, right_payload)}

            try:
                result = await send(batch["items"], payload)
            except TranslationResponseError as exc:
                subtitle = batch["items"][0]["id"] if len(batch["items"]) == 1 else "batch"
                raise RuntimeError(f"translation batch {index + 1}/{len(batches)} failed for subtitle {subtitle}: {exc}") from exc
            entry["translations"] = result
            entry["results"] = result
            entry["done"] = True
            state["updated_at"] = datetime.now(timezone.utc).isoformat()
            save_checkpoint(checkpoint, state)
            translated.update(result)
    return [
        {**sentence, "id": item_ids[i], "translation": translated.get(item_ids[i], "")}
        for i, sentence in enumerate(sentences)
    ]


def _srt_field(sentences: list[dict], field: str) -> str:
    return build_srt([{**sentence, "text": sentence[field]} for sentence in sentences])


def create_archive(directory: Path, sentences: list[dict]) -> Path:
    moment = datetime.now()
    stamp = moment.strftime("%Y%m%d_%H%M%S")
    archive = directory / f"{stamp}_subtitles.zip"
    while archive.exists():
        moment += timedelta(seconds=1)
        stamp = moment.strftime("%Y%m%d_%H%M%S")
        archive = directory / f"{stamp}_subtitles.zip"
    source = _srt_field(sentences, "text")
    translation = _srt_field(sentences, "translation")
    source_translation = _srt_field([{**s, "text": f"{s['text']}\n{s['translation']}"} for s in sentences], "text")
    translation_source = _srt_field([{**s, "text": f"{s['translation']}\n{s['text']}"} for s in sentences], "text")
    temp = archive.with_name(archive.name + ".part")
    with ZipFile(temp, "w", ZIP_DEFLATED) as zipped:
        zipped.writestr(f"{stamp}_src.srt", source)
        zipped.writestr(f"{stamp}_trans.srt", translation)
        zipped.writestr(f"{stamp}_src_trans.srt", source_translation)
        zipped.writestr(f"{stamp}_trans_src.srt", translation_source)
    temp.replace(archive)
    return archive
