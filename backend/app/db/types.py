"""Shared column type helpers."""

from enum import StrEnum

from sqlalchemy import Enum


def str_enum(enum_cls: type[StrEnum], name: str, length: int = 20) -> Enum:
    """Store a ``StrEnum`` as VARCHAR + CHECK constraint.

    Non-native enums avoid ``ALTER TYPE`` migrations when a value is added; the CHECK constraint
    still rejects unknown values at the database level.
    """
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=length,
        values_callable=lambda e: [m.value for m in e],
    )
