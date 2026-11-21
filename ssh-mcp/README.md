# SSH MCP

A small independent SSH MCP server for this project. It retains the persistent Bash execution protocol from the `mcp` branch of [sshore](https://github.com/jay-waves/sshore) (commit `a32546ae1fa3270e6e962720655fc8095d80a01a`); see [LICENSE](LICENSE). It does not track upstream. Mounting, port forwarding, terminal multiplexing, the upstream installer and release machinery have been removed.

The implementation currently targets Linux for both the local MCP server and remote worker. It needs Go 1.26 or newer to build, `ssh` on the local machine for SSH configuration resolution, and Bash on the remote host. Local and remote binaries must have the same version; build for the target's architecture. The remote account must have `ssh-mcp` on its noninteractive `PATH` and an SFTP subsystem for copying files.

## Build and configure

```sh
cd ssh-mcp
go build -o ssh-mcp .
# Install the binary somewhere on PATH locally and on each SSH target.
# For a matching Linux architecture, for example:
scp ssh-mcp work:~/.local/bin/ssh-mcp
```

Configure your MCP client (use an absolute path to the local binary):

```json
{
  "mcpServers": {
    "ssh": { "command": "/absolute/path/to/ssh-mcp", "args": ["mcp"] }
  }
}
```

SSH host aliases, keys and ProxyJump are read from the local SSH configuration. Authentication is noninteractive: use an unencrypted key or an SSH agent. The server uses `StrictHostKeyChecking=accept-new` for agent operations, including file copies: unknown host keys are recorded automatically, while changed keys are rejected. This setting overrides a stricter local host-key policy. The agent can execute arbitrary commands as the SSH user; use a dedicated account or other isolation appropriate to the environment.

## Tools

- `session_open` opens a persistent, non-PTY Bash session for an SSH host.
- `session_execute` submits a complete Bash script and returns an execution ID. Working directory, variables and functions persist. One execution runs at a time per session.
- `session_read` retrieves bounded stdout and stderr chunks with a cursor. Continue reading until `output_closed` is true; background processes can keep output open. Non-UTF-8 chunks are returned in base64.
- `session_list` finds sessions for a host, including after reconnecting the MCP client. `session_close` closes a session.
- `file_copy` takes `host`, `direction` (`upload` or `download`), `local_path` and `remote_path`. It copies one regular file using SFTP, does not overwrite an existing destination and accepts paths containing spaces. Paths are interpreted on their respective machines; use explicit paths.

A per-user worker starts on the remote host on first use and keeps sessions alive across SSH and MCP client reconnects. Its private socket and startup lock are in `$XDG_CACHE_HOME/ssh-mcp`, or `~/.cache/ssh-mcp` by default. A worker restart loses its sessions and execution history. Execution output is bounded and old completed executions may be evicted. This is a Bash session, not the persistent Python REPL proposed in the project document.

For simple human inspection, `ssh-mcp sessions HOST` lists the host's sessions. The same transfer is available without MCP as `ssh-mcp cp upload|download HOST LOCAL_PATH REMOTE_PATH`. To stop the worker and close its sessions, run `ssh HOST ssh-mcp worker-stop` on the target. Child processes started by scripts can outlive their Bash session and may need separate cleanup.

## Validation

```sh
go test -mod=readonly ./...
go vet -mod=readonly ./...
```

The integration test uses an isolated in-process SSH/SFTP fixture and exercises automatic worker startup, session reconnection, bidirectional binary file copy and shutdown. It does not constitute a deployment or a benchmark.
