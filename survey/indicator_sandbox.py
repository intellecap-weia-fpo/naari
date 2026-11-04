"""Restricted execution for admin-authored indicator scripts.

Indicator scripts are stored in the database and edited by survey managers, so
they must not be able to reach the operating system, read other data or change
anything. Three layers:

1. Scripts never see live objects. ``snapshot_submission`` copies the answers
   into small read-only views (``AnswerView``, ``OptionView``) that mimic the
   bits of the ORM the existing scripts use (``answer.selected_options.all()``,
   ``.exists()``, ``option.text``, ``answer.text_answer``, ``answer.question_id``,
   ``submission.answers.all()``). Helper modules are namespaces of selected
   functions, not module objects (``json.codecs`` leads to ``builtins``).
2. A small builtin allow-list (no ``__import__``, ``open``, ``eval``, ``getattr``).
3. An AST check before running: no imports, classes, generators, async code,
   names or attributes starting with ``_``, frame/traceback attributes
   (``gi_frame``, ``f_back``...), or strings containing ``__``.

4. ``run_isolated`` (what the app uses) executes each script in a forked child
   process with CPU, memory and wall-clock limits, so a script that swallows the
   line-budget error, runs one huge C-level operation (``sum(range(10**10))``,
   catastrophic regex) or allocates gigabytes is killed without hanging the
   worker, and anything it changes dies with the child. Only the JSON-encoded
   ``result`` and printed output come back.
"""
import ast
import builtins
import datetime
import io
import json
import math
import os
import re
import select
import signal
import sys
import time
from contextlib import redirect_stdout
from types import SimpleNamespace

SAFE_BUILTIN_NAMES = (
    "abs all any bool dict divmod enumerate filter float frozenset hasattr int isinstance "
    "len list map max min pow print range reversed round set sorted str sum tuple zip "
    "Exception ValueError TypeError KeyError IndexError ZeroDivisionError AttributeError"
).split()
SAFE_BUILTINS = {name: getattr(builtins, name) for name in SAFE_BUILTIN_NAMES}

SAFE_MODULES = {
    "math": SimpleNamespace(**{k: getattr(math, k) for k in dir(math) if not k.startswith("_")}),
    "json": SimpleNamespace(loads=json.loads, dumps=json.dumps),
    "re": SimpleNamespace(
        search=re.search, match=re.match, fullmatch=re.fullmatch, findall=re.findall,
        sub=re.sub, split=re.split, compile=re.compile, escape=re.escape, I=re.I, IGNORECASE=re.I,
    ),
    "datetime": SimpleNamespace(
        datetime=datetime.datetime, date=datetime.date, time=datetime.time, timedelta=datetime.timedelta,
    ),
}

FORBIDDEN_ATTRIBUTES = {"format", "format_map", "mro"}
FORBIDDEN_ATTRIBUTE_PREFIXES = ("gi_", "cr_", "ag_", "f_", "tb_", "co_")
FORBIDDEN_NODES = (
    ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal, ast.ClassDef,
    ast.Yield, ast.YieldFrom, ast.Await, ast.AsyncFunctionDef, ast.AsyncFor, ast.AsyncWith,
)
LINE_BUDGET = 2_000_000
SCRIPT_FILENAME = "<indicator script>"
TIMEOUT_SECONDS = 5
MEMORY_HEADROOM_BYTES = 256 * 1024 * 1024
MAX_OUTPUT_BYTES = 1024 * 1024


class UnsafeScriptError(ValueError):
    pass


class OptionView:
    __slots__ = ("id", "text", "value")

    def __init__(self, option):
        self.id, self.text, self.value = option.pk, option.text, option.value


class ViewList:
    """Read-only stand-in for a related manager / queryset."""

    __slots__ = ("items",)

    def __init__(self, items):
        self.items = tuple(items)

    def all(self):
        return list(self.items)

    def exists(self):
        return bool(self.items)

    def first(self):
        return self.items[0] if self.items else None

    def count(self):
        return len(self.items)

    def __iter__(self):
        return iter(self.items)

    def __len__(self):
        return len(self.items)


class AnswerView:
    __slots__ = ("id", "question_id", "text_answer", "other_text", "selected_options")

    def __init__(self, answer):
        self.id, self.question_id = answer.pk, answer.question_id
        self.text_answer, self.other_text = answer.text_answer, answer.other_text
        self.selected_options = ViewList(OptionView(o) for o in answer.selected_options.all())


class SubmissionView:
    __slots__ = ("id", "survey_id", "status", "submitted_at", "language", "answers")

    def __init__(self, submission, answers):
        self.id, self.survey_id, self.status = submission.pk, submission.survey_id, submission.status
        self.submitted_at, self.language = submission.submitted_at, submission.language
        self.answers = ViewList(answers)


class SurveyView:
    __slots__ = ("id", "title")

    def __init__(self, survey):
        self.id, self.title = survey.pk, survey.title


def snapshot_submission(submission):
    """Plain read-only variables for a script: ``submission``, ``survey`` and ``answers``."""
    answers = [AnswerView(a) for a in submission.answers.prefetch_related("selected_options")]
    return {
        "submission": SubmissionView(submission, answers),
        "survey": SurveyView(submission.survey),
        "answers": {a.question_id: a for a in answers},
    }


