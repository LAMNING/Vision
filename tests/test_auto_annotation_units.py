"""自动标注的纯函数：去重阈值与提示词来源。

不加载任何模型（ONNX 文件不在仓库里），只测 `_iou` / `_dedupe` / `build_prompts`
这三处「离线可验证」的规则：

- 同一个物体被多个 prompt 各框一次时（实测 IoU 0.81–0.99）只保留分数最高的一条；
- 同一类目内部阈值更低（0.6），因为同类别重复提框就是同一个物件；
- 真正不同的物体（骑马的人）IoU 只有 0.4–0.6，必须都留下；
- 手写的 `en_name` 优先，留空才让翻译补位。
"""

import pytest

from app.services.auto_annotation import (
    IOU_DEDUPE,
    IOU_DEDUPE_SAME,
    AutoConfig,
    CategorySpec,
    _dedupe,
    _iou,
    as_english_phrase,
    build_prompts,
)


def prompt(category_id, zh_name='树', en_name='a tree', threshold=0.3):
    return CategorySpec(category_id=category_id, zh_name=zh_name, en_name=en_name, threshold_override=threshold)


def spec(category_id):
    from app.services.auto_annotation import PromptSpec

    return PromptSpec(
        category_id=category_id,
        zh_name='x',
        en_name='x',
        prompt='x',
        source='en_name',
        threshold=0.3,
        span=None,
    )


def hit(category_id, box, score):
    return (spec(category_id), {'box': list(box), 'score': score, 'label': 'x'})


class FakeTranslator:
    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    def translate_many(self, texts):
        self.calls.append(list(texts))
        return [self.mapping.get(text, text) for text in texts]


def test_iou_known_values():
    assert _iou([0, 0, 10, 10], [0, 0, 10, 10]) == pytest.approx(1.0)
    assert _iou([0, 0, 10, 10], [10, 10, 20, 20]) == 0.0
    assert _iou([0, 0, 10, 10], [5, 0, 15, 10]) == pytest.approx(1 / 3)


def test_iou_tolerates_malformed_boxes():
    assert _iou(None, [0, 0, 1, 1]) == 0.0
    assert _iou([0, 0, 1], [0, 0, 1, 1]) == 0.0
    assert _iou(['a', 'b', 'c', 'd'], [0, 0, 1, 1]) == 0.0


def test_dedupe_merges_cross_category_duplicates():
    """IoU 0.9 的两个人/猫/狗框只留分数最高的那个类目。"""
    kept = _dedupe(
        [
            hit(1, (10, 10, 110, 210), 0.55),
            hit(2, (10, 12, 112, 208), 0.40),
            hit(3, (12, 10, 108, 212), 0.36),
        ]
    )
    assert len(kept) == 1
    assert kept[0][0].category_id == 1
    assert kept[0][1]['score'] == 0.55


def test_dedupe_keeps_genuinely_overlapping_objects():
    """骑马的人：IoU 约 0.43，低于跨类目阈值，两个都要留。"""
    person = hit(1, (0, 0, 100, 200), 0.6)
    horse = hit(2, (40, 0, 260, 200), 0.5)
    assert _iou(person[1]['box'], horse[1]['box']) < IOU_DEDUPE
    assert len(_dedupe([person, horse])) == 2


def test_same_category_threshold_is_lower():
    """同类目两个框 IoU 0.65：跨类目放行、同类目合并。"""
    a = (0, 0, 100, 100)
    b = (18, 0, 118, 100)
    overlap = _iou(a, b)
    assert IOU_DEDUPE_SAME <= overlap < IOU_DEDUPE
    assert len(_dedupe([hit(7, a, 0.9), hit(7, b, 0.8)])) == 1
    assert len(_dedupe([hit(7, a, 0.9), hit(8, b, 0.8)])) == 2


def test_dedupe_lets_malformed_boxes_through():
    kept = _dedupe([hit(1, (0, 0, 10, 10), 0.9), (spec(2), {'box': None, 'score': 0.4})])
    assert len(kept) == 2


def test_dedupe_sorts_are_respected_by_map_detections_order():
    """输入按分数降序，输出保留第一条（最高分）——保证留下的类目可预测。"""
    kept = _dedupe([hit(1, (0, 0, 50, 50), 0.9), hit(2, (1, 1, 51, 51), 0.8)])
    assert [item[1]['score'] for item in kept] == [0.9]


def test_written_en_name_wins():
    translator = FakeTranslator({'猫': 'a cat'})
    prompts = build_prompts(
        [prompt(1, '人', 'a person'), prompt(2, '猫', '')],
        AutoConfig(box_threshold=0.3, translate_enabled=True),
        translator,
    )
    by_id = {item.category_id: item for item in prompts}
    assert by_id[1].prompt == 'a person'
    assert by_id[1].source == 'en_name'
    assert by_id[2].prompt == 'a cat'
    assert by_id[2].source == 'translated'
    # 一次批量翻译全部中文名（模型是批处理的），但只有留空的那条用结果
    assert translator.calls == [['人', '猫']]


def test_all_written_needs_no_translator():
    prompts = build_prompts(
        [prompt(1, '树', 'a tree'), prompt(2, '花', 'a flower')],
        AutoConfig(box_threshold=0.3, translate_enabled=True),
        None,
    )
    assert [item.prompt for item in prompts] == ['a tree', 'a flower']


def test_blank_en_name_without_translator_raises():
    with pytest.raises(ValueError, match='需要翻译英文提示词，但未提供 translator'):
        build_prompts([prompt(1, '树', '')], AutoConfig(), None)


def test_translation_runs_even_when_switch_off_if_en_name_blank():
    """`translate_enabled` 只影响「要不要顺带翻译」，留空的英文提示词必须补上。"""
    translator = FakeTranslator({'树': 'a tree'})
    prompts = build_prompts(
        [prompt(1, '树', '')], AutoConfig(translate_enabled=False), translator
    )
    assert prompts[0].prompt == 'a tree'


def test_category_threshold_override_wins():
    prompts = build_prompts(
        [
            CategorySpec(category_id=1, zh_name='树', en_name='a tree', threshold_override=0.55),
            CategorySpec(category_id=2, zh_name='花', en_name='a flower', threshold_override=None),
        ],
        AutoConfig(box_threshold=0.3),
    )
    by_id = {item.category_id: item.threshold for item in prompts}
    assert by_id[1] == 0.55
    assert by_id[2] == 0.3


def test_disabled_categories_are_skipped():
    prompts = build_prompts(
        [
            CategorySpec(category_id=1, zh_name='树', en_name='a tree', enabled=True),
            CategorySpec(category_id=2, zh_name='花', en_name='a flower', enabled=False),
        ],
        AutoConfig(),
    )
    assert [item.category_id for item in prompts] == [1]


def test_translate_style_must_be_known():
    with pytest.raises(ValueError, match='translate_style'):
        build_prompts([prompt(1)], AutoConfig(translate_style='wat'))


def test_as_english_phrase():
    assert as_english_phrase('Cat') == 'a cat'
    assert as_english_phrase('apple') == 'an apple'
    assert as_english_phrase('a remote control') == 'a remote control'
    assert as_english_phrase('  The signboard. ') == 'the signboard'
    assert as_english_phrase('') == ''
