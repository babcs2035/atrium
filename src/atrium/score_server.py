"""cross-encoder の採点の server（`atrium score-server`．d0009 B2）．1 つの GPU の PC に 1 つ置き，質問者の採点の要求を受ける．

- `POST /v1/score`：`{query, texts}` → `{scores, duration_s}`．中身は `CrossEncoderReranker.scores`（同じモデル・revision・batch 4・lock）を呼ぶだけ．
- `GET /healthz`：モデルの名前・revision・dtype・attention の実装・tokenizer の最大長・実際に効いている最大長を返す（起動の確認と条件の記録用．モデルの読み込みが済んでから待ち受ける）．
使い方: scripts/remote/score_server.sh が GPU の PC のコンテナで `atrium score-server --port 8200` を起動する．
"""

from __future__ import annotations

import time
from typing import Literal

from fastapi import FastAPI

from atrium.hf_revisions import pinned_revision
from atrium.protocol import ScoreRequest, ScoreResponse
from atrium.requester import CandidateScorer, CrossEncoderReranker


def create_score_app(
    scorer: CandidateScorer, model: str, revision: str, details: dict[str, str | int] | None = None
) -> FastAPI:
    """採点の FastAPI アプリを作る（試験では scorer に偽物を渡す）．details は /healthz に足す条件（dtype など）．"""
    app = FastAPI(title="atrium cross-encoder scorer")

    @app.get("/healthz")
    def healthz() -> dict[str, str | int]:
        """起動の確認と，採点の条件（dtype・attention の実装・tokenizer の最大長）の記録．"""
        return {"status": "ok", "model": model, "revision": revision, **(details or {})}

    # 採点は GPU の同期処理なので def のまま FastAPI のスレッドプールで動かす（同時の呼び出しは scorer の lock が直列にする）
    @app.post("/v1/score")
    def score(request: ScoreRequest) -> ScoreResponse:
        """質問と各文の得点を文の順に返す．"""
        start = time.perf_counter()
        values = scorer.scores(request.query, request.texts)
        return ScoreResponse(scores=values, duration_s=time.perf_counter() - start)

    return app


def main(
    host: str,
    port: int,
    dtype: Literal["float32", "float16"] = "float32",
    max_length: int | None = None,
) -> None:
    """モデルを dtype・max_length（None は今までと同じ tokenizer の上限）で読み込んでから採点の server を起動する．"""
    import uvicorn

    scorer = CrossEncoderReranker(dtype, max_length)
    app = create_score_app(
        scorer,
        CrossEncoderReranker.MODEL,
        pinned_revision(CrossEncoderReranker.MODEL),
        scorer.describe(),
    )
    uvicorn.run(app, host=host, port=port)
