"""Internal submodules for the pm_dispatch dispatch pipeline.

These modules hold bodies extracted verbatim from ``dispatch_pipeline.py``
during a lossless decomposition. ``dispatch_pipeline.py`` remains the
canonical, importable module and re-exports every public and privately
imported symbol; it loads these siblings via a file-relative bootstrap so
the package stays importable in isolation (no module-level cross-Cell
imports — see ``tests/test_pm_dispatch_no_delivery_import.py``).
"""

from __future__ import annotations
