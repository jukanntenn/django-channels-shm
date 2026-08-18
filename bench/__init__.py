"""Performance benchmark suites for channels-shm.

Layout:
- bench/py/        single-process pytest-benchmark suite (fast, release build)
- bench/xproc/     cross-process scenario benchmarks (multiprocessing, slow)
- bench/docker/    reproducible 3-way comparison under pinned hardware
- bench/checks/    post-run gate scripts (criterion + pytest-benchmark anchors)
- bench/baselines/ committed named baselines and absolute threshold anchors
- bench/results/   gitignored run artifacts (xproc JSON snapshots)
"""
