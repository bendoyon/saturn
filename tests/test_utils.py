from typing import Any

import pytest

from saturn_engine.utils import get_own_attr
from saturn_engine.utils import has_own_attr


def test_get_own_attr() -> None:
    own = object()
    notown = object()
    default = object()

    class A:
        a = notown

    class B:
        __slots__ = ["a"]
        a: Any
        b = notown

    a = A()
    assert get_own_attr(a, "a", default) is default
    assert get_own_attr(a, "b", default) is default
    with pytest.raises(AttributeError):
        assert get_own_attr(a, "a")
    with pytest.raises(AttributeError):
        assert get_own_attr(a, "b")
    assert has_own_attr(a, "a") is False
    assert has_own_attr(a, "b") is False
    a.a = own
    assert get_own_attr(a, "a", default) is own
    assert get_own_attr(a, "a") is own
    assert has_own_attr(a, "a") is True
    assert has_own_attr(a, "b") is False

    b = B()
    assert get_own_attr(b, "a", default) is default
    assert get_own_attr(b, "b", default) is default
    assert get_own_attr(b, "c", default) is default
    with pytest.raises(AttributeError):
        assert get_own_attr(b, "a")
    with pytest.raises(AttributeError):
        assert get_own_attr(b, "b")
    with pytest.raises(AttributeError):
        assert get_own_attr(b, "c")
    assert has_own_attr(b, "a") is False
    assert has_own_attr(b, "b") is False
    assert has_own_attr(b, "c") is False
    b.a = own
    assert get_own_attr(b, "a", default) is own
    assert get_own_attr(b, "a") is own
    assert has_own_attr(b, "a") is True
    assert has_own_attr(b, "b") is False
    assert has_own_attr(b, "c") is False