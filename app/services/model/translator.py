"""Pure-Python Opus-MT (Marian) translator for the LLMDet runtime.

No PyTorch, no `sentencepiece`, no `tokenizers`: the SentencePiece unigram
models are parsed straight out of the `.spm` protobuf files and the encoder /
decoder run in onnxruntime.

The Marian checkpoints are multilingual translation models; LLMDet's text
encoder is English-only, so translating labels before detection is what makes
non-English prompts work.

Usage
-----
>>> from runtime import MarianTranslator
>>> tr = MarianTranslator()                      # defaults to models/opus-mt-zh-en
>>> tr.translate("遥控器")
'Remote control'
>>> LLMDet translator precision: MarianTranslator(precision="fp16")

Model files required (all shipped by the Xenova/onnx-community ONNX repos):
    onnx/encoder_model.onnx    onnx/decoder_model.onnx
    source.spm                 vocab.json

`source.spm` is the SentencePiece unigram model of the *source* language; the
target side is decoded from the shared 65001-entry Marian vocabulary (`vocab.json`),
so `target.spm` is not needed (it cannot encode non-target text anyway).
"""
from __future__ import annotations

import json
import math
import os
import struct
import unicodedata

import numpy as np
import onnxruntime as ort

EOS = 0
PAD = 65000
DECODER_START = 65000

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_MODEL_DIR = os.path.join(ROOT, "models", "opus-mt-zh-en")

SPACE = "\u2581"  # SentencePiece metaspace marker


# --------------------------------------------------------------------------- #
# SentencePiece unigram (model protobuf parsing + Viterbi)
# --------------------------------------------------------------------------- #
def _read_varint(buf: bytes, i: int):
    """Read a protobuf varint; returns (value, new_offset)."""
    shift = 0
    value = 0
    while True:
        byte = buf[i]
        i += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, i
        shift += 7


def _fields(buf: bytes):
    """Yield (field_number, value) for every protobuf field in `buf`."""
    i = 0
    n = len(buf)
    while i < n:
        key, i = _read_varint(buf, i)
        number, wire = key >> 3, key & 7
        if wire == 0:  # varint
            value, i = _read_varint(buf, i)
        elif wire == 1:  # 64-bit
            value, i = buf[i:i + 8], i + 8
        elif wire == 2:  # length-delimited
            length, i = _read_varint(buf, i)
            value, i = buf[i:i + length], i + length
        elif wire == 5:  # 32-bit
            value, i = buf[i:i + 4], i + 4
        else:  # pragma: no cover - unsupported wire type
            raise ValueError(f"unsupported protobuf wire type {wire}")
        yield number, value


class SentencePieceUnigram:
    """Minimal SentencePiece unigram encoder/decoder read from a `.spm` file."""

    NORMAL, UNKNOWN, CONTROL, USER_DEFINED, UNUSED, BYTE = range(1, 7)

    def __init__(self, model_path: str):
        self.pieces: list[str] = []
        self.scores: list[float] = []
        self.types: list[int] = []
        self.add_dummy_prefix = True
        self.remove_extra_whitespaces = True
        self.escape_whitespaces = True
        self._load(os.path.abspath(model_path))
        self._score = {p: s for p, s, t in zip(self.pieces, self.scores, self.types)
                       if t in (self.NORMAL, self.USER_DEFINED)}
        self.unk_id = next((i for i, t in enumerate(self.types) if t == self.UNKNOWN), 0)
        self._unk_score = min(self._score.values(), default=-10.0) - 10.0
        self._max_piece = max((len(p) for p in self._score), default=1)

    # -- protobuf ---------------------------------------------------------- #
    def _load(self, path: str) -> None:
        with open(path, "rb") as handle:
            data = handle.read()
        for number, value in _fields(data):
            if number == 1 and isinstance(value, bytes):
                self._add_piece(value)
            elif number == 3 and isinstance(value, bytes):
                self._read_normalizer(value)

    def _add_piece(self, blob: bytes) -> None:
        piece = ""
        score = 0.0
        ptype = self.NORMAL
        for number, value in _fields(blob):
            if number == 1 and isinstance(value, bytes):
                piece = value.decode("utf-8")
            elif number == 2 and isinstance(value, bytes):
                score = struct.unpack("<f", value)[0]
            elif number == 3 and isinstance(value, int):
                ptype = value
        self.pieces.append(piece)
        self.scores.append(score)
        self.types.append(ptype)

    def _read_normalizer(self, blob: bytes) -> None:
        for number, value in _fields(blob):
            if number == 3 and isinstance(value, int):
                self.add_dummy_prefix = bool(value)
            elif number == 4 and isinstance(value, int):
                self.remove_extra_whitespaces = bool(value)
            elif number == 5 and isinstance(value, int):
                self.escape_whitespaces = bool(value)

    # -- encoding ---------------------------------------------------------- #
    def _normalize(self, text: str) -> str:
        text = unicodedata.normalize("NFKC", text)
        if self.remove_extra_whitespaces:
            text = " ".join(text.split())
            text = text.strip()
        if self.escape_whitespaces:
            text = text.replace(" ", SPACE)
        if self.add_dummy_prefix and not text.startswith(SPACE):
            text = SPACE + text
        return text

    def _viterbi(self, text: str) -> list[str]:
        n = len(text)
        best = [-math.inf] * (n + 1)
        back: list[tuple[int, str] | None] = [None] * (n + 1)
        best[0] = 0.0
        for start in range(n):
            if best[start] == -math.inf:
                continue
            limit = min(n, start + self._max_piece)
            for end in range(start + 1, limit + 1):
                piece = text[start:end]
                score = self._score.get(piece)
                if score is None:
                    continue
                candidate = best[start] + score
                if candidate > best[end]:
                    best[end] = candidate
                    back[end] = (start, piece)
        if best[n] == -math.inf:  # fall back to one piece per character
            return [self.pieces[self.unk_id]] * n
        pieces: list[str] = []
        cursor = n
        while cursor > 0:
            start, piece = back[cursor]
            pieces.append(piece)
            cursor = start
        pieces.reverse()
        return pieces

    def encode(self, text: str) -> list[str]:
        """Return SentencePiece strings (``▁`` marks a word boundary)."""
        normalized = self._normalize(text)
        if not normalized:
            return []
        return self._viterbi(normalized)

    def decode(self, pieces: list[str]) -> str:
        return "".join(pieces).replace(SPACE, " ").strip()


