# 日志审计任务 #89: 安全与权限分析报告

**审计日期**: 2026-04-13
**审计范围**: `polaris/kernelone/` 和 `polaris/cells/roles/kernel/internal/tool_gateway.py`
**审计依据**: TOP 6 生死级修复记忆、HMAC 审计记忆

---

## 1. Workspace 边界验证完整性

### 1.1 验证机制概览

| 组件 | 文件位置 | 验证方式 |
|------|---------|---------|
| `RoleToolGateway` | `polaris/cells/roles/kernel/internal/tool_gateway.py` | `_is_path_traversal()` - URL解码 + 模式匹配 + 路径解析 |
| `CommandExecutionService` | `polaris/kernelone/process/command_executor.py` | `_validate_workspace_boundary()` - 强制执行 |
| `is_path_safe_for_workspace` | `polaris/kernelone/llm/toolkit/tool_normalization/__init__.py` | URL解码 + dangerous pattern检测 + relative_to()验证 |
| `_is_path_safe` | `polaris/kernelone/llm/toolkit/protocol/path_utils.py` | Path.resolve() + relative_to() |
| `normalize_path` | `polaris/kernelone/shared/path_utils.py` | 路径规范化 + `..` 检测 |
| `CommandWhitelistValidator` | `polaris/kernelone/tool_execution/constants.py` | 白名单 + 黑名单模式 |

### 1.2 路径穿越检测覆盖

**Tool Gateway (`tool_gateway.py:642-700`)**:
- URL 双解码检测 (防止 `%2e%2e%2f` 等编码穿越)
- 模式列表: `../`, `..\`, `%2e%2e%2f`, `%252e%252e%252f`, `..;`, `%00` null byte
- 绝对路径验证: 解析后必须在 workspace 内
- 相对路径验证: 解析后必须在 workspace 内

**Command Executor (`command_executor.py:614-636`)**:
- `_validate_workspace_boundary()` 强制执行，不管 allowlist 状态
- 相对路径仅在 workspace 内允许
- 使用 `os.path.commonpath()` 验证边界

**Tool Normalization (`tool_normalization/__init__.py:125-166`)**:
- `is_path_safe_for_workspace()` - 双重 URL 解码
- dangerous patterns: `../`, `..\`, `%2e%2e%2f`, `%252e%252e%252f`, 等
- 使用 `Path.relative_to()` 验证

### 1.3 评估结果

| 检查项 | 状态 | 说明 |
|--------|------|------|
| 绝对路径边界验证 | PASS | 解析后验证必须在 workspace 内 |
| 相对路径边界验证 | PASS | 解析后验证必须在 workspace 内 |
| URL 编码穿越检测 | PASS | 双解码 + 多层模式匹配 |
| Null byte 注入防护 | PASS | `%00` 模式已检测 |
| 命令执行边界验证 | PASS | `_validate_workspace_boundary()` 强制执行 |

---

## 2. Path Traversal 防护

### 2.1 多层防御体系

```
Layer 1: 模式匹配 (URL decode + dangerous patterns)
Layer 2: 路径解析 (Path.resolve() + relative_to())
Layer 3: 边界验证 (os.path.commonpath())
```

### 2.2 关键实现

**`tool_gateway.py:642-700` (`_is_path_traversal`)**:
```python
# 1. URL 解码检测
decoded = urllib.parse.unquote(path)
decoded_again = urllib.parse.unquote(decoded)

# 2. 基础穿越模式
dangerous_patterns = ["../", "..\\", "%2e%2e%2f", ...]

# 3. 绝对路径验证
is_absolute = bool(re.match(r"^[a-zA-Z]:[/\\]", normalized_path) or ...)
if is_absolute:
    candidate = Path(normalized_path).resolve()
    if workspace_root not in candidate.parents:
        return True

# 4. 相对路径验证
resolved = (base / normalized_path).resolve()
if base not in resolved.parents:
    return True
```

**弱点**: 模式匹配使用 `in` 操作符，如果 path 为 `....//` 可能绕过（但有解析后验证兜底）。

### 2.3 评估结果

