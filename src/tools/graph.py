from __future__ import annotations

import html
from pathlib import Path
from textwrap import shorten
from typing import Any, Dict, Final, List, Optional, Set, Tuple, Union

from agents.agent import AgentToolStreamEvent
from agents.items import (
    HandoffCallItem,
    HandoffOutputItem,
    ItemHelpers,
    MessageOutputItem,
    ReasoningItem,
    ToolCallItem,
    ToolCallOutputItem,
)
from agents.stream_events import AgentUpdatedStreamEvent, RunItemStreamEvent, StreamEvent

_NODE_CLASS_BY_COLOR: Final[Dict[str, str]] = {
    "lightblue": "entry",
    "white": "data",
    "lightyellow": "agent",
    "lightgreen": "tool",
    "cornsilk": "message",
    "gainsboro": "reasoning",
}


def _extract_tool_info(item: Any) -> Tuple[str, Optional[str]]:
    """Safely extract tool name and call_id from various item types in openai-agents."""
    raw = getattr(item, "raw_item", item)
    tool_name = None
    call_id = None

    if isinstance(raw, dict):
        tool_name = raw.get("name") or raw.get("tool_name")
        call_id = raw.get("call_id") or raw.get("id")
    else:
        tool_name = getattr(raw, "name", None) or getattr(raw, "tool_name", None)
        call_id = getattr(raw, "call_id", None) or getattr(raw, "id", None)

    if not tool_name:
        tool_name = (
            getattr(item, "tool_name", None)
            or getattr(item, "name", None)
            or getattr(item, "description", None)
            or "tool"
        )
    if not call_id:
        call_id = getattr(item, "call_id", None)

    return str(tool_name), (str(call_id) if call_id else None)


