"""Pair model-generated PowerShell wrappers with direct ∅ne Bash code."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import random
import re
import shlex
import shutil
import subprocess
import tempfile
from time import monotonic

import tiktoken

from remote_bench import ROOT, USAGE_KEYS, model_action
from remote_targets import IMAGE, TIMEOUT, RemoteTargets, _DockerListener, _Relay

MODES = {"pwsh-ssh": "ssh", "one-ssh": "one", "pwsh-docker": "docker"}
HOST = "east"
ARTIFACT = "answer.txt"
SCRIPT_FILE = "job.sh"
WRITE_AND_RUN = f"cat > {SCRIPT_FILE} && bash {SCRIPT_FILE}"
MAX_ATTEMPTS = 3
_ANSWER = {"type": "string"}
SCHEMA = {
    "type": "object",
    "properties": {
        "done": {"type": "boolean"},
        "actions": {"type": "array", "items": {
            "type": "object",
            "properties": {key: _ANSWER for key in ("target", "runtime", "code", "stdin")},
            "required": ["target", "runtime", "code", "stdin"],
            "additionalProperties": False,
        }},
    },
    "required": ["done", "actions"],
    "additionalProperties": False,
}

# Evaluate one literal-only PowerShell call. The named functions launch the
# fixed fixture transport and target. Process.ArgumentList preserves native
# arguments, and its stdin BaseStream preserves the provided UTF-8 bytes.
POWERSHELL = r"""
$ErrorActionPreference = 'Stop'
$utf8 = [Text.UTF8Encoding]::new($false)
$request = [Console]::In.ReadToEnd() | ConvertFrom-Json
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseInput($request.code, [ref]$tokens, [ref]$errors)
if ($errors.Count -or $ast.ParamBlock -or $ast.BeginBlock -or $ast.ProcessBlock -or
    $ast.CleanBlock -or $ast.DynamicParamBlock -or $ast.UsingStatements.Count -or
    $ast.Attributes.Count -or $ast.EndBlock.Statements.Count -ne 1) {
    throw 'Provide exactly one literal ssh or docker call.'
}
$pipeline = $ast.EndBlock.Statements[0]
if ($pipeline -isnot [Management.Automation.Language.PipelineAst] -or
    $pipeline.PipelineElements.Count -ne 1 -or $pipeline.Background) {
    throw 'Use a separate stdin field instead of a pipeline.'
}
$call = $pipeline.PipelineElements[0]
$expected = if ($request.mode -eq 'pwsh-ssh') { 'ssh' } else { 'docker' }
if ($call -isnot [Management.Automation.Language.CommandAst] -or
    $call.GetCommandName() -cne $expected -or $call.Redirections.Count -or
    $call.InvocationOperator -ne [Management.Automation.Language.TokenKind]::Unknown) {
    throw "Use one literal $expected call."
}
foreach ($element in $call.CommandElements) {
    if ($element -is [Management.Automation.Language.StringConstantExpressionAst]) { continue }
    if ($element -is [Management.Automation.Language.CommandParameterAst] -and !$element.Argument -and
        $element.ParameterName -cin @('i', 's', 'c')) { continue }
    throw 'Use literal string arguments without variable interpolation.'
}
function ssh {
    $fileCommand = $request.file_command -and $args[1] -ceq $request.file_command
    if ($args.Count -ne 2 -or $args[0] -cne 'east' -or
        (!$fileCommand -and $args[1] -cne 'bash -s' -and $args[1] -cnotmatch '^bash -c ')) {
        throw 'Use ssh east followed by one literal Bash command string.'
    }
    Invoke-Fixture @($args[1])
}
function docker {
    if ($args.Count -lt 5 -or ($args[0..3] -join ' ') -cne 'exec -i east bash' -or
        !(($args.Count -eq 5 -and $args[4] -ceq '-s') -or ($args.Count -eq 6 -and $args[4] -ceq '-c'))) {
        throw 'Use docker exec -i east bash -s or docker exec -i east bash -c followed by a literal script.'
    }
    Invoke-Fixture @($args[4..($args.Count - 1)])
}
function Invoke-Fixture($variableArgs) {
    $native = @($request.prefix) + $variableArgs
    [IO.File]::WriteAllText($request.metrics, (ConvertTo-Json -Compress -InputObject @($variableArgs)), $utf8)
    $start = [Diagnostics.ProcessStartInfo]::new($request.program)
    foreach ($argument in $native) { $start.ArgumentList.Add([string]$argument) }
    $start.RedirectStandardInput = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.StandardOutputEncoding = $utf8
    $start.StandardErrorEncoding = $utf8
    $process = [Diagnostics.Process]::Start($start)
    try {
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        $payload = $utf8.GetBytes($request.stdin)
        $process.StandardInput.BaseStream.Write($payload, 0, $payload.Length)
        $process.StandardInput.BaseStream.Close()
        if (!$process.WaitForExit(10000)) {
            $process.Kill($true)
            throw 'Native call timed out.'
        }
        [Console]::Out.Write($stdout.Result)
        [Console]::Error.Write($stderr.Result)
        exit $process.ExitCode
    } finally {
        $process.Dispose()
    }
}
& ([ScriptBlock]::Create($request.code))
"""

HELP = {
    "one-ssh": "The code field is decoded Bash source, sent directly to a persistent Bash session over MCP and SSH. Set stdin to an empty string.",
    "pwsh-ssh": "The code field is a Linux PowerShell command: ssh east followed by one literal remote command string. "
                "Either use ssh east 'bash -s' and set stdin to the entire Bash script, or supply a literal "
                "remote command string containing bash -c plus the POSIX-quoted script and set stdin to empty.",
    "pwsh-docker": "The code field is a Linux PowerShell command: docker exec -i east bash followed by its Bash arguments. "
                   "Either use docker exec -i east bash -s and set stdin to the entire Bash script, or use "
                   "docker exec -i east bash -c followed by a PowerShell literal script and set stdin to empty.",
}


FILE_HELP = {
    "one-ssh": f"You may also write the exact source to {SCRIPT_FILE} on the target, for example with a quoted "
                f"Bash heredoc, then run bash {SCRIPT_FILE} in the same action.",
    "pwsh-ssh": f"You may also write and run a target script file by using ssh east '{WRITE_AND_RUN}' "
                "and attaching the exact Bash source as stdin.",
    "pwsh-docker": f"You may also write and run a target script file by using docker exec -i east bash -c "
                   f"'{WRITE_AND_RUN}' and attaching the exact Bash source as stdin.",
}


class _DockerRequestTap:
    """Retain controller-to-daemon bytes for one synthetic fixture connection."""

    def __init__(self, connection, capture):
        self.connection = connection
        self.capture = capture

    def fileno(self):
        return self.connection.fileno()

    def recv(self, size):
        chunk = self.connection.recv(size)
        self.capture.extend(chunk)
        return chunk

    def sendall(self, data):
        self.connection.sendall(data)

    def shutdown(self, how):
        self.connection.shutdown(how)


class _DockerRequestRelay(_Relay):
    def handle(self):
        stream = bytearray()
        self.server.request_streams.append(stream)
        self.request = _DockerRequestTap(self.request, stream)
        super().handle()


class WrapperTargets(RemoteTargets):
    def _serve(self, listener):
        if isinstance(listener, _DockerListener):
            listener.request_streams = []
            listener.RequestHandlerClass = _DockerRequestRelay
        return super()._serve(listener)

    def docker_exec_create(self):
        streams = self._docker_relay.request_streams
        for stream in streams:
            match = re.search(rb"POST /[^ ]+/containers/[^ /]+/exec HTTP/1\.1\r\n", stream)
            if not match:
                continue
            headers, _, body = stream[match.end():].partition(b"\r\n\r\n")
            length = re.search(rb"(?im)^Content-Length: ([0-9]+)\r?$", headers)
            if not length:
                continue
            body_length = int(length[1])
            payload = body[:body_length].decode()
            if len(body) < body_length:
                raise RuntimeError("incomplete captured Docker ExecCreate request")
            command = re.search(r'"Cmd"\s*:\s*', payload)
            if not command:
                raise RuntimeError("Docker ExecCreate request lacked a Cmd field")
            _, end = json.JSONDecoder().raw_decode(payload, command.end())
            return {
                "docker_exec_create_body_bytes": body_length,
                "docker_exec_create_cmd_json_bytes": byte_count(payload[command.end():end]),
            }
        raise RuntimeError("Docker ExecCreate request was absent from the relay capture")

def fixtures():
    settings = {
        "agent": "O'Brien",
        "path": "$HOME/config",
        "windows": r"C:\data\new",
        "label": 'He said "ready" & left',
        "jobs": [
            {"name": f"job-{index:02d}", "args": ["literal $(date)", 'quote="yes"', "café"],
             "enabled": index % 2 == 0}
            for index in range(12)
        ],
    }
    document = json.dumps(settings, ensure_ascii=False, separators=(",", ":"))
    quotes = 'O\'Brien said "ready"; `tick` and \\backslash'
    dollars = 'Keep $HOME, ${USER}, $(date), and $((1+2)) verbatim'
    rows = [
        ("ordinary", "echo ready | tee answer.txt\n", "ready\n"),
        ("multiline", 'for item in north south; do\n  echo "route:$item"\ndone | tee answer.txt\n',
         "route:north\nroute:south\n"),
        ("quotes", f"printf '%s\\n' {shlex.quote(quotes)} | tee answer.txt\n", quotes + "\n"),
        ("dollars", f"printf '%s\\n' {shlex.quote(dollars)} | tee answer.txt\n", dollars + "\n"),
        ("json", f"cat <<'WRAPPER_JSON' | tee answer.txt\n{document}\nWRAPPER_JSON\n", document + "\n"),
    ]
    return rows


def reference_action(mode, script, replay_carrier):
    if replay_carrier == "file":
        if mode == "one-ssh":
            return f"cat > {SCRIPT_FILE} <<'ONE_WRAP_SCRIPT' && bash {SCRIPT_FILE}\n{script}ONE_WRAP_SCRIPT\n", ""
        prefix = "ssh east" if mode == "pwsh-ssh" else "docker exec -i east bash -c"
        return f"{prefix} '{WRITE_AND_RUN}'", script
    if mode == "one-ssh":
        return script, ""
    if replay_carrier == "stdin":
        command = "ssh east 'bash -s'" if mode == "pwsh-ssh" else "docker exec -i east bash -s"
        return command, script
    argument = "bash -c " + shlex.quote(script) if mode == "pwsh-ssh" else script
    quoted = "'" + argument.replace("'", "''") + "'"
    prefix = "ssh east " if mode == "pwsh-ssh" else "docker exec -i east bash -c "
    return prefix + quoted, ""


def byte_count(string):
    return len(string.encode())


def powershell_action(targets, mode, code, stdin, pwsh, controller, allow_file):
    if mode == "pwsh-ssh":
        program = targets._ssh
        prefix = ["-F", str(targets._config), "-T", HOST]
    else:
        program = shutil.which("docker")
        socket = targets._docker_relay.server_address
        prefix = [
            "--host", f"unix://{socket}", "exec", "-i", "--user", "pilot",
            targets.containers[HOST], "bash",
        ]
    if mode == "pwsh-docker":
        targets._docker_relay.request_streams.clear()
    metrics = controller / "native.json"
    metrics.unlink(missing_ok=True)
    request = {
        "mode": mode, "code": code, "stdin": stdin, "metrics": str(metrics),
        "program": program, "prefix": prefix, "file_command": WRITE_AND_RUN if allow_file else "",
    }
    args = [pwsh, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(controller / "wrapper.ps1")]
    env = {**os.environ, "POWERSHELL_TELEMETRY_OPTOUT": "1", "DOTNET_CLI_TELEMETRY_OPTOUT": "1"}
    env.pop("DOCKER_CONTEXT", None)
    payload = json.dumps(request, ensure_ascii=False).encode()
    proc = subprocess.run(args, input=payload, capture_output=True, timeout=TIMEOUT, env=env)
    native = json.loads(metrics.read_text()) if metrics.exists() else []
    decoded = None
    if native and mode == "pwsh-ssh":
        try:
            tokens = shlex.split(native[0])
        except ValueError:
            tokens = []
        if tokens == ["bash", "-s"]:
            decoded = stdin
        elif len(tokens) == 3 and tokens[:2] == ["bash", "-c"]:
            decoded = tokens[2]
    elif native:
        decoded = stdin if native[0] == "-s" else native[1]
    carrier = None
    if native:
        carrier = "stdin" if native[-1] in ("-s", "bash -s") else "inline"
        if allow_file and native[-1] == WRITE_AND_RUN:
            carrier = "file"
            decoded = None
    docker_create = targets.docker_exec_create() if mode == "pwsh-docker" and proc.returncode == 0 else {}
    return proc.stdout, proc.stderr, proc.returncode, {
        **docker_create,
        "controller_pwsh_stdin_bytes": len(payload),
        "native_variable_argv_bytes": sum(map(byte_count, native)),
        "native_stdin_bytes": byte_count(stdin) if native else 0,
        "script_carrier": carrier,
        "target_script_bytes": byte_count(decoded) if decoded is not None else None,
        "decoded_script": decoded,
    }


def trial(case, tier, script, expected, mode, kind, model, pwsh, binary, tokeniser, condition):
    allow_file = condition in ("free", "file")
    with tempfile.TemporaryDirectory(prefix="one-wrapper-controller-") as tmp, WrapperTargets(
        binary, MODES[mode], instrument=True, targets=(HOST,), runtimes=("bash",),
    ) as targets:
        controller = Path(tmp)
        schema = controller / "schema.json"
        schema.write_text(json.dumps(SCHEMA))
        (controller / "wrapper.ps1").write_text(POWERSHELL)
        workspace = targets.workspaces[HOST]
        artifact = workspace / ARTIFACT
        script_file = workspace / SCRIPT_FILE
        attempts, usage = [], Counter()
        feedback = None
        for attempt_number in range(1, (1 if kind == "replay" else MAX_ATTEMPTS) + 1):
            artifact.unlink(missing_ok=True)
            script_file.unlink(missing_ok=True)
            attempt = {"attempt": attempt_number}
            attempts.append(attempt)
            if kind == "model":
                prompt = (
                    "Transport this exact Bash script into the target. Preserve every byte, including its final newline. "
                    "Return exactly one action with target east, runtime bash, and done true. "
                    "Do not inspect the controller or use tools. " + HELP[mode] +
                    (" " + FILE_HELP[mode] if allow_file else "") +
                    "\nThe required Bash source as a JSON string is:\n" + json.dumps(script, ensure_ascii=False)
                )
                if feedback:
                    prompt += "\nThe previous attempt failed this check: " + feedback
                decision, tokens, error = model_action(controller, schema, model, prompt)
                attempt.update(usage=tokens, model_status=error or "ok")
                usage.update({key: tokens[key] for key in USAGE_KEYS if key in tokens})
                if error or len(decision["actions"]) != 1:
                    feedback = error or "expected one action"
                    continue
                action = decision["actions"][0]
                if action["target"] != HOST or action["runtime"] != "bash":
                    feedback = "expected east and bash"
                    continue
                code, stdin = action["code"], action["stdin"]
                if byte_count(stdin) > 12288 or (mode == "one-ssh" and stdin):
                    feedback = "invalid stdin field"
                    continue
            else:
                code, stdin = reference_action(mode, script, condition)
            attempt.update(
                command_bytes=byte_count(code), stdin_bytes=byte_count(stdin),
                model_bytes=byte_count(code) + byte_count(stdin),
                command_tokens=len(tokeniser.encode_ordinary(code)),
                stdin_tokens=len(tokeniser.encode_ordinary(stdin)),
            )
            attempt["model_payload_tokens"] = attempt["command_tokens"] + attempt["stdin_tokens"]
            before = targets.transport_snapshot()
            started = monotonic()
            try:
                if mode == "one-ssh":
                    out, err, status = targets._execute(HOST, code, "bash")
                    out, err = out.encode(), err.encode()
                    layers = {"script_carrier": "mcp_code", "target_script_bytes": byte_count(code), "decoded_script": code}
                else:
                    out, err, status, layers = powershell_action(targets, mode, code, stdin, pwsh, controller, allow_file)
                uses_file = layers["script_carrier"] == "file" or (allow_file and mode == "one-ssh" and script_file.is_file())
                if uses_file:
                    stored = script_file.read_bytes() if script_file.is_file() else None
                    layers.update(
                        script_carrier="file",
                        target_script_bytes=len(stored) if stored is not None else None,
                        decoded_script=stored.decode(errors="replace") if stored is not None else None,
                    )
                checks = {
                    "exit_zero": status == 0,
                    "stdout": out == expected.encode(),
                    "artifact": artifact.is_file() and artifact.read_bytes() == expected.encode(),
                    "script_bytes_exact": layers.pop("decoded_script") == script,
                }
                if uses_file:
                    checks["stored_script"] = stored == script.encode()
                if condition == "file":
                    checks["used_file_carrier"] = uses_file
                attempt.update(
                    status=status, checks=checks, stdout_bytes=len(out), stderr_bytes=len(err), **layers,
                )
                feedback = ", ".join(key for key, passed in checks.items() if not passed)
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
                feedback = "transport error (" + type(exc).__name__ + ")"
            attempt["execution_ms"] = round((monotonic() - started) * 1000, 3)
            attempt["transport"] = {
                key: value - before[key] for key, value in targets.transport_snapshot().items()
            }
            if not feedback:
                break
        return {
            "case": case, "tier": tier, "mode": mode, "reference_script_bytes": byte_count(script),
            "reference_script_tokens": len(tokeniser.encode_ordinary(script)),
            "success": not feedback, "status": feedback or "completed", "retries": len(attempts) - 1,
            "usage": dict(usage), "attempts": attempts,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("replay", "model"))
    parser.add_argument("--model", default="gpt-6.2-sol")
    parser.add_argument("--pwsh", default=os.environ.get("ONE_PWSH", "pwsh"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--case", choices=[case for case, *_ in fixtures()], action="append")
    parser.add_argument("--mode", choices=MODES, action="append")
    choices = parser.add_mutually_exclusive_group()
    choices.add_argument("--replay-stdin", action="store_true")
    choices.add_argument("--replay-file", action="store_true")
    choices.add_argument("--free-carrier", action="store_true")
    parser.add_argument("--repeats", type=int, default=1)
    opts = parser.parse_args()
    if opts.repeats < 1:
        parser.error("--repeats must be positive")
    if (opts.replay_stdin or opts.replay_file) and opts.kind != "replay":
        parser.error("--replay-stdin and --replay-file require replay")
    if opts.free_carrier and opts.kind != "model":
        parser.error("--free-carrier requires model")
    condition = (
        "stdin" if opts.replay_stdin else "file" if opts.replay_file else
        "free" if opts.free_carrier else "inline"
    )
    pwsh = shutil.which(opts.pwsh)
    if not pwsh:
        parser.error("PowerShell executable unavailable; use --pwsh or ONE_PWSH")
    version = lambda *args: subprocess.check_output(args, text=True).strip()
    suffix = {"stdin": "replay-stdin", "file": "file-replay", "free": "free-model"}.get(condition, opts.kind)
    output = opts.output or ROOT / "experiments/results" / f"wrapper-{suffix}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    seed = 20261125
    results = {
        "suite": "wrapper", "kind": opts.kind, "tokeniser": "o200k_base", "trials": [],
        "image": version("docker", "image", "inspect", "--format={{.Id}}", IMAGE),
        "powershell": version(pwsh, "--version"),
        "docker": version("docker", "version", "--format", "{{.Client.Version}}/{{.Server.Version}}"),
        "go": version("go", "version"), "mode_order_seed": seed, "repeats": opts.repeats,
    }
    if opts.kind == "model":
        results.update(model=opts.model, reasoning="low", codex=version("codex", "--version"), max_attempts=MAX_ATTEMPTS)
        if opts.free_carrier:
            results["carrier_policy"] = "free_with_target_file"
    else:
        results["powershell_replay_carrier"] = condition
    order = random.Random(seed)
    modes = list(dict.fromkeys(opts.mode or MODES))
    tokeniser = tiktoken.get_encoding("o200k_base")
    with tempfile.TemporaryDirectory(prefix="one-wrapper-bin-") as temp, ThreadPoolExecutor(3) as pool:
        binary = Path(temp) / "one"
        subprocess.run(
            ["go", "build", "-mod=readonly", "-o", str(binary), "."], cwd=ROOT / "ssh-mcp",
            env={**os.environ, "CGO_ENABLED": "0"}, check=True, timeout=60,
        )
        for repeat in range(1, opts.repeats + 1):
            for tier, (case, script, expected) in enumerate(fixtures(), 1):
                if opts.case and case not in opts.case:
                    continue
                futures = [
                    pool.submit(
                        trial, case, tier, script, expected, mode, opts.kind, opts.model,
                        pwsh, binary, tokeniser, condition,
                    ) for mode in order.sample(modes, len(modes))
                ]
                for future in futures:
                    row = future.result()
                    row["repeat"] = repeat
                    results["trials"].append(row)
                    temporary = output.with_suffix(".tmp")
                    temporary.write_text(json.dumps(results, indent=2) + "\n")
                    temporary.replace(output)
                    print(case, row["mode"], row["status"], row["usage"], flush=True)
    if opts.kind == "replay" and not all(row["success"] for row in results["trials"]):
        raise SystemExit("a reference task failed")


if __name__ == "__main__":
    main()
