# 贡献指南 · Contributing Guide

感谢你对 **Polaris** 的关注！我们欢迎任何形式的贡献，包括 Bug 报告、功能建议、文档改进和代码提交。

---

## 📋 开始之前

1. 请先阅读 [README.md](./README.md) 了解项目整体架构与设计哲学。
2. 阅读 [AGENTS.md](./AGENTS.md) 了解核心治理规范（所有代码贡献必须遵守）。
3. 阅读 `src/backend/docs/AGENT_ARCHITECTURE_STANDARD.md` 了解后端架构标准。

---

## 🐛 报告 Bug

请通过 [GitHub Issues](../../issues) 提交，并包含：

- **复现步骤**：清晰的最小化复现路径
- **预期行为 vs 实际行为**
- **环境信息**：OS、Python 版本、Node 版本
- **相关日志**：来自 `runtime/` 或终端的错误输出

---

## 💡 提交功能建议

在开 Issue 之前，请先确认：

- [ ] 该功能不在现有 [Features Matrix](./README.md#-深度全景核心架构与功能矩阵-features-matrix) 中
- [ ] 该功能符合 Polaris 的核心定位（元工具平台，禁止添加业务代码）

---

## 🔧 代码贡献流程

### 1. Fork & Clone

```bash
git clone https://github.com/YOUR_USERNAME/polaris.git
cd polaris
```

### 2. 安装依赖

```bash
npm run setup:dev
```

### 3. 创建功能分支

```bash
git checkout -b feat/your-feature-name
# 或
git checkout -b fix/your-bug-fix
```

### 4. 编写代码

请遵守以下强制规范：

- **所有文本读写必须显式使用 UTF-8**
- **后端代码**必须先读 `src/backend/AGENTS.md`，新实现优先落在 `src/backend/polaris/` 下
- **Cell 开发**必须先复用已有 Cell 能力，基于 `src/backend/polaris/kernelone/`
- 禁止在主仓代码中添加任何目标项目 / 业务相关代码

### 5. 质量门禁（三道必过）

```bash
# 1. 代码格式
ruff check . --fix && ruff format .

# 2. 类型检查
mypy <your_file>.py

# 3. 单元测试
pytest <your_test_file>.py -v
```

三道门禁全部绿灯后才可提交 PR。

### 6. 提交规范

使用 [Conventional Commits](https://www.conventionalcommits.org/) 格式：

```
feat(director): add scope conflict detection for parallel execution
fix(kernel): resolve false-positive intent classification for Chinese substrings
docs(readme): update features matrix with EDA task market details
refactor(cells): migrate architect cell to KernelOne substrate
```

### 7. 提交 Pull Request

- PR 标题遵循 Conventional Commits 格式
- 描述中说明：**改了什么、为什么改、如何验证**
- 关联对应的 Issue（如有）

---

## 🏛️ 架构约束（不可违反）

| 约束 | 说明 |
|------|------|
| 禁止业务代码入主仓 | Polaris 是元工具平台 |
| Cell 复用优先 | 新开发必须先复用已有 Cell |
| KernelOne 底座优先 | 所有新能力基于 `kernelone/` 契约链路 |
| 旧目录只做兼容垫片 | `app/`、`core/`、`api/`、`scripts/` 不承载新主实现 |

---

## 📄 许可协议

提交代码即代表你同意将贡献内容以 [MIT License](./LICENSE) 授权。
