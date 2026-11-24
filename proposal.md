# ∅ne: Lightweight Remote Execution for a Local LLM Agent

*COMP7607A Natural Language Processing Project*

## Team Information

Yu, Jiawei — 3036809907

## Goal

Build a framework that lets one local LLM agent operate SSH hosts, Docker containers, and WSL environments through persistent REPLs. Each target needs only a language runtime and a small worker. The goals are lower overhead, comparable task success, and easier environment configuration.

## Motivation and Related Work

Operating across containers and remote machines commonly follows two patterns: install an agent in each target, or keep the agent local and invoke remote commands. Both provide access to the workload, but place different costs on deployment and execution.

### An agent in every environment

Each target runs its own agent, with direct access to local files and tools. This avoids wrapping each action in a remote command, but requires agent installation, updates, authentication, and model API access in every environment. Container rebuilds must preserve or repeat this setup. Restricted networks may require additional proxy or VPN configuration, while switching between agents can fragment task context unless it is explicitly shared.

[Separate agent installations in SSH, Docker, and WSL environments each require model API access.](remote-agent.mmd)

The model can still be hosted by an external API; the duplicated components are the agent software and its configuration. Our design keeps these on the local machine and deploys only a small execution worker to each target.

### A local agent invoking remote commands

A local agent can instead use SSH, `docker exec`, or `wsl --exec` through a local shell such as PowerShell. This centralizes agent configuration, but each invocation launches a command process. When each call starts a fresh interpreter, imports, loaded data, and other in-memory objects must be reconstructed. Wrapper syntax, quoting, and environment selection also become part of the agent's generated commands.

[A local agent uses a PowerShell launcher to invoke separate command processes in SSH, Docker, and WSL environments.](direct-execution.mmd)

For example, `docker exec nlp python3 sum.py` followed by `docker exec nlp python3 mean.py` starts two interpreters. In this example, each script loads the same dataset. The dotted arrows show successive invocations, not shared memory. SSH and WSL follow the same pattern.

Intermediate outputs can also enlarge the model context. For repeated questions about a dataset, the agent may reload the file, return tables, and carry results into later turns. Batching, output filtering, and saved files reduce these costs, but do not by themselves preserve live interpreter objects. The opportunity is to reuse both execution state and data across turns, returning only what the model needs.

### Code execution and persistent sessions

CodeAct uses executable code as an action interface [1], Code Mode composes tool operations in code [2], and smolagents supports remote code executors [3]. Building on these ideas, this project combines one local agent with lightweight persistent sessions across environments. The contribution is a practical design and evaluation of deployment cost, execution and context efficiency, and task quality.

## Approach

One local agent will manage persistent REPLs through a shared interface. Connections and interpreters will be reused; intermediate objects will stay in the target environment.

[One local agent manages persistent Python REPL sessions in SSH, Docker, and WSL environments.](architecture.mmd)

After a one-time `import pandas as pd`, turn 1 runs `df = pd.read_csv('data.csv')` and `df['score'].sum()`. Turn 2 runs only `df['score'].mean()`. Each target retains its own `df`; only the requested statistics return to the agent.

A common interface will open sessions, execute code, retrieve output, interrupt work, and reset state. SSH, Docker, and WSL adapters will establish reusable channels to workers. Each target will have its own session and a profile specifying its runtime, working directory, and environment settings.

The prototype will use Python REPLs with shell subprocess support. Code Mode will combine operations, retain intermediate objects, and return selected results. Large outputs will remain accessible by handle. Shell subprocesses will use explicit working directories and environment settings.

Execution IDs will allow status checks and output retrieval after reconnecting to a live worker, avoiding blind retries. Kernel restarts will invalidate stored objects. Container permissions and resource limits will bound execution in controlled tests.

Profiles will reuse target dependencies and available proxy or VPN connectivity. Model API access stays local; workload traffic follows the target network. A local VPN is not assumed to extend to remote hosts or containers.

The three designs differ in where the agent lives and what survives between calls:

