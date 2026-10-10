"""EnronQA の行の単位と列の形を確かめる（制御点のコンテナで実行）．"""
import pyarrow.parquet as pq, glob, json
for split in ("train", "dev", "test"):
    files = sorted(glob.glob(f"/data/raw/data/{split}-*.parquet"))
    t = pq.read_table(files[0])
    print(split, files, sum(pq.ParquetFile(f).metadata.num_rows for f in files), t.schema.names)
t = pq.read_table("/data/raw/data/test-00000-of-00001.parquet").slice(0, 2).to_pylist()
for r in t:
    print({k: (v[:300] if isinstance(v, str) else v) for k, v in r.items() if k not in ("email",)})
    print("EMAIL:", r["email"][:500].replace("\n", " | "))
