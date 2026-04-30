"""Tests for polaris.delivery.cli.visualization.message_group."""

from __future__ import annotations

from unittest.mock import MagicMock

from polaris.delivery.cli.visualization.message_group import CollapsibleMessageGroup


class TestCollapsibleMessageGroup:
    def test_defaults(self) -> None:
        group = CollapsibleMessageGroup()
        assert group.id.startswith("group-")
        assert group.items == []
        assert group.title == ""
        assert group.is_collapsed is False
        assert group.metadata == {}

    def test_add_item(self) -> None:
        group = CollapsibleMessageGroup()
        item = MagicMock()
        group.add_item(item)
        assert len(group.items) == 1

    def test_remove_item_found(self) -> None:
        group = CollapsibleMessageGroup()
        item = MagicMock()
        item.id = "item1"
        group.add_item(item)
        assert group.remove_item("item1") is True
        assert len(group.items) == 0

    def test_remove_item_not_found(self) -> None:
        group = CollapsibleMessageGroup()
        assert group.remove_item("missing") is False

    def test_get_item_found(self) -> None:
        group = CollapsibleMessageGroup()
        item = MagicMock()
        item.id = "item1"
        group.add_item(item)
        assert group.get_item("item1") is item

    def test_get_item_not_found(self) -> None:
        group = CollapsibleMessageGroup()
        assert group.get_item("missing") is None

    def test_get_items_by_type(self) -> None:
        group = CollapsibleMessageGroup()
        item1 = MagicMock()
        item1.type = "DEBUG"
        item2 = MagicMock()
        item2.type = "USER"
        group.add_item(item1)
        group.add_item(item2)
        results = group.get_items_by_type("DEBUG")
        assert len(results) == 1
        assert results[0] is item1

    def test_expand_all(self) -> None:
        group = CollapsibleMessageGroup()
        item = MagicMock()
        item.expand = MagicMock()
        group.add_item(item)
        group.expand_all()
        assert group.is_collapsed is False
        item.expand.assert_called_once()

    def test_collapse_all(self) -> None:
        group = CollapsibleMessageGroup()
        item = MagicMock()
        item.collapse = MagicMock()
        group.add_item(item)
        group.collapse_all()
        assert group.is_collapsed is True
        item.collapse.assert_called_once()

    def test_expand_by_type(self) -> None:
        group = CollapsibleMessageGroup()
        item1 = MagicMock()
        item1.type = "DEBUG"
        item1.expand = MagicMock()
        item2 = MagicMock()
        item2.type = "USER"
        item2.expand = MagicMock()
        group.add_item(item1)
        group.add_item(item2)
        group.expand_by_type("DEBUG")
        item1.expand.assert_called_once()
        item2.expand.assert_not_called()

    def test_collapse_by_type(self) -> None:
        group = CollapsibleMessageGroup()
        item1 = MagicMock()
        item1.type = "DEBUG"
        item1.collapse = MagicMock()
        item2 = MagicMock()
        item2.type = "USER"
        item2.collapse = MagicMock()
        group.add_item(item1)
        group.add_item(item2)
        group.collapse_by_type("DEBUG")
        item1.collapse.assert_called_once()
        item2.collapse.assert_not_called()

    def test_toggle_from_collapsed(self) -> None:
        group = CollapsibleMessageGroup(is_collapsed=True)
        item = MagicMock()
        item.expand = MagicMock()
        group.add_item(item)
        group.toggle()
        assert group.is_collapsed is False
        item.expand.assert_called_once()

    def test_toggle_from_expanded(self) -> None:
        group = CollapsibleMessageGroup(is_collapsed=False)
        item = MagicMock()
        item.collapse = MagicMock()
        group.add_item(item)
        group.toggle()
        assert group.is_collapsed is True
        item.collapse.assert_called_once()

    def test_clear(self) -> None:
        group = CollapsibleMessageGroup()
        group.add_item(MagicMock())
        group.clear()
        assert len(group.items) == 0

    def test_count(self) -> None:
        group = CollapsibleMessageGroup()
        assert group.count() == 0
        group.add_item(MagicMock())
        assert group.count() == 1

    def test_count_by_type(self) -> None:
        group = CollapsibleMessageGroup()
        item1 = MagicMock()
        item1.type = "DEBUG"
        item2 = MagicMock()
        item2.type = "DEBUG"
        item3 = MagicMock()
        item3.type = "USER"
        group.add_item(item1)
        group.add_item(item2)
        group.add_item(item3)
        assert group.count_by_type("DEBUG") == 2
        assert group.count_by_type("USER") == 1

    def test_to_dict(self) -> None:
        group = CollapsibleMessageGroup(id="g1", title="Test", is_collapsed=True)
        group.add_item(MagicMock())
        d = group.to_dict()
        assert d["id"] == "g1"
        assert d["title"] == "Test"
        assert d["is_collapsed"] is True
        assert d["item_count"] == 1
        assert d["metadata"] == {}
