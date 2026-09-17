"""Minimal row store with two backends: PostgreSQL (SQLAlchemy Core, async) and in-memory (APP_ENV=test).

Every query that returns user-owned rows takes the owner explicitly so ownership is enforced at the data layer,
not in handlers.
"""

from __future__ import annotations

import copy
from typing import Any

from sqlalchemy import Table, and_, delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

Row = dict[str, Any]


class Store:
    def __init__(self, factory: async_sessionmaker[AsyncSession] | None) -> None:
        self.factory = factory
        self._mem: dict[str, dict[Any, Row]] = {}

    # ----------------------------------------------------------------- memory helpers
    def _table(self, t: Table) -> dict[Any, Row]:
        return self._mem.setdefault(t.fullname, {})

    @staticmethod
    def _match(row: Row, where: dict[str, Any]) -> bool:
        return all(row.get(k) == v for k, v in where.items())

    # ----------------------------------------------------------------- API
    async def insert(self, t: Table, row: Row) -> Row:
        if self.factory is None:
            self._table(t)[row["id"]] = copy.deepcopy(row)
            return copy.deepcopy(row)
        async with self.factory() as s:
            await s.execute(insert(t).values(**row))
            await s.commit()
        return row

    async def get(self, t: Table, pk: Any, **where: Any) -> Row | None:
        if self.factory is None:
            row = self._table(t).get(pk)
            return copy.deepcopy(row) if row and self._match(row, where) else None
        async with self.factory() as s:
            conds = [t.c.id == pk, *(t.c[k] == v for k, v in where.items())]
            res = await s.execute(select(t).where(and_(*conds)))
            r = res.mappings().first()
            return dict(r) if r else None

    async def find(
        self, t: Table, *, order_by: str = "created_at", desc: bool = True, limit: int | None = None, **where: Any
    ) -> list[Row]:
        if self.factory is None:
            rows = [copy.deepcopy(r) for r in self._table(t).values() if self._match(r, where)]
            rows.sort(key=lambda r: (r.get(order_by) is None, r.get(order_by)), reverse=desc)
            return rows[:limit] if limit else rows
        async with self.factory() as s:
            stmt = select(t)
            if where:
                stmt = stmt.where(and_(*(t.c[k] == v for k, v in where.items())))
            col = t.c[order_by]
            stmt = stmt.order_by(col.desc() if desc else col.asc())
            if limit:
                stmt = stmt.limit(limit)
            res = await s.execute(stmt)
            return [dict(r) for r in res.mappings().all()]

    async def update(self, t: Table, pk: Any, values: Row, **where: Any) -> Row | None:
        """Conditional update (e.g. optimistic concurrency on ``revision``); returns the new row or None."""
        if self.factory is None:
            row = self._table(t).get(pk)
            if row is None or not self._match(row, where):
                return None
            row.update(copy.deepcopy(values))
            return copy.deepcopy(row)
        async with self.factory() as s:
            conds = [t.c.id == pk, *(t.c[k] == v for k, v in where.items())]
            res = await s.execute(update(t).where(and_(*conds)).values(**values).returning(t))
            r = res.mappings().first()
            await s.commit()
            return dict(r) if r else None

    async def delete(self, t: Table, pk: Any) -> None:
        if self.factory is None:
            self._table(t).pop(pk, None)
            return
        async with self.factory() as s:
            await s.execute(delete(t).where(t.c.id == pk))
            await s.commit()

    async def count(self, t: Table, **where: Any) -> int:
        if self.factory is None:
            return sum(1 for r in self._table(t).values() if self._match(r, where))
        async with self.factory() as s:
            stmt = select(func.count()).select_from(t)
            if where:
                stmt = stmt.where(and_(*(t.c[k] == v for k, v in where.items())))
            return int((await s.execute(stmt)).scalar_one())
