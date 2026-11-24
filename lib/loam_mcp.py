"""Lightweight stdio MCP server using only the Python standard library.

Drop-in replacement for ``mcp.server.MCPServer`` for Loam's stdio tool servers:
derives the same JSON schemas from type annotations, speaks JSON-RPC 2.0 over
newline-delimited stdin/stdout, runs concurrent sync and async tool calls, and
honours ``notifications/cancelled`` without importing Pydantic, Starlette,
httpx, or anyio at startup (~7 MB PSS at rest instead of ~50 MB).

``asyncio`` is imported lazily on the first call to an ``async def`` tool so
servers sitting idle after ``initialize`` and ``tools/list`` do not pay for it.
"""

from __future__ import annotations

import inspect
import json
import sys
import threading
import types
import typing
from typing import Any, Callable

DEFAULT_PROTOCOL = "2025-06-18"
_CONTENT_TYPES = frozenset({"text", "image", "audio", "resource", "resource_link"})


def _title(name: str) -> str:
    return name.title().replace("_", " ")


def _type_schema(ann: Any) -> dict[str, Any]:
    if ann is inspect.Parameter.empty or ann is Any:
        return {}
    if ann is type(None):
        return {"type": "null"}
    if ann is str:
        return {"type": "string"}
    if ann is bool:
        return {"type": "boolean"}
    if ann is int:
        return {"type": "integer"}
    if ann is float:
        return {"type": "number"}
    if ann is dict:
        return {"additionalProperties": True, "type": "object"}
    if ann is list:
        return {"items": {}, "type": "array"}

    origin = typing.get_origin(ann)
    args = typing.get_args(ann)

    if origin in (typing.Union, types.UnionType):
        return {"anyOf": [_type_schema(a) for a in args]}
    if origin is dict:
        if len(args) == 2 and args[1] is not Any:
            return {"additionalProperties": _type_schema(args[1]), "type": "object"}
        return {"additionalProperties": True, "type": "object"}
    if origin is list:
        return {"items": _type_schema(args[0]) if args else {}, "type": "array"}
    if origin is typing.Literal:
        return {"enum": list(args)}
    return {}


def _input_schema(fn: Callable[..., Any], hints: dict[str, Any]) -> dict[str, Any]:
    sig = inspect.signature(fn)
    props: dict[str, Any] = {}
    required: list[str] = []
    for pname, param in sig.parameters.items():
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        ann = hints.get(pname, param.annotation)
        prop: dict[str, Any] = {}
        if param.default is not inspect.Parameter.empty:
            prop["default"] = param.default
        else:
            required.append(pname)
        prop["title"] = _title(pname)
        prop.update(_type_schema(ann))
        props[pname] = prop
    schema: dict[str, Any] = {
        "properties": props,
        "type": "object",
        "title": f"{fn.__name__}Arguments",
    }
    if required:
        schema["required"] = required
    return schema


def _output_schema(fn: Callable[..., Any], hints: dict[str, Any]) -> dict[str, Any] | None:
    ret = hints.get("return", inspect.Parameter.empty)
    if ret is inspect.Parameter.empty or ret is Any or ret is type(None):
        return None
    origin = typing.get_origin(ret)
    if ret is dict or origin is dict:
        return {
            "type": "object",
            "additionalProperties": True,
            "title": f"{fn.__name__}DictOutput",
        }
    val_schema = _type_schema(ret)
    if not val_schema:
        return None
    return {
        "properties": {"result": {"title": "Result", **val_schema}},
        "required": ["result"],
        "type": "object",
        "title": f"{fn.__name__}Output",
    }


def _exact_match(value: Any, ann: Any) -> bool:
    if ann is inspect.Parameter.empty or ann is Any:
        return True
    if ann is type(None):
        return value is None
    if ann is bool:
        return isinstance(value, bool)
    if ann is int:
        return isinstance(value, int) and not isinstance(value, bool)
    if ann is float:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if ann is str:
        return isinstance(value, str)
    origin = typing.get_origin(ann)
    if ann is dict or origin is dict:
        return isinstance(value, dict)
    if ann is list or origin is list:
        return isinstance(value, list)
    return False


