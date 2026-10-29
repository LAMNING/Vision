"""Pure-Python BERT WordPiece tokenizer (no `tokenizers`, no `transformers`).

Reproduces `BertTokenizer` (do_lower_case=True, strip_accents=True, never_split
empty) as used by the LLMDet / GroundingDINO text encoder:

    [CLS] token1 token2 ... [SEP]      with token_type_ids = 0

Only the Python standard library is required.
"""
from __future__ import annotations

import unicodedata

CLS_TOKEN = "[CLS]"
SEP_TOKEN = "[SEP]"
UNK_TOKEN = "[UNK]"
CLS_ID = 101
SEP_ID = 102
UNK_ID = 100
MAX_INPUT_CHARS_PER_WORD = 100


def _is_whitespace(char: str) -> bool:
    if char in (" ", "\t", "\n", "\r"):
        return True
    return unicodedata.category(char) == "Zs"


def _is_control(char: str) -> bool:
    if char in ("\t", "\n", "\r"):
        return False
    return unicodedata.category(char).startswith("C")


def _is_punctuation(char: str) -> bool:
    cp = ord(char)
    if 33 <= cp <= 47 or 58 <= cp <= 64 or 91 <= cp <= 96 or 123 <= cp <= 126:
        return True
    return unicodedata.category(char).startswith("P")


def _is_cjk(char: str) -> bool:
    cp = ord(char)
    return (
        0x4E00 <= cp <= 0x9FFF
        or 0x3400 <= cp <= 0x4DBF
        or 0x20000 <= cp <= 0x2A6DF
        or 0x2A700 <= cp <= 0x2B73F
        or 0x2B740 <= cp <= 0x2B81F
        or 0x2B820 <= cp <= 0x2CEAF
        or 0xF900 <= cp <= 0xFAFF
        or 0x2F800 <= cp <= 0x2FA1F
    )


def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


class BertWordPieceTokenizer:
    """Token-to-id conversion compatible with `bert-base-uncased`."""

    def __init__(self, vocab_path: str, do_lower_case: bool = True, max_length: int = 250):
        self.vocab: dict[str, int] = {}
        with open(vocab_path, encoding="utf-8") as f:
            for line in f:
                token = line.rstrip("\n").rstrip("\r")
                self.vocab[token] = len(self.vocab)
        self.ids_to_tokens = {i: t for t, i in self.vocab.items()}
        self.do_lower_case = do_lower_case
        # [CLS] + ... + [SEP] must fit into the model's text budget.
        self.max_length = max_length

    # ---------------------------------------------------------------- basic
    @staticmethod
    def _clean_text(text: str) -> str:
        out = []
        for char in text:
            cp = ord(char)
            if cp == 0 or cp == 0xFFFD or _is_control(char):
                continue
            out.append(" " if _is_whitespace(char) else char)
        return "".join(out)

    @staticmethod
    def _tokenize_chinese_chars(text: str) -> str:
        out = []
        for char in text:
            if _is_cjk(char):
                out.extend([" ", char, " "])
            else:
                out.append(char)
        return "".join(out)

    @staticmethod
    def _run_split_on_punc(text: str) -> list[str]:
        out, start = [], None
        for i, char in enumerate(text):
            if _is_punctuation(char):
                if start is not None:
                    out.append(text[start:i])
                    start = None
                out.append(char)
            elif start is None:
                start = i
        if start is not None:
            out.append(text[start:])
        return out

    def basic_tokenize(self, text: str) -> list[str]:
        text = self._clean_text(text)
        text = self._tokenize_chinese_chars(text)
        split_tokens = []
        for token in text.split():
            if self.do_lower_case:
                token = token.lower()
            token = _strip_accents(token)
            split_tokens.extend(self._run_split_on_punc(token))
        return " ".join(split_tokens).split()

    # ------------------------------------------------------------ wordpiece
    def wordpiece_tokenize(self, tokens: list[str]) -> list[str]:
        output = []
        for token in tokens:
            chars = list(token)
            if len(chars) > MAX_INPUT_CHARS_PER_WORD:
                output.append(UNK_TOKEN)
                continue
            is_bad = False
            start = 0
            sub_tokens = []
            while start < len(chars):
                end = len(chars)
                cur = None
                while start < end:
                    piece = "".join(chars[start:end])
                    if start > 0:
                        piece = "##" + piece
                    if piece in self.vocab:
                        cur = piece
                        break
                    end -= 1
                if cur is None:
                    is_bad = True
                    break
                sub_tokens.append(cur)
                start = end
            output.extend([UNK_TOKEN] if is_bad else sub_tokens)
        return output

    # --------------------------------------------------------------- public
    def tokenize(self, text: str) -> list[str]:
        return self.wordpiece_tokenize(self.basic_tokenize(text))

    def convert_tokens_to_ids(self, tokens: list[str]) -> list[int]:
        return [self.vocab.get(t, UNK_ID) for t in tokens]

    def encode(self, text: str) -> tuple[list[int], list[int], list[int]]:
        """Return (input_ids, token_type_ids, attention_mask) for one string."""
        pieces = self.tokenize(text)[: self.max_length - 2]
        ids = [CLS_ID] + self.convert_tokens_to_ids(pieces) + [SEP_ID]
        token_type_ids = [0] * len(ids)
        attention_mask = [1] * len(ids)
        return ids, token_type_ids, attention_mask

    def decode(self, ids) -> str:
        tokens = [self.ids_to_tokens.get(int(i), UNK_TOKEN) for i in ids]
        return " ".join(tokens).replace(" ##", "").strip()
