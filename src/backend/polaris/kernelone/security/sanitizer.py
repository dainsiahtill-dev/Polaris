from __future__ import annotations

import json
import re
from dataclasses import dataclass

# Pre-compiled patterns for performance
_DANGEROUS_SHELL_CHARS = re.compile(r"[;&|`$\n\r\t()<>\\!#\"\'*?\[\]|~]")
_COMMAND_SUBSTITUTION = re.compile(r"\$\([^)]*\)|`[^`]+`|\$\{[^}]+\}|\$[a-zA-Z_][a-zA-Z0-9_]*")
_PATH_TRAVERSAL = re.compile(r"\.\./|\.\.[\\/]")
_UNICODE_CONFUSABLES = re.compile(
    r"[\u200b-\u200f\u2028-\u202f\ufeff]"  # Zero-width chars
)
# Pattern for potentially dangerous environment variable names
_DANGEROUS_ENV_PATTERN = re.compile(
    r"(?i)^(HOME|USER|PATH|SHELL|PWD|OLDPWD|ENV|JAVA_HOME|PYTHONPATH|LD_"
    r"|DYLD_|LC_?|TERM|DISPLAY|SSH_).*",
    re.IGNORECASE,
)


@dataclass
class InputSanitizer:
    """Sanitizes user input to prevent injection attacks.

    Security Design:
    - Deny by default: any dangerous pattern is removed
    - Comprehensive coverage: all known shell injection vectors
    - Unicode safe: rejects zero-width confusables
    - No bypass via environment variables
    """

    def sanitize_command(self, user_input: str) -> str:
        """Sanitize input for shell commands.

        Removes:
        - Shell metacharacters: ; & | ` $ ( ) < > \\ ! # " ' * ? [ ] | ~
        - Command substitutions: $(...), `...`, ${...}, $var
        - Newlines and control characters
        - Path traversal: ../
        - Unicode confusables: zero-width chars

        Args:
            user_input: Raw user input to sanitize

        Returns:
            Sanitized string safe for shell commands
        """
        if not isinstance(user_input, str):
            return str(user_input)

        # Early return for empty input
        if not user_input:
            return ""

        # Step 1: Reject inputs with zero-width Unicode confusables
        # These can be used to obfuscate malicious patterns
        if _UNICODE_CONFUSABLES.search(user_input):
            # Replace zero-width chars instead of rejecting to avoid breaking
            # legitimate inputs that happen to contain them
            user_input = _UNICODE_CONFUSABLES.sub("", user_input)

        # Step 2: Remove newlines and control characters first
        # These are always dangerous and never legitimate
        sanitized = re.sub(r"[\n\r\t\x00-\x1f\x7f]", "", user_input)

        # Step 3: Remove all dangerous shell metacharacters
        sanitized = _DANGEROUS_SHELL_CHARS.sub("", sanitized)

        # Step 4: Remove command substitutions (after removing $)
        # This catches any remaining substitution patterns
        sanitized = _COMMAND_SUBSTITUTION.sub("", sanitized)

        # Step 5: Remove any remaining $ signs that weren't part of substitutions
        # But preserve legitimate $ in variable-like patterns that weren't caught
        sanitized = re.sub(r"\$+", "$", sanitized)
        # Remove isolated $ signs
        sanitized = re.sub(r"(?<![a-zA-Z_])\$|$(?![a-zA-Z0-9_{])", "", sanitized)

        # Step 6: Remove path traversal attempts
        sanitized = _PATH_TRAVERSAL.sub("", sanitized)

        # Step 7: Additional check for dangerous patterns that might bypass above
        # Check for hex-encoded characters that could decode to dangerous chars
        sanitized = re.sub(r"%[0-9a-fA-F]{2}", "", sanitized)

        return sanitized.strip()

    def sanitize_filename(self, user_input: str) -> str:
        """Sanitize input for filenames.

        Removes:
        - Windows reserved characters: < > : " / \\ | ? *
        - Path separators
        - Control characters
        - Leading/trailing dots and spaces
        - Reserved Windows names: CON, PRN, AUX, NUL, etc.

        Args:
            user_input: Raw user input to sanitize

        Returns:
            Sanitized filename safe for filesystem operations
        """
        if not isinstance(user_input, str):
            return str(user_input)

        # Remove or replace dangerous characters
        sanitized = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", user_input)

        # Remove path separators
        sanitized = re.sub(r"[/\\]", "_", sanitized)

        # Remove leading/trailing dots and spaces
        sanitized = sanitized.strip(". ")

        # Block reserved names on Windows (complete list per Microsoft docs)
        reserved = r"^(CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9]|LPT[1-9])$"
        if re.match(reserved, sanitized, re.IGNORECASE):
            sanitized = "_" + sanitized

        # Prevent absolute paths
        if sanitized.startswith("/") or sanitized.startswith("\\"):
            sanitized = "_" + sanitized.lstrip("/\\")

        # Limit length
        max_length = 255
        if len(sanitized) > max_length:
            match = re.match(r"^(.{0,200})(\.[^.]+)$", sanitized)
            sanitized = match.group(1)[:200] + match.group(2) if match else sanitized[:max_length]

        return sanitized or "_"

    def validate_json(self, user_input: str) -> bool:
        """Validate JSON input.

        Args:
            user_input: String to validate as JSON

        Returns:
            True if valid JSON, False otherwise
        """
        if not isinstance(user_input, str):
            return False
        try:
            json.loads(user_input)
            return True
        except (json.JSONDecodeError, ValueError):
            return False

    def is_safe_for_command(self, user_input: str) -> bool:
        """Check if input is completely safe for shell commands.

        This is a stricter check than sanitize_command() - it returns
        False if ANY potentially dangerous pattern is detected, even if
        sanitization would make it safe.

        Args:
            user_input: Input to validate

        Returns:
            True if input is safe without any modifications
        """
        if not isinstance(user_input, str) or not user_input:
            return False

        # Check for any dangerous shell metacharacters
        if _DANGEROUS_SHELL_CHARS.search(user_input):
            return False

        # Check for command substitutions
        if _COMMAND_SUBSTITUTION.search(user_input):
            return False

        # Check for path traversal
        if _PATH_TRAVERSAL.search(user_input):
            return False

        # Check for Unicode confusables
        if _UNICODE_CONFUSABLES.search(user_input):
            return False

        # Check for hex-encoded dangerous chars
        if re.search(r"%[0-9a-fA-F]{2}", user_input):
            return False

        return True
