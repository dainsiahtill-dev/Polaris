"""Unit tests for platform module solidification registry."""

from __future__ import annotations

import unittest

from polaris.kernelone.platform_modules.registry import (
    MODULE_CASCADE_ORDER,
    PLATFORM_MODULES,
    PlatformModuleStatus,
    get_module,
    list_modules,
    modules_by_status,
)


class TestPlatformModuleRegistry(unittest.TestCase):
    def test_cascade_order_covers_all_registered_modules(self) -> None:
        cascade = set(MODULE_CASCADE_ORDER)
        registered = set(PLATFORM_MODULES)
        self.assertEqual(cascade, registered)

    def test_sealed_modules_have_defect_and_pytest_targets(self) -> None:
        sealed = modules_by_status(PlatformModuleStatus.SEALED)
        self.assertGreaterEqual(len(sealed), 3, "R151–R153 must yield >=3 sealed modules")
        for module in sealed:
            self.assertTrue(module.sealed_by_defect, msg=module.module_id)
            self.assertTrue(module.pytest_targets, msg=module.module_id)
            self.assertTrue(module.invariants, msg=module.module_id)
            self.assertTrue(module.owner_paths, msg=module.module_id)

    def test_get_module_event_wait_is_sealed(self) -> None:
        module = get_module("M01_event_wait")
        self.assertEqual(module.status, PlatformModuleStatus.SEALED)
        self.assertIn("reconnect", " ".join(module.invariants).lower() + module.summary.lower())

    def test_dependencies_reference_existing_modules(self) -> None:
        for module in list_modules():
            for dep in module.depends_on:
                self.assertIn(dep, PLATFORM_MODULES, msg=f"{module.module_id} -> {dep}")

    def test_unknown_module_raises(self) -> None:
        with self.assertRaises(KeyError):
            get_module("M99_does_not_exist")

    def test_list_modules_preserves_cascade_order(self) -> None:
        ids = [module.module_id for module in list_modules()]
        self.assertEqual(ids, list(MODULE_CASCADE_ORDER))


if __name__ == "__main__":
    unittest.main()
