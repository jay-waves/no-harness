"""Disposable two-host fixture for the paired POSIX transport pilot."""
import base64
from contextlib import ExitStack
import json
import os
from pathlib import Path
from queue import Empty, Queue
import selectors
import shlex
import shutil
import socket
import socketserver
import subprocess
import tempfile
from threading import Event, Lock, Thread
from time import monotonic, perf_counter_ns, sleep


IMAGE = "no-harness-remote:python"
TARGETS = ("east", "west")
TIMEOUT = 15

BOOT = """
set -eu
mkdir -p /tmp/pilot /run/sshd
chown "$PILOT_UID:$PILOT_GID" /tmp/pilot
printf 'pilot:x:%s:%s:Pilot:/tmp/pilot:/bin/sh\n' "$PILOT_UID" "$PILOT_GID" >> /etc/passwd
printf 'pilot:*:0:0:99999:7:::\n' >> /etc/shadow
ssh-keygen -q -t ed25519 -N '' -f /run/sshd/host-key
exec /usr/sbin/sshd -D -e -f /fixture/sshd_config
"""

SSHD_CONFIG = """
HostKey /run/sshd/host-key
AuthorizedKeysFile /fixture/client.pub
StrictModes no
AuthenticationMethods publickey
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
AllowUsers pilot
DisableForwarding yes
PermitTTY no
ForceCommand cd /work && exec /bin/sh -c "$SSH_ORIGINAL_COMMAND"
"""


