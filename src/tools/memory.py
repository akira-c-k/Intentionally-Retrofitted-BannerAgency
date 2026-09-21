from __future__ import annotations

import json
from contextvars import ContextVar, Token
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Dict, Literal, Optional

from pydantic import Field
from agents import function_tool

from src.config import BANNER_MEMORY_DIR

_ACTIVE_BANNER_ID: ContextVar[Optional[str]] = ContextVar("active_banner_id", default=None)


def set_active_banner_memory_id(banner_id: str) -> Token[Optional[str]]:
    return _ACTIVE_BANNER_ID.set(banner_id)


def reset_active_banner_memory_id(token: Token[Optional[str]]) -> None:
    _ACTIVE_BANNER_ID.reset(token)


def append_banner_memory_record(
    *,
    banner_id: str,
    iteration: int,
    role: str,
    content: str,
    record_type: Literal["agent_summary", "review_comment", "approval", "exit"],
    memory_dir: Path = BANNER_MEMORY_DIR,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    resolved_banner_id = _ACTIVE_BANNER_ID.get() or banner_id
    memory_dir.mkdir(parents=True, exist_ok=True)
    memory_path = memory_dir / f"{resolved_banner_id}.jsonl"
    created_at = datetime.now().astimezone().isoformat(timespec="seconds")
    record = {
        "created_at": created_at,
        "banner_id": resolved_banner_id,
        "iteration": iteration,
        "role": role,
        "record_type": record_type,
        "content": content,
        "metadata": metadata or {},
    }
    with memory_path.open("a", encoding="utf-8") as memory_file:
        memory_file.write(json.dumps(record, ensure_ascii=False) + "\n")

    return {
        "memory_path": str(memory_path),
        "created_at": created_at,
        "notes": f"Appended banner memory record for {role}.",
    }


@function_tool
def record_banner_memory(
    banner_id: Annotated[str, Field(description="Stable banner run identifier.")],
    iteration: Annotated[int, Field(description="Current banner iteration number.")],
    role: Annotated[str, Field(description="Agent or reviewer role name.")],
    content: Annotated[str, Field(description="Summary or review comment to persist.")],
    record_type: Annotated[
        Literal["agent_summary", "review_comment", "approval", "exit"],
        Field(description="Type of memory record."),
    ],
) -> Dict[str, Any]:
    """Append an agent or reviewer memory record to a banner JSONL log."""
    return append_banner_memory_record(
        banner_id=banner_id,
        iteration=iteration,
        role=role,
        content=content,
        record_type=record_type,
    )
