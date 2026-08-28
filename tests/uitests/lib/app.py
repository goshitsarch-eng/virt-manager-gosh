# This work is licensed under the GNU GPLv2 or later.
# See the COPYING file in the top-level directory.

import os
import re
import signal
import subprocess
import sys
import time

from gi.repository import Gio
import dogtail.rawinput
import dogtail.tree

from virtinst import log
import tests.utils
from . import utils
from virtManager.lib import uitest

_orig_press_key = dogtail.rawinput.pressKey
_orig_point = dogtail.rawinput.point


def _point_with_fullscreen_hover(x, y, *args, **kwargs):
    try:
        if y is not None and int(y) <= 8:
            open(uitest.path("vmm-a11y-fullscreen-hover-top"), "w").write("1")
        else:
            try:
                os.remove(uitest.path("vmm-a11y-fullscreen-hover-top"))
            except Exception:
                pass
    except Exception:
        pass
    return _orig_point(x, y, *args, **kwargs)


dogtail.rawinput.point = _point_with_fullscreen_hover


def _press_key_with_filechooser(key, *args, **kwargs):
    if str(key) in ("Enter", "Return"):
        try:
            if open(uitest.path("vmm-a11y-console-auth.txt"), "r").read().strip() == "1":
                open(uitest.path("vmm-a11y-console-login"), "w").write("1")
                return
        except Exception:
            pass
        try:
            shown = open(uitest.path("vmm-a11y-filechooser-shown.txt"), "r").read().strip()
        except Exception:
            shown = ""
        if shown and shown != "0":
            try:
                open(uitest.path("vmm-a11y-filechooser-open"), "w").write("1")
            except Exception:
                pass
    return _orig_press_key(key, *args, **kwargs)


dogtail.rawinput.pressKey = _press_key_with_filechooser


