// ssh-mcp is a small SSH execution and file-copy server for MCP clients.
package main

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/jay-waves/comp7607a/ssh-mcp/internal/mcp"
	sshio "github.com/jay-waves/comp7607a/ssh-mcp/internal/ssh"
	sdk "github.com/modelcontextprotocol/go-sdk/mcp"
	"github.com/pkg/sftp"
	"golang.org/x/sys/unix"
)

const version = "0.1.0"
const remoteCommand = "ssh-mcp bridge"

func main() {
	if err := run(os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
func run(args []string) error {
	if len(args) == 0 {
		return fmt.Errorf("usage: ssh-mcp mcp | cp upload|download HOST LOCAL REMOTE | sessions HOST | bridge")
	}
	ctx := context.Background()
	switch args[0] {
	case "mcp":
		if len(args) != 1 {
			return fmt.Errorf("usage: ssh-mcp mcp")
		}
		client := &mcp.Client{Version: version, Dial: dialWorker}
		defer client.Shutdown()
		server := mcp.New(version, client)
		addCopyTool(server)
		return server.Run(ctx, &sdk.StdioTransport{})
	case "cp":
		if len(args) != 5 {
			return fmt.Errorf("usage: ssh-mcp cp upload|download HOST LOCAL REMOTE")
		}
		return copyFile(ctx, copyInput{Direction: args[1], Host: args[2], LocalPath: args[3], RemotePath: args[4]})
	case "sessions":
		if len(args) != 2 {
			return fmt.Errorf("usage: ssh-mcp sessions HOST")
		}
		client := &mcp.Client{Version: version, Dial: dialWorker}
		defer client.Shutdown()
		sessions, err := client.List(ctx, mcp.OpenInput{Host: args[1]})
		if err != nil {
			return err
		}
		for _, s := range sessions.Sessions {
			fmt.Printf("%s\t%s\t%s\n", s.ID, s.Host, s.State)
		}
		return nil
	case "bridge":
		if len(args) != 1 {
			return fmt.Errorf("bridge takes no arguments")
		}
		return bridge(ctx)
	case "worker":
		if len(args) != 1 {
			return fmt.Errorf("worker takes no arguments")
		}
		return serveWorker()
	case "worker-stop":
		if len(args) != 1 {
			return fmt.Errorf("worker-stop takes no arguments")
		}
		socket, _, err := workerPaths()
		if err != nil {
			return err
		}
		conn, err := net.Dial("unix", socket)
		if err != nil {
			return err
		}
		defer conn.Close()
		if err = mcp.WriteRequest(conn, mcp.Request{Version: version, Method: "shutdown"}); err != nil {
			return err
		}
		response, err := mcp.ReadResponse(conn)
		if err != nil {
			return err
		}
		if response.Error != "" {
			return errors.New(response.Error)
		}
		return nil
	default:
		return fmt.Errorf("unknown command %q", args[0])
	}
}

func sshOptions(host, command string) sshio.Options {
	return sshio.Options{Host: host, Command: command, Args: []string{"-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new"}}
}

type sshStream struct{ *sshio.Process }

func (s sshStream) Close() error { return s.CloseAndWait() }
func dialWorker(ctx context.Context, host string) (io.ReadWriteCloser, error) {
	p, err := sshio.Start(ctx, sshOptions(host, remoteCommand), os.Stderr)
	if err != nil {
		return nil, err
	}
	return sshStream{p}, nil
}

type copyInput struct {
	Host       string `json:"host" jsonschema:"SSH host or configuration alias"`
	Direction  string `json:"direction" jsonschema:"upload or download"`
	LocalPath  string `json:"local_path" jsonschema:"Local file path"`
	RemotePath string `json:"remote_path" jsonschema:"Remote file path"`
}
type copyResult struct {
	Copied bool `json:"copied"`
}

func addCopyTool(server *sdk.Server) {
	sdk.AddTool(server, &sdk.Tool{Name: "file_copy", Description: "Copy one regular file with SFTP; direction is upload or download. Existing destinations are not overwritten."}, func(ctx context.Context, _ *sdk.CallToolRequest, in copyInput) (*sdk.CallToolResult, copyResult, error) {
		err := copyFile(ctx, in)
		return nil, copyResult{Copied: err == nil}, err
	})
}
func copyFile(ctx context.Context, in copyInput) error {
	if in.Direction != "upload" && in.Direction != "download" {
		return fmt.Errorf("direction must be upload or download")
	}
	if in.LocalPath == "" || in.RemotePath == "" || strings.ContainsRune(in.LocalPath+in.RemotePath, 0) {
		return fmt.Errorf("both file paths are required")
	}
	client, err := sshio.Dial(ctx, sshOptions(in.Host, ""))
	if err != nil {
		return err
	}
	defer client.Close()
	stop := context.AfterFunc(ctx, func() { client.Close() })
	defer stop()
	remote, err := sftp.NewClient(client.Client)
	if err != nil {
		return err
	}
	defer remote.Close()
	if in.Direction == "upload" {
		src, err := os.Open(in.LocalPath)
		if err != nil {
			return err
		}
		defer src.Close()
		info, err := src.Stat()
		if err != nil {
			return err
		}
		if !info.Mode().IsRegular() {
			return fmt.Errorf("source is not a regular file")
		}
		dst, err := remote.OpenFile(in.RemotePath, os.O_WRONLY|os.O_CREATE|os.O_EXCL)
		if err != nil {
			return err
		}
		_, writeErr := io.Copy(dst, src)
		closeErr := dst.Close()
		if err = errors.Join(writeErr, closeErr); err != nil {
			_ = remote.Remove(in.RemotePath)
			return err
		}
		return nil
	}
	src, err := remote.Open(in.RemotePath)
	if err != nil {
		return err
	}
	defer src.Close()
	info, err := src.Stat()
	if err != nil {
		return err
	}
	if !info.Mode().IsRegular() {
		return fmt.Errorf("source is not a regular file")
	}
	dst, err := os.OpenFile(in.LocalPath, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return err
	}
	_, writeErr := io.Copy(dst, src)
	closeErr := dst.Close()
	if err = errors.Join(writeErr, closeErr); err != nil {
		_ = os.Remove(in.LocalPath)
		return err
	}
	return nil
}

// A private per-user Unix socket lets independent SSH channels reuse sessions.
func workerPaths() (string, string, error) {
	base, err := os.UserCacheDir()
	if err != nil {
		return "", "", err
	}
	dir := filepath.Join(base, "ssh-mcp")
	if err = os.MkdirAll(dir, 0700); err != nil {
		return "", "", err
	}
	info, err := os.Lstat(dir)
	if err != nil {
		return "", "", err
	}
	if !info.IsDir() || info.Mode().Perm() != 0700 {
		return "", "", fmt.Errorf("worker directory must be private: %s", dir)
	}
	return filepath.Join(dir, "worker.sock"), filepath.Join(dir, "worker.lock"), nil
}
func bridge(ctx context.Context) error {
	socket, lockPath, err := workerPaths()
	if err != nil {
		return err
	}
	conn, err := net.Dial("unix", socket)
	if err != nil {
		conn, err = func() (net.Conn, error) {
			lock, err := os.OpenFile(lockPath, os.O_CREATE|os.O_RDWR, 0600)
			if err != nil {
				return nil, err
			}
			defer lock.Close()
			if err = unix.Flock(int(lock.Fd()), unix.LOCK_EX); err != nil {
				return nil, err
			}
			defer unix.Flock(int(lock.Fd()), unix.LOCK_UN)
			conn, err = net.Dial("unix", socket)
			if err != nil {
				exe, e := os.Executable()
				if e != nil {
					return nil, e
				}
				cmd := exec.Command(exe, "worker")
				cmd.Stdin = nil
				cmd.Stdout = nil
				cmd.Stderr = nil
				cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
				if e = cmd.Start(); e != nil {
					return nil, e
				}
				_ = cmd.Process.Release()
				deadline := time.Now().Add(5 * time.Second)
				for time.Now().Before(deadline) {
					conn, err = net.Dial("unix", socket)
					if err == nil {
						break
					}
					time.Sleep(20 * time.Millisecond)
				}
				if err != nil {
					return nil, fmt.Errorf("worker did not start: %w", err)
				}
			}
			return conn, nil
		}()
		if err != nil {
			return err
		}
	}
	defer conn.Close()
	go func() { _, _ = io.Copy(conn, os.Stdin); _ = conn.(*net.UnixConn).CloseWrite() }()
	_, err = io.Copy(os.Stdout, conn)
	return err
}

type worker struct {
	mu       sync.Mutex
	sessions map[string]*mcp.BashSession
	stopping bool
}

func (w *worker) Open(ctx context.Context, in mcp.OpenInput) (mcp.Session, error) {
	s, err := mcp.StartBashSession(ctx)
	if err != nil {
		return mcp.Session{}, err
	}
	var token [16]byte
	if _, err = rand.Read(token[:]); err != nil {
		_ = s.Close()
		return mcp.Session{}, err
	}
	id := hex.EncodeToString(token[:])
	w.mu.Lock()
	if w.stopping {
		w.mu.Unlock()
		_ = s.Close()
		return mcp.Session{}, fmt.Errorf("worker is stopping")
	}
	w.sessions[id] = s
	w.mu.Unlock()
	return mcp.Session{ID: id, Host: in.Host, State: "ready"}, nil
}
func (w *worker) lookup(id string) (*mcp.BashSession, error) {
	w.mu.Lock()
	defer w.mu.Unlock()
	s := w.sessions[id]
	if s == nil {
		return nil, fmt.Errorf("session not found")
	}
	return s, nil
}
func (w *worker) List(_ context.Context, in mcp.OpenInput) (mcp.Sessions, error) {
	w.mu.Lock()
	defer w.mu.Unlock()
	out := mcp.Sessions{Sessions: []mcp.Session{}}
	for id, s := range w.sessions {
		out.Sessions = append(out.Sessions, mcp.Session{ID: id, Host: in.Host, State: s.State()})
	}
	return out, nil
}
func (w *worker) Execute(ctx context.Context, in mcp.ExecuteInput) (mcp.Execution, error) {
	s, e := w.lookup(in.SessionID)
	if e != nil {
		return mcp.Execution{}, e
	}
	return s.Execute(ctx, in)
}
func (w *worker) Read(ctx context.Context, in mcp.ReadInput) (mcp.Output, error) {
	s, e := w.lookup(in.SessionID)
	if e != nil {
		return mcp.Output{}, e
	}
	return s.Read(ctx, in)
}
func (w *worker) Close(_ context.Context, in mcp.SessionInput) (mcp.Closed, error) {
	w.mu.Lock()
	s := w.sessions[in.SessionID]
	delete(w.sessions, in.SessionID)
	w.mu.Unlock()
	if s == nil {
		return mcp.Closed{}, fmt.Errorf("session not found")
	}
	if err := s.Close(); err != nil {
		return mcp.Closed{}, err
	}
	return mcp.Closed{Closed: true}, nil
}
func serveWorker() error {
	socket, _, err := workerPaths()
	if err != nil {
		return err
	}
	if existing, err := net.Dial("unix", socket); err == nil {
		_ = existing.Close()
		return fmt.Errorf("worker already running")
	}
	_ = os.Remove(socket)
	ln, err := net.Listen("unix", socket)
	if err != nil {
		return err
	}
	defer ln.Close()
	if err = os.Chmod(socket, 0600); err != nil {
		return err
	}
	w := &worker{sessions: make(map[string]*mcp.BashSession)}
	defer func() {
		w.mu.Lock()
		w.stopping = true
		sessions := w.sessions
		w.sessions = make(map[string]*mcp.BashSession)
		w.mu.Unlock()
		for _, session := range sessions {
			_ = session.Close()
		}
	}()
	for {
		c, err := ln.Accept()
		if err != nil {
			if errors.Is(err, net.ErrClosed) {
				return nil
			}
			return err
		}
		go func() {
			defer c.Close()
			for {
				req, err := mcp.ReadRequest(c)
				if err != nil {
					return
				}
				if req.Method == "shutdown" && req.Version == version {
					_ = mcp.WriteResponse(c, mcp.Response{Version: version})
					_ = ln.Close()
					return
				}
				if err = mcp.WriteResponse(c, mcp.Dispatch(context.Background(), w, req, version)); err != nil {
					return
				}
			}
		}()
	}
}
