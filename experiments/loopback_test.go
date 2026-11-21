//go:build linux

package main

import (
	"context"
	"encoding/json"
	"fmt"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/jay-waves/comp7607a/ssh-mcp/internal/mcp"
	"github.com/jay-waves/comp7607a/ssh-mcp/internal/sshtest"
	sdk "github.com/modelcontextprotocol/go-sdk/mcp"
	cryptossh "golang.org/x/crypto/ssh"
	"golang.org/x/crypto/ssh/agent"
)

type callRow struct {
	Mode     string  `json:"mode"`
	Workflow string  `json:"workflow"`
	Rep      int     `json:"rep"`
	Turn     int     `json:"turn"`
	MS       float64 `json:"ms"`
	Output   string  `json:"output"`
	ExitCode int     `json:"exit_code"`
	Error    string  `json:"error"`
}
type setupRow struct {
	Mode     string  `json:"mode"`
	Workflow string  `json:"workflow,omitempty"`
	Rep      int     `json:"rep,omitempty"`
	MS       float64 `json:"ms"`
	Detail   string  `json:"detail"`
}
type statRow struct {
	Mode     string  `json:"mode"`
	Workflow string  `json:"workflow"`
	Turn     int     `json:"turn"`
	N        int     `json:"n"`
	MedianMS float64 `json:"median_ms"`
	P95MS    float64 `json:"p95_ms"`
}

