"""Grounding support for insight generation against the NAARI evidence library.

The library lives in ``settings.NAARI_DATA_SOURCE_DIR`` (default:
``<repo>/NAARI_Data_Source_URLS``) and has two navigation layers:

- ``index.md`` — the main index: a register mapping every source document
  (NFHS-5, PLFS, Time Use Survey, WEAI, …) to its converted ``.txt`` file and
  the NAARI indicators it covers. Injected verbatim into the insights system
  prompt so the model knows what evidence exists and which file holds it.
- ``_sections/<file>.txt.md`` — a per-file index: an AI-generated section map
  (line ranges, key statistics with line numbers, indicator relevance) so the
  model can jump straight to the right lines of a multi-megabyte report.

The model works Claude Code-style with three tools: ``search_data_sources``
(grep), ``read_data_source`` (read a line range) and ``list_sections``
(fetch the per-file section map).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional, Union

from django.conf import settings

INDEX_FILENAME = "index.md"
SECTIONS_DIRNAME = "_sections"

# Hard ceiling on tool round-trips per insight so the model can't spiral
# into reading a 13 MB survey report end to end.
MAX_TOOL_ROUNDS = 8

# Output caps so each tool result stays a few thousand tokens at most.
MAX_SEARCH_RESULTS = 40
MAX_SEARCH_CHARS = 10_000
MAX_READ_LINES = 300
MAX_LINE_CHARS = 240

GROUNDING_TOOLS = [
    {
        "name": "search_data_sources",
        "description": (
            "Grep the NAARI evidence library (the .txt files catalogued in the "
            "NAARI Data Source Library index in the system prompt). Returns "
            "matching lines as 'filename:line_number: text'. Case-insensitive; "
            "the query is a regular expression, so short specific patterns "
            "work best (e.g. 'bank account', 'unpaid care', 'Jharkhand.*land'). "
            "Optionally restrict the search to a single file."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Keyword or regex to search for.",
                },
                "filename": {
                    "type": "string",
                    "description": (
                        "Optional exact .txt filename from the index to search "
                        "in; omit to search the whole library."
                    ),
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "read_data_source",
        "description": (
            "Read a line range from one .txt file in the NAARI evidence "
            "library, like opening the document at a specific place. Use it "
            "after list_sections or search_data_sources to read the context "
            "around a hit before citing it. Returns numbered lines."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Exact .txt filename as listed in the index.",
                },
                "start_line": {
                    "type": "integer",
                    "description": "1-based line to start reading from (default 1).",
                },
                "num_lines": {
                    "type": "integer",
                    "description": f"Lines to read (default 120, max {MAX_READ_LINES}).",
                },
            },
            "required": ["filename"],
        },
    },
    {
        "name": "list_sections",
        "description": (
            "Fetch the per-file index (section map) of one .txt file in the "
            "NAARI evidence library: an AI-generated table of contents with "
            "line ranges, the key statistics each section contains (with line "
            "numbers), and which NAARI indicators it serves. Call this FIRST "
            "for any large file to decide which lines to read."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Exact .txt filename as listed in the index.",
                },
            },
            "required": ["filename"],
        },
    },
]

# Prepended to index.md in the grounding system-prompt block. All
# grounding-specific instructions live here so the base SYSTEM_PROMPT stays
# valid when the library is absent or disabled.
GROUNDING_GUIDE = (
    "NAARI EVIDENCE LIBRARY:\n"
    "You have direct tool access to the NAARI evidence library — the converted "
    "source documents catalogued in the main index reproduced below. Every "
    "file also has a per-file index (section map with line ranges and key "
    "statistics) available via the list_sections tool. Work like a researcher "
    "before you write:\n"
    "1. From the main index below, pick the 1-3 sources most relevant to the "
    "indicator and the hotspot states in the DATA CONTEXT.\n"
    "2. Call list_sections on each chosen file to see where its content "
    "lives, and/or search_data_sources to grep for your topic (e.g. 'bank "
    "account', 'land ownership', 'Rajasthan').\n"
    "3. Call read_data_source to read the exact lines around a hit before "
    "citing anything from it.\n"
    "4. Ground the briefing in what you actually read. Prefer library "
    "evidence over web search; use web search only for gaps the library "
    "cannot answer.\n\n"
    "CITATIONS: every statistic or claim taken from the library MUST carry a "
    "compact inline citation naming the source document and year as it "
    "appears in the main index — e.g. (NFHS-5, 2019-21), (MoSPI Time Use "
    "Survey, 2024), (NABARD SOMFI, 2023-24), (TCI-BIRD FPO Report, 2026). "
    "Cite only sources you actually read via the tools or web search this "
    "turn; never cite from memory. Skip files the index flags as needing OCR "
    "— they contain almost no text.\n\n"
    "--- NAARI DATA SOURCE LIBRARY INDEX (index.md) ---\n\n"
)

_index_cache: dict = {"mtime": None, "text": None}


def data_source_dir() -> Path:
    return Path(getattr(settings, "NAARI_DATA_SOURCE_DIR", "")).expanduser()


def sections_dir() -> Path:
    return data_source_dir() / SECTIONS_DIRNAME


def section_map_path(filename: str) -> Path:
    return sections_dir() / f"{filename}.md"


def grounding_enabled() -> bool:
    return bool(getattr(settings, "NAARI_GROUNDING_ENABLED", True)) and (
        data_source_dir() / INDEX_FILENAME
    ).is_file()


def load_index_markdown() -> Optional[str]:
    """Return the contents of index.md, cached until the file's mtime changes."""
    path = data_source_dir() / INDEX_FILENAME
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    if _index_cache["mtime"] != mtime:
        try:
            _index_cache["text"] = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        _index_cache["mtime"] = mtime
    return _index_cache["text"]


