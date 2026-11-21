import json
from pathlib import Path
import subprocess
import tarfile

work = Path(__file__).resolve().parent / ".work"
bundle = work / "codex-package"
codex = sum(
    path.stat().st_size
    for path in bundle.rglob("*")
    if path.is_file() and not path.is_symlink()
)
worker = (work / "ssh-mcp").stat().st_size
results = {}
for profile in ("python", "base"):
    container = subprocess.check_output(
        ["docker", "create", f"no-harness-footprint:{profile}"], text=True
    ).strip()
    try:
        with subprocess.Popen(["docker", "export", container], stdout=subprocess.PIPE) as export:
            with tarfile.open(fileobj=export.stdout, mode="r|*") as archive:
                base = sum(item.size for item in archive if item.isfile())
            if export.wait():
                raise RuntimeError(f"Could not export {profile} rootfs")
    finally:
        subprocess.run(["docker", "rm", container], check=True, stdout=subprocess.DEVNULL)
    results[profile] = {
        "base_bytes": base,
        "codex_target_bytes": base + codex,
        "worker_target_bytes": base + worker,
    }
print(json.dumps({
    "codex_bundle_bytes": codex,
    "codex_cli_bytes": (bundle / "bin/codex").stat().st_size,
    "worker_bytes": worker,
    "profiles": results,
}, indent=2))
