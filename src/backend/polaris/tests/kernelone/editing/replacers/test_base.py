"""Tests for EditReplacer abstract base class."""

from __future__ import annotations

from collections.abc import Generator

import pytest
from polaris.kernelone.editing.replacers.base import EditReplacer


class TestEditReplacerAbstract:
    """Tests that EditReplacer enforces abstract method implementation."""

    def test_cannot_instantiate_directly(self) -> None:
        with pytest.raises(TypeError):
            EditReplacer()

    def test_must_implement_name(self) -> None:
        class PartialImpl(EditReplacer):
            @property
            def priority(self) -> int:
                return 1

            def find(self, content: str, search: str) -> Generator[str, None, None]:
                yield ""

        with pytest.raises(TypeError):
            PartialImpl()

    def test_must_implement_priority(self) -> None:
        class PartialImpl(EditReplacer):
            @property
            def name(self) -> str:
                return "test"

            def find(self, content: str, search: str) -> Generator[str, None, None]:
                yield ""

        with pytest.raises(TypeError):
            PartialImpl()

    def test_must_implement_find(self) -> None:
        class PartialImpl(EditReplacer):
            @property
            def name(self) -> str:
                return "test"

            @property
            def priority(self) -> int:
                return 1

        with pytest.raises(TypeError):
            PartialImpl()


class TestEditReplacerFullImpl:
    """Tests with a concrete implementation of EditReplacer."""

    @pytest.fixture
    def replacer(self) -> EditReplacer:
        class TestReplacer(EditReplacer):
            @property
            def name(self) -> str:
                return "test_replacer"

            @property
            def priority(self) -> int:
                return 5

            def find(self, content: str, search: str) -> Generator[str, None, None]:
                if search in content:
                    yield search

        return TestReplacer()

    def test_name_property(self, replacer: EditReplacer) -> None:
        assert replacer.name == "test_replacer"

    def test_priority_property(self, replacer: EditReplacer) -> None:
        assert replacer.priority == 5

    def test_find_yields_match(self, replacer: EditReplacer) -> None:
        results = list(replacer.find("hello world", "world"))
        assert results == ["world"]

    def test_find_no_match(self, replacer: EditReplacer) -> None:
        results = list(replacer.find("hello world", "foo"))
        assert results == []

    def test_find_empty_content(self, replacer: EditReplacer) -> None:
        results = list(replacer.find("", "test"))
        assert results == []

    def test_find_empty_search(self, replacer: EditReplacer) -> None:
        results = list(replacer.find("hello", ""))
        assert results == [""]

    def test_is_instance_of_abc(self, replacer: EditReplacer) -> None:
        assert isinstance(replacer, EditReplacer)

    def test_subclass_of_abc(self, replacer: EditReplacer) -> None:
        assert issubclass(type(replacer), EditReplacer)


class TestEditReplacerPriorityOrdering:
    """Tests for priority-based ordering behavior."""

    def test_lower_priority_tried_first_convention(self) -> None:
        class LowPriorityReplacer(EditReplacer):
            @property
            def name(self) -> str:
                return "low"

            @property
            def priority(self) -> int:
                return 1

            def find(self, content: str, search: str) -> Generator[str, None, None]:
                yield ""

        class HighPriorityReplacer(EditReplacer):
            @property
            def name(self) -> str:
                return "high"

            @property
            def priority(self) -> int:
                return 10

            def find(self, content: str, search: str) -> Generator[str, None, None]:
                yield ""

        low = LowPriorityReplacer()
        high = HighPriorityReplacer()
        assert low.priority < high.priority

    def test_negative_priority_allowed(self) -> None:
        class NegativeReplacer(EditReplacer):
            @property
            def name(self) -> str:
                return "negative"

            @property
            def priority(self) -> int:
                return -5

            def find(self, content: str, search: str) -> Generator[str, None, None]:
                yield ""

        replacer = NegativeReplacer()
        assert replacer.priority == -5

    def test_zero_priority_allowed(self) -> None:
        class ZeroReplacer(EditReplacer):
            @property
            def name(self) -> str:
                return "zero"

            @property
            def priority(self) -> int:
                return 0

            def find(self, content: str, search: str) -> Generator[str, None, None]:
                yield ""

        replacer = ZeroReplacer()
        assert replacer.priority == 0


class TestEditReplacerFindVariations:
    """Tests for different find() implementations."""

    def test_find_yields_multiple_matches(self) -> None:
        class MultiFinder(EditReplacer):
            @property
            def name(self) -> str:
                return "multi"

            @property
            def priority(self) -> int:
                return 1

            def find(self, content: str, search: str) -> Generator[str, None, None]:
                import re

                for match in re.finditer(re.escape(search), content):
                    yield match.group()

        replacer = MultiFinder()
        results = list(replacer.find("abc abc abc", "abc"))
        assert len(results) == 3

    def test_find_can_yield_empty_strings(self) -> None:
        class EmptyYielder(EditReplacer):
            @property
            def name(self) -> str:
                return "empty"

            @property
            def priority(self) -> int:
                return 1

            def find(self, content: str, search: str) -> Generator[str, None, None]:
                yield ""
                yield ""

        replacer = EmptyYielder()
        results = list(replacer.find("anything", "search"))
        assert results == ["", ""]

    def test_find_can_modify_yielded_text(self) -> None:
        class Modifier(EditReplacer):
            @property
            def name(self) -> str:
                return "modifier"

            @property
            def priority(self) -> int:
                return 1

            def find(self, content: str, search: str) -> Generator[str, None, None]:
                if search in content:
                    yield search.upper()

        replacer = Modifier()
        results = list(replacer.find("hello world", "world"))
        assert results == ["WORLD"]
