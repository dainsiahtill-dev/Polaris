import logging
from typing import Any

try:
    # Note: Modern tree-sitter setups might require building languages or using bindings
    # asking user to install `tree-sitter` and `tree-sitter-languages` simplifies this
    import tree_sitter_languages
    from tree_sitter import Parser

    TREE_SITTER_AVAILABLE = True
except ImportError:
    TREE_SITTER_AVAILABLE = False

logger = logging.getLogger(__name__)


class CodeParser:
    def __init__(self) -> None:
        self.available = TREE_SITTER_AVAILABLE
        self.parsers: dict[str, Any] = {}

    def get_parser(self, lang_name: str) -> Any | None:
        if not self.available:
            return None

        if lang_name not in self.parsers:
            try:
                parser = Parser()
                language = tree_sitter_languages.get_language(lang_name)
                parser.language = language
                self.parsers[lang_name] = parser
            except (RuntimeError, ValueError) as e:
                logger.warning(f"Failed to load tree-sitter language {lang_name}: {e}")
                return None
        return self.parsers.get(lang_name)

    def parse_file(self, content: str, extension: str) -> dict[str, Any]:
        """
        Parses code content and returns AST-based metadata.
        Falls back to basic stats if tree-sitter missing.
        """
        lang_map = {
            ".py": "python",
            ".ts": "typescript",
            ".tsx": "tsx",
            ".js": "javascript",
            ".rs": "rust",
            ".go": "go",
            ".cpp": "cpp",
            ".c": "c",
        }

        lang = lang_map.get(extension)
        if not lang or not self.available:
            return {"parsed": False, "reason": "unsupported_or_missing_lib"}

        parser = self.get_parser(lang)
        if not parser:
            return {"parsed": False, "reason": "parser_init_failed"}

        try:
            tree = parser.parse(bytes(content, "utf8"))
            root = tree.root_node

            # Basic extraction (e.g. counting functions)
            # This is a stub for more complex logic (walking the tree)
            # In a real impl, we would use queries to find function_definition

            return {
                "parsed": True,
                "language": lang,
                "node_type": root.type,
                "child_count": root.child_count,
                # "sexp": root.sexp() # S-expression string
            }
        except (RuntimeError, ValueError) as e:
            logger.error(f"Tree-sitter parse error: {e}")
            return {"parsed": False, "error": str(e)}


_parser = CodeParser()


def get_code_parser() -> CodeParser:
    return _parser
