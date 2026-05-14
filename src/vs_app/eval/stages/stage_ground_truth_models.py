from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class StageGroundTruthStage:
    stage_issue_key: str
    stage_summary: str
    stage_name_raw: str
    stage_name_canonical: str
    stage_id: str | None = None
    status: str | None = None
    resolution: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class StageGroundTruthValueStream:
    theme_issue_key: str
    theme_summary: str
    value_stream_name_raw: str
    value_stream_name_canonical: str
    value_stream_id: str | None
    stage_scope: str = "specific_stages"
    stages: list[StageGroundTruthStage] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["stages"] = [stage.to_dict() for stage in self.stages]
        return data


@dataclass(slots=True)
class StageGroundTruthTicket:
    ticket_id: str
    ticket_summary: str
    source: str = "jira_api"
    value_streams: list[StageGroundTruthValueStream] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["value_streams"] = [value_stream.to_dict() for value_stream in self.value_streams]
        return data


__all__ = [
    "StageGroundTruthStage",
    "StageGroundTruthTicket",
    "StageGroundTruthValueStream",
]