| 检查项 | 状态 | 说明 |
|--------|------|------|
| URL 编码检测 | PASS | 双解码 + 递归解码防护 |
| 模式匹配 | PASS | 覆盖常见穿越模式 |
| 路径解析验证 | PASS | 使用 relative_to() 确保安全 |
| Windows 路径支持 | PASS | 支持 `C:\` 和 UNC 路径 |

---

## 3. Tool Whitelist/Blacklist 执行链路

### 3.1 角色工具策略架构

**`RoleToolGateway` (`tool_gateway.py:39-726`)**:
- `policy_id` - 策略标识
- `check_tool_permission()` - 7 步权限检查
- `filter_tools()` - 过滤可用工具列表
- `execute_tool()` - 执行带权限检查的工具

### 3.2 权限检查链路

```
1. check_tool_permission(tool_name, tool_args)
   ├── 黑名单检查 (policy.blacklist)
   ├── 白名单检查 (policy.whitelist + 通配符)
   ├── 代码写入权限 (allow_code_write)
   ├── scope 约束验证
   ├── 命令执行权限 (allow_command_execution)
   ├── 危险命令检测 (_is_dangerous_command)
   ├── 文件删除权限 (allow_file_delete)
   ├── 调用次数限制 (max_tool_calls_per_turn)
   └── 路径穿越检查 (_is_path_traversal)
