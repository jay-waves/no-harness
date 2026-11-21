//go:build linux

package mcp

import (
	"bufio"
	"context"
	"crypto/rand"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/jay-waves/comp7607a/ssh-mcp/internal/proc"
)

const bashOutputLimit = 1 << 20
const bashExecutionLimit = 16

// ProcessSession owns an interpreter and its bounded execution history.
type ProcessSession struct {
	runtime     string
	version     string
	cmd         *exec.Cmd
	input       io.Writer
	status      *bufio.Reader
	dir         string
	mu          sync.Mutex
	unavailable atomic.Bool
	stop        sync.Once
	finished    chan struct{}
	waitErr     error
	executions  []*bashExecution
}

// bashExecution exposes foreground completion separately from stream closure.
type bashExecution struct {
	ID         string
	Done       chan struct{}
	OutputDone chan struct{}
	mu         sync.Mutex
	result     bashResult
	changed    chan struct{}
}
type bashResult struct {
	Stdout, Stderr                    []byte
	ExitCode                          int
	Complete, OutputClosed, Truncated bool
	Err                               error
}

// readOutput copies a bounded chunk from each stream after at most one wait
// for an observable change. Only waiting readers need a notification channel.
func (e *bashExecution) readOutput(ctx context.Context, stdout, stderr, limit int, wait time.Duration) (bashResult, error) {
	for {
		r, changed, err := e.outputChunk(stdout, stderr, limit, wait > 0)
		if changed == nil {
			return r, err
		}
		timer := time.NewTimer(wait)
		defer timer.Stop()
		select {
		case <-ctx.Done():
			return bashResult{}, ctx.Err()
		case <-changed:
		case <-timer.C:
		}
		wait = 0
	}
}

func (e *bashExecution) outputChunk(stdout, stderr, limit int, wait bool) (bashResult, <-chan struct{}, error) {
	e.mu.Lock()
	defer e.mu.Unlock()
	r := e.result
	if stdout < 0 || stdout > len(r.Stdout) || stderr < 0 || stderr > len(r.Stderr) || limit < 0 {
		return bashResult{}, nil, fmt.Errorf("invalid output cursor")
	}
	out, errOut := r.Stdout[stdout:], r.Stderr[stderr:]
	var changed <-chan struct{}
	if wait && len(out) == 0 && len(errOut) == 0 && !r.OutputClosed && r.Err == nil {
		if e.changed == nil {
			e.changed = make(chan struct{})
		}
		changed = e.changed
	}
	r.Stdout = slices.Clone(out[:min(len(out), limit)])
	r.Stderr = slices.Clone(errOut[:min(len(errOut), limit)])
	r.OutputClosed = r.OutputClosed && len(r.Stdout) == len(out) && len(r.Stderr) == len(errOut)
	return r, changed, nil
}
func (e *bashExecution) notify() {
	if e.changed != nil {
		close(e.changed)
		e.changed = nil
	}
}

func StartBashSession(ctx context.Context) (*ProcessSession, error) {
	return StartSession(ctx, "bash")
}

