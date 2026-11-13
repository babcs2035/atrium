# 構成比較表（wafl500〜509）

一次記録 `inventory/*.txt` から抽出した比較属性である．UUID・シリアル番号は個体識別に使われ，値の違いを構成不一致と判定しない（計画書 §1）．

## GPU 実装

| 属性 | wafl500 | wafl501 | wafl502 | wafl503 | wafl504 | wafl505 | wafl506 | wafl507 | wafl508 | wafl509 | 判定 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| driver | 595.99.02 | 595.99.02 | 595.99.02 | 595.99.02 | 595.99.02 | 595.99.02 | 595.99.02 | 595.99.02 | 595.99.02 | 595.99.02 | 一致 |
| cuda | 13.2 | 13.2 | 13.2 | 13.2 | 13.2 | 13.2 | 13.2 | 13.2 | 13.2 | 13.2 | 一致 |
| product | NVIDIA GeForce RTX 3060 | NVIDIA GeForce RTX 3060 | NVIDIA GeForce RTX 3060 | NVIDIA GeForce RTX 3060 | NVIDIA GeForce RTX 3060 | NVIDIA GeForce RTX 3060 | NVIDIA GeForce RTX 3060 | NVIDIA GeForce RTX 3060 | NVIDIA GeForce RTX 3060 | NVIDIA GeForce RTX 3060 | 一致 |
| vbios | 94.06.2F.40.7D | 94.06.2F.00.F5 | 94.06.25.00.7C | 94.06.25.00.5E | 94.06.25.00.52 | 94.06.25.00.52 | 94.06.25.00.52 | 94.06.14.00.A6 | 94.06.2F.40.0E | 94.06.25.00.50 | 不一致(8種) |
| gpu_part | 2504-302-A1 | 2504-302-A1 | 2504-302-A1 | 2504-302-A1 | 2504-302-A1 | 2504-302-A1 | 2504-302-A1 | 2503-300-A1 | 2504-302-A1 | 2504-302-A1 | 不一致(2種) |
| inforom_img | G001.0000.03.03 | G001.0000.03.03 | G001.0000.03.03 | G001.0000.03.03 | G001.0000.03.03 | G001.0000.03.03 | G001.0000.03.03 | G001.0000.03.03 | G001.0000.03.03 | G001.0000.03.03 | 一致 |
| pci_device_id | 250410DE | 250410DE | 250410DE | 250410DE | 250410DE | 250410DE | 250410DE | 250310DE | 250410DE | 250410DE | 不一致(2種) |
| pci_sub_system_id | 88101043 | 40721458 | 250410DE | 153610DE | B63019DA | B63019DA | B63019DA | 250310DE | 88101043 | 463019DA | 不一致(7種) |
| mem_total | 12288 MiB | 12288 MiB | 12288 MiB | 12288 MiB | 12288 MiB | 12288 MiB | 12288 MiB | 12288 MiB | 12288 MiB | 12288 MiB | 一致 |
| persistence_mode | Disabled | Disabled | Disabled | Disabled | Disabled | Disabled | Disabled | Disabled | Disabled | Disabled | 一致 |
| display_attached | No | No | No | No | No | No | No | No | No | No | 一致 |
| pcie_max_gen | 3 | 3 | 3 | 3 | 3 | 3 | 3 | 3 | 4 | 4 | 不一致(2種) |
| pcie_max_width | 16x | 16x | 16x | 16x | 16x | 16x | 16x | 16x | 16x | 16x | 一致 |
| clocks_max_sm | 2100 MHz | 2130 MHz | 2160 MHz | 2100 MHz | 2100 MHz | 2100 MHz | 2100 MHz | 2100 MHz | 2100 MHz | 2130 MHz | 不一致(3種) |
| clocks_max_mem | 7501 MHz | 7501 MHz | 7501 MHz | 7501 MHz | 7501 MHz | 7501 MHz | 7501 MHz | 7501 MHz | 7501 MHz | 7501 MHz | 一致 |

