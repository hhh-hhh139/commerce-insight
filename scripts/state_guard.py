"""Cross-process maintenance lock for the one-instance application state."""
from contextlib import contextmanager
import fcntl
from functools import wraps
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def state_access(*, maintenance=False):
    path = ROOT / ".local/state-control/maintenance.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        try:
            fcntl.flock(stream, (fcntl.LOCK_EX if maintenance else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("application_state_in_use") from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def uses_state(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with state_access():
            return function(*args, **kwargs)
    return wrapped