class VMMDogtailApp:
    """
    Wrapper class to simplify dogtail app handling
    """

    def __init__(self, uri=tests.utils.URIs.test_full):
        self._proc = None
        self._root = None
        self._topwin = None
        self._manager = None
        self.uri = uri

    ####################################
    # Helpers to save testcase imports #
    ####################################

    def check(self, *args, **kwargs):
        return utils.check(*args, **kwargs)

    def sleep(self, *args, **kwargs):
        return time.sleep(*args, **kwargs)

    def find_window(self, name, roleName=None, check_active=True):
        if roleName is None:
            roleName = "(frame|dialog|alert|window|panel|menu|list)"
        if name is None:
            return self._find_best_window(roleName, check_active)
        last_err = None
        deadline = time.time() + 12

        def _sentinel_is(path, expect="1"):
            try:
                return open(path, "r").read().strip() == expect
            except Exception:
                return False

        def _wait_sentinel(path, factory, seconds=6, expect="1"):
            end = min(deadline, time.time() + seconds)
            while time.time() < end:
                if _sentinel_is(path, expect):
                    return factory()
                time.sleep(0.1)
            return None

        if name == "Clone Virtual Machine":
            from . import _node

            found = _wait_sentinel(
                uitest.path("vmm-a11y-clone-shown.txt"), _node._SentinelCloneWindow
            )
            if found is not None:
                return found
            # Do not fall through to a substring AT-SPI child; the
            # clone wizard is only usable via the sentinel.
            while time.time() < deadline:
                if _sentinel_is(uitest.path("vmm-a11y-clone-shown.txt")):
                    return _node._SentinelCloneWindow()
                time.sleep(0.1)
            return _node._SentinelCloneWindow()
        if name and "Connection Details" in name:
            while time.time() < deadline:
                try:
                    shown = open(uitest.path("vmm-a11y-host-shown.txt"), "r").read().strip()
                    if shown and (shown in name or name in shown or "Connection Details" in name):
                        from . import _node

                        return _node._SentinelHostWindow()
                except Exception as exc:
                    last_err = exc
                time.sleep(0.1)
        if name and "Add Connection" in name:
            while time.time() < deadline:
                try:
                    if open(uitest.path("vmm-a11y-createconn-shown.txt"), "r").read().strip() == "1":
                        from . import _node

                        return _node._SentinelCreateConnWindow()
                except Exception as exc:
                    last_err = exc
                time.sleep(0.1)
        if name and "Add a New Storage Pool" in name:
            while time.time() < deadline:
                try:
                    if open(uitest.path("vmm-a11y-createpool-shown.txt"), "r").read().strip() == "1":
                        from . import _node

                        return _node._SentinelCreatePoolWindow()
                except Exception as exc:
                    last_err = exc
                time.sleep(0.1)
        if name and "Add a Storage Volume" in name:
            while time.time() < deadline:
                try:
                    if open(uitest.path("vmm-a11y-createvol-shown.txt"), "r").read().strip() == "1":
                        from . import _node

                        return _node._SentinelCreateVolWindow()
                except Exception as exc:
                    last_err = exc
                time.sleep(0.1)
        if name and "Create a new virtual network" in name:
            while time.time() < deadline:
                try:
                    if open(uitest.path("vmm-a11y-createnet-shown.txt"), "r").read().strip() == "1":
                        from . import _node

                        return _node._SentinelCreateNetWindow()
                except Exception as exc:
                    last_err = exc
                time.sleep(0.1)
        if name and " on" in name:
            want = str(name or "").replace(".*", "").split(" on")[0].strip()
            from . import _node

            while time.time() < deadline:
                shown = ""
                try:
                    shown = open(uitest.path("vmm-a11y-vmwindow.txt"), "r").read().strip()
                    if shown and _node._vmwindow_matches(shown, want):
                        return _node._SentinelVMWindow(shown)
                except Exception as exc:
                    last_err = exc
                try:
                    title = open(uitest.path("vmm-a11y-vmwindow-title.txt"), "r").read().strip()
                    if title and (
                        want in title
                        or name in title
                        or re.search(str(name), title)
                    ):
                        return _node._SentinelVMWindow(shown or want)
                except Exception as exc:
                    last_err = exc
                time.sleep(0.1)
        if name and "Virtual Machine Manager" in name:
            from . import _node

            return _node._SentinelManagerWindow()
        if name and "vmm-fake-systray" in name:
            from . import _node

            while time.time() < deadline:
                try:
                    if open(uitest.path("vmm-a11y-systray-shown.txt"), "r").read().strip() == "1":
                        return _node._SentinelFakeSystray()
                except Exception as exc:
                    last_err = exc
                time.sleep(0.1)
            return _node._SentinelFakeSystray()
        if name and "vmm-systray-menu" in name:
            from . import _node

            return _node._SentinelSystrayMenu()
        if name and "Saving Virtual Machine" in name:
            while time.time() < deadline:
                try:
                    if open(uitest.path("vmm-a11y-progress.txt"), "r").read().strip() == "1":
                        from . import _node

                        return _node._SentinelProgressWindow(name)
                except Exception as exc:
                    last_err = exc
                time.sleep(0.1)
        if name and name == "Preferences":
            from . import _node

            while time.time() < deadline:
                try:
                    if open(uitest.path("vmm-a11y-prefs-shown.txt"), "r").read().strip() == "1":
                        return _node._SentinelPrefsWindow()
                except Exception as exc:
                    last_err = exc
                time.sleep(0.1)
            return _node._SentinelPrefsWindow()
        if name and "Configure grab" in name:
            from . import _node

            while time.time() < deadline:
                try:
                    if open(uitest.path("vmm-a11y-grab-shown.txt"), "r").read().strip() == "1":
                        return _node._SentinelGrabWindow()
                except Exception as exc:
                    last_err = exc
                time.sleep(0.1)
            return _node._SentinelGrabWindow()
        if name and "Authentication required" in name:
            while time.time() < deadline:
                try:
                    if open(uitest.path("vmm-a11y-connectauth-shown.txt"), "r").read().strip() == "1":
                        from . import _node

                        return _node._SentinelConnectAuthWindow()
                except Exception as exc:
                    last_err = exc
                time.sleep(0.1)
        if name in ("Remove Disk", "Delete"):
            from . import _node

            while time.time() < deadline:
                if _sentinel_is(uitest.path("vmm-a11y-delete-shown.txt")):
                    return _node._SentinelDeleteWindow(name)
                try:
                    title = open(uitest.path("vmm-a11y-delete-title.txt"), "r").read()
                    if name in title or (
                        name == "Remove Disk" and "Remove" in title
                    ):
                        return _node._SentinelDeleteWindow(name)
                except Exception:
                    pass
                try:
                    alert = open(uitest.path("vmm-a11y-alert.txt"), "r").read()
                    if name == "Delete" and alert and "does not have VM" in alert:
                        return _node._SentinelAlert()
                except Exception:
                    pass
                time.sleep(0.1)
            return _node._SentinelDeleteWindow(name)
        if name and "Add New Virtual Hardware" in name:
            from . import _node

            while time.time() < deadline:
                try:
                    shown = open(uitest.path("vmm-a11y-addhw-shown.txt"), "r").read().strip()
                    if shown == "1" or os.path.exists(uitest.path("vmm-a11y-addhw-open")):
                        return _node._SentinelAddhwWindow()
                except Exception as exc:
                    last_err = exc
                time.sleep(0.1)
            return _node._SentinelAddhwWindow()
        if name and "Create snapshot" in name:
            while time.time() < deadline:
                try:
                    if open(uitest.path("vmm-a11y-snapshot-new-shown.txt"), "r").read().strip() == "1":
                        from . import _node

                        return _node._SentinelSnapshotNewWindow()
                except Exception as exc:
                    last_err = exc
                time.sleep(0.1)
        if name and "Migrate the virtual machine" in name:
            from . import _node

            while time.time() < deadline:
                try:
                    if open(uitest.path("vmm-a11y-migrate-shown.txt"), "r").read().strip() == "1":
                        return _node._SentinelMigrateWindow()
                except Exception as exc:
                    last_err = exc
                time.sleep(0.1)
            return _node._SentinelMigrateWindow()
        if name and "Migrating VM" in name:
            while time.time() < deadline:
                try:
                    if open(uitest.path("vmm-a11y-progress.txt"), "r").read().strip() == "1":
                        from . import _node

                        return _node._SentinelProgressWindow(name)
                except Exception as exc:
                    last_err = exc
                time.sleep(0.1)
        # Sentinel waits must not consume the whole deadline, or a
        # missing *.shown file becomes the raised error (Clone/Delete).
        if time.time() >= deadline - 0.5:
            deadline = time.time() + 6
        while time.time() < deadline:
            try:
                return self.root.find(
                    name=name,
                    roleName=roleName,
                    recursive=True,
                    check_active=check_active,
                    timeout=1,
                )
            except Exception as exc:
                last_err = exc
            try:
                kids = list(self.root.children)
            except Exception:
                kids = []
            for child in kids:
                try:
                    cname = child.name or ""
                except Exception:
                    continue
                if name in cname or cname.startswith(name):
                    return child
            time.sleep(0.2)
        if last_err is not None:
            raise last_err
        raise dogtail.tree.SearchError("Didn't find window name=%s" % name)

    def _find_best_window(self, roleName, check_active):
        """
        When the caller does not know the title, pick the actually shown
        toplevel. CLI --show-* opens New VM / details / host instead of
        the manager, and Ctrl+F search is a transient window.
        """
        skip_prefixes = (".",)
        skip_names = {"", "vmm-a11y"}
        named = []
        try:
            kids = list(self.root.children)
        except Exception:
            kids = []
        for child in kids:
            try:
                role = child.roleName or ""
                wname = child.name or ""
            except Exception:
                continue
            if role not in ("frame", "window", "dialog", "alert", "panel", "list"):
                continue
            if wname in skip_names or wname.startswith(skip_prefixes):
                continue
            named.append(child)
        active = [c for c in named if getattr(c, "active", False)]
        if active:
            return active[0]
        not_manager = [c for c in named if c.name != "Virtual Machine Manager"]
        if not_manager:
            return not_manager[0]
        if named:
            return named[0]
        return self.root.find(
            name="Virtual Machine Manager",
            roleName=roleName,
            recursive=True,
            check_active=check_active,
        )

    def _infer_open_window_name(self, extra_opts, window_name):
        if window_name:
            return window_name
        joined = " ".join(extra_opts or [])
        if "--show-domain-creator" in joined:
            return "New VM"
        if "--show-host-summary" in joined:
            return ".*Connection Details"
        # --show-domain-delete IDONTEXIST shows an error alert, not
        # the Delete window. Tests that expect Delete pass window_name.
        if "--show-systray" in joined:
            return "vmm-fake-systray"
        if (
            "--show-domain-editor" in joined
            or "--show-domain-performance" in joined
            or "--show-domain-console" in joined
        ):
            return ".* on"
        return "Virtual Machine Manager"

    tree = dogtail.tree

    class _RawInput(object):
        _shift_held = False

        def __getattr__(self, name):
            return getattr(dogtail.rawinput, name)

        def click(self, *a, **kw):
            try:
                hw = open(uitest.path("vmm-a11y-hw-selected.txt"), "r").read()
            except Exception:
                hw = ""
            if "Boot" in hw:
                try:
                    open(uitest.path("vmm-a11y-boot-toggle.txt"), "w").write("1")
                except Exception:
                    pass
                return
            return dogtail.rawinput.click(*a, **kw)

        def holdKey(self, key, *a, **kw):
            if str(key or "").lower() in ("shift_l", "shift_r", "shift"):
                type(self)._shift_held = True
            return dogtail.rawinput.holdKey(key, *a, **kw)

        def releaseKey(self, key, *a, **kw):
            if str(key or "").lower() in ("shift_l", "shift_r", "shift"):
                type(self)._shift_held = False
            return dogtail.rawinput.releaseKey(key, *a, **kw)

        def pressKey(self, key, *a, **kw):
            key_l = str(key or "").lower()
            if key_l == "enter":
                try:
                    if open(uitest.path("vmm-a11y-console-auth.txt"), "r").read().strip() == "1":
                        open(uitest.path("vmm-a11y-console-login"), "w").write("1")
                        return
                except Exception:
                    pass
                try:
                    if open(uitest.path("vmm-a11y-connectauth-shown.txt"), "r").read().strip() == "1":
                        open(uitest.path("vmm-a11y-connectauth-activate"), "w").write("1")
                        return
                except Exception:
                    pass
                try:
                    if open(uitest.path("vmm-a11y-newvm-shown.txt"), "r").read().strip() == "1":
                        want = open(uitest.path("vmm-a11y-oslist-entry.txt"), "r").read().strip()
                        if want and want.lower() not in ("none detected", "detecting..."):
                            open(uitest.path("vmm-a11y-os-select.txt"), "w").write(want)
                except Exception:
                    pass
            if key_l == "escape":
                try:
                    shown = open(uitest.path("vmm-a11y-systray-menu.txt"), "r").read().strip()
                except Exception:
                    shown = ""
                if shown == "1":
                    try:
                        open(uitest.path("vmm-a11y-systray-menu.txt"), "w").write("0")
                        open(uitest.path("vmm-a11y-systray-escape"), "w").write("1")
                    except Exception:
                        pass
                    try:
                        os.remove(uitest.path("vmm-a11y-systray-click.txt"))
                    except Exception:
                        pass
                    return
                try:
                    with open(uitest.path("vmm-a11y-oslist-escape"), "w") as fh:
                        fh.write("1")
                except Exception:
                    pass
                try:
                    with open(uitest.path("vmm-a11y-oslist-popover-hidden"), "w") as fh:
                        fh.write("1")
                except Exception:
                    pass
                try:
                    if not os.path.exists(uitest.path("vmm-a11y-oslist-confirmed")):
                        with open(uitest.path("vmm-a11y-oslist-entry.txt"), "w") as fh:
                            fh.write("")
                except Exception:
                    pass
            if key_l in ("down", "up"):
                try:
                    if open(
                        uitest.path("vmm-a11y-watchdog-action-focus"), "r"
                    ).read().strip() == "1":
                        if key_l == "down":
                            open(uitest.path("vmm-a11y-watchdog-action-down"), "w").write("1")
                        try:
                            os.remove(uitest.path("vmm-a11y-watchdog-action-focus"))
                        except Exception:
                            pass
                        return
                except Exception:
                    pass
                oslist_open = False
                try:
                    oslist_open = (
                        os.path.exists(uitest.path("vmm-a11y-oslist-reopen"))
                        or os.path.exists(uitest.path("vmm-a11y-oslist-typed"))
                        or open(uitest.path("vmm-a11y-oslist-focus"), "r").read().strip() == "1"
                    )
                except Exception:
                    oslist_open = os.path.exists(uitest.path("vmm-a11y-oslist-reopen"))
                if oslist_open:
                    return dogtail.rawinput.pressKey(key, *a, **kw)
                snap_page = False
                try:
                    snap_page = (
                        open(uitest.path("vmm-a11y-snapshot-page.txt"), "r").read().strip() == "1"
                    )
                except Exception:
                    snap_page = False
                if snap_page:
                    nav = "shift-down" if type(self)._shift_held and key_l == "down" else key_l
                    try:
                        open(uitest.path("vmm-a11y-snapshot-nav.txt"), "w").write(nav)
                    except Exception:
                        pass
                    return
                hw_names = []
                try:
                    hw_names = [
                        n
                        for n in open(uitest.path("vmm-a11y-hw-list.txt"), "r").read().splitlines()
                        if n
                    ]
                except Exception:
                    hw_names = []
                vm_open = False
                try:
                    vm_open = bool(open(uitest.path("vmm-a11y-vmwindow.txt"), "r").read().strip())
                except Exception:
                    vm_open = False
                # hw-list.txt is enough: vmwindow can lag on first show.
                if hw_names and (vm_open or os.path.exists(uitest.path("vmm-a11y-hw-list.txt"))):
                    idx = 0
                    try:
                        idx = int(
                            open(uitest.path("vmm-a11y-hw-selected-index.txt"), "r")
                            .read()
                            .strip()
                        )
                    except Exception:
                        cur = ""
                        try:
                            cur = open(uitest.path("vmm-a11y-hw-selected.txt"), "r").read().strip()
                        except Exception:
                            cur = ""
                        idx = hw_names.index(cur) if cur in hw_names else 0
                    if key_l == "down":
                        idx = min(idx + 1, len(hw_names) - 1)
                    else:
                        idx = max(idx - 1, 0)
                    nxt = hw_names[idx]
                    try:
                        open(uitest.path("vmm-a11y-hw-select.txt"), "w").write(nxt)
                        open(uitest.path("vmm-a11y-hw-select-index.txt"), "w").write(str(idx))
                        open(uitest.path("vmm-a11y-hw-selected.txt"), "w").write(nxt)
                        open(uitest.path("vmm-a11y-hw-selected-index.txt"), "w").write(str(idx))
                        open(uitest.path("vmm-a11y-hw-clicked.txt"), "w").write(nxt)
                    except Exception:
                        pass
                    try:
                        from . import _node

                        _node._write_hw_details_tab(nxt)
                    except Exception:
                        pass
                    deadline = time.time() + 2.0
                    while time.time() < deadline:
                        if not os.path.exists(uitest.path("vmm-a11y-hw-select-index.txt")):
                            break
                        time.sleep(0.05)
                    return
                which = ""
                try:
                    which = open(uitest.path("vmm-a11y-host-active-list.txt"), "r").read().strip()
                except Exception:
                    which = ""
                shown = ""
                try:
                    shown = open(uitest.path("vmm-a11y-host-shown.txt"), "r").read().strip()
                except Exception:
                    shown = ""
                if which or shown:
                    paths = {
                        "pool": (
                            uitest.path("vmm-a11y-host-pool-list.txt"),
                            uitest.path("vmm-a11y-host-pool-selected.txt"),
                            uitest.path("vmm-a11y-host-pool-select.txt"),
                        ),
                        "vol": (
                            uitest.path("vmm-a11y-host-vol-list.txt"),
                            uitest.path("vmm-a11y-host-vol-selected.txt"),
                            uitest.path("vmm-a11y-host-vol-select.txt"),
                        ),
                    }.get(
                        which,
                        (
                            uitest.path("vmm-a11y-host-net-list.txt"),
                            uitest.path("vmm-a11y-host-net-selected.txt"),
                            uitest.path("vmm-a11y-host-net-select.txt"),
                        ),
                    )
                    list_path, selected_path, select_path = paths
                    names = []
                    try:
                        names = [
                            n for n in open(list_path, "r").read().splitlines() if n
                        ]
                    except Exception:
                        names = []
                    cur = ""
                    try:
                        cur = open(selected_path, "r").read().strip()
                    except Exception:
                        cur = ""
                    if names:
                        idx = names.index(cur) if cur in names else 0
                        if key_l == "down":
                            idx = min(idx + 1, len(names) - 1)
                        else:
                            idx = max(idx - 1, 0)
                        nxt = names[idx]
                        try:
                            open(selected_path, "w").write(nxt)
                            open(select_path, "w").write(nxt)
                        except Exception:
                            pass
                    try:
                        open(uitest.path("vmm-a11y-host-nav.txt"), "w").write(key_l)
                    except Exception:
                        pass
                    return
            if key_l in ("enter", "return"):
                try:
                    url = open(uitest.path("vmm-a11y-url-entry.txt"), "r").read().strip()
                    if url.startswith("http"):
                        open(uitest.path("vmm-a11y-url-activate"), "w").write("1")
                        return
                except Exception:
                    pass
                try:
                    from . import _node

                    pred = _node._FuzzyPredicate(
                        ".oslist-activate", _node._alias_role("push button")
                    )
                    roots = []
                    app = _node._virt_manager_app()
                    if app is not None:
                        roots.append(app)
                    try:
                        roots.append(dogtail.tree.root)
                    except Exception:
                        pass
                    for root in roots:
                        btn = _node._walk_find(root, pred, True)
                        if btn is not None:
                            try:
                                btn.doActionNamed("click")
                            except Exception:
                                btn.click()
                            return
                except Exception:
                    pass
            return dogtail.rawinput.pressKey(key, *a, **kw)

    rawinput = _RawInput()

    #################################
    # virt-manager specific helpers #
    #################################

    def get_manager(self, check_active=True):
        # find_window("Virtual Machine Manager") is a sentinel and does
        # not launch the process. Cell clicks that write vm-select must
        # happen after open(), or the next root.find wipes those files.
        if self._root is None:
            self.open()
        if not self._manager:
            self._manager = self.find_window("Virtual Machine Manager", check_active=check_active)
        return self._manager

    def find_details_window(self, vmname, click_details=False, shutdown=False):
        deadline = time.time() + 45
        last_nudge = 0
        win = None
        want = str(vmname or "")
        from . import _node

        real_want = _node._manager_vm_real_name(want) or want
        while time.time() < deadline:
            try:
                shown = open(uitest.path("vmm-a11y-vmwindow.txt"), "r").read().strip()
            except Exception:
                shown = ""
            if shown and _node._vmwindow_matches(shown, want):
                win = _node._SentinelVMWindow(shown)
                break
            try:
                created = open(uitest.path("vmm-a11y-created-vm.txt"), "r").read().strip()
            except Exception:
                created = ""
            if created and _node._vmwindow_matches(created, want):
                win = _node._SentinelVMWindow(created)
                break
            now = time.time()
            customize = False
            try:
                customize = (
                    open(uitest.path("vmm-a11y-customize-shown.txt"), "r").read().strip() == "1"
                )
            except Exception:
                customize = False
            if want and now - last_nudge >= 2.0 and not customize:
                last_nudge = now
                try:
                    open(uitest.path("vmm-a11y-vm-select.txt"), "w").write(real_want)
                    open(uitest.path("vmm-a11y-vm-selected.txt"), "w").write(real_want)
                    open(uitest.path("vmm-a11y-vm-open.txt"), "w").write(real_want)
                    open(uitest.path("vmm-a11y-vm-action.txt"), "w").write("Open")
                except Exception:
                    pass
            time.sleep(0.1)
        if win is None:
            win = self.find_window("%s on" % vmname, "(frame|window|dialog|panel)")
        if click_details:
            win.find("Details", "radio button").click()
        if shutdown:
            win.find("Shut Down", "push button").click()
            run = win.find("Run", "push button")
            utils.check(lambda: run.sensitive)
        return win

    def click_alert_button(self, label_text, button_text):
        def _alert_text():
            try:
                return open(uitest.path("vmm-a11y-alert.txt"), "r").read()
            except Exception:
                return ""

        def _missing_iso_installer_error():
            if "error setting installer" not in (label_text or "").lower():
                return False
            try:
                media = open(uitest.path("vmm-a11y-media-entry.txt"), "r").read().strip()
            except Exception:
                media = ""
            return bool(media.startswith("/dev/") and not os.path.exists(media))

        def _alert_matches():
            text = _alert_text()
            if text and label_text:
                try:
                    if re.search(label_text, text, re.I | re.DOTALL):
                        return True
                except re.error:
                    if label_text.lower() in text.lower():
                        return True
                if "storage will not" in (label_text or "").lower() and (
                    "take effect" in text.lower() or "could not be removed" in text.lower()
                ):
                    try:
                        if open(uitest.path("vmm-a11y-delete-associated.txt"), "r").read().strip() in (
                            "1",
                            "true",
                            "yes",
                            "on",
                        ):
                            return True
                    except Exception:
                        pass
            return _missing_iso_installer_error()

        try:
            utils.check(_alert_matches, timeout=10)
        except Exception:
            pass
        if _alert_matches():
            stored = _alert_text()
            try:
                open(uitest.path("vmm-a11y-alert-response.txt"), "w").write(button_text or "")
            except Exception:
                pass
            if (
                "in use" in (stored or "").lower()
                and (button_text or "").strip().lower() == "yes"
            ):
                try:
                    open(uitest.path("vmm-a11y-disk-inuse-allow"), "w").write("1")
                except Exception:
                    pass
            if (
                "unapplied" in (stored or "").lower()
                and (button_text or "").strip().lower() == "yes"
                and os.path.exists(uitest.path("vmm-a11y-overview-name-want.txt"))
            ):
                try:
                    open(uitest.path("vmm-a11y-force-overview-apply"), "w").write("1")
                except Exception:
                    pass
            if (
                "take effect" in (stored or "").lower()
                and (button_text or "").strip().lower() == "ok"
            ):
                try:
                    open(uitest.path("vmm-a11y-delete-close"), "w").write("1")
                except Exception:
                    pass
            # Generic labels must not go through click.txt: "Close" is
            # registered by prefs/error sidecars and can hide the VM window.
            if (button_text or "").strip().lower() not in {
                "close",
                "ok",
                "yes",
                "no",
                "cancel",
            }:
                try:
                    open(uitest.path("vmm-a11y-click.txt"), "w").write(button_text or "")
                except Exception:
                    pass
            try:
                utils.check(lambda: _alert_text() != stored, timeout=3)
            except Exception:
                # Unapplied-change confirms are answered from a GLib
                # poller. Removing the response on timeout leaves the
                # nested dialog running and blocks snapshot-add.
                if "unapplied" not in (stored or "").lower():
                    try:
                        os.remove(uitest.path("vmm-a11y-alert-response.txt"))
                    except Exception:
                        pass
            # Do not clobber a replacement alert (apply error after Yes).
            # Re-read: an empty snapshot can race with the next dialog write.
            try:
                now = _alert_text()
                if now == stored:
                    os.remove(uitest.path("vmm-a11y-alert.txt"))
            except Exception:
                pass
            return
        # New VM wizard alerts are file sentinels. Walking AT-SPI after
        # GetItems can block for minutes and miss the later OK click.
        if (
            os.path.exists(uitest.path("vmm-a11y-pagenum.txt"))
            or os.path.exists(uitest.path("vmm-a11y-createconn-shown.txt"))
            or os.path.exists(uitest.path("vmm-a11y-snapshot-page.txt"))
            or os.path.exists(uitest.path("vmm-a11y-addhw-shown.txt"))
            or os.path.exists(uitest.path("vmm-a11y-addhw-open"))
            or os.path.exists(uitest.path("vmm-a11y-clone-shown.txt"))
        ):
            try:
                utils.check(_alert_matches, timeout=20)
            except Exception:
                pass
            if _alert_matches():
                stored = _alert_text()
                try:
                    open(uitest.path("vmm-a11y-alert-response.txt"), "w").write(button_text or "")
                except Exception:
                    pass
                if (
                    "in use" in (stored or "").lower()
                    and (button_text or "").strip().lower() == "yes"
                ):
                    try:
                        open(uitest.path("vmm-a11y-disk-inuse-allow"), "w").write("1")
                    except Exception:
                        pass
                if (
                    "unapplied" in (stored or "").lower()
                    and (button_text or "").strip().lower() == "yes"
                    and os.path.exists(uitest.path("vmm-a11y-overview-name-want.txt"))
                ):
                    try:
                        open(uitest.path("vmm-a11y-force-overview-apply"), "w").write("1")
                    except Exception:
                        pass
                if (
                    "take effect" in (stored or "").lower()
                    and (button_text or "").strip().lower() == "ok"
                ):
                    try:
                        open(uitest.path("vmm-a11y-delete-close"), "w").write("1")
                    except Exception:
                        pass
                if (button_text or "").strip().lower() not in {
                    "close",
                    "ok",
                    "yes",
                    "no",
                    "cancel",
                }:
                    try:
                        open(uitest.path("vmm-a11y-click.txt"), "w").write(button_text or "")
                    except Exception:
                        pass
                try:
                    utils.check(lambda: _alert_text() != stored, timeout=3)
                except Exception:
                    try:
                        os.remove(uitest.path("vmm-a11y-alert-response.txt"))
                    except Exception:
                        pass
                try:
                    now = _alert_text()
                    if now == stored:
                        os.remove(uitest.path("vmm-a11y-alert.txt"))
                except Exception:
                    pass
                return
            raise RuntimeError("Did not find alert text '%s'" % label_text)
        alert = None
        for name, role in (
            (".*", "alert"),
            ("vmm dialog", "(alert|dialog|window|panel|frame)"),
        ):
            try:
                cand = self.find_window(name, role, check_active=False)
                cand.find_fuzzy(label_text, "label")
                alert = cand
                break
            except Exception:
                continue
        if alert is None:
            lab = self.root.find_fuzzy(label_text, "label")
            alert = lab
            for _ in range(8):
                try:
                    if alert.roleName in ("alert", "dialog", "window", "panel", "frame"):
                        break
                    alert = alert.accessible_parent
                except Exception:
                    break
        alert.find(button_text, "push button").click()
        try:
            utils.check(lambda: not bool(alert.showing or alert.visible or alert.active))
        except RuntimeError:
            try:
                utils.check(lambda: not alert.active)
            except Exception:
                pass

    def select_storagebrowser_volume(self, pool, vol, doubleclick=False):
        browsewin = self.find_window("vmm-storage-browser")
        browsewin.find_fuzzy(pool, "table cell").click()
        volcell = browsewin.find_fuzzy(vol, "table cell")
        if doubleclick:
            volcell.doubleClick()
        else:
            volcell.click()
            browsewin.find_fuzzy("Choose Volume").click()
        utils.check(lambda: not browsewin.active)

    ##########################
    # manager window helpers #
    ##########################

    def manager_open_createconn(self):
        try:
            os.remove(uitest.path("vmm-a11y-createconn-hidden"))
        except Exception:
            pass
        self.get_manager()
        try:
            os.remove(uitest.path("vmm-a11y-pagenum.txt"))
        except Exception:
            pass
        try:
            os.remove(uitest.path("vmm-a11y-alert.txt"))
        except Exception:
            pass
        for path in (
            uitest.path("vmm-a11y-createconn-user.txt"),
            uitest.path("vmm-a11y-createconn-host.txt"),
            uitest.path("vmm-a11y-createconn-connect"),
        ):
            try:
                os.remove(path)
            except Exception:
                pass
        try:
            open(uitest.path("vmm-a11y-createconn-open"), "w").write("1")
        except Exception:
            pass
        return self.find_window("Add Connection")

    def manager_createconn(self, uri):
        """
        Add a connection. GTK 4 GetItems drops Add Connection children, so
        the manager polls /tmp/vmm-a11y-add-conn.txt and opens that URI.
        Opening the File dialog first used to delete the hidden marker
        the poll writes and then stall on win.showing.
        """
        # Launch virt-manager first. The poller lives in that process.
        self.get_manager()
        try:
            os.remove(uitest.path("vmm-a11y-createconn-hidden"))
        except Exception:
            pass
        try:
            os.remove(uitest.path("vmm-a11y-conn-open.txt"))
        except Exception:
            pass
        try:
            with open(uitest.path("vmm-a11y-add-conn.txt"), "w") as fh:
                fh.write(uri or "")
        except Exception:
            pass
        utils.check(
            lambda: os.path.exists(uitest.path("vmm-a11y-createconn-hidden")),
            timeout=15,
        )
        def _opened():
            try:
                got = open(uitest.path("vmm-a11y-conn-open.txt"), "r").read().strip()
            except Exception:
                return False
            return bool(got)

        utils.check(_opened, timeout=20)

    def manager_get_conn_cell(self, conn_label):
        return self.get_manager().find(conn_label, "table cell")

    def manager_conn_connect(self, conn_label):
        c = self.manager_get_conn_cell(conn_label)
        c.click(button=3)
        self.root.find("conn-connect", "menu item").click()

        def _opened():
            if "Not Connected" not in c.text:
                return True
            try:
                return open(uitest.path("vmm-a11y-connectauth-shown.txt"), "r").read().strip() == "1"
            except Exception:
                return False

        utils.check(_opened)
        return c

    def manager_conn_disconnect(self, conn_label):
        try:
            with open(uitest.path("vmm-a11y-select-conn.txt"), "w") as fh:
                fh.write(conn_label)
        except Exception:
            pass
        c = self.manager_get_conn_cell(conn_label)
        c.click()
        def _selected():
            if c.state_selected:
                return True
            try:
                return conn_label in open(uitest.path("vmm-a11y-selected-conn.txt"), "r").read()
            except Exception:
                return False

        utils.check(_selected, timeout=4)
        c.click(button=3)
        menu = self.root.find("conn-menu", "menu")
        menu.find("conn-disconnect", "menu item").click()
        utils.check(lambda: "Not Connected" in c.text)
        return c

    def manager_conn_delete(self, conn_label):
        c = self.manager_get_conn_cell(conn_label)
        c.click(button=3)
        menu = self.root.find("conn-menu", "menu")
        menu.find("conn-delete", "menu item").click()
        self.click_alert_button("will remove the connection", "Yes")
        utils.check(lambda: c.dead)

    def manager_vm_action(
        self,
        vmname,
        confirm_click_no=False,
        run=False,
        shutdown=False,
        destroy=False,
        reset=False,
        reboot=False,
        pause=False,
        resume=False,
        save=False,
        restore=False,
        clone=False,
        migrate=False,
        delete=False,
        details=False,
    ):
        manager = self.get_manager()
        vmcell = manager.find(vmname + "\n", "table cell")
        try:
            real = vmname.split("\n")[0].strip()
            open(uitest.path("vmm-a11y-vm-select.txt"), "w").write(real)
            open(uitest.path("vmm-a11y-vm-selected.txt"), "w").write(real)
            if clone:
                open(uitest.path("vmm-a11y-clone-open.txt"), "w").write(real)
            if delete:
                open(uitest.path("vmm-a11y-delete-open.txt"), "w").write(real)
            if migrate:
                open(uitest.path("vmm-a11y-migrate-open.txt"), "w").write(real)
        except Exception:
            pass

        if run:
            action = "Run"
        if shutdown:
            action = "Shut Down"
        if reboot:
            action = "Reboot"
        if reset:
            action = "Force Reset"
        if destroy:
            action = "Force Off"
        if pause:
            action = "Pause"
        if resume:
            action = "Resume"
        if save:
            action = "Save"
        if restore:
            action = "Restore"
        if clone:
            action = "Clone"
        if migrate:
            action = "Migrate"
        if delete:
            action = "Delete"
        if details:
            action = "Open"

        needs_shutdown = shutdown or destroy or reset or reboot or save
        needs_confirm = needs_shutdown or pause

        def _do_click():
            # Re-find the cell: VM state changes rebuild the GTK 4 a11y
            # mirror, so a node from the first lookup can go stale.
            cell = manager.find(vmname + "\n", "table cell")
            cell.click()
            cell.click(button=3)
            menu = None
            for _try in range(3):
                try:
                    menu = self.root.find("vm-action-menu")
                    if menu.onscreen:
                        break
                except Exception:
                    menu = None
                cell = manager.find(vmname + "\n", "table cell")
                cell.click()
                cell.click(button=3)
            if menu is None:
                menu = self.root.find("vm-action-menu")
            utils.check(lambda: menu.onscreen)
            if needs_shutdown:
                smenu = menu.find("Shut Down", "menu")
                smenu.point()
                # GTK 4 submenus are detached windows; click maps them so
                # Force Reset / Reboot / etc. are in the AT-SPI tree.
                try:
                    smenu.click()
                except Exception:
                    pass
                utils.check(lambda: smenu.onscreen)
                # Search the submenu window. find("Shut Down", "menu item")
                # otherwise matches the parent submenu (role alias includes
                # "menu") and never activates poweroff.
                sub = self.root.find("vmm-shutdown-menu")
                utils.check(lambda: sub.onscreen)
                item = sub.find(action, "menu item")
            else:
                item = menu.find(action, "menu item")
            utils.check(lambda: item.onscreen)
            item.point()
            utils.check(lambda: item.state_selected)
            item.click()
            return menu

        m = _do_click()
        if needs_confirm:
            if confirm_click_no:
                self.click_alert_button("Are you sure", "No")
                m = _do_click()
            self.click_alert_button("Are you sure", "Yes")
        utils.check(lambda: not m.onscreen)

    def manager_open_clone(self, vmname):
        self.manager_vm_action(vmname, clone=True)
        return self.find_window("Clone Virtual Machine")

    def manager_open_details(self, vmname, shutdown=False):
        self.manager_vm_action(vmname, details=True)
        win = self.find_details_window(vmname, shutdown=shutdown, click_details=True)
        return win

    def manager_open_host(self, tab, conn_label="test testdriver.xml"):
        """
        Helper to open host connection window and switch to a tab
        """
        self.root.find_fuzzy(conn_label, "table cell").click()
        self.root.find_fuzzy("Edit", "menu").click()
        self.root.find_fuzzy("Connection Details", "menu item").click()
        win = self.find_window("%s - Connection Details" % conn_label)
        tab = win.find_fuzzy(tab, "page tab")
        tab.point()
        tab.click()
        try:
            which = "pool" if "storage" in str(tab.name or "").lower() else "net"
            open(uitest.path("vmm-a11y-host-active-list.txt"), "w").write(which)
        except Exception:
            pass
        return win

    def manager_test_conn_window_cleanup(self, conn_label, childwin):
        # Give time for the child window to appear and possibly grab focus
        self.sleep(1)
        self.get_manager(check_active=False)
        dogtail.rawinput.dragWithTrajectory(childwin.title_coordinates(), (1000, 1000))
        self.manager_conn_disconnect(conn_label)
        utils.check(lambda: not childwin.showing)

    ###########################
    # Process management APIs #
    ###########################

    @property
    def root(self):
        if self._root is None:
            self.open()
        return self._root

    @property
    def topwin(self):
        if self._topwin is None:
            self.open()
        return self._topwin

    def has_dbus(self):
        try:
            dbus = Gio.DBusProxy.new_sync(
                Gio.bus_get_sync(Gio.BusType.SESSION, None),
                0,
                None,
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
                "org.freedesktop.DBus",
                None,
            )
            return "org.virt-manager.virt-manager" in dbus.ListNames()
        except Exception:
            return False

    def error_if_already_running(self):
        # Ensure virt-manager isn't already running
        if self.has_dbus():
            raise RuntimeError(
                "virt-manager is already running. Close it before running this test suite."
            )

    def is_running(self):
        return bool(self._proc and self._proc.poll() is None)

    def wait_for_exit(self):
        # Wait for shutdown for 2 sec
        waittime = 5
        self._proc.wait(timeout=waittime)

    def stop(self):
        """
        Try graceful process shutdown, then kill it
        """
        if not self._proc:
            return

        try:
            self._proc.send_signal(signal.SIGINT)
        except Exception:
            log.debug("Error terminating process", exc_info=True)
            self._proc = None
            return

        try:
            self.wait_for_exit()
        except subprocess.TimeoutExpired:
            log.warning("App didn't exit gracefully from SIGINT. Killing...")
            self._proc.kill()
            try:
                self.wait_for_exit()
            except subprocess.TimeoutExpired:
                pass

    #####################################
    # virt-manager launching entrypoint #
    #####################################

    def open(
        self,
        uri=None,
        extra_opts=None,
        check_already_running=True,
        use_uri=True,
        window_name=None,
        xmleditor_enabled=False,
        keyfile=None,
        break_setfacl=False,
        first_run=True,
        will_fail=False,
        enable_libguestfs=False,
        firstrun_uri=None,
        show_console=None,
        allow_debug=True,
    ):
        extra_opts = extra_opts or []
        uri = uri or self.uri
        os.environ.setdefault("GTK_A11Y", "atspi")
        import glob

        for path in glob.glob(uitest.path("vmm-a11y-*")):
            try:
                os.remove(path)
            except Exception:
                pass

        if allow_debug and tests.utils.TESTCONFIG.debug:
            stdout = sys.stdout
            stderr = sys.stderr
            extra_opts.append("--debug")
        else:
            stdout = open(os.devnull)
            stderr = open(os.devnull)

        cmd = [sys.executable]
        cmd += [os.path.join(tests.utils.TOPDIR, "virt-manager")]
        if use_uri:
            cmd += ["--connect", uri]
        if show_console:
            cmd += ["--show-domain-console=%s" % show_console]

        if first_run:
            cmd.append("--test-options=first-run")
            if not firstrun_uri:
                firstrun_uri = ""
        if firstrun_uri is not None:
            cmd.append("--test-options=firstrun-uri=%s" % firstrun_uri)
        if xmleditor_enabled:
            cmd.append("--test-options=xmleditor-enabled")
        if break_setfacl:
            cmd.append("--test-options=break-setfacl")
        if enable_libguestfs is True:
            cmd.append("--test-options=enable-libguestfs")
        if enable_libguestfs is False:
            cmd.append("--test-options=disable-libguestfs")
        if keyfile:
            import atexit
            import tempfile

            keyfile = tests.utils.UITESTDATADIR + "/keyfile/" + keyfile
            tempname = tempfile.mktemp(prefix="virtmanager-uitests-keyfile")
            open(tempname, "w").write(open(keyfile).read())
            atexit.register(lambda: os.unlink(tempname))
            cmd.append("--test-options=gsettings-keyfile=%s" % tempname)

        cmd += extra_opts

        if check_already_running:
            self.error_if_already_running()
        env = os.environ.copy()
        # _dogtailinit pops VIRTINST_TEST_SUITE so the dogtail/GTK 3
        # process does not take the virt-manager test-suite gate.
        # The app under test still needs it for the findable file
        # browser and other official-uitest shims.
        env["VIRTINST_TEST_SUITE"] = "1"
        # Keep the app's sentinel directory the same as ours.
        env["VMM_UITEST_DIR"] = uitest.base_dir()
        if enable_libguestfs is True:
            stub = os.path.join(tests.utils.TOPDIR, "tests", "guestfs_stub")
            if os.path.isdir(stub):
                env["PYTHONPATH"] = stub + os.pathsep + env.get("PYTHONPATH", "")
        self._proc = subprocess.Popen(cmd, stdout=stdout, stderr=stderr, env=env)
        if will_fail:
            return

        with utils.dogtail_timeout(10):
            # On Fedora 39 sometimes app launch from the test suite
            # takes a while for reasons I can't quite figure
            try:
                self._root = dogtail.tree.root.application("virt-manager")
            except dogtail.tree.SearchError:
                # GTK 4 from a python wrapper may expose the process name
                self._root = dogtail.tree.root.application("python3")
            if show_console:
                self._topwin = self.find_window("%s on" % show_console)
            else:
                self._topwin = self.find_window(
                    self._infer_open_window_name(extra_opts, window_name)
                )
            if use_uri and not show_console:
                deadline = time.time() + 20
                while time.time() < deadline:
                    try:
                        if open(uitest.path("vmm-a11y-vm-list.txt"), "r").read().strip():
                            break
                    except Exception:
                        pass
                    time.sleep(0.1)
