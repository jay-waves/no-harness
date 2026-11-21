<p class="eyebrow">COMP7607A · NATURAL LANGUAGE PROCESSING PROJECT</p>

# Lightweight Remote Execution

## Persistent Code Mode sessions for a local LLM agent

<p class="caption">Yu, Jiawei · 3036809907</p>

Notes:
This deck presents the proposed design and evaluation from proposal.md.

---

<h1 class="chapter-title">Background</h1>

---

# One gateway for many devices and providers

<!-- diagram: gateway.d2 -->
<!-- alt: Robots, Android phones, Arduino boards, and Raspberry Pis connect to one unified gateway with three components: Network proxy, LLM gateway, and Logs. The gateway connects to Codex, Claude Code, OpenCode, DeepSeek, and network proxy providers. -->

<p class="caption">One connection per device and provider gives N + M integrations instead of up to N × M pairings. Inspired by LLM gateways and network proxies such as Clash.</p>

Notes:
The N + M model counts one integration per device and provider. The prototype currently targets Linux over SSH. Other devices and providers illustrate the motivation and would need appropriate adapters.

---

# Resource cost of three CLI harnesses

| Harness | Install | PSS, start → 64 turns | Session data at 64 turns | Growth per turn |
| --- | ---: | ---: | ---: | ---: |
| <img class="brand-icon" src="brand-icon:claude-code.png" alt=""> Claude Code 2.1.327 | 356 MiB | 189 → 480 MiB | 23.1 MiB | 386 KiB |
| <img class="brand-icon" src="brand-icon:codex.svg" alt=""> Codex 0.170.0 | 478 MiB | 121 → 438 MiB | 59.4 MiB | 875 KiB |
| <img class="brand-icon" src="brand-icon:opencode.svg" alt=""> OpenCode 2.3.2 | 241 MiB | 350 → 691 MiB | 17.1 MiB | 145 KiB |

Linux benchmark. Startup PSS is headless. Turn 64 is an interactive terminal checkpoint. Session growth is the net change from turns 4 to 64, including databases and logs.

