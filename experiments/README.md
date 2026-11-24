# Reproduce the experiments

Use the system Go toolchain required by `ssh-mcp/go.mod` and Python 3. Run the commands from the repository root. The Linux x86-64 footprint and memory tests also require Docker, curl, tar, and sha256sum.

| Command | What it runs | Local output |
| --- | --- | --- |
| `./experiments/run.sh loopback` | 45 scripted trials across exec once, Bash REPL, and Python REPL on an authenticated loopback SSH fixture | `experiments/results/loopback.json` |
| `./experiments/run.sh footprint` | Alpine rootfs and payload sizes, four task checks, and five PSS samples per deployment | `experiments/results/footprint.json` and `experiments/results/memory.jsonl` |
| `./experiments/run.sh serialization` | PowerShell and Docker exec versus the ∅ne Bash worker, including launch latency, peak sampled RSS, and codec microbenchmarks | `experiments/results/serialization.json` |
| `python3 experiments/remote_bench.py replay` | Reference task checks over direct SSH, Docker exec, and ∅ne MCP | `experiments/results/remote-replay.json` |
| `python3 experiments/remote_bench.py model` | Small model-driven task comparison over the same three paths | `experiments/results/remote-model.json` |

The loopback runner uses a Go overlay so the experiment can reuse the ∅ne module and its SSH fixture. Normal `go test ./...` in that module does not run the experiment.

The footprint runner verifies the pinned Codex 0.170.0 Linux musl archive, builds the current worker, and runs the tests in disposable containers. The network is disabled during measurements. The mock endpoint listens inside the Codex container and uses a placeholder API key. The Alpine image digest is pinned. Alpine package revisions can change, so later runs may have different base sizes. Timing, memory, and worker build size can also vary by host and toolchain.

## Task quality and context growth

Use the system Docker Engine, Go and uv. Build the existing Python fixture image once if it is absent. Replay is deterministic and does not call a model. DataBench and HumanEval fetch public fixtures from pinned revisions.

```sh
docker build -t no-harness-footprint:python - < experiments/Dockerfile
uv run --locked --script experiments/task_bench.py replay
uv run --locked --script experiments/task_bench.py replay --family databench --output experiments/results/task-databench.json
uv run --locked --script experiments/task_bench.py replay --family humaneval --output experiments/results/task-humaneval.json
uv run --locked --script experiments/task_curves.py experiments/results/task-replay.json experiments/results/task-curves.svg
uv run --locked --script experiments/task_curves.py experiments/results/task-databench.json experiments/results/task-databench-curves.svg
```

The synthetic replay pairs 24 fixtures from four designs across fresh and persistent Python, each with full and bounded tool output. Full output includes diagnostic records and is subject to a 32 KiB cap. Bounded output retains the first line with the answer. Both policies execute the same code for a given interpreter mode. Fresh Python uses Docker exec and can retain files. Persistent Python uses the real ∅ne worker on a local socket. A fresh isolated workspace is created for each run. Per-stage typed JSON answers, fixture preservation, and private repair checks determine success.

Replay counts submitted code and model-visible results using `o200k_base`. Cumulative input sums two serialized prompts per stage, one before the tool call and one after the tool result, with previous messages retained. This is an offline estimate for that message layout. It does not estimate provider token accounting, caching, or cost. Execution wall time is recorded separately and includes transport and process work specific to each path.

