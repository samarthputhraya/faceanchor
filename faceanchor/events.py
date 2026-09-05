"""One event stream, consumed by the CLI renderer, the SSE API and tests."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from .canonical import iso

Stage = str  # scan | search | extract | anchor | verify


@dataclass
class StageEvent:
    kind: str                 # stage_start|stage_end|log|candidate|match|record|tx|verified|error
    stage: Stage
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    ts: str = field(default_factory=iso)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


Emitter = Callable[[StageEvent], None]


def null_emitter(_: StageEvent) -> None:
    return None
