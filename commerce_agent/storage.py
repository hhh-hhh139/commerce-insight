"""Project-local atomic journals and a single-writer lock for local checkpoints."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]


class AgentError(Exception):
    pass


def local_path(path):
    path = Path(path).resolve()
    if not path.is_relative_to(ROOT / ".local"):
        raise AgentError("agent_storage_outside_project")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def write_json(path, value):
    path = local_path(path)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".write-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
        os.replace(name, path)
        folder = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(folder)
        finally:
            os.close(folder)
    finally:
        if os.path.exists(name): os.unlink(name)


@contextmanager
def lock(path, blocking=True):
    path = local_path(path)
    with path.open("a+") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            raise AgentError("agent_checkpoint_busy") from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


class Journal:
    def __init__(self, path):
        self.path = local_path(path)

    def read(self):
        return json.loads(self.path.read_text()) if self.path.exists() else {"model_attempts": 0, "tool_attempts": 0, "events": []}

    def reserve(self, kind, limit):
        with lock(str(self.path) + ".lock"):
            state = self.read(); key = kind + "_attempts"
            if state[key] >= limit:
                raise AgentError(kind + "_call_limit")
            state[key] += 1
            state["events"].append({"kind": kind, "number": state[key], "status": "started"})
            write_json(self.path, state)
            return len(state["events"]) - 1

    def finish(self, position, **details):
        with lock(str(self.path) + ".lock"):
            state = self.read()
            state["events"][position].update(status="completed", **details)
            write_json(self.path, state)
