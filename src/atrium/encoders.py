"""クエリと文書の埋め込み（質問者と中継点で使う．torch が必要）．

MedCPT は MedRAG（`src/utils.py` の CustomizeSentenceTransformer と embed()）と同じ入力・pooling で動かす．
FeB4RAG の検索器ごとの前処理（接頭辞・pooling・正規化）は RAGRoute
（`ragroute/models/feb4rag/custom_models.py`，`model_zoo.py`，MIT License）から移植した．
SGPT の specb は，元の実装が sentence-transformers を改造して括弧を特殊トークンとして扱うのに対し，
ここでは括弧のトークンを本文と別にトークン化して連結することで同じ入力列を作る．
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

import numpy as np
import torch
from sentence_transformers import SentenceTransformer
from transformers import AutoModel, AutoTokenizer, PreTrainedTokenizerBase

from atrium.arrays import F32Array

Doc = tuple[str, str]  # (title, text)
MAX_LENGTH = 512


class Encoder(Protocol):
    """検索器の共通インタフェース．"""

    name: str

    def encode_queries(self, queries: Sequence[str]) -> F32Array:
        """クエリを埋め込む（float32，行がクエリ）．"""
        ...

    def encode_docs(self, docs: Sequence[Doc]) -> F32Array:
        """文書を埋め込む（float32，行が文書）．"""
        ...


def _device() -> str:
    return "cuda" if torch.cuda.is_available() else "cpu"


class MedcptEncoder:
    """MedCPT（クエリ側と文書側で別のモデル．CLS pooling）．

    MedRAG は sentence-transformers の Transformer（max_seq_length は 512）と CLS pooling で
    埋め込んでいる．ここでは同じ処理を transformers で直接書く：文書は [title, content] を
    文の組として truncation="longest_first" で 512 トークンに切り詰め，先頭トークンの出力を使う．
    """

    def __init__(
        self, query_model: str, article_model: str, batch_size: int = 256, load_article: bool = True
    ) -> None:
        """クエリ側のモデルと，必要なら文書側のモデルを読み込む．"""
        self.name = query_model
        self._batch_size = batch_size
        self._device = _device()
        self._query = self._load(query_model)
        self._article = self._load(article_model) if load_article else None

    def _load(self, model_name: str) -> tuple[PreTrainedTokenizerBase, torch.nn.Module]:
        tokenizer = AutoTokenizer.from_pretrained(model_name)
        model = AutoModel.from_pretrained(model_name).to(self._device).eval()
        return tokenizer, model

    @torch.no_grad()
    def _encode(
        self,
        loaded: tuple[PreTrainedTokenizerBase, torch.nn.Module],
        first: list[str],
        second: list[str] | None,
    ) -> F32Array:
        tokenizer, model = loaded
        # 文字数の降順に並べてバッチを作り，padding を減らす（attention mask があるので結果は変わらない）．
        # 出力は元の順序へ戻す
        lengths = [
            len(f) + (len(second[i]) if second is not None else 0) for i, f in enumerate(first)
        ]
        order = np.argsort(-np.asarray(lengths), kind="stable")
        out = np.empty((len(first), 0), dtype=np.float32)
        for start in range(0, len(first), self._batch_size):
            idx = order[start : start + self._batch_size]
            batch = tokenizer(
                [first[i] for i in idx],
                [second[i] for i in idx] if second is not None else None,
                padding=True,
                truncation="longest_first",
                max_length=MAX_LENGTH,
                return_tensors="pt",
            ).to(self._device)
            hidden = model(**batch).last_hidden_state[:, 0].float().cpu().numpy()
            if out.shape[1] == 0:
                out = np.empty((len(first), hidden.shape[1]), dtype=np.float32)
            out[idx] = hidden
        return out

    def encode_queries(self, queries: Sequence[str]) -> F32Array:
        """質問文をそのまま埋め込む．"""
        return self._encode(self._query, list(queries), None)

    def encode_docs(self, docs: Sequence[Doc]) -> F32Array:
        """MedRAG と同じく [title, content] の組として埋め込む．"""
        if self._article is None:
            raise RuntimeError("article encoder is not loaded")
        return self._encode(self._article, [t for t, _ in docs], [c for _, c in docs])


@dataclass(frozen=True)
class HfEncoderSpec:
    """FeB4RAG の検索器 1 個の前処理．"""

    hf_name: str
    family: Literal["sentence_transformers", "e5", "angle", "sgpt"]
    query_prefix: str = ""
    doc_prefix: str = ""


# FeB4RAG の engines.csv の model 列の値 → 前処理
FEB4RAG_ENCODERS: dict[str, HfEncoderSpec] = {
    "e5-large": HfEncoderSpec("intfloat/e5-large", "e5", "query: ", "passage: "),
    "e5-base": HfEncoderSpec("intfloat/e5-base", "e5", "query: ", "passage: "),
    "multilingual-e5-large": HfEncoderSpec(
        "intfloat/multilingual-e5-large", "e5", "query: ", "passage: "
    ),
    "UAE-Large-V1": HfEncoderSpec(
        "WhereIsAI/UAE-Large-V1",
        "angle",
        "Represent this sentence for searching relevant passages:",
    ),
    "all-mpnet-base-v2": HfEncoderSpec(
        "sentence-transformers/all-mpnet-base-v2", "sentence_transformers"
    ),
    "ember-v1": HfEncoderSpec("llmrails/ember-v1", "sentence_transformers"),
    "gte-base": HfEncoderSpec("thenlper/gte-base", "sentence_transformers"),
    "SGPT-5.8B-weightedmean-msmarco-specb-bitfit": HfEncoderSpec(
        "Muennighoff/SGPT-5.8B-weightedmean-msmarco-specb-bitfit", "sgpt"
    ),
}


class HfEncoder:
    """FeB4RAG の検索器（FEB4RAG_ENCODERS の 1 項目）．"""

    def __init__(self, name: str, batch_size: int = 32, device: str | None = None) -> None:
        """モデルを読み込む．SGPT（5.8B）は 12 GB の GPU に載らないため既定で CPU に置く．"""
        self.name = name
        self.spec = FEB4RAG_ENCODERS[name]
        self._batch_size = batch_size
        self._device = device or ("cpu" if self.spec.family == "sgpt" else _device())
        if self.spec.family == "sentence_transformers":
            self._st = SentenceTransformer(self.spec.hf_name, device=self._device)
        else:
            self._tokenizer = AutoTokenizer.from_pretrained(self.spec.hf_name)
            self._model = AutoModel.from_pretrained(self.spec.hf_name).to(self._device).eval()

    def encode_queries(self, queries: Sequence[str]) -> F32Array:
        """検索器ごとの接頭辞を付けて埋め込む．"""
        if self.spec.family == "sgpt":
            return self._encode_sgpt(list(queries), "[", "]")
        return self._encode([self.spec.query_prefix + q for q in queries])

    def encode_docs(self, docs: Sequence[Doc]) -> F32Array:
        """「title text」に接頭辞を付けて埋め込む（BEIR と同じく空白で連結する）．"""
        texts = [f"{title} {text}".strip() for title, text in docs]
        if self.spec.family == "sgpt":
            return self._encode_sgpt(texts, "{", "}")
        return self._encode([self.spec.doc_prefix + t for t in texts])

    @torch.no_grad()
    def _encode(self, texts: list[str]) -> F32Array:
        if self.spec.family == "sentence_transformers":
            out = self._st.encode(texts, batch_size=self._batch_size, show_progress_bar=False)
            return np.asarray(out, dtype=np.float32)
        chunks: list[F32Array] = []
        for start in range(0, len(texts), self._batch_size):
            batch = self._tokenizer(
                texts[start : start + self._batch_size],
                padding=True,
                truncation=True,
                max_length=MAX_LENGTH,
                return_tensors="pt",
            ).to(self._device)
            hidden = self._model(**batch).last_hidden_state
            mask = batch["attention_mask"].unsqueeze(-1).float()
            if self.spec.family == "e5":
                emb = (hidden * mask).sum(1) / mask.sum(1)
            else:  # angle: CLS pooling ＋ L2 正規化
                emb = torch.nn.functional.normalize(hidden[:, 0], dim=-1)
            chunks.append(emb.float().cpu().numpy())
        return np.concatenate(chunks).astype(np.float32)

    @torch.no_grad()
    def _encode_sgpt(self, texts: list[str], open_tok: str, close_tok: str) -> F32Array:
        tok = self._tokenizer
        open_ids = tok.encode(open_tok, add_special_tokens=False)
        close_ids = tok.encode(close_tok, add_special_tokens=False)
        out: list[F32Array] = []
        for text in texts:
            body = tok.encode(text, add_special_tokens=False)[
                : MAX_LENGTH - len(open_ids) - len(close_ids)
            ]
            ids = torch.tensor([open_ids + body + close_ids], device=self._device)
            hidden = self._model(input_ids=ids).last_hidden_state[0]
            # SGPT の weighted mean: 後ろのトークンほど重みを大きくする（重みは位置 1..L）
            weights = torch.arange(1, hidden.shape[0] + 1, device=self._device, dtype=hidden.dtype)
            emb = (hidden * weights[:, None]).sum(0) / weights.sum()
            out.append(emb.float().cpu().numpy())
        return np.stack(out).astype(np.float32)
