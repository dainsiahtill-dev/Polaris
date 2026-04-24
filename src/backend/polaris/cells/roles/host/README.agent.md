# roles.host Cell

## 职责
Unified host protocol core. Provides HostKind, HostCapabilityProfile, and UnifiedHostAdapter so that role runtime can adapt execution strategy (streaming, async tools, file write, audit export) based on the calling host environment (workflow, electron_workbench, tui, cli, api_server, headless).

## 公开契约
模块: polaris.cells.roles.host.public.contracts

## 依赖
- roles.session

## 效果
- fs.read:workspace/**
