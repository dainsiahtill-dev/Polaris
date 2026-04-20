"""Schema Validator for Tri-Axis Role Configuration.

Validates YAML configuration files against their schemas.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# Schema directory
_SCHEMA_DIR = Path(__file__).parent.parent.parent / "assets" / "roles" / "schema"


class ConfigValidationError(Exception):
    """Configuration validation error."""

    def __init__(self, file_path: Path, errors: list[str]) -> None:
        self.file_path = file_path
        self.errors = errors
        super().__init__(f"Validation failed for {file_path}: {errors}")


class SchemaValidator:
    """Validates role configuration files against schemas."""

    def __init__(self) -> None:
        self._schemas: dict[str, dict[str, Any]] = {}
        self._schema_load_errors: list[str] = []
        self._load_schemas()

    def _load_schemas(self) -> None:
        """Load all schema files."""
        if not _SCHEMA_DIR.exists():
            logger.warning(f"Schema directory not found: {_SCHEMA_DIR}")
            return

        for schema_file in _SCHEMA_DIR.glob("*.schema.yaml"):
            try:
                with open(schema_file, encoding="utf-8") as f:
                    schema = yaml.safe_load(f)
                schema_id = schema_file.stem.replace(".schema", "")
                self._schemas[schema_id] = schema
                logger.debug(f"Loaded schema: {schema_id}")
            except (yaml.YAMLError, OSError) as e:
                error_msg = f"Failed to load schema {schema_file}: {e}"
                logger.error(error_msg)
                self._schema_load_errors.append(error_msg)

    def has_schema_load_errors(self) -> bool:
        """Check if there were schema load errors."""
        return len(self._schema_load_errors) > 0

    def get_schema_load_errors(self) -> list[str]:
        """Get list of schema load errors."""
        return self._schema_load_errors.copy()

    def get_schema(self, schema_name: str) -> dict[str, Any] | None:
        """Get a schema by name."""
        return self._schemas.get(schema_name)

    def validate_anchor(self, config: dict[str, Any]) -> list[str]:
        """Validate an anchor configuration."""
        errors: list[str] = []
        schema = self._schemas.get("anchor")

        if not schema:
            return ["Schema 'anchor' not found"]

        errors.extend(self._validate_required_fields(config, schema, "anchor"))
        errors.extend(self._validate_workflow(config.get("macro_workflow", {})))
        errors.extend(self._validate_output_constraint(config.get("output_constraint", {})))

        return errors

    def validate_persona(self, config: dict[str, Any]) -> list[str]:
        """Validate a persona configuration."""
        errors: list[str] = []
        schema = self._schemas.get("persona")

        if not schema:
            return ["Schema 'persona' not found"]

        errors.extend(self._validate_required_fields(config, schema, "persona"))
        errors.extend(self._validate_vocabulary(config.get("vocabulary", [])))
        errors.extend(self._validate_expression(config.get("expression", {})))

        return errors

    def validate_profession(self, config: dict[str, Any]) -> list[str]:
        """Validate a profession configuration."""
        errors: list[str] = []
        schema = self._schemas.get("profession")

        if not schema:
            return ["Schema 'profession' not found"]

        errors.extend(self._validate_required_fields(config, schema, "profession"))
        errors.extend(self._validate_identity(config.get("identity", "")))
        errors.extend(self._validate_expertise(config.get("expertise", [])))
        errors.extend(self._validate_engineering_standards(config.get("engineering_standards", {})))
        errors.extend(self._validate_task_protocols(config.get("task_protocols", {})))

        return errors

    def _validate_required_fields(self, config: dict[str, Any], schema: dict[str, Any], schema_type: str) -> list[str]:
        """Validate required fields."""
        errors: list[str] = []
        required = schema.get("required", [])

        for field in required:
            if field not in config:
                errors.append(f"Missing required field: {field}")

        return errors

    def _validate_workflow(self, workflow: dict[str, Any]) -> list[str]:
        """Validate workflow configuration."""
        errors: list[str] = []

        if not workflow:
            return ["Workflow is empty"]

        if "type" not in workflow:
            errors.append("Workflow missing 'type'")

        if "stages" not in workflow:
            errors.append("Workflow missing 'stages'")
        else:
            stages = workflow["stages"]
            if not isinstance(stages, list):
                errors.append("Workflow stages must be a list")
            elif len(stages) == 0:
                errors.append("Workflow stages cannot be empty")

        return errors

    def _validate_output_constraint(self, constraint: dict[str, Any]) -> list[str]:
        """Validate output constraint configuration."""
        errors: list[str] = []

        required_fields = ["thinking_tag", "thinking_max_tokens", "required_order"]
        for field in required_fields:
            if field not in constraint:
                errors.append(f"Output constraint missing: {field}")

        if "thinking_max_tokens" in constraint:
            max_tokens = constraint["thinking_max_tokens"]
            if not isinstance(max_tokens, int):
                errors.append("thinking_max_tokens must be an integer")
            elif max_tokens < 50 or max_tokens > 1000:
                errors.append("thinking_max_tokens must be between 50 and 1000")

        return errors

    def _validate_vocabulary(self, vocabulary: list[Any]) -> list[str]:
        """Validate vocabulary list."""
        errors: list[str] = []

        if not isinstance(vocabulary, list):
            return ["Vocabulary must be a list"]

        if len(vocabulary) < 3:
            errors.append("Vocabulary must have at least 3 items")

        for i, item in enumerate(vocabulary):
            if not isinstance(item, str):
                errors.append(f"Vocabulary item {i} must be a string")

        return errors

    def _validate_expression(self, expression: dict[str, Any]) -> list[str]:
        """Validate expression configuration."""
        errors: list[str] = []
        required = ["greeting", "thinking_prefix", "thinking_suffix", "conclusion_prefix", "farewell"]

        for field in required:
            if field not in expression:
                errors.append(f"Expression missing: {field}")

        return errors

    def _validate_identity(self, identity: str) -> list[str]:
        """Validate identity definition."""
        errors: list[str] = []

        if not isinstance(identity, str):
            return ["Identity must be a string"]

        if len(identity) < 20:
            errors.append("Identity must be at least 20 characters")

        return errors

    def _validate_expertise(self, expertise: list[Any]) -> list[str]:
        """Validate expertise list."""
        errors: list[str] = []

        if not isinstance(expertise, list):
            return ["Expertise must be a list"]

        if len(expertise) < 3:
            errors.append("Expertise must have at least 3 items")

        return errors

    def _validate_engineering_standards(self, standards: dict[str, Any]) -> list[str]:
        """Validate engineering standards configuration."""
        errors: list[str] = []

        if not standards:
            return ["Engineering standards is empty"]

        if "coverage_mode" not in standards:
            errors.append("Engineering standards missing 'coverage_mode'")
        elif standards["coverage_mode"] not in ["inherit", "extend", "strict", "override"]:
            errors.append("coverage_mode must be one of: inherit, extend, strict, override")

        if "standards" not in standards:
            errors.append("Engineering standards missing 'standards'")

        if "red_lines" not in standards:
            errors.append("Engineering standards missing 'red_lines'")

        return errors

    def _validate_task_protocols(self, protocols: dict[str, Any]) -> list[str]:
        """Validate task protocols configuration."""
        errors: list[str] = []

        if not protocols:
            return []

        # Standard task protocols
        standard_protocols = [
            "new_code",
            "refactor",
            "code_review",
            "bug_fix",
            "security_review",
            "architecture_change",
            "planning",
        ]

        for key in protocols:
            if key not in standard_protocols:
                # Custom protocols are allowed, just log a warning
                logger.debug(f"Custom task protocol detected: {key}")

        return errors

    def validate_file(self, file_path: Path) -> list[str]:
        """Validate a configuration file based on its type."""
        errors: list[str] = []

        if not file_path.exists():
            return [f"File not found: {file_path}"]

        try:
            with open(file_path, encoding="utf-8") as f:
                config = yaml.safe_load(f)
        except yaml.YAMLError as e:
            return [f"YAML parse error: {e}"]

        if not config:
            return ["Empty configuration file"]

        # Determine schema type from file path
        if "anchors" in file_path.parts:
            return self.validate_anchor(config)
        elif "personas" in file_path.parts:
            return self.validate_persona(config)
        elif "professions" in file_path.parts:
            if file_path.name.startswith("_"):
                return []  # Skip base templates
            return self.validate_profession(config)
        elif "recipes" in file_path.parts:
            # Recipe validation is simpler
            if "recipes" not in config:
                return ["Missing 'recipes' key"]
            return []
        elif "formats" in file_path.parts:
            # Format validation is minimal
            return []

        return errors


def validate_all_configs() -> dict[str, list[str]]:
    """Validate all configuration files in the roles directory."""
    validator = SchemaValidator()
    results: dict[str, list[str]] = {}

    # Report schema load errors if any
    if validator.has_schema_load_errors():
        results["_schema_load_errors"] = validator.get_schema_load_errors()

    roles_dir = Path(__file__).parent.parent.parent / "assets" / "roles"

    if not roles_dir.exists():
        return {"error": [f"Roles directory not found: {roles_dir}"]}

    for yaml_file in roles_dir.rglob("*.yaml"):
        # Skip schema files
        if "schema" in yaml_file.parts:
            continue

        errors = validator.validate_file(yaml_file)
        if errors:
            results[str(yaml_file.relative_to(roles_dir))] = errors

    return results


# CLI entry point
if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)

    results = validate_all_configs()

    if not results:
        print("[PASS] All configurations are valid!")
        sys.exit(0)
    else:
        print("[FAIL] Validation errors found:")
        for file, errors in results.items():
            print(f"\n{file}:")
            for error in errors:
                print(f"  - {error}")
        sys.exit(1)
