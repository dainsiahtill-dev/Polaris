import json
import logging
import os
import re
import subprocess
import sys
import time
from typing import Any, Union

from polaris.kernelone.fs.text_ops import read_file_safe
from polaris.kernelone.tool_execution.io_tools import ensure_codex_available

try:
    from polaris.kernelone.runtime.usage_metrics import TokenUsage, UsageContext, track_usage
except ImportError:
    from polaris.kernelone.runtime.usage_metrics import (  # type: ignore
        TokenUsage,
        UsageContext,
        track_usage,
    )

logger = logging.getLogger(__name__)

_CODEX_CAPS_CACHE: dict[str, set[str]] = {}


def _env_flag(name: str, default: str = "") -> bool:
    value = str(os.environ.get(name, default)).strip().lower()
    return value in ("1", "true", "yes", "on")


def _decode_with_fallback(data: bytes) -> str:
    if not data:
        return ""
    try:
        text = data.decode("utf-8")
        return text
    except UnicodeDecodeError:
        pass
    try:
        text = data.decode("utf-8", errors="replace")
    except (RuntimeError, ValueError):
        text = ""
    if text:
        bad = text.count("\ufffd")
        if bad / max(len(text), 1) < 0.02:
            return text
    for enc in ("utf-8-sig", "gbk", "cp936"):
        try:
            return data.decode(enc)
        except (RuntimeError, ValueError) as exc:
            logger.warning("kernelone.process.codex_adapter.decode failed for %s: %s", enc, exc, exc_info=True)
            continue
    return data.decode("utf-8", errors="replace")


def _read_codex_output(path: str) -> str:
    if not os.path.exists(path):
        return ""
    try:
        with open(path, "rb") as handle:
            data = handle.read()
        text = _decode_with_fallback(data)
        # Normalize output to UTF-8 for downstream consumers.
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text)
        except (RuntimeError, ValueError) as e:
            logger.debug(f"Failed to normalize UTF-8: {e}")
        return text
    except (RuntimeError, ValueError) as e:
        logger.debug(f"Failed to read file: {e}")
        return read_file_safe(path)


def _extract_codex_json_output(raw_output: str) -> str:
    lines = (raw_output or "").splitlines()
    reasoning_parts: list[str] = []
    message_parts: list[str] = []
    for line in lines:
        trimmed = line.strip()
        if not trimmed or not trimmed.startswith("{"):
            continue
        try:
            payload = json.loads(trimmed)
        except (RuntimeError, ValueError) as exc:
            logger.warning("kernelone.process.codex_adapter.parse_json failed: %s", exc, exc_info=True)
            continue
        if not isinstance(payload, dict):
            continue
        item = payload.get("item")
        if payload.get("type") == "item.completed" and isinstance(item, dict):
            item_type = str(item.get("type") or "")
            text = item.get("text")
            if not isinstance(text, str):
                continue
            if item_type in ("reasoning", "thought", "analysis"):
                reasoning_parts.append(text.strip())
            elif item_type in ("agent_message", "assistant_message", "message"):
                message_parts.append(text.strip())
    if not reasoning_parts and not message_parts:
        return raw_output
    output_chunks: list[str] = []
    if reasoning_parts:
        output_chunks.append("<thinking>" + "\n\n".join(reasoning_parts).strip() + "</thinking>")
    if message_parts:
        output_chunks.append("\n".join(message_parts).strip())
    return "\n".join(output_chunks).strip()


def build_codex_command(base_args: list[str], codex_path: str) -> list[str]:
    ext = os.path.splitext(codex_path)[1].lower()
    if ext == ".ps1":
        return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", codex_path, *base_args]
    if ext in (".cmd", ".bat"):
        return ["cmd.exe", "/c", codex_path, *base_args]
    return [codex_path, *base_args]