```

### 3.3 工具分类映射

**`TOOL_CATEGORIES` (`tool_gateway.py:54-82`)**:
- `code_write`: write_file, create_file, modify_file, search_replace, edit_file, append_to_file
- `command_execution`: execute_command, run_shell, exec_cmd, shell_execute, system_call
- `file_delete`: delete_file, remove_file, rm_file, cleanup_file, delete_directory
- `read_only`: read_file, search_code, grep, ripgrep, glob, list_directory, file_exists, 等

### 3.4 白名单强制策略 (CLAUDE.md 6.6)

**关键约束**:
- 工具白名单检查在别名归一化**之前**执行
- `tool_normalization/__init__.py` 中 `TOOL_NAME_ALIASES` 只允许同一工具的命令别名
- **禁止**: 跨工具语义映射 (如 `repo_read_head` -> `read_file`)
- **允许**: `run_command` -> `execute_command` (同一工具的不同调用风格)

### 3.5 评估结果

| 检查项 | 状态 | 说明 |
|--------|------|------|
| 黑名单检查 | PASS | 检查在白名单之前 |
| 白名单检查 | PASS | 支持通配符匹配 |
| 代码写入权限 | PASS | 单独 `allow_code_write` 标志 |
| 命令执行权限 | PASS | 单独 `allow_command_execution` 标志 |
| 危险命令检测 | PASS | 委托 `is_dangerous_command()` |
| 调用次数限制 | PASS | `max_tool_calls_per_turn` |
| 路径穿越检查 | PASS | 7 步检查的最后一步 |
| 别名映射安全 | PASS | 只允许同工具别名 |

---

## 4. Audit HMAC Chain 完整性

### 4.1 HMAC 实现架构

**`KernelAuditRuntime` (`polaris/kernelone/audit/runtime.py`)**:

| 组件 | 功能 |
|------|------|
| `_hmac_key` | 32字节随机密钥，从 env 或文件加载 |
| `_compute_signature()` | HMAC-SHA256 签名 |
| `_hash_event()` | SHA-256 事件哈希 |
| `verify_chain()` | 链完整性验证 |

### 4.2 HMAC 签名机制

**签名链** (`runtime.py:365-376`):
```python
def _compute_signature(self, event: KernelAuditEvent) -> str:
    link = f"{event.prev_hash}{event.event_id}{event.timestamp.isoformat()}{event.event_type.value}"
    return hmac.new(
        self._hmac_key,
        link.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
```

**事件哈希** (`runtime.py:355-363`):
```python
@staticmethod
def _hash_event(event: KernelAuditEvent) -> str:
    payload = json.dumps(event.to_dict(), sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
```

### 4.3 密钥管理

**密钥来源优先级** (`runtime.py:165-184`):
1. 环境变量 `audit_hmac_key`
2. 文件 `runtime_root/.polaris_audit_key`
3. 生成新密钥并持久化

**权限设置**:
```python
if sys.platform != "win32":
    os.chmod(key_file, stat.S_IRUSR | stat.S_IWUSR)  # 0o600
```

### 4.4 链验证机制

**`verify_chain()` (`runtime.py:532-534`)**:
```python
def verify_chain(self) -> KernelChainVerificationResult:
    return self._store.verify_chain()
```

**健康检查** (`runtime.py:752-845`):
- `chain_valid`: 是否通过哈希链验证
- `gap_count`: 链中断计数
- `total_events`: 事件总数

### 4.5 评估结果

| 检查项 | 状态 | 说明 |
|--------|------|------|
| HMAC-SHA256 实现 | PASS | 使用标准 hmac + sha256 |
| 链链接完整性 | PASS | prev_hash + event_id + timestamp + event_type |
| 密钥管理 | PASS | 32字节随机密钥，文件权限 0o600 |
| 密钥持久化 | PASS | 支持 env 和文件两种方式 |
| 链验证接口 | PASS | `verify_chain()` 方法 |
| 健康检查 | PASS | `health_check()` 包含链验证 |
| 防篡改 | PASS | 签名基于前一个哈希 |

---

## 5. 命令注入风险

### 5.1 命令执行安全措施

**`CommandExecutionService` (`polaris/kernelone/process/command_executor.py`)**:

| 安全措施 | 实现 |
|---------|------|
| 禁止 shell 操作符 | `;`, `&&`, `||`, `|`, `` ` ``, `$(`, `<`, `>` |
| 危险环境变量过滤 | LD_*, PYTHON*, RUST_*, NODE_*, BASH_* 等 |
| 白名单命令验证 | `CommandWhitelistValidator` |
| 工作目录边界 | `_resolve_cwd()` 验证 |
| 可执行文件边界 | `_validate_executable()` 验证 |
| npx 包白名单 | `_SAFE_NPX_PACKAGES` |

### 5.2 危险模式检测

**`BLOCKED_COMMAND_PATTERNS` (`constants.py:25-42`)**:
```python
r"\brm\s+-rf\b",
r"\brm\s+-r\b",
r"\bdel\s+/s\b",
r"\brmdir\s+/s\b",
r"\bformat\s+[a-z]:",
r"\bmkfs\b",
r"\bdd\s+if=.*of=.*",
r"\b:(){\s*:\|:\s*&\s*};:",  # Fork bomb
r"\bcurl\s+.*\|.*sh\b",
r"\bwget\s+.*\|.*sh\b",
r"\bchmod\s+777\b",
r"\bsudo\s+rm\b",
r">/dev/sd[a-z]",
```

**`is_dangerous_command` (`security/dangerous_patterns.py:60-71`)**:
```python
def is_dangerous_command(text: str) -> bool:
    if not text:
        return False
    return bool(_get_pattern().search(text))
```

### 5.3 环境变量过滤

**危险变量类别** (`command_executor.py:33-99`):
- 动态链接器: LD_*
- Python: PYTHON*
- Rust: RUST_*
- Node.js: NODE_*
- Shell: BASH_*, PS1-PS4, ENV, ZDOTDIR

### 5.4 评估结果

| 检查项 | 状态 | 说明 |
|--------|------|------|
| Shell 操作符过滤 | PASS | 禁止 `;`, `&&`, `||`, `|`, `` ` ``, `$()` |
| 危险命令检测 | PASS | 正则模式匹配 |
| 环境变量过滤 | PASS | 精确匹配 + 前缀匹配 |
| 白名单验证 | PASS | `CommandWhitelistValidator` |
| npx 包白名单 | PASS | 仅允许已知安全包 |
| 工作目录边界 | PASS | 解析后验证必须在 workspace 内 |
| Python 模块验证 | PASS | 仅允许 workspace 内模块 |
| 控制字符过滤 | PASS | `_UNSAFE_TOKEN_RE` |

---

## 6. XSS/注入风险 (Prompt 相关)

### 6.1 Prompt 注入风险分析

**风险区域**:
- `polaris/kernelone/prompts/catalog.py` - Prompt 模板
- `polaris/kernelone/context/compaction.py` - 上下文压缩重注入
- `polaris/kernelone/llm/reasoning/` - 推理相关 prompt

### 6.2 现有安全措施

**SensitiveFieldRedactor** (`audit/omniscient/redaction.py`):
```python
DEFAULT_SENSITIVE_PATTERNS = [
    "password", "passwd", "pwd", "token", "secret", "api_key",
    "apikey", "api-key", "authorization", "auth", "credential", ...
]
```

**Token 检测**:
- Bearer token 检测
- 十六进制长字符串 (32+ 字符)
- JWT 模式
- Base64 编码字符串

### 6.3 潜在风险点

| 风险点 | 描述 | 现有缓解 |
|--------|------|---------|
| Prompt 模板注入 | 用户输入直接插入 prompt | 无明确转义 |
| 上下文重注入 | `_build_reinjection_prompt()` | 无输入验证 |
| Anthropomorphic Context | `_get_context_bundle()` | 需进一步分析 |
| LLM 输出嵌入 | tool result 嵌入 prompt | 无消毒处理 |

### 6.4 评估结果

| 检查项 | 状态 | 说明 |
|--------|------|------|
| SQL 注入防护 | PASS | `sanitize_sql()` 实现 |
| 命令注入防护 | PASS | `sanitize_command()` 实现 |
| 文件名注入防护 | PASS | `sanitize_filename()` 实现 |
| Prompt 模板注入 | WARNING | 无明确转义机制 |
| 上下文重注入 | WARNING | `_build_reinjection_prompt()` 无输入验证 |
| 敏感信息过滤 | PASS | `SensitiveFieldRedactor` 实现 |
| 输出截断 | PASS | `max_output_chars` 限制 |

---

## 7. 综合评估

### 7.1 强项

1. **HMAC 链完整性**: 完整的 HMAC-SHA256 签名链，密钥管理规范
2. **Workspace 边界验证**: 多层防御 (模式匹配 + 路径解析 + 边界验证)
3. **工具白名单策略**: 角色基的权限控制，7 步检查链路
4. **命令执行安全**: 完整的危险模式检测、环境变量过滤、白名单验证
5. **路径穿越防护**: URL 双解码、多层模式匹配、Path.resolve() 验证

### 7.2 弱项与建议

| 弱项 | 风险等级 | 建议 |
|------|---------|------|
| Prompt 模板无转义 | MEDIUM | 添加 prompt 注入检测 |
| `_build_reinjection_prompt()` 无验证 | MEDIUM | 添加输入验证 |
| PATH 白名单未实现 | LOW | `_filter_safe_path_entries()` TODO |
| 模式匹配绕过风险 | LOW | 使用更严格的正则 |

### 7.3 安全态势总结

| 类别 | 评分 | 说明 |
|------|------|------|
| Workspace 边界 | 9/10 | 多层防御，覆盖全面 |
| 路径穿越防护 | 9/10 | URL 解码 + 模式 + 解析 |
| 工具权限控制 | 9/10 | 完整的白名单/黑名单链路 |
| HMAC 链完整性 | 9/10 | 规范实现，密钥管理完善 |
| 命令注入防护 | 9/10 | 模式检测 + 白名单 + 环境过滤 |
| Prompt 安全 | 6/10 | 敏感信息过滤好，但模板注入风险 |

---

## 8. 参考文件清单

| 文件 | 路径 |
|------|------|
| RoleToolGateway | `polaris/cells/roles/kernel/internal/tool_gateway.py` |
| CommandExecutionService | `polaris/kernelone/process/command_executor.py` |
| KernelAuditRuntime | `polaris/kernelone/audit/runtime.py` |
| AuditGateway | `polaris/kernelone/audit/gateway.py` |
| CommandWhitelistValidator | `polaris/kernelone/tool_execution/constants.py` |
| is_dangerous_command | `polaris/kernelone/security/dangerous_patterns.py` |
| InputSanitizer | `polaris/kernelone/security/sanitizer.py` |
| SensitiveFieldRedactor | `polaris/kernelone/audit/omniscient/redaction.py` |
| tool_normalization | `polaris/kernelone/llm/toolkit/tool_normalization/__init__.py` |
| path_utils | `polaris/kernelone/shared/path_utils.py` |
| CommandSecurity | `polaris/kernelone/tool_execution/security.py` |

---

## 9. 审计结论

本次审计覆盖了 `polaris/kernelone/` 和 `tool_gateway.py` 下的安全和权限控制机制。

**总体评价**: 系统安全机制设计良好，特别是在:
- Workspace 边界验证
- 工具白名单/黑名单执行
- HMAC 审计链
- 命令注入防护

**需要关注**:
- Prompt 模板注入防护较弱
- 上下文重注入机制缺少输入验证

建议后续对 Prompt 相关代码进行专项安全审计，添加 prompt 注入检测机制。
