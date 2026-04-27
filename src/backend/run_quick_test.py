"""Quick SUPER pipeline test after tool_choice fix."""
import subprocess, sys, os, re, pathlib

WORKSPACE = r"C:\Temp\polaris_super_test"
BACKEND = r"C:\Users\dains\Documents\GitLab\polaris\src\backend"

env = os.environ.copy()
env["PYTHONUTF8"] = "1"
msg = "请开发一个Python控制台猜数字小游戏：系统随机生成1-100的数字，玩家输入猜测，系统提示大了或小了，直到猜对为止。请先制定计划蓝图，然后开始落地执行。"

print("[TEST] Running SUPER pipeline with tool_choice=required fix...")
result = subprocess.run(
    [sys.executable, "-m", "polaris.delivery.cli", "console",
     "--backend", "plain", "--workspace", WORKSPACE,
     "--role", "director", "--super", "--batch"],
    input=msg, capture_output=True, text=True, encoding="utf-8",
    env=env, cwd=BACKEND, timeout=600,
)
print(f"[TEST] Exit: {result.returncode}")

with open(os.path.join(WORKSPACE, "stderr.txt"), "w", encoding="utf-8") as f:
    f.write(result.stderr)

stages = {
    "architect": r"stage_role: architect",
    "pm": r"SUPER_MODE_PM_HANDOFF",
    "ce": r"chief_engineer",
    "director": r"DIRECTOR_TASK_HANDOFF",
    "blueprint": r"BLUEPRINT_WRITTEN",
    "write_file": r"write_file.*bytes_written|write_file.*success.*true",
}
print("\n=== PIPELINE ===")
for name, pat in stages.items():
    found = bool(re.search(pat, result.stderr))
    print(f"  [{('OK' if found else 'MISS'):4s}] {name}")

print("\n=== FILES ===")
for p in pathlib.Path(WORKSPACE).rglob("*"):
    if p.is_file() and ".polaris" not in str(p) and "__pycache__" not in str(p):
        if p.suffix in (".txt", ".jsonl", ".log"):
            continue
        print(f"  {p.relative_to(WORKSPACE)} ({p.stat().st_size} bytes)")