## 電力・冷却設定

| 属性 | wafl500 | wafl501 | wafl502 | wafl503 | wafl504 | wafl505 | wafl506 | wafl507 | wafl508 | wafl509 | 判定 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| power_limit | 170.00 W | 170.00 W | 180.00 W | 170.00 W | 170.00 W | 170.00 W | 170.00 W | 170.00 W | 170.00 W | 170.00 W | 不一致(2種) |
| power_default | 170.00 W | 170.00 W | 180.00 W | 170.00 W | 170.00 W | 170.00 W | 170.00 W | 170.00 W | 170.00 W | 170.00 W | 不一致(2種) |
| power_min | 100.00 W | 100.00 W | 100.00 W | 100.00 W | 100.00 W | 100.00 W | 100.00 W | 100.00 W | 100.00 W | 100.00 W | 一致 |
| power_max | 187.00 W | 212.00 W | 190.00 W | 170.00 W | 170.00 W | 170.00 W | 170.00 W | 170.00 W | 187.00 W | 187.00 W | 不一致(4種) |
| temp_gpu | 43 C | 45 C | 46 C | 40 C | 30 C | 30 C | 29 C | 54 C | 29 C | 42 C | 不一致(8種) |
| temp_mem | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | 一致 |
| fan_speed | 0 % | 0 % | 0 % | 0 % | 30 % | 30 % | 30 % | 0 % | 40 % | 0 % | 不一致(3種) |
| perf_state | P8 | P8 | P8 | P8 | P8 | P8 | P8 | P8 | P8 | P8 | 一致 |

## PC 本体

| 属性 | wafl500 | wafl501 | wafl502 | wafl503 | wafl504 | wafl505 | wafl506 | wafl507 | wafl508 | wafl509 | 判定 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| cpu_model | Intel(R) Core(TM) i7-8700 CPU @ 3.20GHz | Intel(R) Core(TM) i7-8700 CPU @ 3.20GHz | Intel(R) Core(TM) i7-8700 CPU @ 3.20GHz | Intel(R) Core(TM) i7-8700 CPU @ 3.20GHz | Intel(R) Core(TM) i7-9700 CPU @ 3.00GHz | Intel(R) Core(TM) i7-9700 CPU @ 3.00GHz | Intel(R) Core(TM) i7-9700K CPU @ 3.60GHz | Intel(R) Core(TM) i7-9700 CPU @ 3.00GHz | 12th Gen Intel(R) Core(TM) i5-12400F | 12th Gen Intel(R) Core(TM) i5-12400F | 不一致(4種) |
| cpu_cpus | 12 | 12 | 12 | 12 | 8 | 8 | 8 | 8 | 12 | 12 | 不一致(2種) |
| cpu_cores_per_socket | 6 | 6 | 6 | 6 | 8 | 8 | 8 | 8 | 6 | 6 | 不一致(2種) |
| cpu_threads_per_core | 2 | 2 | 2 | 2 | 1 | 1 | 1 | 1 | 2 | 2 | 不一致(2種) |
| cpu_max_mhz | 4600.0000 | 4600.0000 | 4600.0000 | 4600.0000 | 4700.0000 | 4700.0000 | 4900.0000 | 4700.0000 | 4400.0000 | 4400.0000 | 不一致(4種) |
| cpu_l3 | 12 MiB (1 instance) | 12 MiB (1 instance) | 12 MiB (1 instance) | 12 MiB (1 instance) | 12 MiB (1 instance) | 12 MiB (1 instance) | 12 MiB (1 instance) | 12 MiB (1 instance) | 18 MiB (1 instance) | 18 MiB (1 instance) | 不一致(2種) |
| ram_total | 15 GiB | 15 GiB | 15 GiB | 15 GiB | 15 GiB | 15 GiB | 15 GiB | 15 GiB | 31 GiB | 31 GiB | 不一致(2種) |
| ram_speeds | 2400 | 2666 | 2666 | 2666 | 2667 | 2400 | 2400 | 2666 | 2133 | 2667 | 不一致(4種) |
| storage_disks | nvme0n1 PC 512110190592 | nvme0n1 LA 512110190592 | nvme0n1 Vi3000 512110190592 | nvme0n1 MZVLW512HMJP-000H1 512110190592 | sda Reader 0 | nvme0n1 nvme 1024209543168 | sda Reader 0 | nvme0n1 SSD 1000204886016 | sda Reader 0 | nvme0n1 SSD 1000204886016 | sda Reader 0 | nvme0n1 SSD 1000204886016 | nvme0n1 X4000 1024209543168 | nvme0n1 EN600Pro 1024209543168 | 不一致(8種) |
| sys_board | PRIME H310M-AT R2.0 | PRIME H310M-AT R2.0 | PRIME H310M-AT R2.0 | PRIME H310M-AT R2.0 | Z390 Pro4 | Z390 Pro4 | Z370 Pro4 | TUF Z390-PLUS GAMING | H610M-HVS/M.2 R2.0 | H610M-HVS/M.2 R2.0 | 不一致(5種) |
| sys_bios | 2208 | 2208 | 2208 | 2208 | P4.70 | P4.70 | P4.30 | 3006 | 11.01 | 11.01 | 不一致(5種) |
| sys_governor | powersave | powersave | powersave | powersave | powersave | powersave | powersave | powersave | powersave | powersave | 一致 |
| sys_nic_speed_mbps | 1000 | 1000 | 1000 | 1000 | 1000 | 1000 | 1000 | 1000 | 1000 | 1000 | 一致 |

