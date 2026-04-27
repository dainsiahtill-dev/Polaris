"""Run SUPER mode full pipeline test."""
import subprocess, sys, os, re, time

WORKSPACE = r"C:\Temp\polaris_super_test"
BACKEND = r"C:\Users\dains\Documents\GitLab\polaris\src\backend"
INPUT_MSG = (
    "请开发一个Python控制台猜数字小游戏：系统随机生成1-100的数字，"
    "玩家输入猜测，系统提示大了或小了，直到猜对为止。"
    "请先制定计划蓝图，然后开始落地执行。"
)
LOG_FILE = os.path.join(WORKSPACE, "super_pipeline.log")

env = os.environ.copy()
env["PYTHONUTF8"] = "1"
env["PYTHONIOENCODING"] = "utf-8"

print(f"[TEST] Starting SUPER pipeline at {time.strftime('%H:%M:%S')}...")

result = subprocess.run(
    [sys.executable, "-m", "polaris.delivery.cli", "console",
     "--backend", "plain",
     "--workspace", WORKSPACE,
     "--role", "director",
     "--super",
     "--batch"],
    input=INPUT_MSG,
    capture_output=True,
    text=True,
    encoding="utf-8",
    env=env,
    cwd=BACKEND,
    timeout=300,
)

# Save full log
with open(LOG_FILE, "w", encoding="utf-8") as f:
    f.write("=== STDOUT ===\n")
    f.write(result.stdout)
    f.write("\n=== STDERR ===\n")
    f.write(result.stderr)
    f.write(f"\n=== EXIT CODE: {result.returncode} ===\n")

print(f"[TEST] Done at {time.strftime('%H:%M:%S')}, exit={result.returncode}")
print(f"[TEST] Log: {LOG_FILE}")

stderr = result.stderr

# Check pipeline stages
checks = {
    "architect_role": r"stream_turn started.*role=architect",
    "architect_readonly": r"SUPER_MODE_READONLY_STAGE",
    "blueprint_written": r"SUPER_MODE_BLUEPRINT_WRITTEN",
    "pm_role": r"stream_turn started.*role=pm",
    "pm_handoff": r"SUPER_MODE_PM_HANDOFF",
    "task_extract": r"SUPER_MODE_TASK_EXTRACT",
    "task_persist": r"SUPER_MODE_PERSIST",
    "ce_role": r"stream_turn started.*role=chief_engineer",
    "ce_handoff": r"SUPER_MODE_CE_HANDOFF",
    "ce_claim": r"pending_design",
    "director_role": r"stream_turn started.*role=director",
    "director_handoff": r"SUPER_MODE_DIRECTOR_TASK_HANDOFF",
    "director_continue": r"SUPER_MODE_DIRECTOR_CONTINUE",
    "task_ack_exec": r"next_stage.*pending_exec",
    "task_ack_qa": r"next_stage.*pending_qa",
}

print("\n=== PIPELINE VERIFICATION ===")
for name, pattern in checks.items():
    found = bool(re.search(pattern, stderr))
    mark = "OK" if found else "MISS"
    print(f"  [{mark}] {name}")

# Role call sequence
role_calls = re.findall(r"stream_turn started:.*?role=(\w+)", stderr)
print(f"\nRole sequence: {' -> '.join(role_calls)}")

# Count Director turns
director_turns = len(re.findall(r"stream_turn started.*role=director", stderr))
print(f"Director turns: {director_turns}")

# Output files
print("\n=== OUTPUT FILES ===")
for root, dirs, files in os.walk(WORKSPACE):
    dirs[:] = [d for d in dirs if not d.startswith((".", "__pycache__"))]
    for f in files:
        if f.endswith(".log"):
            continue
        path = os.path.join(root, f)
        relpath = os.path.relpath(path, WORKSPACE)
        size = os.path.getsize(path)
        print(f"  {relpath} ({size} bytes)")

# Verify game
print("\n=== GAME VERIFICATION ===")
game_dir = os.path.join(WORKSPACE, "game")
if os.path.isdir(game_dir):
    py_files = [f for f in os.listdir(game_dir) if f.endswith(".py")]
    print(f"  Game files: {py_files}")
    try:
        sys.path.insert(0, WORKSPACE)
        from game.guess_number import show_welcome, play_one_round, main
        print("  Import OK: game.guess_number")
    except Exception as e:
        print(f"  Import FAIL: {e}")
else:
    print("  No game/ directory")

# Just import and verify function signatures
try:
    import importlib
    spec = importlib.util.spec_from_file_location("guess", os.path.join(WORKSPACE, "game", "guess_number.py"))
    if spec and spec.loader:
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        funcs = [name for name in dir(mod) if not name.startswith("_")]
        print(f"  Functions: {funcs}")
        print("  Code executes without errors")
except Exception as e:
    print(f"  Execution error: {e}")
