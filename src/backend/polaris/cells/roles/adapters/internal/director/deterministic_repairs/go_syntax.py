"""Conservative lexical helpers for deterministic Go repairs.

This is intentionally not a general Go parser.  It exposes only the source
spans needed by the repair planner and fails closed when syntax cannot be
identified unambiguously.  Comments and string/rune literals are kept out of
code-token matching so textual evidence cannot be mistaken for executable Go.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

_DECLARATION_KEYWORDS = frozenset({"func", "type"})
_OPENING_TO_CLOSING = {"(": ")", "[": "]", "{": "}"}
_CLOSING_TO_OPENING = {value: key for key, value in _OPENING_TO_CLOSING.items()}


@dataclass(frozen=True, slots=True)
class GoImportLiteral:
    """One unescaped import string content span."""

    start: int
    end: int
    path: str


@dataclass(frozen=True, slots=True)
class GoDeclaration:
    """A safely bounded top-level function/method or type declaration."""

    file: str
    kind: str
    name: str
    start: int
    end: int
    line: int
    signature: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _GoToken:
    kind: str
    value: str
    start: int
    end: int
    line: int


def _scan_go_tokens(source: str) -> tuple[_GoToken, ...]:
    tokens: list[_GoToken] = []
    index = 0
    line = 1
    length = len(source)
    while index < length:
        character = source[index]
        if character in {" ", "\t", "\r", "\f", "\v"}:
            index += 1
            continue
        if character == "\n":
            tokens.append(_GoToken("newline", "\n", index, index + 1, line))
            line += 1
            index += 1
            continue
        if source.startswith("//", index):
            newline = source.find("\n", index + 2)
            index = length if newline < 0 else newline
            continue
        if source.startswith("/*", index):
            closing = source.find("*/", index + 2)
            end = length if closing < 0 else closing + 2
            cursor = index
            while True:
                newline = source.find("\n", cursor, end)
                if newline < 0:
                    break
                tokens.append(_GoToken("newline", "\n", newline, newline + 1, line))
                line += 1
                cursor = newline + 1
            index = end
            continue
        if character in {'"', "'", "`"}:
            quote = character
            token_start = index
            token_line = line
            index += 1
            content_start = index
            while index < length:
                current = source[index]
                if quote != "`" and current == "\\":
                    index = min(length, index + 2)
                    continue
                if current == quote:
                    content = source[content_start:index]
                    index += 1
                    tokens.append(
                        _GoToken("string", content, token_start, index, token_line)
                    )
                    break
                if current == "\n":
                    line += 1
                index += 1
            continue
        if character.isalpha() or character == "_":
            token_start = index
            index += 1
            while index < length and (source[index].isalnum() or source[index] == "_"):
                index += 1
            tokens.append(
                _GoToken(
                    "identifier",
                    source[token_start:index],
                    token_start,
                    index,
                    line,
                )
            )
            continue
        tokens.append(_GoToken("punctuation", character, index, index + 1, line))
        index += 1
    return tuple(sorted(tokens, key=lambda token: (token.start, token.end)))


def _skip_newlines(tokens: Sequence[_GoToken], index: int) -> int:
    while index < len(tokens) and tokens[index].kind == "newline":
        index += 1
    return index


def _matching_token_index(tokens: Sequence[_GoToken], opening_index: int) -> int | None:
    opening = tokens[opening_index].value
    closing = _OPENING_TO_CLOSING.get(opening)
    if closing is None:
        return None
    stack = [opening]
    for index in range(opening_index + 1, len(tokens)):
        value = tokens[index].value
        if value in _OPENING_TO_CLOSING:
            stack.append(value)
            continue
        expected_opening = _CLOSING_TO_OPENING.get(value)
        if expected_opening is None:
            continue
        if not stack or stack[-1] != expected_opening:
            return None
        stack.pop()
        if not stack:
            return index
    return None


def iter_go_import_literals(source: str) -> tuple[GoImportLiteral, ...]:
    """Return only real import literals, never comments or unrelated strings."""

    tokens = _scan_go_tokens(source)
    literals: list[GoImportLiteral] = []
    index = 0
    depth = 0
    while index < len(tokens):
        token = tokens[index]
        if token.value in {"{", "(", "["}:
            depth += 1
            index += 1
            continue
        if token.value in {"}", ")", "]"}:
            depth = max(0, depth - 1)
            index += 1
            continue
        if depth != 0 or token.kind != "identifier" or token.value != "import":
            index += 1
            continue

        cursor = _skip_newlines(tokens, index + 1)
        if cursor >= len(tokens):
            break
        if tokens[cursor].value == "(":
            closing = _matching_token_index(tokens, cursor)
            if closing is None:
                break
            for candidate in tokens[cursor + 1 : closing]:
                if candidate.kind == "string" and "\\" not in candidate.value:
                    literals.append(
                        GoImportLiteral(
                            start=candidate.start + 1,
                            end=candidate.end - 1,
                            path=candidate.value,
                        )
                    )
            index = closing + 1
            continue

        for candidate in tokens[cursor : min(len(tokens), cursor + 4)]:
            if candidate.kind == "string" and "\\" not in candidate.value:
                literals.append(
                    GoImportLiteral(
                        start=candidate.start + 1,
                        end=candidate.end - 1,
                        path=candidate.value,
                    )
                )
                break
            if candidate.kind == "newline" or candidate.value == ";":
                break
        index = cursor + 1
    return tuple(sorted(literals, key=lambda literal: literal.start))


def _parse_func_declaration(
    *,
    file: str,
    tokens: Sequence[_GoToken],
    start_index: int,
) -> GoDeclaration | None:
    cursor = _skip_newlines(tokens, start_index + 1)
    if cursor >= len(tokens):
        return None
    if tokens[cursor].value == "(":
        receiver_end = _matching_token_index(tokens, cursor)
        if receiver_end is None:
            return None
        cursor = _skip_newlines(tokens, receiver_end + 1)
    if cursor >= len(tokens) or tokens[cursor].kind != "identifier":
        return None
    name = tokens[cursor].value

    body_start: int | None = None
    scan = cursor + 1
    delimiter_depth = 0
    while scan < len(tokens):
        value = tokens[scan].value
        if value in {"(", "["}:
            delimiter_depth += 1
        elif value in {")",
            "]",
        }:
            delimiter_depth = max(0, delimiter_depth - 1)
        elif value == "{" and delimiter_depth == 0:
            body_start = scan
            break
        elif tokens[scan].kind == "newline" and delimiter_depth == 0:
            return None
        scan += 1
    if body_start is None:
        return None
    body_end = _matching_token_index(tokens, body_start)
    if body_end is None:
        return None
    end_index = body_end
    while end_index + 1 < len(tokens) and (
        tokens[end_index + 1].kind == "newline"
        or tokens[end_index + 1].value == ";"
    ):
        end_index += 1
    signature = tuple(
        token.value
        for token in tokens[start_index : body_end + 1]
        if token.kind != "newline"
    )
    return GoDeclaration(
        file=file,
        kind="func",
        name=name,
        start=tokens[start_index].start,
        end=tokens[end_index].end,
        line=tokens[start_index].line,
        signature=signature,
    )


def _parse_type_declaration(
    *,
    file: str,
    tokens: Sequence[_GoToken],
    start_index: int,
) -> GoDeclaration | None:
    cursor = _skip_newlines(tokens, start_index + 1)
    if cursor >= len(tokens) or tokens[cursor].kind != "identifier":
        return None
    name = tokens[cursor].value
    stack: list[str] = []
    boundary_index = len(tokens)
    scan = cursor + 1
    while scan < len(tokens):
        token = tokens[scan]
        value = token.value
        if value in _OPENING_TO_CLOSING:
            stack.append(value)
        elif value in _CLOSING_TO_OPENING:
            if not stack or stack[-1] != _CLOSING_TO_OPENING[value]:
                return None
            stack.pop()
        elif (token.kind == "newline" or value == ";") and not stack:
            boundary_index = scan + 1
            break
        scan += 1
    signature = tuple(
        token.value
        for token in tokens[start_index:boundary_index]
        if token.kind != "newline" and token.value != ";"
    )
    if not signature:
        return None
    end = tokens[boundary_index - 1].end if boundary_index else tokens[start_index].end
    return GoDeclaration(
        file=file,
        kind="type",
        name=name,
        start=tokens[start_index].start,
        end=end,
        line=tokens[start_index].line,
        signature=signature,
    )


def iter_go_top_level_declarations(file: str, source: str) -> tuple[GoDeclaration, ...]:
    """Return safely bounded top-level func/method and single type declarations."""

    tokens = _scan_go_tokens(source)
    declarations: list[GoDeclaration] = []
    index = 0
    brace_depth = 0
    paren_depth = 0
    bracket_depth = 0
    while index < len(tokens):
        token = tokens[index]
        if (
            brace_depth == 0
            and paren_depth == 0
            and bracket_depth == 0
            and token.kind == "identifier"
            and token.value in _DECLARATION_KEYWORDS
        ):
            if token.value == "func":
                declaration = _parse_func_declaration(
                    file=file, tokens=tokens, start_index=index
                )
            else:
                declaration = _parse_type_declaration(
                    file=file, tokens=tokens, start_index=index
                )
            if declaration is not None:
                declarations.append(declaration)
                while index < len(tokens) and tokens[index].end <= declaration.end:
                    index += 1
                continue

        if token.value == "{":
            brace_depth += 1
        elif token.value == "}":
            brace_depth = max(0, brace_depth - 1)
        elif token.value == "(":
            paren_depth += 1
        elif token.value == ")":
            paren_depth = max(0, paren_depth - 1)
        elif token.value == "[":
            bracket_depth += 1
        elif token.value == "]":
            bracket_depth = max(0, bracket_depth - 1)
        index += 1
    return tuple(declarations)
