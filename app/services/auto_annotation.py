"""Auto-annotation: project categories -> English prompts -> bbox annotations.

Pure logic on top of the vendored LLMDet runtime -- no DB access here. The task
worker (``app/services/auto_annotate.py``, M3) loads categories/config with crud
and calls :func:`annotate_image`.

Why token spans instead of label strings: the detector merges every prompt into
one sentence (``"a cat. a remote control."``) and a query may decode to a mixed
label (e.g. ``"a people a"``). Each detection therefore carries the column
indices of its positive text tokens (``token_indices``); a detection belongs to
the category whose prompt span overlaps them the most.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable, Protocol, Sequence

from app.services.model.detector import merge_candidate_labels

DETERMINERS = ('a', 'an', 'the', 'some', 'this', 'that', 'these', 'those')
TRANSLATE_STYLES = ('phrase', 'raw')

# 同一物体被多个 prompt 各框一次时，这些框几乎完全重合（实测 0.81–0.99）；
# 真正互相重叠的不同物体（人骑马之类）通常只有 0.4–0.6。取 0.8 只砍重复、不砍真重叠。
IOU_DEDUPE = 0.8
# 同一个类目内部更激进：同类两个框重叠到 0.6 基本就是同一个物件被提了两次，
# 而不同类目之间可能真的是两个物体叠在一起，所以门槛继续留在 0.8。
IOU_DEDUPE_SAME = 0.6


class TranslatorLike(Protocol):
    def translate_many(self, texts: Sequence[str]) -> list[str]: ...


class DetectorLike(Protocol):
    tokenizer: Any

    def predict(
        self,
        image,
        labels,
        threshold: float = 0.3,
        text_threshold: float = 0.25,
        top_k: int | None = None,
    ) -> list[dict]: ...


@dataclass(frozen=True)
class AutoConfig:
    """Slice of ``project_auto_config`` the annotation pass needs."""

    box_threshold: float = 0.3
    text_threshold: float = 0.25
    top_k: int | None = None
    translate_enabled: bool = True
    translate_style: str = 'phrase'


@dataclass(frozen=True)
class CategorySpec:
    category_id: int
    zh_name: str
    en_name: str = ''
    threshold_override: float | None = None
    enabled: bool = True


@dataclass(frozen=True)
class PromptSpec:
    category_id: int
    zh_name: str
    en_name: str
    prompt: str
    source: str  # 'translated' | 'en_name' | 'zh_fallback'
    threshold: float
    span: tuple[int, int] | None = None


# --------------------------------------------------------------------------- #
# prompt building
# --------------------------------------------------------------------------- #
def as_english_phrase(text: str) -> str:
    """'Cat'/'Remote control' -> 'a cat'/'a remote control' (GroundingDINO likes phrases)."""
    phrase = text.strip().rstrip('.').strip().lower()
    if not phrase:
        return ''
    if phrase.split()[0] in DETERMINERS:
        return phrase
    return ('an ' if phrase[0] in 'aeiou' else 'a ') + phrase


def _normalize_translation(text: str, style: str) -> str:
    value = ' '.join((text or '').strip().rstrip('.').split()).lower()
    if not value:
        return ''
    return value if style == 'raw' else as_english_phrase(value)


def build_prompts(
    categories: Iterable[CategorySpec],
    config: AutoConfig,
    translator: TranslatorLike | None = None,
) -> list[PromptSpec]:
    """One prompt per enabled category: 手写的 en_name 优先，留空才让翻译补位。"""
    if config.translate_style not in TRANSLATE_STYLES:
        raise ValueError(
            f'translate_style must be one of {TRANSLATE_STYLES}, got {config.translate_style!r}'
        )
    enabled = [category for category in categories if category.enabled]
    if not enabled:
        return []

    # 只有「英文提示词为空」的类目才需要翻译。en_name 一旦填了就是用户选定的检测口径，
    # 翻译结果不能再盖掉它（从前 translate_enabled 一开就会盖掉，手填的英文永远不生效）。
    needs_translation = any(not category.en_name.strip() for category in enabled)

    translations: list[str] | None = None
    if needs_translation:
        if translator is None:
            raise ValueError('需要翻译英文提示词，但未提供 translator')
        translations = translator.translate_many(
            [category.zh_name for category in enabled]
        )
        if len(translations) != len(enabled):
            raise ValueError(
                f'translator returned {len(translations)} items for {len(enabled)} categories'
            )

    prompts: list[PromptSpec] = []
    for index, category in enumerate(enabled):
        written = ' '.join(category.en_name.strip().split()).lower()
        if written:
            prompt = written
            source = 'en_name'
        else:
            prompt = _normalize_translation(translations[index], config.translate_style)  # type: ignore[index]
            source = 'translated'
        if not prompt:
            continue
        threshold = (
            category.threshold_override
            if category.threshold_override is not None
            else config.box_threshold
        )
        prompts.append(
            PromptSpec(
                category_id=category.category_id,
                zh_name=category.zh_name,
                en_name=category.en_name,
                prompt=prompt,
                source=source,
                threshold=float(threshold),
            )
        )
    return prompts


# --------------------------------------------------------------------------- #
# token spans
# --------------------------------------------------------------------------- #
def attach_spans(
    prompts: Sequence[PromptSpec], tokenizer
) -> tuple[list[PromptSpec], str, list[int]]:
    """Tokenize the merged prompt sentence and record each prompt's column span."""
    merged = merge_candidate_labels([prompt.prompt for prompt in prompts])
    ids, _, _ = tokenizer.encode(merged)

    spans: list[tuple[int, int]] = []
    cursor = 1  # skip [CLS]
    for prompt in prompts:
        pieces = tokenizer.tokenize(prompt.prompt)
        if not pieces:
            raise ValueError(f'prompt tokenized to nothing: {prompt.prompt!r}')
        spans.append((cursor, cursor + len(pieces)))
        cursor += len(pieces) + 1  # prompt + the '.' separator
    expected = cursor + 1  # + [SEP]
    if expected != len(ids):
        raise ValueError(
            f'prompt span mismatch: expected {expected} ids, tokenizer returned {len(ids)} '
            f'(text={merged!r})'
        )
    return (
        [replace(prompt, span=span) for prompt, span in zip(prompts, spans)],
        merged,
        ids,
    )


