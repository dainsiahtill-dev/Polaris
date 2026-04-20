---
name: code-review
description: Comprehensive code review with security, performance, and maintainability checklists
tags: [code-review, security, quality-assurance]
---

# Code Review Skill

Use this skill when reviewing code for quality, security, and best practices.

## Review Checklist

### 1. Correctness
- [ ] Logic errors
- [ ] Edge cases handled
- [ ] Error handling paths
- [ ] Null/None checks
- [ ] Type safety

### 2. Security
- [ ] Input validation
- [ ] Injection vulnerabilities (SQL, XSS, command)
- [ ] Authentication/authorization
- [ ] Sensitive data exposure
- [ ] Cryptographic practices

### 3. Performance
- [ ] Algorithmic complexity
- [ ] Database query efficiency
- [ ] Memory usage
- [ ] Caching opportunities
- [ ] Resource leaks

### 4. Maintainability
- [ ] Clear naming
- [ ] Single responsibility
- [ ] Function length (prefer < 50 lines)
- [ ] Comment quality
- [ ] Test coverage

### 5. Architecture
- [ ] Consistent patterns
- [ ] Proper abstraction
- [ ] Dependency direction
- [ ] Coupling/cohesion

## Severity Levels

| Level | Description | Examples |
|-------|-------------|----------|
| **CRITICAL** | Security vulnerability, data loss | SQL injection, unvalidated input |
| **HIGH** | Likely bugs, significant issues | Race conditions, unhandled errors |
| **MEDIUM** | Code smells, minor issues | Poor naming, missing tests |
| **LOW** | Style, nitpicks | Whitespace, minor refactors |

## Review Template

```markdown
## Summary
- Lines reviewed: {n}
- Issues found: {critical} critical, {high} high, {medium} medium, {low} low
- Overall: {excellent/good/needs-work}

## Critical Issues (must fix)
1. **[CRITICAL]** {issue}
   - Location: {file}:{line}
   - Problem: {description}
   - Fix: {suggestion}

## High Priority Issues (should fix)
...

## Suggestions (nice to have)
...

## Positive Findings
- {what was done well}
```

## Language-Specific Notes

### Python
- Use type hints
- Follow PEP 8
- Prefer exceptions over error codes
- Use context managers (with)

### TypeScript/JavaScript
- Enable strict mode
- Prefer const/let over var
- Handle async errors
- Avoid any type

### Rust
- Handle Result/Option properly
- Minimize clone()
- Use iterators effectively
- Check unsafe blocks carefully

## Common Issues by Category

### Security
1. **Command Injection**
   ```python
   # BAD
   os.system(f"ls {user_input}")

   # GOOD
   subprocess.run(["ls", user_input], shell=False)
   ```

2. **Path Traversal**
   ```python
   # BAD
   open(f"/data/{filename}")

   # GOOD
   safe_path = sandbox / filename
   if safe_path.resolve().startswith(sandbox):
       open(safe_path)
   ```

3. **Hardcoded Secrets**
   ```python
   # BAD
   API_KEY = "sk-abc123"

   # GOOD
   API_KEY = os.environ["API_KEY"]
   ```

### Performance
1. **N+1 Queries**
   ```python
   # BAD
   for user in users:
       orders = db.query(f"SELECT * FROM orders WHERE user_id={user.id}")

   # GOOD
   orders = db.query("""
       SELECT * FROM orders
       WHERE user_id IN (SELECT id FROM users)
   """)
   ```

2. **Inefficient Loops**
   ```python
   # BAD
   result = []
   for i in range(len(items)):
       result.append(process(items[i]))

   # GOOD
   result = [process(item) for item in items]
   ```