def _coerce(value: Any, ann: Any, pname: str) -> Any:
    if ann is inspect.Parameter.empty or ann is Any:
        return value
    origin = typing.get_origin(ann)
    args = typing.get_args(ann)

    if origin in (typing.Union, types.UnionType):
        if value is None and type(None) in args:
            return None
        for member in args:
            if member is not type(None) and _exact_match(value, member):
                return _coerce(value, member, pname)
        for member in args:
            if member is type(None):
                continue
            try:
                return _coerce(value, member, pname)
            except ValueError:
                continue
        raise ValueError(f"invalid value for {pname!r}: {value!r}")

    if ann is type(None):
        if value is None:
            return None
        raise ValueError(f"{pname!r} must be null; got {value!r}")

    if ann is str:
        if isinstance(value, str):
            return value
        raise ValueError(f"{pname!r} must be a string; got {type(value).__name__}")

    if ann is bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and value in (0, 1):
            return bool(value)
        if isinstance(value, str):
            low = value.strip().lower()
            if low in ("true", "1", "yes", "on"):
                return True
            if low in ("false", "0", "no", "off"):
                return False
        raise ValueError(f"{pname!r} must be a boolean; got {value!r}")

    if ann is int:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str):
            try:
                return int(value.strip())
            except ValueError:
                pass
        raise ValueError(f"{pname!r} must be an integer; got {value!r}")

    if ann is float:
        if isinstance(value, bool):
            return float(int(value))
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value.strip())
            except ValueError:
                pass
        raise ValueError(f"{pname!r} must be a number; got {value!r}")

    if ann is dict or origin is dict:
        if isinstance(value, dict):
            return value
        raise ValueError(f"{pname!r} must be an object; got {type(value).__name__}")

    if ann is list or origin is list:
        if isinstance(value, list):
            if args:
                return [_coerce(item, args[0], pname) for item in value]
            return value
        raise ValueError(f"{pname!r} must be an array; got {type(value).__name__}")

    if origin is typing.Literal:
        if value in args:
            return value
        raise ValueError(f"{pname!r} must be one of {args!r}; got {value!r}")

    return value


def _format_result(value: Any) -> dict[str, Any]:
    if (
        isinstance(value, list)
        and value
        and all(isinstance(b, dict) and b.get("type") in _CONTENT_TYPES for b in value)
    ):
        return {"content": value, "isError": False}
    if isinstance(value, dict):
        return {
            "content": [{"type": "text", "text": json.dumps(value, indent=2)}],
            "structuredContent": value,
            "isError": False,
        }
    if isinstance(value, str):
        return {
            "content": [{"type": "text", "text": value}],
            "structuredContent": {"result": value},
            "isError": False,
        }
    return {
        "content": [{"type": "text", "text": json.dumps(value, indent=2)}],
        "structuredContent": {"result": value},
        "isError": False,
    }


class _Tool:
    __slots__ = ("name", "fn", "sig", "hints", "is_async", "spec")

    def __init__(
        self,
        name: str,
        fn: Callable[..., Any],
        description: str | None,
        annotations: dict[str, Any] | None,
    ) -> None:
        self.name = name
        self.fn = fn
        self.sig = inspect.signature(fn)
        try:
            self.hints = typing.get_type_hints(fn)
        except Exception:
            self.hints = {}
        self.is_async = inspect.iscoroutinefunction(fn)
        spec: dict[str, Any] = {
            "name": name,
            "description": description if description is not None else (inspect.getdoc(fn) or ""),
            "inputSchema": _input_schema(fn, self.hints),
        }
        out_schema = _output_schema(fn, self.hints)
        if out_schema is not None:
            spec["outputSchema"] = out_schema
        if annotations is not None:
            spec["annotations"] = annotations
        self.spec = spec

    def bind(self, raw_args: Any) -> dict[str, Any]:
        if raw_args is None:
            raw_args = {}
        if not isinstance(raw_args, dict):
            raise ValueError(f"arguments must be an object; got {type(raw_args).__name__}")
        kwargs: dict[str, Any] = {}
        missing: list[str] = []
        for pname, param in self.sig.parameters.items():
            if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
                continue
            if pname in raw_args:
                ann = self.hints.get(pname, param.annotation)
                kwargs[pname] = _coerce(raw_args[pname], ann, pname)
            elif param.default is inspect.Parameter.empty:
                missing.append(pname)
        if missing:
            raise ValueError(f"missing required argument(s): {', '.join(missing)}")
        return kwargs


