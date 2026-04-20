#!/bin/bash
# rollback_contextos_v2.sh - Context OS v2 Rollback Script
# Blueprint v2.1 Specification
#
# 用法: ./rollback_contextos_v2.sh [context|semantic|cognitive|all] [--dry-run]
# 执行时间: <= 30s
#
# 回滚范围:
#   context   - 回滚上下文状态和缓存
#   semantic  - 回滚语义索引
#   cognitive - 回滚认知/推理状态
#   all       - 回滚所有上述组件
#
# 选项:
#   --dry-run   只输出将要执行的操作，不实际修改

set -euo pipefail

# ============================================================
# Configuration
# ============================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(dirname "$SCRIPT_DIR")"
WORKSPACE="${WORKSPACE:-.}"

GATEWAY_PATH="${BACKEND_DIR}/polaris/cells/roles/kernel/internal/context_gateway.py"
BACKUP_DIR="${WORKSPACE}/meta/backups/context_gateway"
SNAPSHOT_DIR="${BACKUP_DIR}/snapshots"
CACHE_DIR="${WORKSPACE}/.cache/context"

# Rollback scope definitions
SCOPE_CONTEXT_FILES=(
    "${GATEWAY_PATH}"
    "${WORKSPACE}/polaris/kernelone/context/context_os/models.py"
    "${WORKSPACE}/polaris/kernelone/context/history_materialization.py"
)

SCOPE_SEMANTIC_FILES=(
    "${WORKSPACE}/polaris/kernelone/context/semantic/index_store.py"
    "${WORKSPACE}/polaris/kernelone/context/semantic/descriptor_cache.json"
    "${WORKSPACE}/.semantic_index/"
)

SCOPE_COGNITIVE_FILES=(
    "${WORKSPACE}/polaris/kernelone/cognitive/execution/rollback_manager.py"
    "${WORKSPACE}/polaris/kernelone/cognitive/state/working_state.json"
    "${WORKSPACE}/polaris/kernelone/cognitive/state/session_state.json"
)

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# ============================================================
# Notification Configuration
# ============================================================
NOTIFICATION_WEBHOOK_URL="${NOTIFICATION_WEBHOOK_URL:-}"
NOTIFICATION_SLACK="${NOTIFICATION_SLACK:-false}"
NOTIFICATION_PAGERDUTY="${NOTIFICATION_PAGERDUTY:-false}"

# Grayscale Configuration
GRAYSCALE_STATE_FILE="${WORKSPACE}/meta/grayscale/state.json"
GRAYSCALE_METRICS_FILE="${WORKSPACE}/meta/grayscale/metrics.json"

# Rollback Thresholds (P0 - immediate rollback)
THRESHOLD_E2E_SUCCESS_RATE=0.95
THRESHOLD_CONTEXT_CONSISTENCY=0.99
THRESHOLD_FALLBACK_SUCCESS_RATE=0.95
THRESHOLD_BOUNDARY_VIOLATION_RATE=0.0
THRESHOLD_UNAUTHORIZED_CALLS=0

# ============================================================
# Functions
# ============================================================

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

show_usage() {
    cat <<EOF
用法: $(basename "$0") [context|semantic|cognitive|all] [--dry-run] [--check] [--auto-confirm]

回滚 Context OS v2 组件到上一个稳定状态。

参数:
  context    - 回滚上下文状态和缓存
  semantic   - 回滚语义索引
  cognitive  - 回滚认知/推理状态
  all        - 回滚所有组件 (默认)

选项:
  --dry-run  只输出将要执行的操作，不实际修改
  --check    只检查灰度状态，不执行回滚
  --auto-confirm  自动确认回滚（用于自动触发场景）

示例:
  $(basename "$0") context           # 回滚上下文
  $(basename "$0") all --dry-run      # 预览所有回滚操作
  $(basename "$0") semantic           # 回滚语义索引
  $(basename "$0") all --check        # 检查灰度状态
  $(basename "$0") all --auto-confirm # 自动回滚
EOF
}

# ============================================================
# Notification Functions
# ============================================================

