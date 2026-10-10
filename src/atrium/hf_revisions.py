"""Hugging Face のモデルの版（commit）の固定．

モデルを名前だけで読むと，上流のリポジトリの main が差し替えられたときに，黙って別の重みやコードを読む．
ここの commit は 2026-10-10 に制御点（wafl-ctrl5）のキャッシュの refs/main が指していた版で，
それまでの実験はすべてこの版で動いた（各デバイスへ配ったキャッシュも同じ版だった）．
SGPT のキャッシュには古い snapshot（7453f6b）も残っているが，読まれていたのは refs/main の版である．
"""

from __future__ import annotations

HF_MODEL_REVISIONS: dict[str, str] = {
    "BAAI/bge-reranker-v2-m3": "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e",
    "Muennighoff/SGPT-5.8B-weightedmean-msmarco-specb-bitfit": (
        "7d8e64fce40a95dfb82e2b9ce8c58c5476910454"
    ),
    "Snowflake/snowflake-arctic-embed-m-v1.5": "e58a8f756156a1293d763f17e3aae643474e9b8a",
    "WhereIsAI/UAE-Large-V1": "9c9b2c999b3350cfb3171ed429320668e39b00b8",
    "intfloat/e5-base": "b533fe4636f4a2507c08ddab40644d20b0006d6a",
    "intfloat/e5-large": "4dc6d853a804b9c8886ede6dda8a073b7dc08a81",
    "intfloat/multilingual-e5-large": "3d7cfbdacd47fdda877c5cd8a79fbcc4f2a574f3",
    "llmrails/ember-v1": "5e5ce5904901f6ce1c353a95020f17f09e5d021d",
    "ncbi/MedCPT-Article-Encoder": "d05a736da4bb84ee4057b7f7999485be6ed85465",
    "ncbi/MedCPT-Query-Encoder": "d83a36cc6b8e3a5c5e9d9d6ba156808c1643dcbc",
    "sentence-transformers/all-mpnet-base-v2": "e8c3b32edf5434bc2275fc9bab85f82640a19130",
    "thenlper/gte-base": "c078288308d8dee004ab72c6191778064285ec0c",
}


def pinned_revision(model_name: str) -> str:
    """モデルの固定した commit を返す．

    表に無いモデルは読ませない．固定し忘れたモデルが黙って最新の版で読まれるのではなく，
    実行の最初に失敗として現れるようにするため．
    """
    try:
        return HF_MODEL_REVISIONS[model_name]
    except KeyError:
        raise KeyError(
            f"{model_name} has no pinned revision; add it to atrium.hf_revisions.HF_MODEL_REVISIONS"
        ) from None
