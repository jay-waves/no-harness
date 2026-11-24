<p class="eyebrow">COMP7607A · NATURAL LANGUAGE PROCESSING PROJECT</p>

# ∅ne

## Lightweight remote execution for coding agents

<p class="caption">Yu, Jiawei · 3036809907</p>

Notes:
∅ne is pronounced “One.” The command is one. This deck presents the prototype and its evaluation.

---

<h1 class="chapter-title">Background</h1>

---

# One gateway for many devices and providers

<!-- diagram: gateway.d2 -->
<!-- alt: A robot rover, Android phone, Arduino board, and Raspberry Pi each connect directly to one unified gateway with three components: Network proxy, LLM gateway, and Logs. The gateway connects to Codex, Claude Code, Qwen, DeepSeek, and Gemini. -->

<p class="caption">One connection per device and provider gives N + M integrations instead of up to N × M pairings. Inspired by LLM gateways and network proxies such as Clash.</p>

Notes:
The N + M model counts one integration per device and provider. The prototype currently targets Linux over SSH. Other devices and providers illustrate the motivation and would need appropriate adapters.

---

# What agent harnesses cost

| Harness | Published install size | Reported RSS |
| --- | ---: | ---: |
| <img class="brand-icon" src="brand-icon:claude-code.png" alt=""> Claude Code | 356 MiB | 310.9 MB (cli) |
| <img class="brand-icon" src="brand-icon:codex.svg" alt=""> Codex CLI | 478 MiB | 123.4 MB (cli) |
| <img class="brand-icon" src="brand-icon:opencode.svg" alt=""> OpenCode | 241 MiB | 576.1 MB (cli + service) |
| <img class="brand-icon" src="brand-icon:grok.svg" alt=""> Grok Build | 199 MiB | 96.7 MB (cli) |
| <img class="brand-icon" src="brand-icon:deepseek.png" alt=""> DeepSeek Harness | 5.94 MiB (npm package) | 185.6 MB (cli) |
| <img class="brand-icon" src="brand-icon:nanobot.svg" alt=""> HKUDS nanobot | ≈161 MB (wheel) | ≈420 MB (gateway) |

Notes:
Install figures for Claude Code 2.1.327, Codex 0.170.0, and OpenCode 2.3.2 are image increments from Harness Resource Bench. Grok Build 1.0.92 is the Linux executable. DeepSeek 0.2.2-rc.5 is the main npm package alone. nanobot 0.4.2 is the unpacked wheel. Harness Perf Benchmark reports mean RSS for Claude Code 2.1.277, Codex 0.155.1, OpenCode v2 2.0.10, Grok Build 1.0.41, and DeepSeek 0.1.5-rc.2. The RSS benchmark sampled macOS process trees without browser or desktop clients. OpenCode used its standalone service mode. Grok and OpenCode ran in separate batches. The nanobot gateway measurement is from 0.1.5.post2.

---

<h1 class="chapter-title">Design</h1>

---

# Where should the agent run?

| Design | Agent deployment | State across calls |
| --- | --- | --- |
| Remote agents | Full agent and model API access in each target | Managed by each agent |
| Direct commands | One local agent invokes target commands | Fresh interpreter per invocation |
| Remote REPLs | One local agent and thin target workers | Live objects persist per target |

A remote agent repeats setup, authentication, and network configuration across targets. Direct commands centralize setup but may repeat imports and data loading.

Notes:
The direct baseline can still reuse SSH connections and running containers, batch commands, filter output, and save files.

---

# A full agent in every target

<!-- diagram: remote-agent.d2 -->
<!-- alt: The user connects to separate agents on SSH, Docker, and WSL targets. Each agent has its own context and Model API configuration. -->

<p class="caption">Each installation has its own task context, tools, and model API configuration.</p>

---

# Direct commands start new processes

<!-- diagram: direct-execution.d2 -->
<!-- alt: The user starts the local agent, which invokes SSH, docker exec, and wsl --exec through a PowerShell launcher. Python commands on the SSH host, Node.js commands in Docker, and shell commands in WSL each start a fresh process. -->

<p class="caption">In PowerShell, docker exec nlp bash -c 'node --version && hostname' passes through three stages. PowerShell parses the quoted argument, Docker encodes the argument list as JSON, and Bash interprets && in the container.</p>

