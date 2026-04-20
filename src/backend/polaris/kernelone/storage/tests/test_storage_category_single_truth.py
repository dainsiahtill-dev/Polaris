"""Tests for StorageCategory single-source-of-truth invariant.

Verifies that:
1. StorageCategory is importable from contracts.py.
2. All members are semantically identical between contracts.py and policy.py
   (policy.py now re-exports from contracts.py, but this test makes the
   invariant explicit and catches regressions).
3. policy.py does NOT define its own StorageCategory class.
"""

from __future__ import annotations


def test_storage_category_importable_from_contracts() -> None:
    """StorageCategory must be importable from contracts and be a str-Enum."""
    from enum import Enum

    from polaris.kernelone.storage.contracts import StorageCategory

    assert issubclass(StorageCategory, str), "StorageCategory must subclass str"
    assert issubclass(StorageCategory, Enum), "StorageCategory must be an Enum"


def test_storage_category_all_members_present() -> None:
    """All expected members must be present in contracts.StorageCategory."""
    from polaris.kernelone.storage.contracts import StorageCategory

    expected_members = {
        "GLOBAL_CONFIG",
        "WORKSPACE_PERSISTENT",
        "RUNTIME_CURRENT",
        "RUNTIME_RUN",
        "WORKSPACE_HISTORY",
        "FACTORY_CURRENT",
        "FACTORY_HISTORY",
    }
    actual_members = {m.name for m in StorageCategory}
    assert expected_members == actual_members, f"Member mismatch. Expected: {expected_members}, Got: {actual_members}"


def test_storage_category_values_match_expected() -> None:
    """Member values must match the canonical string values."""
    from polaris.kernelone.storage.contracts import StorageCategory

    expected_values = {
        "GLOBAL_CONFIG": "global_config",
        "WORKSPACE_PERSISTENT": "workspace_persistent",
        "RUNTIME_CURRENT": "runtime_current",
        "RUNTIME_RUN": "runtime_run",
        "WORKSPACE_HISTORY": "workspace_history",
        "FACTORY_CURRENT": "factory_current",
        "FACTORY_HISTORY": "factory_history",
    }
    for name, value in expected_values.items():
        member = StorageCategory[name]
        assert member.value == value, f"StorageCategory.{name} has value {member.value!r}, expected {value!r}"


def test_policy_storage_category_is_same_object_as_contracts() -> None:
    """policy.StorageCategory must be the same class as contracts.StorageCategory.

    After the fix, policy.py re-exports from contracts.py, so both names must
    resolve to the identical class object.
    """
    from polaris.kernelone.storage import contracts, policy

    assert policy.StorageCategory is contracts.StorageCategory, (
        "policy.StorageCategory and contracts.StorageCategory must be the same object. "
        "The fix requires policy.py to import StorageCategory from contracts.py, "
        "not define its own."
    )


def test_policy_module_has_no_own_storage_category_definition() -> None:
    """policy.py must not define its own StorageCategory class.

    We verify this by checking that no class named StorageCategory is defined
    *inside* the policy module's source (i.e., the class's __module__ points
    to contracts, not policy).
    """
    from polaris.kernelone.storage import policy

    cat_cls = getattr(policy, "StorageCategory", None)
    assert cat_cls is not None, "policy.StorageCategory must still be accessible"
    assert cat_cls.__module__ == "polaris.kernelone.storage.contracts", (
        f"StorageCategory.__module__ is {cat_cls.__module__!r}. "
        "It must be 'polaris.kernelone.storage.contracts' — policy.py "
        "must not define its own StorageCategory."
    )


def test_storage_category_accessible_from_package_init() -> None:
    """StorageCategory must be re-exported from the storage package __init__."""
    from polaris.kernelone import storage
    from polaris.kernelone.storage.contracts import StorageCategory

    pkg_cat = getattr(storage, "StorageCategory", None)
    assert pkg_cat is not None, "StorageCategory must be exported from storage package"
    assert pkg_cat is StorageCategory, "storage.StorageCategory must be the contracts.StorageCategory object"
