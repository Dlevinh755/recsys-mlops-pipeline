"""Feast entities — join keys match the column names used throughout the
lakehouse (`product_id`, `user_id`), not generic "item"/"user" ids.
"""

from feast import Entity
from feast.value_type import ValueType

user = Entity(name="user", join_keys=["user_id"], value_type=ValueType.STRING)
product = Entity(name="product", join_keys=["product_id"], value_type=ValueType.STRING)
