# roles.scout (探子)

Auxiliary **read-only** code/symbol reconnaissance. Called inline by a main
role (Director/PM) within its own Turn — synchronous, side-effect-free, no
TaskMarket, no persistence. Entry point: `ScoutProbeService.probe()`.

Read-only is enforced at the read-tool adapter boundary (only
`ToolSpecRegistry` tools whose category is `read`). Never wire write/exec tools.
