"""収集済みの構成記録から，全 10 台の構成比較表を作る．

読み取り専用の一次記録（inventory/*.txt）を解析し，計画書 §1 の比較する層ごとに
一致／不一致を判定する．性能の判定ではない．出力は markdown の比較表と，
判定に使う機械可読な JSON の 2 つである．

使い方:
    uv run --no-sync python scripts/gpu_equivalence/analyze_inventory.py \
        --inventory results/20261111_gpu_equivalence/inventory \
        --out results/20261111_gpu_equivalence/inventory
"""

from __future__ import annotations

import argparse
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

HOSTS = [f"wafl50{i}" for i in range(10)]


def split_sections(text: str) -> dict[str, str]:
    """### 見出しで区切られたセクションを辞書にする．"""
    sections: dict[str, str] = {}
    current = ""
    lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("### "):
            if current:
                sections[current] = "\n".join(lines)
            current = line[4:].strip()
            lines = []
        elif line.startswith("exit_code="):
            # 各 probe の終了コードは XML 等の本文に混入すると不正になるため，本文から除く
            continue
        else:
            lines.append(line)
    if current:
        sections[current] = "\n".join(lines)
    return sections


def parse_gpu_xml(sections: dict[str, str]) -> dict[str, str]:
    """nvidia-smi の XML から GPU の比較属性を抜く．"""
    raw = sections.get("gpu_xml", "")
    if "<nvidia_smi_log>" not in raw:
        return {"parse": "missing"}
    # DOCTYPE の DTD 参照を除去して標準パーサで読む
    raw = re.sub(r"<!DOCTYPE[^>]*>", "", raw)
    root = ET.fromstring(raw)
    gpu = root.find("gpu")
    if gpu is None:
        return {"parse": "no_gpu"}

    def text(path: str) -> str:
        node = gpu.find(path)
        return (node.text or "").strip() if node is not None else ""

    return {
        "driver": text("driver_version") or root.findtext("driver_version", "").strip(),
        "cuda": text("cuda_version") or root.findtext("cuda_version", "").strip(),
        "product": text("product_name"),
        "vbios": text("vbios_version"),
        "gpu_part": text("gpu_part_number"),
        "inforom_img": text("inforom_version/img_version"),
        "uuid": text("uuid"),
        "serial": text("serial"),
        "display_attached": text("display_attached"),
        "persistence_mode": text("persistence_mode"),
        "pci_device_id": text("pci/pci_device_id"),
        "pci_sub_system_id": text("pci/pci_sub_system_id"),
        "pcie_max_gen": text("pci/pci_gpu_link_info/pcie_gen/max_link_gen"),
        "pcie_cur_gen": text("pci/pci_gpu_link_info/pcie_gen/current_link_gen"),
        "pcie_max_width": text("pci/pci_gpu_link_info/link_widths/max_link_width"),
        "pcie_cur_width": text("pci/pci_gpu_link_info/link_widths/current_link_width"),
        "mem_total": text("fb_memory_usage/total"),
        "mem_used": text("fb_memory_usage/used"),
        "power_limit": text("gpu_power_readings/current_power_limit"),
        "power_default": text("gpu_power_readings/default_power_limit"),
        "power_min": text("gpu_power_readings/min_power_limit"),
        "power_max": text("gpu_power_readings/max_power_limit"),
        "clocks_sm": text("clocks/sm_clock"),
        "clocks_mem": text("clocks/mem_clock"),
        "clocks_max_sm": text("max_clocks/sm_clock"),
        "clocks_max_mem": text("max_clocks/mem_clock"),
        "temp_gpu": text("temperature/gpu_temp"),
        "temp_mem": text("temperature/memory_temp"),
        "fan_speed": text("fan_speed"),
        "ecc": text("ecc_mode/current"),
        "perf_state": text("performance_state"),
    }


