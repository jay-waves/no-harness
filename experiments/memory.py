import os, json, subprocess, threading, time, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
env=dict(os.environ, CODEX_HOME="/tmp/cli-home", OPENAI_API_KEY="benchmark-placeholder")
Path(env["CODEX_HOME"]).mkdir(exist_ok=True)
seen=threading.Event()
class Model(BaseHTTPRequestHandler):
 def do_GET(self):
  self.send_response(404); self.end_headers()
 def do_POST(self):
  self.rfile.read(int(self.headers.get('Content-Length','0')))
  seen.set()
  time.sleep(20)
def sample(pid):
 f=Path(f'/proc/{pid}/smaps_rollup').read_text()
 return int(next(x.split()[1] for x in f.splitlines() if x.startswith('Pss:')))
def codex_breakdown(pid):
 fields = {}
 for line in Path(f'/proc/{pid}/smaps_rollup').read_text().splitlines():
  key, _, value = line.partition(':')
  if key in ('Pss', 'Pss_Anon', 'Pss_File'):
   fields[key] = int(value.split()[0])
 return {
  'file_kib': fields['Pss_File'],
  'anonymous_kib': fields['Pss_Anon'],
  'other_kib': fields['Pss'] - fields['Pss_File'] - fields['Pss_Anon'],
  'executable_mapping_kib': mapping_pss(pid, str(Path(f'/proc/{pid}/exe').readlink())),
 }
def libc(pid):
 return mapping_pss(pid, 'ld-musl-')
def mapping_pss(pid, name):
 total=0
 matched=False
 for line in Path(f'/proc/{pid}/smaps').read_text().splitlines():
  if line and line[0] in '0123456789abcdef' and '-' in line.split()[0]:
   matched = name in line
  elif matched and line.startswith('Pss:'):
   total += int(line.split()[1])
 return total
def tree(pid):
 yield pid
 try:
  ids=Path(f'/proc/{pid}/task/{pid}/children').read_text().split()
 except FileNotFoundError: return
 for p in ids: yield from tree(int(p))
def rows(pid):
 out=[]
 for p in tree(pid):
  try: out.append([Path(f'/proc/{p}/comm').read_text().strip(),sample(p),libc(p)])
  except FileNotFoundError: pass
 return out
if sys.argv[1]=='codex':
 server=HTTPServer(('127.0.0.1',0),Model)
 threading.Thread(target=server.serve_forever,daemon=True).start()
 url=f'http://127.0.0.1:{server.server_port}/v1'
 with open('/tmp/codex-bench.log','w+') as log:
  cli=subprocess.Popen(['/opt/codex/bin/codex','exec','--skip-git-repo-check','-C','/tmp','-m','gpt-5.4','-c','model_provider="benchmark"','-c','model_providers.benchmark.name="benchmark"','-c',f'model_providers.benchmark.base_url="{url}"','-c','model_providers.benchmark.env_key="OPENAI_API_KEY"','Return OK.'],env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log)
  try:
   if not seen.wait(12):
    log.seek(0); raise RuntimeError(log.read()[-3000:])
   py=subprocess.Popen(['python3','-c','import csv,time; values=list(range(1,100001)); time.sleep(30)'])
   try:
    time.sleep(.25)
    print(json.dumps({'mode':'codex','components':rows(cli.pid)+rows(py.pid),'codex_breakdown':codex_breakdown(cli.pid)}),flush=True)
   finally: py.terminate(); py.wait()
  finally: cli.terminate(); cli.wait()
else:
 p=subprocess.Popen(['ssh-mcp','bridge'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
 def rpc(**kwargs):
  p.stdin.write(json.dumps({'version':'0.2.0',**kwargs})+'\n');p.stdin.flush()
  result=json.loads(p.stdout.readline())
  if result.get('error'): raise RuntimeError(result)
  return result
 try:
  rpc(method='hello',protocol=1)
  sid=rpc(method='open',open={'host':'fixture','runtime':'python'})['session']['id']
  eid=rpc(method='execute',execute={'session_id':sid,'code':'import csv,os\nvalues=list(range(1,100001))\nprint(os.getpid(),os.getppid())'})['execution']['id']
  out=''; cur=''
  while True:
   r=rpc(method='read',read={'session_id':sid,'execution_id':eid,'cursor':cur,'wait_ms':1000})['output']
   out+=r['stdout'];cur=r['cursor']
   if r['state']=='completed' and r['output_closed']: break
  python_pid, worker=map(int,out.split())
  time.sleep(.25)
  print(json.dumps({'mode':'worker','components':[[label,sample(pid),libc(pid)] for label,pid in [("bridge",p.pid),("worker",worker),("python3",python_pid)]]}),flush=True)
  rpc(method='close',session={'session_id':sid})
 finally: p.stdin.close();p.wait()