send_notification() {
    local level="$1"
    local message="$2"

    if [[ "$NOTIFICATION_SLACK" == "true" && -n "$NOTIFICATION_WEBHOOK_URL" ]]; then
        local payload="{\"level\":\"$level\",\"message\":\"$message\",\"timestamp\":\"$(date -Iseconds)\"}"
        curl -s -X POST -H 'Content-Type: application/json' -d "$payload" "$NOTIFICATION_WEBHOOK_URL" 2>/dev/null || true
    fi

    if [[ "$NOTIFICATION_PAGERDUTY" == "true" ]]; then
        echo "[PAGERDUTY] $level: $message"
    fi

    case "$level" in
        CRITICAL)
            log_error "NOTIFICATION: $message"
            ;;
        WARNING)
            log_warning "NOTIFICATION: $message"
            ;;
        *)
            log_info "NOTIFICATION: $message"
            ;;
    esac
}

# ============================================================
# Grayscale Status Functions
# ============================================================

get_metric() {
    local metric_name="$1"
    local default_value="${2:-}"

    if [[ -f "$GRAYSCALE_METRICS_FILE" ]]; then
        grep "\"${metric_name}\"" "$GRAYSCALE_METRICS_FILE" 2>/dev/null | sed 's/.*: *\([0-9.]*\).*/\1/' || echo "$default_value"
    else
        echo "$default_value"
    fi
}

check_grayscale_state() {
    log_info "检查灰度状态..."

    if [[ ! -f "$GRAYSCALE_STATE_FILE" ]]; then
        log_warning "灰度状态文件不存在: $GRAYSCALE_STATE_FILE"
        echo "STABLE"
        return 0
    fi

    local state=$(grep '"state"' "$GRAYSCALE_STATE_FILE" 2>/dev/null | sed 's/.*: *"\([^"]*\)".*/\1/' || echo "UNKNOWN")
    local phase=$(grep '"phase"' "$GRAYSCALE_STATE_FILE" 2>/dev/null | sed 's/.*: *"\([^"]*\)".*/\1/' || echo "UNKNOWN")
    local traffic=$(grep '"traffic_ratio"' "$GRAYSCALE_STATE_FILE" 2>/dev/null | sed 's/.*: *\([0-9.]*\).*/\1/' || echo "0")

    echo "灰度状态: $state"
    echo "当前阶段: $phase"
    echo "流量比例: $traffic%"

    echo "$state"
}

# ============================================================
# Rollback Trigger Condition Checks
# ============================================================

# Portable floating point comparison (avoids bc dependency)
cmp_float() {
    local left="$1"
    local op="$2"
    local right="$3"

    local result=$(awk -v l="$left" -v r="$right" 'BEGIN { printf "%.6f", (l '"$op"' r) }')
    [[ "$result" == "1.000000" ]]
}

check_rollback_conditions() {
    log_info "检查回滚触发条件..."

    local should_rollback=false
    local trigger_reason=""

    # 检查 Agent E2E 成功率
    local e2e_rate=$(get_metric "agent_e2e_success_rate" "1.0")
    if cmp_float "$e2e_rate" "<" "$THRESHOLD_E2E_SUCCESS_RATE"; then
        should_rollback=true
        trigger_reason="Agent E2E < ${THRESHOLD_E2E_SUCCESS_RATE}%: ${e2e_rate}"
        send_notification "CRITICAL" "Agent E2E 成功率触发回滚: ${e2e_rate}"
    fi

    # 检查 Context 投影一致性
    local consistency=$(get_metric "context_projection_consistency" "1.0")
    if cmp_float "$consistency" "<" "$THRESHOLD_CONTEXT_CONSISTENCY"; then
        should_rollback=true
        trigger_reason="${trigger_reason}; Context 一致性 < ${THRESHOLD_CONTEXT_CONSISTENCY}%: ${consistency}"
        send_notification "CRITICAL" "Context 投影一致性触发回滚: ${consistency}"
    fi

    # 检查 Fallback 成功率
    local fallback_rate=$(get_metric "fallback_success_rate" "1.0")
    if cmp_float "$fallback_rate" "<" "$THRESHOLD_FALLBACK_SUCCESS_RATE"; then
        should_rollback=true
        trigger_reason="${trigger_reason}; Fallback 成功率 < ${THRESHOLD_FALLBACK_SUCCESS_RATE}%: ${fallback_rate}"
        send_notification "WARNING" "Fallback 成功率触发回滚: ${fallback_rate}"
    fi

    # 检查越界率
    local violation_rate=$(get_metric "semantic_boundary_violation_rate" "0.0")
    if cmp_float "$violation_rate" ">" "$THRESHOLD_BOUNDARY_VIOLATION_RATE"; then
        should_rollback=true
        trigger_reason="${trigger_reason}; 语义越界率 > ${THRESHOLD_BOUNDARY_VIOLATION_RATE}: ${violation_rate}"
        send_notification "CRITICAL" "语义越界触发回滚: ${violation_rate}"
    fi

    # 检查越权工具调用
    local unauthorized=$(get_metric "unauthorized_tool_calls" "0")
    if [[ "$unauthorized" -gt "$THRESHOLD_UNAUTHORIZED_CALLS" ]]; then
        should_rollback=true
        trigger_reason="${trigger_reason}; 越权调用 > ${THRESHOLD_UNAUTHORIZED_CALLS}: ${unauthorized}"
        send_notification "CRITICAL" "越权工具调用触发回滚: ${unauthorized}"
    fi

    if [[ "$should_rollback" == "true" ]]; then
        echo "[TRIGGER] 回滚触发: ${trigger_reason}"
        return 1
    fi

    log_success "所有指标正常，无需回滚"
    return 0
}