def _probe_codex_exec_capabilities(
    codex_path: str,
    *,
    workspace: str,
    env: dict[str, str],
    timeout: int,
) -> set[str]:
    cached = _CODEX_CAPS_CACHE.get(codex_path)
    if isinstance(cached, set) and cached:
        return set(cached)
    caps: set[str] = set()
    try:
        help_cmd = build_codex_command(["exec", "--help"], codex_path)
        proc = subprocess.run(
            help_cmd,
            cwd=workspace,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout if timeout > 0 else 30,
            check=False,
        )
        out = str(proc.stdout or "")
        for flag in (
            "--ask-for-approval",
            "--full-auto",
            "--dangerously-bypass-approvals-and-sandbox",
            "--json",
            "--output-schema",
            "--add-dir",
            "--profile",
        ):
            if flag in out:
                caps.add(flag)
    except (RuntimeError, ValueError) as e:
        logger.debug(f"Codex capabilities probe failed: {e}")
    if not caps:
        # conservative default for older/newer CLIs
        caps = {"--json", "--add-dir", "--profile", "--output-schema"}
    _CODEX_CAPS_CACHE[codex_path] = set(caps)
    return caps


def _drop_flag(args: list[str], flag: str, value_follows: bool = False) -> list[str]:
    cleaned: list[str] = []
    i = 0
    while i < len(args):
        if args[i] == flag:
            i += 2 if value_follows else 1
            continue
        cleaned.append(args[i])
        i += 1
    return cleaned


def _sanitize_args_by_caps(args: list[str], caps: set[str]) -> list[str]:
    sanitized = list(args)
    gated_flags = (
        ("--ask-for-approval", True),
        ("--full-auto", False),
        ("--dangerously-bypass-approvals-and-sandbox", False),
        ("--output-schema", True),
        ("--add-dir", True),
        ("--profile", True),
    )
    for flag, has_value in gated_flags:
        if flag not in caps:
            sanitized = _drop_flag(sanitized, flag, value_follows=has_value)
    if "--json" not in caps:
        sanitized = _drop_flag(sanitized, "--json", value_follows=False)
    return sanitized


def _strip_unexpected_argument(args: list[str], output: str) -> list[str]:
    match = re.search(r"unexpected argument '([^']+)'", str(output or ""), re.IGNORECASE)
    if not match:
        return args
    token = str(match.group(1) or "").strip()
    if not token:
        return args
    cleaned: list[str] = []
    i = 0
    while i < len(args):
        current = str(args[i] or "")
        if current == token:
            i += 1
            continue
        if current.startswith(token + "="):
            i += 1
            continue
        cleaned.append(args[i])
        i += 1
    return cleaned


def _detect_encoding_violations(output: str) -> bool:
    if not output:
        return False
    patterns = [
        r"(?i)Get-Content\\b(?![^\r\n]*-Encoding)",
        r"(?i)Set-Content\\b(?![^\r\n]*-Encoding)",
        r"(?i)Add-Content\\b(?![^\r\n]*-Encoding)",
        r"(?i)Out-File\\b(?![^\r\n]*-Encoding)",
    ]
    return any(re.search(p, output) for p in patterns)


def _encoding_guardrail() -> str:
    return (
        "Encoding guardrail (HARD RULE): You MUST use UTF-8 for any PowerShell read/write.\n"
        "- Reads: Get-Content -Encoding utf8 (or Get-Content -Raw -Encoding utf8).\n"
        "- Writes: Set-Content -Encoding utf8, Add-Content -Encoding utf8, Out-File -Encoding utf8.\n"
        "- Do NOT set global PowerShell defaults; just include -Encoding utf8 in each command.\n"
        "- If you already ran a PowerShell command without UTF-8, re-run it immediately with the UTF-8 flags.\n"
        "- Prefer repo tools (python -m tools.main repo_read_* ) over PowerShell reads when available.\n"
    )


def _retry_prompt_for_encoding(prompt: str) -> str:
    return _encoding_guardrail() + "\n" + prompt