[Harness Resource Bench, Nov. 19, 2026](https://github.com/leonardcser/harness-resource-bench/tree/main/results/2026-11-19) · [Method](https://github.com/leonardcser/harness-resource-bench)

---

# Public data for three more harnesses

| Harness | Package footprint | Session storage | Memory |
| --- | --- | --- | --- |
| <img class="brand-icon" src="brand-icon:grok.svg" alt=""> Grok Build | 199 MiB Linux executable | ≈50 MiB in one long session | No comparable figure found |
| <img class="brand-icon" src="brand-icon:deepseek.png" alt=""> DeepSeek Harness | 5.94 MiB unpacked main npm package | 193 KB per short fix | Web server, 470–700 MB RSS after restart |
| <img class="brand-icon" src="brand-icon:nanobot.svg" alt=""> HKUDS nanobot | 60.8 MB wheel, ≈161 MB unpacked | No per-turn figure found | Older gateway, ≈420 MB RSS |

Package figures exclude external dependencies. Web server RSS excludes the browser. npm describes distribution. Runtime memory can depend on Node.js, loaded dependencies, execution mode, and retained history.

Notes:
Grok Build 1.0.92 executable size comes from the official Linux x86-64 release Content-Length. The long-session log is one issue report without a turn count. DeepSeek disk data is from 0.2.2-rc.5 and the Web memory case is from 0.2.2-rc.3. That case reported 2.0–2.1 GiB RSS plus swap after use, with a 68.6 MB compressed session. The nanobot wheel is 0.4.2 and the gateway RSS is from 0.1.5.post2. PSS apportions shared pages, while RSS counts them in full. These observations use different workloads and process boundaries.

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
<!-- alt: The user starts the local agent, which uses SSH, docker exec, and wsl --exec. Python commands on the SSH host, Node.js commands in Docker, and shell commands in WSL each start a fresh process. -->

<p class="caption">Each command starts a fresh process. Later commands reload data they need again.</p>

---

# Remote REPLs keep state across calls

<!-- diagram: architecture.d2 -->
<!-- alt: The user starts Codex on the controller, and Codex calls a session manager over MCP. The illustrated targets run a Python REPL on the SSH host, a Node.js REPL in Docker, and a shell REPL in WSL. -->

<p class="caption">Each REPL keeps its own session state. The prototype supports Bash and Python. Node.js illustrates another runtime.</p>

Notes:
Model API access stays local. Workload traffic follows the target network. The profiles reuse target dependencies and available proxy or VPN connectivity. Code Mode can combine operations and keep large outputs available by handle.

---

# How ssh-mcp runs a code block

Codex calls a local MCP server. An SSH bridge forwards the request to a worker on the target.

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

# Persistent Python cuts repeated CSV work

| Warm loopback SSH | CSV parses | Three turns, including a new REPL where applicable |
| --- | ---: | ---: |
| Exec once | 3 | 113.98 ms |
| Bash REPL | 3 | 117.22 ms |
| Python REPL | 1 | 61.02 ms |

<p class="caption">Median of five trials per mode. All three used the same Python parser and calculations.</p>

---

# Per-target deployment footprint

| Unpacked footprint | Standalone Codex | SSH MCP worker |
| --- | ---: | ---: |
| Added to each target | **478.09 MiB** | **9.17 MiB** |
| Total with Alpine, Python, and OpenSSH | 527.85 MiB | 58.94 MiB |
| Total with the C toolchain | 714.89 MiB | 245.97 MiB |

Four scripted checks passed through the worker. We read and wrote a file, compiled C, analyzed CSV data, and verified an SSH service configuration.

<p class="caption">The Codex bundle contains several static executables and optional voice libraries. Both targets use the same Alpine, Python, and task dependencies.</p>

Notes:
Unpacked Linux x86-64 footprint. Codex 0.170.0 official musl bundle versus a stripped static ssh-mcp 0.2.0 build. The Codex CLI, exec server, and Code Mode host are separate static binaries. The optional voice bundle includes 11.72 MiB of shared libraries and uses the glibc loader. The two task routes used deterministic scripts. Codex startup was checked separately. Docker carried the worker bridge. See REPORT.md for the executable and dependency breakdown.

---

# What takes up disk space on each target

<!-- chart: codex-bundle -->

<p class="caption">Python worker baseline: Alpine rootfs (musl), BusyBox, and Python. The measured 49.77 MiB base also includes OpenSSH.</p>

Notes:
The large figures above the bars are payload increments. The Alpine layer is included in both stacks and drawn at the same scale. The Python worker needs an Alpine rootfs, BusyBox, and Python, alongside the worker executable. The SSH deployment additionally needs an SSH server. Unpacked official Codex 0.170.0 Linux x86-64 musl bundle versus the stripped static ssh-mcp worker. The voice shared libraries include GLib, GStreamer, Opus, and glibc. See the next slide for the sampled runtime memory.

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

# Measure execution, context, and setup costs

| Goal | Measurements |
| --- | --- |
| Lower overhead | Installation size, target memory, startup and warm-call latency, task duration, transferred bytes |
| Less context use | Command and tool-output tokens, cumulative model input tokens, agent turns |
| Comparable quality | Assertions and final environment state under fixed budgets |
| Easier configuration | Setup time, manual steps, configuration changes |

Notes:
Include proxy-required targets and targets without model API access. These are planned measurements.

---

# Isolate the effect of persistent state

- Use fixed-code benchmarks to separate startup and data loading costs from model latency.
- Disable persistence while retaining Code Mode to separate state reuse from batching.
- Vary follow-up count and dataset size to measure context growth.
- Review stale state, disconnects, timeouts, and resets.

<p class="takeaway">Compare success rates in pairs, cluster confidence intervals by task, and fix a five-percentage-point non-inferiority margin in advance.</p>

Notes:
An inconclusive comparison leaves comparable quality unestablished.

---

<h1 class="chapter-title">Conclusion</h1>

---

# Project contribution and related work

**ssh-mcp** keeps Bash and Python sessions alive across SSH reconnects.

Three-turn CSV task **113.98 → 61.02 ms**. Worker deployment **9.17 MiB per target**, versus **478.09 MiB** for the full Codex CLI bundle.

| Related work | Public release |
| --- | --- |
| [DeepSeek Harness SSH](https://github.com/deepseek-ai/deepseek-harness/releases/tag/dsh-v0.2.1-alpha.2) | Oct. 9, 2026 · v0.2.1-alpha.2. Code Mode over SSH through a custom profile. |
| [Codex exec-server](https://github.com/openai/codex/releases/tag/rust-v0.170.0) | Nov. 18, 2026 · v0.170.0 **[Experimental]**. Released after the course proposal took shape. |

<p class="caption">Lightweight remote execution is an emerging design direction. Broad adoption has yet to be established.</p>

Notes:
The CSV pilot used warm loopback SSH. The deployment figures compare the worker with the full Codex CLI bundle. The proposal milestone refers to when the course idea took shape.

---

# References

- [Harness Resource Bench, run manifest and raw summary](https://github.com/leonardcser/harness-resource-bench/tree/main/results/2026-11-19)
- [Session-Bench v1](https://jazzyalex.github.io/agent-sessions/blog/session-bench-v1/), two-prompt fix. Claude Code 113 KB, Codex 185 KB, DeepSeek 193 KB, OpenCode 235 KB.
- Grok Build · [Linux release binary](https://x.ai/cli/grok-1.0.92-linux-x86_64) · [Session log report](https://github.com/farion1231/cc-switch/issues/6660)
- DeepSeek Harness · [npm package metadata](https://registry.npmjs.org/@deepseek-ai/dsh/0.2.2-rc.5) · [Web server RSS report](https://github.com/deepseek-ai/deepseek-harness/discussions/13639)
- HKUDS nanobot · [PyPI release files](https://pypi.org/project/nanobot-ai/0.4.2/#files) · [History storage](https://github.com/HKUDS/nanobot/blob/main/docs/memory.md) · [Gateway RSS profile](https://github.com/HKUDS/nanobot/issues/3410)
- Remote execution · [Codex 0.170.0](https://github.com/openai/codex/releases/tag/rust-v0.170.0) · [DeepSeek Harness SSH subsystem](https://github.com/deepseek-ai/deepseek-harness/blob/2e39e70496aa02986ca6a820c5d497e816b69d7e/docs/subsystems/ssh.md)