func StartSession(ctx context.Context, runtimeName string) (result *ProcessSession, _ error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	var cmd *exec.Cmd
	switch runtimeName {
	case "bash":
		cmd = exec.Command("bash", "--noprofile", "--norc")
	case "python":
		cmd = exec.Command("python3", "-u", "-c", pythonBootstrap)
	default:
		return nil, fmt.Errorf("unsupported runtime %q", runtimeName)
	}
	dir, err := os.MkdirTemp("", "ssh-mcp-"+runtimeName+"-")
	if err != nil {
		return nil, err
	}
	defer func() {
		if result == nil {
			_ = os.RemoveAll(dir)
		}
	}()
	controlRead, controlWrite, err := os.Pipe()
	if err != nil {
		return nil, err
	}
	defer controlWrite.Close()
	defer func() {
		if result == nil {
			_ = controlRead.Close()
		}
	}()
	cmd.ExtraFiles = []*os.File{controlWrite}
	input, err := cmd.StdinPipe()
	if err == nil {
		err = cmd.Start()
	}
	if err != nil {
		return nil, fmt.Errorf("start %s interpreter: %w", runtimeName, err)
	}
	// Read the version from the same process that will execute submitted code.
	// A deadline prevents broken interpreter wrappers from blocking open forever.
	deadline := time.Now().Add(5 * time.Second)
	if sooner, ok := ctx.Deadline(); ok && sooner.Before(deadline) {
		deadline = sooner
	}
	status := bufio.NewReader(controlRead)
	readVersion := func() (string, error) {
		if err := controlRead.SetReadDeadline(deadline); err != nil {
			return "", err
		}
		if runtimeName == "bash" {
			if _, err := io.WriteString(input, "builtin printf '%s\\n' \"$BASH_VERSION\" >&3\n"); err != nil {
				return "", err
			}
		}
		line, err := status.ReadSlice('\n')
		if err != nil {
			return "", err
		}
		if err := controlRead.SetReadDeadline(time.Time{}); err != nil {
			return "", err
		}
		return strings.TrimSpace(string(line)), nil
	}
	runtimeVersion, err := readVersion()
	if err != nil || runtimeVersion == "" {
		_ = cmd.Process.Kill()
		_ = proc.Wait(cmd)
		if err != nil {
			return nil, fmt.Errorf("%s interpreter did not report its version: %w", runtimeName, err)
		}
		return nil, fmt.Errorf("%s interpreter reported an empty version", runtimeName)
	}
	b := &ProcessSession{runtime: runtimeName, version: runtimeVersion, cmd: cmd, input: input, status: status, dir: dir, finished: make(chan struct{})}
	go func() {
		b.waitErr = proc.Wait(cmd)
		b.unavailable.Store(true)
		// Descendants may inherit the descriptor after Bash exits.
		_ = controlRead.Close()
		// Let any submission settle before removing its temporary paths.
		b.mu.Lock()
		if n := len(b.executions); n != 0 {
			<-b.executions[n-1].Done
		}
		_ = os.RemoveAll(dir)
		b.mu.Unlock()
		close(b.finished)
	}()
	return b, nil
}

func bashQuote(s string) string { return "'" + strings.ReplaceAll(s, "'", "'\\''") + "'" }