func TestExecutionPilot(t *testing.T) {
	output := os.Getenv("NO_HARNESS_PILOT_OUTPUT")
	if output == "" {
		t.Fatal("NO_HARNESS_PILOT_OUTPUT must name the results file")
	}
	root := t.TempDir()
	bin := filepath.Join(root, "ssh-mcp")
	if out, e := exec.Command("go", "build", "-buildvcs=false", "-o", bin, ".").CombinedOutput(); e != nil {
		t.Fatalf("build: %v %s", e, out)
	}
	data := filepath.Join(root, "data")
	if e := os.MkdirAll(data, 0700); e != nil {
		t.Fatal(e)
	}
	file := filepath.Join(data, "bytes.bin")
	_ = os.WriteFile(file, []byte(strings.Repeat("0123456789abcdef\n", 4096)), 0600)
	for n, s := range map[string]string{"a.txt": "keep\nskip\nkeep too\n", "b.txt": "skip\nkeep three\n"} {
		if e := os.WriteFile(filepath.Join(data, n), []byte(s), 0600); e != nil {
			t.Fatal(e)
		}
	}
	csv := filepath.Join(data, "integers.csv")
	f, e := os.Create(csv)
	if e != nil {
		t.Fatal(e)
	}
	for i := 1; i <= 100000; i++ {
		fmt.Fprintln(f, i)
	}
	if e = f.Close(); e != nil {
		t.Fatal(e)
	}
	env := append(os.Environ(), "XDG_CACHE_HOME="+filepath.Join(root, "cache"))
	script := filepath.Join(root, "remote")
	body := "#!/bin/sh\n[ \"$1\" = -- ] && shift\n[ \"$1\" = fixture ] && shift\nif [ \"$1\" = 'ssh-mcp bridge' ]; then exec '" + bin + "' bridge; fi\ncd '" + data + "'\nexec /bin/sh -c \"$1\"\n"
	if e = os.WriteFile(script, []byte(body), 0700); e != nil {
		t.Fatal(e)
	}
	wrapper := sshtest.Wrap(t, script, env)
	cfgBytes, e := exec.Command(wrapper, "-G", "fixture").Output()
	if e != nil {
		t.Fatal(e)
	}
	cfg := map[string]string{}
	for _, ln := range strings.Split(string(cfgBytes), "\n") {
		k, v, ok := strings.Cut(ln, " ")
		if ok {
			cfg[k] = v
		}
	}
	port, _ := strconv.Atoi(cfg["port"])
	agentConn, e := net.Dial("unix", cfg["identityagent"])
	if e != nil {
		t.Fatal(e)
	}
	defer agentConn.Close()
	signers, e := agent.NewClient(agentConn).Signers()
	if e != nil {
		t.Fatal(e)
	}
	dialAt := time.Now()
	sshConn, e := cryptossh.Dial("tcp", fmt.Sprintf("127.0.0.1:%d", port), &cryptossh.ClientConfig{User: "fixture", Auth: []cryptossh.AuthMethod{cryptossh.PublicKeys(signers...)}, HostKeyCallback: cryptossh.InsecureIgnoreHostKey(), Timeout: 5 * time.Second})
	if e != nil {
		t.Fatal(e)
	}
	defer sshConn.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Minute)
	defer cancel()
	cmd := exec.Command(bin, "mcp")
	cmd.Env = append(env, "SSH_MCP_SSH="+wrapper)
	mcpAt := time.Now()
	client, e := sdk.NewClient(&sdk.Implementation{Name: "pilot", Version: "1"}, nil).Connect(ctx, &sdk.CommandTransport{Command: cmd}, nil)
	if e != nil {
		t.Fatal(e)
	}
	defer client.Close()
	stop := exec.Command(bin, "worker-stop")
	stop.Env = env
	defer stop.Run()
	var setup []setupRow
	setup = append(setup, setupRow{Mode: "direct", MS: ms(time.Since(dialAt)), Detail: "warm authenticated SSH transport; one client, new exec channel per turn"}, setupRow{Mode: "mcp", MS: ms(time.Since(mcpAt)), Detail: "stdio MCP server startup and SDK handshake; worker SSH connection starts on first session_open"})
	var calls []callRow
	sessions := map[string]string{}
	currentRep := 0
	callTool := func(name string, args, out any) error {
		r, er := client.CallTool(ctx, &sdk.CallToolParams{Name: name, Arguments: args})
		if er != nil {
			return er
		}
		if r.IsError {
			return fmt.Errorf("%s: tool error", name)
		}
		if out != nil {
			b, _ := json.Marshal(r.StructuredContent)
			return json.Unmarshal(b, out)
		}
		return nil
	}
	open := func(mode string, rep int) {
		at := time.Now()
		args := map[string]any{"host": "fixture"}
		if mode == "python" {
			args["runtime"] = "python"
		}
		var s mcp.Session
		if er := callTool("session_open", args, &s); er != nil {
			t.Fatal(er)
		}
		sessions[mode] = s.ID
		setup = append(setup, setupRow{Mode: mode, Rep: rep, MS: ms(time.Since(at)), Detail: "session_open tool call; new REPL for each repetition"})
	}
	repl := func(mode, wf string, turn int, code string) string {
		at := time.Now()
		var ex mcp.Execution
		if er := callTool("session_execute", mcp.ExecuteInput{SessionID: sessions[mode], Code: code, WaitMS: 1000}, &ex); er != nil {
			t.Fatalf("%s/%s turn %d: %v", mode, wf, turn, er)
		}
		var b strings.Builder
		cur := ""
		if ex.Output != nil {
			b.WriteString(ex.Output.Stdout)
			cur = ex.Output.Cursor
		}
		for ex.Output == nil || ex.Output.State != "completed" || !ex.Output.OutputClosed {
			var o mcp.Output
			if er := callTool("session_read", mcp.ReadInput{SessionID: sessions[mode], ExecutionID: ex.ID, Cursor: cur, WaitMS: 1000}, &o); er != nil {
				t.Fatalf("%s/%s turn %d read: %v", mode, wf, turn, er)
			}
			ex.Output = &o
			cur = o.Cursor
			b.WriteString(o.Stdout)
			if o.State == "unknown" {
				t.Fatalf("%s/%s unknown execution state", mode, wf)
			}
		}
		exitCode := -1
		if ex.Output.ExitCode != nil {
			exitCode = *ex.Output.ExitCode
		}
		out := b.String()
		calls = append(calls, callRow{mode, wf, currentRep, turn, ms(time.Since(at)), out, exitCode, ""})
		if exitCode != 0 {
			t.Fatalf("%s/%s turn %d exit %d stderr=%q", mode, wf, turn, exitCode, ex.Output.Stderr)
		}
		return out
	}
	direct := func(wf string, turn int, command string) string {
		at := time.Now()
		s, er := sshConn.NewSession()
		if er != nil {
			t.Fatal(er)
		}
		out, er := s.CombinedOutput(command)
		_ = s.Close()
		code := 0
		if er != nil {
			code = 1
		}
		calls = append(calls, callRow{"direct", wf, currentRep, turn, ms(time.Since(at)), string(out), code, errString(er)})
		if er != nil {
			t.Fatalf("direct/%s turn %d: %v: %s", wf, turn, er, out)
		}
		return string(out)
	}
	setupDirect := func(wf string, rep int, command string) {
		at := time.Now()
		s, er := sshConn.NewSession()
		if er == nil {
			var b []byte
			b, er = s.CombinedOutput(command)
			_ = s.Close()
			if er == nil && len(b) > 0 {
				er = fmt.Errorf("unexpected setup output %q", b)
			}
		}
		if er != nil {
			t.Fatalf("direct setup: %v", er)
		}
		setup = append(setup, setupRow{Mode: "direct", Workflow: wf, Rep: rep, MS: ms(time.Since(at)), Detail: "fixture and filter setup; excluded from workload calls"})
	}
	setupREPL := func(mode, wf string, rep int, code string) {
		before := len(calls)
		out := repl(mode, wf, -1, code)
		row := calls[len(calls)-1]
		calls = calls[:before]
		if out != "" || row.ExitCode != 0 {
			t.Fatalf("%s setup failed: %+v", mode, row)
		}
		setup = append(setup, setupRow{Mode: mode, Workflow: wf, Rep: rep, MS: row.MS, Detail: "fixture and filter setup turn; excluded from workload calls"})
	}
	// One warm execution per mode, then remove its calls and sessions from measurements.
	setupBase := len(setup)
	direct("warm", 0, "printf warm")
	open("bash", 0)
	repl("bash", "warm", 0, "printf warm")
	open("python", 0)
	repl("python", "warm", 0, "print('warm')")
	for _, mode := range []string{"bash", "python"} {
		var closed mcp.Closed
		if er := callTool("session_close", mcp.SessionInput{SessionID: sessions[mode]}, &closed); er != nil {
			t.Fatal(er)
		}
	}
	calls = nil
	setup = setup[:setupBase]
	bytesExpected := strconv.Itoa(len(strings.Repeat("0123456789abcdef\n", 4096))) + "\n"
	qcsv := strconv.Quote(csv)
	csvLoad := "import csv\nwith open(" + qcsv + ") as f: values=[int(r[0]) for r in csv.reader(f)]\n"
	csvCount := "python3 -c " + shellQuote(csvLoad+"print(len(values))")
	csvSum := "python3 -c " + shellQuote(csvLoad+"print(sum(values))")
	csvThreshold := "python3 -c " + shellQuote(csvLoad+"print(sum(v > 50000 for v in values))")
	for rep := 1; rep <= 5; rep++ {
		currentRep = rep
		open("bash", rep)
		open("python", rep)
		for _, wf := range []string{"bytes", "filter", "csv"} {
			for _, mode := range []string{"direct", "bash", "python"} {
				run := filepath.Join(root, fmt.Sprintf("run-%d-%s-%s", rep, wf, mode))
				switch wf {
				case "bytes":
					var got string
					if mode == "python" {
						got = repl(mode, wf, 1, "import os; print(os.path.getsize("+strconv.Quote(file)+"))")
					} else {
						got = directOrBash(mode, wf, 1, "wc -c < "+shellQuote(file), repl, direct)
					}
					if got != bytesExpected {
						t.Fatalf("%s bytes %q want %q", mode, got, bytesExpected)
					}
				case "filter":
					setupShell := "mkdir -p " + shellQuote(run) + "; cp " + shellQuote(filepath.Join(data, "a.txt")) + " " + shellQuote(filepath.Join(run, "a.txt")) + "; cp " + shellQuote(filepath.Join(data, "b.txt")) + " " + shellQuote(filepath.Join(run, "b.txt")) + "; printf '#!/bin/sh\\ngrep keep \"$1\"\\n' > " + shellQuote(filepath.Join(run, "filter")) + "; chmod +x " + shellQuote(filepath.Join(run, "filter"))
					if mode == "direct" {
						setupDirect(wf, rep, setupShell)
					} else if mode == "bash" {
						setupREPL(mode, wf, rep, "mkdir -p "+shellQuote(run)+"; cp "+shellQuote(filepath.Join(data, "a.txt"))+" "+shellQuote(filepath.Join(run, "a.txt"))+"; cp "+shellQuote(filepath.Join(data, "b.txt"))+" "+shellQuote(filepath.Join(run, "b.txt"))+"; cd "+shellQuote(run)+"; filter() { grep keep \"$1\"; }")
					} else {
						code := "import os,pathlib\np=pathlib.Path(" + strconv.Quote(run) + ");p.mkdir(parents=True,exist_ok=True);os.chdir(p)\nfor n in ('a.txt','b.txt'): pathlib.Path(n).write_text(pathlib.Path(" + strconv.Quote(data) + ").joinpath(n).read_text())\ndef apply_filter(path):\n return ''.join(line for line in open(path) if 'keep' in line)"
						setupREPL(mode, wf, rep, code)
					}
					var a, b string
					if mode == "python" {
						a = repl(mode, wf, 1, "print(apply_filter('a.txt'),end='')")
						b = repl(mode, wf, 2, "print(apply_filter('b.txt'),end='')")
					} else if mode == "bash" {
						a = repl(mode, wf, 1, "filter a.txt")
						b = repl(mode, wf, 2, "filter b.txt")
					} else {
						a = direct(wf, 1, shellQuote(filepath.Join(run, "filter"))+" "+shellQuote(filepath.Join(run, "a.txt")))
						b = direct(wf, 2, shellQuote(filepath.Join(run, "filter"))+" "+shellQuote(filepath.Join(run, "b.txt")))
					}
					if a != "keep\nkeep too\n" || b != "keep three\n" {
						t.Fatalf("%s filter outputs %q %q", mode, a, b)
					}
				case "csv":
					var a, b, c string
					if mode == "python" {
						a = repl(mode, wf, 1, csvLoad+"print(len(values))")
						b = repl(mode, wf, 2, "print(sum(values))")
						c = repl(mode, wf, 3, "print(sum(v > 50000 for v in values))")
					} else {
						a = directOrBash(mode, wf, 1, csvCount, repl, direct)
						b = directOrBash(mode, wf, 2, csvSum, repl, direct)
						c = directOrBash(mode, wf, 3, csvThreshold, repl, direct)
					}
					if strings.TrimSpace(a) != "100000" || strings.TrimSpace(b) != "5000050000" || strings.TrimSpace(c) != "50000" {
						t.Fatalf("%s csv outputs %q %q %q", mode, a, b, c)
					}
				}
			}
		}
		for _, mode := range []string{"bash", "python"} {
			var closed mcp.Closed
			if er := callTool("session_close", mcp.SessionInput{SessionID: sessions[mode]}, &closed); er != nil {
				t.Fatal(er)
			}
		}
	}
	expected := 5 * 3 * (1 + 2 + 3)
	if len(calls) != expected {
		t.Fatalf("measured %d calls, wanted %d", len(calls), expected)
	}
	for _, c := range calls {
		if c.Error != "" || c.ExitCode != 0 {
			t.Errorf("failed call: %+v", c)
		}
	}
	// Summarize medians and p95 per mode/workflow/turn. Setup remains separate.
	var stats []statRow
	for _, mode := range []string{"direct", "bash", "python"} {
		for _, wf := range []string{"bytes", "filter", "csv"} {
			maxTurn := 1
			if wf == "filter" {
				maxTurn = 2
			}
			if wf == "csv" {
				maxTurn = 3
			}
			for turn := 1; turn <= maxTurn; turn++ {
				var xs []float64
				for _, c := range calls {
					if c.Mode == mode && c.Workflow == wf && c.Turn == turn {
						xs = append(xs, c.MS)
					}
				}
				sort.Float64s(xs)
				if len(xs) > 0 {
					stats = append(stats, statRow{mode, wf, turn, len(xs), xs[(len(xs)-1)/2], xs[(len(xs)*95+99)/100-1]})
				}
			}
		}
	}
	result := struct {
		Transport     string         `json:"transport"`
		Rows          int            `json:"csv_rows"`
		Reads         map[string]int `json:"csv_reads_per_workflow"`
		Expected      string         `json:"expected_outputs"`
		Successes     int            `json:"successes"`
		ExpectedCalls int            `json:"expected_calls"`
		Setup         []setupRow     `json:"setup_times"`
		TurnStats     []statRow      `json:"turn_stats"`
		Calls         []callRow      `json:"calls"`
		Memory        string         `json:"target_memory"`
		Deviation     string         `json:"deviation"`
	}{"Direct: one authenticated SSH connection with a fresh channel for every command. Bash/Python: official Go MCP SDK stdio CommandTransport, call_tool via session_open/execute/read/close; shared fixture SSH worker.", 100000, map[string]int{"direct": 3, "bash": 3, "python": 1}, "byte count 69632; filter results keep/keep too and keep three; CSV count 100000, sum 5000050000, above 50000 count 50000", len(calls), expected, setup, stats, calls, "Unavailable: loopback fixture executes target commands as local subprocesses without an isolated target cgroup; process PSS/RSS would include host-level sharing.", "CSV uses identical Python stdlib csv parsing in all modes; direct and Bash launch a fresh Python process on each step; Python REPL parses once on turn 1 and reuses list. Each REPL session reset and timed at each repetition; the direct authenticated SSH transport stays warm across repetitions."}
	b, _ := json.MarshalIndent(result, "", "  ")
	if er := os.WriteFile(output, b, 0600); er != nil {
		t.Fatal(er)
	}
	fmt.Printf("PILOT_RESULT_FILE=%s calls=%d setups=%d stats=%d\n", output, len(calls), len(setup), len(stats))
}
func ms(d time.Duration) float64 { return float64(d.Microseconds()) / 1000 }
func errString(e error) string {
	if e == nil {
		return ""
	}
	return e.Error()
}
func shellQuote(s string) string { return "'" + strings.ReplaceAll(s, "'", "'\\''") + "'" }
func directOrBash(mode, wf string, turn int, code string, repl func(string, string, int, string) string, direct func(string, int, string) string) string {
	if mode == "bash" {
		return repl(mode, wf, turn, code)
	}
	return direct(wf, turn, code)
}