DataBench uses three released questions each from Forbes and Titanic. The fixture retains every row and projects the relevant columns. HumanEval uses two official problems as repair and artifact checks, with staged examples and official tests run in a separate target process. These are adaptations of two small subsets. [DataBench release](https://huggingface.co/datasets/cardiffnlp/databench/tree/e75d53add267d2f9cfa32efd65ad77f0807adfad) · [HumanEval release](https://github.com/openai/human-eval/tree/b9453e8f1865df0a0057a1383767caa400acfbcf)

The optional model pilot requires an authenticated Codex CLI. It makes one provider request per newly revealed question, passes previous code and tool results as explicit history, and allows one Python action per question. The driver executes each action in a disposable network-disabled target. The default DataBench pilot uses 12 requests total. It records per-decision provider input, cached input, output and reasoning tokens, independent task checks, and failure status. Only aggregate measurements are written to the results file.

```sh
uv run --locked --script experiments/task_bench.py model --family databench --output experiments/results/task-model-databench.json
uv run --locked --script experiments/task_curves.py experiments/results/task-model-databench.json experiments/results/task-model-curves.svg
```

## Two-target agent comparison

Use the system Go toolchain, Python 3, Docker Engine, OpenSSH and `ssh-keygen`. The model run also needs an authenticated Codex CLI. Setup, reference checks, and execution use only synthetic fixtures and disposable local targets.

```sh
docker build --build-arg BUILD_TOOLS=bash -t no-harness-remote:python experiments
python3 experiments/remote_bench.py replay
python3 experiments/remote_bench.py model
```

Each trial starts two containers from the same image, with separate workspaces named east and west. The three arms use real OpenSSH, local Docker exec, or the production ∅ne MCP server over real SSH. Bash and Python actions use the same controller schema. Only ∅ne retains interpreter state per target. All arms can batch actions, filter output, relay small values, and preserve files. The containers have no external network access. Docker uses the host daemon and SSH is a local simulation.

The controller reveals one task stage at a time and gives the same model a bounded batch of actions per decision. It executes the actions and passes their results back to the model. The verifier separately checks JSON artifacts and inputs from outside the visible model tools. The JSON output records verified success, action counts, provider tokens, target duration, and controller wall time. The replay checks setup and correctness without model requests. The model run is a three-task pilot with one trial per task and arm.

## Progressively disclosed telemetry benchmark

The seven-checkpoint task follows BFCL v3's per-turn state grading and missing-parameter interaction. It adapts ToolSandbox's ordered milestones and state preservation to two Linux workspaces. The driver reveals the west export, an operator-supplied threshold, and a late west revision at distinct checkpoints. The revision changes which services qualify for the alert. The upfront Docker control gets the same input files and artifact requirements together. Each arm can batch within a decision and retain an on-disk cache. These are local benchmark adaptations. [BFCL v3](https://gorilla.cs.berkeley.edu/blogs/13_bfcl_v3_multi_turn.html) · [ToolSandbox](https://arxiv.org/abs/2408.04682)

Use the image and tools from the [two-target comparison](#two-target-agent-comparison), plus GNU `/usr/bin/time` for model-process peak RSS and uv for plotting. All results go to `experiments/results/` by default.

```sh
python3 experiments/interactive_case.py
python3 experiments/interactive_bench.py replay
python3 experiments/interactive_bench.py replay --release upfront
python3 experiments/interactive_bench.py model
python3 experiments/interactive_bench.py model --release upfront
uv run --locked --script experiments/task_curves.py experiments/results/interactive-model-staged.json experiments/results/interactive-curves.svg
```

Each checkpoint checks typed JSON artifacts, compares earlier artifacts byte for byte, and verifies released inputs remain intact. Missing-parameter grading checks a meaningful clarification with zero execution actions. Unreleased input paths must remain absent. A failed checkpoint can receive feedback and retry within the same decision budget. The next checkpoint opens only after a pass.

The runner saves provider input, cached input and output tokens, decision and action counts, and submitted script sizes. Application payload is decoded UTF-8 code, stdout, and stderr before adding transport frames. Separate counters cover the local Docker CLI's daemon Unix socket bytes, SSH stream bytes at the loopback relay, and ∅ne MCP JSON-RPC bytes at the controller's stdio. The MCP harness also times local JSON encoding and decoding. Those layers overlap and their byte counts describe different boundaries.

The RSS sampler covers both target containers' processes plus separate controller-side transport processes, with samples every 20 ms during actions and before and after each checkpoint. ∅ne preopens Bash and Python on both targets before the idle sample. Summed RSS can count shared pages more than once, and sampling can miss brief peaks. GNU time records maximum RSS for each individual Codex CLI invocation separately. The fixture uses warm local transports. Its I/O counters cover the application and transport boundaries above, and its memory counters cover local processes.

## Experiment 1: command tokens

With the SSH fixture image described above, run `uv run --with tiktoken==0.12.0 --with matplotlib==3.10.8 python experiments/command_token_suite.py`. The runner checks four answers on each of six tasks per path, counts submitted Python using `o200k_base`, and writes `results/experiment-one.json` plus SVG and PNG cumulative curves. Each task starts with fresh workspaces. The JSON records each submitted command, its token count, answer, process ID and SSH connection probe.

The extended experiment uses three original evolving specifications with eight checkpoints each and two paired model runs per task. The grader invokes the current `solve(data)` implementation on the withheld case for the new requirement and on all earlier cases. SSH imports the solution in a fresh interpreter over its reused connection. ∅ne resolves the function in the retained Python namespace. Both arms can save or edit files.

```sh
uv run --with tiktoken==0.12.0 python experiments/evolution_bench.py
uv run --locked --script experiments/task_curves.py experiments/results/evolution.json experiments/results/evolution.svg
```

The runner records every submitted command including retries, provider usage, and strict checkpoint outcomes. The verifier is outside the token budget. The protocol draws on [SlopCodeBench](https://arxiv.org/abs/2603.24755).

## Joined-table follow-ups over SSH

Build the target image using the two-target setup above. The Python fixture writes a synthetic fact table and a small dimension table into the same target workspace. The eight questions progressively ask for joined counts, sums, filtered groups, maxima and regional winners.

```sh
python3 experiments/reuse_fixed.py
uv run --locked --script experiments/reuse_live.py
python3 experiments/reuse_trace.py
uv run --locked --script experiments/task_curves.py experiments/results/reuse-fixed.json experiments/results/reuse-fixed.svg
uv run --locked --script experiments/task_curves.py experiments/results/reuse-live.json experiments/results/reuse-live.svg
uv run --locked --script experiments/task_curves.py experiments/results/reuse-trace.json experiments/results/reuse-trace.svg
```

The fixed run covers 8 and 128 MiB, with one verified eight-query sequence for each of four policies. Both disk-cache and in-memory policies reuse identical packed joined rows. Reported file bytes count logical source and packed-cache I/O; Linux may satisfy reads from its page cache. Memory samples sum RSS across the processes in one target container. Each fixed ∅ne trial opens one Python interpreter. The reset arm replaces that interpreter before each follow-up. Transport counters include the reset calls.

The live run uses one 128 MiB table per isolated trajectory and three pairs of runs. Both arms may write reusable programs or intermediate data. Each stage receives the current question and prior action history. The controller executes the model's submitted code and verifies the JSON answer against the independent fixture key. Command tokens use `o200k_base`; provider input, cached input and output come from Codex usage. The fixed-run I/O counters describe the reference strategies. The live trajectories record the code the model chose. Replaying that exact code on clean targets records RSS and Linux inotify opens of each input per action, with the same answer checks and matching fixture hashes. The live interface preopens Bash and Python for the ∅ne arm. File-open events establish access frequency; full-byte I/O is measured only for the fixed strategies. JSON and figure links in the report resolve locally after running these commands.

For the four-query command-token control, run `uv run --with tiktoken==0.12.0 --with matplotlib==3.10.8 python experiments/reuse_command_tokens.py` with the same SSH fixture image. It writes the submitted code, per-turn token counts, verified answers, interpreter PIDs and connection probes to `results/reuse-command-tokens.json`, and the cumulative curve to `results/reuse-command-tokens.svg`.

## PowerShell wrapper supplement

The runner uses the same target image, Go, OpenSSH, Docker and Codex CLI as the joined-table comparison. Specify the installed PowerShell executable via `ONE_PWSH` when `pwsh` is outside PATH. Run the reference and model commands below for inline arguments, stdin and target script files. The final command draws the free-choice result.

```sh
uv run --with tiktoken==0.12.0 python3 experiments/wrapper_bench.py replay
uv run --with tiktoken==0.12.0 python3 experiments/wrapper_bench.py model
uv run --with tiktoken==0.12.0 python3 experiments/wrapper_bench.py replay --replay-stdin --mode pwsh-docker --case ordinary --case json
uv run --with tiktoken==0.12.0 python3 experiments/wrapper_bench.py replay --replay-file
uv run --with tiktoken==0.12.0 python3 experiments/wrapper_bench.py model --free-carrier
uv run --locked --script experiments/task_curves.py experiments/results/wrapper-model.json experiments/results/wrapper-model.svg
uv run --locked --script experiments/task_curves.py experiments/results/wrapper-replay.json experiments/results/wrapper-replay.svg
uv run --locked --script experiments/task_curves.py experiments/results/wrapper-free-model.json experiments/results/wrapper-free-model.svg
```

The model supplies decoded Bash code to ∅ne, or one PowerShell SSH or Docker invocation with literal arguments and an optional UTF-8 stdin field. The controller resolves the target alias and attaches stdin. The free-choice condition also permits writing and executing `job.sh` on the target. The file-carrier reference requires that file on every path and verifies its literal bytes. The PowerShell surface accepts the documented fixture commands. Each fixture requires identical source bytes, exit status, stdout and artifact. The model receives up to three attempts. Counters separate submitted `o200k_base` tokens, provider tokens and bytes at each transport boundary. The replay also measures the size of the actual Docker ExecCreate JSON body and its embedded `Cmd` array. MCP stdio, SSH traffic and the Docker daemon socket are separate observation points. The original timing and codec experiment follows below.

## Serialization and command launch

Use Linux x86-64, a local Docker Engine with cgroup v2, and Linux PowerShell. The published run used [PowerShell 7.6.6](https://github.com/PowerShell/PowerShell/releases/tag/v7.6.6). Install `pwsh` on your PATH or point `ONE_PWSH` at an existing executable. The runner reuses the pinned image below and creates two disposable containers with networking disabled. Node-RED is the image distribution, and the experiment only runs its Bash and sleep executables plus the mounted ∅ne binary.

```sh
docker pull nodered/node-red@sha256:e085d4a06591fd0aca2ea2a91364d27f1a0e31e885675f77935e10f95412a963
./experiments/run.sh serialization
```

Each path runs the same Bash compound command at three script sizes. A Bash builtin prints `EPOCHREALTIME` at the first statement. The driver subtracts its dispatch timestamp on the same host. Completion is timed separately through process exit or the final worker output and successful exit status. PowerShell runs both as a fresh process for each call and as a warmed process that still starts a fresh Docker CLI and container Bash per call. The Go driver calls the production worker protocol on a private Unix socket with an already-open Bash session. SSH, MCP, model inference, PowerShell command-string construction, and container/session setup are outside the launch timing.

Each mode and size has two warm-up calls, 30 timed calls, and five separate memory trials. Memory trials sample the combined RSS of the PowerShell process tree and the target container processes, or the ∅ne target processes, at 2 ms intervals. RSS can count shared pages multiple times and sampling can miss brief peaks. The benchmark driver, shared Docker daemon, and container runtime processes outside the target cgroups are excluded.

The codec measurements time single-quote escaping, PowerShell parsing, Docker Exec Create JSON encoding and decoding using the 29.9.2 request schema, and the production ∅ne request frame helpers using in-memory I/O. A batched `bash -n` run approximates Bash parsing cost on both paths and amortizes interpreter startup. The results record command and request sizes along with timings. Codec timings exclude socket I/O and cannot be added to reconstruct observed launch latency. The two paths also perform different process and filesystem work.

The original loopback and memory measurements are available locally under `experiments/results/*-original.*` when archived from the measurement machine. Fresh runs write separate files. Downloaded packages and executables live under `experiments/.work/`. Both directories are ignored by Git. The report contains the published measurements.

[Project report](../REPORT.md)
