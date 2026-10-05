# atrium のコンテナイメージ（制御点 wafl-ctrl5 で build し，ローカル registry から各ノードへ配る）．
#
#   target=node : 専門家ノード用（GPU なしの PC）．FAISS・FastAPI だけを入れた軽いイメージ
#   target=full : 質問者・データ中継点・E0 用．torch（CUDA 版）・transformers を含む
#
# 使い方: scripts/remote/setup.sh が `docker build --target node|full` で作る．
FROM python:3.12-slim AS base
# uv はイメージ内の pip で入れる（ghcr.io の uv イメージは，環境によって匿名での取得が拒否されるため）
RUN pip install --no-cache-dir uv==0.6.13
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    HF_HOME=/cache/huggingface
WORKDIR /app
# libgomp1: faiss-cpu の OpenMP．git: StatPearls の取得などで使う
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 git curl \
    && rm -rf /var/lib/apt/lists/*

FROM base AS node
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
# --no-editable: venv の site-packages へ実体を入れる．editable だと /app/src を直接読むため，
# 操作端末の umask（077）で作られたソースの権限のまま，コンテナ内の一般ユーザーから読めなくなる
RUN uv sync --frozen --no-dev --no-editable
ARG GIT_HEAD=unknown
ENV ATRIUM_GIT_HEAD=$GIT_HEAD

FROM base AS full
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --extra requester --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --extra requester --no-editable
ARG GIT_HEAD=unknown
ENV ATRIUM_GIT_HEAD=$GIT_HEAD