def invoke_codex(
    prompt: str,
    output_file: str,
    workspace: str,
    show_output: bool,
    full_auto: bool,
    dangerous: bool,
    profile: str,
    timeout: int,
    extra_env: dict[str, str] | None = None,
    usage_ctx: Union["UsageContext", Any] | None = None,
    events_path: str = "",
) -> str:
    del output_file  # unused; kept for API compatibility
    codex_path = ensure_codex_available()
    codex_model = (
        str(
            os.environ.get("KERNELONE_CODEX_MODEL") or os.environ.get("POLARIS_CODEX_MODEL") or "gpt-5.2-codex"
        ).strip()
        or "gpt-5.2-codex"
    )
    # fmt: off
    codex_sandbox = str(os.environ.get("KERNELONE_CODEX_SANDBOX") or os.environ.get("POLARIS_CODEX_SANDBOX") or "").strip() or "safe"
    # fmt: on
    codex_color = (
        str(os.environ.get("KERNELONE_CODEX_COLOR") or os.environ.get("POLARIS_CODEX_COLOR") or "never").strip()
        or "never"
    )
    codex_cd = (
        str(os.environ.get("KERNELONE_CODEX_CD") or os.environ.get("POLARIS_CODEX_CD") or "").strip() or workspace
    )
    codex_approvals = str(
        os.environ.get("KERNELONE_CODEX_APPROVALS") or os.environ.get("POLARIS_CODEX_APPROVALS") or ""
    ).strip()
    codex_output_schema = str(
        os.environ.get("KERNELONE_CODEX_OUTPUT_SCHEMA") or os.environ.get("POLARIS_CODEX_OUTPUT_SCHEMA") or ""
    ).strip()
    codex_add_dirs = str(
        os.environ.get("KERNELONE_CODEX_ADD_DIRS") or os.environ.get("POLARIS_CODEX_ADD_DIRS") or ""
    ).strip()
    codex_config_overrides = str(
        os.environ.get("KERNELONE_CODEX_CONFIG") or os.environ.get("POLARIS_CODEX_CONFIG") or ""
    ).strip()
    codex_use_oss = _env_flag("KERNELONE_CODEX_OSS", os.environ.get("POLARIS_CODEX_OSS", "0"))
    # fmt: off
    codex_skip_git_check = _env_flag("KERNELONE_CODEX_SKIP_GIT_CHECK", os.environ.get("POLARIS_CODEX_SKIP_GIT_CHECK", "0"))
    # fmt: on

    run_cwd = os.path.abspath(codex_cd or workspace)
    if not os.path.isdir(run_cwd):
        run_cwd = os.path.abspath(workspace)

    # Prefer process cwd over `codex exec --cd` to avoid engine-specific CLI failures.
    args = ["exec", "--color", codex_color]
    if codex_skip_git_check:
        args.append("--skip-git-repo-check")
    args += ["--model", codex_model, "--sandbox", codex_sandbox, "--json"]
    if codex_approvals:
        args += ["--ask-for-approval", codex_approvals]
    if codex_use_oss:
        args.append("--oss")
    if codex_output_schema:
        args += ["--output-schema", codex_output_schema]
    if codex_add_dirs:
        for entry in re.split(r"[;,]", codex_add_dirs):
            entry = entry.strip()
            if entry:
                args += ["--add-dir", entry]
    if codex_config_overrides:
        for entry in re.split(r"[;,]", codex_config_overrides):
            entry = entry.strip()
            if entry:
                args += ["--config", entry]
    if dangerous:
        args.append("--dangerously-bypass-approvals-and-sandbox")
    elif full_auto:
        args.append("--full-auto")
    if profile:
        args.extend(["--profile", profile])

    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("LANG", "en_US.UTF-8")
    env.setdefault("LC_ALL", "en_US.UTF-8")
    env.setdefault("LC_CTYPE", "en_US.UTF-8")
    if extra_env:
        env.update(extra_env)
    caps = _probe_codex_exec_capabilities(
        codex_path,
        workspace=run_cwd,
        env=env,
        timeout=timeout,
    )
    args = _sanitize_args_by_caps(args, caps)

    capture_stdout = (
        True
        if "--json" in args
        else str(
            os.environ.get("KERNELONE_CODEX_CAPTURE_STDOUT") or os.environ.get("POLARIS_CODEX_CAPTURE_STDOUT", "0")
        )
        .strip()
        .lower()
        not in (
            "0",
            "false",
            "no",
            "off",
            "",
        )
    )

    def _run_once(run_prompt: str) -> str:
        base_cmd = build_codex_command(args, codex_path)
        # Pass prompt via stdin to avoid command-line length/quoting limits.
        # SECURITY: shell=False is mandatory in KernelOne (enforced by contract).
        # We pass args as a list, so subprocess uses shell=False automatically.
        run_cmd = [*base_cmd, "-"]
        process = subprocess.Popen(
            run_cmd,  # list of args — subprocess.run uses shell=False by default
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE if capture_stdout else None,
            stderr=subprocess.STDOUT if capture_stdout else None,
            # NOTE: text=True with encoding= is correct; do NOT add shell=True.
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=run_cwd,
            env=env,
        )
        try:
            stdout_text, _ = process.communicate(
                input=run_prompt,
                timeout=timeout if timeout > 0 else None,
            )
        except subprocess.TimeoutExpired:
            logger.warning("Codex process timed out after %ds; killing pid=%s", timeout, process.pid)
            process.kill()
            try:
                process.communicate(timeout=5)
            except (RuntimeError, ValueError) as exc:
                logger.debug("Timed out codex process cleanup failed: %s", exc)
            raise

        if not capture_stdout:
            return ""

        output = stdout_text or ""
        if output and "--json" in args:
            output = _extract_codex_json_output(output)
        if output and (show_output or not sys.stdout.isatty()):
            try:
                sys.stdout.write(output)
                sys.stdout.flush()
            except (RuntimeError, ValueError) as e:
                logger.debug("Stdout write failed: %s", e)
        return output

    try:
        use_guard = str(
            os.environ.get("KERNELONE_CODEX_UTF8_GUARD") or os.environ.get("POLARIS_CODEX_UTF8_GUARD", "1")
        ).strip().lower() not in (
            "0",
            "false",
            "no",
            "off",
            "",
        )
        run_prompt = (_encoding_guardrail() + "\n" + prompt) if use_guard else prompt

        start_time = time.time()
        output = _run_once(run_prompt)
        duration_ms = int((time.time() - start_time) * 1000)

        if "unexpected argument" in str(output or "").lower():
            adjusted_args = _strip_unexpected_argument(args, output)
            if adjusted_args != args:
                args[:] = adjusted_args
                output = _run_once(run_prompt)
                duration_ms = int((time.time() - start_time) * 1000)

        if capture_stdout and _detect_encoding_violations(output):
            output = _run_once(_retry_prompt_for_encoding(prompt))
            duration_ms = int((time.time() - start_time) * 1000)

        if usage_ctx and events_path:
            p_chars = len(run_prompt)
            c_chars = len(output)
            p_tokens = p_chars // 4
            c_tokens = c_chars // 4
            usage_obj = TokenUsage(
                prompt_tokens=p_tokens,
                completion_tokens=c_tokens,
                total_tokens=p_tokens + c_tokens,
                estimated=True,
                prompt_chars=p_chars,
                completion_chars=c_chars,
            )
            track_usage(events_path, usage_ctx, "codex-cli", "codex", usage_obj, duration_ms, ok=bool(output))

    except subprocess.TimeoutExpired:
        if usage_ctx and events_path:
            usage_obj = TokenUsage(
                prompt_tokens=len(prompt) // 4,
                completion_tokens=0,
                total_tokens=len(prompt) // 4,
                estimated=True,
                prompt_chars=len(prompt),
                completion_chars=0,
            )
            track_usage(events_path, usage_ctx, "codex-cli", "codex", usage_obj, 0, ok=False, error="Timeout")
        return ""

    return output