def _iou(box_a: Sequence[float], box_b: Sequence[float]) -> float:
    """IoU of two [x0, y0, x1, y1] boxes (0.0 when either is malformed)."""
    try:
        ax0, ay0, ax1, ay1 = (float(value) for value in box_a)
        bx0, by0, bx1, by1 = (float(value) for value in box_b)
    except (TypeError, ValueError):
        return 0.0
    inner = max(0.0, min(ax1, bx1) - max(ax0, bx0)) * max(0.0, min(ay1, by1) - max(ay0, by0))
    if inner <= 0:
        return 0.0
    area_a = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    area_b = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    union = area_a + area_b - inner
    return inner / union if union > 0 else 0.0


def _dedupe(hits: Sequence[tuple[PromptSpec, dict]]) -> list[tuple[PromptSpec, dict]]:
    """Keep one box per physical object.

    All prompts share a single merged sentence, so the detector may emit the same
    region several times — once per prompt — and span mapping then hands each copy
    to a different category (observed IoU 0.81–0.99 between those copies). Genuinely
    different objects that overlap (a person riding a horse) sit far lower, around
    0.4–0.6, so a high threshold removes the duplicates without merging real ones.
    Same-category boxes are held to a lower bar: two boxes of one class overlapping
    that much are the same object proposed twice.
    """
    kept: list[tuple[PromptSpec, dict]] = []
    for category, result in hits:
        box = result.get('box')
        if not box or len(box) != 4:
            kept.append((category, result))
            continue
        duplicate = False
        for other_category, other in kept:
            same = other_category.category_id == category.category_id
            limit = IOU_DEDUPE_SAME if same else IOU_DEDUPE
            if _iou(box, other.get('box') or ()) >= limit:
                duplicate = True
                break
        if duplicate:
            continue
        kept.append((category, result))
    return kept


def _map_detections(results: Sequence[dict], prompts: Sequence[PromptSpec]) -> list[tuple[PromptSpec, dict]]:
    hits: list[tuple[PromptSpec, dict]] = []
    for result in results:
        indices = result.get('token_indices')
        category: PromptSpec | None = None
        if indices:
            best_count = 0
            for prompt in prompts:
                if prompt.span is None:
                    continue
                count = sum(1 for index in indices if prompt.span[0] <= index < prompt.span[1])
                if count > best_count:
                    category = prompt
                    best_count = count
        if category is None:  # no token indices (or no overlap): match the decoded label
            label = ' '.join(str(result.get('label', '')).lower().split())
            category = next((prompt for prompt in prompts if prompt.prompt == label), None)
        if category is None:
            continue
        score = float(result.get('score') or 0.0)
        if score < category.threshold:
            continue
        hits.append((category, result))
    hits.sort(key=lambda item: float(item[1].get('score') or 0.0), reverse=True)
    return _dedupe(hits)


def to_annotation_item(category: PromptSpec, result: dict) -> dict:
    """Shape accepted by ``app.data.crud.annotation.add_current`` (AnnotationItem)."""
    box = [round(float(value), 2) for value in (result.get('box') or [0, 0, 0, 0])]
    score = round(float(result.get('score') or 0.0), 4)
    return {
        'label': category.zh_name[:50],
        'annotation_type': 'bbox',
        'payload': {
            'box': box,
            'score': score,
            'prompt': category.prompt,
            'detected_label': str(result.get('label') or ''),
            'detector': 'llmdet',
        },
        'confidence': score,
        'category_id': category.category_id,
    }


def annotate_image(
    detector: DetectorLike,
    image,
    categories: Iterable[CategorySpec],
    config: AutoConfig,
    translator: TranslatorLike | None = None,
) -> list[dict]:
    """Run one image through LLMDet and return annotation items (one per box)."""
    prompts = build_prompts(categories, config, translator)
    if not prompts:
        return []
    prompts, _merged, _ids = attach_spans(prompts, detector.tokenizer)
    results = detector.predict(
        image,
        [prompt.prompt for prompt in prompts],
        threshold=min(prompt.threshold for prompt in prompts),
        text_threshold=config.text_threshold,
        top_k=config.top_k,
    )
    return [to_annotation_item(category, result) for category, result in _map_detections(results, prompts)]


# --------------------------------------------------------------------------- #
# ORM adapters (used by the task worker in M3 / verification scripts)
# --------------------------------------------------------------------------- #
def category_specs(categories: Iterable[Any]) -> list[CategorySpec]:
    return [
        CategorySpec(
            category_id=row.id,
            zh_name=row.zh_name,
            en_name=row.en_name or '',
            threshold_override=row.threshold_override,
            enabled=bool(row.enabled),
        )
        for row in categories
    ]


def auto_config(row: Any) -> AutoConfig:
    return AutoConfig(
        box_threshold=float(row.box_threshold),
        text_threshold=float(row.text_threshold),
        top_k=row.top_k,
        translate_enabled=bool(row.translate_enabled),
        translate_style=row.translate_style or 'phrase',
    )
