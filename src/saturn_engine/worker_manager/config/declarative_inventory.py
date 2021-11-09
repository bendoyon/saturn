import dataclasses
import functools
from typing import Any

import desert
import marshmallow

from saturn_engine.core import api

from .declarative_base import BaseObject


@dataclasses.dataclass
class InventorySpec:
    type: str
    options: dict[str, Any]


@dataclasses.dataclass
class Inventory(BaseObject):
    spec: InventorySpec

    @classmethod
    @functools.cache
    def schema(cls) -> marshmallow.Schema:
        return desert.schema(cls)

    def to_core_object(self) -> api.InventoryItem:
        return api.InventoryItem(
            name=self.metadata.name,
            type=self.spec.type,
            options=self.spec.options,
        )