## 実行環境

| 属性 | wafl500 | wafl501 | wafl502 | wafl503 | wafl504 | wafl505 | wafl506 | wafl507 | wafl508 | wafl509 | 判定 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| rt_os | Ubuntu 24.04.5 LTS | Ubuntu 24.04.5 LTS | Ubuntu 24.04.5 LTS | Ubuntu 24.04.5 LTS | Ubuntu 24.04.5 LTS | Ubuntu 24.04.5 LTS | Ubuntu 24.04.5 LTS | Ubuntu 24.04.5 LTS | Ubuntu 24.04.5 LTS | Ubuntu 24.04.5 LTS | 一致 |
| rt_kernel | 7.0.0-42-generic | 6.8.0-147-generic | 7.0.0-42-generic | 7.0.0-42-generic | 6.8.0-147-generic | 6.8.0-147-generic | 6.8.0-147-generic | 6.8.0-147-generic | 6.8.0-147-generic | 6.8.0-147-generic | 不一致(2種) |
| rt_full_image_id | no_latest_tag | no_latest_tag | sha256:97f37fc180a50fc0012112588d3a203bbc0aceb0c50e545d112f3f42ab706e7f | sha256:97f37fc180a50fc0012112588d3a203bbc0aceb0c50e545d112f3f42ab706e7f | sha256:97f37fc180a50fc0012112588d3a203bbc0aceb0c50e545d112f3f42ab706e7f | sha256:97f37fc180a50fc0012112588d3a203bbc0aceb0c50e545d112f3f42ab706e7f | sha256:97f37fc180a50fc0012112588d3a203bbc0aceb0c50e545d112f3f42ab706e7f | sha256:97f37fc180a50fc0012112588d3a203bbc0aceb0c50e545d112f3f42ab706e7f | sha256:a1cce7764b12f971e893c3a9d93b2dc425d45816a543dde4956a23431f735556 | sha256:bd4eb5990aa901fe0b5c87b6cbf73b59e85a886a564f4fcb7e44d3b377982b83 | 不一致(4種) |

## 常駐・占有

| 属性 | wafl500 | wafl501 | wafl502 | wafl503 | wafl504 | wafl505 | wafl506 | wafl507 | wafl508 | wafl509 | 判定 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| ct_containers | none | none | none | none | none | none | none | none | none | none | 一致 |
| ct_compute_apps | none | none | none | none | none | none | none | none | none | none | 一致 |

