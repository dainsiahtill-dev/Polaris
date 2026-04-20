"""Base interface for Edit Replacers.

This module defines the abstract base class for all EditReplacer
implementations.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Generator


class EditReplacer(ABC):
    """Abstract base class for edit replacers.

    A replacer is responsible for finding potential matches of a
    search string within content, using a specific matching strategy.

    Each replacer has a priority that determines its position in
    the fallback chain. Lower priority values are tried first.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the name of this replacer strategy.

        Returns:
            Strategy name
        """

    @property
    @abstractmethod
    def priority(self) -> int:
        """Return the priority of this replacer.

        Lower values are tried first in the fallback chain.

        Returns:
            Priority value (lower = earlier)
        """

    @abstractmethod
    def find(self, content: str, search: str) -> Generator[str, None, None]:
        """Find matches of search string in content.

        Args:
            content: The content to search in
            search: The string to find

        Yields:
            Matched text found in content
        """