class MCPServer:
    """Minimal stdio MCP server with the same decorator surface as ``mcp.server.MCPServer``."""

    def __init__(self, name: str, version: str = "") -> None:
        self.name = name
        self.version = version
        self._tools: dict[str, _Tool] = {}
        self._tool_order: list[str] = []
        self._write_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._inflight: dict[Any, Any] = {}
        self._cancelled: set[Any] = set()
        self._loop: Any = None
        self._loop_thread: threading.Thread | None = None

    def tool(
        self,
        name: str | None = None,
        description: str | None = None,
        annotations: dict[str, Any] | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            tname = name or fn.__name__
            entry = _Tool(tname, fn, description, annotations)
            if tname not in self._tools:
                self._tool_order.append(tname)
            self._tools[tname] = entry
            return fn

        return decorator

    def _send(self, frame: dict[str, Any]) -> None:
        line = json.dumps(frame, ensure_ascii=False) + "\n"
        with self._write_lock:
            sys.stdout.write(line)
            sys.stdout.flush()

    def notify_progress(
        self,
        progress_token: str | int,
        progress: float,
        total: float | None = None,
        message: str | None = None,
    ) -> None:
        params: dict[str, Any] = {"progressToken": progress_token, "progress": progress}
        if total is not None:
            params["total"] = total
        if message is not None:
            params["message"] = message
        self._send({"jsonrpc": "2.0", "method": "notifications/progress", "params": params})

    def _ensure_loop(self) -> Any:
        with self._state_lock:
            if self._loop is not None:
                return self._loop
            import asyncio  # noqa: PLC0415  deferred until first async tool call

            loop = asyncio.new_event_loop()
            ready = threading.Event()

            def _runner() -> None:
                asyncio.set_event_loop(loop)
                ready.set()
                loop.run_forever()

            thread = threading.Thread(target=_runner, name="loam-mcp-loop", daemon=True)
            thread.start()
            ready.wait()
            self._loop = loop
            self._loop_thread = thread
            return loop

    def _finish(self, mid: Any, payload: dict[str, Any]) -> None:
        with self._state_lock:
            self._inflight.pop(mid, None)
            if mid in self._cancelled:
                self._cancelled.discard(mid)
                return
        if mid is not None:
            self._send({"jsonrpc": "2.0", "id": mid, **payload})

    def _dispatch_tool_call(self, mid: Any, params: dict[str, Any]) -> None:
        tname = params.get("name")
        tool = self._tools.get(tname) if isinstance(tname, str) else None
        if tool is None:
            if mid is not None:
                self._send({
                    "jsonrpc": "2.0",
                    "id": mid,
                    "result": {
                        "content": [{"type": "text", "text": f"Unknown tool: {tname}"}],
                        "isError": True,
                    },
                })
            return

        try:
            kwargs = tool.bind(params.get("arguments"))
        except ValueError as exc:
            if mid is not None:
                self._send({
                    "jsonrpc": "2.0",
                    "id": mid,
                    "result": {
                        "content": [{"type": "text", "text": f"Error executing tool {tname}: {exc}"}],
                        "isError": True,
                    },
                })
            return

        if tool.is_async:
            import asyncio  # noqa: PLC0415

            loop = self._ensure_loop()

            async def _run_async() -> None:
                try:
                    res = await tool.fn(**kwargs)
                except asyncio.CancelledError:
                    with self._state_lock:
                        self._inflight.pop(mid, None)
                        self._cancelled.discard(mid)
                    return
                except Exception as exc:  # noqa: BLE001
                    self._finish(mid, {
                        "result": {
                            "content": [{"type": "text", "text": f"Error executing tool {tname}: {exc}"}],
                            "isError": True,
                        },
                    })
                    return
                self._finish(mid, {"result": _format_result(res)})

            with self._state_lock:
                fut = asyncio.run_coroutine_threadsafe(_run_async(), loop)
                if mid is not None:
                    self._inflight[mid] = fut
            return

        def _run_sync() -> None:
            try:
                res = tool.fn(**kwargs)
            except Exception as exc:  # noqa: BLE001
                self._finish(mid, {
                    "result": {
                        "content": [{"type": "text", "text": f"Error executing tool {tname}: {exc}"}],
                        "isError": True,
                    },
                })
                return
            self._finish(mid, {"result": _format_result(res)})

        thread = threading.Thread(target=_run_sync, daemon=True)
        with self._state_lock:
            if mid is not None:
                self._inflight[mid] = thread
        thread.start()

    def _handle_message(self, msg: dict[str, Any]) -> None:
        method = msg.get("method")
        mid = msg.get("id")
        params = msg.get("params") or {}

        if method == "initialize":
            version = (params.get("protocolVersion") if isinstance(params, dict) else None) or DEFAULT_PROTOCOL
            self._send({
                "jsonrpc": "2.0",
                "id": mid,
                "result": {
                    "capabilities": {
                        "experimental": {},
                        "prompts": {"listChanged": False},
                        "resources": {"listChanged": False, "subscribe": False},
                        "tools": {"listChanged": False},
                    },
                    "protocolVersion": version,
                    "serverInfo": {"name": self.name, "version": self.version},
                },
            })
            return

        if method == "notifications/initialized":
            return

        if method == "notifications/cancelled":
            req_id = params.get("requestId") if isinstance(params, dict) else None
            if req_id is not None:
                with self._state_lock:
                    handle = self._inflight.get(req_id)
                    if handle is not None:
                        self._cancelled.add(req_id)
                        cancel = getattr(handle, "cancel", None)
                        if callable(cancel):
                            cancel()
            return

        if method == "ping":
            if mid is not None:
                self._send({"jsonrpc": "2.0", "id": mid, "result": {}})
            return

        if method == "tools/list":
            if mid is not None:
                self._send({
                    "jsonrpc": "2.0",
                    "id": mid,
                    "result": {"tools": [self._tools[k].spec for k in self._tool_order]},
                })
            return

        if method == "tools/call":
            self._dispatch_tool_call(mid, params if isinstance(params, dict) else {})
            return

        if method == "prompts/list":
            if mid is not None:
                self._send({"jsonrpc": "2.0", "id": mid, "result": {"prompts": []}})
            return

        if method == "resources/list":
            if mid is not None:
                self._send({"jsonrpc": "2.0", "id": mid, "result": {"resources": []}})
            return

        if method == "resources/templates/list":
            if mid is not None:
                self._send({"jsonrpc": "2.0", "id": mid, "result": {"resourceTemplates": []}})
            return

        if mid is None:
            return

        self._send({
            "jsonrpc": "2.0",
            "id": mid,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        })

    def _shutdown(self) -> None:
        # Wait for in-flight calls started before stdin EOF (supports piped batch tests).
        while True:
            with self._state_lock:
                handles = list(self._inflight.values())
            if not handles:
                break
            for handle in handles:
                if isinstance(handle, threading.Thread):
                    handle.join()
                else:
                    try:
                        handle.result()
                    except Exception:  # noqa: BLE001
                        pass
        loop = self._loop
        thread = self._loop_thread
        if loop is not None:
            loop.call_soon_threadsafe(loop.stop)
            if thread is not None:
                thread.join(timeout=5)
            loop.close()
            self._loop = None
            self._loop_thread = None

    def run(self) -> None:
        try:
            while True:
                line = sys.stdin.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if isinstance(msg, dict):
                    self._handle_message(msg)
        finally:
            self._shutdown()
