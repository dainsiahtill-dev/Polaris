---
name: agent-builder
description: Design AI agents for any domain with structured architecture
tags: [agent-design, architecture, best-practices]
---

# Agent Builder Skill

Use this skill when designing AI agents for new domains or use cases.

## Design Process

### 1. Define Agent Purpose
- **Goal**: What problem does the agent solve?
- **Scope**: What is in/out of scope?
- **Success Criteria**: How do we measure success?

### 2. Choose Architecture Pattern

#### Option A: ReAct (Reasoning + Acting)
```
Thought → Action → Observation → ... → Answer
```
Best for: Multi-step reasoning, tool use

#### Option B: Plan-and-Execute
```
Plan → Execute Steps → Verify → Deliver
```
Best for: Complex tasks with clear sub-tasks

#### Option C: Reflection
```
Draft → Critique → Revise → Final
```
Best for: Quality-sensitive outputs (code, writing)

### 3. Design Tool Set

Tools should be:
- **Atomic**: Do one thing well
- **Composable**: Can be combined
- **Safe**: Have validation and limits

Common tool categories:
- `read` / `write` / `edit` - File operations
- `bash` - Command execution
- `search` - Information retrieval
- `ask` - Human-in-the-loop

### 4. Define System Prompt

Structure:
```
ROLE: Who you are
GOAL: What you're trying to achieve
CONSTRAINTS: Hard limits
TOOLS: Available capabilities
WORKFLOW: How to approach tasks
OUTPUT FORMAT: Expected response structure
```

### 5. Set Safety Boundaries

- Path sandboxing
- Command filtering
- Timeout limits
- Budget constraints

## Example: Code Review Agent

```yaml
name: code-reviewer
role: Expert code reviewer
goal: Find bugs, security issues, and suggest improvements
constraints:
  - Never execute untrusted code
  - Focus on critical issues first
  - Be constructive in feedback
tools:
  - read_file
  - search_code
  - write_review
workflow:
  1. Read the code carefully
  2. Check for: bugs, security, performance, maintainability
  3. Write detailed review comments
  4. Suggest concrete improvements
```

## Anti-Patterns to Avoid

1. **Too many tools** - Start with 3-5, add as needed
2. **Vague prompts** - Be specific about role and constraints
3. **No safety limits** - Always set boundaries
4. **Monolithic design** - Prefer composable sub-agents
5. **Ignoring context limits** - Plan for compression
