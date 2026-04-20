# Environment Variable Migration Guide

## Overview

This document describes the migration from `POLARIS_*` to `KERNELONE_*` environment variable prefixes.

## Migration Status

| Component | Status | Notes |
|-----------|--------|-------|
| `polaris/bootstrap/config_loader.py` | Migrated | KERNELONE_* primary, POLARIS_* fallback |
| `polaris/bootstrap/backend_bootstrap.py` | Migrated | Sets both prefixes for compatibility |
| `polaris/kernelone/_runtime_config.py` | Already Supported | KERNELONE_* primary, POLARIS_* fallback |

## Environment Variable Mapping

### Bootstrap Configuration (config_loader.py)

| Config Key | New Variable (Primary) | Old Variable (Fallback) |
|------------|------------------------|-------------------------|
| `server.host` | `KERNELONE_HOST` | `POLARIS_HOST` |
| `server.port` | `KERNELONE_BACKEND_PORT` | `POLARIS_BACKEND_PORT` |
| `logging.level` | `KERNELONE_LOG_LEVEL` | `POLARIS_LOG_LEVEL` |
| `logging.enable_debug_tracing` | `KERNELONE_DEBUG_TRACING` | `POLARIS_DEBUG_TRACING` |
| `pm.backend` | `KERNELONE_PM_BACKEND` | `POLARIS_PM_BACKEND` |
| `pm.model` | `KERNELONE_PM_MODEL` | `POLARIS_PM_MODEL` |
| `pm.show_output` | `KERNELONE_PM_SHOW_OUTPUT` | `POLARIS_PM_SHOW_OUTPUT` |
| `pm.runs_director` | `KERNELONE_PM_RUNS_DIRECTOR` | `POLARIS_PM_RUNS_DIRECTOR` |
| `pm.director_timeout` | `KERNELONE_PM_DIRECTOR_TIMEOUT` | `POLARIS_PM_DIRECTOR_TIMEOUT` |
| `pm.director_iterations` | `KERNELONE_PM_DIRECTOR_ITERATIONS` | `POLARIS_PM_DIRECTOR_ITERATIONS` |
| `director.model` | `KERNELONE_DIRECTOR_MODEL` | `POLARIS_DIRECTOR_MODEL` |
| `director.iterations` | `KERNELONE_DIRECTOR_ITERATIONS` | `POLARIS_DIRECTOR_ITERATIONS` |
| `llm.model` | `KERNELONE_MODEL` | `POLARIS_MODEL` |
| `llm.provider` | `KERNELONE_LLM_PROVIDER` | `POLARIS_LLM_PROVIDER` |
| `llm.base_url` | `KERNELONE_LLM_BASE_URL` | `POLARIS_LLM_BASE_URL` |
| `llm.api_key` | `KERNELONE_LLM_API_KEY` | `POLARIS_LLM_API_KEY` |
| `workspace` | `KERNELONE_WORKSPACE` | `POLARIS_WORKSPACE` |
| `self_upgrade_mode` | `KERNELONE_SELF_UPGRADE_MODE` | `POLARIS_SELF_UPGRADE_MODE` |
| `server.cors_origins` | `KERNELONE_CORS_ORIGINS` | `POLARIS_CORS_ORIGINS` |
| `runtime.ramdisk_root` | `KERNELONE_RAMDISK_ROOT` | `POLARIS_RAMDISK_ROOT` |

### Runtime Configuration (_runtime_config.py)

See `polaris/kernelone/_runtime_config.py` for the complete list of supported environment variables.

## Priority Order

1. **KERNELONE_*** (new primary prefix) - Highest priority
2. **POLARIS_*** (legacy fallback prefix) - Used if primary not set
3. **Default values** - Used if neither prefix is set

## Usage Examples

### New Style (Recommended)

```bash
export KERNELONE_WORKSPACE=/path/to/workspace
export KERNELONE_BACKEND_PORT=49977
export KERNELONE_MODEL=modelscope.cn/unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF:latest
```

### Legacy Style (Still Supported)

```bash
export POLARIS_WORKSPACE=/path/to/workspace
export POLARIS_BACKEND_PORT=49977
export POLARIS_MODEL=modelscope.cn/unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF:latest
```

### Mixed Style (KERNELONE_ takes precedence)

```bash
export KERNELONE_BACKEND_PORT=8080  # This will be used
export POLARIS_BACKEND_PORT=49977  # This will be ignored
```

## Migration Checklist

- [ ] Update shell profiles to use `KERNELONE_*` prefix
- [ ] Update CI/CD pipelines to use `KERNELONE_*` prefix
- [ ] Update Docker compose files to use `KERNELONE_*` prefix
- [ ] Update documentation references
- [ ] Notify team members about the migration

## Backward Compatibility

The system maintains full backward compatibility. Existing `POLARIS_*` environment variables will continue to work, but a deprecation warning may be added in future releases.

## Future Deprecation Timeline

| Phase | Date | Action |
|-------|------|--------|
| Phase 1 | Current | Both prefixes supported, KERNELONE_* preferred |
| Phase 2 | TBD | Deprecation warnings for POLARIS_* |
| Phase 3 | TBD | Removal of POLARIS_* support |
