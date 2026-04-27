"""Complex Todo CLI test with fixed anti-exploration prompts."""
import subprocess, sys, os, re, pathlib, time

W = r"C:\Temp\polaris_super_test4"
B = r"C:\Users\dains\Documents\GitLab\polaris\src\backend"
os.makedirs(W, exist_ok=True)
env = os.environ.copy()
env["PYTHONUTF8"] = "1"
env["KERNELONE_ENABLE_SESSION_ORCHESTRATOR"] = "1"
msg = "用Python实现一个命令行Todo事项管理器，支持增删改查、分类标签、数据持久化到JSON。采用多模块架构。请先制定计划蓝图，然后开始编码落地执行。"

print(f"START {time.strftime('%H:%M:%S')}")
proc = subprocess.Popen(
    [sys.executable, "-m", "polaris.delivery.cli", "console",
     "--backend", "plain", "--workspace", W, "--role", "director",
     "--super", "--batch", "--debug"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    env=env, cwd=B)
try:
    o, e = proc.communicate(input=msg.encode("utf-8"), timeout=900)
    print(f"DONE {time.strftime('%H:%M:%S')} exit={proc.returncode}")
except subprocess.TimeoutExpired:
    proc.kill()
    o, e = proc.communicate()
    print(f"TIMEOUT {time.strftime('%H:%M:%S')}")

et = e.decode("utf-8", errors="replace")
with open(os.path.join(W, "stderr.txt"), "w", encoding="utf-8") as f:
    f.write(et)

roles = re.findall(r"profile=(\w+)", et)
unique_roles = list(dict.fromkeys(roles))
print(f"Roles: {' -> '.join(unique_roles)}")

checks = {
    "pm_empty": r"pm_plan:.*PM planning stage produced no output",
    "tasks_extract": r"SUPER_MODE_TASK_EXTRACT",
    "director_handoff": r"DIRECTOR_TASK_HANDOFF",
    "director_write": r"write_file.*success|bytes_written",
    "pipeline_complete": r"SUPER_MODE_PIPELINE_COMPLETE",
}
print("\n=== PIPELINE ===")
for name, pat in checks.items():
    found = bool(re.search(pat, et))
    if name == "pm_empty":
        print(f"  [{'FAIL' if found else 'OK':4s}] {name}")
    else:
        print(f"  [{'OK' if found else 'MISS':4s}] {name}")

print("\n=== FILES ===")
for p in pathlib.Path(W).rglob("*"):
    if p.is_file() and ".polaris" not in str(p) and "__pycache__" not in str(p):
        if p.suffix in (".txt", ".jsonl"):
            continue
        print(f"  {p.relative_to(W)} ({p.stat().st_size}b)")