# ============================================================
# Update Grayscale State
# ============================================================

update_grayscale_state() {
    local new_state="$1"

    if [[ "$DRY_RUN" == "true" ]]; then
        echo "  [DRY-RUN] 更新灰度状态: $new_state"
        return
    fi

    mkdir -p "$(dirname "$GRAYSCALE_STATE_FILE")"

    local timestamp=$(date -Iseconds)
    cat > "$GRAYSCALE_STATE_FILE" <<EOF
{
    "state": "$new_state",
    "updated_at": "$timestamp",
    "last_rollback": "$timestamp"
}
EOF
    log_info "灰度状态已更新: $new_state"
}

# ============================================================
# Parse Arguments
# ============================================================
TARGET="${1:-all}"
DRY_RUN=false
CHECK_ONLY=false
AUTO_CONFIRM=false

if [[ "$#" -ge 1 ]]; then
    case "$1" in
        context|semantic|cognitive|all)
            TARGET="$1"
            ;;
        --dry-run|-n)
            TARGET="all"
            DRY_RUN=true
            ;;
        --check|-c)
            TARGET="all"
            CHECK_ONLY=true
            ;;
        --auto-confirm|-y)
            TARGET="all"
            AUTO_CONFIRM=true
            ;;
        -h|--help)
            show_usage
            exit 0
            ;;
        *)
            log_error "未知参数: $1"
            show_usage
            exit 1
            ;;
    esac
fi

shift || true
while [[ "$#" -ge 1 ]]; do
    case "$1" in
        --dry-run|-n)
            DRY_RUN=true
            ;;
        --check|-c)
            CHECK_ONLY=true
            ;;
        --auto-confirm|-y)
            AUTO_CONFIRM=true
            ;;
        *)
            log_error "未知参数: $1"
            show_usage
            exit 1
            ;;
    esac
    shift
done

# ============================================================
# Timing Budget (30s limit)
# ============================================================
START_TIME=$(date +%s)
TIMEOUT_SECONDS=30

check_timeout() {
    local elapsed=$(($(date +%s) - START_TIME))
    if [[ $elapsed -gt $TIMEOUT_SECONDS ]]; then
        log_error "操作超时 (${elapsed}s > ${TIMEOUT_SECONDS}s)"
        return 1
    fi
    return 0
}

# ============================================================
# Backup Directory Management
# ============================================================
init_backup_dirs() {
    log_info "初始化备份目录..."
    if [[ "$DRY_RUN" == true ]]; then
        echo "  [DRY-RUN] mkdir -p ${SNAPSHOT_DIR}"
        echo "  [DRY-RUN] mkdir -p ${CACHE_DIR}"
        return
    fi

    mkdir -p "${SNAPSHOT_DIR}" 2>/dev/null || true
    mkdir -p "${CACHE_DIR}" 2>/dev/null || true
    log_success "备份目录已就绪: ${SNAPSHOT_DIR}"
}

