<p class="eyebrow">COMP7607A · NATURAL LANGUAGE PROCESSING PROJECT</p>

# Lightweight Remote Execution

## Persistent Code Mode sessions for a local LLM agent

<p class="caption">Yu, Jiawei · 3036809907</p>

Notes:
This deck presents the proposed design and evaluation from proposal.md.

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

<!-- diagram: remote-agent.mmd -->

<p class="caption">Each installation has its own task context, tools, and model API configuration.</p>

---

# Resource cost of three CLI harnesses

| Harness | Install | PSS, start → 64 turns | Session data at 64 turns | Growth per turn |
| --- | ---: | ---: | ---: | ---: |
| Claude Code 2.1.327 | 356 MiB | 189 → 480 MiB | 23.1 MiB | 386 KiB |
| Codex 0.170.0 | 478 MiB | 121 → 438 MiB | 59.4 MiB | 875 KiB |
| OpenCode 2.3.2 | 241 MiB | 350 → 691 MiB | 17.1 MiB | 145 KiB |

Linux benchmark. Startup PSS is headless. Turn 64 is an interactive terminal checkpoint. Session growth is the net change from turns 4 to 64, including databases and logs.

[Harness Resource Bench, Nov. 19, 2026](https://github.com/leonardcser/harness-resource-bench/tree/main/results/2026-11-19) · [Method](https://github.com/leonardcser/harness-resource-bench)

---

# Public data for three more harnesses

| Harness | Package footprint | Session storage | Memory |
| --- | --- | --- | --- |
| Grok Build | 199 MiB Linux executable | ≈50 MiB in one long session | No comparable figure found |
| DeepSeek Harness | 5.94 MiB unpacked main npm package | 193 KB per short fix | Web server, 470–700 MB RSS after restart |
| HKUDS nanobot | 60.8 MB wheel, ≈161 MB unpacked | No per-turn figure found | Older gateway, ≈420 MB RSS |

Package figures exclude external dependencies. Web server RSS excludes the browser. npm describes distribution. Runtime memory can depend on Node.js, loaded dependencies, execution mode, and retained history.

Notes:
Grok Build 1.0.92 executable size comes from the official Linux x86-64 release Content-Length. The long-session log is one issue report without a turn count. DeepSeek disk data is from 0.2.2-rc.5 and the Web memory case is from 0.2.2-rc.3. That case reported 2.0–2.1 GiB RSS plus swap after use, with a 68.6 MB compressed session. The nanobot wheel is 0.4.2 and the gateway RSS is from 0.1.5.post2. PSS apportions shared pages, while RSS counts them in full. These observations use different workloads and process boundaries.

---

# Sources for public resource data

- [Harness Resource Bench, run manifest and raw summary](https://github.com/leonardcser/harness-resource-bench/tree/main/results/2026-11-19)
- [Session-Bench v1](https://jazzyalex.github.io/agent-sessions/blog/session-bench-v1/), two-prompt fix. Claude Code 113 KB, Codex 185 KB, DeepSeek 193 KB, OpenCode 235 KB.
- Grok Build · [Linux release binary](https://x.ai/cli/grok-1.0.92-linux-x86_64) · [Session log report](https://github.com/farion1231/cc-switch/issues/6660)
- DeepSeek Harness · [npm package metadata](https://registry.npmjs.org/@deepseek-ai/dsh/0.2.2-rc.5) · [Web server RSS report](https://github.com/deepseek-ai/deepseek-harness/discussions/13639)
- HKUDS nanobot · [PyPI release files](https://pypi.org/project/nanobot-ai/0.4.2/#files) · [History storage](https://github.com/HKUDS/nanobot/blob/main/docs/memory.md) · [Gateway RSS profile](https://github.com/HKUDS/nanobot/issues/3410)

---

# Direct commands start new processes

<!-- diagram: direct-execution.mmd -->

<p class="caption">Separate interpreter invocations reload the dataset to compute the sum and mean.</p>

---

# Remote REPLs reuse the dataset

<!-- diagram: architecture.mmd -->

<p class="caption">The local agent receives the sum and mean. Each target keeps its own DataFrame.</p>

Notes:
Model API access stays local. Workload traffic follows the target network. The profiles reuse target dependencies and available proxy or VPN connectivity. Code Mode can combine operations and keep large outputs available by handle.

---

# Session behavior across calls

| When | Proposed behavior |
| --- | --- |
| Open a session | Reuse a target channel and apply its runtime, working directory, and environment profile |
| Execute code | Run Python or shell subprocesses with explicit working directories and environment settings |
| Reconnect | Check the execution ID and retrieve output from a live worker |
| Interrupt or reset | Interrupt work through the shared interface; invalidate stored objects after a kernel restart |

<p class="caption">Controlled tests will bound execution with container permissions and resource limits.</p>

Notes:
Execution IDs enable status and output retrieval after reconnect, avoiding blind retries.

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

# Prior work and project deliverables

| Prior work | Connection to the design |
| --- | --- |
| [CodeAct](https://arxiv.org/abs/2402.01030) | Executable code as an action interface |
| [Code Mode](https://developers.cloudflare.com/agents/tools/codemode/) | Tool operations composed in code |
| [smolagents](https://huggingface.co/docs/smolagents/en/tutorials/secure_code_execution) | Remote code executors |

The project will deliver a prototype, reproducible tasks, and an evaluation of when persistent remote execution reduces cost while preserving success and simplifying setup.

Notes:
The evaluation and prototype are proposed deliverables.
