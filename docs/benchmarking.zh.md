# Benchmarking

[English](benchmarking.md) | 中文

基准测试位于 `bench/` 下：`py/`（pytest-benchmark 单进程）、`xproc/`（跨进程脚本）、`docker/`（固定环境三方对比）、`checks/`（锚点/回归闸门）。Rust criterion 基准在 crate 内。

| nox 会话 | 内容 |
|--------|---------|
| `nox -s bench_py` | pytest-benchmark，单进程 |
| `nox -s bench_rust` | native crate 内的 criterion 基准 |
| `nox -s bench_cross` | 跨进程脚本 |
| `nox -s bench` | 以上全部 |
| `nox -s check_regression` | 回归闸门 |
| `nox -s check_anchors` | 锚点闸门 |

已发布的数字来自固定规格的 Docker 环境（2 核 / 2 GB，`bench/docker/`）；当前表格与复现命令见 [README 基准](../README.zh.md#性能基准)。Python 基准用 `python -O` 运行，测的才是发布模式的层。
