from __future__ import annotations

from typing import Any

from resource_engine import ListQuery, Page
from resource_engine.errors import ConditionNotSupported
from sqlalchemy import and_, false, func, literal, not_, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession


class SqlAlchemyRepository[E]:
    """Generic repository for an imperatively mapped domain entity.

    Applies ``ListQuery.condition`` (row-level permissions) as SQL.
    """

    supports_conditions = True

    def __init__(self, session: AsyncSession, entity_cls: type[E], id_field: str = "id") -> None:
        self.session = session
        self.entity_cls = entity_cls
        self.id_field = id_field

    async def get(self, entity_id: Any) -> E | None:
        return await self.session.get(self.entity_cls, entity_id)

    async def list(self, query: ListQuery) -> Page[E]:
        stmt = select(self.entity_cls)
        for field, value in query.filters.items():
            stmt = stmt.where(getattr(self.entity_cls, field) == value)
        if query.condition is not None:
            stmt = stmt.where(compile_condition(query.condition, self.entity_cls))
        total = await self.session.scalar(select(func.count()).select_from(stmt.subquery()))
        for key in query.order_by:
            column = getattr(self.entity_cls, key.lstrip("-"))
            stmt = stmt.order_by(column.desc() if key.startswith("-") else column.asc())
        stmt = stmt.offset(query.offset).limit(query.limit)
        items = list((await self.session.scalars(stmt)).all())
        return Page(items, total or 0, query.offset, query.limit)

    async def add(self, entity: E) -> E:
        self.session.add(entity)
        await self.session.flush()
        return entity

    async def update(self, entity: E) -> E:
        self.session.add(entity)
        await self.session.flush()
        return entity

    async def delete(self, entity: E) -> None:
        await self.session.delete(entity)
        await self.session.flush()


def compile_condition(node: Any, entity_cls: Any) -> Any:
    """Policy condition over ``resource.*`` → SQLAlchemy expression."""
    if node is True:
        return true()
    if node is False:
        return false()
    ((op, args),) = node.items()
    if op == "and":
        return and_(*(compile_condition(c, entity_cls) for c in args))
    if op == "or":
        return or_(*(compile_condition(c, entity_cls) for c in args))
    if op == "not":
        return not_(compile_condition(args, entity_cls))

    def operand(value: Any) -> Any:
        if isinstance(value, dict) and set(value) == {"attr"}:
            namespace, _, name = value["attr"].partition(".")
            column = getattr(entity_cls, name, None) if namespace == "resource" else None
            if column is None or "." in name:
                raise ConditionNotSupported(f"Cannot filter on {value['attr']!r} in SQL")
            return column
        return literal(value)

    if op == "exists":
        return operand(args).is_not(None)
    left, right = args
    if op in ("in", "nin"):
        if not isinstance(right, list):
            raise ConditionNotSupported(f"'{op}' needs a literal list in SQL")
        column = operand(left)
        clause = column.in_(right) if right else false()
        return not_(clause) if op == "nin" else clause
    if op == "contains":
        raise ConditionNotSupported("'contains' on record attributes is not supported in SQL")
    a, b = operand(left), operand(right)
    if op == "eq":
        return a.is_(None) if right is None else a == b
    if op == "ne":
        return a.is_not(None) if right is None else a != b
    return {"lt": a < b, "le": a <= b, "gt": a > b, "ge": a >= b}[op]
