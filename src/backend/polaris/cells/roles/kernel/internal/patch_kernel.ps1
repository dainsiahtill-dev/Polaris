$kernelPath = "C:\Users\dains\Documents\GitLab\polaris\src\backend\polaris\cells\roles\kernel\internal\kernel.py"
$content = Get-Content -Path $kernelPath -Raw -Encoding UTF8

# 1. Add TYPE_CHECKING to typing import
if ($content -notmatch 'from typing import TYPE_CHECKING') {
    $content = $content -replace 'from typing import Any', 'from typing import TYPE_CHECKING, Any'
}

# 2. Add TYPE_CHECKING block for ToolGatewayPort
if ($content -notmatch '# ToolGatewayPort Protocol for DI') {
    $pattern = 'from polaris\.cells\.roles\.kernel\.internal\.tool_gateway import RoleToolGateway, ToolAuthorizationError'
    $replacement = @'
# ToolGatewayPort Protocol for DI (import lazily to avoid circular deps)
if TYPE_CHECKING:
    from polaris.cells.roles.kernel.public.contracts import ToolGatewayPort

from polaris.cells.roles.kernel.internal.tool_gateway import RoleToolGateway, ToolAuthorizationError
from polaris.cells.roles.kernel.internal._tool_gateway_di import _DelegatingToolGateway
'@
    $content = $content -replace [regex]::Escape('from polaris.cells.roles.kernel.internal.tool_gateway import RoleToolGateway, ToolAuthorizationError'), $replacement
}

# 3. Modify __init__ to accept tool_gateway parameter
$oldInit = @'
    def __init__(
        self,
        workspace: str = "",
        registry: RoleProfileRegistry | None = None,
        use_structured_output: bool | None = None,
    ):
        """初始化执行内核

        Args:
            workspace: 工作区路径
            registry: 角色注册表（默认使用全局实例）
            use_structured_output: 是否启用结构化输出（默认从环境变量读取）
        """
        self.workspace = workspace
        self.registry = registry or RoleProfileRegistry()

        # 结构化输出配置
'@

$newInit = @'
    def __init__(
        self,
        workspace: str = "",
        registry: RoleProfileRegistry | None = None,
        use_structured_output: bool | None = None,
        tool_gateway: "ToolGatewayPort" | None = None,
    ):
        """初始化执行内核

        Args:
            workspace: 工作区路径
            registry: 角色注册表（默认使用全局实例）
            use_structured_output: 是否启用结构化输出（默认从环境变量读取）
            tool_gateway: 工具网关实现（支持 ToolGatewayPort Protocol），
                         若为 None 则每次请求创建默认 RoleToolGateway
        """
        self.workspace = workspace
        self.registry = registry or RoleProfileRegistry()
        self._tool_gateway = tool_gateway  # M1: DI注入点

        # 结构化输出配置
'@

if ($content -match [regex]::Escape($oldInit)) {
    $content = $content -replace [regex]::Escape($oldInit), $newInit
    Write-Host "Patched __init__ successfully"
}
else {
    Write-Host "Warning: Could not find __init__ pattern"
}

# 4. Modify _create_gateway to support DI
$oldCreateGateway = @'
    def _create_gateway(
        self,
        profile: RoleProfile,
        request: RoleTurnRequest,
    ) -> RoleToolGateway:
        """Create one per-request tool gateway with session-aware execution context."""
        session_id = str((request.metadata or {}).get("session_id") or "").strip() or None
        memory_provider = RoleSessionContextMemoryService() if session_id else None
        return RoleToolGateway(
            profile,
            self.workspace,
            session_id=session_id,
            session_memory_provider=memory_provider,
        )
'@

$newCreateGateway = @'
    def _create_gateway(
        self,
        profile: RoleProfile,
        request: RoleTurnRequest,
    ) -> RoleToolGateway:
        """Create one per-request tool gateway with session-aware execution context.

        M1: 如果 kernel 在构造时注入了 tool_gateway，则使用注入的网关；
            否则每次请求创建默认 RoleToolGateway 实例。
        """
        # M1: 检查是否注入了外部 tool_gateway
        if self._tool_gateway is not None:
            if isinstance(self._tool_gateway, RoleToolGateway):
                return self._tool_gateway
            # 对于其他 ToolGatewayPort 实现，包装为委托网关
            return _DelegatingToolGateway(self._tool_gateway)

        # 默认行为：每次请求创建新实例
        session_id = str((request.metadata or {}).get("session_id") or "").strip() or None
        memory_provider = RoleSessionContextMemoryService() if session_id else None
        return RoleToolGateway(
            profile,
            self.workspace,
            session_id=session_id,
            session_memory_provider=memory_provider,
        )
'@

if ($content -match [regex]::Escape($oldCreateGateway)) {
    $content = $content -replace [regex]::Escape($oldCreateGateway), $newCreateGateway
    Write-Host "Patched _create_gateway successfully"
}
else {
    Write-Host "Warning: Could not find _create_gateway pattern"
}

# Write back
$content | Set-Content -Path $kernelPath -Encoding UTF8 -NoNewline
Write-Host "kernel.py patched successfully!"
