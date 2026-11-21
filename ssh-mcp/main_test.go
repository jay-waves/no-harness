package main

import (
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
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
	stop := exec.Command(binary, "worker-stop")
	stop.Env = env
	if out, err := stop.CombinedOutput(); err != nil {
		t.Fatalf("stop worker: %v %s", err, out)
	}
}