// run executes code in the current interpreter. Background descendants can
// keep output streams open; their output remains associated with this call.
func (b *ProcessSession) run(ctx context.Context, code, stdin string) (*bashExecution, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	oldest := -1
	if len(b.executions) == bashExecutionLimit {
		oldest = slices.IndexFunc(b.executions, func(e *bashExecution) bool {
			return channelClosed(e.Done) && channelClosed(e.OutputDone)
		})
		if oldest < 0 {
			return nil, fmt.Errorf("execution limit reached; output streams are still open")
		}
	}
	if n := len(b.executions); n > 0 && !channelClosed(b.executions[n-1].Done) {
		return nil, fmt.Errorf("%s session is busy", b.runtime)
	}
	if b.unavailable.Load() {
		return nil, fmt.Errorf("%s session closed", b.runtime)
	}
	if strings.ContainsRune(code, 0) {
		return nil, fmt.Errorf("NUL in %s code", b.runtime)
	}
	id := rand.Text()
	path := filepath.Join(b.dir, id)
	if err := os.Mkdir(path, 0700); err != nil {
		return nil, err
	}
	var readers [2]*os.File
	submitted := false
	defer func() {
		if submitted {
			return
		}
		for _, reader := range readers {
			if reader != nil {
				_ = reader.Close()
			}
		}
		_ = os.RemoveAll(path)
	}()
	codePath, inPath := filepath.Join(path, "code"), filepath.Join(path, "stdin")
	outPath, errPath := filepath.Join(path, "stdout"), filepath.Join(path, "stderr")
	if err := os.WriteFile(codePath, []byte(code), 0600); err != nil {
		return nil, err
	}
	if err := os.WriteFile(inPath, []byte(stdin), 0600); err != nil {
		return nil, err
	}
	for i, name := range [...]string{outPath, errPath} {
		if err := syscall.Mkfifo(name, 0600); err != nil {
			return nil, err
		}
		// Open readers before submission so Bash can open both writers.
		// A nonblocking open also lets us clean up if Bash never opens them.
		reader, err := os.OpenFile(name, os.O_RDONLY|syscall.O_NONBLOCK, 0)
		if err != nil {
			return nil, err
		}
		readers[i] = reader
	}

	execution := &bashExecution{ID: id, Done: make(chan struct{}), OutputDone: make(chan struct{})}
	read := func(f *os.File, dst *[]byte, stream string) error {
		defer f.Close()
		buf := make([]byte, 32*1024)
		for {
			n, e := f.Read(buf)
			if n > 0 {
				execution.mu.Lock()
				remaining := bashOutputLimit - len(*dst)
				changed := remaining > 0 || !execution.result.Truncated
				if n > remaining {
					execution.result.Truncated = true
				}
				if remaining > 0 {
					*dst = append(*dst, buf[:min(n, remaining)]...)
				}
				if changed {
					execution.notify()
				}
				execution.mu.Unlock()
			}
			if errors.Is(e, io.EOF) {
				return nil
			}
			if e != nil {
				return fmt.Errorf("%s: %w", stream, e)
			}
		}
	}
	var command string
	if b.runtime == "python" {
		job, err := json.Marshal(map[string]string{
			"id": id, "code": codePath, "stdin": inPath, "stdout": outPath, "stderr": errPath,
		})
		if err != nil {
			return nil, err
		}
		command = string(job) + "\n"
	} else {
		command = fmt.Sprintf("{ builtin printf '%%s ready\\n' %s >&3; builtin source %s; } <%s >%s 2>%s; builtin printf '%%s %%s\\n' %s \"$?\" >&3\n",
			bashQuote(id), bashQuote(codePath), bashQuote(inPath), bashQuote(outPath), bashQuote(errPath), bashQuote(id))
	}
	if _, err := io.WriteString(b.input, command); err != nil {
		// A partial submission makes this input stream unsuitable for another job.
		b.unavailable.Store(true)
		return nil, err
	}
	submitted = true
	if oldest >= 0 {
		b.executions = slices.Delete(b.executions, oldest, oldest+1)
	}
	b.executions = append(b.executions, execution)
	go func() {
		line, err := b.status.ReadSlice('\n')
		// Before this marker, a FIFO with no writers can look like EOF.
		// A missing marker means setup failed or Bash exited. The already
		// opened readers can still drain any output and finish.
		go func() {
			var stdout sync.WaitGroup
			var stdoutErr error
			stdout.Go(func() {
				stdoutErr = read(readers[0], &execution.result.Stdout, "stdout")
			})
			stderrErr := read(readers[1], &execution.result.Stderr, "stderr")
			stdout.Wait()
			outputErr := errors.Join(stdoutErr, stderrErr)
			execution.mu.Lock()
			execution.result.OutputClosed = outputErr == nil
			if outputErr != nil {
				execution.result.Err = errors.Join(execution.result.Err, fmt.Errorf("output read failed: %w", outputErr))
			}
			close(execution.OutputDone)
			execution.notify()
			execution.mu.Unlock()
			_ = os.RemoveAll(path)
		}()
		if err == nil && string(line) == id+" ready\n" {
			line, err = b.status.ReadSlice('\n')
		}
		fields := strings.Fields(string(line))
		code := 0
		if err == nil && (len(fields) != 2 || fields[0] != id) {
			err = fmt.Errorf("invalid %s completion", b.runtime)
		}
		if err == nil {
			code, err = strconv.Atoi(fields[1])
		}
		if err != nil {
			b.unavailable.Store(true)
		}
		execution.mu.Lock()
		if err != nil {
			execution.result.Err = errors.Join(execution.result.Err, fmt.Errorf("%s completion unavailable: %w", b.runtime, err))
		} else {
			execution.result.Complete = true
			execution.result.ExitCode = code
		}
		close(execution.Done) // Admit the next script before notifying output readers.
		execution.notify()
		execution.mu.Unlock()
	}()
	return execution, nil
}

func (b *ProcessSession) Close() error {
	b.stop.Do(func() {
		b.unavailable.Store(true)
		_ = b.cmd.Process.Kill()
	})
	<-b.finished
	err := b.waitErr
	if _, ok := errors.AsType[*exec.ExitError](err); ok {
		return nil
	}
	return err
}

// Version describes the running interpreter.
func (b *ProcessSession) Version() string { return b.version }

// PID identifies the owned process for the application lifecycle.
func (b *ProcessSession) PID() int { return b.cmd.Process.Pid }

func channelClosed(ch <-chan struct{}) bool {
	select {
	case <-ch:
		return true
	default:
		return false
	}
}