def _run(*args, check=False, input=None, on_spawn=None):
    payload = input.encode() if input is not None else None
    with subprocess.Popen(
        args, stdin=subprocess.PIPE if payload is not None else None,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ) as process:
        if on_spawn is not None:
            on_spawn(process.pid)
        try:
            stdout, stderr = process.communicate(payload, timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise
    stdout = stdout.decode(errors="replace")
    stderr = stderr.decode(errors="replace")
    if check:
        if process.returncode != 0:
            raise RuntimeError(f"{args[0]} failed with status {process.returncode}: {stderr.strip()}")
        return stdout.strip()
    return stdout, stderr, process.returncode


class _Relay(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            if isinstance(self.server.target, tuple):
                remote = socket.create_connection(self.server.target, timeout=TIMEOUT)
            else:
                remote = socket.socket(socket.AF_UNIX)
                try:
                    remote.connect(self.server.target)
                except OSError:
                    remote.close()
                    raise
        except OSError:
            return
        with remote, selectors.DefaultSelector() as poll:
            remote.settimeout(None)
            poll.register(self.request, selectors.EVENT_READ, remote)
            poll.register(remote, selectors.EVENT_READ, self.request)
            while poll.get_map():
                for key, _ in poll.select():
                    try:
                        chunk = key.fileobj.recv(65536)
                        if chunk:
                            if self.server.instrument:
                                self.server.count(int(key.fileobj is remote), len(chunk))
                            key.data.sendall(chunk)
                        else:
                            poll.unregister(key.fileobj)
                            key.data.shutdown(socket.SHUT_WR)
                    except OSError:
                        return


class _MeasuredListener:
    def __init__(self, address, target, *, instrument=True):
        super().__init__(address, _Relay)
        self.target = target
        self.instrument = instrument
        self._lock = Lock()
        self._bytes = [0, 0]

    def count(self, direction, size):
        with self._lock:
            self._bytes[direction] += size

    def bytes(self):
        with self._lock:
            return tuple(self._bytes)


class _LoopbackListener(_MeasuredListener, socketserver.ThreadingTCPServer):
    daemon_threads = True


class _DockerListener(_MeasuredListener, socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


def _process_rss(pids):
    """Sum Linux VmRSS for live processes, with shared pages counted per process."""
    total = 0
    for pid in set(pids):
        try:
            for line in (Path("/proc") / str(pid) / "status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    total += int(line.split()[1])
                    break
        except (FileNotFoundError, ProcessLookupError):
            continue
        except PermissionError:
            return None
    return total


def _container_cgroup(pid, container):
    try:
        for line in (Path("/proc") / str(pid) / "cgroup").read_text().splitlines():
            if line.startswith("0::"):
                procs = Path("/sys/fs/cgroup") / line[3:].lstrip("/") / "cgroup.procs"
                # Match the Docker container, not a coincident PID on a different host.
                if container in procs.parent.name and str(pid) in procs.read_text().split():
                    return procs.parent
    except OSError:
        pass
    return None


class RemoteTargets:
    """Run identical containers with fresh Docker/SSH or persistent MCP sessions."""

    def __init__(self, binary, mode, *, instrument=False, targets=TARGETS, runtimes=("bash", "python")):
        if mode not in ("docker", "ssh", "one"):
            raise ValueError(f"unknown remote transport {mode}")
        self.mode = mode
        self.instrument = instrument
        self.targets = tuple(targets)
        self.runtimes = tuple(runtimes)
        self.binary = Path(binary).resolve(strict=True)
        self.workspaces = {}
        self.endpoints = {}
        self.containers = {}
        self.sessions = {}
        self.transport_calls = 0
        self.last_action = {}
        self._ssh_relays = {}
        self._docker_relay = None
        self._container_cgroups = {}
        self._client_pids = []
        self._action_pid = None
        self._mcp_counts = [0, 0, 0, 0]
        self._id = 0
        self._responses = Queue()
        self._stack = ExitStack()
        self._ssh = shutil.which("ssh")
        if self._ssh is None:
            raise FileNotFoundError("OpenSSH client not found")

    def __enter__(self):
        try:
            self._start()
        except BaseException:
            self._stack.close()
            raise
        return self

    def __exit__(self, *exc):
        self._stack.close()

    def _start(self):
        root = Path(self._stack.enter_context(tempfile.TemporaryDirectory(prefix="one-remote-", dir="/tmp")))
        self._config = root / "ssh_config"
        self._config.touch()
        known_hosts = root / "known_hosts"
        known_hosts.touch()
        identity = root / "client"
        _run("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", identity, check=True)
        binary = root / "one"
        shutil.copyfile(self.binary, binary)
        binary.chmod(0o755)
        uid, gid = os.getuid(), os.getgid()
        if uid == 0:
            uid = gid = 1000
        for target in self.targets:
            fixture = root / target
            fixture.mkdir()
            workspace = root / f"work-{target}"
            workspace.mkdir()
            if os.getuid() == 0:
                os.chown(workspace, uid, gid)
            self.workspaces[target] = workspace
            shutil.copyfile(identity.with_suffix(".pub"), fixture / "client.pub")
            (fixture / "sshd_config").write_text(SSHD_CONFIG)

            network = f"{root.name}-{target}"
            _run("docker", "network", "create", "--internal", network, check=True)
            self._stack.callback(_run, "docker", "network", "rm", network)
            container = _run(
                "docker", "run", "-d", "--rm", "--pull=never", "--network", network, "--name", network,
                "--dns=127.0.0.1", "--dns-search=.", "--security-opt=no-new-privileges",
                "--pids-limit=64", "--memory=256m",
                "-e", f"PILOT_UID={uid}", "-e", f"PILOT_GID={gid}", "-w", "/work",
                "-v", f"{workspace}:/work:Z", "-v", f"{binary}:/usr/local/bin/one:ro,z",
                "-v", f"{fixture}:/fixture:ro,Z", IMAGE, "/bin/sh", "-c", BOOT, check=True,
            )
            self._stack.callback(_run, "docker", "rm", "-f", container)
            self.containers[target] = container
            if self.instrument:
                pid = int(_run("docker", "inspect", "--format", "{{.State.Pid}}", container, check=True))
                self._container_cgroups[target] = _container_cgroup(pid, container)
            address = _run(
                "docker", "inspect", "--format", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
                container, check=True,
            )
            # Docker suppresses published ports on internal bridge networks.
            relay = self._serve(_LoopbackListener(
                ("127.0.0.1", 0), (address, 22), instrument=self.instrument,
            ))
            self._ssh_relays[target] = relay
            port = relay.server_address[1]
            self.endpoints[target] = f"127.0.0.1:{port}"
            deadline = monotonic() + 5
            while True:
                host_key, stderr, status = _run(
                    "docker", "exec", container, "cat", "/run/sshd/host-key.pub",
                )
                if status == 0:
                    break
                if monotonic() >= deadline:
                    raise RuntimeError(f"SSH host key for {target} failed: {stderr.strip()}")
                sleep(.05)
            with known_hosts.open("a") as handle:
                handle.write(f"[127.0.0.1]:{port} {host_key.strip()}\n")
            with self._config.open("a") as handle:
                handle.write(
                    f"Host {target}\n HostName 127.0.0.1\n Port {port}\n User pilot\n"
                    f" IdentityFile {identity}\n UserKnownHostsFile {known_hosts}\n"
                    f" ControlPath {root / target / 'control'}\n"
                    " AddressFamily inet\n BatchMode yes\n IdentitiesOnly yes\n IdentityAgent none\n"
                    " GlobalKnownHostsFile none\n StrictHostKeyChecking yes\n ConnectTimeout 2\n"
                )
            deadline = monotonic() + 5
            while True:
                _, stderr, status = _run(self._ssh, "-F", self._config, "-T", target, "true")
                if status == 0:
                    break
                if monotonic() >= deadline:
                    raise RuntimeError(f"SSH fixture {target} failed: {stderr.strip()}")
                sleep(.05)
            if self.mode == "ssh":
                _run(self._ssh, "-F", self._config, "-MNf", target, check=True)
                self._stack.callback(_run, self._ssh, "-F", self._config, "-O", "exit", target)
                if self.instrument:
                    _, stderr, _ = _run(self._ssh, "-F", self._config, "-O", "check", target)
                    if "(pid=" in stderr:
                        self._client_pids.append(int(stderr.split("(pid=", 1)[1].split(")", 1)[0]))

        if self.mode == "one":
            self._start_mcp(root)
        elif self.mode == "docker" and self.instrument:
            self._start_docker_relay(root)

    def _serve(self, listener):
        self._stack.enter_context(listener)
        thread = Thread(target=listener.serve_forever, kwargs={"poll_interval": .05}, daemon=True)
        thread.start()
        self._stack.callback(listener.shutdown)
        return listener

    def _start_docker_relay(self, root):
        host = os.environ.get("DOCKER_HOST") if not os.environ.get("DOCKER_CONTEXT") else None
        if not host:
            host = _run("docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}", check=True)
        if host.startswith("unix://"):
            self._docker_relay = self._serve(_DockerListener(str(root / "docker.sock"), host[7:]))

    def _start_mcp(self, root):
        resolver = root / "resolve-ssh"
        # The production dialer uses OpenSSH to resolve config, then performs SSH itself.
        resolver.write_text(
            "#!/bin/sh\nexec " + shlex.join([
                self._ssh, "-F", str(self._config), "-o", "StrictHostKeyChecking=yes",
            ]) + ' "$@"\n'
        )
        resolver.chmod(0o700)
        self._mcp_log = self._stack.enter_context(tempfile.TemporaryFile(mode="w+b"))
        self._mcp = subprocess.Popen(
            [self.binary, "mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._mcp_log,
            env={"PATH": os.environ["PATH"], "SSH_MCP_SSH": str(resolver)},
        )
        self._client_pids.append(self._mcp.pid)
        self._stack.callback(self._stop_mcp)
        Thread(target=self._read_mcp, daemon=True).start()
        self._rpc("initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "remote-pilot", "version": "1"},
        })
        self._rpc("notifications/initialized", {}, notification=True)
        for target in self.targets:
            for runtime in self.runtimes:
                self._open_session(target, runtime)

    def _open_session(self, target, runtime):
        session = self._tool("session_open", host=target, runtime=runtime)
        if session["system"]["cwd"] != "/work":
            raise RuntimeError(f"remote session started in {session['system']['cwd']}")
        self.sessions[target, runtime] = session["id"]

    def reset_session(self, target, runtime):
        """Discard interpreter state while leaving the MCP client and SSH connection warm."""
        session_id = self.sessions.pop((target, runtime))
        self._tool("session_close", session_id=session_id)
        self._open_session(target, runtime)

    def _stop_mcp(self):
        self._mcp.stdin.close()
        try:
            self._mcp.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self._mcp.kill()
            self._mcp.wait()
        self._mcp.stdout.close()

    def _read_mcp(self):
        for line in self._mcp.stdout:
            self._responses.put(line)
        self._responses.put(None)

    def _rpc(self, method, params, *, notification=False):
        request = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notification:
            self._id += 1
            request["id"] = self._id
        start = perf_counter_ns() if self.instrument else 0
        encoded = json.dumps(request).encode() + b"\n"
        if self.instrument:
            self._mcp_counts[2] += perf_counter_ns() - start
        self._mcp.stdin.write(encoded)
        self._mcp.stdin.flush()
        if self.instrument:
            self._mcp_counts[0] += len(encoded)
        if notification:
            return
        try:
            line = self._responses.get(timeout=TIMEOUT)
        except Empty as error:
            raise TimeoutError(f"MCP {method} did not respond within {TIMEOUT} seconds") from error
        if line is None:
            self._mcp_log.seek(0)
            raise RuntimeError(f"MCP server exited: {self._mcp_log.read().decode(errors='replace')}")
        start = perf_counter_ns() if self.instrument else 0
        response = json.loads(line)
        if self.instrument:
            self._mcp_counts[3] += perf_counter_ns() - start
            self._mcp_counts[1] += len(line)
        if response.get("id") != self._id or "error" in response:
            raise RuntimeError(f"MCP {method}: {response}")
        return response["result"]

    def _tool(self, name, **arguments):
        self.transport_calls += 1
        result = self._rpc("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise RuntimeError(f"MCP {name}: {result['content']}")
        return result["structuredContent"]

    def memory_snapshot(self):
        target_pids = []
        target_rss = None
        if self._container_cgroups and all(self._container_cgroups.values()):
            try:
                for group in self._container_cgroups.values():
                    target_pids.extend((group / "cgroup.procs").read_text().split())
                target_rss = _process_rss(target_pids)
            except OSError:
                pass
        controller_pids = self._client_pids.copy()
        if self._action_pid is not None:
            controller_pids.append(self._action_pid)
        return {"target_kib": target_rss, "controller_kib": _process_rss(controller_pids)}

    def transport_snapshot(self):
        counters = {}
        if self.mode in ("one", "ssh"):
            counters["ssh_tx_bytes"], counters["ssh_rx_bytes"] = map(
                sum, zip(*(relay.bytes() for relay in self._ssh_relays.values()))
            )
        if self.mode == "one":
            keys = ("mcp_tx_bytes", "mcp_rx_bytes", "mcp_encode_ns", "mcp_decode_ns")
            counters.update(zip(keys, self._mcp_counts))
        if self._docker_relay is not None:
            counters["docker_tx_bytes"], counters["docker_rx_bytes"] = self._docker_relay.bytes()
        return counters

    def execute(self, target, code, runtime="bash"):
        if not self.instrument:
            return self._execute(target, code, runtime)
        before = self.memory_snapshot()
        peak = before.copy()
        transport_before = self.transport_snapshot()
        stop = Event()
        samples = 0

        def sample():
            nonlocal samples
            snapshot = self.memory_snapshot()
            samples += 1
            for key, value in snapshot.items():
                if value is not None:
                    peak[key] = max(peak[key] or 0, value)

        def monitor():
            while not stop.wait(.02):
                sample()

        sampler = Thread(target=monitor)
        sampler.start()
        start = perf_counter_ns()
        try:
            return self._execute(target, code, runtime)
        finally:
            action_ns = perf_counter_ns() - start
            transport_after = self.transport_snapshot()
            stop.set()
            sampler.join()
            self._action_pid = None
            after = self.memory_snapshot()
            memory = {"samples_during": samples}
            for key in before:
                name = key.removesuffix("_kib")
                available = [value for value in (peak[key], after[key]) if value is not None]
                memory[f"{name}_before_kib"] = before[key]
                memory[f"{name}_peak_kib"] = max(available, default=None)
                memory[f"{name}_after_kib"] = after[key]
            self.last_action = {
                "action_ns": action_ns,
                "memory": memory,
                "transport": {key: value - transport_before[key] for key, value in transport_after.items()},
            }

    def _record_action_pid(self, pid):
        self._action_pid = pid

    def _execute(self, target, code, runtime):
        if runtime not in ("bash", "python"):
            raise ValueError(f"unknown remote runtime {runtime}")
        if self.mode != "one":
            self.transport_calls += 1
            on_spawn = self._record_action_pid if self.instrument else None
            command = ["bash", "--noprofile", "--norc", "-s"] if runtime == "bash" else ["python3", "-"]
            if self.mode == "docker":
                docker = ["docker"]
                if self._docker_relay is not None:
                    docker.extend(("--host", "unix://" + self._docker_relay.server_address))
                return _run(
                    *docker, "exec", "-i", "--user", "pilot", "-e", "USER=pilot", "-e", "LOGNAME=pilot",
                    self.containers[target], *command, input=code, on_spawn=on_spawn,
                )
            return _run(
                self._ssh, "-F", self._config, "-T", target, *command,
                input=code, on_spawn=on_spawn,
            )

        args = {"session_id": self.sessions[target, runtime], "wait_ms": 500}
        execution = self._tool("session_execute", code=code, **args)
        if execution.get("read_error"):
            raise RuntimeError(execution["read_error"])
        output = execution.get("output")
        streams = {"stdout": bytearray(), "stderr": bytearray()}
        deadline = monotonic() + TIMEOUT
        cursor = ""
        while monotonic() < deadline:
            if output is None:
                output = self._tool("session_read", execution_id=execution["id"], cursor=cursor, **args)
            for name, data in streams.items():
                encoded = output.get(f"{name}_base64")
                data.extend(base64.b64decode(encoded) if encoded else output.get(name, "").encode())
            if output.get("error") or output.get("truncated"):
                raise RuntimeError(f"MCP execution output lost: {output}")
            if output["output_closed"] and output.get("exit_code") is not None:
                return (
                    streams["stdout"].decode(errors="replace"),
                    streams["stderr"].decode(errors="replace"),
                    output["exit_code"],
                )
            cursor = output["cursor"]
            output = None
        raise TimeoutError("MCP execution exceeded 15 seconds")
