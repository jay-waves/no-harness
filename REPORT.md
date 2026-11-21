# Lightweight Remote Execution for a Local LLM Agent

COMP7607A Natural Language Processing Project

Yu, Jiawei · 3036809907

## Research question

Can one local Codex instance operate remote environments through thin workers with less per-target deployment overhead? When does a persistent shell or Python session improve remote execution over running each command in a fresh process?

The [proposal](proposal.md) describes the broader design for SSH, Docker, and WSL. This report records public harness measurements and the planned SSH comparison. The current prototype supports persistent Bash sessions, execution IDs, bounded output reads, reconnecting to live sessions, and SFTP. The persistent Python worker and project benchmarks are pending. [SSH MCP implementation](ssh-mcp/README.md)

## Public harness resource measurements

Harness Resource Bench ran all three CLI harnesses below in the same Linux environment on November 19, 2026. Installation is the added size of the container image. Memory is proportional set size (PSS), which apportions shared pages across processes. Startup memory comes from the headless startup case, and the 64-turn checkpoint comes from a separate interactive terminal (TUI) case. Session storage includes transcripts, databases, write-ahead logs (WAL), and related files. The rate is the difference between the reported median sizes at turns 4 and 64 divided by 60. [1]

| Harness | Install | Startup PSS | Turn 64 PSS | Session storage, turn 4 → 64 | Net growth |
| --- | ---: | ---: | ---: | ---: | ---: |
| Claude Code 2.1.327 | 356 MiB | 189 MiB | 480 MiB | 0.4 → 23.1 MiB | 386 KiB/turn |
| Codex 0.170.0 | 478 MiB | 121 MiB | 438 MiB | 8.1 → 59.4 MiB | 875 KiB/turn |
| OpenCode 2.3.2 | 241 MiB | 350 MiB | 691 MiB | 8.6 → 17.1 MiB | 145 KiB/turn |

Session-Bench v1 provides a shorter workload. A two-prompt fix averaged 113 KB for Claude Code, 185 KB for Codex, 193 KB for DeepSeek Harness, and 235 KB for OpenCode across three runs per harness. Its accounting can include rows attributable to one session in a shared database. The rates above describe the specific 64-turn workload. [1, 2]

| Harness | Public disk evidence | Public session evidence | Public memory evidence |
| --- | --- | --- | --- |
| Grok Build 1.0.92 | 199 MiB Linux x86-64 executable, from HTTP Content-Length [3] | One roughly 50 MiB `updates.jsonl` report without a turn count or duration [4] | No comparable figure found |
| DeepSeek Harness | 5.94 MiB unpacked for the `@deepseek-ai/dsh` 0.2.2-rc.5 package alone, excluding dependencies and Node.js [5] | 193 KB per two-prompt fix. One reported compressed long session reached 68.6 MB. [2, 6] | `dsh web` 0.2.2-rc.3 user report of 470–700 MB server RSS after restart and 2.0–2.1 GiB RSS plus swap after use [6] |
| HKUDS nanobot | 0.4.2 Linux x86-64 wheel is 60.8 MB compressed and about 161 MB unpacked, excluding Python and dependencies. The unpacked size sums the wheel directory entries. [7] | Session histories and summary archives. No public per-turn rate found. [8] | Maintainer measured about 420 MB RSS for a 0.1.5.post2 gateway. No comparable figure found for 0.4.2. [9] |

### Interpreting memory

The DeepSeek figure covers the Web backend server process. Browser memory would be a separate measurement. The report includes a long-session restoration workload, which can raise server memory. The nanobot figure covers an older gateway process. These RSS observations use different workloads and process boundaries from the CLI benchmark's PSS figures. RSS counts resident pages in full, including shared pages. RSS plus swap also includes memory that is no longer resident. [1, 6, 9]

npm is a distribution method. Node.js, loaded dependencies, the selected frontend or server mode, and retained session state can affect runtime memory. These sources do not isolate the contribution of any one factor. The same CLI benchmark also reports a separate one-turn headless workload, which is a closer reference for scripted execution. [1, 5, 6]

## Planned SSH comparison

| Execution mode | State available to the next step |
| --- | --- |
| Single-step execution | Files and other explicit external state from prior commands |
| Persistent Bash | Working directory, shell variables, and functions |
| Persistent Python | Imported modules, loaded data, and intermediate Python objects |

Start with a one-shot file check and a three-turn analysis of the same remote CSV. Keep the target, task inputs, and output policy fixed. In the CSV task, all three modes use equivalent Python calculations. Measure correctness, response time, and data-load count. Record target disk and memory for the worker plus its interpreter and retained data. The published harness numbers motivate the comparison. The project's own measurements will establish the worker footprint and whether persistence pays off for the chosen tasks.

## References

1. Leonard Cser. *Harness Resource Bench*, November 19, 2026. [Method and code](https://github.com/leonardcser/harness-resource-bench) · [Run manifest](https://github.com/leonardcser/harness-resource-bench/blob/main/results/2026-11-19/manifest.json) · [Raw summary](https://github.com/leonardcser/harness-resource-bench/blob/main/results/2026-11-19/summary.json)
2. *Session-Bench v1*. [Task and session storage measurements](https://jazzyalex.github.io/agent-sessions/blog/session-bench-v1/)
3. xAI. *Grok Build 1.0.92*. [Package metadata](https://formulae.brew.sh/api/cask/grok-build.json) · [Linux x86-64 release binary](https://x.ai/cli/grok-1.0.92-linux-x86_64)
4. *Grok Build session storage*. [Official session documentation](https://github.com/xai-org/grok-build/blob/main/crates/codegen/xai-grok-pager/docs/user-guide/17-sessions.md) · [Session-size report](https://github.com/farion1231/cc-switch/issues/6660)
5. DeepSeek. [`@deepseek-ai/dsh` 0.2.2-rc.5 registry metadata](https://registry.npmjs.org/@deepseek-ai/dsh/0.2.2-rc.5)
6. DeepSeek Harness. [Web server memory and session-size report](https://github.com/deepseek-ai/deepseek-harness/discussions/13639)
7. HKUDS. [nanobot-ai 0.4.2 release files](https://pypi.org/project/nanobot-ai/0.4.2/#files)
8. HKUDS. [nanobot history and memory documentation](https://github.com/HKUDS/nanobot/blob/main/docs/memory.md)
9. HKUDS. [Maintainer profile of the nanobot 0.1.5.post2 gateway](https://github.com/HKUDS/nanobot/issues/3410)
