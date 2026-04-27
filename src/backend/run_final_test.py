"""Final SUPER pipeline test - 20min timeout."""
import subprocess, sys, os, re, pathlib, time

WORKSPACE = r"C:\Temp\polaris_super_test"
BACKEND = r"C:\Users\dains\Documents\GitLab\polaris\src\backend"

os.makedirs(WORKSPACE, exist_ok=True)
env = os.environ.copy()
env["PYTHONUTF8"] = "1"
msg = "请创建hello.py文件，打印Hello World。请先制定计划蓝图，然后开始落地执行。"

print(f"START: {time.strftime('%H:%M:%S')}")
proc = subprocess.Popen(
    [sys.executable, "-m", "polaris.delivery.cli", "console",
     "--backend", "plain", "--workspace", WORKSPACE,
     "--role", "director", "--super", "--batch"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    env=env, cwd=BACKEND,
)
try:
    stdout, stderr = proc.communicate(input=msg.encode("utf-8"), timeout=1200)
    print(f"DONE: {time.strftime('%H:%M:%S')} exit={proc.returncode}")
except subprocess.TimeoutExpired:
    proc.kill()
    stdout, stderr = proc.communicate()
    print(f"TIMEOUT: {time.strftime('%H:%M:%S')}")

stderr_text = stderr.decode("utf-8", errors="replace")
with open(os.path.join(WORKSPACE, "stderr.txt"), "w", encoding="utf-8") as f:
    f.write(stderr_text)

checks = {
    "architect": r"profile=architect",
    "pm": r"profile=pm",
    "ce": r"profile=chief_engineer",
    "director_task": r"DIRECTOR_TASK_HANDOFF",
    "blueprint": r"BLUEPRINT_WRITTEN",
    "write": r"write_file.*success|bytes_written",
}
print("\n=== PIPELINE ===")
for name, pat in checks.items():
    found = bool(re.search(pat, stderr_text))
    print(f"  [{'OK' if found else 'MISS'}] {name}")

print("\n=== FILES ===")
for p in pathlib.Path(WORKSPACE).rglob("*.py"):
    if "__pycache__" not in str(p):
        print(f"  {p.relative_to(WORKSPACE)} ({p.stat().st_size}b)")