def build_system_block() -> Optional[str]:
    """The grounding system-prompt block: guide + full index.md, or None."""
    index_md = load_index_markdown()
    if not index_md:
        return None
    return GROUNDING_GUIDE + index_md


# ----------------------------------------------------------------------
# Tool execution
# ----------------------------------------------------------------------

def execute_grounding_tool(name: str, tool_input: Optional[dict]) -> str:
    """Run one grounding tool call and return its result as a string.

    Never raises: bad input comes back as an ERROR string the model can
    recover from on the next round.
    """
    tool_input = tool_input or {}
    if name == "search_data_sources":
        return _search(tool_input)
    if name == "read_data_source":
        return _read(tool_input)
    if name == "list_sections":
        return _list_sections(tool_input)
    return f"ERROR: unknown tool '{name}'."


def _resolve_file(raw_filename) -> Union[Path, str]:
    """Map a tool-supplied filename onto a real library file, or an error string."""
    filename = os.path.basename(str(raw_filename or "").strip())
    if not filename or not filename.endswith(".txt"):
        return "ERROR: `filename` must be one of the .txt files listed in the index."
    path = data_source_dir() / filename
    if not path.is_file():
        return (
            f"ERROR: '{filename}' not found in the evidence library. Use the "
            "exact filename from the index, including the .txt extension."
        )
    return path


def _search(tool_input: dict) -> str:
    query = str(tool_input.get("query") or "").strip()
    if not query:
        return "ERROR: `query` is required."
    try:
        pattern = re.compile(query, re.IGNORECASE)
    except re.error:
        pattern = re.compile(re.escape(query), re.IGNORECASE)

    if tool_input.get("filename"):
        resolved = _resolve_file(tool_input["filename"])
        if isinstance(resolved, str):
            return resolved
        paths = [resolved]
    else:
        paths = sorted(data_source_dir().glob("*.txt"))
        if not paths:
            return "ERROR: the evidence library folder contains no .txt files."

    results: list[str] = []
    total_chars = 0
    truncated = False
    for path in paths:
        try:
            with path.open(encoding="utf-8", errors="replace") as fh:
                for lineno, line in enumerate(fh, start=1):
                    if not pattern.search(line):
                        continue
                    snippet = line.strip()
                    if len(snippet) > MAX_LINE_CHARS:
                        snippet = snippet[:MAX_LINE_CHARS] + "…"
                    entry = f"{path.name}:{lineno}: {snippet}"
                    results.append(entry)
                    total_chars += len(entry)
                    if (
                        len(results) >= MAX_SEARCH_RESULTS
                        or total_chars >= MAX_SEARCH_CHARS
                    ):
                        truncated = True
                        break
        except OSError as exc:
            results.append(f"{path.name}: ERROR reading file: {exc}")
        if truncated:
            break

    if not results:
        return (
            f"No matches for /{query}/ — try a shorter or alternative keyword, "
            "or list_sections on a likely file."
        )
    header = f"{len(results)} match(es) for /{query}/"
    if truncated:
        header += " (truncated — narrow the query or restrict to one filename)"
    return header + ":\n" + "\n".join(results)


def _read(tool_input: dict) -> str:
    resolved = _resolve_file(tool_input.get("filename"))
    if isinstance(resolved, str):
        return resolved
    path = resolved

    def _as_int(value, default):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    start = max(1, _as_int(tool_input.get("start_line"), 1))
    num = min(MAX_READ_LINES, max(1, _as_int(tool_input.get("num_lines"), 120)))

    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return f"ERROR: could not read '{path.name}': {exc}"

    total = len(lines)
    if start > total:
        return (
            f"ERROR: start_line {start} is past the end of '{path.name}' "
            f"({total} lines)."
        )

    end = min(total, start + num - 1)
    out = []
    for i in range(start, end + 1):
        text = lines[i - 1]
        if len(text) > MAX_LINE_CHARS:
            text = text[:MAX_LINE_CHARS] + "…"
        out.append(f"{i}| {text}")
    header = f"[{path.name} — lines {start}-{end} of {total}]\n"
    return header + "\n".join(out)


def _list_sections(tool_input: dict) -> str:
    resolved = _resolve_file(tool_input.get("filename"))
    if isinstance(resolved, str):
        return resolved
    path = resolved

    map_path = section_map_path(path.name)
    if not map_path.is_file():
        return (
            f"No section map exists for '{path.name}'. Use search_data_sources "
            "to grep it, or read_data_source from line 1 (small files can be "
            "read directly)."
        )
    try:
        return map_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"ERROR: could not read the section map for '{path.name}': {exc}"
