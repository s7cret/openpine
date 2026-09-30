"""One component at a time; multiple shards retain the bounded CPU budget."""
import pytest
from openpine.verification import execution_campaign as owner


def test_empty_component_window():
    assert owner.component_window([], {}) is None


def test_idle_window_selects_first_queued_component():
    queued=[({'component':'a'},{}),({'component':'b'},{}),({'component':'a'}, {})]
    assert owner.component_window(queued, {})=='a'


def test_running_component_owns_window_until_all_its_shards_finish():
    queued=[({'component':'b'},{}),({'component':'a'}, {})]
    running={object():({'component':'a'},{}),object():({'component':'a'}, {})}
    assert owner.component_window(queued,running)=='a'


def test_mixed_running_components_fail_closed():
    running={object():({'component':'a'},{}),object():({'component':'b'}, {})}
    with pytest.raises(ValueError,match='mixed component'):
        owner.component_window([],running)