# ============================================================
# Snapshot Creation
# ============================================================
create_snapshot() {
    local scope="$1"
    local timestamp=$(date +%Y%m%d_%H%M%S)
    local snapshot_name="${scope}_${timestamp}"
    local snapshot_path="${SNAPSHOT_DIR}/${snapshot_name}"

    log_info "创建 ${scope} 快照: ${snapshot_name}"

    if [[ "$DRY_RUN" == true ]]; then
        echo "  [DRY-RUN] 创建快照: ${snapshot_path}"
        return
    fi

    mkdir -p "${snapshot_path}"

    case "$scope" in
        context)
            for file in "${SCOPE_CONTEXT_FILES[@]}"; do
                if [[ -f "$file" ]]; then
                    cp "$file" "${snapshot_path}/" 2>/dev/null || true
                    echo "  快照: $file"
                fi
            done
            ;;
        semantic)
            for file in "${SCOPE_SEMANTIC_FILES[@]}"; do
                if [[ -f "$file" ]]; then
                    cp "$file" "${snapshot_path}/" 2>/dev/null || true
                    echo "  快照: $file"
                elif [[ -d "$file" ]]; then
                    cp -r "$file" "${snapshot_path}/" 2>/dev/null || true
                    echo "  快照目录: $file"
                fi
            done
            ;;
        cognitive)
            for file in "${SCOPE_COGNITIVE_FILES[@]}"; do
                if [[ -f "$file" ]]; then
                    cp "$file" "${snapshot_path}/" 2>/dev/null || true
                    echo "  快照: $file"
                elif [[ -d "$file" ]]; then
                    cp -r "$file" "${snapshot_path}/" 2>/dev/null || true
                    echo "  快照目录: $file"
                fi
            done
            ;;
        all)
            create_snapshot "context"
            create_snapshot "semantic"
            create_snapshot "cognitive"
            ;;
    esac

    # 保存快照元数据
    cat > "${snapshot_path}/snapshot_meta.json" <<EOF
{
    "scope": "${scope}",
    "timestamp": "${timestamp}",
    "created_at": "$(date -Iseconds)",
    "backup_path": "${snapshot_path}"
}
EOF

    log_success "快照已创建: ${snapshot_name}"
}

