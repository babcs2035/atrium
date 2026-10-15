# Iteration 14 の事前の測定の入力（Iteration 13 の prestudy/make_inputs.py に pool_assign: lean を足したもの）（ローカルの placement と変種ごとの config）を作る（制御点の atrium-full のコンテナの中で使い捨てに動かす）．
# 使い方：python make_inputs.py <元の placement.json> <元の config.yaml> <出力ディレクトリ>
# ノードは本物の atrium node を 127.0.0.1:18100+i で 30 個動かす（本物のシャードの複製を読む）．質問者は本物の atrium run．
import json
import sys
from pathlib import Path

import yaml

BASE_PORT = 18100
QUESTION_LIMIT = 4  # 受信箱ごとに 4 問．150 受信箱で 600 問

SHAPES = {
    "fs": {"routing": "flood_score"},
    "dp12": {"routing": "device_probe"},
}


def main() -> None:
    """placement と config を書く．"""
    placement_src, config_src, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    placement = json.loads(placement_src.read_text())
    for i, node in enumerate(placement["nodes"]):
        node["host"] = "127.0.0.1"
        node["url"] = f"http://127.0.0.1:{BASE_PORT + i}"
    (out / "placement_local.json").write_text(json.dumps(placement, indent=1))
    nodes_txt = "\n".join(
        f"{BASE_PORT + i} {','.join(n['shard_ids'])}" for i, n in enumerate(placement["nodes"])
    )
    (out / "nodes.txt").write_text(nodes_txt + "\n")
    cfg = yaml.safe_load(config_src.read_text())
    for shape, over in SHAPES.items():
        c = json.loads(json.dumps(cfg))
        e = c["experiment"]
        e.update(
            dataset="enronqa",
            answer_mode="retrieval_only",
            question_limit=QUESTION_LIMIT,
            parallel=30,
            http_pool="per_node",
            requester_procs=1,
            node_workers=1,
            probe_body="pre_encoded",
            sniffio_lookup="blocked",
            pool_assign="lean",
            routing=over["routing"],
        )
        c["routing"]["probe_d"]["enronqa"] = 12
        (out / f"cfg_{shape}.yaml").write_text(yaml.safe_dump(c, allow_unicode=True, sort_keys=False))


if __name__ == "__main__":
    main()
