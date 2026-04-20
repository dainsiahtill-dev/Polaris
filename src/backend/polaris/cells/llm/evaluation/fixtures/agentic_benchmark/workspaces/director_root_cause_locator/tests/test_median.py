from __future__ import annotations

import pytest
from src.median import median


def test_median_raises_on_empty_list() -> None:
    with pytest.raises(ValueError):
        median([])
