//go:build linux

package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/jay-waves/comp7607a/one/internal/mcp"
)

const serializationImage = "nodered/node-red@sha256:e085d4a06591fd0aca2ea2a91364d27f1a0e31e885675f77935e10f95412a963"

type serializationSample struct {
	Mode        string `json:"mode"`
	ScriptBytes int    `json:"script_bytes"`
	LaunchUS    int64  `json:"launch_us,omitempty"`
	CompleteUS  int64  `json:"complete_us,omitempty"`
	PeakRSSKiB  int    `json:"peak_sampled_rss_kib,omitempty"`
}

func TestSerializationPilot(t *testing.T) {
	output := os.Getenv("ONE_SERIALIZATION_OUTPUT")
	if output == "" {
		t.Fatal("ONE_SERIALIZATION_OUTPUT must name the results file")
	}
	pwsh := os.Getenv("ONE_PWSH")
	if pwsh == "" {
		pwsh = "pwsh"
	}
	t.Setenv("POWERSHELL_TELEMETRY_OPTOUT", "1")
	t.Setenv("DOTNET_CLI_TELEMETRY_OPTOUT", "1")
	t.Setenv("DOTNET_NOLOGO", "1")
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Minute)
	t.Cleanup(cancel)
	run := func(name string, args ...string) string {
		t.Helper()
		data, err := exec.CommandContext(ctx, name, args...).CombinedOutput()
		if err != nil {
			t.Fatalf("%s %s: %v\n%s", name, strings.Join(args[:min(2, len(args))], " "), err, data)
		}
		return strings.TrimSpace(string(data))
	}
	pwshVersion := run(pwsh, "--version")
	dockerVersion := run("docker", "version", "--format", "{{.Client.Version}}/{{.Server.Version}}")
	root := t.TempDir()
	bin := filepath.Join(root, "one")
	t.Setenv("CGO_ENABLED", "0")
	run("go", "build", "-mod=readonly", "-o", bin, ".")
	startContainer := func(worker bool) (string, string) {
		t.Helper()
		args := []string{"run", "--pull", "never", "--detach", "--rm", "--network", "none",
			"--user", fmt.Sprintf("%d:%d", os.Getuid(), os.Getgid())}
		if worker {
			args = append(args, "-e", "XDG_CACHE_HOME=/bench", "-v", root+":/bench:Z", "--entrypoint", "/bench/one", serializationImage, "worker")
		} else {
			args = append(args, "--entrypoint", "/bin/sleep", serializationImage, "infinity")
		}
		id := run("docker", args...)
		t.Cleanup(func() {
			cleanup, cancel := context.WithTimeout(context.Background(), 10*time.Second)
			defer cancel()
			if data, err := exec.CommandContext(cleanup, "docker", "stop", "--time", "0", id).CombinedOutput(); err != nil {
				t.Errorf("stop experiment container: %v: %s", err, data)
			}
		})
		pid := run("docker", "inspect", "--format", "{{.State.Pid}}", id)
		cgroup, err := os.ReadFile("/proc/" + pid + "/cgroup")
		if err != nil {
			t.Fatal(err)
		}
		path, ok := strings.CutPrefix(strings.TrimSpace(string(cgroup)), "0::/")
		if !ok {
			t.Fatal("a local Docker Engine with cgroup v2 is required")
		}
		procs := filepath.Join("/sys/fs/cgroup", path, "cgroup.procs")
		if data, err := os.ReadFile(procs); err != nil || !slices.Contains(strings.Fields(string(data)), pid) {
			t.Fatalf("cannot enumerate local container processes in %s: %v", procs, err)
		}
		return id, procs
	}
	target, dockerProcs := startContainer(false)
	_, oneProcs := startContainer(true)
	bashVersion := run("docker", "exec", target, "bash", "-c", "printf %s \"$BASH_VERSION\"")

	var socket net.Conn
	for deadline := time.Now().Add(5 * time.Second); ; {
		var err error
		socket, err = net.Dial("unix", filepath.Join(root, "ssh-mcp", "worker.sock"))
		if err == nil {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal(err)
		}
		time.Sleep(10 * time.Millisecond)
	}
	defer socket.Close()
	reader := bufio.NewReader(socket)
	rpc := func(req mcp.Request) mcp.Response {
		t.Helper()
		req.Version = version
		if err := socket.SetDeadline(time.Now().Add(5 * time.Second)); err != nil {
			t.Fatal(err)
		}
		if err := mcp.WriteRequest(socket, req); err != nil {
			t.Fatal(err)
		}
		res, err := mcp.ReadResponse(reader)
		if err != nil || res.Error != "" {
			t.Fatalf("worker %s: %v %s", req.Method, err, res.Error)
		}
		return res
	}
	rpc(mcp.Request{Method: "hello", Protocol: mcp.ProtocolVersion})
	request := mcp.Request{Version: version, Method: "execute", Execute: mcp.ExecuteInput{
		SessionID: rpc(mcp.Request{Method: "open", Open: mcp.OpenInput{Runtime: "bash"}}).Session.ID,
	}}
	closeSession := mcp.Request{Method: "close", Session: mcp.SessionInput{SessionID: request.Execute.SessionID}}
	defer rpc(closeSession)

	const hostLoop = `
[Console]::WriteLine("ready")
while ($null -ne ($line = [Console]::ReadLine())) {
    & ([scriptblock]::Create($line))
    [Console]::WriteLine("exit:$LASTEXITCODE")
}
`
	warm := exec.CommandContext(ctx, pwsh, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", hostLoop)
	input, err := warm.StdinPipe()
	if err != nil {
		t.Fatal(err)
	}
	stdout, err := warm.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	warm.Stderr = os.Stderr
	if err := warm.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() {
		input.Close()
		if err := warm.Wait(); err != nil {
			t.Error(err)
		}
	}()
	lines := bufio.NewScanner(stdout)
	readLine := func() string {
		t.Helper()
		if !lines.Scan() {
			t.Fatalf("PowerShell stdout ended: %v", lines.Err())
		}
		return lines.Text()
	}
	if readLine() != "ready" {
		t.Fatal("PowerShell warm launcher failed to start")
	}

	var samples []serializationSample
	var codecs []map[string]any
	for _, payloadBytes := range []int{0, 1024, 16 * 1024} {
		script := "{ builtin printf '%s\\n' \"$EPOCHREALTIME\"; : '" + strings.Repeat("x", payloadBytes) + "'; }"
		quote := func() string { return "'" + strings.ReplaceAll(script, "'", "''") + "'" }
		command := "docker exec " + target + " bash -c " + quote()
		request.Execute.Code = script
		trial := func(mode string, memory bool) serializationSample {
			t.Helper()
			procs, pid := dockerProcs, 0
			var child *exec.Cmd
			var combined bytes.Buffer
			switch mode {
			case "pwsh-cold":
				child = exec.CommandContext(ctx, pwsh, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command)
				child.Stdout, child.Stderr = &combined, &combined
			case "pwsh-warm":
				pid = warm.Process.Pid
			case "one-warm":
				procs = oneProcs
			}
			var marker string
			start := time.Now()
			if child != nil {
				if err := child.Start(); err != nil {
					t.Fatal(err)
				}
				pid = child.Process.Pid
			}
			var stop, done chan struct{}
			var peak int
			if memory {
				stop, done = make(chan struct{}), make(chan struct{})
				go func() {
					defer close(done)
					ticker := time.NewTicker(2 * time.Millisecond)
					defer ticker.Stop()
					for {
						peak = max(peak, serializationRSS(procs, pid))
						select {
						case <-stop:
							return
						case <-ticker.C:
						}
					}
				}()
				defer func() {
					if stop != nil {
						close(stop)
						<-done
					}
				}()
			}
			switch mode {
			case "pwsh-cold":
				if err := child.Wait(); err != nil {
					t.Fatalf("cold PowerShell: %v: %s", err, combined.String())
				}
				marker = strings.TrimSpace(combined.String())
			case "pwsh-warm":
				if _, err := fmt.Fprintln(input, command); err != nil {
					t.Fatal(err)
				}
				marker = readLine()
				if line := readLine(); line != "exit:0" {
					t.Fatalf("warm PowerShell: %q", line)
				}
			case "one-warm":
				result := rpc(request).Execution
				read := mcp.ReadInput{SessionID: request.Execute.SessionID, ExecutionID: result.ID, WaitMS: 1000}
				var body strings.Builder
				for deadline := time.Now().Add(5 * time.Second); ; {
					out := rpc(mcp.Request{Method: "read", Read: read}).Output
					body.WriteString(out.Stdout)
					if out.Stderr != "" || out.Truncated || out.Error != "" {
						t.Fatalf("worker output: %+v", out)
					}
					if out.OutputClosed && out.ExitCode != nil {
						if *out.ExitCode != 0 {
							t.Fatalf("worker exit: %d", *out.ExitCode)
						}
						break
					}
					if time.Now().After(deadline) {
						t.Fatal("worker command did not finish")
					}
					read.Cursor = out.Cursor
				}
				marker = strings.TrimSpace(body.String())
			}
			elapsed := time.Since(start).Microseconds()
			stamp, err := strconv.ParseInt(strings.Replace(marker, ".", "", 1), 10, 64)
			launch := stamp - start.UnixMicro()
			if err != nil || launch <= 0 || launch > elapsed+1 {
				t.Fatalf("%s: invalid Bash marker %q: start latency %d us, completion %d us", mode, marker, launch, elapsed)
			}
			row := serializationSample{Mode: mode, ScriptBytes: len(script), LaunchUS: launch, CompleteUS: elapsed}
			if memory {
				close(stop)
				<-done
				stop = nil
				if peak == 0 {
					t.Fatal("RSS sampling found no readable processes")
				}
				row.LaunchUS, row.CompleteUS, row.PeakRSSKiB = 0, 0, peak
			}
			return row
		}

		modes := []string{"pwsh-cold", "pwsh-warm", "one-warm"}
		for i := 0; i < 2; i++ {
			for _, mode := range modes {
				trial(mode, false)
			}
		}
		for i := 0; i < 35; i++ {
			for j := range modes {
				samples = append(samples, trial(modes[(i+j)%len(modes)], i >= 30))
			}
		}
		codecs = append(codecs, measureSerializationCodecs(t, ctx, pwsh, target, command, script, request, quote))
		t.Logf("measured %d-byte scripts", len(script))
	}

	data, err := json.MarshalIndent(map[string]any{
		"environment": map[string]any{
			"kernel": run("uname", "-sr"), "go": run("go", "version"), "pwsh": pwshVersion,
			"docker_client_server": dockerVersion, "bash": bashVersion, "image": serializationImage,
			"cpu": strings.Split(run("sh", "-c", "grep -m1 '^model name' /proc/cpuinfo || uname -m"), "\n")[0],
		},
		"samples": samples, "codecs": codecs,
	}, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(output, append(data, '\n'), 0600); err != nil {
		t.Fatal(err)
	}
	t.Log(output)
}

func serializationRSS(procs string, root int) int {
	pids, _ := os.ReadFile(procs)
	queue := []string{}
	if root != 0 {
		queue = append(queue, strconv.Itoa(root))
		for i := 0; i < len(queue); i++ {
			tasks, _ := filepath.Glob("/proc/" + queue[i] + "/task/*/children")
			for _, path := range tasks {
				children, _ := os.ReadFile(path)
				for _, child := range strings.Fields(string(children)) {
					if !slices.Contains(queue, child) {
						queue = append(queue, child)
					}
				}
			}
		}
	}
	queue = append(queue, strings.Fields(string(pids))...)
	pages := 0
	for _, pid := range queue {
		data, _ := os.ReadFile("/proc/" + pid + "/statm")
		fields := strings.Fields(string(data))
		if len(fields) >= 2 {
			rss, _ := strconv.Atoi(fields[1])
			pages += rss
		}
	}
	return pages * (os.Getpagesize() / 1024)
}

func measureSerializationCodecs(t *testing.T, ctx context.Context, pwsh, target, command, script string, req mcp.Request, quote func() string) map[string]any {
	t.Helper()
	// Moby 29.9.2 ExecCreateRequest, with the defaults used by plain docker exec.
	type dockerExec struct {
		User         string
		Privileged   bool
		Tty          bool
		ConsoleSize  *[2]uint `json:",omitempty"`
		AttachStdin  bool
		AttachStderr bool
		AttachStdout bool
		DetachKeys   string
		Env          []string
		WorkingDir   string
		Cmd          []string
	}
	dockerValue := dockerExec{AttachStdout: true, AttachStderr: true, Cmd: []string{"bash", "-c", script}}
	dockerJSON, err := json.Marshal(dockerValue)
	if err != nil {
		t.Fatal(err)
	}
	var frame bytes.Buffer
	if err := mcp.WriteRequest(&frame, req); err != nil {
		t.Fatal(err)
	}
	measure := func(f func()) int64 {
		const iterations = 2000
		start := time.Now()
		for range iterations {
			f()
		}
		return time.Since(start).Nanoseconds() / iterations
	}
	cmd := exec.CommandContext(ctx, pwsh, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", `
$ErrorActionPreference = "Stop"
$line = [Console]::In.ReadToEnd()
$null = [scriptblock]::Create($line)
$clock = [Diagnostics.Stopwatch]::StartNew()
for ($i = 0; $i -lt 2000; $i++) {
    $null = [System.Management.Automation.Language.Parser]::ParseInput($line, [ref]$null, [ref]$null)
}
$clock.Stop()
[Console]::WriteLine([long] ($clock.Elapsed.TotalNanoseconds / 2000))
`)
	cmd.Stdin = strings.NewReader(command)
	parseOutput, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("PowerShell parser: %v: %s", err, parseOutput)
	}
	parseNS, err := strconv.ParseInt(strings.TrimSpace(string(parseOutput)), 10, 64)
	if err != nil || parseNS <= 0 {
		t.Fatalf("PowerShell parser timing: %q", parseOutput)
	}

	// Parse batches of identical Bash compound commands in the target image.
	const batch = 512
	bash := exec.CommandContext(ctx, "docker", "exec", "-i", target, "bash", "-c", `
set -e
file=$(mktemp)
trap 'rm -f "$file"' EXIT
cat >"$file"
TIMEFORMAT=%R
time bash -n "$file"
`)
	bash.Stdin = strings.NewReader(strings.Repeat(script+"\n", batch))
	bashOutput, err := bash.CombinedOutput()
	if err != nil {
		t.Fatalf("Bash syntax check: %v: %s", err, bashOutput)
	}
	bashSeconds, err := strconv.ParseFloat(strings.TrimSpace(string(bashOutput)), 64)
	if err != nil {
		t.Fatal(err)
	}
	return map[string]any{
		"script_bytes": len(script), "pwsh_command_bytes": len(command),
		"pwsh_quote_ns": measure(func() { _ = quote() }),
		"pwsh_parse_ns": parseNS, "bash_parse_batch_ns_per_script": int64(bashSeconds * 1e9 / batch),
		"docker_exec_json_bytes": len(dockerJSON),
		"docker_exec_json_encode_ns": measure(func() {
			if _, err := json.Marshal(dockerValue); err != nil {
				t.Fatal(err)
			}
		}),
		"docker_exec_json_decode_ns": measure(func() {
			var decoded dockerExec
			if err := json.NewDecoder(bytes.NewReader(dockerJSON)).Decode(&decoded); err != nil || decoded.Cmd[2] != script {
				t.Fatalf("Docker argument JSON round trip failed: %v", err)
			}
		}),
		"one_request_frame_bytes": frame.Len(),
		"one_request_encode_ns": measure(func() {
			if err := mcp.WriteRequest(io.Discard, req); err != nil {
				t.Fatal(err)
			}
		}),
		"one_request_decode_ns": measure(func() {
			decoded, err := mcp.ReadRequest(bytes.NewReader(frame.Bytes()))
			if err != nil || decoded.Execute.Code != script {
				t.Fatalf("worker request round trip failed: %v", err)
			}
		}),
	}
}