# ============================================================
# Restore from Snapshot
# ============================================================
restore_from_snapshot() {
    local scope="$1"

    # 查找最新的对应 scope 快照
    local latest_snapshot=$(ls -t "${SNAPSHOT_DIR}/${scope}_"* 2>/dev/null | head -1)

    if [[ -z "$latest_snapshot" || ! -d "$latest_snapshot" ]]; then
        log_warning "未找到 ${scope} 的快照，跳过恢复"
        return 0
    fi

    log_info "从快照恢复 ${scope}: $(basename "$latest_snapshot")"

    if [[ "$DRY_RUN" == true ]]; then
        echo "  [DRY-RUN] 恢复目录: ${latest_snapshot}"
        return
    fi

    # 复制快照内容回原位置
    for item in "${latest_snapshot}"/*; do
        local basename=$(basename "$item")
        if [[ "$basename" == "snapshot_meta.json" ]]; then
            continue
        fi

        if [[ -d "$item" ]]; then
            # 处理目录
            local target_dir=""
            case "$basename" in
                .semantic_index)
                    target_dir="${WORKSPACE}/.semantic_index"
                    ;;
                *)
                    target_dir="${WORKSPACE}/${basename}"
                    ;;
            esac
            if [[ -n "$target_dir" ]]; then
                rm -rf "$target_dir" 2>/dev/null || true
                cp -r "$item" "$target_dir/"
                echo "  恢复目录: $target_dir"
            fi
        else
            # 处理文件
            local target_file=""
            case "$basename" in
                context_gateway.py)
                    target_file="${GATEWAY_PATH}"
                    ;;
                models.py)
                    target_file="${WORKSPACE}/polaris/kernelone/context/context_os/models.py"
                    ;;
                history_materialization.py)
                    target_file="${WORKSPACE}/polaris/kernelone/context/history_materialization.py"
                    ;;
                index_store.py)
                    target_file="${WORKSPACE}/polaris/kernelone/context/semantic/index_store.py"
                    ;;
                descriptor_cache.json)
                    target_file="${WORKSPACE}/polaris/kernelone/context/semantic/descriptor_cache.json"
                    ;;
                rollback_manager.py)
                    target_file="${WORKSPACE}/polaris/kernelone/cognitive/execution/rollback_manager.py"
                    ;;
                working_state.json)
                    target_file="${WORKSPACE}/polaris/kernelone/cognitive/state/working_state.json"
                    ;;
                session_state.json)
                    target_file="${WORKSPACE}/polaris/kernelone/cognitive/state/session_state.json"
                    ;;
                *)
                    target_file="${WORKSPACE}/${basename}"
                    ;;
            esac

            if [[ -n "$target_file" ]]; then
                cp "$item" "$target_file"
                echo "  恢复文件: $target_file"
            fi
        fi
    done

    log_success "${scope} 已从快照恢复"
}

# ============================================================
# Clean Context Cache
# ============================================================
clean_context_cache() {
    log_info "清理 Context 缓存..."

    if [[ "$DRY_RUN" == true ]]; then
        echo "  [DRY-RUN] 清理缓存目录: ${CACHE_DIR}"
        echo "  [DRY-RUN] 清理索引: ${WORKSPACE}/.semantic_index/"
        echo "  [DRY-RUN] 清理 Python 缓存: ${WORKSPACE}/**/__pycache__/"
        return
    fi

    # 清理 context cache
    if [[ -d "${CACHE_DIR}" ]]; then
        rm -rf "${CACHE_DIR:?}"/*
        echo "  已清理: ${CACHE_DIR}"
    fi

    # 清理 semantic index
    if [[ -d "${WORKSPACE}/.semantic_index/" ]]; then
        rm -rf "${WORKSPACE}/.semantic_index/"*.lock 2>/dev/null || true
        echo "  已清理索引锁"
    fi

    # 清理 Python 缓存
    find "${WORKSPACE}" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    find "${WORKSPACE}" -type f -name "*.pyc" -delete 2>/dev/null || true

    log_success "缓存已清理"
}

# ============================================================
# Verify Rollback
# ============================================================
verify_rollback() {
    local scope="$1"

    log_info "验证 ${scope} 回滚..."

    if [[ "$DRY_RUN" == true ]]; then
        echo "  [DRY-RUN] 验证文件完整性"
        return
    fi

    # 基本验证：检查关键文件是否存在
    case "$scope" in
        context)
            if [[ -f "${GATEWAY_PATH}" ]]; then
                log_success "context_gateway.py 验证通过"
            else
                log_error "context_gateway.py 验证失败"
                return 1
            fi
            ;;
        semantic)
            if [[ -f "${WORKSPACE}/polaris/kernelone/context/semantic/index_store.py" ]] || \
               [[ ! -d "${WORKSPACE}/.semantic_index/" ]]; then
                log_success "semantic 验证通过"
            else
                log_warning "semantic 验证警告"
            fi
            ;;
        cognitive)
            if [[ -f "${WORKSPACE}/polaris/kernelone/cognitive/execution/rollback_manager.py" ]]; then
                log_success "rollback_manager.py 验证通过"
            else
                log_warning "rollback_manager.py 验证警告"
            fi
            ;;
    esac

    check_timeout || return 1
    return 0
}

# ============================================================
# List Snapshots
# ============================================================
list_snapshots() {
    log_info "可用快照:"

    if [[ ! -d "${SNAPSHOT_DIR}" ]]; then
        echo "  (无快照)"
        return
    fi

    local count=0
    for snapshot in "${SNAPSHOT_DIR}"/*/; do
        if [[ -d "$snapshot" ]]; then
            local name=$(basename "$snapshot")
            local meta="${snapshot}/snapshot_meta.json"
            local scope="unknown"
            local timestamp="unknown"

            if [[ -f "$meta" ]]; then
                scope=$(grep -o '"scope": *"[^"]*"' "$meta" 2>/dev/null | cut -d'"' -f4 || echo "unknown")
                timestamp=$(grep -o '"timestamp": *"[^"]*"' "$meta" 2>/dev/null | cut -d'"' -f4 || echo "unknown")
            fi

            echo "  - ${name} (scope: ${scope}, time: ${timestamp})"
            count=$((count + 1))
        fi
    done

    if [[ $count -eq 0 ]]; then
        echo "  (无快照)"
    fi
}

