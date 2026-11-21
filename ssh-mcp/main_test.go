package main

import (
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/jay-waves/comp7607a/ssh-mcp/internal/mcp"
	"github.com/jay-waves/comp7607a/ssh-mcp/internal/sshtest"
	sdk "github.com/modelcontextprotocol/go-sdk/mcp"
)

func TestMCPOverSSH(t *testing.T) {
	dir := t.TempDir()
	binary := filepath.Join(dir, "ssh-mcp")
	build := exec.Command("go", "build", "-buildvcs=false", "-o", binary, ".")
	if out, err := build.CombinedOutput(); err != nil {
		t.Fatalf("build: %v %s", err, out)
	}
	cache := filepath.Join(dir, "cache")
	env := append(os.Environ(), "XDG_CACHE_HOME="+cache)
	t.Cleanup(func() {
		stop := exec.Command(binary, "worker-stop")
		stop.Env = env
		_ = stop.Run()
	})
	script := filepath.Join(dir, "remote")
	if err := os.WriteFile(script, []byte("#!/bin/sh\nexec '"+binary+"' bridge\n"), 0700); err != nil {
		t.Fatal(err)
	}
	wrapper := sshtest.Wrap(t, script, env)
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	connect := func() *sdk.ClientSession {
		cmd := exec.Command(binary, "mcp")
		cmd.Env = append(env, "SSH_MCP_SSH="+wrapper)
		cmd.Stderr = os.Stderr
		c, err := sdk.NewClient(&sdk.Implementation{Name: "test", Version: "1"}, nil).Connect(ctx, &sdk.CommandTransport{Command: cmd}, nil)
		if err != nil {
			t.Fatal(err)
		}
		return c
	}
	call := func(c *sdk.ClientSession, name string, args any, out any, wantErr bool) {
		t.Helper()
		res, err := c.CallTool(ctx, &sdk.CallToolParams{Name: name, Arguments: args})
		if err != nil || res.IsError != wantErr {
			t.Fatalf("%s: %+v %v", name, res, err)
		}
		if out != nil {
			data, _ := json.Marshal(res.StructuredContent)
			if err = json.Unmarshal(data, out); err != nil {
				t.Fatal(err)
			}
		}
	}
	c := connect()
	var session mcp.Session
	call(c, "session_open", map[string]any{"host": "fixture"}, &session, false)
	var ex mcp.Execution
	call(c, "session_execute", map[string]any{"session_id": session.ID, "code": "x=retained", "wait_ms": 1000}, &ex, false)
	for ex.Output == nil || ex.Output.State != "completed" {
		var out mcp.Output
		call(c, "session_read", map[string]any{"session_id": session.ID, "execution_id": ex.ID, "wait_ms": 100}, &out, false)
		ex.Output = &out
	}
	_ = c.Close()
	c = connect()
	defer c.Close()
	var sessions mcp.Sessions
	call(c, "session_list", map[string]any{"host": "fixture"}, &sessions, false)
	if len(sessions.Sessions) != 1 || sessions.Sessions[0].ID != session.ID {
		t.Fatalf("sessions %+v", sessions)
	}
	call(c, "session_execute", map[string]any{"session_id": session.ID, "code": "printf %s \"$x\"", "wait_ms": 1000}, &ex, false)
	output := ""
	cursor := ""
	for {
		if ex.Output != nil {
			output += ex.Output.Stdout
			cursor = ex.Output.Cursor
			if ex.Output.State == "completed" && ex.Output.OutputClosed {
				break
			}
		}
		var out mcp.Output
		call(c, "session_read", map[string]any{"session_id": session.ID, "execution_id": ex.ID, "cursor": cursor, "wait_ms": 100}, &out, false)
		ex.Output = &out
	}
	if output != "retained" {
		t.Fatalf("output %q", output)
	}
	src := filepath.Join(dir, "input file")
	remote := filepath.Join(dir, "remote file")
	dst := filepath.Join(dir, "downloaded file")
	content := []byte("data\x00\xff")
	if err := os.WriteFile(src, content, 0600); err != nil {
		t.Fatal(err)
	}
	copyArgs := func(direction, local string) map[string]any {
		return map[string]any{"host": "fixture", "direction": direction, "local_path": local, "remote_path": remote}
	}
	call(c, "file_copy", copyArgs("upload", src), nil, false)
	call(c, "file_copy", copyArgs("upload", src), nil, true)
	call(c, "file_copy", copyArgs("download", dst), nil, false)
	data, err := os.ReadFile(dst)
	if err != nil || string(data) != string(content) {
		t.Fatalf("copied %q: %v", data, err)
	}
	cliRemote := filepath.Join(dir, "cli remote")
	cliDownload := filepath.Join(dir, "cli download")
	for _, args := range [][]string{
		{"cp", "upload", "fixture", src, cliRemote},
		{"cp", "download", "fixture", cliDownload, cliRemote},
	} {
		cmd := exec.CommandContext(ctx, binary, args...)
		cmd.Env = append(env, "SSH_MCP_SSH="+wrapper)
		if out, err := cmd.CombinedOutput(); err != nil {
			t.Fatalf("cp %s: %v %s", args[1], err, out)
		}
	}
	if data, err := os.ReadFile(cliDownload); err != nil || string(data) != string(content) {
		t.Fatalf("CLI copy %q: %v", data, err)
	}
	call(c, "session_close", map[string]any{"session_id": session.ID}, nil, false)
	{
		var py mcp.Session
		call(c, "session_open", map[string]any{"host": "fixture", "runtime": "python"}, &py, false)
		if py.Runtime != "python" || py.ProtocolVersion != mcp.ProtocolVersion || py.WorkerVersion != version || py.System == nil || py.System.OS != "linux" ||
			py.System.Arch == "" || py.System.RuntimeVersion == "" || py.System.Cwd == "" {
			t.Fatalf("missing remote runtime metadata: %+v", py)
		}

		execute := func(client *sdk.ClientSession, code string, wantExit int) (string, string) {
			t.Helper()
			var run mcp.Execution
			call(client, "session_execute", mcp.ExecuteInput{SessionID: py.ID, Code: code, WaitMS: 100}, &run, false)
			var stdout, stderr strings.Builder
			cursor := ""
			result := run.Output
			for {
				if result != nil {
					stdout.WriteString(result.Stdout)
					stderr.WriteString(result.Stderr)
					cursor = result.Cursor
					if result.State == "unknown" {
						t.Fatalf("execution outcome unknown: %+v", result)
					}
					if result.State == "completed" && result.OutputClosed {
						if result.ExitCode == nil || *result.ExitCode != wantExit {
							t.Fatalf("execution exit code: %+v, stderr %q", result, stderr.String())
						}
						return stdout.String(), stderr.String()
					}
				}
				var page mcp.Output
				call(client, "session_read", mcp.ReadInput{SessionID: py.ID, ExecutionID: run.ID, Cursor: cursor, WaitMS: 100}, &page, false)
				result = &page
			}
		}

		stdout, stderr := execute(c, "import os, subprocess, sys\nvalues = [6, 7]\nos.write(1, b'direct\\n')\nsubprocess.run([sys.executable, '-c', 'import sys; print(42); print(24, file=sys.stderr)'], check=True)", 0)
		if stdout != "direct\n42\n" || stderr != "24\n" {
			t.Fatalf("unexpected subprocess output: stdout %q, stderr %q", stdout, stderr)
		}
		_, stderr = execute(c, "raise ValueError('fixture error')", 1)
		if !strings.Contains(stderr, "ValueError: fixture error") {
			t.Fatalf("missing Python traceback: %q", stderr)
		}

		if err := c.Close(); err != nil {
			t.Fatal(err)
		}
		reconnected := connect()
		defer reconnected.Close()
		var live mcp.Sessions
		call(reconnected, "session_list", mcp.OpenInput{Host: "fixture"}, &live, false)
		if len(live.Sessions) != 1 || live.Sessions[0].ID != py.ID || live.Sessions[0].Runtime != "python" {
			t.Fatalf("Python session missing after reconnect: %+v", live)
		}
		stdout, stderr = execute(reconnected, "print(sum(values))\nprint('x' * 5000)", 0)
		if stdout != "13\n"+strings.Repeat("x", 5000)+"\n" || stderr != "" {
			t.Fatalf("unexpected output after reconnect: stdout bytes %d, stderr %q", len(stdout), stderr)
		}

		// The child retains its original stdout even while the REPL runs
		// another block with different output streams.
		var background mcp.Execution
		call(reconnected, "session_execute", mcp.ExecuteInput{
			SessionID: py.ID,
			Code:      "delayed = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.read(); print(99)'], stdin=subprocess.PIPE)",
		}, &background, false)
		readBackground := func(cursor string) mcp.Output {
			var out mcp.Output
			call(reconnected, "session_read", mcp.ReadInput{
				SessionID: py.ID, ExecutionID: background.ID, Cursor: cursor, WaitMS: 100,
			}, &out, false)
			if out.State == "unknown" {
				t.Fatalf("background execution outcome unknown: %+v", out)
			}
			return out
		}
		bg := readBackground("")
		for bg.State != "completed" {
			bg = readBackground(bg.Cursor)
		}
		if bg.OutputClosed {
			t.Fatal("background child lost its output stream")
		}
		stdout, stderr = execute(reconnected, "delayed.stdin.close()\ndelayed.wait()\nprint(100)", 0)
		if stdout != "100\n" || stderr != "" {
			t.Fatalf("unexpected output from the next block: stdout %q, stderr %q", stdout, stderr)
		}
		var late strings.Builder
		for !bg.OutputClosed {
			bg = readBackground(bg.Cursor)
			late.WriteString(bg.Stdout)
		}
		if late.String() != "99\n" {
			t.Fatalf("late output assigned to the wrong execution: %q", late.String())
		}
		call(reconnected, "session_close", mcp.SessionInput{SessionID: py.ID}, nil, false)
	}
	stop := exec.Command(binary, "worker-stop")
	stop.Env = env
	if out, err := stop.CombinedOutput(); err != nil {
		t.Fatalf("stop worker: %v %s", err, out)
	}
}