def parse_cpu(sections: dict[str, str]) -> dict[str, str]:
    """lscpu から CPU の型番とコア構成を抜く．"""
    raw = sections.get("cpu", "")
    fields = {
        "model": "Model name:",
        "cpus": "CPU(s):",
        "cores_per_socket": "Core(s) per socket:",
        "threads_per_core": "Thread(s) per core:",
        "max_mhz": "CPU max MHz:",
        "min_mhz": "CPU min MHz:",
        "l3": "L3 cache:",
    }
    out: dict[str, str] = {}
    for key, marker in fields.items():
        for line in raw.splitlines():
            if marker in line:
                out[key] = line.split(":", 1)[1].strip()
                break
    return out


def parse_ram(sections: dict[str, str]) -> dict[str, str]:
    """free と dmidecode から RAM の容量と速度を抜く．"""
    raw = sections.get("ram", "")
    total = ""
    for line in raw.splitlines():
        if line.startswith("Mem:"):
            parts = line.split()
            if len(parts) > 1:
                total = f"{int(parts[1]) // (1024**3)} GiB"
            break
    mem_raw = sections.get("memory_modules", "")
    speeds = sorted(set(re.findall(r"Configured Memory Speed:\s*(\S+)", mem_raw)))
    return {"total": total, "speeds": " / ".join(speeds) if speeds else "unconfirmed"}


def parse_storage(sections: dict[str, str]) -> dict[str, str]:
    """lsblk からディスクの型番と接続を抜く（loop とスナップショットは除く）．"""
    raw = sections.get("storage", "")
    disks: list[str] = []
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[1] == "disk":
            name, size, model = parts[0], parts[2], parts[4] if len(parts) > 4 else ""
            disks.append(f"{name} {model} {size}")
    return {"disks": " | ".join(disks) if disks else "unconfirmed"}


def parse_sysfs(sections: dict[str, str]) -> dict[str, str]:
    """governor とボード情報，NIC の速度を抜く．"""
    raw = sections.get("sysfs", "")
    out: dict[str, str] = {}
    for line in raw.splitlines():
        if "scaling_governor=" in line:
            out["governor"] = line.split("=", 1)[1].strip()
        elif "board_name=" in line:
            out["board"] = line.split("=", 1)[1].strip()
        elif "bios_version=" in line:
            out["bios"] = line.split("=", 1)[1].strip()
        elif line.strip().endswith("/speed=") or re.search(r"/speed=\d+", line):
            m = re.search(r"/speed=(\d+)", line)
            if m:
                out.setdefault("nic_speed_mbps", m.group(1))
    return out


def parse_runtime(sections: dict[str, str]) -> dict[str, str]:
    """OS・カーネル・イメージの ID を抜く．"""
    os_raw = sections.get("os", "")
    pretty = ""
    for line in os_raw.splitlines():
        if line.startswith("PRETTY_NAME="):
            pretty = line.split("=", 1)[1].strip().strip('"')
            break
    kernel = sections.get("kernel", "").split()[2] if sections.get("kernel") else "unconfirmed"
    full_image = sections.get("full_image", "").strip()
    if full_image.startswith("Error"):
        image_id = "no_latest_tag"
    else:
        image_id = full_image.split()[0] if full_image else "unconfirmed"
    return {"os": pretty, "kernel": kernel, "full_image_id": image_id}


def parse_containers(sections: dict[str, str]) -> dict[str, str]:
    """常駐コンテナと compute 用のプロセスを抜く（占有可否の判断材料）．"""
    containers = [
        line for line in sections.get("running_containers", "").splitlines() if line.strip()
    ]
    apps = [
        line
        for line in sections.get("compute_apps", "").splitlines()
        if line.strip() and "pid" not in line
    ]
    return {
        "containers": " | ".join(containers) if containers else "none",
        "compute_apps": " | ".join(apps) if apps else "none",
    }