# ============================================================
# Main Rollback Flow
# ============================================================
main() {
    echo "=============================================="
    echo "  Context OS v2 Rollback Script (v2.1)"
    echo "=============================================="
    echo ""
    echo "配置:"
    echo "  目标范围: ${TARGET}"
    echo "  模式: $(if [[ "$DRY_RUN" == true ]]; then echo "DRY-RUN (只预览)"; elif [[ "$CHECK_ONLY" == true ]]; then echo "CHECK-ONLY (状态检查)"; elif [[ "$AUTO_CONFIRM" == true ]]; then echo "AUTO-CONFIRM (自动确认)"; else echo "执行"; fi)"
    echo "  备份目录: ${SNAPSHOT_DIR}"
    echo "  超时限制: ${TIMEOUT_SECONDS}s"
    echo ""

    # Check-only mode: just check grayscale status
    if [[ "$CHECK_ONLY" == true ]]; then
        check_grayscale_state
        check_rollback_conditions
        exit $?
    fi

    if [[ "$DRY_RUN" == true ]]; then
        log_warning "DRY-RUN 模式: 只显示将要执行的操作"
        echo ""
    fi

    # Check rollback conditions before proceeding
    if [[ "$AUTO_CONFIRM" != true ]]; then
        echo ""
        echo "--- Pre-flight: 灰度状态检查 ---"
        check_grayscale_state
        echo ""
        check_rollback_conditions || true
        echo ""

        read -p "确认执行回滚? [y/N] " -r confirm
        if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
            log_info "回滚已取消"
            exit 0
        fi
    else
        echo ""
        echo "--- Pre-flight: 灰度状态检查 ---"
        check_grayscale_state
        echo ""
        if ! check_rollback_conditions; then
            log_warning "触发自动回滚"
        fi
    fi

    # 检查关键路径
    if [[ ! -d "$(dirname "$GATEWAY_PATH")" ]]; then
        log_error "关键路径不存在: $(dirname "$GATEWAY_PATH")"
        send_notification "CRITICAL" "回滚失败: 关键路径不存在"
        exit 1
    fi

    # 初始化
    init_backup_dirs || { log_error "初始化失败"; exit 1; }
    check_timeout || exit 1

    echo ""
    echo "--- Phase 1: 创建回滚前快照 ---"
    create_snapshot "$TARGET" || { log_error "快照创建失败"; exit 1; }
    check_timeout || exit 1

    echo ""
    echo "--- Phase 2: 清理 Context 缓存 ---"
    clean_context_cache
    check_timeout || exit 1

    echo ""
    echo "--- Phase 3: 执行回滚 ---"
    restore_from_snapshot "$TARGET" || {
        log_error "回滚失败"
        send_notification "CRITICAL" "回滚执行失败"
        exit 1
    }
    check_timeout || exit 1

    echo ""
    echo "--- Phase 4: 验证 ---"
    verify_rollback "$TARGET" || log_warning "验证有警告"

    echo ""
    echo "--- Phase 5: 更新灰度状态 ---"
    update_grayscale_state "ROLLED_BACK"

    echo ""
    echo "--- Phase 6: 可用快照 ---"
    list_snapshots

    local elapsed=$(($(date +%s) - START_TIME))
    echo ""
    echo "=============================================="
    if [[ "$DRY_RUN" == true ]]; then
        log_success "DRY-RUN 完成 (${elapsed}s)"
    else
        log_success "回滚完成 (${elapsed}s)"
        send_notification "WARNING" "Context OS v2 回滚完成，耗时 ${elapsed}s"
    fi
    echo "=============================================="

    if [[ $elapsed -gt $TIMEOUT_SECONDS ]]; then
        log_warning "警告: 执行时间超过 ${TIMEOUT_SECONDS}s 目标"
    fi
}

# ============================================================
# Execute
# ============================================================
main
