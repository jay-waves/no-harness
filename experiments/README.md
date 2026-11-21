# Reproduce the experiments

Use the system Go toolchain required by `ssh-mcp/go.mod` and Python 3. Run the commands from the repository root. The Linux x86-64 footprint and memory tests also require Docker, curl, tar, and sha256sum.

| Command | What it runs | Local output |
| --- | --- | --- |
| `./experiments/run.sh loopback` | 45 scripted trials across exec once, Bash REPL, and Python REPL on an authenticated loopback SSH fixture | `experiments/results/loopback.json` |
| `./experiments/run.sh footprint` | Alpine rootfs and payload sizes, four task checks, and five PSS samples per deployment | `experiments/results/footprint.json` and `experiments/results/memory.jsonl` |

The loopback runner uses a Go overlay so the experiment can reuse the SSH MCP module and its SSH fixture. Normal `go test ./...` in that module does not run the experiment.

The footprint runner verifies the pinned Codex 0.170.0 Linux musl archive, builds the current worker, and runs the tests in disposable containers. The network is disabled during measurements. The mock endpoint listens inside the Codex container and uses a placeholder API key. The Alpine image digest is pinned. Alpine package revisions can change, so later runs may have different base sizes. Timing, memory, and worker build size can also vary by host and toolchain.

The original loopback and memory measurements are available locally under `experiments/results/*-original.*` when archived from the measurement machine. Fresh runs write separate files. Downloaded packages and executables live under `experiments/.work/`. Both directories are ignored by Git. The report contains the published measurements.

[Project report](../REPORT.md)