class RuntimeGraphBuilder:
    """Collect orchestration events and emit a Mermaid flowchart."""

    def __init__(self) -> None:
        self._step = 1
        self._query_index = 0
        self._message_index = 0
        self._reasoning_index = 0
        self._nodes: Dict[str, Tuple[str, str, str]] = {}
        self._edges: List[Tuple[str, str, str, str]] = []
        self._tool_calls: Dict[str, str] = {}
        self._linked_agents_from_tools: Set[Tuple[str, str]] = set()
        self._ensure_node("start", "user", "ellipse", "lightblue")

    def record_query(self, query: str, starting_agent: str) -> None:
        self._query_index += 1
        query_id = f"input_query_{self._query_index}"
        self._ensure_node(query_id, self._clip(query), "note", "white")
        self._ensure_agent_node(starting_agent)
        self._add_edge("start", query_id, "input", "solid")
        self._add_edge(query_id, starting_agent, "dispatch", "solid")

    def record_stream_event(
        self,
        event: StreamEvent,
        *,
        nested_tool_name: Optional[str] = None,
    ) -> None:
        if isinstance(event, AgentUpdatedStreamEvent):
            self._ensure_agent_node(event.new_agent.name)
            return

        if not isinstance(event, RunItemStreamEvent):
            return

        item = event.item
        agent = getattr(item, "agent", None)
        agent_name = getattr(agent, "name", "orchestrator") if agent else "orchestrator"
        self._ensure_agent_node(agent_name)

        if isinstance(item, ToolCallItem):
            tool_name, call_id = _extract_tool_info(item)
            self._ensure_tool_node(tool_name)
            if call_id:
                self._tool_calls[call_id] = tool_name
            self._add_edge(agent_name, tool_name, "tool call", "dotted")
            return

        if isinstance(item, ToolCallOutputItem):
            _, call_id = _extract_tool_info(item)
            tool_name = self._tool_calls.get(call_id or "", nested_tool_name or "tool")
            self._ensure_tool_node(tool_name)
            self._add_edge(tool_name, agent_name, "tool output", "dotted")
            return

        if isinstance(item, HandoffCallItem):
            tool_name, _ = _extract_tool_info(item)
            self._ensure_tool_node(tool_name)
            self._add_edge(agent_name, tool_name, "handoff call", "dotted")
            return

        if isinstance(item, HandoffOutputItem):
            source_agent = getattr(item, "source_agent", None)
            target_agent = getattr(item, "target_agent", None)
            src_name = getattr(source_agent, "name", agent_name) if source_agent else agent_name
            tgt_name = getattr(target_agent, "name", "target_agent") if target_agent else "target_agent"
            self._ensure_agent_node(src_name)
            self._ensure_agent_node(tgt_name)
            self._add_edge(src_name, tgt_name, "handoff", "dashed")
            return

        if isinstance(item, MessageOutputItem):
            self._message_index += 1
            message_id = f"message_{self._message_index}"
            label = f"{agent_name} says\\n{self._clip(ItemHelpers.text_message_output(item))}"
            self._ensure_node(message_id, label, "note", "cornsilk")
            self._add_edge(agent_name, message_id, "message", "solid")
            return

        if isinstance(item, ReasoningItem):
            self._reasoning_index += 1
            reasoning_id = f"reasoning_{self._reasoning_index}"
            self._ensure_node(reasoning_id, f"{agent_name} reasoning", "note", "gainsboro")
            self._add_edge(agent_name, reasoning_id, "reasoning", "solid")

    def record_nested_agent_event(self, payload: Union[AgentToolStreamEvent, Dict[str, Any]]) -> None:
        agent = payload.get("agent")
        nested_agent = getattr(agent, "name", "agent") if agent else "agent"
        self._ensure_agent_node(nested_agent)
        tool_call_item = payload.get("tool_call")
        extracted_tool_name: Optional[str] = None
        if tool_call_item is not None:
            extracted_tool_name, _ = _extract_tool_info(tool_call_item)
            self._ensure_tool_node(extracted_tool_name)
            link_key = (extracted_tool_name, nested_agent)
            if link_key not in self._linked_agents_from_tools:
                self._linked_agents_from_tools.add(link_key)
                self._add_edge(extracted_tool_name, nested_agent, "run subagent", "solid")

        event = payload.get("event")
        if event is not None:
            self.record_stream_event(event, nested_tool_name=extracted_tool_name)

    def to_mermaid(self) -> str:
        lines: List[str] = [
            "flowchart TD",
            "    classDef entry fill:#dbeafe,stroke:#1d4ed8,stroke-width:2px,color:#0f172a;",
            "    classDef agent fill:#fef3c7,stroke:#b45309,stroke-width:2px,color:#0f172a;",
            "    classDef tool fill:#dcfce7,stroke:#15803d,stroke-width:2px,color:#0f172a;",
            "    classDef data fill:#ffffff,stroke:#475569,stroke-width:1.5px,color:#0f172a;",
            "    classDef message fill:#fef9c3,stroke:#a16207,stroke-width:1.5px,color:#0f172a;",
            "    classDef reasoning fill:#f1f5f9,stroke:#64748b,stroke-width:1.5px,color:#0f172a;",
            "",
        ]

        for node_id, (label, shape, color) in sorted(self._nodes.items()):
            rendered_label = self._render_node_label(node_id, label, shape)
            lines.append(f"    {rendered_label}")
            class_name = _NODE_CLASS_BY_COLOR.get(color)
            if class_name:
                lines.append(f"    class {node_id} {class_name};")

        lines.append("")
        for source, target, label, style in self._edges:
            arrow = "-.->" if style == "dotted" else ("-->" if style == "solid" else "-.->")
            safe_label = label.replace('"', "'")
            lines.append(f'    {source} {arrow}|"{safe_label}"| {target}')

        return "\n".join(lines)

    def save(self, output_base_path: str | Path) -> Tuple[Path, Path]:
        base_path = Path(output_base_path)
        base_path.parent.mkdir(parents=True, exist_ok=True)
        mermaid_code = self.to_mermaid()
        markdown_path = base_path.with_suffix(".md")
        markdown_path.write_text(f"```mermaid\n{mermaid_code}\n```\n", encoding="utf-8")
        mermaid_path = base_path.with_suffix(".mmd")
        mermaid_path.write_text(mermaid_code + "\n", encoding="utf-8")
        return markdown_path, mermaid_path

    def _ensure_agent_node(self, name: str) -> None:
        self._ensure_node(name, name, "square", "lightyellow")

    def _ensure_tool_node(self, name: str) -> None:
        self._ensure_node(name, name, "diamond", "lightgreen")

    def _ensure_node(self, node_id: str, label: str, shape: str, color: str) -> None:
        if node_id not in self._nodes:
            self._nodes[node_id] = (label, shape, color)

    def _add_edge(self, source: str, target: str, label: str, style: str) -> None:
        edge = (source, target, f"{self._step}. {label}", style)
        self._edges.append(edge)
        self._step += 1

    @staticmethod
    def _clip(value: str, width: int = 40) -> str:
        one_line = " ".join(value.split())
        return html.escape(shorten(one_line, width=width, placeholder="..."))

    @staticmethod
    def _render_node_label(node_id: str, label: str, shape: str) -> str:
        safe_label = label.replace('"', "&quot;")
        if shape == "ellipse":
            return f'{node_id}(["{safe_label}"])'
        if shape == "diamond":
            return f'{node_id}{{"{safe_label}"}}'
        if shape == "note":
            return f'{node_id}[/"{safe_label}"/]'
        return f'{node_id}["{safe_label}"]'
