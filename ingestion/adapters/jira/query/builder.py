from __future__ import annotations

from typing import Iterable


class JIRAQueryBuilder:
    """Small helper for assembling JQL fragments."""

    @staticmethod
    def quote(value: str) -> str:
        escaped = str(value).replace('"', '\\"')
        return f'"{escaped}"'

    def equals(self, field: str, value: str) -> str:
        return f"{field} = {self.quote(value)}"

    def in_list(self, field: str, values: Iterable[str]) -> str:
        quoted = ", ".join(self.quote(value) for value in values if str(value).strip())
        return f"{field} in ({quoted})" if quoted else ""

    def and_all(self, *clauses: str) -> str:
        active = [clause.strip() for clause in clauses if clause and clause.strip()]
        return " AND ".join(active)

    def or_any(self, *clauses: str) -> str:
        active = [clause.strip() for clause in clauses if clause and clause.strip()]
        return " OR ".join(active)