Notes:
PowerShell passes the single-quoted script as one argument. The Docker CLI serializes the argument list in the Exec API request, and the Docker Engine decodes it. Bash reads the argument after -c as shell source and runs hostname only if node --version succeeds. See the PowerShell quoting rules (https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_quoting_rules) and the Docker exec reference (https://docs.docker.com/reference/cli/docker/container/exec/).

---

# Remote REPLs keep state across calls

<!-- diagram: architecture.d2 -->
<!-- alt: The user starts Codex on the controller, and Codex calls the ∅ne session manager over MCP. The illustrated targets run a Python REPL on the SSH host, a Node.js REPL in Docker, and a shell REPL in WSL. -->

<p class="caption">Each REPL keeps its own session state. The prototype supports Bash and Python. Node.js illustrates another runtime.</p>

Notes:
Model API access stays local. Workload traffic follows the target network. The profiles reuse target dependencies and available proxy or VPN connectivity. Code Mode can combine operations and keep large outputs available by handle.

---

# How ∅ne runs a code block

Codex calls the local MCP server. ∅ne forwards the request over SSH to its worker on the target.

| Step | What happens |
| --- | --- |
| Open | Choose Bash or Python and receive a session ID, version, and environment summary. |
| Execute | Send a code block to the same interpreter. The next block can reuse its state. |
| Read | Use the execution ID and a cursor to retrieve stdout and stderr. |
| Reconnect | Find the live session and continue where the previous connection left off. |

Notes:
The per-user worker owns the interpreter processes and listens on a private Unix socket. The SSH bridge can disconnect while the worker stays alive. Closing a session and opening a new one starts a fresh interpreter.

---

<h1 class="chapter-title">Experiments</h1>

---

# Experiment 1 · Controlled command-token baseline

<!-- chart: command-tokens -->

<p class="caption">Six scripted tasks, four questions each. All 48 answers passed. Independent commands used 35 tokens on either path.</p>

Notes:
Direct SSH reused the authenticated connection and started fresh Python for every question. Each SSH task used the cheaper of repeating its setup or saving and importing a helper. The persistent Python REPL submitted setup once. Both arms used identical expressions and answer formats. Token counts cover submitted Python only, measured with o200k_base. Connection tuples and interpreter PIDs were verified. The graph sums the cumulative counts across all six tasks after each question. Source: experiments/command_token_suite.py and REPORT.md, Experiment 1.

---

# Experiment 1 · 4.0% fewer command tokens

Three tasks, eight checkpoints each, two paired runs per task. The model extended its previous Python implementation as new requirements appeared.

| Task | SSH command tokens | REPL command tokens |
| --- | ---: | ---: |
| Ledger reversals and splits | 6,223 | 6,395 |
| Layered configuration | 8,345 | 7,800 |
| Telemetry corrections | 6,977 | 6,483 |
| **Total** | **21,545** | **20,678** |

<p class="caption">Each path passed 48/48 strict checkpoints, including regression tests. Protocol adapted from SlopCodeBench.</p>

Notes:
Same low-reasoning gpt-6.2-sol on both paths. SSH kept its connection and started fresh Python. ∅ne kept the Python interpreter. Both could save files. Command totals include retries. Provider input including cached tokens fell 3.2%, and output fell 4.7%. Both replicates agreed on the direction within each task. Source: REPORT.md, Extended Experiment 1.

---

# Experiment 2 · How the script reaches Bash

For the same **1,177-byte script**, sending the source on stdin reduced the Docker ExecCreate JSON body from **1,609 to 169 bytes**.

| Measured at the Docker daemon | Inline argument | Standard input |
| --- | ---: | ---: |
| Entire ExecCreate JSON body | 1,609 B | 169 B |
| Encoded Cmd array inside the body | 1,453 B | 13 B |
| Script bytes sent separately on stdin | 0 B | 1,177 B |

Across five scripts, the model used **449 command + stdin tokens** with ∅ne, **484** with PowerShell → SSH, and **489** with PowerShell → Docker. All 15 free-choice calls passed on the first attempt.

Notes:
The top table comes from fixed replays capturing the actual HTTP body at the local Docker socket. Cmd is part of that body. When stdin carries the script, its bytes still cross the transport. The token comparison comes from a separate live trial using gpt-6.2-sol at low reasoning. Both PowerShell paths chose stdin on all five fixtures, while ∅ne sent Bash directly. The model could also save and run a target script. These are submitted payload tokens, excluding the provider envelope. Source: REPORT.md, Model-generated PowerShell wrappers.

---

# Command launch and serialization on Linux

**∅ne cut median launch time by 95.4% and peak sampled RSS by 91.5%** compared with warm PowerShell and Docker exec in this Linux pilot.

| Execution path | Launch median ↓ | Launch p95 ↓ | Peak RSS ↓ |
| --- | ---: | ---: | ---: |
| Fresh PowerShell + Docker exec | 171.30 ms | 179.46 ms | 131.8 MiB |
| Warm PowerShell + Docker exec | 50.00 ms | 52.65 ms | 152.4 MiB |
| Warm ∅ne worker + Bash | 2.28 ms | 2.47 ms | 13.0 MiB |

<p class="caption">Same 1,073-byte Bash script. Launch ends at the first target statement. ∅ne uses a local worker socket. 30 latency and 5 memory trials per path.</p>

Notes:
PowerShell 7.6.6, Docker 29.9.2, Bash 5.3.9, one Fedora host. A warm PowerShell still launches Docker CLI and Bash for each call. ∅ne connects directly to its already-running worker, with no SSH or MCP overhead in this pilot. The memory peak is sampled at 2 ms and excludes shared Docker services. The separate PowerShell quote and parse measurements and Bash syntax checks are in REPORT.md. Isolated encode and decode took 8.8 µs for Docker’s JSON request and 10.9 µs for ∅ne’s frame. Codec microbenchmarks exclude live socket I/O. The headline measures the complete launch path. At 16,433 bytes, ∅ne's median launch rises to 18.66 ms.

---

# Per-target deployment footprint

| Unpacked footprint | Standalone Codex | ∅ne worker |
| --- | ---: | ---: |
| Added to each target | **478.09 MiB** | **9.17 MiB** |
| Total with Alpine, Python, and OpenSSH | 527.85 MiB | 58.94 MiB |
| Total with the C toolchain | 714.89 MiB | 245.97 MiB |

Four scripted checks passed through the worker. We read and wrote a file, compiled C, analyzed CSV data, and verified an SSH service configuration.

<p class="caption">The Codex bundle contains several static executables and optional voice libraries. Both targets use the same Alpine, Python, and task dependencies.</p>

Notes:
Unpacked Linux x86-64 footprint. Codex 0.170.0 official musl bundle versus a stripped static build of the ∅ne worker prototype. The Codex CLI, exec server, and Code Mode host are separate static binaries. The optional voice bundle includes 11.72 MiB of shared libraries and uses the glibc loader. The two task routes used deterministic scripts. Codex startup was checked separately. Docker carried the worker bridge. See REPORT.md for the executable and dependency breakdown.

---

# What takes up disk space on each target

<!-- chart: codex-bundle -->

<p class="caption">Python worker baseline: Alpine rootfs (musl), BusyBox, and Python. The measured 49.77 MiB base also includes OpenSSH.</p>

Notes:
The large figures above the bars are payload increments. The Alpine layer is included in both stacks and drawn at the same scale. The Python worker needs an Alpine rootfs, BusyBox, and Python, alongside the worker executable. The SSH deployment additionally needs an SSH server. Unpacked official Codex 0.170.0 Linux x86-64 musl bundle versus the stripped static ∅ne worker. The voice shared libraries include GLib, GStreamer, Opus, and glibc. See the next slide for the sampled runtime memory.

---

# Where the memory difference comes from

<!-- chart: memory-footprint -->

<p class="caption">Median proportional set size (PSS), five samples per setup on Alpine with the same Python workload.</p>

Notes:
Values are median proportional set size (PSS) by component from Linux procfs. Codex file-backed pages and anonymous pages are measured separately. The executable mapping is the dominant named mapping, and its PSS overlaps the page categories shown here. The Codex-side Python process is a separately launched workload control. The stack totals sum component medians. Python excludes the separately shown musl mappings. The target bridge and daemon are separate processes. Neither sampled process tree contained Node.js, glibc, or a live shell process. Rootfs size is measured separately.

---

# Three configurations, 216 planned runs

| Configuration | What runs in the target |
| --- | --- |
| Direct commands | Commands invoked via SSH, Docker, or WSL |
| Remote agent | The same agent with access to the same model API |
| Persistent remote REPLs | A thin worker controlled by the local agent |

24 checkable tasks × 3 repetitions × 3 configurations. Tasks cover file operations, iterative data analysis, debugging, and work across environments.

Notes:
Hold the model, tasks, tool capabilities, token budgets, and time budgets fixed. Use equivalent initial states and controlled services. Include independent commands and workloads that benefit from state reuse. Measure cold setup and warm execution separately.

---

# What changes cumulative input tokens?

| Replay conditions | Stage 1 | Stage 2 | Stage 3 |
| --- | ---: | ---: | ---: |
| Fresh Python, full output | 484 | 1,777 | 3,873 |
| Persistent Python, full output | 484 | 1,755 | 3,783 |
| Fresh Python, bounded output | 288 | 992 | 2,103 |
| Persistent Python, bounded output | 288 | 970 | 2,013 |

Bounded output saved **1,769 replay input tokens** on average. Persistence saved **90** by avoiding repeated setup code.

<p class="caption">Mean cumulative o200k_base input across 24 matched fixtures. All 96 fixed-code runs passed verification.</p>

Notes:
The full-output policy includes diagnostic records. Bounded output retains the answer line. Inventory and repair were zero-effect controls for persistence. Analysis saved 140 tokens and reconciliation saved 220 on average. The replay assumes two model requests per stage and retains history. Provider usage is measured separately.

---

# Published tasks and provider token use

| Model-chosen actions on DataBench | Fresh Python | Persistent ∅ne |
| --- | ---: | ---: |
| Correct answers across Forbes and Titanic | 6/6 | 6/6 |
| Provider input tokens, including cached | 106,439 | 106,496 |
| Visible tool result tokens | 138 | 138 |

Both paths returned compact results and used nearly the same provider input. Two HumanEval tasks also passed all replay checks with identical replay token counts across conditions.

<p class="caption">The DataBench model pilot covers two tables. A larger paired study is needed to estimate the difference in success rates.</p>

Notes:
Codex CLI 0.169.1, gpt-6.2-sol with low reasoning, 12 requests total. One Python action per question. Each question was revealed after the preceding action was run. Counts of cached tokens varied by request. HumanEval uses two published problems and their official tests against saved solution.py. The public subsets are adapted to staged tasks. For the larger comparison, cluster confidence intervals by task and set the non-inferiority margin at five percentage points before collecting data.

---

<h1 class="chapter-title">Conclusion</h1>

---

# Project contribution and related work

**∅ne** keeps Bash and Python sessions alive across SSH reconnects. The command is `one`.

Evolving Python tasks used **4.0% fewer command tokens** with a persistent REPL. Worker deployment **9.17 MiB per target**, versus **478.09 MiB** for the full Codex CLI bundle.

| Related work | Public release |
| --- | --- |
| [DeepSeek Harness SSH](https://github.com/deepseek-ai/deepseek-harness/releases/tag/dsh-v0.2.1-alpha.2) | Oct. 9, 2026 · v0.2.1-alpha.2. Code Mode over SSH through a custom profile. |
| [Codex exec-server](https://github.com/openai/codex/releases/tag/rust-v0.170.0) | Nov. 18, 2026 · v0.170.0 **[Experimental]**. Released after the course proposal took shape. |

<p class="caption">Lightweight remote execution is an emerging design direction. Broad adoption has yet to be established.</p>

Notes:
The deployment figures compare the worker with the full Codex CLI bundle. The proposal milestone refers to when the course idea took shape.

---

# References

- [Harness Resource Bench, run manifest and raw summary](https://github.com/leonardcser/harness-resource-bench/tree/main/results/2026-11-19)
- [Harness Perf Benchmark, process-tree RSS results](https://github.com/KonghaYao/harness-perf-benchmark#benchmark-results)
- [Session-Bench v1](https://jazzyalex.github.io/agent-sessions/blog/session-bench-v1/), two-prompt fix. Claude Code 113 KB, Codex 185 KB, DeepSeek 193 KB, OpenCode 235 KB.
- Grok Build · [Linux release binary](https://x.ai/cli/grok-1.0.92-linux-x86_64) · [Session log report](https://github.com/farion1231/cc-switch/issues/6660)
- DeepSeek Harness · [npm package metadata](https://registry.npmjs.org/@deepseek-ai/dsh/0.2.2-rc.5) · [Web server RSS report](https://github.com/deepseek-ai/deepseek-harness/discussions/13639)
- HKUDS nanobot · [PyPI release files](https://pypi.org/project/nanobot-ai/0.4.2/#files) · [History storage](https://github.com/HKUDS/nanobot/blob/main/docs/memory.md) · [Gateway RSS profile](https://github.com/HKUDS/nanobot/issues/3410)
- Remote execution · [Codex 0.170.0](https://github.com/openai/codex/releases/tag/rust-v0.170.0) · [DeepSeek Harness SSH subsystem](https://github.com/deepseek-ai/deepseek-harness/blob/2e39e70496aa02986ca6a820c5d497e816b69d7e/docs/subsystems/ssh.md)
- Orlanski et al. (2026). [SlopCodeBench](https://arxiv.org/abs/2603.24755), evolving specifications and checkpoint regression tests.
