//go:build linux

package mcp

// The job stream and status pipe stay separate from user code's standard
// streams, which are redirected per execution, including for subprocesses.
const pythonBootstrap = `
import json
import os
import platform
import sys
import traceback

jobs = os.fdopen(os.dup(0), "rb")
status = os.fdopen(3, "w", encoding="utf-8", buffering=1)
os.set_inheritable(3, False)
original_fds = [os.dup(fd) for fd in range(3)]
streams = sys.stdin, sys.stdout, sys.stderr
namespace = {"__name__": "__main__"}
status.write(platform.python_version() + "\n")

for line in jobs:
    job = json.loads(line)
    exit_code = 0
    try:
        with open(job["stdin"], "rb") as stdin, \
             open(job["stdout"], "wb", buffering=0) as stdout, \
             open(job["stderr"], "wb", buffering=0) as stderr:
            for fd, stream in enumerate((stdin, stdout, stderr)):
                os.dup2(stream.fileno(), fd)
            # Use a fresh input buffer for each job.
            with open(0, "r", encoding="utf-8", closefd=False) as user_input:
                sys.stdin = user_input
                status.write(job["id"] + " ready\n")
                try:
                    with open(job["code"], "rb") as source:
                        code = compile(source.read(), "<one>", "exec")
                    exec(code, namespace)
                except BaseException:
                    traceback.print_exc(file=streams[2])
                    exit_code = 1
                finally:
                    streams[1].flush()
                    streams[2].flush()
    finally:
        sys.stdin, sys.stdout, sys.stderr = streams
        for fd, original in enumerate(original_fds):
            os.dup2(original, fd)
    status.write(f"{job['id']} {exit_code}\n")
`