# --------------------------------------------------------------------------- #
# Marian translator
# --------------------------------------------------------------------------- #
class MarianTranslator:
    """Greedy-decoding Marian (Opus-MT) translator in pure onnxruntime."""

    def __init__(self, model_dir: str | None = None, providers=None, precision: str = "fp32",
                 max_new_tokens: int = 32):
        self.model_dir = os.path.abspath(model_dir or DEFAULT_MODEL_DIR)
        self.max_new_tokens = max_new_tokens
        suffix = "" if precision == "fp32" else f"_{precision}"
        enc_path = os.path.join(self.model_dir, "onnx", f"encoder_model{suffix}.onnx")
        dec_path = os.path.join(self.model_dir, "onnx", f"decoder_model{suffix}.onnx")
        if not os.path.isdir(os.path.join(self.model_dir, "onnx")):
            raise FileNotFoundError(
                f"翻译模型目录不存在：{self.model_dir}\n"
                "把 IMAGELABEL_MARIAN_DIR（或用 /admin/model-config）指向 Marian/OPUS-MT 模型根目录"
                "（该目录下应有 onnx/、vocab.json、source.spm）；不需要翻译时留空，"
                "类目直接手填英文提示词即可。"
            )
        if not os.path.isfile(dec_path):  # fp16/int8 name their files differently
            candidates = sorted(
                p for p in os.listdir(os.path.join(self.model_dir, "onnx"))
                if p.startswith("decoder_model") and p.endswith(".onnx")
                and "merged" not in p and precision in p
            )
            if candidates:
                dec_path = os.path.join(self.model_dir, "onnx", candidates[0])
        if not os.path.isfile(enc_path):
            raise FileNotFoundError(
                f"翻译模型编码器缺失：{enc_path}（检查 IMAGELABEL_MARIAN_DIR 与 IMAGELABEL_MARIAN_PRECISION）"
            )
        if not os.path.isfile(dec_path):
            raise FileNotFoundError(
                f"翻译模型解码器缺失：{dec_path}（检查 IMAGELABEL_MARIAN_DIR 与 IMAGELABEL_MARIAN_PRECISION）"
            )

        opts = ort.SessionOptions()
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.providers = providers or ["CPUExecutionProvider"]
        self.encoder = ort.InferenceSession(enc_path, opts, providers=self.providers)
        self.decoder = ort.InferenceSession(dec_path, opts, providers=self.providers)

        with open(os.path.join(self.model_dir, "vocab.json"), encoding="utf-8") as handle:
            self.vocab: dict[str, int] = json.load(handle)
        self.inverse_vocab = {index: piece for piece, index in self.vocab.items()}
        self.source = SentencePieceUnigram(os.path.join(self.model_dir, "source.spm"))
        # The target sentencepiece model only knows the output vocabulary, so it
        # cannot encode Chinese; decoding is a plain metaspace join of the shared
        # 65001-entry Marian vocabulary instead.
        self.unk_id = self.vocab.get("<unk>", 1)

    # -- ids --------------------------------------------------------------- #
    def source_ids(self, text: str) -> list[int]:
        return [self.vocab.get(piece, self.unk_id) for piece in self.source.encode(text)] + [EOS]

    def target_text(self, ids: list[int]) -> str:
        return "".join(self.inverse_vocab.get(i, "") for i in ids).replace(SPACE, " ").strip()

    # -- inference --------------------------------------------------------- #
    def encode(self, text: str) -> np.ndarray:
        ids = np.asarray([self.source_ids(text)], dtype=np.int64)
        return self.encoder.run(None, {
            "input_ids": ids,
            "attention_mask": np.ones_like(ids),
        })[0]

    def translate(self, text: str) -> str:
        """Translate one string with greedy decoding (no KV cache)."""
        text = (text or "").strip()
        if not text:
            return ""
        hidden = self.encode(text)
        mask = np.ones((hidden.shape[0], hidden.shape[1]), dtype=np.int64)
        out = [DECODER_START]
        for _ in range(self.max_new_tokens):
            logits = self.decoder.run(None, {
                "input_ids": np.asarray([out], dtype=np.int64),
                "encoder_hidden_states": hidden,
                "encoder_attention_mask": mask,
            })[0]
            nxt = int(np.argmax(logits[0, -1]))
            if nxt in (EOS, PAD):
                break
            out.append(nxt)
        return self.target_text(out[1:])

    def translate_many(self, texts) -> list[str]:
        return [self.translate(t) for t in texts]