def analyze(inventory: Path) -> list[dict[str, str]]:
    """全ホストの構成属性を 1 行ずつ返す．"""
    rows: list[dict[str, str]] = []
    for host in HOSTS:
        path = inventory / f"{host}.txt"
        if not path.exists():
            rows.append({"host": host, "missing": "true"})
            continue
        sections = split_sections(path.read_text())
        row: dict[str, str] = {"host": host}
        row.update(parse_gpu_xml(sections))
        row.update({f"cpu_{k}": v for k, v in parse_cpu(sections).items()})
        row.update({f"ram_{k}": v for k, v in parse_ram(sections).items()})
        row.update({f"storage_{k}": v for k, v in parse_storage(sections).items()})
        row.update({f"sys_{k}": v for k, v in parse_sysfs(sections).items()})
        row.update({f"rt_{k}": v for k, v in parse_runtime(sections).items()})
        row.update({f"ct_{k}": v for k, v in parse_containers(sections).items()})
        rows.append(row)
    return rows


def group_consistency(rows: list[dict[str, str]], key: str) -> str:
    """ある属性が全 10 台で一致するか判定する．UUID・シリアルは除外．"""
    values = {r.get(key, "unconfirmed") for r in rows if r.get("missing") != "true"}
    if len(values) == 1:
        return "一致"
    return f"不一致({len(values)}種)"


def main() -> None:
    """比較表を markdown と JSON で保存する．"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    rows = analyze(args.inventory)
    (args.out / "config_rows.json").write_text(
        json.dumps(rows, indent=2, ensure_ascii=False) + "\n"
    )

    # 比較する属性の順序（計画書 §1 の層に対応）．UUID・シリアルは個体識別用のため一致判定に含めない
    compare_keys = [
        (
            "GPU 実装",
            [
                "driver",
                "cuda",
                "product",
                "vbios",
                "gpu_part",
                "inforom_img",
                "pci_device_id",
                "pci_sub_system_id",
                "mem_total",
                "persistence_mode",
                "display_attached",
                "pcie_max_gen",
                "pcie_max_width",
                "clocks_max_sm",
                "clocks_max_mem",
            ],
        ),
        (
            "電力・冷却設定",
            [
                "power_limit",
                "power_default",
                "power_min",
                "power_max",
                "temp_gpu",
                "temp_mem",
                "fan_speed",
                "perf_state",
            ],
        ),
        (
            "PC 本体",
            [
                "cpu_model",
                "cpu_cpus",
                "cpu_cores_per_socket",
                "cpu_threads_per_core",
                "cpu_max_mhz",
                "cpu_l3",
                "ram_total",
                "ram_speeds",
                "storage_disks",
                "sys_board",
                "sys_bios",
                "sys_governor",
                "sys_nic_speed_mbps",
            ],
        ),
        ("実行環境", ["rt_os", "rt_kernel", "rt_full_image_id"]),
        ("常駐・占有", ["ct_containers", "ct_compute_apps"]),
    ]
    lines = [
        "# 構成比較表（wafl500〜509）",
        "",
        "一次記録 `inventory/*.txt` から抽出した比較属性である．UUID・シリアル番号は個体識別に使われ，値の違いを構成不一致と判定しない（計画書 §1）．",
        "",
    ]
    for layer, keys in compare_keys:
        lines.append(f"## {layer}")
        lines.append("")
        lines.append("| 属性 | " + " | ".join(HOSTS) + " | 判定 |")
        lines.append("|---" * (len(HOSTS) + 2) + "|")
        for key in keys:
            label = key
            values = [r.get(key, "—") for r in rows]
            verdict = group_consistency(rows, key)
            lines.append(f"| {label} | " + " | ".join(values) + f" | {verdict} |")
        lines.append("")
    (args.out / "config_comparison.md").write_text("\n".join(lines) + "\n")
    print(f"wrote {args.out / 'config_rows.json'} and {args.out / 'config_comparison.md'}")


if __name__ == "__main__":
    main()
