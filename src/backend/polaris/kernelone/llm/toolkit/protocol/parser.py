"""Protocol parser for the protocol module.

Parses various file operation protocols into unified FileOperation IR.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from polaris.kernelone.llm.toolkit.protocol.constants import EditType
from polaris.kernelone.llm.toolkit.protocol.models import FileOperation, _normalize_path

logger = logging.getLogger(__name__)


class ProtocolParser:
    """Unified protocol parser.

    Supports protocol dialects:
    1. Routed rich formats (apply_patch / editblock / unified diff / whole-file fence)
    2. PATCH_FILE + SEARCH/REPLACE (<<<<<<< SEARCH format)
    3. PATCH_FILE + SEARCH/REPLACE (SEARCH:\n...\nREPLACE:\n format)
    4. FILE: ... END FILE (full file)
    5. CREATE: ... END FILE (create new file)
    6. DELETE_FILE: path
    7. Standalone SEARCH/REPLACE blocks
    """

    # Regex patterns
    PATCH_FILE_HEADER = re.compile(r"(?:^|\n)\s*PATCH_FILE(?::|\s+)\s*([^\n]*?)\s*(?:\n|$)", re.IGNORECASE)
    FILE_HEADER = re.compile(r"(?:^|\n)\s*FILE(?::|\s+)\s*([^\n]*?)\s*(?:\n|$)", re.IGNORECASE)
    CREATE_HEADER = re.compile(r"(?:^|\n)\s*CREATE(?::|\s+)\s*([^\n]*?)\s*(?:\n|$)", re.IGNORECASE)
    DELETE_HEADER = re.compile(
        r"(?:^|\n)\s*DELETE(?:_FILE)?(?::|\s+)\s*([^\n]+?)\s*(?:\n|$)",
        re.IGNORECASE,
    )

    # SEARCH/REPLACE patterns
    SEARCH_REPLACE_GIT = re.compile(
        r"<<<<<<<\s*SEARCH\s*\n(.*?)\n=======\s*\n(.*?)\n>>>>>>>\s*REPLACE",
        re.DOTALL | re.IGNORECASE,
    )
    SEARCH_REPLACE_SIMPLE = re.compile(
        r"(?:^|\n)SEARCH:?\s*\n(.*?)\nREPLACE:?\s*\n(.*?)(?=\nSEARCH:?\s*\n|\nEND\s+(?:PATCH_FILE|FILE)|\Z)",
        re.DOTALL | re.IGNORECASE,
    )

    # End markers
    END_PATCH_FILE = re.compile(r"\n\s*END\s+PATCH_FILE\s*(?:\n|$)", re.IGNORECASE)
    END_FILE = re.compile(r"\n\s*END\s+(?:FILE|CREATE)\s*(?:\n|$)", re.IGNORECASE)

    @classmethod
    def parse(cls, text: str) -> list[FileOperation]:
        """Parse all protocol dialects, returning unified IR list.

        Args:
            text: Input text containing protocol operations

        Returns:
            List of FileOperation (deduplicated)
        """
        if not text or text.strip() in ("", "NO_CHANGES"):
            return []

        operations: list[FileOperation] = []
        seen: set[str] = set()  # Deduplication fingerprints

        # Parse in priority order
        parsers = [
            cls._parse_routed_editing_formats,
            cls._parse_delete_operations,
            cls._parse_patch_file_blocks,
            cls._parse_file_blocks,
            cls._parse_standalone_search_replace,
        ]

        for parser in parsers:
            try:
                ops = parser(text)
                for op in ops:
                    fingerprint = op.compute_hash()
                    if fingerprint not in seen:
                        seen.add(fingerprint)
                        operations.append(op)
            except (RuntimeError, ValueError) as e:
                logger.warning(f"Parser {parser.__name__} failed: {e}")

        return operations

    @classmethod
    def _parse_delete_operations(cls, text: str) -> list[FileOperation]:
        """Parse DELETE_FILE/DELETE operations."""
        operations = []
        for match in cls.DELETE_HEADER.finditer(text):
            path = _normalize_path(match.group(1))

            if path:
                operations.append(
                    FileOperation(
                        path=path,
                        edit_type=EditType.DELETE,
                        original_format="DELETE_FILE",
                        source_line=text[: match.start()].count("\n") + 1,
                    )
                )

        return operations

    @classmethod
    def _parse_patch_file_blocks(cls, text: str) -> list[FileOperation]:
        """Parse PATCH_FILE blocks (with SEARCH/REPLACE)."""
        operations: list[FileOperation] = []

        # Split PATCH_FILE blocks
        parts = cls.PATCH_FILE_HEADER.split(text)
        if len(parts) < 2:
            return operations

        for i in range(1, len(parts), 2):
            header = parts[i].strip()
            body = parts[i + 1] if i + 1 < len(parts) else ""

            # Extract file path
            file_path = cls._extract_file_path(header, body)
            if not file_path:
                continue

            # Extract to END PATCH_FILE
            block_end = cls.END_PATCH_FILE.search(body)
            if block_end:
                body = body[: block_end.start()]

            # Parse SEARCH/REPLACE
            search_replaces = cls._extract_search_replace(body)
            if search_replaces:
                for search, replace in search_replaces:
                    operations.append(
                        FileOperation(
                            path=file_path,
                            edit_type=EditType.SEARCH_REPLACE,
                            search=cls._normalize_search(search),
                            replace=cls._normalize_replace(replace),
                            original_format="PATCH_FILE+SEARCH_REPLACE",
                            source_line=text[: parts[i - 1].rfind("\n")].count("\n") + 1,
                        )
                    )
            else:
                # No SEARCH/REPLACE, treat as full file content
                content = cls._clean_code_content(body)
                if content:
                    operations.append(
                        FileOperation(
                            path=file_path,
                            edit_type=EditType.FULL_FILE,
                            replace=content,
                            original_format="PATCH_FILE_DIRECT",
                        )
                    )

        return operations

    @classmethod
    def _parse_file_blocks(cls, text: str) -> list[FileOperation]:
        """Parse FILE:/CREATE: blocks (full file format)."""
        operations = []

        for pattern, edit_type, format_name in [
            (cls.CREATE_HEADER, EditType.CREATE, "CREATE"),
            (cls.FILE_HEADER, EditType.FULL_FILE, "FILE"),
        ]:
            for match in pattern.finditer(text):
                file_path = _normalize_path(match.group(1))

                if not file_path:
                    continue

                # Extract content to END FILE
                start_pos = match.end()
                end_match = cls.END_FILE.search(text, start_pos)

                if end_match:
                    content = text[start_pos : end_match.start()]
                else:
                    # Check if next FILE block starts
                    next_file = cls.FILE_HEADER.search(text, start_pos)

                    content = text[start_pos : next_file.start()] if next_file else text[start_pos:]

                content = cls._clean_code_content(content)

                if content:
                    operations.append(
                        FileOperation(
                            path=file_path,
                            edit_type=edit_type,
                            replace=content,
                            original_format=format_name,
                            source_line=text[: match.start()].count("\n") + 1,
                        )
                    )

        return operations

    @classmethod
    def _parse_standalone_search_replace(cls, text: str) -> list[FileOperation]:
        """Parse standalone SEARCH/REPLACE blocks (without prefix wrapper)."""
        operations = []

        # Pattern: filepath.py\n<<<<<<< SEARCH
        standalone_pattern = re.compile(
            r"(?:^|\n)\s*([A-Za-z0-9_./\-]+\.[A-Za-z0-9_]+)\s*\n<<<<<<<\s*SEARCH\s*\n(.*?)\n=======\s*\n(.*?)\n>>>>>>\s*REPLACE",
            re.DOTALL,
        )

        for match in standalone_pattern.finditer(text):
            file_path = _normalize_path(match.group(1))
            search = cls._normalize_search(match.group(2))
            replace = cls._normalize_replace(match.group(3))

            if file_path:
                operations.append(
                    FileOperation(
                        path=file_path,
                        edit_type=EditType.SEARCH_REPLACE,
                        search=search,
                        replace=replace,
                        original_format="STANDALONE_SEARCH_REPLACE",
                        source_line=text[: match.start()].count("\n") + 1,
                    )
                )

        return operations

    @classmethod
    def _parse_routed_editing_formats(cls, text: str) -> list[FileOperation]:
        """Parse routed editing formats (apply_patch / editblock)."""
        # Import routing function
        try:
            from polaris.kernelone.editing import route_edit_operations
        except ImportError:
            return []

        operations = []
        root = Path(".").resolve()
        inchat_files: list[str] = []

        try:
            for p in root.rglob("*"):
                if p.is_file():
                    inchat_files.append(p.relative_to(root).as_posix())
                if len(inchat_files) >= 5000:
                    break
        except (RuntimeError, ValueError) as exc:
            logger.debug("workspace file listing failed: %s", exc)
            return []

        routed = route_edit_operations(text, inchat_files=inchat_files)

        for op in routed:
            if not op.path:
                continue
            if op.kind == "delete":
                operations.append(FileOperation(path=op.path, edit_type=EditType.DELETE))
            elif op.kind == "create":
                operations.append(
                    FileOperation(
                        path=op.path,
                        edit_type=EditType.CREATE,
                        replace=op.content,
                        move_to=op.move_to or None,
                    )
                )
            elif op.kind == "full_file":
                operations.append(
                    FileOperation(
                        path=op.path,
                        edit_type=EditType.FULL_FILE,
                        replace=op.content,
                        move_to=op.move_to or None,
                    )
                )
            elif op.kind == "search_replace":
                operations.append(
                    FileOperation(
                        path=op.path,
                        edit_type=EditType.SEARCH_REPLACE,
                        search=op.search,
                        replace=op.replace,
                        move_to=op.move_to or None,
                    )
                )

        return operations

    @classmethod
    def _extract_file_path(cls, header: str, body: str) -> str:
        """Extract file path from header."""
        header = header.strip()
        body = body.strip() if body else ""

        # First non-empty line of header or body
        for line in [header, *body.split("\n")[:3]]:
            line = line.strip()
            if not line:
                continue
            # Skip common markers
            if line.upper() in ("PATCH_FILE", "END PATCH_FILE", "SEARCH", "REPLACE"):
                continue
            if line.startswith("<<<<<<") or line.startswith("=======") or line.startswith(">>>>>>"):
                continue
            # If it looks like a path, use it
            if "/" in line or "." in line or "\\" in line:
                return _normalize_path(line)

        return _normalize_path(header) if header else ""

    @classmethod
    def _extract_search_replace(cls, body: str) -> list[tuple[str, str]]:
        """Extract SEARCH/REPLACE pairs from body."""
        pairs = []

        # Git-style: <<<<<<< SEARCH ... ======= ... >>>>>>> REPLACE
        for match in cls.SEARCH_REPLACE_GIT.finditer(body):
            search = match.group(1)
            replace = match.group(2)
            pairs.append((search, replace))

        # Simple style: SEARCH:\n...\nREPLACE:\n...
        for match in cls.SEARCH_REPLACE_SIMPLE.finditer(body):
            search = match.group(1)
            replace = match.group(2)
            pairs.append((search, replace))

        return pairs

    @staticmethod
    def _normalize_search(text: str) -> str:
        """Normalize search text, stripping empty markers."""
        text = text.rstrip("\n")
        # Strip common empty markers
        stripped = text.strip()
        if stripped.lower() in (
            "<empty>",
            "<empty or missing>",
            "empty",
            "empty or missing",
        ):
            return ""
        return text

    @staticmethod
    def _normalize_replace(text: str) -> str:
        """Normalize replace text."""
        return text.rstrip("\n")

    @staticmethod
    def _clean_code_content(content: str) -> str:
        """Clean code content, removing markers."""
        lines = content.splitlines()
        cleaned_lines = []

        for _i, line in enumerate(lines):
            upper = line.strip().upper()
            if upper.startswith("<<<<<<<") or upper.startswith("=======") or upper.startswith(">>>>>>"):
                continue
            if upper in ("<<<<<<< SEARCH", "<<<<<<< SEARCH\n"):
                continue
            if upper in ("<<<<<<< SEARCH",):
                continue
            cleaned_lines.append(line)

        result = "\n".join(cleaned_lines)
        # Remove trailing markers
        result = re.sub(r"\n\s*<<<<<<<\s*SEARCH\s*\n.*", "", result, flags=re.DOTALL | re.IGNORECASE)
        result = re.sub(r"\n\s*=======\s*\n.*", "", result, flags=re.DOTALL | re.IGNORECASE)
        result = re.sub(r"\n\s*>>>>>>\s*REPLACE\s*\n.*", "", result, flags=re.DOTALL | re.IGNORECASE)

        return result.strip("\n")
