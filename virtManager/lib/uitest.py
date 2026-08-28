# Copyright (C) 2026 virt-manager GTK4/Adwaita port
#
# This work is licensed under the GNU GPLv2 or later.
# See the COPYING file in the top-level directory.

"""
Private location for the AT-SPI ui-test sentinel files.

The GTK 4 port drives (and observes) parts of its dogtail ui tests through
small sentinel files: the test writes ``<dir>/vmm-a11y-delete-open.txt``, a
poller in the app picks it up and opens the Delete dialog, and the app
writes state back out for the test to read.

These used to live at fixed ``/tmp/vmm-a11y-*`` paths. On a multi-user
machine ``/tmp`` is world-writable, so that let *any* local user drive a
running virt-manager: deleting storage volumes, applying XML, starting or
migrating domains, and steering file choosers at arbitrary paths. The
predictable names were also a symlink target for the ``open(path, "w")``
calls scattered through the UI code.

So route every sentinel through this module instead:

* When a ui test asks for the machinery (``VMM_UITEST_DIR`` is exported by
  the test harness, which is the only thing that knows the path) sentinels
  live in that directory, and :func:`enabled` is true so the pollers run.
* Otherwise sentinels resolve into a fresh private per-process directory
  that nothing else can find or write to, :func:`enabled` is false, and the
  pollers that consume them are never registered at all.

Deliberately imports nothing from ``gi`` — the dogtail side of the ui tests
runs under GTK 3 and imports this module too.
"""

import atexit
import os
import shutil
import tempfile

_ENV_DIR = "VMM_UITEST_DIR"

_cached_dir = None


def enabled():
    """Whether a ui test asked for the sentinel machinery to be live."""
    return bool(os.environ.get(_ENV_DIR))


def base_dir():
    """Directory holding the sentinel files. Created 0700 on first use."""
    global _cached_dir
    if _cached_dir is not None:
        return _cached_dir

    envdir = os.environ.get(_ENV_DIR)
    if envdir:
        try:
            os.makedirs(envdir, mode=0o700, exist_ok=True)
        except Exception:  # pragma: no cover
            pass
        _cached_dir = envdir
        return _cached_dir

    # Not under test: give the (harmless) writes somewhere private to land
    # rather than a shared, guessable /tmp path.
    _cached_dir = tempfile.mkdtemp(prefix="virt-manager-")
    atexit.register(shutil.rmtree, _cached_dir, True)
    return _cached_dir


def path(name):
    """Absolute path of the sentinel called ``name``."""
    return os.path.join(base_dir(), name)


def poll_add(interval, callback, *args):
    """``GLib.timeout_add`` for a sentinel poller, skipped when not testing.

    Returns the GLib source id, or 0 when the machinery is disabled and no
    source was registered. Keeping these unregistered matters: the port
    installs well over a hundred of them, most at 50ms, and each tick stats
    or opens files.
    """
    if not enabled():
        return 0
    from gi.repository import GLib

    return GLib.timeout_add(interval, callback, *args)
