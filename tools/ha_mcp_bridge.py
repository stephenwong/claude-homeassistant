"""Dynamic stdio-to-SSE/Streamable-HTTP bridge for Home Assistant MCP server (ha-mcp).

Enables AI agent harnesses like Antigravity (agy) to communicate with ha-mcp over
stdio while dynamically resolving the MCP URL from .ha-mcp-url or .env.
Supports both Streamable HTTP (MCP 2024-11-05) and classic SSE GET/POST transports.
"""

import asyncio
import codecs
import os
import sys
from collections.abc import Callable, Coroutine, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiohttp

from tools.common import load_env_file


@dataclass(frozen=True)
class SSEEvent:
    """Parsed Server-Sent Event."""

    event: str
    data: str


def parse_sse_lines(lines: Iterable[str]) -> Iterable[SSEEvent]:
    """Parse raw SSE lines into SSEEvent instances."""
    event_type = "message"
    data_lines: list[str] = []

    for raw_line in lines:
        line = raw_line.rstrip("\r\n")
        if not line:
            if data_lines:
                yield SSEEvent(event=event_type, data="\n".join(data_lines))
                data_lines = []
                event_type = "message"
            continue

        if line.startswith(":"):
            # SSE comment / keepalive
            continue

        if ":" in line:
            field, _, val = line.partition(":")
            val = val.removeprefix(" ")
            if field == "event":
                event_type = val
            elif field == "data":
                data_lines.append(val)
        else:
            if line == "data":
                data_lines.append("")

    if data_lines:
        yield SSEEvent(event=event_type, data="\n".join(data_lines))


def resolve_mcp_url(repo_root: Path | None = None) -> str | None:
    """Resolve the ha-mcp URL from .ha-mcp-url or .env (fallback)."""
    root = repo_root or Path(__file__).resolve().parent.parent

    # 1. Primary: .ha-mcp-url file
    url_file = root / ".ha-mcp-url"
    if url_file.exists():
        try:
            content = url_file.read_text(encoding="utf-8").strip()
            if content:
                if content.startswith(("http://", "https://")):
                    return content
                sys.stderr.write(f"Error: Invalid MCP URL in {url_file}: {content}\n")
                return None
        except OSError as e:
            sys.stderr.write(f"Warning: Could not read {url_file}: {e}\n")

    # 2. Fallback: .env or os.environ
    load_env_file(root / ".env")
    env_url = os.environ.get("HA_MCP_URL", "").strip()
    if env_url:
        if env_url.startswith(("http://", "https://")):
            return env_url
        sys.stderr.write(f"Error: Invalid MCP URL in HA_MCP_URL: {env_url}\n")
        return None

    sys.stderr.write(
        "Error: No HA MCP URL found. Set HA_MCP_URL in .env or put "
        "the URL in .ha-mcp-url\n"
    )
    return None


async def run_bridge(
    mcp_url: str,
    stdin_reader: Callable[[], Coroutine[Any, Any, str]] | None = None,
) -> int:
    """Run the bidirectional stdio <-> MCP bridge."""
    stop_event = asyncio.Event()
    session_id: list[str] = []

    async def default_read_line() -> str:
        return await asyncio.to_thread(sys.stdin.readline)

    reader_fn: Callable[[], Coroutine[Any, Any, str]] = (
        stdin_reader or default_read_line
    )

    async def handle_post_response(response: aiohttp.ClientResponse) -> None:
        if "mcp-session-id" in response.headers:
            session_id.clear()
            session_id.append(response.headers["mcp-session-id"])

        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            decoder = codecs.getincrementaldecoder("utf-8")("replace")
            buffer = ""
            lines_to_parse: list[str] = []

            async for chunk in response.content:
                buffer += decoder.decode(chunk)
                while "\n" in buffer:
                    raw_line, buffer = buffer.split("\n", 1)
                    lines_to_parse.append(raw_line)

            final_text = decoder.decode(b"", final=True)
            if final_text:
                buffer += final_text
            if buffer:
                lines_to_parse.append(buffer)

            for event in parse_sse_lines(lines_to_parse):
                if event.event == "message":
                    sys.stdout.write(event.data + "\n")
                    sys.stdout.flush()
        else:
            text = await response.text()
            if text.strip():
                sys.stdout.write(text.strip() + "\n")
                sys.stdout.flush()

    async def bridge_loop(session: aiohttp.ClientSession) -> None:
        try:
            while not stop_event.is_set():
                read_task: asyncio.Task[str] = asyncio.create_task(reader_fn())
                stop_waiter = asyncio.create_task(stop_event.wait())
                tasks: list[asyncio.Task[Any]] = [read_task, stop_waiter]
                _done, pending = await asyncio.wait(
                    tasks, return_when=asyncio.FIRST_COMPLETED
                )
                for pending_task in pending:
                    pending_task.cancel()

                if stop_event.is_set() and not read_task.done():
                    read_task.cancel()
                    break

                line = read_task.result()
                if not line:
                    # EOF reached on stdin
                    break

                line_str = line.strip()
                if not line_str:
                    continue

                headers = {
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                }
                if session_id:
                    headers["mcp-session-id"] = session_id[0]

                try:
                    async with session.post(
                        mcp_url, data=line_str, headers=headers
                    ) as post_res:
                        if post_res.status >= 400:
                            sys.stderr.write(
                                f"Warning: POST to {mcp_url} returned status "
                                f"{post_res.status}\n"
                            )
                        await handle_post_response(post_res)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    sys.stderr.write(f"Error sending message to MCP server: {e}\n")
        finally:
            stop_event.set()

    try:
        timeout = aiohttp.ClientTimeout(total=None, connect=10.0)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            bridge_task = asyncio.create_task(bridge_loop(session))
            await stop_event.wait()
            bridge_task.cancel()
            await asyncio.gather(bridge_task, return_exceptions=True)
            return 0
    except asyncio.CancelledError:
        return 0
    except Exception as e:
        sys.stderr.write(f"Error in bridge execution: {e}\n")
        return 1


def main() -> int:
    """CLI entry point for ha_mcp_bridge."""
    url = resolve_mcp_url()
    if not url:
        return 1

    try:
        return asyncio.run(run_bridge(url))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