| Design | Agent deployment | Execution state | Main tradeoff |
| --- | --- | --- | --- |
| Remote agents | Full agent in every target | Managed by each agent | Direct local tools, but repeated setup and separate contexts. |
| Direct commands | One local agent | Fresh interpreter per invocation | Simple access, but repeated initialization and command wrapping. |
| Remote REPLs | One local agent plus thin workers | Objects persist per target | Shared agent context and reusable data, with session management. |

## Evaluation

We will compare three configurations using the same model, tasks, tool capabilities, and token and time budgets.

| Configuration | Execution |
| --- | --- |
| Direct commands | Local agent invokes SSH, Docker, or WSL commands. |
| Remote agent | The same agent runs inside each target and calls the same model API. |
| Proposed framework | Local agent controls persistent remote REPLs. |

The direct baseline may reuse SSH connections and running containers, batch commands, filter output, and save files. Cold setup and warm execution will be measured separately.

We will run 24 checkable tasks three times per configuration: 216 runs covering file operations, iterative data analysis, debugging, and multiple environments. Tasks will include independent commands and workloads that benefit from state reuse, with equivalent initial states and controlled services.

| Goal | Measurements |
| --- | --- |
| Lower overhead | Installation size, remote memory, startup and warm-call latency, task duration, and transferred bytes. |
| Less context use | Command tokens, tool-output tokens, cumulative model input tokens, and agent turns. |
| Comparable quality | Success verified by assertions and final environment state under fixed budgets. |
| Easier configuration | Setup time, manual steps, and configuration changes, including proxy-required targets and targets without model API access. |

Fixed-code benchmarks will isolate process startup and data-loading costs from model latency. Disabling persistence while retaining Code Mode will separate state reuse from batching. We will vary follow-up count and data size to measure context growth.

The first task pilots vary interpreter persistence and tool-output size independently. File-backed tasks provide a control for state that survives either mode. Repeated questions over DataBench tables test reuse of loaded data, and small HumanEval cases check generated code against published tests. The task verifier checks artifacts and regressions at each configuration. Replay token counts test a fixed transcript layout. A matched model pilot records provider input, cached input, and output tokens to measure usage when the model chooses its own code. [Pilot results](REPORT.md#task-quality-and-context-growth)

A separate two-target pilot compares direct SSH, direct Docker exec, and ∅ne MCP over SSH with one local model. It uses shared task fixtures and reports provider tokens, verified outcomes, and target action time alongside controller wall time. The full agent installed on each target remains part of the planned comparison. [Two-target pilot](REPORT.md#agent-tasks-over-ssh-docker-and-∅ne)

The telemetry follow-up study adds a matched upfront control and a seven-checkpoint workflow with a missing parameter and a late data revision. Its primary measurements are model tokens, sampled target and controller RSS, and bytes at each transport boundary. [Telemetry benchmark](REPORT.md#progressively-disclosed-telemetry-benchmark)

Experiment 1 compares submitted Python tokens on multiplexed SSH with fresh interpreters and on a persistent remote Python REPL across six four-question tasks. [Task-level measurements and curves](REPORT.md#experiment-1-command-tokens-over-ssh-and-a-persistent-python-repl)

The repeated-query supplement measures logical source and cache I/O alongside target RSS, using the same joined representation in memory and on disk. A separate PowerShell supplement measures how the choice of inline arguments or stdin changes model-generated command tokens and the actual Docker ExecCreate JSON body. [Repeated queries](REPORT.md#repeated-queries-against-a-remote-join) · [PowerShell wrappers](REPORT.md#model-generated-powershell-wrappers)

Success-rate differences will use paired comparisons with confidence intervals clustered by task. A five-percentage-point non-inferiority margin will be fixed in advance; inconclusive results will not establish equal quality. Failure analysis will cover stale state, disconnects, timeouts, and resets.

## Deliverables

A working prototype, reproducible tasks, and an evaluation showing when persistent remote execution reduces execution and context costs while preserving task success and simplifying setup.

## References

1. Wang et al. *Executable Code Actions Elicit Better LLM Agents*. ICML 2024. <https://arxiv.org/abs/2402.01030>
2. Cloudflare. *Code Mode*. <https://developers.cloudflare.com/agents/tools/codemode/>
3. Hugging Face. *Secure code execution in smolagents*. <https://huggingface.co/docs/smolagents/en/tutorials/secure_code_execution>
