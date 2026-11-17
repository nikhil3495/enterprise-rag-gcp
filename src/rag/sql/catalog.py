"""Table catalog for text-to-SQL.

The catalog the model sees is built from BigQuery table/column descriptions in production (seeded from
``schema.yaml``), or directly from ``schema.yaml`` locally. Enum-like columns list "Allowed values: …"
so the model uses exact literals.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

_ALLOWED_RE = re.compile(r"Allowed values:\s*(.+?)\.?$")


class ColumnSpec(BaseModel):
    name: str
    type: str
    description: str = ""
    allowed_values: list[str] = Field(default_factory=list)

    @property
    def full_description(self) -> str:
        desc = self.description.strip()
        if self.allowed_values:
            desc = f"{desc} Allowed values: {', '.join(self.allowed_values)}."
        return desc.strip()


class TableSpec(BaseModel):
    name: str
    description: str = ""
    columns: list[ColumnSpec] = Field(default_factory=list)


class Catalog(BaseModel):
    dataset: str
    description: str = ""
    tables: list[TableSpec] = Field(default_factory=list)

    @property
    def table_names(self) -> list[str]:
        return [t.name for t in self.tables]

    def table(self, name: str) -> TableSpec | None:
        return next((t for t in self.tables if t.name.lower() == name.lower()), None)

    def summary(self) -> str:
        """One line per table — used by the router."""
        return "\n".join(
            f"- {t.name}: {t.description.strip()} Columns: {', '.join(c.name for c in t.columns)}."
            for t in self.tables
        )

    def detailed(self, project: str) -> str:
        """Full schema with descriptions — used by the SQL drafter."""
        lines = []
        for t in self.tables:
            lines.append(f"TABLE `{project}.{self.dataset}.{t.name}` — {t.description.strip()}")
            for c in t.columns:
                lines.append(f"  - {c.name} {c.type}: {c.full_description}")
        return "\n".join(lines)

    # -------------------------------------------------------------------------------------------
    @classmethod
    def from_yaml(cls, path: str | Path) -> Catalog:
        raw: dict[str, Any] = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        tables = [
            TableSpec(
                name=name,
                description=spec.get("description", ""),
                columns=[ColumnSpec.model_validate(c) for c in spec.get("columns", [])],
            )
            for name, spec in raw["tables"].items()
        ]
        return cls(dataset=raw["dataset"], description=raw.get("description", ""), tables=tables)

    @classmethod
    def from_bigquery(cls, client: Any, project: str, dataset: str) -> Catalog:
        tables = []
        for item in client.list_tables(f"{project}.{dataset}"):
            t = client.get_table(item.reference)
            cols = []
            for f in t.schema:
                desc = f.description or ""
                allowed: list[str] = []
                m = _ALLOWED_RE.search(desc)
                if m:
                    allowed = [v.strip() for v in m.group(1).split(",") if v.strip()]
                    desc = desc[: m.start()].strip()
                cols.append(
                    ColumnSpec(name=f.name, type=f.field_type, description=desc, allowed_values=allowed)
                )
            tables.append(TableSpec(name=t.table_id, description=t.description or "", columns=cols))
        tables.sort(key=lambda t: t.name)
        return cls(dataset=dataset, tables=tables)