def validate(code):
    """Raise UnsafeScriptError if the script uses anything outside the sandbox."""
    tree = ast.parse(code, SCRIPT_FILENAME, "exec")
    for node in ast.walk(tree):
        if isinstance(node, FORBIDDEN_NODES):
            raise UnsafeScriptError(f"{type(node).__name__} is not allowed in indicator scripts.")
        if isinstance(node, ast.Name) and node.id.startswith("_"):
            raise UnsafeScriptError(f"Names starting with '_' are not allowed: {node.id}")
        if isinstance(node, ast.Attribute):
            if not isinstance(node.ctx, ast.Load):
                raise UnsafeScriptError("Setting or deleting attributes is not allowed in indicator scripts.")
            if node.attr.startswith("_") or node.attr.startswith(FORBIDDEN_ATTRIBUTE_PREFIXES):
                raise UnsafeScriptError(f"'.{node.attr}' is not allowed in indicator scripts.")
            if node.attr in FORBIDDEN_ATTRIBUTES:
                raise UnsafeScriptError(f"'.{node.attr}' is not allowed in indicator scripts.")
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and "__" in node.value:
            raise UnsafeScriptError("Strings containing '__' are not allowed.")
    return tree


def run(code, variables):
    """Validate and execute ``code`` with plain ``variables``; return the namespace afterwards."""
    tree = validate(code)
    fresh_modules = {name: SimpleNamespace(**vars(module)) for name, module in SAFE_MODULES.items()}
    namespace = {"__builtins__": dict(SAFE_BUILTINS), **fresh_modules, **variables}
    lines = 0

    def budget(frame, event, arg):
        nonlocal lines
        if frame.f_code.co_filename != SCRIPT_FILENAME:
            return None
        if event == "line":
            lines += 1
            if lines > LINE_BUDGET:
                raise UnsafeScriptError("Indicator script ran too long.")
        return budget

    previous = sys.gettrace()
    sys.settrace(budget)
    try:
        exec(compile(tree, SCRIPT_FILENAME, "exec"), namespace)
    finally:
        sys.settrace(previous)
    return namespace


class ScriptFailed(Exception):
    pass


def _limit_child_resources():
    try:
        import resource
    except ImportError:  # pragma: no cover - non-POSIX
        return
    limits = [(resource.RLIMIT_CPU, (TIMEOUT_SECONDS, TIMEOUT_SECONDS + 1))]
    try:
        with open("/proc/self/statm") as statm:
            current = int(statm.read().split()[0]) * os.sysconf("SC_PAGE_SIZE")
        limits.append((resource.RLIMIT_AS, (current + MEMORY_HEADROOM_BYTES,) * 2))
    except OSError:
        pass  # no /proc (macOS): the wall-clock timeout still applies
    for which, value in limits:
        try:
            resource.setrlimit(which, value)
        except (ValueError, OSError):
            pass


def _max_fd():
    try:
        return min(os.sysconf("SC_OPEN_MAX"), 1 << 20)
    except (ValueError, OSError):
        return 4096


def run_isolated(code, variables, timeout=TIMEOUT_SECONDS):
    """Run a script in a forked child with resource limits; return ``(result, printed_output)``.

    ``variables`` must be plain data (see ``snapshot_submission``); the child never
    touches the database. Raises ``ScriptFailed`` with a readable message.
    """
    try:
        validate(code)
    except SyntaxError as e:
        raise ScriptFailed(f"SyntaxError: {e}") from None
    except UnsafeScriptError as e:
        raise ScriptFailed(str(e)) from None

    read_fd, write_fd = os.pipe()
    parent_pid = os.getpid()
    try:
        pid = os.fork()
    except BaseException as e:
        # A signal handler can raise inside the child as fork() returns; never let it unwind into Django.
        if os.getpid() != parent_pid:
            os._exit(0)
        os.close(read_fd)
        os.close(write_fd)
        if isinstance(e, OSError):
            raise ScriptFailed("Could not start the indicator script.") from None
        raise
    if pid == 0:  # child: never return into Django, whatever happens
        try:
            # Drop inherited descriptors (DB, HTTP and listening sockets) except the pipe.
            os.closerange(3, write_fd)
            os.closerange(write_fd + 1, _max_fd())
            _limit_child_resources()
            output = io.StringIO()
            try:
                with redirect_stdout(output):
                    namespace = run(code, variables)
                data = json.dumps(
                    {"ok": True, "result": namespace.get("result"), "output": output.getvalue()}, default=str
                )
            except BaseException as e:  # noqa: BLE001 - report everything to the parent
                try:
                    message = f"{type(e).__name__}: {e}"[:2000]
                except BaseException:  # noqa: BLE001 - str(e) itself can fail
                    message = type(e).__name__
                data = json.dumps({"ok": False, "error": message})
            os.write(write_fd, data.encode()[:MAX_OUTPUT_BYTES])
        finally:
            os._exit(0)

    os.close(write_fd)
    chunks, size, finished = [], 0, False
    deadline = time.monotonic() + timeout + 1
    poller = select.poll()
    poller.register(read_fd, select.POLLIN)
    try:
        while size <= MAX_OUTPUT_BYTES:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not poller.poll(remaining * 1000):
                break
            chunk = os.read(read_fd, 65536)
            if not chunk:
                finished = True
                break
            chunks.append(chunk)
            size += len(chunk)
    finally:
        os.close(read_fd)
        if not finished:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        os.waitpid(pid, 0)

    if not finished:
        raise ScriptFailed("Indicator script took too long and was stopped.")
    if not chunks:
        raise ScriptFailed("Indicator script was stopped (CPU or memory limit).")
    try:
        payload = json.loads(b"".join(chunks))
    except ValueError:
        raise ScriptFailed("Indicator script produced too much output.") from None
    if not payload.get("ok"):
        raise ScriptFailed(payload.get("error", "Indicator script failed."))
    return payload.get("result"), payload.get("output", "")
