import json
import pathlib
import subprocess
import sys
import uuid

root = pathlib.Path(__file__).resolve().parent / ".work"
profile = sys.argv[1]
base = f"no-harness-footprint:{profile}"
version = "0.2.0"
task_prefix = "from pathlib import Path\nimport subprocess\np=Path('/tmp/demo')\np.mkdir(exist_ok=True)\n"
tasks = [
    ("files", "f=p/'notes.txt'\nf.write_text('alpha\\nbeta\\n')\nassert f.read_text().splitlines()==['alpha','beta']"),
    ("compile", "s=p/'answer.c'\ns.write_text('#include <stdio.h>\\nint main(void) { puts(\"42\"); }\\n')\nb=p/'answer'\nsubprocess.run(['cc',str(s),'-o',str(b)],check=True)\nassert subprocess.check_output([b],text=True)=='42\\n'"),
    ("analysis", "import csv\nf=p/'input.csv'\nf.write_text('value\\n3\\n7\\n11\\n')\nwith f.open() as fd: values=[int(r['value']) for r in csv.DictReader(fd)]\nassert (len(values),sum(values))==(3,21)"),
    ("service-config", "key=p/'demo_host_key'\nsubprocess.run(['ssh-keygen','-t','ed25519','-N','','-f',str(key)],check=True,capture_output=True)\nf=p/'sshd_config'\nf.write_text(f'Port 2222\\nHostKey {key}\\nPasswordAuthentication no\\n')\nr=subprocess.check_output(['/usr/sbin/sshd','-T','-f',str(f)],text=True)\nassert 'port 2222' in r.splitlines() and 'passwordauthentication no' in r.splitlines()"),

]
def docker(*args, **kw):
    return subprocess.run(["docker",*args],check=True,**kw)
def part(client, **msg):
    client.stdin.write(json.dumps(dict(version=version, **msg))+"\n")
    client.stdin.flush()
    result = json.loads(client.stdout.readline())
    if result.get("error"):
        raise RuntimeError(result)
    return result
if profile == "python":
    tasks = [(name, code) for name, code in tasks if name != "compile"]

for name, code in tasks:
    docker("run","--rm","--network","none","-v",f"{root}/codex-package:/opt/codex:ro,Z",base,"python3","-c",task_prefix+code)
    print("direct",name,"PASS",flush=True)
name = f"no-harness-footprint-{uuid.uuid4().hex[:12]}"
docker("run","--rm","-d","--name",name,"--network","none","-v",f"{root/'one'}:/usr/local/bin/one:ro,Z",base,"sleep","300",stdout=subprocess.DEVNULL)
try:
    with subprocess.Popen(["docker","exec","-i",name,"one","bridge"],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True) as worker:
        info = part(worker, method="hello", protocol=1)
        print("worker handshake",info,flush=True)
        session = part(worker, method="open", open={"host":"container","runtime":"python"})["session"]["id"]
        for title, code in tasks:
            job = part(worker,method="execute",execute={"session_id":session,"code":task_prefix+code})["execution"]["id"]
            cursor = ""
            for i in range(30):
                out = part(worker,method="read",read={"session_id":session,"execution_id":job,"cursor":cursor,"wait_ms":1000})["output"]
                cursor = out["cursor"]
                if out.get("stderr") or out.get("error"):
                    raise RuntimeError(out)
                if out["state"]=="completed" and out["output_closed"]:
                    assert out["exit_code"]==0, out
                    break
            else:
                raise RuntimeError("worker task timeout")
            print("worker",title,"PASS",flush=True)
        part(worker,method="close",session={"session_id":session})
        worker.stdin.close()
finally:
    docker("stop",name,stdout=subprocess.DEVNULL)
