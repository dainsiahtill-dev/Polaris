from __future__ import annotations

import json
import re
from dataclasses import dataclass


@dataclass
class InputSanitizer:
    """Sanitizes user input to prevent injection attacks."""

    def sanitize_command(self, user_input: str) -> str:
        """Sanitize input for shell commands."""
        if not isinstance(user_input, str):
            return str(user_input)

        # Remove dangerous shell characters (including \n, \r, \t)
        dangerous_chars = r"[;&|`$\n\r\t()<>\\!]"
        sanitized = re.sub(dangerous_chars, "", user_input)

        # Remove command substitutions
        sanitized = re.sub(r"\$\([^)]*\)", "", sanitized)
        sanitized = re.sub(r"`[^`]+`", "", sanitized)
        sanitized = re.sub(r"\$[{]?[a-zA-Z_][a-zA-Z0-9_]*[}]?", "", sanitized)

        # Remove path traversal attempts
        sanitized = re.sub(r"\.\./", "", sanitized)
        sanitized = re.sub(r"\.\.[\\/]", "", sanitized)

        return sanitized.strip()

    def sanitize_filename(self, user_input: str) -> str:
        """Sanitize input for filenames."""
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
        """Validate JSON input."""
        if not isinstance(user_input, str):
            return False
        try:
            json.loads(user_input)
            return True
        except (json.JSONDecodeError, ValueError):
            return False
