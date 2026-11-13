#!/usr/bin/env bash
# GPU 同等性調査でホストの構成を読み取り，セクション付きの一次記録を出力する．
set -uo pipefail
section() { printf '\n### %s\n' "$1"; }
probe() {
  section "$1"
  shift
  "$@" 2>&1
  printf '\nexit_code=%s\n' "$?"
}
probe time date -Is
probe hostname hostname
probe uptime uptime
probe os cat /etc/os-release
probe kernel uname -a
probe cpu lscpu
probe ram free -b
probe storage lsblk -b -o NAME,TYPE,SIZE,MODEL,TRAN,ROTA,MOUNTPOINTS
probe disk_free df -B1 /tmp /home/denjo/workspace/ktakahashi/atrium
probe pci lspci -nnk
probe pci_detail lspci -vv
probe gpu_xml nvidia-smi -q -x
probe gpu_topology nvidia-smi topo -m
probe compute_apps nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv
probe running_containers docker ps --format '{{.Names}} {{.Image}} {{.Status}}'
probe images docker images --no-trunc --format '{{.Repository}}:{{.Tag}} {{.ID}}'
probe full_image docker image inspect --format '{{.Id}} {{json .RepoDigests}}' localhost:5000/atrium-full:latest
probe sensors sensors
probe interfaces ip -s link
section sysfs
for f in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor /sys/class/dmi/id/{board_vendor,board_name,bios_version,product_name} /sys/class/net/*/speed; do
  if [ -r "$f" ]; then printf '%s=' "$f"; cat "$f" 2>&1; fi
done
section memory_modules
if command -v dmidecode >/dev/null && sudo -n true 2>/dev/null; then
  sudo -n dmidecode --type memory | awk '/Size:|Type:|Speed:|Manufacturer:|Part Number:|Locator:/ {print}'
else
  printf 'unavailable: no noninteractive permission for dmidecode\n'
fi
section network_details
for dev in /sys/class/net/*; do
  name=${dev##*/}
  if [ "$name" != lo ]; then ethtool "$name" 2>&1; fi
done
section model_manifests
base=/home/denjo/workspace/ktakahashi/atrium
if [ -d "$base/ollama/models/manifests" ]; then
  find "$base/ollama/models/manifests" -type f -exec sha256sum {} +
fi
section cached_model_paths
find "$base/hf-cache" -maxdepth 6 -type d -name snapshots -o -name 'models--*' 2>/dev/null
section gpu_initial
nvidia-smi --query-gpu=uuid,name,temperature.gpu,utilization.gpu,memory.used,power.limit,clocks.current.sm,clocks.current.memory --format=csv
section recent_gpu_errors
journalctl -k --since '1 hour ago' --no-pager 2>/dev/null | rg 'NVRM|Xid|fallen off' || true
