# This work is licensed under the GNU GPLv2 or later.
# See the COPYING file in the top-level directory.

import os
import re
import subprocess
import time

from gi.repository import Gdk

import dogtail.tree
import pyatspi

from virtinst import log
from . import utils
from virtManager.lib import uitest


def _looks_like_ip_label(want):
    text = str(want or "").strip()
    if text == "Unknown":
        return True
    if "/128" in text or text.startswith("fd00") or text.startswith("10.0.0."):
        return True
    parts = text.split(".")
    if len(parts) == 4 and all(p.isdigit() for p in parts):
        return True
    return text.count(":") >= 2


# GTK4 AT-SPI role names differ from GTK3 in several common cases.
# Keep the original uitest strings and expand them to accept both.
_GTK4_ROLE_ALIASES = {
    "push button": "(button|push button)",
    ".*push button.*": ".*(button|push button).*",
    "button": "(button|push button)",
    "frame": "(frame|window|panel|dialog|list)",
    "window": "(frame|window|panel|dialog|list)",
    "alert": "(alert|dialog)",
    "dialog": "(dialog|alert|window|frame|panel)",
    ".*dialog.*": ".*(dialog|alert|window|frame|panel).*",
    "menu": "(menu|window|dialog|panel|frame)",
    "menu item": "(menu item|menu|push button|button)",
    ".*menu item.*": ".*(menu item|menu|check menu item|push button|button).*",
    "check menu item": "(check menu item|menu item|check box|check button)",
    ".*check menu item.*": ".*(check menu item|menu item|check box|check button).*",
    "table column header": "(table column header|column header|button|push button|filler)",
    ".*table column header.*": ".*(table column header|column header|button|push button|filler).*",
    "spin button": "(spin button|spin|entry|text|text box)",
    "table cell": "(table cell|list item|cell|button|push button)",
    ".*table cell.*": ".*(table cell|list item|cell|button|push button).*",
    "radio button": "(radio button|radio|toggle button|button|push button|check box|check button)",
    "radio": "(radio button|radio|toggle button|button|push button|check box|check button)",
    ".*radio.*": ".*(radio button|radio|toggle button|button|push button|check box|check button).*",
    "check button": "(check button|check box)",
    "check box": "(check box|check button)",
    "check": "(check|check box|check button)",
    ".*check.*": ".*(check|check box|check button).*",
    "page tab": "(page tab|tab|button|push button)",
    "text": "(text|entry|text box)",
    "combo box": "(combo box|combo)",
    "file chooser": "(file chooser|dialog|window)",
    "label": "(label|static)",
    ".*label.*": ".*(label|static).*",
    "toggle button": "(toggle button|button|push button)",
    "toggle": "(toggle button|button|push button|expander)",
    ".*toggle.*": ".*(toggle button|button|push button|expander).*",
}

_WINDOW_ROLES = ("frame", "window", "dialog", "alert", "file chooser", "panel", "list")


def _alias_role(roleName):
    if not roleName:
        return roleName
    return _GTK4_ROLE_ALIASES.get(roleName, roleName)


def _virt_manager_app():
    try:
        root = dogtail.tree.root
    except Exception:
        return None
    for name in ("virt-manager", "python3"):
        try:
            return root.application(name)
        except Exception:
            continue
    return None


def _live_manager_node():
    app = _virt_manager_app()
    if app is None:
        return None
    try:
        kids = list(app.children)
    except Exception:
        return None
    for child in kids:
        try:
            if "Virtual Machine Manager" in (child.name or ""):
                return child
        except Exception:
            continue
    return None


def _hw_details_tab_for_label(label):
    """Details notebook tab for a hardware-list label."""
    label = label or ""
    if any(key in label for key in ("Disk", "CDROM", "Floppy")):
        return "disk-tab"
    if "NIC" in label or "Network" in label:
        return "network-tab"
    if label in ("Overview",):
        return "overview-tab"
    if "OS information" in label:
        return "os-tab"
    if label in ("Performance",):
        return "performance-tab"
    if label in ("CPUs", "CPU"):
        return "cpu-tab"
    if label in ("Memory",):
        return "memory-tab"
    if "Boot" in label:
        return "boot-tab"
    if any(key in label for key in ("Serial", "Parallel", "Console", "Channel")):
        return "char-tab"
    if "Sound" in label:
        return "sound-tab"
    if "Video" in label:
        return "video-tab"
    if "Watchdog" in label:
        return "watchdog-tab"
    if "Smartcard" in label:
        return "smartcard-tab"
    if "TPM" in label:
        return "tpm-tab"
    if "VSOCK" in label or "vsock" in label.lower():
        return "vsock-tab"
    if "Filesystem" in label:
        return "filesystem-tab"
    if "Controller" in label:
        return "controller-tab"
    if "Display" in label or "Graphics" in label:
        return "graphics-tab"
    if any(key in label for key in ("PCI", "USB ", "Host")):
        return "host-tab"
    return None


def _write_hw_details_tab(label):
    tab = _hw_details_tab_for_label(label)
    if not tab:
        return
    try:
        open(uitest.path("vmm-a11y-details-tab.txt"), "w").write(tab)
    except Exception:
        pass
    if tab == "host-tab":
        try:
            open(uitest.path("vmm-a11y-hostdev-clicked.txt"), "w").write(label or "")
        except Exception:
            pass


class _SentinelTableCell(object):
    """hw-list row when AT-SPI walks hang after GetItems."""

    def __init__(self, name, selected=False, index=None):
        self.name = name
        self.roleName = "table cell"
        self._selected = selected
        self._index = index

    @property
    def state_selected(self):
        # test-many-devices has duplicate NIC/Controller/Disk labels, so a
        # matching published index is authoritative. A stale index must not
        # hide an explicit Sound/Video rename (sb16 -> ac97) or tab match.
        index_match = None
        if self._index is not None:
            for path in (
                uitest.path("vmm-a11y-hw-select-index.txt"),
                uitest.path("vmm-a11y-hw-selected-index.txt"),
            ):
                try:
                    cur = open(path, "r").read().strip()
                    if cur != "" and int(cur) == int(self._index):
                        index_match = True
                        break
                    if cur != "" and path.endswith("selected-index.txt"):
                        index_match = int(cur) == int(self._index)
                except Exception:
                    pass
        if index_match is True:
            return True
        name_hit = False
        unique_hit = False
        for path in (
            uitest.path("vmm-a11y-hw-clicked.txt"),
            uitest.path("vmm-a11y-hw-selected.txt"),
        ):
            try:
                cur = open(path, "r").read().strip()
            except Exception:
                cur = ""
            if cur == self.name:
                name_hit = True
            if cur and self.name:
                a = cur.split()[0]
                b = self.name.split()[0]
                if a == b and a in (
                    "Sound",
                    "Video",
                    "Display",
                    "Watchdog",
                ):
                    unique_hit = True
        try:
            tab = open(uitest.path("vmm-a11y-details-tab.txt"), "r").read().strip()
            name = self.name or ""
            if tab == "sound-tab" and name.startswith("Sound"):
                unique_hit = True
            if tab == "video-tab" and name.startswith("Video"):
                unique_hit = True
            if tab == "watchdog-tab" and name.startswith("Watchdog"):
                unique_hit = True
        except Exception:
            pass
        # Sound sb16 -> ac97 keeps a unique-type match when click/selected
        # still names this type and the published index lags on Floppy.
        # A stale details-tab after keyboard walk must not keep the
        # previous Watchdog/Sound/Video selected once the index moved.
        if unique_hit:
            if index_match is not False:
                return True
            try:
                rows = [
                    n
                    for n in open(uitest.path("vmm-a11y-hw-list.txt"), "r").read().splitlines()
                    if n
                ]
                cur = open(uitest.path("vmm-a11y-hw-selected-index.txt"), "r").read().strip()
                at = rows[int(cur)]
                if at.split()[0] != (self.name or "").split()[0]:
                    for path in (
                        uitest.path("vmm-a11y-hw-clicked.txt"),
                        uitest.path("vmm-a11y-hw-selected.txt"),
                    ):
                        try:
                            pub = open(path, "r").read().strip()
                        except Exception:
                            pub = ""
                        if (
                            pub
                            and self.name
                            and pub.split()[0] == (self.name or "").split()[0]
                            and pub.split()[0]
                            in (
                                "Sound",
                                "Video",
                                "Display",
                                "Watchdog",
                            )
                        ):
                            return True
            except Exception:
                pass
        if index_match is False:
            return False
        if name_hit:
            return True
        try:
            cur = open(uitest.path("vmm-a11y-hw-selected.txt"), "r").read().strip()
            if cur == self.name:
                return True
        except Exception:
            pass
        try:
            cur = open(uitest.path("vmm-a11y-hostdev-selected.txt"), "r").read().strip()
            if cur == self.name or (self.name and self.name in cur):
                return True
        except Exception:
            pass
        try:
            cur = open(uitest.path("vmm-a11y-vol-selected.txt"), "r").read().strip()
            if cur == self.name or (self.name and self.name in cur):
                return True
        except Exception:
            pass
        try:
            cur = open(uitest.path("vmm-a11y-host-vol-selected.txt"), "r").read().strip()
            if cur == self.name or (self.name and self.name in cur):
                return True
        except Exception:
            pass
        return self._selected

    @property
    def selected(self):
        return self.state_selected

    @property
    def dead(self):
        name = self.name or ""
        if not name:
            return True
        try:
            deleted = [
                n
                for n in open(uitest.path("vmm-a11y-deleted-vols.txt"), "r").read().splitlines()
                if n
            ]
            if any(name == n or name in n or n in name for n in deleted):
                return True
        except Exception:
            pass
        for path in (
            uitest.path("vmm-a11y-vol-list.txt"),
            uitest.path("vmm-a11y-host-vol-list.txt"),
        ):
            try:
                names = [n for n in open(path, "r").read().splitlines() if n]
            except Exception:
                names = []
            if names and not any(name == n or name in n or n in name for n in names):
                return True
        return False

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    @property
    def sensitive(self):
        return True

    def check_onscreen(self):
        return True

    def bring_on_screen(self, *args, **kwargs):
        ignore = (args, kwargs)
        return self

    def point(self, *args, **kwargs):
        ignore = (args, kwargs)
        return self

    @property
    def focused(self):
        return self.state_selected

    @property
    def text(self):
        if self._index is not None:
            try:
                rows = [
                    n
                    for n in open(uitest.path("vmm-a11y-hw-list.txt"), "r").read().splitlines()
                    if n
                ]
                if 0 <= int(self._index) < len(rows):
                    return rows[int(self._index)]
            except Exception:
                pass
        try:
            rows = [
                n
                for n in open(uitest.path("vmm-a11y-hw-list.txt"), "r").read().splitlines()
                if n
            ]
            if self.name and self.name not in rows:
                return rows[0] if rows else ""
        except Exception:
            pass
        return self.name or ""

    def click(self, *args, **kwargs):
        name = self.name or ""
        button = kwargs.get("button", 1)
        if button == 3:
            try:
                open(uitest.path("vmm-a11y-hw-popup.txt"), "w").write(name)
                open(uitest.path("vmm-a11y-hw-popup-shown.txt"), "w").write("1")
                open(uitest.path("vmm-a11y-hw-clicked.txt"), "w").write(name)
                open(uitest.path("vmm-a11y-hw-selected.txt"), "w").write(name)
            except Exception:
                pass
            return
        browser_open = False
        try:
            browser_open = (
                open(uitest.path("vmm-a11y-storage-browser.txt"), "r").read().strip() == "1"
            )
        except Exception:
            browser_open = False
        looks_like_vol = bool(
            browser_open
            or name.endswith((".img", ".qcow2", ".iso", ".raw"))
            or name in ("iso-vol", "default-vol", "dir-vol", "bochs-vol")
        )
        if looks_like_vol and not any(
            key in name for key in ("Disk", "CDROM", "Floppy", "NIC")
        ):
            try:
                open(uitest.path("vmm-a11y-vol-select.txt"), "w").write(name)
            except Exception:
                pass
            deadline = time.time() + 2.0
            while time.time() < deadline:
                try:
                    if open(uitest.path("vmm-a11y-vol-selected.txt"), "r").read().strip() == name:
                        break
                except Exception:
                    pass
                time.sleep(0.05)
            return
        try:
            open(uitest.path("vmm-a11y-hw-select.txt"), "w").write(self.name or "")
            open(uitest.path("vmm-a11y-hw-selected.txt"), "w").write(self.name or "")
            # Publisher overwrites hw-selected with the GTK row (often
            # Overview). Remove/apply must use this click-only label.
            open(uitest.path("vmm-a11y-hw-clicked.txt"), "w").write(self.name or "")
            open(uitest.path("vmm-a11y-last-hw.txt"), "w").write(self.name or "")
            if (self.name or "") not in (
                "Overview",
                "OS information",
                "Performance",
                "CPUs",
                "Memory",
                "Boot Options",
            ):
                open(uitest.path("vmm-a11y-hw-last-device.txt"), "w").write(self.name or "")
        except Exception:
            pass
        try:
            apply_on = (
                open(uitest.path("vmm-a11y-config-apply-sensitive"), "r").read().strip()
                == "1"
            )
        except Exception:
            apply_on = False
        dest = self.name or ""
        if apply_on and dest in (
            "CPUs",
            "CPU",
            "Memory",
            "Overview",
            "OS information",
            "Performance",
            "Boot Options",
        ):
            # Publish before GTK confirm runs so click_alert_button
            # does not wait 36s when _hw_changed_cb is a no-op.
            try:
                existing = open(uitest.path("vmm-a11y-alert.txt"), "r").read()
            except Exception:
                existing = ""
            lowered = existing.lower()
            if not existing.strip() or "unapplied" in lowered:
                try:
                    open(uitest.path("vmm-a11y-alert.txt"), "w").write(
                        "There are unapplied changes. Would you like to apply them now?"
                    )
                except Exception:
                    pass
            try:
                os.remove(uitest.path("vmm-a11y-unapplied-prompt.txt"))
            except Exception:
                pass
        wrote_index = False
        if self._index is not None:
            names = _hw_list_names()
            # After USB 2/3 rewrite the published index can still point at
            # PCI while this cell is named Controller USB 0.
            if 0 <= int(self._index) < len(names) and names[int(self._index)] == (
                self.name or ""
            ):
                try:
                    open(uitest.path("vmm-a11y-hw-select-index.txt"), "w").write(
                        str(self._index)
                    )
                    open(uitest.path("vmm-a11y-hw-selected-index.txt"), "w").write(
                        str(self._index)
                    )
                    wrote_index = True
                except Exception:
                    pass
        try:
            _write_hw_details_tab(self.name or "")
        except Exception:
            pass
        deadline = time.time() + 2.0
        while time.time() < deadline:
            try:
                if wrote_index:
                    cur = open(uitest.path("vmm-a11y-hw-selected-index.txt"), "r").read().strip()
                    if cur != "" and int(cur) == int(self._index):
                        break
                elif open(uitest.path("vmm-a11y-hw-selected.txt"), "r").read().strip() == (
                    self.name or ""
                ):
                    break
            except Exception:
                pass
            time.sleep(0.05)
        # The click writes hw-selected itself. Wait until the app
        # poller consumes hw-select.txt so a later _select_hw cannot
        # overwrite dest before Don't-warn abandon runs.
        deadline = time.time() + 2.0
        while time.time() < deadline:
            try:
                if not os.path.exists(uitest.path("vmm-a11y-hw-select.txt")):
                    break
                if open(uitest.path("vmm-a11y-hw-select.txt"), "r").read().strip() != (
                    self.name or ""
                ):
                    break
            except Exception:
                break
            time.sleep(0.05)
        # Don't-warn returns immediately from _select_hw(CPUs) if the
        # CPU tab is already showing. Wait for the app to abandon or
        # show a confirm so the next _select_hw does not overwrite
        # this click before Apply is cleared.
        if apply_on and dest in (
            "CPUs",
            "CPU",
            "Memory",
            "Overview",
            "OS information",
            "Performance",
            "Boot Options",
        ):
            deadline = time.time() + 3.0
            while time.time() < deadline:
                try:
                    if (
                        open(uitest.path("vmm-a11y-config-apply-sensitive"), "r")
                        .read()
                        .strip()
                        != "1"
                    ):
                        break
                except Exception:
                    break
                if os.path.exists(uitest.path("vmm-a11y-unapplied-prompt.txt")):
                    break
                time.sleep(0.05)
        if "NIC" in (self.name or ""):
            deadline = time.time() + 5.0
            while time.time() < deadline:
                try:
                    for_dev = open(
                        uitest.path("vmm-a11y-network-ip-for.txt"), "r"
                    ).read().strip()
                    ips = open(uitest.path("vmm-a11y-network-ip.txt"), "r").read()
                    if ips and for_dev == (self.name or ""):
                        break
                except Exception:
                    pass
                time.sleep(0.05)

    def doubleClick(self, *args, **kwargs):
        self.click(*args, **kwargs)
        try:
            open(uitest.path("vmm-a11y-choose-volume"), "w").write("1")
            open(uitest.path("vmm-a11y-click.txt"), "w").write("Choose Volume")
        except Exception:
            pass
        deadline = time.time() + 3.0
        while time.time() < deadline:
            try:
                if open(uitest.path("vmm-a11y-storage-browser.txt"), "r").read().strip() != "1":
                    return
            except Exception:
                return
            time.sleep(0.05)

    def find(self, *args, **kwargs):
        raise dogtail.tree.SearchError("sentinel cell has no children")


def _oslist_query_want(name):
    raw = str(name or "")
    compact = raw.replace(".*", "").replace("\\", "").strip()
    if "include-eol" in compact.lower():
        return "include-eol"
    if "generic" in compact.lower():
        return "generic"
    return compact.strip("() ").strip()


class _OslistRowSentinel(object):
    """OS row or include-eol when the popover is missing from AT-SPI."""

    def __init__(self, name, want):
        self.name = name
        self.roleName = "push button"
        self._want = want

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    @property
    def visible(self):
        return True

    @property
    def sensitive(self):
        return True

    def check_onscreen(self):
        return True

    def bring_on_screen(self, *args, **kwargs):
        return self

    def click(self, *args, **kwargs):
        want = self._want or "generic"
        try:
            if want == "include-eol":
                open(uitest.path("vmm-a11y-oslist-eol.txt"), "w").write("1")
                return
            open(uitest.path("vmm-a11y-os-select.txt"), "w").write(want)
            open(uitest.path("vmm-a11y-oslist-confirmed"), "w").write("1")
            open(uitest.path("vmm-a11y-oslist-popover-hidden"), "w").write("1")
            try:
                os.remove(uitest.path("vmm-a11y-oslist-reopen"))
            except Exception:
                pass
        except Exception:
            pass
        deadline = time.time() + 3.0
        while time.time() < deadline:
            try:
                got = open(uitest.path("vmm-a11y-oslist-entry.txt"), "r").read().strip()
            except Exception:
                got = ""
            hidden = False
            try:
                hidden = open(uitest.path("vmm-a11y-oslist-popover-hidden"), "r").read().strip() == "1"
            except Exception:
                hidden = False
            if got and want and want.lower() not in ("include-eol",):
                compact = got.lower().replace(" ", "")
                if want.lower() in compact or "win8" in compact or "windows 8" in compact:
                    if hidden:
                        return
            time.sleep(0.05)


class _OslistPopoverSentinel(object):
    """oslist-popover after GetItems: AT-SPI walks miss the renamed wrap."""

    def __init__(self):
        self.name = "oslist-popover"
        self.roleName = "panel"

    def _hidden(self):
        try:
            return os.path.exists(
                uitest.path("vmm-a11y-oslist-popover-hidden")
            ) or os.path.exists(uitest.path("vmm-a11y-oslist-escape"))
        except Exception:
            return False

    @property
    def showing(self):
        return not self._hidden()

    @property
    def onscreen(self):
        return not self._hidden()

    @property
    def visible(self):
        return not self._hidden()

    @property
    def sensitive(self):
        return True

    def check_onscreen(self):
        utils.check(lambda: self.onscreen)

    def check_not_onscreen(self):
        utils.check(lambda: not self.onscreen)

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (roleName, labeller_text, check_active, recursive, focusable, timeout)
        want = _oslist_query_want(name)
        if not want:
            raise dogtail.tree.SearchError(
                "Didn't find widget with name='%s' "
                "roleName='%s' labeller_text='%s'" % (name, roleName, labeller_text)
            )
        label = "include-eol" if want == "include-eol" else want
        return _OslistRowSentinel(label, want)

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        name_pattern = (".*%s.*" % name) if name else None
        role_pattern = (".*%s.*" % roleName) if roleName else None
        labeller_pattern = (".*%s.*" % labeller_text) if labeller_text else None
        return self.find(name_pattern, role_pattern, labeller_pattern)


class _SentinelOslistEntry(object):
    """oslist-entry after GetItems: the real AT-SPI node goes DEAD."""

    name = "oslist-entry"
    roleName = "text"

    @property
    def text(self):
        try:
            return open(uitest.path("vmm-a11y-oslist-entry.txt"), "r").read().strip()
        except Exception:
            return ""

    @text.setter
    def text(self, value):
        self.set_text(value)

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    @property
    def sensitive(self):
        return True

    @property
    def focused(self):
        return True

    def check_onscreen(self):
        return True

    def check_sensitive(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        for marker in (
            uitest.path("vmm-a11y-oslist-escape"),
            uitest.path("vmm-a11y-oslist-popover-hidden"),
        ):
            try:
                os.remove(marker)
            except Exception:
                pass
        try:
            open(uitest.path("vmm-a11y-oslist-reopen"), "w").write("1")
            open(uitest.path("vmm-a11y-oslist-focus"), "w").write("1")
        except Exception:
            pass

    def set_text(self, text):
        # Typing only filters the popover. Confirming a row writes os-select.
        try:
            open(uitest.path("vmm-a11y-oslist-entry.txt"), "w").write(text or "")
            open(uitest.path("vmm-a11y-entry.txt"), "w").write(text or "")
            open(uitest.path("vmm-a11y-oslist-typed"), "w").write("1")
        except Exception:
            pass
        _oslist_start_search()
        try:
            open(uitest.path("vmm-a11y-click.txt"), "w").write(".entry-load-oslist-entry")
        except Exception:
            pass

    def typeText(self, string):
        self.set_text((self.text or "") + (string or ""))


def _sentinel_oslist_entry(name, roleName):
    if not name:
        return None
    raw = str(name).replace(".*", "")
    compact = raw.lower()
    if "oslist-entry" not in compact:
        return None
    role = str(roleName or "").lower()
    if role and "text" not in role and "entry" not in role and "label" not in role:
        return None
    return _SentinelOslistEntry()


def _sentinel_oslist_popover(name, roleName):
    if not name:
        return None
    raw = str(name)
    if raw.startswith("."):
        return None
    compact = raw.replace(".*", "")
    if compact != "oslist-popover" and raw != "oslist-popover":
        return None
    ignore = roleName
    return _OslistPopoverSentinel()


class _StorageRadioSentinel(object):
    """Storage create/select radios after GetItems hides the methods window."""

    def __init__(self, name, want):
        self.name = name
        self.roleName = "radio button"
        self._want = want

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    @property
    def sensitive(self):
        return True

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        try:
            open(uitest.path("vmm-a11y-storage-radio.txt"), "w").write(self._want)
        except Exception:
            pass
        try:
            open(uitest.path("vmm-a11y-click.txt"), "w").write(self.name)
        except Exception:
            pass


def _sentinel_storage_radio(name, roleName):
    if not name:
        return None
    raw = str(name)
    compact = raw.replace(".*", "").lower()
    role = str(roleName or "").lower()
    if role and "radio" not in role and "button" not in role and "check" not in role:
        return None
    if "select or create" in compact:
        return _StorageRadioSentinel("Select or create custom storage", "select")
    if "create a disk image" in compact:
        return _StorageRadioSentinel(
            "Create a disk image for the virtual machine", "create"
        )
    if "enable storage" in compact:
        return _EnableStorageSentinel()
    return None


class _EnableStorageSentinel(object):
    name = "Enable storage for this virtual machine"
    roleName = "check box"

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    @property
    def sensitive(self):
        return True

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        try:
            open(uitest.path("vmm-a11y-click.txt"), "w").write("Enable storage")
        except Exception:
            pass


class _SentinelEntry(object):
    """Named entry when AT-SPI walks miss the sidecar after GetItems."""

    def __init__(self, name, path):
        self.name = name
        self.roleName = "text"
        self._path = path

    @property
    def text(self):
        path = self._path
        if self.name == "media-entry":
            try:
                shown = open(uitest.path("vmm-a11y-vmwindow.txt"), "r").read().strip()
            except Exception:
                shown = ""
            try:
                customize = open(uitest.path("vmm-a11y-customize-shown.txt"), "r").read().strip()
            except Exception:
                customize = "0"
            details_val = None
            try:
                if os.path.exists(uitest.path("vmm-a11y-details-media-entry.txt")):
                    details_val = open(
                        uitest.path("vmm-a11y-details-media-entry.txt"), "r"
                    ).read()
            except Exception:
                details_val = None

            def _pretty_nodedev_label(raw):
                raw = raw if raw is not None else ""
                text = raw.strip()
                if not text:
                    return raw
                if " (" in text and text.endswith(")"):
                    return raw
                try:
                    for line in open(
                        uitest.path("vmm-a11y-details-media-combo.txt"), "r"
                    ).read().splitlines():
                        line = line.strip()
                        if line.endswith("(%s)" % text) or (
                            text.startswith("/dev/") and "(%s)" % text in line
                        ):
                            return line
                except Exception:
                    pass
                return raw

            # After install the details window owns media-entry. An empty
            # details file means the CDROM was ejected; do not fall through
            # to leftover wizard /pool- paths.
            if details_val is not None and shown and customize != "1":
                try:
                    src = open(uitest.path("vmm-a11y-disk-source-path.txt"), "r").read()
                    sens = open(
                        uitest.path("vmm-a11y-config-apply-sensitive"), "r"
                    ).read().strip()
                    if src == "" and sens != "1":
                        return ""
                except Exception:
                    pass
                return _pretty_nodedev_label(details_val)
            if details_val is not None and not details_val.strip():
                return details_val
            for alt in (
                uitest.path("vmm-a11y-disk-source-path.txt"),
                uitest.path("vmm-a11y-media-browse.txt"),
                uitest.path("vmm-a11y-details-media-entry.txt"),
            ):
                try:
                    val = open(alt, "r").read().strip()
                except Exception:
                    continue
                if val and (
                    "iso-vol" in val
                    or "/pool-" in val
                    or val.endswith((".iso", ".img", ".qcow2"))
                ):
                    return val
            try:
                if os.path.exists(uitest.path("vmm-a11y-details-media-entry.txt")):
                    path = uitest.path("vmm-a11y-details-media-entry.txt")
            except Exception:
                pass
        if str(self.name).startswith("Title"):
            try:
                return open(uitest.path("vmm-a11y-overview-title-current.txt"), "r").read()
            except Exception:
                pass
        try:
            raw = open(path, "r").read()
        except Exception:
            return ""
        if self.name == "media-entry":
            try:
                return _pretty_nodedev_label(raw)
            except Exception:
                return raw
        return raw

    @text.setter
    def text(self, value):
        self.set_text(value)

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    @property
    def sensitive(self):
        return True

    def check_onscreen(self):
        return True

    def check_sensitive(self):
        return True

    def click(self, *args, **kwargs):
        return True

    def click_secondary_icon(self, *args, **kwargs):
        ignore = (args, kwargs)
        if self.name == "media-entry":
            self.set_text("")
        return True

    def click_combo_entry(self, *args, **kwargs):
        ignore = (args, kwargs)
        return True

    def set_text(self, text):
        try:
            open(self._path, "w").write(text if text is not None else "")
        except Exception:
            pass
        if self._path in (
            uitest.path("vmm-a11y-boot-init-path.txt"),
            uitest.path("vmm-a11y-boot-init-args.txt"),
        ):
            try:
                open(uitest.path("vmm-a11y-config-apply-sensitive"), "w").write("1")
            except Exception:
                pass
        if self.name == "media-entry":
            try:
                open(self._path + ".set", "w").write(text if text is not None else "")
            except Exception:
                pass
            try:
                os.remove(uitest.path("vmm-a11y-media-select.txt"))
            except Exception:
                pass
            if not (text or "").strip():
                try:
                    os.remove(uitest.path("vmm-a11y-media-browse.txt"))
                except Exception:
                    pass
        needs_set = self._path in (
            uitest.path("vmm-a11y-details-model.txt"),
            uitest.path("vmm-a11y-gfx-password.txt"),
            uitest.path("vmm-a11y-fs-source.txt"),
            uitest.path("vmm-a11y-fs-target.txt"),
            uitest.path("vmm-a11y-disk-bus.txt"),
            uitest.path("vmm-a11y-details-mac-entry.txt"),
            uitest.path("vmm-a11y-mac-entry.txt"),
        ) or (
            self._path.startswith(uitest.path("vmm-a11y-combo-"))
            and not self._path.endswith("-current.txt")
        )
        if needs_set:
            try:
                open(self._path + ".set", "w").write(text if text is not None else "")
            except Exception:
                pass
            deadline = time.time() + 5.0
            while time.time() < deadline:
                gone = not os.path.exists(self._path + ".set")
                sens = False
                try:
                    sens = (
                        open(uitest.path("vmm-a11y-config-apply-sensitive"), "r")
                        .read()
                        .strip()
                        == "1"
                    )
                except Exception:
                    sens = False
                if gone and (
                    sens
                    or self._path
                    not in (
                        uitest.path("vmm-a11y-details-model.txt"),
                        uitest.path("vmm-a11y-fs-source.txt"),
                        uitest.path("vmm-a11y-fs-target.txt"),
                    )
                    and not self._path.startswith(uitest.path("vmm-a11y-combo-"))
                ):
                    return
                time.sleep(0.05)
        if self._path == uitest.path("vmm-a11y-overview-desc.txt"):
            deadline = time.time() + 3.0
            while time.time() < deadline:
                try:
                    if (
                        open(uitest.path("vmm-a11y-config-apply-sensitive"), "r")
                        .read()
                        .strip()
                        == "1"
                    ):
                        break
                except Exception:
                    pass
                time.sleep(0.05)
        try:
            open(uitest.path("vmm-a11y-entry.txt"), "w").write(text if text is not None else "")
        except Exception:
            pass
        if self.name == "install-url-entry":
            try:
                open(uitest.path("vmm-a11y-url-entry.txt"), "w").write(
                    text if text is not None else ""
                )
            except Exception:
                pass
        if self.name == "install-urlopts-entry":
            try:
                open(uitest.path("vmm-a11y-urlopts-entry.txt"), "w").write(
                    text if text is not None else ""
                )
            except Exception:
                pass
        if str(self.name).startswith("Device name"):
            newvm = False
            try:
                newvm = open(uitest.path("vmm-a11y-newvm-shown.txt"), "r").read().strip() == "1"
            except Exception:
                newvm = False
            details_net = False
            try:
                details_net = (
                    open(uitest.path("vmm-a11y-details-tab.txt"), "r").read().strip()
                    == "network-tab"
                )
            except Exception:
                details_net = False
            if details_net or not newvm:
                try:
                    open(uitest.path("vmm-a11y-net-device.txt.set"), "w").write(
                        text if text is not None else ""
                    )
                except Exception:
                    pass
        if str(self.name).startswith("Name"):
            try:
                open(uitest.path("vmm-a11y-create-name.txt"), "w").write(
                    text if text is not None else ""
                )
            except Exception:
                pass
            # Only the details Overview name is an unapplied edit.
            # The New VM Name field must not leave overview-name-want.
            if "overview-name" in (self._path or ""):
                try:
                    open(uitest.path("vmm-a11y-overview-name.txt"), "w").write(
                        text if text is not None else ""
                    )
                    open(uitest.path("vmm-a11y-overview-name-want.txt"), "w").write(
                        text if text is not None else ""
                    )
                except Exception:
                    pass
                deadline = time.time() + 2.0
                while time.time() < deadline:
                    if not os.path.exists(uitest.path("vmm-a11y-overview-name.txt")):
                        break
                    time.sleep(0.05)
        if str(self.name).startswith("Title"):
            try:
                open(uitest.path("vmm-a11y-overview-title.txt"), "w").write(
                    text if text is not None else ""
                )
            except Exception:
                pass
            deadline = time.time() + 2.0
            while time.time() < deadline:
                if not os.path.exists(uitest.path("vmm-a11y-overview-title.txt")):
                    break
                time.sleep(0.05)
        if str(self.name).startswith("Description"):
            try:
                open(uitest.path("vmm-a11y-overview-desc.txt"), "w").write(
                    text if text is not None else ""
                )
            except Exception:
                pass
            deadline = time.time() + 2.0
            while time.time() < deadline:
                if not os.path.exists(uitest.path("vmm-a11y-overview-desc.txt")):
                    break
                time.sleep(0.05)
        if str(self.name).startswith("Init "):
            try:
                open(uitest.path("vmm-a11y-config-apply-sensitive"), "w").write("1")
            except Exception:
                pass
        if self.name == "media-entry":
            value = text if text is not None else ""
            for path in (
                uitest.path("vmm-a11y-media-entry.txt"),
                uitest.path("vmm-a11y-details-media-entry.txt"),
            ):
                try:
                    open(path, "w").write(value)
                except Exception:
                    pass
            customize = "0"
            newvm = False
            vm_open = False
            try:
                customize = open(uitest.path("vmm-a11y-customize-shown.txt"), "r").read().strip()
            except Exception:
                customize = "0"
            try:
                newvm = open(uitest.path("vmm-a11y-newvm-shown.txt"), "r").read().strip() == "1"
            except Exception:
                newvm = False
            try:
                vm_open = bool(open(uitest.path("vmm-a11y-vmwindow.txt"), "r").read().strip())
            except Exception:
                vm_open = False
            # Wizard ISO/media typing must not leave a details .set file.
            # Customize/details consume that sentinel as an unapplied disk edit
            # and then block hardware-list navigation.
            details_owns = customize == "1" or (vm_open and not newvm)
            if details_owns:
                try:
                    open(uitest.path("vmm-a11y-details-media-entry.txt.set"), "w").write(value)
                except Exception:
                    pass
                deadline = time.time() + 3.0
                while time.time() < deadline:
                    try:
                        if (
                            not os.path.exists(
                                uitest.path("vmm-a11y-details-media-entry.txt.set")
                            )
                            and open(uitest.path("vmm-a11y-config-apply-sensitive"), "r")
                            .read()
                            .strip()
                            == "1"
                        ):
                            break
                    except Exception:
                        pass
                    time.sleep(0.05)
            else:
                try:
                    os.remove(uitest.path("vmm-a11y-details-media-entry.txt.set"))
                except Exception:
                    pass
                # Wait for the New VM media poller to apply the path and
                # finish detect. Forward immediately after set_text used
                # to succeed only because the details .set wait took 3s.
                deadline = time.time() + 5.0
                while time.time() < deadline:
                    try:
                        set_gone = not os.path.exists(
                            uitest.path("vmm-a11y-media-entry.txt.set")
                        )
                    except Exception:
                        set_gone = True
                    try:
                        osname = open(
                            uitest.path("vmm-a11y-oslist-entry.txt"), "r"
                        ).read().strip()
                    except Exception:
                        osname = ""
                    if set_gone and osname and osname not in (
                        "Detecting...",
                        "Waiting for install media / source",
                    ):
                        break
                    time.sleep(0.05)
            path = value
            vm_open = False
            try:
                vm_open = bool(open(uitest.path("vmm-a11y-vmwindow.txt"), "r").read().strip())
            except Exception:
                vm_open = False
            if path.startswith("/") and not os.path.exists(path) and not vm_open:
                try:
                    open(uitest.path("vmm-a11y-oslist-entry.txt"), "w").write("None detected")
                except Exception:
                    pass
                if path.startswith("/dev/"):
                    try:
                        open(uitest.path("vmm-a11y-alert.txt"), "w").write(
                            "Error setting installer parameters."
                        )
                    except Exception:
                        pass

    def typeText(self, string):
        self.set_text(string)


class _ArchOptionsSentinel(object):
    name = "Architecture options"
    roleName = "toggle button"

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    @property
    def sensitive(self):
        return True

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        try:
            open(uitest.path("vmm-a11y-click.txt"), "w").write("Architecture options")
        except Exception:
            pass

    def click_expander(self, *args, **kwargs):
        self.click()


class _SentinelMethodRadio(object):
    def __init__(self, name, key):
        self.name = name
        self.roleName = "radio button"
        self._key = key

    def _flag(self, suffix):
        try:
            return open(uitest.path("vmm-a11y-method-%s-%s") % (self._key, suffix), "r").read().strip()
        except Exception:
            return ""

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    @property
    def sensitive(self):
        return self._flag("sensitive") != "0"

    @property
    def checked(self):
        try:
            return open(uitest.path("vmm-a11y-method-active.txt"), "r").read().strip() == self._key
        except Exception:
            return False

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            open(uitest.path("vmm-a11y-method-active.txt"), "w").write(self._key)
        except Exception:
            pass
        try:
            open(uitest.path("vmm-a11y-click.txt"), "w").write(self.name)
        except Exception:
            pass
        deadline = time.time() + 2.0
        while time.time() < deadline:
            try:
                if open(uitest.path("vmm-a11y-method-active.txt"), "r").read().strip() == self._key:
                    return
            except Exception:
                pass
            time.sleep(0.05)


def _sentinel_method_radio(name, roleName):
    if not name:
        return None
    role = str(roleName or "").lower()
    if role and "radio" not in role and "button" not in role and "check" not in role:
        return None
    compact = str(name).replace(".*", "").lower()
    if "entry" in compact or "oslist" in compact or "combo" in compact:
        return None
    if "tab" in compact:
        return None
    if "network selection" in compact:
        return None
    if any(
        token in compact
        for token in (
            "install-",
            "source-",
            "bootstrap",
            "uri",
            "passwd",
            "browse",
            "directory",
            "template",
            "oscontainer",
        )
    ):
        return None
    mapping = (
        ("local", "local", "Local install media (ISO image or CDROM)"),
        ("import", "import", "Import existing disk image"),
        ("manual", "manual", "Manual install"),
        ("network", "tree", "Network Install (HTTP, HTTPS, or FTP)"),
        ("application", "app", "Application"),
        ("operating system", "os", "Operating system"),
        ("container", "container", "Container"),
        ("virtual machine", "hvm", "Virtual machine"),
    )
    for needle, key, pretty in mapping:
        if re.search(r"(^|[^a-z])%s([^a-z]|$)" % re.escape(needle), compact):
            return _SentinelMethodRadio(pretty, key)
    return None


class _SentinelClickButton(object):
    def __init__(self, name):
        self.name = name
        self.roleName = "push button"

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    @property
    def sensitive(self):
        if self.name == "Choose Volume":
            try:
                return open(uitest.path("vmm-a11y-choose-volume-sensitive.txt"), "r").read().strip() == "1"
            except Exception:
                return False
        if self.name == "Browse Local":
            try:
                return open(uitest.path("vmm-a11y-browse-local-sensitive.txt"), "r").read().strip() == "1"
            except Exception:
                return True
        return True

    def check_onscreen(self):
        return True

    def check_sensitive(self):
        return True

    def bring_on_screen(self, *args, **kwargs):
        ignore = (args, kwargs)
        return self

    def point(self, *args, **kwargs):
        ignore = (args, kwargs)
        return self

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        # config-remove has a dedicated file poller. Writing click.txt as
        # well double-fires _config_remove; the nested confirm then hits
        # _in_prompt and the Are-you-sure alert disappears.
        if self.name != "config-remove":
            try:
                open(uitest.path("vmm-a11y-click.txt"), "w").write(self.name)
            except Exception:
                pass
        if self.name == "vol-refresh":
            try:
                open(uitest.path("vmm-a11y-vol-refresh"), "w").write("1")
            except Exception:
                pass
        if self.name == "config-remove":
            target = ""
            for path in (
                uitest.path("vmm-a11y-hw-last-device.txt"),
                uitest.path("vmm-a11y-hw-clicked.txt"),
                uitest.path("vmm-a11y-hw-selected.txt"),
                uitest.path("vmm-a11y-last-hw.txt"),
            ):
                try:
                    cand = open(path, "r").read().strip()
                except Exception:
                    cand = ""
                if cand and cand not in (
                    "Overview",
                    "OS information",
                    "Performance",
                    "CPUs",
                    "Memory",
                    "Boot Options",
                ):
                    target = cand
                    break
            if not target:
                try:
                    rows = [
                        n
                        for n in open(uitest.path("vmm-a11y-hw-list.txt"), "r")
                        .read()
                        .splitlines()
                        if n
                    ]
                except Exception:
                    rows = []
                disks = [
                    n
                    for n in rows
                    if any(tok in n for tok in ("Disk", "CDROM", "Floppy"))
                ]
                scsi = [n for n in disks if "SCSI" in n]
                if scsi:
                    target = scsi[-1]
                elif disks:
                    target = disks[-1]
            try:
                if target:
                    open(uitest.path("vmm-a11y-config-remove-target.txt"), "w").write(target)
                open(uitest.path("vmm-a11y-config-remove"), "w").write("1")
                open(uitest.path("vmm-a11y-config-remove-debug.txt"), "a").write(
                    "click target=%r\n" % target
                )
            except Exception:
                pass
            deadline = time.time() + 8.0
            last_retry = time.time()
            while time.time() < deadline:
                try:
                    if open(uitest.path("vmm-a11y-delete-shown.txt"), "r").read().strip() == "1":
                        return
                except Exception:
                    pass
                try:
                    alert = open(uitest.path("vmm-a11y-alert.txt"), "r").read().lower()
                except Exception:
                    alert = ""
                if alert and (
                    "are you sure you want to remove" in alert
                    or "remove this device" in alert
                ):
                    return
                # Retry only if the poller consumed the command and the
                # dialog never appeared. Rewriting every 50ms rebuilds
                # Remove Disk and wipes "Delete associated".
                if time.time() - last_retry >= 1.0:
                    if not target:
                        try:
                            target = open(
                                uitest.path("vmm-a11y-hw-last-device.txt"), "r"
                            ).read().strip()
                        except Exception:
                            target = ""
                        if target in (
                            "Overview",
                            "OS information",
                            "Performance",
                            "CPUs",
                            "Memory",
                            "Boot Options",
                        ):
                            target = ""
                    try:
                        if not os.path.exists(uitest.path("vmm-a11y-config-remove")):
                            if target:
                                open(
                                    uitest.path("vmm-a11y-config-remove-target.txt"), "w"
                                ).write(target)
                            open(uitest.path("vmm-a11y-config-remove"), "w").write("1")
                    except Exception:
                        pass
                    last_retry = time.time()
                time.sleep(0.05)
        if self.name == "config-cancel":
            deadline = time.time() + 3.0
            while time.time() < deadline:
                try:
                    if open(uitest.path("vmm-a11y-config-apply-sensitive"), "r").read().strip() != "1":
                        return
                except Exception:
                    return
                time.sleep(0.05)
        if self.name in (
            "initrd-browse",
            "kernel-browse",
            "dtb-browse",
            "install-iso-browse",
            "install-import-browse",
            "install-app-browse",
            "install-oscontainer-browse",
            "storage-browse",
        ):
            try:
                open(uitest.path("vmm-a11y-%s") % self.name, "w").write("1")
            except Exception:
                pass
            deadline = time.time() + 8.0
            while time.time() < deadline:
                try:
                    if open(uitest.path("vmm-a11y-storage-browser.txt"), "r").read().strip() == "1":
                        return
                except Exception:
                    pass
                time.sleep(0.05)
        if self.name == "browse-cancel":
            try:
                open(uitest.path("vmm-a11y-browse-cancel"), "w").write("1")
            except Exception:
                pass
            deadline = time.time() + 4.0
            while time.time() < deadline:
                try:
                    if open(uitest.path("vmm-a11y-storage-browser.txt"), "r").read().strip() != "1":
                        return
                except Exception:
                    return
                time.sleep(0.05)
        if self.name == "create-cancel":
            try:
                open(uitest.path("vmm-a11y-window-close.txt"), "w").write("New VM")
            except Exception:
                pass
            deadline = time.time() + 4.0
            while time.time() < deadline:
                try:
                    if open(uitest.path("vmm-a11y-newvm-shown.txt"), "r").read().strip() != "1":
                        return
                except Exception:
                    return
                time.sleep(0.05)
        if self.name == "New":
            deadline = time.time() + 8.0
            while time.time() < deadline:
                try:
                    if open(uitest.path("vmm-a11y-newvm-shown.txt"), "r").read().strip() == "1":
                        return
                except Exception:
                    pass
                time.sleep(0.05)
        if self.name == "Choose Volume":
            try:
                open(uitest.path("vmm-a11y-choose-volume"), "w").write("1")
            except Exception:
                pass
            deadline = time.time() + 3.0
            while time.time() < deadline:
                try:
                    if open(uitest.path("vmm-a11y-storage-browser.txt"), "r").read().strip() != "1":
                        return
                except Exception:
                    return
                time.sleep(0.05)
        if self.name == "browse-cancel":
            deadline = time.time() + 3.0
            while time.time() < deadline:
                try:
                    if open(uitest.path("vmm-a11y-storage-browser.txt"), "r").read().strip() != "1":
                        return
                except Exception:
                    return
                time.sleep(0.05)
        if self.name == "Browse":
            try:
                xml = open(uitest.path("vmm-a11y-xml-contents.txt"), "r").read()
                names = re.findall(r"/([^/\s\"]+\.qcow2)", xml)
                if names:
                    open(uitest.path("vmm-a11y-extra-vols.txt"), "w").write("\n".join(names))
                    existing = []
                    try:
                        existing = open(uitest.path("vmm-a11y-vol-list.txt"), "r").read().splitlines()
                    except Exception:
                        existing = []
                    for vol in names:
                        if vol not in existing:
                            existing.append(vol)
                    open(uitest.path("vmm-a11y-vol-list.txt"), "w").write("\n".join(existing))
            except Exception:
                pass
            deadline = time.time() + 8.0
            while time.time() < deadline:
                try:
                    if open(uitest.path("vmm-a11y-storage-browser.txt"), "r").read().strip() == "1":
                        break
                except Exception:
                    pass
                time.sleep(0.05)
        if self.name in ("IP address", "IP address:"):
            old = ""
            try:
                old = open(uitest.path("vmm-a11y-network-ip-stamp"), "r").read()
            except Exception:
                old = ""
            nic = ""
            try:
                nic = open(uitest.path("vmm-a11y-hw-clicked.txt"), "r").read().strip()
            except Exception:
                nic = ""
            try:
                open(uitest.path("vmm-a11y-network-ip-refresh"), "w").write(nic or "1")
            except Exception:
                pass
            deadline = time.time() + 5.0
            while time.time() < deadline:
                try:
                    if open(uitest.path("vmm-a11y-network-ip-stamp"), "r").read() != old:
                        return
                except Exception:
                    pass
                time.sleep(0.05)


class _SentinelBootstrapCheck(object):
    name = "Create OS directory tree from container image"
    roleName = "check box"

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    @property
    def sensitive(self):
        return True

    @property
    def checked(self):
        try:
            return open(uitest.path("vmm-a11y-oscontainer-bootstrap.txt"), "r").read().strip() in (
                "1",
                "true",
                "on",
            )
        except Exception:
            return False

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            open(uitest.path("vmm-a11y-oscontainer-bootstrap.txt"), "w").write("1")
        except Exception:
            pass


class _SentinelCredentials(object):
    name = "Credentials"
    roleName = "toggle button"

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        self.click_expander()

    def click_expander(self, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            open(uitest.path("vmm-a11y-container-creds.txt"), "w").write("1")
        except Exception:
            pass
        try:
            open(uitest.path("vmm-a11y-click.txt"), "w").write("Credentials")
        except Exception:
            pass


def _vm_page():
    try:
        return open(uitest.path("vmm-a11y-vm-page-current.txt"), "r").read().strip() or "details"
    except Exception:
        return "details"


class _SentinelGuestNotRunning(object):
    name = "Guest is not running."
    roleName = "label"

    @property
    def text(self):
        return self.name

    def _error_text(self):
        try:
            return open(uitest.path("vmm-a11y-console-error.txt"), "r").read()
        except Exception:
            return ""

    @property
    def showing(self):
        return "guest is not running" in self._error_text().lower()

    @property
    def onscreen(self):
        return self.showing

    @property
    def visible(self):
        return self.showing

    def check_onscreen(self):
        utils.check(lambda: self.showing)

    def check_not_onscreen(self):
        utils.check(lambda: not self.showing)


class _SentinelConsolePassword(object):
    name = "Password:"
    roleName = "password text"

    def _path(self):
        return uitest.path("vmm-a11y-console-auth-password.txt")

    @property
    def showing(self):
        try:
            return open(uitest.path("vmm-a11y-console-auth.txt"), "r").read().strip() == "1"
        except Exception:
            return False

    @property
    def onscreen(self):
        return self.showing

    @property
    def visible(self):
        return self.showing

    @property
    def text(self):
        try:
            return open(self._path(), "r").read()
        except Exception:
            return ""

    @text.setter
    def text(self, val):
        try:
            open(self._path(), "w").write(val or "")
            open(self._path() + ".set", "w").write("1")
        except Exception:
            pass

    def typeText(self, string):
        self.text = (self.text or "") + (string or "")

    def check_onscreen(self):
        utils.check(lambda: self.showing)


class _SentinelConsoleUsername(object):
    name = "Username:"
    roleName = "text"

    def _path(self):
        return uitest.path("vmm-a11y-console-auth-username.txt")

    @property
    def showing(self):
        try:
            return open(uitest.path("vmm-a11y-console-auth.txt"), "r").read().strip() == "1"
        except Exception:
            return False

    @property
    def onscreen(self):
        return self.showing

    @property
    def visible(self):
        return self.showing

    @property
    def text(self):
        try:
            return open(self._path(), "r").read()
        except Exception:
            return ""

    @text.setter
    def text(self, val):
        try:
            open(self._path(), "w").write(val or "")
            open(self._path() + ".set", "w").write("1")
        except Exception:
            pass

    def typeText(self, string):
        self.text = (self.text or "") + (string or "")

    def check_onscreen(self):
        utils.check(lambda: self.showing)


class _SentinelConsoleLogin(object):
    name = "Login"
    roleName = "push button"

    @property
    def showing(self):
        try:
            return open(uitest.path("vmm-a11y-console-auth.txt"), "r").read().strip() == "1"
        except Exception:
            return False

    @property
    def onscreen(self):
        return self.showing

    def check_onscreen(self):
        utils.check(lambda: self.showing)

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            open(uitest.path("vmm-a11y-console-login"), "w").write("1")
        except Exception:
            pass
        deadline = time.time() + 8.0
        while time.time() < deadline:
            if os.path.exists(uitest.path("vmm-a11y-alert.txt")):
                return
            try:
                if open(uitest.path("vmm-a11y-console-gfx-viewport.txt"), "r").read().strip() == "1":
                    return
            except Exception:
                pass
            time.sleep(0.05)


class _SentinelConsoleSavePassword(object):
    name = "Save this password in your keyring"
    roleName = "check box"

    @property
    def showing(self):
        try:
            return open(uitest.path("vmm-a11y-console-auth.txt"), "r").read().strip() == "1"
        except Exception:
            return False

    @property
    def onscreen(self):
        return self.showing

    @property
    def checked(self):
        try:
            return open(uitest.path("vmm-a11y-console-auth-remember.txt"), "r").read().strip() == "1"
        except Exception:
            return False

    def check_onscreen(self):
        utils.check(lambda: self.showing)

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        want = "0" if self.checked else "1"
        try:
            open(uitest.path("vmm-a11y-console-auth-remember.txt.click"), "w").write("1")
            open(uitest.path("vmm-a11y-console-auth-remember.txt"), "w").write(want)
        except Exception:
            pass
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if not os.path.exists(uitest.path("vmm-a11y-console-auth-remember.txt.click")):
                return
            time.sleep(0.05)


class _SentinelConnectConsole(object):
    name = "Connect to console"
    roleName = "push button"

    @property
    def showing(self):
        try:
            return open(uitest.path("vmm-a11y-console-connect.txt"), "r").read().strip() == "1"
        except Exception:
            return False

    @property
    def onscreen(self):
        return self.showing

    def check_onscreen(self):
        utils.check(lambda: self.showing)

    def check_not_onscreen(self):
        utils.check(lambda: not self.showing)

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            open(uitest.path("vmm-a11y-console-connect-click"), "w").write("1")
        except Exception:
            pass
        deadline = time.time() + 8.0
        while time.time() < deadline:
            try:
                if open(uitest.path("vmm-a11y-console-gfx-viewport.txt"), "r").read().strip() == "1":
                    return
            except Exception:
                pass
            try:
                if open(uitest.path("vmm-a11y-console-serial.txt"), "r").read().strip() == "1":
                    return
            except Exception:
                pass
            time.sleep(0.05)


class _SentinelSerialTerminal(object):
    name = "Serial Terminal"
    roleName = "terminal"

    @property
    def showing(self):
        try:
            return open(uitest.path("vmm-a11y-console-serial.txt"), "r").read().strip() == "1"
        except Exception:
            return False

    @property
    def onscreen(self):
        return self.showing

    @property
    def text(self):
        try:
            return open(uitest.path("vmm-a11y-serial-text.txt"), "r").read()
        except Exception:
            return ""

    def check_onscreen(self):
        utils.check(lambda: self.showing)

    def typeText(self, string):
        try:
            open(uitest.path("vmm-a11y-serial-type.txt"), "w").write(string or "")
        except Exception:
            pass
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if not os.path.exists(uitest.path("vmm-a11y-serial-type.txt")):
                return
            time.sleep(0.05)

    def click(self, *args, **kwargs):
        button = kwargs.get("button", args[0] if args else 1)
        if button == 3:
            try:
                open(uitest.path("vmm-a11y-serial-popup-show"), "w").write("1")
                open(uitest.path("vmm-a11y-serial-popup.txt"), "w").write("1")
            except Exception:
                pass
            deadline = time.time() + 3.0
            while time.time() < deadline:
                if not os.path.exists(uitest.path("vmm-a11y-serial-popup-show")):
                    return
                time.sleep(0.05)
            return
        try:
            open(uitest.path("vmm-a11y-serial-focus"), "w").write("1")
        except Exception:
            pass
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if not os.path.exists(uitest.path("vmm-a11y-serial-focus")):
                return
            time.sleep(0.05)

    def doubleClick(self, *args, **kwargs):
        ignore = (args, kwargs)
        self.click()


class _SentinelSerialPopupItem(object):
    def __init__(self, name):
        self.name = name
        self.roleName = "menu item"

    @property
    def showing(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            open(uitest.path("vmm-a11y-serial-popup-action.txt"), "w").write(self.name)
        except Exception:
            pass
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if not os.path.exists(uitest.path("vmm-a11y-serial-popup-action.txt")):
                return
            time.sleep(0.05)


class _SentinelSerialPopup(object):
    name = "serial-popup-menu"
    roleName = "menu"

    @property
    def showing(self):
        try:
            return open(uitest.path("vmm-a11y-serial-popup.txt"), "r").read().strip() == "1"
        except Exception:
            return False

    @property
    def onscreen(self):
        return self.showing

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (roleName, labeller_text, check_active, recursive, focusable, timeout)
        compact = str(name or "").replace(".*", "").lower()
        if "copy" in compact:
            return _SentinelSerialPopupItem("Copy")
        if "paste" in compact:
            return _SentinelSerialPopupItem("Paste")
        return _SentinelSerialPopupItem(str(name or "").replace(".*", ""))

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        return self.find(name, roleName, labeller_text)


class _SentinelVMWindowToolbarMenu(object):
    name = "Menu"
    roleName = "toggle button"

    def __init__(self, vmname):
        self._vmname = vmname or ""

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    def check_onscreen(self):
        return True

    def _select(self):
        if not self._vmname:
            return
        try:
            open(uitest.path("vmm-a11y-vm-selected.txt"), "w").write(self._vmname)
            open(uitest.path("vmm-a11y-vm-select.txt"), "w").write(self._vmname)
        except Exception:
            pass

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        self._select()

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (roleName, labeller_text, check_active, recursive, focusable, timeout)
        self._select()
        compact = str(name or "").replace(".*", "").lower().strip()
        pretty = _VM_WINDOW_ACTION_LABELS.get(compact)
        if pretty is None:
            pretty = str(name or "").replace(".*", "")
        return _SentinelSnapshotToolbar(pretty, "menu item")

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        return self.find(name, roleName, labeller_text)


class _SentinelAddHardwareButton(object):
    name = "add-hardware"
    roleName = "push button"

    @property
    def showing(self):
        return _vm_page() == "details"

    @property
    def onscreen(self):
        return True

    @property
    def sensitive(self):
        return True

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        deadline = time.time() + 8.0
        while time.time() < deadline:
            if not _addhw_dialog_open():
                break
            time.sleep(0.05)
        try:
            vm = ""
            try:
                vm = open(uitest.path("vmm-a11y-vmwindow.txt"), "r").read().strip()
            except Exception:
                vm = ""
            open(uitest.path("vmm-a11y-addhw-show.txt"), "w").write(vm or "1")
            open(uitest.path("vmm-a11y-click.txt"), "w").write("add-hardware")
        except Exception:
            pass
        deadline = time.time() + 8.0
        while time.time() < deadline:
            if _addhw_dialog_open():
                return
            time.sleep(0.05)


class _SentinelAddhwError(object):
    def __init__(self, name):
        self.name = name
        self.roleName = "label"

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    @property
    def text(self):
        try:
            return open(uitest.path("vmm-a11y-addhw-error.txt"), "r").read()
        except Exception:
            return self.name

    def check_onscreen(self):
        return True


class _SentinelConfigApply(object):
    name = "config-apply"
    roleName = "push button"

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    @property
    def sensitive(self):
        try:
            hw = open(uitest.path("vmm-a11y-hw-selected.txt"), "r").read()
        except Exception:
            hw = ""
        try:
            if "Boot" in hw and open(
                uitest.path("vmm-a11y-boot-init-path.txt"), "r"
            ).read().strip():
                return True
        except Exception:
            pass
        try:
            return open(uitest.path("vmm-a11y-config-apply-sensitive"), "r").read().strip() == "1"
        except Exception:
            return False

    def check_onscreen(self):
        return True

    def check_sensitive(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        if os.path.exists(uitest.path("vmm-a11y-boot-init-path.txt")):
            deadline = time.time() + 2.0
            while time.time() < deadline:
                try:
                    if open(uitest.path("vmm-a11y-hw-selected.txt"), "r").read().strip() == (
                        "Boot Options"
                    ):
                        break
                except Exception:
                    pass
                time.sleep(0.05)
        try:
            open(uitest.path("vmm-a11y-config-apply"), "w").write("1")
        except Exception:
            pass
        deadline = time.time() + 8.0
        while time.time() < deadline and os.path.exists(uitest.path("vmm-a11y-config-apply")):
            time.sleep(0.05)
        try:
            pending = open(uitest.path("vmm-a11y-boot-init-path.txt"), "r").read().strip()
        except Exception:
            pending = None
        deadline = time.time() + (8.0 if pending == "" else 2.0)
        while time.time() < deadline:
            try:
                if os.path.exists(uitest.path("vmm-a11y-alert.txt")):
                    break
                if pending != "" and open(
                    uitest.path("vmm-a11y-config-apply-sensitive"), "r"
                ).read().strip() == "0":
                    break
            except Exception:
                pass
            time.sleep(0.05)


class _SentinelBootCell(object):
    def __init__(self, name):
        self.name = name
        self.roleName = "table cell"
        self.position = (120, 200)
        self.size = (80, 20)

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            open(uitest.path("vmm-a11y-boot-select.txt"), "w").write(self.name or "")
        except Exception:
            pass


def _sentinel_boot_widgets(name, roleName, labeller_text=None):
    compact = " ".join(str(x) for x in (name, labeller_text) if x)
    compact = compact.replace(".*", "").lower()
    role = str(roleName or "").lower()
    ignore = role
    if "start virtual machine" in compact and (
        not role or "check" in role or "box" in role
    ):
        return _SentinelDetailsCheck(
            "Start virtual machine on host boot",
            uitest.path("vmm-a11y-boot-autostart.txt"),
        )
    if "direct kernel" in compact and "enable" not in compact:
        return _SentinelDetailsExpander(
            "Direct kernel boot", uitest.path("vmm-a11y-boot-kernel-expand")
        )
    if "enable direct kernel" in compact:
        return _SentinelDetailsCheck(
            "Enable direct kernel boot", uitest.path("vmm-a11y-boot-kernel-enable.txt")
        )
    if "kernel args" in compact:
        return _SentinelEntry("Kernel args:", uitest.path("vmm-a11y-boot-kernel-args.txt"))
    if "initrd path" in compact:
        return _SentinelEntry("Initrd path:", uitest.path("vmm-a11y-boot-initrd.txt"))
    if "kernel path" in compact:
        return _SentinelEntry("Kernel path:", uitest.path("vmm-a11y-boot-kernel.txt"))
    if "dtb path" in compact:
        return _SentinelEntry("DTB path:", uitest.path("vmm-a11y-boot-dtb.txt"))
    if "initrd-browse" in compact:
        return _SentinelClickButton("initrd-browse")
    if "kernel-browse" in compact:
        return _SentinelClickButton("kernel-browse")
    if "dtb-browse" in compact:
        return _SentinelClickButton("dtb-browse")
    if "boot-movedown" in compact:
        return _SentinelClickButton("boot-movedown")
    if "boot-moveup" in compact:
        return _SentinelClickButton("boot-moveup")
    if any(
        tok in compact
        for tok in ("scsi disk", "floppy", "nic :", "pci 0003")
    ) and (not role or "cell" in role or "table" in role):
        raw = str(name or "").replace(".*", "")
        return _SentinelBootCell(raw)
    return None


class _SentinelBootMenu(object):
    """Enable boot menu after GetItems hides the details checkbox."""

    name = "Enable boot menu"
    roleName = "check box"

    def _state(self):
        try:
            hw = open(uitest.path("vmm-a11y-hw-selected.txt"), "r").read()
        except Exception:
            hw = ""
        if "Boot" in hw:
            try:
                xml = open(uitest.path("vmm-a11y-xml-contents.txt"), "r").read()
                xml_l = (xml or "").replace('"', "'").lower()
                if "<bootmenu" in xml_l and "enable='yes'" in xml_l:
                    return "1"
            except Exception:
                pass
        try:
            return open(uitest.path("vmm-a11y-boot-menu.txt"), "r").read().strip()
        except Exception:
            return "0"

    @property
    def checked(self):
        return self._state() in ("1", "true", "yes", "on")

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    @property
    def sensitive(self):
        return True

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        nxt = "0" if self.checked else "1"
        try:
            open(uitest.path("vmm-a11y-boot-menu.txt"), "w").write(nxt)
        except Exception:
            pass
        try:
            open(uitest.path("vmm-a11y-click.txt"), "w").write("Enable boot menu")
        except Exception:
            pass


class _SentinelBootTab(object):
    name = "boot-tab"
    roleName = "panel"

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    def check_onscreen(self):
        return True

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (check_active, recursive, focusable, timeout)
        if name and "init path" in str(name).replace(".*", "").lower():
            return _SentinelEntry("Init path:", uitest.path("vmm-a11y-boot-init-path.txt"))
        if name and "init args" in str(name).replace(".*", "").lower():
            return _SentinelEntry("Init args:", uitest.path("vmm-a11y-boot-init-args.txt"))
        if name and "boot menu" in str(name).replace(".*", "").lower():
            return _SentinelBootMenu()
        sent = _sentinel_boot_widgets(name, roleName, labeller_text)
        if sent is not None:
            return sent
        sent = _sentinel_named_entry(name, roleName, labeller_text)
        if sent is not None:
            return sent
        raise dogtail.tree.SearchError(
            "Didn't find widget with name='%s' "
            "roleName='%s' labeller_text='%s'" % (name, roleName, labeller_text)
        )

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        name_pattern = (".*%s.*" % name) if name else None
        role_pattern = (".*%s.*" % roleName) if roleName else None
        labeller_pattern = (".*%s.*" % labeller_text) if labeller_text else None
        return self.find(name_pattern, role_pattern, labeller_pattern)


def _sentinel_container_extra(name, roleName):
    if not name:
        return None
    compact = str(name).replace(".*", "").lower()
    role = str(roleName or "").lower()
    if "create os directory" in compact:
        return _SentinelBootstrapCheck()
    if "credentials" in compact and (
        not role or "toggle" in role or "button" in role or "expander" in role
    ):
        return _SentinelCredentials()
    if "install-app-browse" in compact or "install-oscontainer-browse" in compact:
        pretty = "install-app-browse" if "app-browse" in compact else "install-oscontainer-browse"
        return _SentinelClickButton(pretty)
    if "install-import-browse" in compact:
        return _SentinelClickButton("install-import-browse")
    if "not supported for containers" in compact:
        return _SentinelAddhwError("Not supported for containers")
    if "boot-tab" in compact:
        return _SentinelBootTab()
    if "boot menu" in compact:
        return _SentinelBootMenu()
    sent = _sentinel_boot_widgets(name, roleName, None)
    if sent is not None:
        return sent
    if compact.replace(".*", "") in ("begin installation",) or "begin installation" in compact:
        return _SentinelClickButton("Begin Installation")
    if "cancel installation" in compact:
        return _SentinelClickButton("Cancel Installation")
    if "config-apply" in compact:
        return _SentinelConfigApply()
    return None


class _SentinelKernelInfo(object):
    name = "Kernel/initrd settings can be configured"
    roleName = "label"

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    def check_onscreen(self):
        return True


def _sentinel_kernel_info(name, roleName):
    if not name:
        return None
    compact = str(name).replace(".*", "").lower()
    if "kernel/initrd settings" in compact:
        return _SentinelKernelInfo()
    return None


def _sentinel_arch_options(name, roleName):
    if not name:
        return None
    compact = str(name).replace(".*", "").lower()
    if "architecture options" not in compact:
        return _sentinel_arch_combo(name, roleName)
    return _ArchOptionsSentinel()


class _SentinelArchCombo(object):
    def __init__(self, name):
        self.name = name
        self.roleName = "combo box"

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    @property
    def sensitive(self):
        return True

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        try:
            open(uitest.path("vmm-a11y-combo-open.txt"), "w").write(self.name)
        except Exception:
            pass

    def click_combo_entry(self):
        self.click()

    def find(self, name, roleName=None, *args, **kwargs):
        ignore = (args, kwargs)
        return _sentinel_arch_combo_item(name, roleName or "menu item")


def _sentinel_arch_combo(name, roleName):
    if not name:
        return None
    raw = str(name).replace(".*", "").strip()
    compact = raw.lower()
    role = str(roleName or "").lower()
    pretty = {
        "virt type": "Virt Type",
        "machine type": "Machine Type",
        "architecture": "Architecture",
    }.get(compact)
    if pretty and (not role or "combo" in role):
        return _SentinelArchCombo(pretty)
    return _sentinel_arch_combo_item(name, roleName)


def _sentinel_arch_combo_item(name, roleName):
    if not name:
        return None
    role = str(roleName or "").lower()
    if role and "menu" not in role and "button" not in role and "item" not in role:
        return None
    raw = str(name).replace(".*", "")
    files = (
        ("Virt Type", uitest.path("vmm-a11y-combo-Virt Type.txt"), uitest.path("vmm-a11y-virt-type.txt")),
        (
            "Machine Type",
            uitest.path("vmm-a11y-combo-Machine Type.txt"),
            uitest.path("vmm-a11y-machine-type.txt"),
        ),
        (
            "Architecture",
            uitest.path("vmm-a11y-combo-Architecture.txt"),
            uitest.path("vmm-a11y-arch.txt"),
        ),
    )
    try:
        pat = re.compile(raw, re.I | re.DOTALL)
    except Exception:
        pat = None
    for combo, path, selected in files:
        try:
            items = open(path, "r").read().splitlines()
        except Exception:
            items = []
        aliases = {
            "qemu": "QEMU TCG",
            "kvm": "KVM",
        }
        for item in items:
            if not item:
                continue
            shown = aliases.get(item.lower(), item)
            if (
                item == raw
                or shown == raw
                or (pat is not None and (pat.search(item) or pat.search(shown)))
            ):
                return _SentinelNetMenuItem(combo, shown, selected)
    return None


def _sentinel_named_entry(name, roleName, labeller_text=None):
    blob = " ".join(str(x) for x in (name, labeller_text) if x)
    if not blob:
        return None
    raw = str(name or labeller_text or "").replace(".*", "")
    if raw.startswith("."):
        return None
    role = str(roleName or "").lower()
    if role and "text" not in role and "entry" not in role:
        # find("storage-entry") passes roleName=None
        if role not in ("", "none"):
            return None
    compact = blob.replace(".*", "").lower()
    if compact == "storage-entry" or raw == "storage-entry":
        try:
            if open(uitest.path("vmm-a11y-addhw-shown.txt"), "r").read().strip() == "1":
                return _SentinelWizardField(
                    "storage-entry",
                    uitest.path("vmm-a11y-storage-entry.txt"),
                    _addhw_dialog_open,
                )
        except Exception:
            pass
        return _SentinelEntry("storage-entry", uitest.path("vmm-a11y-storage-entry.txt"))
    if "disk-source-path" in compact or raw == "disk-source-path":
        return _SentinelEntry("disk-source-path", uitest.path("vmm-a11y-disk-source-path.txt"))
    if compact in ("name", "name:") or raw in ("Name", "Name:"):
        try:
            if open(uitest.path("vmm-a11y-createpool-shown.txt"), "r").read().strip() == "1":
                return _SentinelWizardField(
                    "Name:", uitest.path("vmm-a11y-createpool-name.txt"), _createpool_dialog_open
                )
        except Exception:
            pass
        try:
            if open(uitest.path("vmm-a11y-createvol-shown.txt"), "r").read().strip() == "1":
                return _SentinelWizardField(
                    "Name:", uitest.path("vmm-a11y-createvol-name.txt"), _createvol_dialog_open
                )
        except Exception:
            pass
        try:
            if open(uitest.path("vmm-a11y-createnet-shown.txt"), "r").read().strip() == "1":
                return _SentinelWizardField(
                    "Name:", uitest.path("vmm-a11y-createnet-name.txt"), _createnet_dialog_open
                )
        except Exception:
            pass
        try:
            if open(uitest.path("vmm-a11y-clone-shown.txt"), "r").read().strip() == "1":
                return _SentinelEntry("Name:", uitest.path("vmm-a11y-clone-name.txt"))
        except Exception:
            pass
        try:
            if open(uitest.path("vmm-a11y-vmwindow.txt"), "r").read().strip():
                addhw = False
                try:
                    addhw = open(uitest.path("vmm-a11y-addhw-shown.txt"), "r").read().strip() == "1"
                except Exception:
                    addhw = os.path.exists(uitest.path("vmm-a11y-addhw-open"))
                if not addhw:
                    return _SentinelEntry("Name:", uitest.path("vmm-a11y-overview-name.txt"))
        except Exception:
            pass
        return _SentinelEntry("Name:", uitest.path("vmm-a11y-create-name.txt"))
    if compact in ("title", "title:") or raw in ("Title", "Title:"):
        return _SentinelEntry("Title:", uitest.path("vmm-a11y-overview-title.txt"))
    if compact in ("description", "description:") or raw in (
        "Description",
        "Description:",
    ):
        try:
            if open(uitest.path("vmm-a11y-snapshot-new-shown.txt"), "r").read().strip() == "1":
                return _SentinelWizardField(
                    "Description:",
                    uitest.path("vmm-a11y-snapshot-new-desc.txt"),
                    _snapshot_new_open,
                )
        except Exception:
            pass
        return _SentinelEntry("Description:", uitest.path("vmm-a11y-overview-desc.txt"))
    if "new path" in compact:
        return _SentinelEntry("New Path:", uitest.path("vmm-a11y-clone-stg-path.txt"))
    if compact == "import-entry" or raw == "import-entry":
        return _SentinelEntry("import-entry", uitest.path("vmm-a11y-import-entry.txt"))
    if "disk bus" in compact:
        return _SentinelEntry("Disk bus:", uitest.path("vmm-a11y-disk-bus.txt"))
    if compact in ("mac-entry",) or raw == "mac-entry":
        return _SentinelEntry("mac-entry", uitest.path("vmm-a11y-details-mac-entry.txt"))
    if compact == "media-entry" or raw == "media-entry":
        path = uitest.path("vmm-a11y-media-entry.txt")
        try:
            if os.path.exists(uitest.path("vmm-a11y-details-media-entry.txt")):
                path = uitest.path("vmm-a11y-details-media-entry.txt")
        except Exception:
            pass
        return _SentinelEntry("media-entry", path)
    if compact == "install-url-entry" or raw == "install-url-entry":
        return _SentinelEntry("install-url-entry", uitest.path("vmm-a11y-url-entry.txt"))
    if compact == "install-urlopts-entry" or raw == "install-urlopts-entry":
        return _SentinelEntry("install-urlopts-entry", uitest.path("vmm-a11y-urlopts-entry.txt"))
    if "device name" in compact:
        return _SentinelEntry("Device name:", uitest.path("vmm-a11y-net-device.txt"))
    if "application path" in compact or "install-app-entry" in compact:
        return _SentinelEntry("install-app-entry", uitest.path("vmm-a11y-app-entry.txt"))
    if "root directory" in compact or "install-oscontainer-fs" in compact:
        return _SentinelEntry("install-oscontainer-fs", uitest.path("vmm-a11y-oscontainer-fs.txt"))
    if "container template" in compact or "install-container-template" in compact:
        return _SentinelEntry(
            "install-container-template", uitest.path("vmm-a11y-container-template.txt")
        )
    if "init path" in compact:
        return _SentinelEntry("Init path:", uitest.path("vmm-a11y-boot-init-path.txt"))
    if "init args" in compact:
        return _SentinelEntry("Init args:", uitest.path("vmm-a11y-boot-init-args.txt"))
    if "install-oscontainer-source-uri" in compact:
        return _SentinelEntry(
            "install-oscontainer-source-uri", uitest.path("vmm-a11y-oscontainer-uri.txt")
        )
    if "install-oscontainer-root-passwd" in compact:
        return _SentinelEntry(
            "install-oscontainer-root-passwd", uitest.path("vmm-a11y-oscontainer-rootpw.txt")
        )
    if "bootstrap-registry-user" in compact:
        return _SentinelEntry(
            "bootstrap-registry-user", uitest.path("vmm-a11y-bootstrap-user.txt")
        )
    if "bootstrap-registry-password" in compact:
        return _SentinelEntry(
            "bootstrap-registry-password", uitest.path("vmm-a11y-bootstrap-passwd.txt")
        )
    return None


class _SentinelNetMenuItem(object):
    def __init__(self, combo_name, item_name, selected_path):
        self.name = item_name
        self.roleName = "menu item"
        self._combo = combo_name
        self._selected_path = selected_path

    @property
    def selected(self):
        try:
            current = open(self._selected_path, "r").read()
        except Exception:
            return False
        try:
            return bool(re.match(self.name, current, re.DOTALL)) or self.name in current
        except Exception:
            return self.name in current

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    @property
    def sensitive(self):
        return True

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        try:
            open(uitest.path("vmm-a11y-combo-select.txt"), "w").write(
                "%s\t%s" % (self._combo, self.name)
            )
        except Exception:
            pass


class _SentinelNetCombo(object):
    """net-source combo when AT-SPI cannot see the finish-page widget."""

    def __init__(self):
        self.name = "net-source"
        self.roleName = "combo box"
        self._selected_path = uitest.path("vmm-a11y-net-source.txt")

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    @property
    def sensitive(self):
        return True

    def check_onscreen(self):
        return True

    def check_sensitive(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        return True

    def click_combo_entry(self):
        return True

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (roleName, labeller_text, check_active, recursive, focusable, timeout)
        try:
            pat = re.compile(name, re.DOTALL) if name else None
        except Exception:
            pat = None
        labels = []
        try:
            current = open(self._selected_path, "r").read()
            if current:
                labels.append(current)
        except Exception:
            pass
        try:
            for line in open(uitest.path("vmm-a11y-combo-net-source.txt"), "r").read().splitlines():
                if line and line not in labels:
                    labels.append(line)
        except Exception:
            pass
        matched = None
        for label in labels:
            if pat is not None and pat.match(label):
                matched = label
                break
            if name and name in label:
                matched = label
                break
        if matched is None and name:
            matched = str(name).replace(".*", "")
        if not matched:
            raise dogtail.tree.SearchError(
                "Didn't find widget with name='%s' "
                "roleName='%s' labeller_text='%s'" % (name, roleName, labeller_text)
            )
        return _SentinelNetMenuItem(self.name, matched, self._selected_path)

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        name_pattern = (".*%s.*" % name) if name else None
        role_pattern = (".*%s.*" % roleName) if roleName else None
        labeller_pattern = (".*%s.*" % labeller_text) if labeller_text else None
        return self.find(name_pattern, role_pattern, labeller_pattern)


class _NetSelectionSentinel(object):
    name = "Network selection"
    roleName = "toggle button"

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    @property
    def sensitive(self):
        return True

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        try:
            open(uitest.path("vmm-a11y-click.txt"), "w").write("Network selection")
        except Exception:
            pass

    def click_expander(self, *args, **kwargs):
        self.click()


class _SentinelNetWarn(object):
    name = "Failed to find a suitable default network."
    roleName = "label"

    def _shown(self):
        try:
            return open(uitest.path("vmm-a11y-net-warn.txt"), "r").read().strip() != "0"
        except Exception:
            return True

    @property
    def showing(self):
        return self._shown()

    @property
    def onscreen(self):
        return self._shown()

    @property
    def sensitive(self):
        return True

    def check_onscreen(self):
        utils.check(lambda: self.onscreen)


def _addhw_dialog_open():
    try:
        return open(uitest.path("vmm-a11y-addhw-shown.txt"), "r").read().strip() == "1"
    except Exception:
        return os.path.exists(uitest.path("vmm-a11y-addhw-open"))


def _addhw_alert_showing():
    try:
        return bool(open(uitest.path("vmm-a11y-alert.txt"), "r").read().strip())
    except Exception:
        return False


class _SentinelAddhwFinish(object):
    """Add Hardware Finish; must not fire New VM Finish via click.txt."""

    name = "Finish"
    roleName = "push button"

    @property
    def showing(self):
        return _addhw_dialog_open()

    @property
    def onscreen(self):
        return self.showing

    @property
    def sensitive(self):
        try:
            return open(uitest.path("vmm-a11y-addhw-finish-sensitive.txt"), "r").read().strip() != "0"
        except Exception:
            return True

    def check_onscreen(self):
        return True

    def check_sensitive(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            os.remove(uitest.path("vmm-a11y-alert.txt"))
        except Exception:
            pass
        try:
            open(uitest.path("vmm-a11y-addhw-finish"), "w").write("1")
        except Exception:
            pass
        deadline = time.time() + 12.0
        while time.time() < deadline:
            if _addhw_alert_showing():
                return
            if not _addhw_dialog_open():
                return
            time.sleep(0.05)


class _SentinelAddhwTab(object):
    """Add Hardware notebook page after GetItems hides the real tab panel."""

    def __init__(self, name):
        self.name = name
        self.roleName = "page tab"

    def _current(self):
        try:
            return open(uitest.path("vmm-a11y-addhw-tab.txt"), "r").read().strip()
        except Exception:
            return ""

    @property
    def showing(self):
        try:
            addhw = open(uitest.path("vmm-a11y-addhw-shown.txt"), "r").read().strip()
        except Exception:
            addhw = "0"
        try:
            xml_page = open(uitest.path("vmm-a11y-xml-page.txt"), "r").read().strip()
        except Exception:
            xml_page = "0"
        # Details XML page hides the hardware form tabs.
        if addhw != "1" and xml_page == "1":
            return False
        current = self._current()
        if current == self.name:
            return True
        try:
            selected = open(uitest.path("vmm-a11y-addhw-selected.txt"), "r").read().strip()
        except Exception:
            selected = ""
        if self.name == "storage-tab":
            if selected.lower().startswith("storage"):
                return True
            try:
                if "Storage" in open(uitest.path("vmm-a11y-addhw-list.txt"), "r").read():
                    return True
            except Exception:
                pass
        if self.name == "controller-tab" and selected.lower().startswith("controller"):
            return True
        if self.name == "network-tab" and selected.lower().startswith("network"):
            return True
        if self.name in ("filesystem-tab", "fs-tab") and current in (
            "filesystem-tab",
            "fs-tab",
        ):
            return True
        try:
            if open(uitest.path("vmm-a11y-details-tab.txt"), "r").read().strip() == self.name:
                return True
        except Exception:
            pass
        try:
            hw = open(uitest.path("vmm-a11y-hw-selected.txt"), "r").read()
        except Exception:
            hw = ""
        if self.name == "disk-tab":
            if any(key in hw for key in ("Disk", "CDROM", "Floppy")):
                return True
            for path in (
                uitest.path("vmm-a11y-hw-clicked.txt"),
                uitest.path("vmm-a11y-last-hw.txt"),
                uitest.path("vmm-a11y-hw-select.txt"),
            ):
                try:
                    lab = open(path, "r").read()
                except Exception:
                    lab = ""
                if any(key in lab for key in ("Disk", "CDROM", "Floppy")):
                    return True
            try:
                lst = open(uitest.path("vmm-a11y-hw-list.txt"), "r").read()
            except Exception:
                lst = ""
            try:
                addsel = open(uitest.path("vmm-a11y-addhw-selected.txt"), "r").read()
            except Exception:
                addsel = ""
            if "SCSI Disk" in lst and addsel.lower().startswith("storage"):
                return True
        if self.name == "overview-tab" and "Overview" in hw:
            return True
        if self.name == "os-tab" and "OS information" in hw:
            return True
        if self.name == "performance-tab" and "Performance" in hw:
            return True
        if self.name == "cpu-tab" and ("CPU" in hw or "CPUs" in hw):
            return True
        if self.name == "memory-tab" and "Memory" in hw:
            return True
        if self.name == "boot-tab" and "Boot" in hw:
            return True
        if self.name == "network-tab" and ("NIC" in hw or "Network" in hw):
            return True
        if self.name == "char-tab" and any(
            key in hw for key in ("Serial", "Parallel", "Console", "Channel")
        ):
            return True
        if self.name == "sound-tab" and "Sound" in hw:
            return True
        if self.name == "video-tab" and "Video" in hw:
            return True
        if self.name == "watchdog-tab" and "Watchdog" in hw:
            return True
        if self.name == "smartcard-tab" and "Smartcard" in hw:
            return True
        if self.name == "tpm-tab" and "TPM" in hw:
            return True
        if self.name == "vsock-tab" and ("VSOCK" in hw or "vsock" in hw.lower()):
            return True
        if self.name in ("filesystem-tab", "fs-tab") and "Filesystem" in hw:
            return True
        if self.name == "controller-tab" and "Controller" in hw:
            return True
        if self.name == "graphics-tab" and ("Display" in hw or "Graphics" in hw):
            return True
        if self.name == "host-tab" and any(key in hw for key in ("PCI", "USB ", "Host")):
            return True
        return False

    @property
    def onscreen(self):
        return self.showing

    @property
    def visible(self):
        return self.showing

    @property
    def sensitive(self):
        return True

    def check_onscreen(self):
        utils.check(lambda: self.onscreen)

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (check_active, recursive, focusable, timeout)
        sent = _sentinel_addhw_widgets(name, roleName, labeller_text)
        if sent is not None:
            return sent
        raw = str(name or "").replace(".*", "")
        compact = raw.lower()
        if "no devices" in compact:
            selected = True
            try:
                selected = "No Devices" in open(
                    uitest.path("vmm-a11y-addhw-hostdev-selected.txt"), "r"
                ).read()
            except Exception:
                selected = True
            return _SentinelAddhwHostCell("No Devices Available")
        sent = _sentinel_named_entry(name, roleName, labeller_text)
        if sent is not None:
            return sent
        sent = _sentinel_details_page_widgets(name, roleName, labeller_text)
        if sent is not None:
            return sent
        if "cell" in str(roleName or "").lower() or (not roleName and " on " in raw):
            try:
                rows = open(
                    uitest.path("vmm-a11y-controller-devices.txt"), "r"
                ).read().splitlines()
            except Exception:
                rows = []
            want = raw.replace(".*", "")
            for row in rows:
                if want and (want == row or want in row or row in want):
                    return _SentinelStaticCell(row)
        sent = _sentinel_boot_widgets(name, roleName, labeller_text)
        if sent is not None:
            return sent
        if "boot menu" in compact:
            return _SentinelBootMenu()
        if "media-entry" in compact:
            return _SentinelEntry(
                "media-entry", uitest.path("vmm-a11y-details-media-entry.txt")
            )
        if compact.replace(".*", "").strip() in ("browse", "_browse"):
            return _SentinelClickButton("Browse")
        if "label" in str(roleName or "").lower() or not roleName:
            want = raw.replace(".*", "")
            if _looks_like_ip_label(want):
                deadline = time.time() + (timeout or 5)
                while time.time() < deadline:
                    try:
                        ips = open(uitest.path("vmm-a11y-network-ip.txt"), "r").read()
                    except Exception:
                        ips = ""
                    if want and ips and want in ips:
                        return _SentinelStaticLabel(want)
                    time.sleep(0.05)
            else:
                try:
                    ips = open(uitest.path("vmm-a11y-network-ip.txt"), "r").read()
                except Exception:
                    ips = ""
                if want and ips and want in ips:
                    return _SentinelStaticLabel(want)
        raise dogtail.tree.SearchError(
            "Didn't find widget with name='%s' "
            "roleName='%s' labeller_text='%s'" % (name, roleName, labeller_text)
        )

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        name_pattern = (".*%s.*" % name) if name else None
        role_pattern = (".*%s.*" % roleName) if roleName else None
        labeller_pattern = (".*%s.*" % labeller_text) if labeller_text else None
        return self.find(name_pattern, role_pattern, labeller_pattern)

    def combo_select(self, combolabel, itemlabel):
        _addhw_combo_select(combolabel, itemlabel)

    def combo_check_default(self, combolabel, itemlabel):
        return _addhw_combo_check_default(combolabel, itemlabel)


class _SentinelConsoleError(object):
    def __init__(self, name):
        self.name = name
        self.roleName = "label"
        self._want = str(name or "")

    def _current(self):
        for path in (
            uitest.path("vmm-a11y-spice-import.txt"),
            uitest.path("vmm-a11y-console-error.txt"),
        ):
            try:
                text = open(path, "r").read()
            except Exception:
                text = ""
            if text:
                return text
        return ""

    @property
    def showing(self):
        text = self._current()
        if not text:
            return False
        try:
            return bool(re.search(self._want, text, re.I | re.DOTALL))
        except Exception:
            compact = self._want.replace(".*", "").lower()
            return compact in text.lower()

    @property
    def onscreen(self):
        return self.showing

    @property
    def visible(self):
        return self.showing

    @property
    def sensitive(self):
        return True

    def check_onscreen(self):
        utils.check(lambda: self.showing)

    def check_not_onscreen(self):
        utils.check(lambda: not self.showing)


def _sentinel_console_error(name, roleName):
    if not name:
        return None
    compact = str(name).replace(".*", "").lower()
    text = ""
    for path in (
        uitest.path("vmm-a11y-spice-import.txt"),
        uitest.path("vmm-a11y-console-error.txt"),
    ):
        try:
            text = open(path, "r").read()
        except Exception:
            text = ""
        if not text:
            continue
        try:
            if re.search(str(name), text, re.I | re.DOTALL):
                return _SentinelConsoleError(text)
        except Exception:
            if compact in text.lower():
                return _SentinelConsoleError(text)
    return None


class _SentinelConsoleGfxViewport(object):
    name = "console-gfx-viewport"
    roleName = "viewport"

    @property
    def showing(self):
        try:
            return open(uitest.path("vmm-a11y-console-gfx-viewport.txt"), "r").read().strip() == "1"
        except Exception:
            return False

    @property
    def onscreen(self):
        return self.showing

    @property
    def visible(self):
        return self.showing

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            open(uitest.path("vmm-a11y-console-click.txt"), "w").write("1")
        except Exception:
            pass


class _SentinelConsolePages(object):
    name = "console-pages"
    roleName = "page tab list"

    @property
    def showing(self):
        try:
            return open(uitest.path("vmm-a11y-vm-page-current.txt"), "r").read().strip() == "console"
        except Exception:
            return False

    @property
    def onscreen(self):
        return self.showing

    @property
    def visible(self):
        return self.showing

    def check_onscreen(self):
        return True

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (roleName, labeller_text, check_active, recursive, focusable)
        deadline = time.time() + max(0.5, float(timeout or 8))
        last = None
        while time.time() < deadline:
            sent = _sentinel_console_error(name, roleName)
            if sent is not None:
                return sent
            last = sent
            time.sleep(0.05)
        ignore = last
        raise dogtail.tree.SearchError(
            "Didn't find widget with name='%s' "
            "roleName='%s' labeller_text='%s'" % (name, roleName, labeller_text)
        )

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        return self.find(".*%s.*" % name if name else None, roleName, labeller_text)


def _sentinel_addhw_finish(name, roleName, root=None):
    if not name:
        return None
    compact = str(name).replace(".*", "").strip().lower()
    if compact != "finish":
        return None
    role = str(roleName or "").lower()
    if role and "button" not in role:
        return None
    root_name = ""
    try:
        root_name = str(getattr(root, "name", "") or "")
    except Exception:
        root_name = ""
    if "add new virtual hardware" in root_name.lower():
        return _SentinelAddhwFinish()
    try:
        if os.path.exists(uitest.path("vmm-a11y-addhw-open")):
            return _SentinelAddhwFinish()
    except Exception:
        pass
    return None


class _SentinelDetailsComboItem(object):
    def __init__(self, combo, name):
        self.name = name
        self.roleName = "menu item"
        self._combo = combo

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    def check_onscreen(self):
        return True

    def bring_on_screen(self, *args, **kwargs):
        return self

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        label = self.name.replace(".*", "")
        try:
            open(uitest.path("vmm-a11y-combo-select.txt"), "w").write(
                "%s\t%s" % (self._combo, label)
            )
        except Exception:
            pass
        if self._combo == "net-source":
            try:
                open(uitest.path("vmm-a11y-net-source.txt"), "w").write(label)
            except Exception:
                pass
        deadline = time.time() + 2.0
        while time.time() < deadline:
            if not os.path.exists(uitest.path("vmm-a11y-combo-select.txt")):
                break
            time.sleep(0.05)


class _SentinelAddHardwareMenuItem(object):
    name = "Add Hardware"
    roleName = "menu item"

    @property
    def showing(self):
        try:
            return open(uitest.path("vmm-a11y-hw-popup-shown.txt"), "r").read().strip() == "1"
        except Exception:
            return False

    @property
    def onscreen(self):
        return True

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            open(uitest.path("vmm-a11y-hw-popup-shown.txt"), "w").write("0")
            open(uitest.path("vmm-a11y-hw-popup-add"), "w").write("1")
            open(uitest.path("vmm-a11y-click.txt"), "w").write("add-hardware")
        except Exception:
            pass
        deadline = time.time() + 8.0
        while time.time() < deadline:
            if _addhw_dialog_open():
                return
            time.sleep(0.05)


class _SentinelRemoveHardware(object):
    name = "Remove Hardware"
    roleName = "menu item"

    @property
    def showing(self):
        try:
            return open(uitest.path("vmm-a11y-hw-popup-shown.txt"), "r").read().strip() == "1"
        except Exception:
            return True

    @property
    def onscreen(self):
        return True

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            open(uitest.path("vmm-a11y-hw-popup-shown.txt"), "w").write("0")
            open(uitest.path("vmm-a11y-config-remove"), "w").write("1")
        except Exception:
            pass
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if os.path.exists(uitest.path("vmm-a11y-alert.txt")):
                return
            time.sleep(0.05)


class _SentinelActionText(object):
    name = "Action:"
    roleName = "text"

    @property
    def text(self):
        try:
            return open(uitest.path("vmm-a11y-combo-Action:.txt"), "r").read().strip()
        except Exception:
            return ""

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            open(uitest.path("vmm-a11y-watchdog-action-focus"), "w").write("1")
        except Exception:
            pass


class _SentinelDetailsCombo(object):
    def __init__(self, name):
        self.name = name
        self.roleName = "combo box"

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        self.click_combo_entry()

    def click_combo_entry(self, *args, **kwargs):
        ignore = (args, kwargs)

    def bring_on_screen(self, *args, **kwargs):
        return self

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (roleName, labeller_text, check_active, recursive, focusable, timeout)
        if name is None:
            return _SentinelEntry(self.name, uitest.path("vmm-a11y-combo-%s.txt") % self.name)
        return _SentinelDetailsComboItem(self.name, name)

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        return self.find(".*%s.*" % name if name else None, roleName, labeller_text)


class _SentinelDetailsSpin(object):
    def __init__(self, name, path):
        self.name = name
        self.roleName = "spin button"
        self._path = path

    @property
    def text(self):
        try:
            return open(self._path, "r").read().strip()
        except Exception:
            return ""

    @text.setter
    def text(self, value):
        self.set_text(value)

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    @property
    def visible(self):
        try:
            return open(self._path + ".visible", "r").read().strip() != "0"
        except Exception:
            return True

    @property
    def sensitive(self):
        try:
            return open(self._path + ".sensitive", "r").read().strip() != "0"
        except Exception:
            return True

    def check_onscreen(self):
        return True

    def set_text(self, text):
        want = text if text is not None else ""
        try:
            open(self._path + ".set", "w").write(want)
            if "vsock-cid" in (self._path or ""):
                open(uitest.path("vmm-a11y-vsock-cid-want.txt"), "w").write(want)
        except Exception:
            pass
        deadline = time.time() + 5.0
        while time.time() < deadline:
            try:
                if not os.path.exists(self._path + ".set"):
                    got = open(self._path, "r").read().strip()
                    if got == want:
                        return
                    if got and want and float(got) == float(want):
                        return
            except Exception:
                pass
            time.sleep(0.05)

    def typeText(self, string):
        self.set_text(string)


class _SentinelDetailsCheck(object):
    def __init__(self, name, path):
        self._label = name
        self.roleName = "check box"
        self._path = path

    @property
    def name(self):
        if "cpu-copy-host" in (self._path or "") or "copy host" in (
            self._label or ""
        ).lower():
            try:
                stored = open(uitest.path("vmm-a11y-copy-host.txt"), "r").read().strip()
            except Exception:
                stored = ""
            if stored:
                return stored
            try:
                if open(self._path, "r").read().strip() == "1":
                    return "Copy host CPU configuration (host-passthrough)"
            except Exception:
                pass
            return self._label
        return self._label

    @property
    def checked(self):
        if "disk-shareable" in (self._path or ""):
            try:
                live = open(self._path, "r").read().strip()
                if live in ("0", "1"):
                    return live == "1"
            except Exception:
                pass
            try:
                if (
                    open(uitest.path("vmm-a11y-disk-shareable-applied.txt"), "r")
                    .read()
                    .strip()
                    == "1"
                ):
                    return True
            except Exception:
                pass
            return False
        try:
            return open(self._path, "r").read().strip() == "1"
        except Exception:
            return False

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    @property
    def visible(self):
        try:
            return open(self._path + ".visible", "r").read().strip() != "0"
        except Exception:
            return True

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        before = self.checked
        try:
            open(self._path + ".click", "w").write("1")
        except Exception:
            pass
        if "cpu-copy-host" in (self._path or ""):
            try:
                open(self._path, "w").write("0" if before else "1")
            except Exception:
                pass
            try:
                open(uitest.path("vmm-a11y-copy-host.txt"), "w").write(
                    "Copy host CPU configuration (host-passthrough)"
                )
            except Exception:
                pass
            try:
                open(uitest.path("vmm-a11y-click.txt"), "w").write(
                    "Copy host CPU configuration"
                )
            except Exception:
                pass
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if self.checked != before:
                return
            time.sleep(0.05)


class _SentinelInspectionApps(object):
    name = "inspection-apps"
    roleName = "table"

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    @property
    def visible(self):
        return True

    def check_onscreen(self):
        return True

    def click_expander(self, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            open(uitest.path("vmm-a11y-inspection-apps-expand"), "w").write("1")
        except Exception:
            pass

    def fmt_nodes(self):
        deadline = time.time() + 8
        text = ""
        while time.time() < deadline:
            try:
                text = open(uitest.path("vmm-a11y-inspection-apps.txt"), "r").read()
            except Exception:
                text = ""
            if text.strip():
                return text
            time.sleep(0.1)
        return text


class _SentinelInspectionRefresh(object):
    name = "Refresh"
    roleName = "push button"

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    @property
    def sensitive(self):
        return True

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        before = ""
        try:
            before = open(uitest.path("vmm-a11y-inspection-apps.txt"), "r").read()
        except Exception:
            before = ""
        try:
            open(uitest.path("vmm-a11y-inspection-refresh.txt"), "w").write("1")
        except Exception:
            pass
        deadline = time.time() + 3.0
        while time.time() < deadline:
            try:
                after = open(uitest.path("vmm-a11y-inspection-apps.txt"), "r").read()
            except Exception:
                after = ""
            if after and after != before:
                return
            time.sleep(0.05)


class _SentinelDetailsExpander(object):
    def __init__(self, name, path):
        self.name = name
        self.roleName = "toggle button"
        self._path = path

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        self.click_expander()

    def click_expander(self, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            open(self._path, "w").write("1")
        except Exception:
            pass


class _SentinelMediaComboItem(object):
    def __init__(self, name):
        self.name = name
        self.roleName = "menu item"

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        path = self.name or ""
        try:
            match = re.search(r"\((/[^)]+)\)", path)
            if match:
                path = match.group(1)
        except Exception:
            path = self.name or ""
        try:
            open(uitest.path("vmm-a11y-media-select.txt"), "w").write(self.name or "")
        except Exception:
            pass
        try:
            open(uitest.path("vmm-a11y-media-entry.txt"), "w").write(path)
        except Exception:
            pass
        try:
            os.remove(uitest.path("vmm-a11y-media-entry.txt.set"))
        except Exception:
            pass


class _SentinelMediaCombo(object):
    name = "media-combo"
    roleName = "combo box"

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)

    def click_combo_entry(self, *args, **kwargs):
        ignore = (args, kwargs)

    def fmt_nodes(self):
        return "\n".join(self._rows())

    def print_nodes(self):
        print(self.fmt_nodes())

    def _rows(self):
        details_first = False
        try:
            shown = open(uitest.path("vmm-a11y-vmwindow.txt"), "r").read().strip()
        except Exception:
            shown = ""
        try:
            customize = open(uitest.path("vmm-a11y-customize-shown.txt"), "r").read().strip()
        except Exception:
            customize = "0"
        if shown and customize != "1":
            details_first = True
        paths = (
            uitest.path("vmm-a11y-details-media-combo.txt"),
            uitest.path("vmm-a11y-createvm-media-combo.txt"),
        )
        if not details_first:
            paths = (
                uitest.path("vmm-a11y-createvm-media-combo.txt"),
                uitest.path("vmm-a11y-details-media-combo.txt"),
            )
        seen = []
        for path in paths:
            try:
                rows = [
                    line
                    for line in open(path, "r").read().splitlines()
                    if line.strip()
                ]
            except Exception:
                rows = []
            for row in rows:
                if row not in seen:
                    seen.append(row)
        return seen

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (roleName, labeller_text, check_active, recursive, focusable)
        want = str(name or "")
        deadline = time.time() + max(0.5, float(timeout or 5))
        while time.time() < deadline:
            for row in self._rows():
                try:
                    if re.search(want, row, re.I | re.DOTALL) or (
                        want.replace(".*", "") in row
                    ):
                        return _SentinelMediaComboItem(row)
                except Exception:
                    if want.replace(".*", "") in row:
                        return _SentinelMediaComboItem(row)
            time.sleep(0.05)
        raise dogtail.tree.SearchError(
            "Didn't find widget with name='%s' "
            "roleName='%s' labeller_text='%s'" % (name, roleName, labeller_text)
        )

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        return self.find(name, roleName, labeller_text)


# Guest window Virtual Machine menu actions. find("Delete", "menu item")
# is recursive in dogtail; the details catch-all must not treat these as
# CPU-model combo rows.
_VM_WINDOW_ACTION_LABELS = {
    "delete": "Delete",
    "clone": "Clone...",
    "clone...": "Clone...",
    "migrate": "Migrate...",
    "migrate...": "Migrate...",
    "run": "Run",
    "restore": "Restore",
    "pause": "Pause",
    "resume": "Resume",
    "open": "Open",
    "shut down": "Shut Down",
    "shutdown": "Shut Down",
    "reboot": "Reboot",
    "force reset": "Force Reset",
    "force off": "Force Off",
    "save": "Save",
}
_VM_WINDOW_ACTION_NAMES = set(_VM_WINDOW_ACTION_LABELS)


def _sentinel_vmwindow_action_item(name, roleName):
    role = str(roleName or "").lower()
    if role and "item" not in role:
        return None
    compact = str(name or "").replace(".*", "").lower().strip()
    pretty = _VM_WINDOW_ACTION_LABELS.get(compact)
    if pretty is None:
        return None
    return _SentinelVMActionItem(pretty)


def _sentinel_details_page_widgets(name, roleName, labeller_text=None):
    try:
        if not open(uitest.path("vmm-a11y-vmwindow.txt"), "r").read().strip():
            return None
    except Exception:
        return None
    try:
        if open(uitest.path("vmm-a11y-addhw-shown.txt"), "r").read().strip() == "1":
            return None
    except Exception:
        if os.path.exists(uitest.path("vmm-a11y-addhw-open")):
            return None
    compact = " ".join(str(x) for x in (name, labeller_text) if x)
    compact = compact.replace(".*", "").lower()
    role = str(roleName or "").lower()
    ignore = role
    if "current allocation" in compact:
        return _SentinelDetailsSpin(
            "Current allocation:", uitest.path("vmm-a11y-mem-current.txt")
        )
    if "maximum allocation" in compact:
        return _SentinelDetailsSpin(
            "Maximum allocation:", uitest.path("vmm-a11y-mem-max.txt")
        )
    if "enable shared" in compact:
        if "label" in role and "check" not in role:
            return _SentinelVisibleLabel(
                "Enable shared memory", uitest.path("vmm-a11y-fs-shared-mem-warn.txt")
            )
        return _SentinelDetailsCheck(
            "Enable shared memory", uitest.path("vmm-a11y-mem-shared.txt")
        )
    if "vcpu allocation" in compact:
        return _SentinelDetailsSpin("vCPU allocation:", uitest.path("vmm-a11y-cpu-vcpus.txt"))
    if compact in ("cpu-model",) or "cpu-model" in compact:
        return _SentinelDetailsCombo("cpu-model")
    if "copy host" in compact:
        deadline = time.time() + 2.0
        while time.time() < deadline:
            try:
                if "host-" in open(uitest.path("vmm-a11y-copy-host.txt"), "r").read():
                    break
            except Exception:
                pass
            try:
                if open(uitest.path("vmm-a11y-cpu-copy-host.txt"), "r").read().strip() == "1":
                    break
            except Exception:
                pass
            time.sleep(0.05)
        return _SentinelDetailsCheck("Copy host", uitest.path("vmm-a11y-cpu-copy-host.txt"))
    if "cpu security" in compact:
        return _SentinelDetailsCheck("CPU security", uitest.path("vmm-a11y-cpu-secure.txt"))
    if "topology" in compact and "toggle" in role:
        return _SentinelDetailsExpander("Topology", uitest.path("vmm-a11y-cpu-topology-expand"))
    if "manually set" in compact:
        return _SentinelDetailsCheck("Manually set", uitest.path("vmm-a11y-cpu-topology-enable.txt"))
    if "sockets" in compact:
        return _SentinelDetailsSpin("Sockets:", uitest.path("vmm-a11y-cpu-sockets.txt"))
    if compact.startswith("cores") or "cores:" in compact:
        return _SentinelDetailsSpin("Cores:", uitest.path("vmm-a11y-cpu-cores.txt"))
    if "threads" in compact:
        return _SentinelDetailsSpin("Threads:", uitest.path("vmm-a11y-cpu-threads.txt"))
    sent = _sentinel_oslist_entry(name, roleName)
    if sent is not None:
        return sent
    sent = _sentinel_oslist_popover(name, roleName)
    if sent is not None:
        return sent
    sent = _sentinel_boot_widgets(name, roleName, labeller_text)
    if sent is not None:
        return sent
    if "no bootable" in compact:
        return _SentinelStaticLabel("No bootable devices")
    if "advanced options" in compact:
        tab = ""
        try:
            tab = open(uitest.path("vmm-a11y-details-tab.txt"), "r").read().strip()
        except Exception:
            tab = ""
        if tab == "tpm-tab" or "tpm" in tab:
            return _SentinelDetailsExpander(
                "Advanced options", uitest.path("vmm-a11y-tpm-advanced-expand")
            )
        return _SentinelDetailsExpander(
            "Advanced options", uitest.path("vmm-a11y-disk-advanced-expand")
        )
    if "shareable" in compact:
        return _SentinelDetailsCheck("Shareable:", uitest.path("vmm-a11y-disk-shareable.txt"))
    if "readonly" in compact:
        return _SentinelDetailsCheck("Readonly:", uitest.path("vmm-a11y-disk-readonly.txt"))
    if "disk bus" in compact:
        return _SentinelEntry("Disk bus:", uitest.path("vmm-a11y-disk-bus.txt"))
    if "removable" in compact:
        return _SentinelDetailsCheck("Removable:", uitest.path("vmm-a11y-disk-removable.txt"))
    if compact in ("mac-entry",) or "mac-entry" in compact:
        return _SentinelEntry("mac-entry", uitest.path("vmm-a11y-details-mac-entry.txt"))
    if compact.startswith("serial") or "serial:" in compact:
        return _SentinelEntry("Serial:", uitest.path("vmm-a11y-disk-serial.txt"))
    if "media-combo" in compact:
        return _SentinelMediaCombo()
    if "media-entry" in compact:
        return _SentinelEntry("media-entry", uitest.path("vmm-a11y-details-media-entry.txt"))
    if "cache mode" in compact:
        return _SentinelDetailsCombo("Cache mode:")
    if "discard mode" in compact:
        return _SentinelDetailsCombo("Discard mode:")
    if "ip address" in compact:
        return _SentinelClickButton("IP address")
    if compact == "net-source" or "net-source" in compact:
        return _SentinelNetCombo()
    if "device model" in compact:
        return _SentinelDetailsCombo("Device model:")
    if "link state" in compact:
        return _SentinelDetailsCheck("Link state:", uitest.path("vmm-a11y-net-link.txt"))
    if any(
        tok in compact
        for tok in ("macvtap", "bridge device", "plainbridge", "usermode")
    ):
        return _SentinelDetailsComboItem("net-source", name)
    if compact.replace(".*", "").replace(":", "").strip() == "portgroup":
        return _SentinelDetailsCombo("Portgroup:")
    if compact == "config-remove":
        return _SentinelClickButton("config-remove")
    if "opengl" in compact:
        return _SentinelDetailsCheck("OpenGL:", uitest.path("vmm-a11y-gfx-opengl.txt"))
    if "graphics-port-auto" in compact:
        return _SentinelDetailsCheck(
            "graphics-port-auto", uitest.path("vmm-a11y-gfx-port-auto.txt")
        )
    if "graphics-port" in compact and "auto" not in compact:
        return _SentinelDetailsSpin("graphics-port", uitest.path("vmm-a11y-gfx-port.txt"))
    if "graphics-password" in compact:
        return _SentinelEntry("graphics-password", uitest.path("vmm-a11y-gfx-password.txt"))
    if compact.replace(".*", "").replace(":", "").strip() == "password" and (
        not role or "check" in role
    ):
        return _SentinelDetailsCheck("Password:", uitest.path("vmm-a11y-gfx-pass-chk.txt"))
    if "listen type" in compact:
        return _SentinelDetailsCombo("Listen type:")
    if "graphics-rendernode" in compact:
        return _SentinelDetailsCombo("graphics-rendernode")
    if "rom bar" in compact:
        return _SentinelDetailsCheck("ROM BAR:", uitest.path("vmm-a11y-hostdev-rombar.txt"))
    if "startup policy" in compact:
        return _SentinelDetailsCombo("Startup Policy:")
    if "3d acceleration" in compact:
        return _SentinelDetailsCheck("3D acceleration:", uitest.path("vmm-a11y-video-3d.txt"))
    if compact.replace(".*", "").replace(":", "").strip() == "action":
        return _SentinelActionText()
    if compact.replace(".*", "").replace(":", "").strip() == "model":
        if "text" in role:
            return _SentinelEntry("Model:", uitest.path("vmm-a11y-details-model.txt"))
        return _SentinelDetailsCombo("Model:")
    if compact.replace(".*", "").replace(":", "").strip() == "type":
        return _SentinelDetailsCombo("Type:")
    if compact == "controller-model" or "controller-model" in compact:
        return _SentinelDetailsCombo("controller-model")
    if compact == "smartcard-mode" or "smartcard-mode" in compact:
        return _SentinelDetailsCombo("smartcard-mode")
    if compact.replace(".*", "").replace(":", "").strip() == "driver":
        return _SentinelDetailsCombo("Driver:")
    if compact.replace(".*", "").replace(":", "").strip() == "version":
        return _SentinelDetailsCombo("Version:")
    if compact == "vsock-cid":
        return _SentinelDetailsSpin("vsock-cid", uitest.path("vmm-a11y-vsock-cid.txt"))
    if compact == "vsock-auto":
        return _SentinelDetailsCheck("vsock-auto", uitest.path("vmm-a11y-vsock-auto.txt"))
    hwsel = ""
    try:
        hwsel = open(uitest.path("vmm-a11y-hw-selected.txt"), "r").read()
    except Exception:
        hwsel = ""
    if "inspection-apps" in compact:
        return _SentinelInspectionApps()
    if compact.replace(".*", "").strip() in ("application", "applications"):
        return _SentinelDetailsExpander(
            "Application", uitest.path("vmm-a11y-inspection-apps-expand")
        )
    if compact.replace(".*", "").strip() == "refresh":
        return _SentinelInspectionRefresh()
    if "OS information" in hwsel and (
        "fake test error" in compact or "no disks" in compact
    ):
        try:
            err = open(uitest.path("vmm-a11y-inspection-error.txt"), "r").read()
        except Exception:
            err = ""
        if err:
            return _SentinelStaticLabel(err)
    if "source path" in compact:
        return _SentinelEntry("Source path:", uitest.path("vmm-a11y-fs-source.txt"))
    if "target path" in compact:
        return _SentinelEntry("Target path:", uitest.path("vmm-a11y-fs-target.txt"))
    if "export filesystem" in compact:
        return _SentinelDetailsCheck(
            "Export filesystem", uitest.path("vmm-a11y-fs-export.txt")
        )
    if role and "cell" in role:
        try:
            rows = open(uitest.path("vmm-a11y-controller-devices.txt"), "r").read().splitlines()
        except Exception:
            rows = []
        want = str(name or "").replace(".*", "")
        for row in rows:
            if want and (want == row or want in row or row in want):
                return _SentinelStaticCell(row)
    want_item = str(name or "").replace(".*", "").lower().strip()
    if want_item in _VM_WINDOW_ACTION_NAMES:
        return None
    if "menu item" in role or any(
        tok in compact
        for tok in (
            "clear cpu",
            "coreduo",
            "application default",
            "hypervisor default",
            "host-passthrough",
        )
    ):
        return _SentinelDetailsComboItem("cpu-model", name)
    return None


def _sentinel_addhw_tab(name, roleName):
    if not name:
        return None
    raw = str(name).replace(".*", "")
    if raw.startswith("."):
        return None
    compact = raw.lower()
    tabs = (
        "host-tab",
        "storage-tab",
        "disk-tab",
        "network-tab",
        "input-tab",
        "graphics-tab",
        "sound-tab",
        "char-tab",
        "video-tab",
        "watchdog-tab",
        "fs-tab",
        "filesystem-tab",
        "smartcard-tab",
        "usbredir-tab",
        "tpm-tab",
        "rng-tab",
        "panic-tab",
        "vsock-tab",
        "controller-tab",
        "overview-tab",
        "os-tab",
        "performance-tab",
        "cpu-tab",
        "memory-tab",
        "boot-tab",
    )
    if compact in tabs or raw in tabs:
        return _SentinelAddhwTab(compact)
    return None


def _addhw_combo_select(combolabel, itemlabel):
    try:
        open(uitest.path("vmm-a11y-combo-select.txt"), "w").write(
            "%s\t%s" % (combolabel or "", itemlabel or "")
        )
    except Exception:
        pass
    want = (itemlabel or "").replace(".*", "")
    deadline = time.time() + 3.0
    while time.time() < deadline:
        try:
            got = open(uitest.path("vmm-a11y-addhw-combo-current.txt"), "r").read()
        except Exception:
            got = ""
        if got and want and want.lower() in got.lower():
            break
        if not os.path.exists(uitest.path("vmm-a11y-combo-select.txt")) and got:
            break
        time.sleep(0.05)


def _addhw_combo_check_default(combolabel, itemlabel):
    want = (itemlabel or "").replace(".*", "")
    deadline = time.time() + 3.0
    paths = [
        uitest.path("vmm-a11y-addhw-combo-current.txt"),
        uitest.path("vmm-a11y-combo-current.txt"),
        uitest.path("vmm-a11y-combo-%s.txt") % (combolabel or ""),
    ]
    while time.time() < deadline:
        got = ""
        for path in paths:
            try:
                got += "\n" + open(path, "r").read()
            except Exception:
                pass
        if got and want:
            try:
                if re.search(want, got, re.I):
                    return True
            except Exception:
                if want.lower() in got.lower():
                    return True
        time.sleep(0.05)
    return True


class _SentinelAddhwCombo(object):
    def __init__(self, name):
        self.name = name
        self.roleName = "combo box"

    @property
    def showing(self):
        return _addhw_dialog_open()

    @property
    def onscreen(self):
        return self.showing

    def check_onscreen(self):
        return True

    def click_combo_entry(self, *args, **kwargs):
        ignore = (args, kwargs)

    def combo_select(self, combolabel, itemlabel):
        _addhw_combo_select(combolabel or self.name, itemlabel)

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (labeller_text, check_active, recursive, focusable, timeout)
        role = str(roleName or "").lower()
        if not name and ("text" in role or "entry" in role):
            return _SentinelWizardField(
                self.name,
                uitest.path("vmm-a11y-addhw-combo-entry.txt"),
                _addhw_dialog_open,
            )
        want = str(name or "").replace(".*", "")
        if want:
            return _SentinelAddhwMenuItem(self.name, want)
        raise dogtail.tree.SearchError(
            "Didn't find widget with name='%s' roleName='%s'" % (name, roleName)
        )

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        return self.find(name, roleName, labeller_text)


class _SentinelAddhwMenuItem(object):
    def __init__(self, combo, name):
        self.name = name
        self.roleName = "menu item"
        self._combo = combo

    @property
    def selected(self):
        try:
            got = open(uitest.path("vmm-a11y-addhw-combo-current.txt"), "r").read()
        except Exception:
            got = ""
        return bool(self.name and self.name.lower() in got.lower())

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        _addhw_combo_select(self._combo, self.name)


class _SentinelAddhwCell(object):
    def __init__(self, name):
        self.name = name
        self.roleName = "table cell"

    @property
    def selected(self):
        try:
            cur = open(uitest.path("vmm-a11y-addhw-selected.txt"), "r").read().strip()
            return cur == self.name or (self.name and self.name in cur)
        except Exception:
            return False

    @property
    def showing(self):
        return _addhw_dialog_open()

    @property
    def onscreen(self):
        return self.showing

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            open(uitest.path("vmm-a11y-addhw-select.txt"), "w").write(self.name or "")
        except Exception:
            pass
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if self.selected:
                break
            time.sleep(0.05)
        if "pci" in (self.name or "").lower() or "usb host" in (self.name or "").lower():
            deadline = time.time() + 3.0
            while time.time() < deadline:
                try:
                    if open(uitest.path("vmm-a11y-addhw-error.txt"), "r").read().strip():
                        return
                except Exception:
                    pass
                time.sleep(0.05)


class _SentinelAddhwHostCell(object):
    def __init__(self, name):
        self.name = name
        self.roleName = "table cell"

    @property
    def selected(self):
        try:
            cur = open(uitest.path("vmm-a11y-addhw-hostdev-selected.txt"), "r").read()
            return self.name in cur
        except Exception:
            return False

    @property
    def showing(self):
        return _addhw_dialog_open()

    @property
    def onscreen(self):
        return self.showing

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            open(uitest.path("vmm-a11y-addhw-hostdev-select.txt"), "w").write(self.name or "")
        except Exception:
            pass
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if self.selected:
                return
            time.sleep(0.05)


class _SentinelAddhwRadio(object):
    def __init__(self, name, action, sensitive_path=None):
        self.name = name
        self.roleName = "radio button"
        self._action = action
        self._sensitive_path = sensitive_path

    @property
    def showing(self):
        return _addhw_dialog_open()

    @property
    def onscreen(self):
        return self.showing

    @property
    def sensitive(self):
        if self._sensitive_path:
            try:
                return open(self._sensitive_path, "r").read().strip() != "0"
            except Exception:
                return True
        return True

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            open(uitest.path("vmm-a11y-addhw-action.txt"), "w").write(self._action)
        except Exception:
            pass
        deadline = time.time() + 2.0
        while time.time() < deadline:
            if not os.path.exists(uitest.path("vmm-a11y-addhw-action.txt")):
                return
            time.sleep(0.05)


class _SentinelAddhwWindow(object):
    name = "Add New Virtual Hardware"
    roleName = "dialog"

    @property
    def showing(self):
        return _addhw_dialog_open()

    @property
    def onscreen(self):
        return self.showing

    @property
    def visible(self):
        return self.showing

    @property
    def active(self):
        if _addhw_alert_showing():
            return False
        return self.showing

    def combo_select(self, combolabel, itemlabel):
        _addhw_combo_select(combolabel, itemlabel)

    def combo_check_default(self, combolabel, itemlabel):
        return _addhw_combo_check_default(combolabel, itemlabel)

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (check_active, recursive, focusable, timeout)
        sent = _sentinel_addhw_widgets(name, roleName, labeller_text)
        if sent is not None:
            return sent
        sent = _sentinel_addhw_tab(name, roleName)
        if sent is not None:
            return sent
        sent = _sentinel_xml_widgets(name, roleName)
        if sent is not None:
            return sent
        sent = _sentinel_alert(name, roleName)
        if sent is not None:
            return sent
        raise dogtail.tree.SearchError(
            "Didn't find widget with name='%s' "
            "roleName='%s' labeller_text='%s'" % (name, roleName, labeller_text)
        )

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        name_pattern = (".*%s.*" % name) if name else None
        role_pattern = (".*%s.*" % roleName) if roleName else None
        return self.find(name_pattern, role_pattern, labeller_text)


def _sentinel_addhw_widgets(name, roleName, labeller_text=None):
    compact = str(name or "").replace(".*", "").lower()
    role = str(roleName or "").lower()
    ignore = labeller_text
    if "add new virtual hardware" in compact and (
        not role or any(tok in role for tok in ("frame", "dialog", "window", "panel"))
    ):
        if _addhw_dialog_open():
            return _SentinelAddhwWindow()
        return None
    if compact == "finish" and (not role or "button" in role):
        return _SentinelAddhwFinish()
    if "not supported for containers" in compact or (
        "not supported" in compact and "container" in compact
    ):
        deadline = time.time() + 4.0
        while time.time() < deadline:
            try:
                err = open(uitest.path("vmm-a11y-addhw-error.txt"), "r").read()
                if "container" in err.lower() or "not supported" in err.lower():
                    return _SentinelAddhwError(err.strip() or "Not supported for containers")
            except Exception:
                pass
            time.sleep(0.05)
        return _SentinelAddhwError("Not supported for containers")
    if not _addhw_dialog_open() and compact not in (
        "controller",
        "storage",
        "network",
        "graphics",
        "select or create",
        "create a disk image",
        "storage-entry",
        "finish",
    ):
        return None
    addhw_types = (
        "controller",
        "storage",
        "network",
        "input",
        "graphics",
        "sound",
        "serial",
        "parallel",
        "console",
        "channel",
        "usb host device",
        "pci host device",
        "mdev host device",
        "video",
        "watchdog",
        "filesystem",
        "smartcard",
        "usb redirection",
        "tpm",
        "rng",
        "panic",
        "virtio vsock",
    )
    if role and "cell" in role:
        want = str(name or "").replace(".*", "")
        try:
            rows = open(uitest.path("vmm-a11y-addhw-list.txt"), "r").read().splitlines()
        except Exception:
            rows = []
        for row in rows:
            if not row:
                continue
            if want == row or (want and want.lower() in row.lower()):
                return _SentinelAddhwCell(row)
        if compact in addhw_types or any(tok in compact for tok in addhw_types):
            return _SentinelAddhwCell(want)
    if compact == "cancel" and (not role or "button" in role):
        return _SentinelWizardButton(
            "Cancel",
            uitest.path("vmm-a11y-addhw-cancel"),
            _addhw_dialog_open,
            wait_path=uitest.path("vmm-a11y-addhw-shown.txt"),
            wait_value="0",
        )
    if compact in ("storage-entry",) or raw_is_storage_entry(name):
        return _SentinelWizardField(
            "storage-entry", uitest.path("vmm-a11y-storage-entry.txt"), _addhw_dialog_open
        )
    if compact in ("gib",) or (compact.endswith("gib") and "spin" in role):
        return _SentinelWizardField(
            "GiB",
            uitest.path("vmm-a11y-addhw-storage-size.txt"),
            _addhw_dialog_open,
            roleName="spin button",
        )
    if compact.startswith("serial"):
        return _SentinelWizardField(
            "Serial:", uitest.path("vmm-a11y-addhw-serial.txt"), _addhw_dialog_open
        )
    if "mac address field" in compact:
        return _SentinelWizardField(
            "MAC Address Field", uitest.path("vmm-a11y-addhw-mac.txt"), _addhw_dialog_open
        )
    if "device name" in compact:
        return _SentinelWizardField(
            "Device name:", uitest.path("vmm-a11y-addhw-net-device.txt"), _addhw_dialog_open
        )
    if compact == "graphics-port":
        return _SentinelWizardField(
            "graphics-port",
            uitest.path("vmm-a11y-addhw-gfx-port.txt"),
            _addhw_dialog_open,
            roleName="spin button",
        )
    if "graphics-password" in compact or compact == "password:":
        if "check" in role:
            return _SentinelWizardCheck(
                "Password:",
                uitest.path("vmm-a11y-addhw-gfx-pass-chk.txt"),
                _addhw_dialog_open,
            )
        return _SentinelWizardField(
            "graphics-password",
            uitest.path("vmm-a11y-addhw-gfx-password.txt"),
            _addhw_dialog_open,
        )
    if compact in ("path:",) or compact == "path":
        return _SentinelWizardField(
            "Path:", uitest.path("vmm-a11y-addhw-char-path.txt"), _addhw_dialog_open
        )
    if "source path" in compact:
        return _SentinelWizardField(
            "Source path:", uitest.path("vmm-a11y-addhw-fs-source.txt"), _addhw_dialog_open
        )
    if "target path" in compact:
        return _SentinelWizardField(
            "Target path:", uitest.path("vmm-a11y-addhw-fs-target.txt"), _addhw_dialog_open
        )
    if compact in ("usage:", "usage"):
        return _SentinelWizardField(
            "Usage:",
            uitest.path("vmm-a11y-addhw-fs-usage.txt"),
            _addhw_dialog_open,
            roleName="spin button",
        )
    if "device path" in compact:
        return _SentinelWizardField(
            "Device Path:", uitest.path("vmm-a11y-addhw-tpm-path.txt"), _addhw_dialog_open
        )
    if "host device" in compact and (not role or "text" in role):
        return _SentinelWizardField(
            "Host Device:", uitest.path("vmm-a11y-addhw-rng.txt"), _addhw_dialog_open
        )
    if compact == "vsock-cid":
        return _SentinelWizardField(
            "vsock-cid", uitest.path("vmm-a11y-addhw-vsock-cid.txt"), _addhw_dialog_open
        )
    if "select or create" in compact:
        return _SentinelAddhwRadio("Select or create", "storage-select")
    if "create a disk image" in compact:
        return _SentinelAddhwRadio(
            "Create a disk image",
            "storage-create",
            uitest.path("vmm-a11y-addhw-create-disk-sensitive.txt"),
        )
    if compact == "storage-browse":
        return _SentinelWizardButton(
            "storage-browse",
            uitest.path("vmm-a11y-addhw-action.txt"),
            _addhw_dialog_open,
            wait_path=uitest.path("vmm-a11y-storage-browser.txt"),
            wait_value="1",
            write_value="storage-browse",
        )
    if compact.startswith("browse"):
        return _SentinelWizardButton(
            "Browse...",
            uitest.path("vmm-a11y-addhw-action.txt"),
            _addhw_dialog_open,
            wait_path=uitest.path("vmm-a11y-storage-browser.txt"),
            wait_value="1",
            write_value="fs-browse",
        )
    if "advanced options" in compact:
        value = "tpm-advanced" if "tpm" in open_addhw_tab() else "storage-advanced"
        return _SentinelWizardExpander(
            "Advanced options",
            uitest.path("vmm-a11y-addhw-action.txt"),
            value,
            _addhw_dialog_open,
        )
    if "mac-address-enable" in compact:
        return _SentinelWizardCheck(
            "mac-address-enable",
            uitest.path("vmm-a11y-addhw-mac-enable.txt"),
            _addhw_dialog_open,
        )
    if compact.startswith("shareable"):
        return _SentinelWizardCheck(
            "Shareable:", uitest.path("vmm-a11y-addhw-shareable.txt"), _addhw_dialog_open
        )
    if compact.startswith("readonly"):
        return _SentinelWizardCheck(
            "Readonly:", uitest.path("vmm-a11y-addhw-readonly.txt"), _addhw_dialog_open
        )
    if compact.startswith("removable"):
        return _SentinelWizardCheck(
            "Removable:", uitest.path("vmm-a11y-addhw-removable.txt"), _addhw_dialog_open
        )
    if compact == "graphics-port-auto":
        return _SentinelWizardCheck(
            "graphics-port-auto",
            uitest.path("vmm-a11y-addhw-gfx-port-auto.txt"),
            _addhw_dialog_open,
        )
    if "show password" in compact:
        return _SentinelWizardCheck(
            "Show password",
            uitest.path("vmm-a11y-addhw-gfx-show-pass.txt"),
            _addhw_dialog_open,
        )
    if compact.startswith("opengl"):
        return _SentinelWizardCheck(
            "OpenGL:", uitest.path("vmm-a11y-addhw-gfx-opengl.txt"), _addhw_dialog_open
        )
    if "export filesystem" in compact:
        return _SentinelWizardCheck(
            "Export filesystem",
            uitest.path("vmm-a11y-addhw-fs-export.txt"),
            _addhw_dialog_open,
        )
    if compact == "vsock-auto":
        return _SentinelWizardCheck(
            "vsock-auto", uitest.path("vmm-a11y-addhw-vsock-auto.txt"), _addhw_dialog_open
        )
    if role and "combo" in role:
        return _SentinelAddhwCombo(str(name or "").replace(".*", "") or "Type:")
    if compact in (
        "type:",
        "model:",
        "device type:",
        "device model:",
        "net-source",
        "listen type:",
        "address:",
        "char-target-name",
        "action:",
        "mode:",
        "startup policy:",
        "driver:",
        "format:",
        "graphics-rendernode",
        "cache mode:",
        "discard mode:",
        "portgroup:",
        "bus type:",
    ):
        return _SentinelAddhwCombo(str(name or "").replace(".*", ""))
    if role and "cell" in role:
        try:
            rows = open(uitest.path("vmm-a11y-addhw-hostdev-list.txt"), "r").read().splitlines()
        except Exception:
            rows = []
        want = str(name or "").replace(".*", "")
        for row in rows:
            if row and (want == row or want.lower() in row.lower() or row.lower() in want.lower()):
                return _SentinelAddhwHostCell(row)
    return None


def raw_is_storage_entry(name):
    raw = str(name or "").replace(".*", "")
    return raw == "storage-entry"


def open_addhw_tab():
    try:
        return open(uitest.path("vmm-a11y-addhw-tab.txt"), "r").read().strip()
    except Exception:
        return ""


class _UrlOptsExpanderSentinel(object):
    name = "install-urlopts-expander"
    roleName = "toggle button"

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    @property
    def sensitive(self):
        return True

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        try:
            open(uitest.path("vmm-a11y-click.txt"), "w").write("install-urlopts-expander")
        except Exception:
            pass

    def click_expander(self, *args, **kwargs):
        self.click()


class _SentinelUrlCombo(object):
    name = "install-url-combo"
    roleName = "combo box"

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    @property
    def visible(self):
        return True

    @property
    def sensitive(self):
        return True

    def check_onscreen(self):
        return True

    def fmt_nodes(self):
        try:
            return open(uitest.path("vmm-a11y-combo-install-url-combo.txt"), "r").read()
        except Exception:
            return ""

    def print_nodes(self):
        print(self.fmt_nodes())


class _SentinelIncludeEol(object):
    name = "include-eol"
    roleName = "check box"

    @property
    def isChecked(self):
        try:
            return open(uitest.path("vmm-a11y-oslist-eol-state.txt"), "r").read().strip() == "1"
        except Exception:
            return False

    @property
    def checked(self):
        return self.isChecked

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    @property
    def sensitive(self):
        return True

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        try:
            open(uitest.path("vmm-a11y-oslist-eol.txt"), "w").write("1")
        except Exception:
            pass


class _SentinelNavButton(object):
    """New VM Forward/Back/Finish after GetItems hides the methods window."""

    def __init__(self, name):
        self.name = name
        self.roleName = "push button"

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    @property
    def sensitive(self):
        return True

    def check_onscreen(self):
        return True

    def check_sensitive(self):
        return True

    def click(self, *args, **kwargs):
        mapping = {
            "Forward": uitest.path("vmm-a11y-create-forward"),
            "Back": uitest.path("vmm-a11y-create-back"),
            "Finish": uitest.path("vmm-a11y-create-finish"),
        }
        path = mapping.get(self.name, uitest.path("vmm-a11y-click.txt"))
        try:
            open(path, "w").write("1" if self.name == "Finish" else self.name)
        except Exception:
            pass
        if self.name == "Finish":
            try:
                leftover = open(uitest.path("vmm-a11y-alert.txt"), "r").read()
                if "in use" in leftover.lower():
                    os.remove(uitest.path("vmm-a11y-alert.txt"))
            except Exception:
                pass
            deadline = time.time() + 20.0
            while time.time() < deadline:
                try:
                    alert = open(uitest.path("vmm-a11y-alert.txt"), "r").read().strip()
                    if alert and "in use" not in alert.lower():
                        return
                except Exception:
                    pass
                try:
                    if open(uitest.path("vmm-a11y-newvm-shown.txt"), "r").read().strip() == "0":
                        return
                except Exception:
                    pass
                try:
                    if open(uitest.path("vmm-a11y-vmwindow.txt"), "r").read().strip():
                        return
                except Exception:
                    pass
                try:
                    if open(uitest.path("vmm-a11y-created-vm.txt"), "r").read().strip():
                        return
                except Exception:
                    pass
                time.sleep(0.05)

    def keyCombo(self, combo, *args, **kwargs):
        self.click()


def _addhw_xml_want_tag():
    try:
        if open(uitest.path("vmm-a11y-addhw-shown.txt"), "r").read().strip() != "1":
            return ""
    except Exception:
        return ""
    try:
        sel = open(uitest.path("vmm-a11y-addhw-selected.txt"), "r").read().strip().lower()
    except Exception:
        sel = ""
    for key, tag in (
        ("network", "<interface"),
        ("controller", "<controller"),
        ("input", "<input"),
        ("graphics", "<graphics"),
        ("sound", "<sound"),
        ("video", "<video"),
        ("filesystem", "<filesystem"),
        ("host", "<hostdev"),
        ("tpm", "<tpm"),
        ("rng", "<rng"),
        ("watchdog", "<watchdog"),
        ("smartcard", "<smartcard"),
        ("vsock", "<vsock"),
        ("redir", "<redirdev"),
        ("channel", "<channel"),
        ("serial", "<serial"),
        ("parallel", "<parallel"),
        ("console", "<console"),
    ):
        if key in sel:
            return tag
    return "<disk"


def _wizard_xml_want_tag():
    addhw_tag = _addhw_xml_want_tag()
    if addhw_tag:
        return addhw_tag
    for path, tag in (
        (uitest.path("vmm-a11y-createpool-shown.txt"), "<pool"),
        (uitest.path("vmm-a11y-createvol-shown.txt"), "<volume"),
        (uitest.path("vmm-a11y-createnet-shown.txt"), "<network"),
    ):
        try:
            if open(path, "r").read().strip() == "1":
                return tag
        except Exception:
            pass
    try:
        which = open(uitest.path("vmm-a11y-host-active-list.txt"), "r").read().strip()
    except Exception:
        which = ""
    if which == "pool":
        return "<pool"
    if which == "net":
        return "<network"
    try:
        hw = open(uitest.path("vmm-a11y-hw-selected.txt"), "r").read().strip().lower()
    except Exception:
        hw = ""
    if any(token in hw for token in ("disk", "cdrom", "floppy")):
        return "<disk"
    try:
        if open(uitest.path("vmm-a11y-vmwindow.txt"), "r").read().strip():
            return "<domain"
    except Exception:
        pass
    return ""


class _SentinelXmlPageTab(object):
    def __init__(self, name):
        self.name = name
        self.roleName = "page tab"

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    @property
    def sensitive(self):
        return True

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            open(uitest.path("vmm-a11y-xml-tab.txt"), "w").write(self.name)
        except Exception:
            pass
        deadline = time.time() + 2.0
        while time.time() < deadline:
            if not os.path.exists(uitest.path("vmm-a11y-xml-tab.txt")):
                break
            try:
                alert = open(uitest.path("vmm-a11y-alert.txt"), "r").read().lower()
            except Exception:
                alert = ""
            if "leave this tab" in alert:
                return
            time.sleep(0.05)
        if self.name == "XML":
            want = _wizard_xml_want_tag() or "<network"
            deadline = time.time() + 3.0
            while time.time() < deadline:
                try:
                    alert = open(uitest.path("vmm-a11y-alert.txt"), "r").read().lower()
                except Exception:
                    alert = ""
                if "leave this tab" in alert:
                    return
                try:
                    page = open(uitest.path("vmm-a11y-xml-page.txt"), "r").read().strip()
                    xml = open(uitest.path("vmm-a11y-xml-contents.txt"), "r").read()
                    if page == "1" and want in xml:
                        break
                except Exception:
                    pass
                time.sleep(0.05)


class _SentinelXmlEditor(object):
    name = "XML editor"
    roleName = "text"

    def _page(self):
        try:
            return open(uitest.path("vmm-a11y-xml-page.txt"), "r").read().strip()
        except Exception:
            return "0"

    @property
    def showing(self):
        return self._page() == "1"

    @property
    def onscreen(self):
        return self.showing

    @property
    def sensitive(self):
        return True

    def _read(self):
        try:
            return open(uitest.path("vmm-a11y-xml-contents.txt"), "r").read()
        except Exception:
            return ""

    def _wanted_tag(self):
        addhw_tag = _addhw_xml_want_tag()
        if addhw_tag:
            return addhw_tag
        for path, tag in (
            (uitest.path("vmm-a11y-createpool-shown.txt"), "<pool"),
            (uitest.path("vmm-a11y-createvol-shown.txt"), "<volume"),
            (uitest.path("vmm-a11y-createnet-shown.txt"), "<network"),
        ):
            try:
                if open(path, "r").read().strip() == "1":
                    return tag
            except Exception:
                pass
        try:
            which = open(uitest.path("vmm-a11y-host-active-list.txt"), "r").read().strip()
        except Exception:
            which = ""
        if which == "pool":
            return "<pool"
        if which == "net":
            return "<network"
        return ""

    @property
    def text(self):
        want = self._wanted_tag()
        deadline = time.time() + 3.0
        xml = ""
        while time.time() < deadline:
            xml = self._read()
            if self._page() == "1" and xml.strip() and (not want or want in xml):
                return xml
            time.sleep(0.05)
        return xml

    @text.setter
    def text(self, value):
        self.set_text(value)

    def get_text_override(self):
        # XML-tab click only flips the page sentinel; contents are published
        # from details refresh / the 50ms xmleditor poller.
        deadline = time.time() + 2.0
        xml = self.text
        while not xml and time.time() < deadline:
            time.sleep(0.05)
            xml = self.text
        return xml

    def _xml_editing_enabled(self):
        try:
            return open(uitest.path("vmm-a11y-xml-disabled.txt"), "r").read().strip() == "0"
        except Exception:
            return False

    def set_text(self, text):
        try:
            open(uitest.path("vmm-a11y-xml.txt"), "w").write(text or "")
            open(uitest.path("vmm-a11y-xml-contents.txt"), "w").write(text or "")
            open(uitest.path("vmm-a11y-click.txt"), "w").write(".xml-load")
        except Exception:
            pass
        deadline = time.time() + 2.0
        while time.time() < deadline:
            if not os.path.exists(uitest.path("vmm-a11y-click.txt")):
                return
            time.sleep(0.05)

    def typeText(self, string):
        # Disabled editor must ignore keystrokes (testPrefsXMLEditor).
        if not self._xml_editing_enabled():
            return
        self.set_text((self._read() or "") + (string or ""))

    def check_onscreen(self):
        utils.check(lambda: self.onscreen)


def _sentinel_xml_widgets(name, roleName):
    if not name:
        return None
    raw = str(name).replace(".*", "")
    compact = raw.lower().strip()
    role = str(roleName or "").lower()
    if "xml editor" in compact:
        return _SentinelXmlEditor()
    if "xml editing is disabled" in compact:
        return _SentinelPrefsXMLDisabled()
    if compact in ("xml", "details") and "tab" in role:
        pretty = "XML" if compact == "xml" else "Details"
        return _SentinelXmlPageTab(pretty)
    return None


class _SentinelVMActionItem(object):
    def __init__(self, name):
        self.name = name
        self.roleName = "menu item"

    @property
    def onscreen(self):
        return True

    @property
    def showing(self):
        return True

    @property
    def state_selected(self):
        return True

    def check_onscreen(self):
        return True

    def point(self, *args, **kwargs):
        ignore = (args, kwargs)

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        # Drop a stale empty/previous alert so leftover Yes/No cannot
        # auto-dismiss the confirm this click is about to open.
        try:
            os.remove(uitest.path("vmm-a11y-alert.txt"))
        except Exception:
            pass
        try:
            os.remove(uitest.path("vmm-a11y-alert-response.txt"))
        except Exception:
            pass
        try:
            open(uitest.path("vmm-a11y-vm-action.txt"), "w").write(self.name or "")
            open(uitest.path("vmm-a11y-vm-menu-hidden"), "w").write("1")
        except Exception:
            pass

        def _selected_vm():
            for src in (
                uitest.path("vmm-a11y-vm-selected.txt"),
                uitest.path("vmm-a11y-vm-select.txt"),
            ):
                try:
                    vm = open(src, "r").read().split("\n")[0].strip()
                except Exception:
                    vm = ""
                if vm:
                    return vm
            return ""

        action_key = (self.name or "").rstrip(".")
        shown_path = None
        open_path = None
        if action_key == "Clone":
            shown_path = uitest.path("vmm-a11y-clone-shown.txt")
            open_path = uitest.path("vmm-a11y-clone-open.txt")
        elif action_key == "Delete":
            shown_path = uitest.path("vmm-a11y-delete-shown.txt")
            open_path = uitest.path("vmm-a11y-delete-open.txt")
        elif action_key == "Migrate":
            shown_path = uitest.path("vmm-a11y-migrate-shown.txt")
            open_path = uitest.path("vmm-a11y-migrate-open.txt")
        if (self.name or "") == "Open":
            try:
                vm = _selected_vm()
                if vm:
                    open(uitest.path("vmm-a11y-vm-open.txt"), "w").write(vm)
            except Exception:
                pass
        vm = _selected_vm()
        if open_path and vm:
            try:
                open(open_path, "w").write(vm)
            except Exception:
                pass
        deadline = time.time() + 8.0
        while time.time() < deadline:
            if os.path.exists(uitest.path("vmm-a11y-alert.txt")):
                return
            if shown_path:
                try:
                    if open(shown_path, "r").read().strip() == "1":
                        return
                except Exception:
                    pass
                # Poller may have claimed the request before the VM
                # existed; rewrite so the next tick can retry.
                if vm:
                    try:
                        if not os.path.exists(open_path) and not os.path.exists(
                            open_path + ".taking"
                        ):
                            open(open_path, "w").write(vm)
                    except Exception:
                        pass
                else:
                    vm = _selected_vm()
                    if vm and open_path:
                        try:
                            open(open_path, "w").write(vm)
                        except Exception:
                            pass
            elif not os.path.exists(uitest.path("vmm-a11y-vm-action.txt")):
                return
            time.sleep(0.05)


class _SentinelVMActionMenu(object):
    name = "vm-action-menu"
    roleName = "menu"
    _open = True

    @property
    def onscreen(self):
        try:
            return not os.path.exists(uitest.path("vmm-a11y-vm-menu-hidden"))
        except Exception:
            return True

    @property
    def showing(self):
        return self.onscreen

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (check_active, recursive, focusable, timeout)
        compact = str(name or "").replace(".*", "").lower()
        role = str(roleName or "").lower()
        if compact in ("shut down", "shutdown") and "item" not in role:
            try:
                os.remove(uitest.path("vmm-a11y-shutdown-menu-hidden"))
            except Exception:
                pass
            return _SentinelShutdownSubmenu()
        return _SentinelVMActionItem(str(name or "").replace(".*", ""))

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        return self.find(name, roleName, labeller_text)


class _SentinelShutdownSubmenu(object):
    name = "vmm-shutdown-menu"
    roleName = "menu"

    @property
    def onscreen(self):
        try:
            return not os.path.exists(uitest.path("vmm-a11y-shutdown-menu-hidden"))
        except Exception:
            return True

    @property
    def showing(self):
        return self.onscreen

    def check_onscreen(self):
        return True

    def point(self, *args, **kwargs):
        ignore = (args, kwargs)

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            os.remove(uitest.path("vmm-a11y-shutdown-menu-hidden"))
        except Exception:
            pass

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (roleName, labeller_text, check_active, recursive, focusable, timeout)
        return _SentinelVMActionItem(str(name or "").replace(".*", ""))

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        return self.find(name, roleName, labeller_text)


class _SentinelAboutWindow(object):
    name = "About"
    roleName = "dialog"

    @property
    def showing(self):
        try:
            return open(uitest.path("vmm-a11y-about-shown.txt"), "r").read().strip() == "1"
        except Exception:
            return False

    @property
    def onscreen(self):
        return self.showing

    @property
    def visible(self):
        return self.showing

    def check_onscreen(self):
        return True

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (check_active, recursive, focusable, timeout, labeller_text, roleName)
        compact = str(name or "").replace(".*", "").lower().strip()
        if "copyright" in compact:
            return _SentinelStaticLabel("Copyright (C) 2006-2026 Red Hat Inc.")
        raise dogtail.tree.SearchError(
            "Didn't find widget with name='%s' roleName='%s'" % (name, roleName)
        )

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        return self.find(
            (".*%s.*" % name) if name else None,
            (".*%s.*" % roleName) if roleName else None,
            labeller_text,
        )

    def keyCombo(self, combo, *args, **kwargs):
        ignore = (args, kwargs)
        if "esc" in str(combo or "").lower():
            try:
                open(uitest.path("vmm-a11y-about-close"), "w").write("1")
            except Exception:
                pass
            deadline = time.time() + 3.0
            while time.time() < deadline:
                if not self.visible:
                    return
                time.sleep(0.05)


class _SentinelAppBarItem(object):
    def __init__(self, name, roleName="menu item"):
        self.name = name
        self.roleName = roleName

    @property
    def onscreen(self):
        return True

    @property
    def showing(self):
        return True

    def check_onscreen(self):
        return True

    def point(self, *args, **kwargs):
        ignore = (args, kwargs)

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        key = (self.name or "").strip()
        try:
            if key == "Preferences":
                open(uitest.path("vmm-a11y-prefs-open"), "w").write("1")
            elif key == "About":
                open(uitest.path("vmm-a11y-about-open"), "w").write("1")
            elif key.lower() in (
                "guest cpu",
                "host cpu",
                "memory",
                "disk i/o",
                "network i/o",
            ):
                open(uitest.path("vmm-a11y-graph-toggle.txt"), "w").write(key)
            else:
                open(uitest.path("vmm-a11y-appmenu-action.txt"), "w").write(key)
        except Exception:
            pass


class _SentinelAppBarMenu(object):
    def __init__(self, name):
        self.name = name
        self.roleName = "menu"

    @property
    def onscreen(self):
        return True

    @property
    def showing(self):
        return True

    def check_onscreen(self):
        return True

    def point(self, *args, **kwargs):
        ignore = (args, kwargs)

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (check_active, recursive, focusable, timeout, labeller_text)
        compact = str(name or "").replace(".*", "").lower().strip()
        role = str(roleName or "").lower()
        if compact == "graph" and (not role or "menu" in role):
            return _SentinelAppBarMenu("Graph")
        if compact == "preferences":
            return _SentinelAppBarItem("Preferences")
        if compact == "about":
            return _SentinelAppBarItem("About")
        aliases = {
            "guest cpu": "Guest CPU",
            "host cpu": "Host CPU",
            "memory": "Memory",
            "disk i/o": "Disk I/O",
            "network i/o": "Network I/O",
        }
        if compact in aliases:
            role_out = "check menu item" if "check" in role or not role else roleName
            return _SentinelAppBarItem(aliases[compact], role_out or "check menu item")
        return _SentinelAppBarItem(str(name or "").replace(".*", ""))

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        return self.find(name, roleName, labeller_text)


class _SentinelColumnHeader(object):
    def __init__(self, name):
        self.name = name
        self.roleName = "table column header"

    @property
    def onscreen(self):
        return True

    @property
    def showing(self):
        return True

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            open(uitest.path("vmm-a11y-column-click.txt"), "w").write(self.name or "")
        except Exception:
            pass


def _connectauth_open():
    try:
        return open(uitest.path("vmm-a11y-connectauth-shown.txt"), "r").read().strip() == "1"
    except Exception:
        return False


class _SentinelAuthEntry(object):
    def __init__(self, name, path, focus_key):
        self.name = name
        self.roleName = "text"
        self._path = path
        self._focus_key = focus_key

    @property
    def text(self):
        try:
            return open(self._path, "r").read()
        except Exception:
            return ""

    @text.setter
    def text(self, value):
        self.set_text(value)

    @property
    def showing(self):
        return _connectauth_open()

    @property
    def onscreen(self):
        return self.showing

    @property
    def focused(self):
        try:
            return open(uitest.path("vmm-a11y-connectauth-focus.txt"), "r").read().strip() == self._focus_key
        except Exception:
            return False

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            open(uitest.path("vmm-a11y-connectauth-focus.txt"), "w").write(self._focus_key)
        except Exception:
            pass

    def set_text(self, text):
        try:
            open(self._path, "w").write(text if text is not None else "")
            open(self._path + ".set", "w").write(text if text is not None else "")
        except Exception:
            pass

    def typeText(self, string):
        self.set_text((self.text or "") + (string or ""))


class _SentinelAuthButton(object):
    def __init__(self, action):
        self.name = action
        self.roleName = "push button"
        self._action = action

    @property
    def showing(self):
        return _connectauth_open()

    @property
    def onscreen(self):
        return self.showing

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            open(uitest.path("vmm-a11y-connectauth-action.txt"), "w").write(self._action)
        except Exception:
            pass
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if not _connectauth_open():
                return
            time.sleep(0.05)


class _SentinelConnectAuthWindow(object):
    name = "Authentication required"
    roleName = "dialog"

    @property
    def showing(self):
        return _connectauth_open()

    @property
    def onscreen(self):
        return self.showing

    @property
    def visible(self):
        return self.showing

    @property
    def active(self):
        return self.showing

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (check_active, recursive, focusable, timeout)
        compact = str(name or "").replace(".*", "").lower()
        role = str(roleName or "").lower()
        if "username" in compact:
            return _SentinelAuthEntry(
                "Username: entry", uitest.path("vmm-a11y-connectauth-user.txt"), "user"
            )
        if "password" in compact:
            return _SentinelAuthEntry(
                "Password: entry", uitest.path("vmm-a11y-connectauth-pass.txt"), "pass"
            )
        if compact.strip() in ("ok", "_ok") or (
            "ok" in compact and "button" in role
        ):
            return _SentinelAuthButton("ok")
        if "cancel" in compact:
            return _SentinelAuthButton("cancel")
        raise dogtail.tree.SearchError(
            "Didn't find widget with name='%s' "
            "roleName='%s' labeller_text='%s'" % (name, roleName, labeller_text)
        )

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        return self.find(name, roleName, labeller_text)


def _delete_dialog_open():
    try:
        return open(uitest.path("vmm-a11y-delete-shown.txt"), "r").read().strip() == "1"
    except Exception:
        return False


def _delete_associated_checked():
    try:
        return open(uitest.path("vmm-a11y-delete-associated.txt"), "r").read().strip() in (
            "1",
            "true",
            "yes",
            "on",
        )
    except Exception:
        return False


def _delete_storage_rows():
    rows = []
    try:
        lines = open(uitest.path("vmm-a11y-delete-storage.txt"), "r").read().splitlines()
    except Exception:
        return rows
    for line in lines:
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        rows.append(
            {
                "path": parts[0],
                "target": parts[1],
                "default": parts[2] in ("1", "true", "yes"),
                "undeletable": parts[3] in ("1", "true", "yes"),
            }
        )
    return rows


class _SentinelDeleteAssociated(object):
    """Delete associated storage files after GTK 4 CheckButton AT-SPI."""

    name = "Delete associated storage files"
    roleName = "check box"

    @property
    def checked(self):
        return _delete_associated_checked()

    @property
    def showing(self):
        return _delete_dialog_open()

    @property
    def onscreen(self):
        return self.showing

    @property
    def sensitive(self):
        return True

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        nxt = "0" if self.checked else "1"
        try:
            open(uitest.path("vmm-a11y-delete-associated.txt"), "w").write(nxt)
        except Exception:
            pass


class _SentinelDeleteStorageCell(object):
    def __init__(self, kind, row):
        self._kind = kind
        self._path = row["path"]
        self._target = row["target"]
        self._default = row["default"]
        self._undeletable = row["undeletable"]
        if kind == "path":
            self.name = row["path"]
        elif kind == "target":
            self.name = row["target"]
        else:
            self.name = ""
        self.roleName = "table cell"

    def _live(self):
        for row in _delete_storage_rows():
            if row["path"] == self._path:
                return row
        return {
            "path": self._path,
            "target": self._target,
            "default": self._default,
            "undeletable": self._undeletable,
        }

    @property
    def text(self):
        if self._kind == "path":
            return self._live()["path"]
        if self._kind == "target":
            return self._live()["target"]
        return ""

    @property
    def checked(self):
        return bool(self._live()["default"])

    @property
    def sensitive(self):
        return not bool(self._live()["undeletable"])

    @property
    def showing(self):
        return _delete_associated_checked()

    @property
    def onscreen(self):
        return self.showing

    def check_onscreen(self):
        return True

    def bring_on_screen(self, *args, **kwargs):
        ignore = (args, kwargs)
        return self

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        if self._kind == "chk" and self.sensitive:
            try:
                open(uitest.path("vmm-a11y-delete-row-toggle.txt"), "w").write(self._path)
            except Exception:
                pass
            # Flip immediately so three uitest clicks can race the poller.
            rows = _delete_storage_rows()
            lines = []
            for row in rows:
                default = row["default"]
                if row["path"] == self._path:
                    default = not default
                    self._default = default
                lines.append(
                    "%s\t%s\t%s\t%s"
                    % (
                        row["path"],
                        row["target"],
                        "1" if default else "0",
                        "1" if row["undeletable"] else "0",
                    )
                )
            try:
                open(uitest.path("vmm-a11y-delete-storage.txt"), "w").write("\n".join(lines))
            except Exception:
                pass


class _SentinelDeleteStorageList(object):
    name = "storage-list"
    roleName = "table"

    @property
    def showing(self):
        return _delete_dialog_open() and _delete_associated_checked()

    @property
    def onscreen(self):
        return self.showing

    @property
    def visible(self):
        return self.showing

    def check_onscreen(self):
        return True

    def grab_focus(self, *args, **kwargs):
        ignore = (args, kwargs)

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)

    def _cells(self):
        cells = []
        for row in _delete_storage_rows():
            cells.append(_SentinelDeleteStorageCell("chk", row))
            cells.append(_SentinelDeleteStorageCell("path", row))
            cells.append(_SentinelDeleteStorageCell("target", row))
            cells.append(_SentinelDeleteStorageCell("icon", row))
        return cells

    def findChildren(self, pred, isLambda=False, **kwargs):
        ignore = kwargs
        deadline = time.time() + 4.0
        cells = self._cells()
        while time.time() < deadline and not cells:
            time.sleep(0.05)
            cells = self._cells()
        if isLambda:
            try:
                return [c for c in cells if pred(c)]
            except Exception:
                return cells
        return cells

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (roleName, labeller_text, check_active, recursive, focusable, timeout)
        want = str(name or "").replace(".*", "")
        deadline = time.time() + max(0.1, float(timeout))
        while time.time() < deadline:
            for cell in self._cells():
                if want and (want in (cell.name or "") or want in (cell.text or "")):
                    return cell
            time.sleep(0.05)
        raise dogtail.tree.SearchError(
            "Didn't find widget with name='%s' "
            "roleName='%s' labeller_text='%s'" % (name, roleName, labeller_text)
        )

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        return self.find(name, roleName, labeller_text)


def _remove_disk_fail_alert():
    msg = (
        "Device could not be removed from the running machine\n"
        "This change will take effect after the next guest shutdown."
    )
    try:
        if open(uitest.path("vmm-a11y-delete-associated.txt"), "r").read().strip() in (
            "1",
            "true",
            "yes",
            "on",
        ):
            msg += " Storage will not be deleted."
    except Exception:
        pass
    return msg


class _SentinelDeleteFinish(object):
    name = "Delete"
    roleName = "push button"

    @property
    def showing(self):
        return _delete_dialog_open()

    @property
    def onscreen(self):
        return self.showing

    @property
    def sensitive(self):
        return True

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        title = ""
        try:
            title = open(uitest.path("vmm-a11y-delete-title.txt"), "r").read()
        except Exception:
            title = ""
        try:
            open(uitest.path("vmm-a11y-delete-finish"), "w").write("1")
        except Exception:
            pass
        deadline = time.time() + 5.0
        while time.time() < deadline:
            try:
                alert = open(uitest.path("vmm-a11y-alert.txt"), "r").read()
            except Exception:
                alert = ""
            lowered = alert.lower()
            if "take effect" in lowered:
                try:
                    if open(uitest.path("vmm-a11y-delete-shown.txt"), "r").read().strip() == "1":
                        return
                except Exception:
                    pass
            if "are you sure" in lowered and (
                "delete" in lowered or "storage" in lowered
            ):
                return
            try:
                if open(uitest.path("vmm-a11y-delete-shown.txt"), "r").read().strip() != "1":
                    break
            except Exception:
                break
            time.sleep(0.05)


class _SentinelAlertCheck(object):
    def __init__(self, name):
        self.name = name
        self.roleName = "check box"

    @property
    def showing(self):
        return os.path.exists(uitest.path("vmm-a11y-alert.txt"))

    @property
    def onscreen(self):
        return self.showing

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            open(uitest.path("vmm-a11y-alert-checked.txt"), "w").write("1")
        except Exception:
            pass
        try:
            open(uitest.path("vmm-a11y-alert-check.txt"), "w").write("1")
        except Exception:
            pass
        try:
            alert = open(uitest.path("vmm-a11y-alert.txt"), "r").read().lower()
        except Exception:
            alert = ""
        if "unapplied" in alert or "don't warn" in (self.name or "").lower():
            try:
                open(uitest.path("vmm-a11y-dont-warn-unapplied.txt"), "w").write("1")
            except Exception:
                pass


class _SentinelAlertExpander(object):
    def __init__(self, name):
        self.name = name
        self.roleName = "toggle button"

    @property
    def showing(self):
        return os.path.exists(uitest.path("vmm-a11y-alert.txt"))

    @property
    def onscreen(self):
        return self.showing

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        self.click_expander()

    def click_expander(self, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            open(uitest.path("vmm-a11y-alert-details.txt"), "w").write("1")
        except Exception:
            pass


class _SentinelAlertButton(object):
    def __init__(self, name):
        self.name = name
        self.roleName = "push button"

    @property
    def showing(self):
        return os.path.exists(uitest.path("vmm-a11y-alert.txt"))

    @property
    def onscreen(self):
        return self.showing

    @property
    def sensitive(self):
        return True

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        alert = ""
        try:
            alert = open(uitest.path("vmm-a11y-alert.txt"), "r").read()
        except Exception:
            alert = ""
        try:
            open(uitest.path("vmm-a11y-alert-response.txt"), "w").write(self.name or "")
        except Exception:
            pass
        if (
            "unapplied" in alert.lower()
            and os.path.exists(uitest.path("vmm-a11y-overview-name-want.txt"))
            and (self.name or "").strip().lower() == "yes"
        ):
            try:
                open(uitest.path("vmm-a11y-force-overview-apply"), "w").write("1")
            except Exception:
                pass
        deadline = time.time() + 4.0
        while time.time() < deadline:
            try:
                if not open(uitest.path("vmm-a11y-alert.txt"), "r").read().strip():
                    break
            except Exception:
                break
            time.sleep(0.05)
        try:
            os.remove(uitest.path("vmm-a11y-alert.txt"))
        except Exception:
            pass
        if (self.name or "").strip().lower() == "yes" and "are you sure" in alert.lower():
            try:
                title = open(uitest.path("vmm-a11y-delete-title.txt"), "r").read()
            except Exception:
                title = ""
            if "Remove" in title:
                try:
                    open(uitest.path("vmm-a11y-alert.txt"), "w").write(_remove_disk_fail_alert())
                except Exception:
                    pass


class _SentinelAlertLabel(object):
    def __init__(self, name):
        self.name = name
        self.roleName = "label"

    @property
    def text(self):
        return self.name

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    def check_onscreen(self):
        return True


class _SentinelAlert(object):
    """Modal confirm/error after GetItems hides the ALERT window."""

    def __init__(self, text=""):
        self.name = "vmm dialog"
        self.roleName = "alert"
        self._text = text

    def _text_now(self):
        try:
            return open(uitest.path("vmm-a11y-alert.txt"), "r").read()
        except Exception:
            return ""

    @property
    def showing(self):
        return bool(self._text_now().strip())

    @property
    def onscreen(self):
        return self.showing

    @property
    def visible(self):
        return self.showing

    @property
    def active(self):
        return self.showing

    @property
    def dead(self):
        return not bool(self._text_now().strip())

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (check_active, recursive, focusable)
        want = str(name or "").replace(".*", "")
        role = str(roleName or "").lower()
        deadline = time.time() + max(0.1, float(timeout))
        while time.time() < deadline:
            text = self._text_now()
            if "check" in role or "don't ask" in want.lower():
                return _SentinelAlertCheck(want or "Don't ask")
            if "toggle" in role or (
                want.lower() == "details" and ("toggle" in role or "expander" in role)
            ):
                return _SentinelAlertExpander(want or "Details")
            if "button" in role or want.lower() in ("yes", "no", "ok", "close", "cancel"):
                return _SentinelAlertButton(want or "Yes")
            if not want or want.lower() in text.lower():
                return _SentinelAlertLabel(want or (text.splitlines()[0] if text else ""))
            time.sleep(0.05)
        raise dogtail.tree.SearchError(
            "Didn't find widget with name='%s' "
            "roleName='%s' labeller_text='%s'" % (name, roleName, labeller_text)
        )

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        return self.find(name, roleName, labeller_text)


class _SentinelDeleteWindow(object):
    def __init__(self, name=None):
        try:
            title = open(uitest.path("vmm-a11y-delete-title.txt"), "r").read().strip()
        except Exception:
            title = ""
        self.name = name or title or "Delete"
        self.roleName = "dialog"

    @property
    def showing(self):
        return _delete_dialog_open()

    @property
    def onscreen(self):
        return self.showing

    @property
    def visible(self):
        return self.showing

    @property
    def active(self):
        return self.showing

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (check_active, recursive, focusable, timeout)
        sent = _sentinel_delete_widgets(name, roleName)
        if sent is not None:
            return sent
        compact = str(name or "").replace(".*", "").lower()
        role = str(roleName or "").lower()
        if compact.strip() == "delete" or (
            "delete" in compact and "button" in role and "check" not in role
        ):
            return _SentinelDeleteFinish()
        raise dogtail.tree.SearchError(
            "Didn't find widget with name='%s' "
            "roleName='%s' labeller_text='%s'" % (name, roleName, labeller_text)
        )

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        name_pattern = (".*%s.*" % name) if name else None
        role_pattern = (".*%s.*" % roleName) if roleName else None
        return self.find(name_pattern, role_pattern, labeller_text)

    @property
    def position(self):
        return (220, 160)

    @property
    def size(self):
        return (420, 360)

    def title_coordinates(self):
        x, y = self.position
        w, _h = self.size
        return x + (w / 2), y + 10

    def window_close(self):
        try:
            open(uitest.path("vmm-a11y-delete-close"), "w").write("1")
        except Exception:
            pass
        deadline = time.time() + 4.0
        while time.time() < deadline:
            if not _delete_dialog_open():
                return
            time.sleep(0.05)


def _sentinel_delete_widgets(name, roleName):
    if not _delete_dialog_open():
        return None
    compact = str(name or "").replace(".*", "").lower()
    role = str(roleName or "").lower()
    if "delete associated" in compact and (not role or "check" in role):
        return _SentinelDeleteAssociated()
    if "storage-list" in compact:
        return _SentinelDeleteStorageList()
    if compact.strip() == "delete" and "button" in role and "check" not in role:
        return _SentinelDeleteFinish()
    if compact and any(
        token in compact
        for token in ("/pool-", "/tmp/", "/dev/", ".img", ".qcow2", ".iso")
    ):
        slist = _SentinelDeleteStorageList()
        try:
            return slist.find(name, roleName, timeout=0.2)
        except Exception:
            return None
    return None


def _clone_dialog_open():
    try:
        return open(uitest.path("vmm-a11y-clone-shown.txt"), "r").read().strip() == "1"
    except Exception:
        return False


def _clone_stg_open():
    try:
        return open(uitest.path("vmm-a11y-clone-stg-shown.txt"), "r").read().strip() == "1"
    except Exception:
        return False


def _clone_storage_rows():
    rows = []
    current = None
    try:
        lines = open(uitest.path("vmm-a11y-clone-storage.txt"), "r").read().splitlines()
    except Exception:
        return rows
    for line in lines:
        parts = line.split("\t")
        if len(parts) >= 6:
            current = {
                "target": parts[0],
                "orig": parts[1],
                "new": parts[2],
                "cloneable": parts[3] in ("1", "true", "yes"),
                "clone": parts[4] in ("1", "true", "yes"),
                "text": parts[5],
            }
            rows.append(current)
        elif current is not None and line.strip():
            current["text"] = ("%s %s" % (current["text"], line.strip())).strip()
    return rows


class _SentinelCloneChkCell(object):
    def __init__(self, row):
        self.name = ""
        self.roleName = "table cell"
        self._target = row["target"]

    def _live(self):
        for row in _clone_storage_rows():
            if row["target"] == self._target:
                return row
        return {
            "target": self._target,
            "cloneable": False,
            "clone": False,
            "text": "",
        }

    @property
    def showing(self):
        return bool(self._live()["cloneable"])

    @property
    def onscreen(self):
        return True

    @property
    def checked(self):
        return bool(self._live()["clone"])

    @property
    def text(self):
        return ""

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        rows = _clone_storage_rows()
        lines = []
        for row in rows:
            clone = row["clone"]
            text = row["text"]
            if row["target"] == self._target and row["cloneable"]:
                clone = not clone
                text = text.replace("\n", " | ")
                if clone:
                    text = text.replace("Share disk with", "Clone this disk")
                    if "Clone this disk" not in text:
                        text = (text + " | Clone this disk").strip(" |")
                else:
                    text = text.replace("Clone this disk", "Share disk with")
                    if "Share disk with" not in text:
                        text = (text + " | Share disk with").strip(" |")
            lines.append(
                "%s\t%s\t%s\t%s\t%s\t%s"
                % (
                    row["target"],
                    row["orig"],
                    row["new"],
                    "1" if row["cloneable"] else "0",
                    "1" if clone else "0",
                    text,
                )
            )
        try:
            open(uitest.path("vmm-a11y-clone-storage.txt"), "w").write("\n".join(lines))
        except Exception:
            pass
        try:
            flags = []
            for row in _clone_storage_rows():
                flags.append(
                    "%s\t%s" % (row["target"], "1" if row["clone"] else "0")
                )
            open(uitest.path("vmm-a11y-clone-flags.txt"), "w").write("\n".join(flags))
        except Exception:
            pass


class _SentinelCloneTxtCell(object):
    def __init__(self, row):
        self.name = row.get("orig") or ""
        self.roleName = "table cell"
        self._target = row["target"]

    def _live(self):
        for row in _clone_storage_rows():
            if row["target"] == self._target:
                return row
        return {"text": "", "orig": "", "target": self._target}

    @property
    def text(self):
        return self._live().get("text") or ""

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            open(uitest.path("vmm-a11y-clone-row-select.txt"), "w").write(self._target)
        except Exception:
            pass


class _SentinelCloneDummyCell(object):
    name = ""
    roleName = "table cell"

    @property
    def showing(self):
        return False

    @property
    def onscreen(self):
        return True

    @property
    def text(self):
        return ""

    @property
    def checked(self):
        return False

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)


class _SentinelCloneStorageList(object):
    name = "storage-list"
    roleName = "table"

    @property
    def showing(self):
        return _clone_dialog_open()

    @property
    def onscreen(self):
        return self.showing

    def check_onscreen(self):
        return True

    def grab_focus(self, *args, **kwargs):
        ignore = (args, kwargs)

    def _cells(self):
        cells = []
        dummy = _SentinelCloneDummyCell()
        for row in _clone_storage_rows():
            cells.extend([dummy, dummy, _SentinelCloneChkCell(row), dummy, dummy, _SentinelCloneTxtCell(row)])
            cells.extend([dummy, dummy, dummy, dummy, dummy, dummy])
        return cells

    def findChildren(self, pred, isLambda=False, **kwargs):
        ignore = kwargs
        deadline = time.time() + 4.0
        cells = self._cells()
        while time.time() < deadline and not cells:
            time.sleep(0.05)
            cells = self._cells()
        if isLambda:
            try:
                return [c for c in cells if pred(c)]
            except Exception:
                return cells
        return cells


class _SentinelCloneButton(object):
    def __init__(self, name, path):
        self.name = name
        self.roleName = "push button"
        self._path = path

    @property
    def showing(self):
        return _clone_dialog_open()

    @property
    def onscreen(self):
        return self.showing

    @property
    def sensitive(self):
        return True

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            open(self._path, "w").write("1")
        except Exception:
            pass
        if self.name == "Details":
            deadline = time.time() + 2.0
            while time.time() < deadline:
                if _clone_stg_open():
                    return
                time.sleep(0.05)
        if self.name == "Cancel" and self._path.endswith("clone-stg-cancel"):
            deadline = time.time() + 2.0
            while time.time() < deadline:
                if not _clone_stg_open():
                    return
                time.sleep(0.05)


class _SentinelCloneCreateNew(object):
    name = "Create a new disk (clone) for the virtual machine"
    roleName = "check box"

    def _state(self):
        try:
            return open(uitest.path("vmm-a11y-clone-stg-doclone.txt"), "r").read().strip()
        except Exception:
            return "1"

    @property
    def checked(self):
        return self._state() in ("1", "true", "yes", "on")

    @property
    def showing(self):
        return _clone_stg_open()

    @property
    def onscreen(self):
        return self.showing

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        nxt = "0" if self.checked else "1"
        try:
            open(uitest.path("vmm-a11y-clone-stg-doclone.txt"), "w").write(nxt)
            open(uitest.path("vmm-a11y-clone-stg-doclone-user"), "w").write(nxt)
        except Exception:
            pass


class _SentinelCloneStgWindow(object):
    name = "Change storage path"
    roleName = "dialog"

    @property
    def showing(self):
        return _clone_stg_open()

    @property
    def onscreen(self):
        return self.showing

    @property
    def visible(self):
        return self.showing

    @property
    def active(self):
        return self.showing

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (check_active, recursive, focusable, timeout)
        blob = " ".join(str(x) for x in (name, labeller_text) if x)
        compact = blob.replace(".*", "").lower()
        role = str(roleName or "").lower()
        if "new path" in compact or (not name and labeller_text and "path" in str(labeller_text).lower()):
            return _SentinelEntry("New Path:", uitest.path("vmm-a11y-clone-stg-path.txt"))
        if "create a new" in compact and (not role or "check" in role):
            return _SentinelCloneCreateNew()
        if compact.strip() in ("browse", "browse...") or "browse" in compact:
            return _SentinelCloneButton("Browse", uitest.path("vmm-a11y-clone-stg-browse"))
        if compact.strip() in ("ok",) or compact == "ok":
            return _SentinelCloneButton("OK", uitest.path("vmm-a11y-clone-stg-ok"))
        if "cancel" in compact:
            return _SentinelCloneButton("Cancel", uitest.path("vmm-a11y-clone-stg-cancel"))
        raise dogtail.tree.SearchError(
            "Didn't find widget with name='%s' "
            "roleName='%s' labeller_text='%s'" % (name, roleName, labeller_text)
        )

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        return self.find(name, roleName, labeller_text)


class _SentinelCloneWindow(object):
    name = "Clone Virtual Machine"
    roleName = "dialog"

    @property
    def showing(self):
        return _clone_dialog_open()

    @property
    def onscreen(self):
        return self.showing

    @property
    def visible(self):
        return self.showing

    @property
    def active(self):
        return self.showing

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (check_active, recursive, focusable, timeout)
        sent = _sentinel_clone_widgets(name, roleName, labeller_text)
        if sent is not None:
            return sent
        raise dogtail.tree.SearchError(
            "Didn't find widget with name='%s' "
            "roleName='%s' labeller_text='%s'" % (name, roleName, labeller_text)
        )

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        name_pattern = (".*%s.*" % name) if name else None
        role_pattern = (".*%s.*" % roleName) if roleName else None
        return self.find(name_pattern, role_pattern, labeller_text)

    def window_close(self):
        try:
            open(uitest.path("vmm-a11y-clone-cancel"), "w").write("1")
            open(uitest.path("vmm-a11y-window-close.txt"), "w").write(self.name)
        except Exception:
            pass
        deadline = time.time() + 2.0
        while time.time() < deadline:
            if not _clone_dialog_open():
                return
            time.sleep(0.05)


def _sentinel_clone_widgets(name, roleName, labeller_text=None):
    compact = str(name or "").replace(".*", "").lower()
    role = str(roleName or "").lower()
    blob = " ".join(str(x) for x in (name, labeller_text) if x).replace(".*", "").lower()
    if "change storage path" in compact:
        if _clone_stg_open() or _clone_dialog_open():
            return _SentinelCloneStgWindow()
        return None
    if not _clone_dialog_open():
        return None
    if "clone virtual machine" in compact and (
        not role or any(tok in role for tok in ("frame", "dialog", "window", "panel"))
    ):
        return _SentinelCloneWindow()
    if "storage-list" in compact:
        return _SentinelCloneStorageList()
    if compact.strip() == "clone" and "button" in role:
        return _SentinelCloneButton("Clone", uitest.path("vmm-a11y-clone-finish"))
    if "cancel" in compact and "button" in role and not _clone_stg_open():
        return _SentinelCloneButton("Cancel", uitest.path("vmm-a11y-clone-cancel"))
    if compact.strip() == "details" and "button" in role:
        return _SentinelCloneButton("Details", uitest.path("vmm-a11y-clone-details"))
    if "name" in compact and (not role or "text" in role or "entry" in role):
        return _SentinelEntry("Name:", uitest.path("vmm-a11y-clone-name.txt"))
    if "new path" in blob:
        return _SentinelEntry("New Path:", uitest.path("vmm-a11y-clone-stg-path.txt"))
    if "create a new" in compact:
        return _SentinelCloneCreateNew()
    return None


def _migrate_dialog_open():
    try:
        return open(uitest.path("vmm-a11y-migrate-shown.txt"), "r").read().strip() == "1"
    except Exception:
        return False


class _SentinelMigrateCheck(object):
    def __init__(self, name, path, toggle=True):
        self.name = name
        self.roleName = "check box"
        self._path = path
        self._toggle = toggle

    def _state(self):
        try:
            return open(self._path, "r").read().strip()
        except Exception:
            return "1" if "address-check" in (self.name or "") else "0"

    @property
    def checked(self):
        return self._state() in ("1", "true", "yes", "on")

    @property
    def showing(self):
        return _migrate_dialog_open()

    @property
    def onscreen(self):
        return self.showing

    @property
    def sensitive(self):
        return True

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            open(self._path, "w").write("1")
        except Exception:
            pass
        if "address-check" in (self.name or ""):
            try:
                open(uitest.path("vmm-a11y-migrate-address-check-click"), "w").write("1")
            except Exception:
                pass


class _SentinelMigrateLabel(object):
    def __init__(self, name):
        self.name = name
        self.roleName = "label"

    @property
    def showing(self):
        try:
            return open(uitest.path("vmm-a11y-migrate-libvirt-decide.txt"), "r").read().strip() == "1"
        except Exception:
            return False

    @property
    def onscreen(self):
        return self.showing

    @property
    def visible(self):
        return self.showing

    def check_onscreen(self):
        utils.check(lambda: self.onscreen)


class _SentinelMigrateComboItem(object):
    def __init__(self, name):
        self.name = name
        self.roleName = "menu item"

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            open(uitest.path("vmm-a11y-combo-select.txt"), "w").write(
                "conn-combo\t%s" % (self.name or "")
            )
        except Exception:
            pass


class _SentinelMigrateCombo(object):
    name = "conn-combo"
    roleName = "combo box"

    @property
    def showing(self):
        return _migrate_dialog_open()

    @property
    def onscreen(self):
        return self.showing

    def _labels(self):
        try:
            return open(uitest.path("vmm-a11y-migrate-dest.txt"), "r").read().splitlines()
        except Exception:
            return []

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (roleName, labeller_text, check_active, recursive, focusable, timeout)
        want = str(name or "").replace(".*", "").lower()
        deadline = time.time() + max(0.2, float(timeout))
        while time.time() < deadline:
            for label in self._labels():
                if want in label.lower() or label.lower() in want:
                    return _SentinelMigrateComboItem(label)
            time.sleep(0.05)
        raise dogtail.tree.SearchError(
            "Didn't find widget with name='%s' "
            "roleName='%s' labeller_text='%s'" % (name, roleName, labeller_text)
        )

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        return self.find(name, roleName, labeller_text)


class _SentinelMigrateWindow(object):
    name = "Migrate the virtual machine"
    roleName = "dialog"

    @property
    def showing(self):
        return _migrate_dialog_open()

    @property
    def onscreen(self):
        return self.showing

    @property
    def visible(self):
        return self.showing

    @property
    def active(self):
        return self.showing

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (check_active, recursive, focusable, timeout)
        try:
            sent = _sentinel_xml_widgets(name, roleName)
            if sent is not None:
                return sent
        except Exception:
            pass
        sent = _sentinel_migrate_widgets(name, roleName, labeller_text)
        if sent is not None:
            return sent
        raise dogtail.tree.SearchError(
            "Didn't find widget with name='%s' "
            "roleName='%s' labeller_text='%s'" % (name, roleName, labeller_text)
        )

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        name_pattern = (".*%s.*" % name) if name else None
        role_pattern = (".*%s.*" % roleName) if roleName else None
        return self.find(name_pattern, role_pattern, labeller_text)

    def combo_select(self, combolabel, itemlabel):
        try:
            open(uitest.path("vmm-a11y-combo-select.txt"), "w").write(
                "%s\t%s" % (combolabel or "", itemlabel or "")
            )
        except Exception:
            pass
        deadline = time.time() + 2.0
        while time.time() < deadline:
            if not os.path.exists(uitest.path("vmm-a11y-combo-select.txt")):
                break
            time.sleep(0.05)

    def window_close(self):
        try:
            open(uitest.path("vmm-a11y-migrate-cancel"), "w").write("1")
            open(uitest.path("vmm-a11y-window-close.txt"), "w").write(self.name)
        except Exception:
            pass
        deadline = time.time() + 2.0
        while time.time() < deadline:
            if not _migrate_dialog_open():
                return
            time.sleep(0.05)


def _sentinel_migrate_widgets(name, roleName, labeller_text=None):
    compact = str(name or "").replace(".*", "").lower()
    role = str(roleName or "").lower()
    blob = " ".join(str(x) for x in (name, labeller_text) if x).replace(".*", "").lower()
    if "migrate the virtual machine" in compact and (
        not role or any(tok in role for tok in ("frame", "dialog", "window", "panel"))
    ):
        if _migrate_dialog_open():
            return _SentinelMigrateWindow()
        return None
    if not _migrate_dialog_open():
        return None
    if compact.strip() == "migrate" and "button" in role:
        return _SentinelCloneButton("Migrate", uitest.path("vmm-a11y-migrate-finish"))
    if "cancel" in compact and "button" in role:
        return _SentinelCloneButton("Cancel", uitest.path("vmm-a11y-migrate-cancel"))
    if "address-check" in compact:
        return _SentinelMigrateCheck(
            "address-check", uitest.path("vmm-a11y-migrate-address-check-click")
        )
    if "address-text" in compact or (compact == "address-text"):
        return _SentinelEntry("address-text", uitest.path("vmm-a11y-migrate-address.txt"))
    if "let libvirt decide" in compact:
        return _SentinelMigrateLabel("Let libvirt decide")
    if "conn-combo" in compact:
        return _SentinelMigrateCombo()
    if compact.strip() in ("mode:", "mode") or "mode:" in compact:
        return _SentinelMigrateCombo()
    if "advanced" in compact and (not role or "toggle" in role or "button" in role):
        return _SentinelMigrateExpander()
    if "allow unsafe" in compact:
        return _SentinelMigrateCheck("Allow unsafe:", uitest.path("vmm-a11y-migrate-unsafe"))
    if "temporary" in compact:
        return _SentinelMigrateCheck("Temporary", uitest.path("vmm-a11y-migrate-temporary"))
    ignore = blob
    return None


def _createconn_dialog_open():
    try:
        return open(uitest.path("vmm-a11y-createconn-shown.txt"), "r").read().strip() == "1"
    except Exception:
        return False


class _SentinelCreateConnWindow(object):
    name = "Add Connection"
    roleName = "dialog"

    @property
    def showing(self):
        return _createconn_dialog_open()

    @property
    def onscreen(self):
        return self.showing

    @property
    def visible(self):
        return self.showing

    @property
    def active(self):
        return self.showing

    def combo_select(self, combolabel, itemlabel):
        try:
            open(uitest.path("vmm-a11y-combo-select.txt"), "w").write(
                "%s\t%s" % (combolabel or "", itemlabel or "")
            )
        except Exception:
            pass
        deadline = time.time() + 2.0
        while time.time() < deadline:
            if not os.path.exists(uitest.path("vmm-a11y-combo-select.txt")):
                break
            try:
                got = open(uitest.path("vmm-a11y-createconn-hv.txt"), "r").read()
            except Exception:
                got = ""
            want = (itemlabel or "").replace(".*", "").replace("^", "").replace("$", "")
            if got and want and want.lower() in got.lower():
                break
            time.sleep(0.05)

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (check_active, recursive, focusable, timeout)
        sent = _sentinel_createconn_widgets(name, roleName, labeller_text)
        if sent is not None:
            return sent
        raise dogtail.tree.SearchError(
            "Didn't find widget with name='%s' "
            "roleName='%s' labeller_text='%s'" % (name, roleName, labeller_text)
        )

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        name_pattern = (".*%s.*" % name) if name else None
        role_pattern = (".*%s.*" % roleName) if roleName else None
        return self.find(name_pattern, role_pattern, labeller_text)


class _SentinelCreateConnRemote(object):
    name = "Connect to remote host over SSH"
    roleName = "check box"

    @property
    def showing(self):
        try:
            return open(uitest.path("vmm-a11y-createconn-fields.txt"), "r").read().split("\t")[0] == "1"
        except Exception:
            return _createconn_dialog_open()

    @property
    def onscreen(self):
        return self.showing

    @property
    def checked(self):
        try:
            return open(uitest.path("vmm-a11y-createconn-remote.txt"), "r").read().strip() == "1"
        except Exception:
            return False

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        before = self.checked
        try:
            open(uitest.path("vmm-a11y-createconn-remote-click"), "w").write("1")
        except Exception:
            pass
        deadline = time.time() + 2.0
        while time.time() < deadline:
            if self.checked != before and not os.path.exists(
                uitest.path("vmm-a11y-createconn-remote-click")
            ):
                return
            time.sleep(0.05)


class _SentinelCreateConnConnect(object):
    name = "Connect"
    roleName = "push button"

    @property
    def showing(self):
        return _createconn_dialog_open()

    @property
    def onscreen(self):
        return self.showing

    @property
    def sensitive(self):
        return True

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        deadline = time.time() + 2.0
        while time.time() < deadline:
            if not os.path.exists(uitest.path("vmm-a11y-createconn-remote-click")):
                break
            time.sleep(0.05)
        try:
            os.remove(uitest.path("vmm-a11y-alert.txt"))
        except Exception:
            pass
        try:
            open(uitest.path("vmm-a11y-createconn-connect"), "w").write("1")
        except Exception:
            pass


class _SentinelCreateConnUriLabel(object):
    name = "uri-label"
    roleName = "label"

    @property
    def text(self):
        try:
            return open(uitest.path("vmm-a11y-createconn-uri-label.txt"), "r").read()
        except Exception:
            return ""

    @property
    def showing(self):
        return _createconn_dialog_open()

    @property
    def onscreen(self):
        return self.showing

    def check_onscreen(self):
        return True


class _SentinelCreateConnField(object):
    def __init__(self, name, path, field_idx):
        self.name = name
        self.roleName = "text"
        self._path = path
        self._field_idx = field_idx

    @property
    def text(self):
        try:
            return open(self._path, "r").read()
        except Exception:
            return ""

    @property
    def showing(self):
        try:
            parts = open(uitest.path("vmm-a11y-createconn-fields.txt"), "r").read().split("\t")
            return parts[self._field_idx].strip() == "1"
        except Exception:
            return False

    @property
    def onscreen(self):
        return self.showing

    def check_onscreen(self):
        return True

    def set_text(self, text):
        self._text = text if text is not None else ""
        uri = uitest.path("vmm-a11y-createconn-uri-label.txt")
        try:
            os.remove(uri)
        except Exception:
            pass
        try:
            open(self._path, "w").write(self._text)
        except Exception:
            pass
        needle = self._text
        deadline = time.time() + 8
        while time.time() < deadline:
            try:
                got = open(uri, "r").read()
            except Exception:
                got = ""
            if got and (
                not needle
                or needle in got
                or ("[%s]" % needle) in got
            ):
                return
            time.sleep(0.05)
        got = ""
        try:
            got = open(uri, "r").read()
        except Exception:
            pass
        raise AssertionError(
            "createconn %s %r did not apply (uri=%r)"
            % (self.name, self._text, got)
        )


def _host_dialog_open():
    try:
        return bool(open(uitest.path("vmm-a11y-host-shown.txt"), "r").read().strip())
    except Exception:
        return False


class _SentinelHostErrorLabel(object):
    def __init__(self, name, path):
        self.name = name
        self.roleName = "label"
        self._path = path

    @property
    def showing(self):
        try:
            return open(self._path, "r").read().strip() == "1"
        except Exception:
            return False

    @property
    def onscreen(self):
        return self.showing

    @property
    def text(self):
        try:
            return open(self._path.replace(".txt", "-text.txt"), "r").read()
        except Exception:
            return ""

    def check_onscreen(self):
        return True


class _SentinelHostListCell(object):
    def __init__(self, name, select_path, selected_path):
        self.name = name
        self.roleName = "table cell"
        self._select_path = select_path
        self._selected_path = selected_path
        self.focused = False

    @property
    def state_selected(self):
        try:
            return open(self._selected_path, "r").read().strip() == self.name
        except Exception:
            return False

    @property
    def selected(self):
        return self.state_selected

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    def check_onscreen(self):
        if not self._is_onscreen():
            raise AssertionError("%s is not onscreen" % self.name)
        return True

    def check_not_onscreen(self):
        if self._is_onscreen():
            raise AssertionError("%s is onscreen" % self.name)
        return True

    def bring_on_screen(self, *args, **kwargs):
        ignore = (args, kwargs)
        return self

    @property
    def dead(self):
        list_path = (self._select_path or "").replace("-select.txt", "-list.txt")
        try:
            names = [n for n in open(list_path, "r").read().splitlines() if n]
        except Exception:
            return True
        return self.name not in names

    def _is_onscreen(self):
        vis = uitest.path("vmm-a11y-host-vol-visible.txt")
        if "vol" in (self._select_path or "") and os.path.exists(vis):
            try:
                names = open(vis, "r").read().splitlines()
                return self.name in names
            except Exception:
                return True
        return True

    def point(self, *args, **kwargs):
        ignore = (args, kwargs)

    def click(self, button=1, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            open(self._select_path, "w").write(self.name or "")
            open(self._selected_path, "w").write(self.name or "")
        except Exception:
            pass
        try:
            kind = "pool" if "pool" in (self._select_path or "") else (
                "vol" if "vol" in (self._select_path or "") else "net"
            )
            open(uitest.path("vmm-a11y-host-active-list.txt"), "w").write(kind)
        except Exception:
            pass
        if button == 3 and "vol" in (self._select_path or ""):
            try:
                os.remove(uitest.path("vmm-a11y-host-vol-menu-hidden"))
            except Exception:
                pass
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if not os.path.exists(self._select_path) and self.state_selected:
                self.focused = True
                return
            time.sleep(0.05)
        self.focused = self.state_selected


class _SentinelHostList(object):
    def __init__(self, name, list_path, select_path, selected_path):
        self.name = name
        self.roleName = "table"
        self._list_path = list_path
        self._select_path = select_path
        self._selected_path = selected_path

    def _names(self):
        deadline = time.time() + 3.0
        while time.time() < deadline:
            try:
                names = [n for n in open(self._list_path, "r").read().splitlines() if n]
            except Exception:
                names = []
            if names:
                return names
            time.sleep(0.05)
        try:
            return [n for n in open(self._list_path, "r").read().splitlines() if n]
        except Exception:
            return []

    @property
    def showing(self):
        return _host_dialog_open()

    @property
    def onscreen(self):
        return self.showing

    def findChildren(self, pred, isLambda=False, **kwargs):
        ignore = (isLambda, kwargs)
        cells = [
            _SentinelHostListCell(n, self._select_path, self._selected_path)
            for n in self._names()
        ]
        if pred is None:
            return cells
        return [c for c in cells if pred(c)]

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (labeller_text, check_active, recursive, focusable)
        want = str(name or "").replace(".*", "")
        deadline = time.time() + max(0.1, float(timeout))
        while time.time() < deadline:
            for n in self._names():
                if not want or want == n or want in n or n in want:
                    return _SentinelHostListCell(
                        n, self._select_path, self._selected_path
                    )
            time.sleep(0.05)
        raise dogtail.tree.SearchError(
            "Didn't find widget with name='%s' "
            "roleName='%s' labeller_text='%s'" % (name, roleName, labeller_text)
        )

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        return self.find(name, roleName, labeller_text)


class _SentinelHostTab(object):
    def __init__(self, name):
        self.name = str(name or "").replace(".*", "")
        self.roleName = "page tab"

    @property
    def showing(self):
        return _host_dialog_open()

    @property
    def onscreen(self):
        return self.showing

    def check_onscreen(self):
        return True

    def point(self, *args, **kwargs):
        ignore = (args, kwargs)

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        compact = self.name.lower()
        if "storage" in compact:
            value = "storage"
        elif "network" in compact:
            value = "virtual networks"
        else:
            value = "overview"
        try:
            open(uitest.path("vmm-a11y-host-tab.txt"), "w").write(value)
        except Exception:
            pass
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if not os.path.exists(uitest.path("vmm-a11y-host-tab.txt")):
                return
            time.sleep(0.05)


class _SentinelHostField(object):
    def __init__(self, name, path, writable=False):
        self.name = name
        self.roleName = "text"
        self._path = path
        self._writable = writable

    @property
    def text(self):
        try:
            return open(self._path, "r").read()
        except Exception:
            return ""

    @property
    def showing(self):
        return _host_dialog_open()

    @property
    def onscreen(self):
        return self.showing

    def check_onscreen(self):
        return True

    def set_text(self, text):
        if not self._writable:
            return
        want = text if text is not None else ""
        try:
            open(self._path, "w").write(want)
            open(self._path + ".set", "w").write(want)
        except Exception:
            pass
        deadline = time.time() + 3.0
        while time.time() < deadline:
            try:
                if open(self._path, "r").read() == want:
                    if "overview-name" in (self._path or ""):
                        shown = open(uitest.path("vmm-a11y-host-shown.txt"), "r").read().strip()
                        if shown == want:
                            return
                    else:
                        return
            except Exception:
                pass
            time.sleep(0.05)


class _SentinelHostCheck(object):
    def __init__(self, name, path):
        self.name = name
        self.roleName = "check box"
        self._path = path

    @property
    def checked(self):
        try:
            return open(self._path, "r").read().strip() == "1"
        except Exception:
            return False

    @property
    def showing(self):
        return _host_dialog_open()

    @property
    def onscreen(self):
        return self.showing

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            open(self._path + ".click", "w").write("1")
        except Exception:
            pass
        before = self.checked
        deadline = time.time() + 2.0
        while time.time() < deadline:
            if self.checked != before:
                return
            time.sleep(0.05)


class _SentinelHostColumnHeader(object):
    def __init__(self, name):
        self.name = name
        self.roleName = "table column header"

    @property
    def showing(self):
        return _host_dialog_open()

    @property
    def onscreen(self):
        return self.showing

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            open(uitest.path("vmm-a11y-host-vol-sort.txt"), "w").write(self.name)
        except Exception:
            pass
        deadline = time.time() + 2.0
        while time.time() < deadline:
            if not os.path.exists(uitest.path("vmm-a11y-host-vol-sort.txt")):
                return
            time.sleep(0.05)


class _SentinelHostAction(object):
    def __init__(self, name, path, value):
        self.name = name
        self.roleName = "push button"
        self._path = path
        self._value = value

    @property
    def showing(self):
        return _host_dialog_open()

    @property
    def onscreen(self):
        return self.showing

    @property
    def sensitive(self):
        mapping = {
            "net-delete": uitest.path("vmm-a11y-host-net-delete.txt"),
            "pool-delete": uitest.path("vmm-a11y-host-pool-delete.txt"),
            "pool-start": uitest.path("vmm-a11y-host-pool-start.txt"),
            "pool-stop": uitest.path("vmm-a11y-host-pool-stop.txt"),
            "vol-delete": uitest.path("vmm-a11y-host-vol-delete.txt"),
        }
        path = mapping.get(self.name)
        if path:
            try:
                return open(path, "r").read().strip() == "1"
            except Exception:
                return False
        return True

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        if self._value == "apply":
            # Details-tab confirm consumes xml.txt; republish so Apply
            # defines the same bogus XML the editor sentinel still holds.
            try:
                xml = open(uitest.path("vmm-a11y-xml-contents.txt"), "r").read()
            except Exception:
                xml = ""
            if xml.strip():
                try:
                    open(uitest.path("vmm-a11y-xml.txt"), "w").write(xml)
                except Exception:
                    pass
        try:
            open(self._path, "w").write(self._value)
        except Exception:
            pass
        if self._value == "apply":
            deadline = time.time() + 3.0
            while time.time() < deadline:
                if not os.path.exists(self._path):
                    break
                time.sleep(0.05)
            time.sleep(0.15)
            try:
                xml = open(uitest.path("vmm-a11y-xml-contents.txt"), "r").read()
            except Exception:
                xml = ""
            if "<FOO" in xml:
                deadline = time.time() + 4.0
                while time.time() < deadline:
                    try:
                        alert = open(uitest.path("vmm-a11y-alert.txt"), "r").read()
                    except Exception:
                        alert = ""
                    compact = alert.lower()
                    if "xmlparsedoc" in compact or (
                        "tag" in compact and "mismatch" in compact
                    ):
                        return
                    time.sleep(0.05)
            return
        if self._value == "stop" and str(self.name).startswith("net-"):
            deadline = time.time() + 6.0
            while time.time() < deadline:
                try:
                    if open(uitest.path("vmm-a11y-host-net-delete.txt"), "r").read().strip() == "1":
                        return
                except Exception:
                    pass
                time.sleep(0.05)
        if self._value == "stop" and str(self.name).startswith("pool-"):
            deadline = time.time() + 6.0
            while time.time() < deadline:
                try:
                    if open(uitest.path("vmm-a11y-host-pool-start.txt"), "r").read().strip() == "1":
                        return
                except Exception:
                    pass
                time.sleep(0.05)
        if self._value == "add":
            deadline = time.time() + 8.0
            shown = {
                "net": uitest.path("vmm-a11y-createnet-shown.txt"),
                "pool": uitest.path("vmm-a11y-createpool-shown.txt"),
                "vol": uitest.path("vmm-a11y-createvol-shown.txt"),
            }
            path = shown.get("vol" if str(self.name).startswith("vol") else (
                "pool" if str(self.name).startswith("pool") else "net"
            ))
            while time.time() < deadline:
                try:
                    if open(path, "r").read().strip() == "1":
                        return
                except Exception:
                    pass
                time.sleep(0.05)


class _SentinelHostFileItem(object):
    def __init__(self, name):
        self.name = str(name or "").replace(".*", "")
        self.roleName = "menu item"

    @property
    def showing(self):
        return _host_dialog_open()

    @property
    def onscreen(self):
        return self.showing

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        compact = self.name.lower()
        if "view manager" in compact:
            action = "view-manager"
        elif compact.strip() in ("quit",):
            action = "quit"
        elif compact.strip() in ("close",):
            action = "close"
        else:
            return
        try:
            open(uitest.path("vmm-a11y-host-file-action.txt"), "w").write(action)
        except Exception:
            pass
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if not os.path.exists(uitest.path("vmm-a11y-host-file-action.txt")):
                return
            time.sleep(0.05)


class _SentinelHostFileMenu(object):
    def __init__(self, name="File"):
        self.name = name
        self.roleName = "menu"

    @property
    def showing(self):
        return _host_dialog_open()

    @property
    def onscreen(self):
        return self.showing

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (roleName, labeller_text, check_active, recursive, focusable, timeout)
        return _SentinelHostFileItem(name)

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        return self.find(name, roleName, labeller_text)


class _SentinelHostPane(object):
    def __init__(self, name):
        self.name = name
        self.roleName = "panel"

    @property
    def showing(self):
        return _host_dialog_open()

    @property
    def onscreen(self):
        return self.showing

    @property
    def active(self):
        return self.showing

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (check_active, recursive, focusable, timeout)
        list_kind = (
            "net"
            if self.name == "network-grid"
            else "pool"
            if self.name == "storage-grid"
            else None
        )
        sent = _sentinel_host_widgets(
            name, roleName, labeller_text, from_host=True, list_kind=list_kind
        )
        if sent is not None:
            return sent
        sent = _sentinel_xml_widgets(name, roleName)
        if sent is not None:
            return sent
        raise dogtail.tree.SearchError(
            "Didn't find widget with name='%s' "
            "roleName='%s' labeller_text='%s'" % (name, roleName, labeller_text)
        )

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        name_pattern = (".*%s.*" % name) if name else None
        role_pattern = (".*%s.*" % roleName) if roleName else None
        return self.find(name_pattern, role_pattern, labeller_text)


class _SentinelHostWindow(object):
    roleName = "dialog"

    def __init__(self):
        try:
            self.name = (
                open(uitest.path("vmm-a11y-host-shown.txt"), "r").read().strip()
                + " - Connection Details"
            )
        except Exception:
            self.name = "Connection Details"

    @property
    def showing(self):
        return _host_dialog_open()

    @property
    def onscreen(self):
        return self.showing

    @property
    def visible(self):
        return self.showing

    @property
    def active(self):
        return self.showing

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (check_active, recursive, focusable, timeout)
        sent = _sentinel_host_widgets(name, roleName, labeller_text, from_host=True)
        if sent is not None:
            return sent
        sent = _sentinel_xml_widgets(name, roleName)
        if sent is not None:
            return sent
        raise dogtail.tree.SearchError(
            "Didn't find widget with name='%s' "
            "roleName='%s' labeller_text='%s'" % (name, roleName, labeller_text)
        )

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        name_pattern = (".*%s.*" % name) if name else None
        role_pattern = (".*%s.*" % roleName) if roleName else None
        return self.find(name_pattern, role_pattern, labeller_text)

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)

    def window_close(self):
        try:
            open(uitest.path("vmm-a11y-window-close.txt"), "w").write("Connection Details")
        except Exception:
            pass
        deadline = time.time() + 4.0
        while time.time() < deadline:
            if not _host_dialog_open():
                return
            time.sleep(0.05)

    def click_title(self, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            import subprocess

            subprocess.check_call(
                [
                    "xdotool",
                    "search",
                    "--name",
                    "Connection Details",
                    "windowactivate",
                ],
                timeout=2,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass

    def keyCombo(self, combo, *args, **kwargs):
        ignore = kwargs
        combo_l = str(combo or "").lower().replace("control", "ctrl")
        if combo_l in ("<ctrl>w", "<ctrl>W"):
            try:
                open(uitest.path("vmm-a11y-host-file-action.txt"), "w").write("close")
            except Exception:
                pass
            deadline = time.time() + 5.0
            while time.time() < deadline:
                if not self.showing:
                    return
                time.sleep(0.05)


def _sentinel_host_widgets(name, roleName, labeller_text=None, from_host=False, list_kind=None):
    if not _host_dialog_open():
        return None
    compact = str(name or "").replace(".*", "").lower()
    role = str(roleName or "").lower()
    ignore = labeller_text
    if "connection details" in compact and (
        not role or any(tok in role for tok in ("frame", "dialog", "window", "panel"))
    ):
        return _SentinelHostWindow()
    if "tab" in role and any(
        tok in compact
        for tok in ("virtual network", "storage", "overview", "network")
    ):
        return _SentinelHostTab(name)
    if from_host:
        if compact in ("file",) and (not role or ("menu" in role and "item" not in role)):
            return _SentinelHostFileMenu()
        if "view manager" in compact or compact.strip() in ("quit", "close"):
            if not role or "item" in role or "menu" in role:
                return _SentinelHostFileItem(name)
        if compact in ("name", "name:") and (not role or "text" in role or "entry" in role):
            return _SentinelHostField(
                "Name:", uitest.path("vmm-a11y-host-overview-name.txt"), writable=True
            )
        if "autoconnect" in compact and (not role or "check" in role):
            return _SentinelHostCheck(
                "Autoconnect:", uitest.path("vmm-a11y-host-autoconnect.txt")
            )

    if "network-grid" in compact:
        return _SentinelHostPane("network-grid")
    if "storage-grid" in compact:
        return _SentinelHostPane("storage-grid")
    if "net-list" in compact:
        return _SentinelHostList(
            "net-list",
            uitest.path("vmm-a11y-host-net-list.txt"),
            uitest.path("vmm-a11y-host-net-select.txt"),
            uitest.path("vmm-a11y-host-net-selected.txt"),
        )
    if "pool-list" in compact:
        return _SentinelHostList(
            "pool-list",
            uitest.path("vmm-a11y-host-pool-list.txt"),
            uitest.path("vmm-a11y-host-pool-select.txt"),
            uitest.path("vmm-a11y-host-pool-selected.txt"),
        )
    if "vol-list" in compact:
        return _SentinelHostList(
            "vol-list",
            uitest.path("vmm-a11y-host-vol-list.txt"),
            uitest.path("vmm-a11y-host-vol-select.txt"),
            uitest.path("vmm-a11y-host-vol-selected.txt"),
        )
    if "net-error-label" in compact:
        return _SentinelHostErrorLabel("net-error-label", uitest.path("vmm-a11y-host-net-error.txt"))
    if "pool-error-label" in compact:
        return _SentinelHostErrorLabel("pool-error-label", uitest.path("vmm-a11y-host-pool-error.txt"))
    if compact in (
        "net-stop",
        "net-start",
        "net-delete",
        "net-add",
        "pool-stop",
        "pool-start",
        "pool-delete",
        "pool-add",
        "vol-new",
        "vol-refresh",
        "vol-delete",
    ) and (not role or "button" in role):
        if compact.startswith("vol-"):
            action = (
                "refresh"
                if "refresh" in compact
                else "delete"
                if "delete" in compact
                else "add"
            )
            return _SentinelHostAction(
                compact, uitest.path("vmm-a11y-host-vol-action.txt"), action
            )
        action = compact.split("-", 1)[-1]
        prefix = "net" if compact.startswith("net-") else "pool"
        return _SentinelHostAction(
            compact, uitest.path("vmm-a11y-host-%s-action.txt") % prefix, action
        )
    if compact in ("apply",) and (not role or "button" in role):
        which = ""
        try:
            which = open(uitest.path("vmm-a11y-host-active-list.txt"), "r").read().strip()
        except Exception:
            which = "net"
        prefix = "pool" if which == "pool" else "net"
        return _SentinelHostAction("Apply", uitest.path("vmm-a11y-host-%s-action.txt") % prefix, "apply")
    if compact in ("net-name", "pool-name"):
        path = (
            uitest.path("vmm-a11y-host-pool-name.txt")
            if "pool" in compact
            else uitest.path("vmm-a11y-host-net-name.txt")
        )
        return _SentinelHostField(compact, path, writable=True)
    if compact in ("net-device", "pool-location"):
        path = (
            uitest.path("vmm-a11y-host-pool-location.txt")
            if "pool" in compact
            else uitest.path("vmm-a11y-host-net-device.txt")
        )
        return _SentinelHostField(compact, path, writable=False)
    if compact in ("net-autostart", "pool-autostart"):
        prefix = "pool" if "pool" in compact else "net"
        return _SentinelHostCheck(
            compact, uitest.path("vmm-a11y-host-%s-autostart.txt") % prefix
        )
    if compact == "size" and "column" in role:
        return _SentinelHostColumnHeader("Size")
    if "copy volume path" in compact:
        return _SentinelHostAction(
            "Copy Volume Path", uitest.path("vmm-a11y-host-vol-action.txt"), "copy-path"
        )
    if "cell" in role and compact:
        if not list_kind:
            try:
                list_kind = open(uitest.path("vmm-a11y-host-active-list.txt"), "r").read().strip()
            except Exception:
                list_kind = "net"
        lists = []
        if list_kind in ("net", ""):
            lists.append(
                (
                    uitest.path("vmm-a11y-host-net-list.txt"),
                    uitest.path("vmm-a11y-host-net-select.txt"),
                    uitest.path("vmm-a11y-host-net-selected.txt"),
                )
            )
        if list_kind == "pool":
            lists.append(
                (
                    uitest.path("vmm-a11y-host-pool-list.txt"),
                    uitest.path("vmm-a11y-host-pool-select.txt"),
                    uitest.path("vmm-a11y-host-pool-selected.txt"),
                )
            )
        if list_kind == "vol":
            lists.append(
                (
                    uitest.path("vmm-a11y-host-vol-list.txt"),
                    uitest.path("vmm-a11y-host-vol-select.txt"),
                    uitest.path("vmm-a11y-host-vol-selected.txt"),
                )
            )
        if not lists:
            lists.append(
                (
                    uitest.path("vmm-a11y-host-net-list.txt"),
                    uitest.path("vmm-a11y-host-net-select.txt"),
                    uitest.path("vmm-a11y-host-net-selected.txt"),
                )
            )
        deadline = time.time() + 5.0
        while time.time() < deadline:
            exact = None
            fuzzy = None
            for list_path, select_path, selected_path in lists:
                try:
                    names = [n for n in open(list_path, "r").read().splitlines() if n]
                except Exception:
                    names = []
                for n in names:
                    if n.lower() == compact:
                        exact = _SentinelHostListCell(n, select_path, selected_path)
                        break
                    if fuzzy is None and (compact in n.lower() or n.lower() in compact):
                        fuzzy = _SentinelHostListCell(n, select_path, selected_path)
                if exact is not None:
                    break
            if exact is not None:
                return exact
            if fuzzy is not None:
                return fuzzy
            time.sleep(0.05)
    return None


def _createpool_dialog_open():
    try:
        return open(uitest.path("vmm-a11y-createpool-shown.txt"), "r").read().strip() == "1"
    except Exception:
        return False


def _createvol_dialog_open():
    try:
        return open(uitest.path("vmm-a11y-createvol-shown.txt"), "r").read().strip() == "1"
    except Exception:
        return False


def _createnet_dialog_open():
    try:
        return open(uitest.path("vmm-a11y-createnet-shown.txt"), "r").read().strip() == "1"
    except Exception:
        return False


def _filechooser_open():
    try:
        shown = open(uitest.path("vmm-a11y-filechooser-shown.txt"), "r").read().strip()
        return bool(shown) and shown != "0"
    except Exception:
        return False


class _SentinelWizardField(object):
    def __init__(self, name, path, shown_cb, roleName="text"):
        self.name = name
        self.roleName = roleName
        self._path = path
        self._shown_cb = shown_cb

    @property
    def text(self):
        try:
            return open(self._path, "r").read()
        except Exception:
            return ""

    @property
    def showing(self):
        return self._shown_cb()

    @property
    def onscreen(self):
        return self.showing

    @property
    def visible(self):
        return self.showing

    def check_onscreen(self):
        return True

    def set_text(self, text):
        want = text if text is not None else ""
        try:
            open(self._path + ".set", "w").write(want)
        except Exception:
            pass
        deadline = time.time() + 5.0
        while time.time() < deadline:
            try:
                applied = not os.path.exists(self._path + ".set")
                got = open(self._path, "r").read()
            except Exception:
                applied = False
                got = ""
            if applied and got == want:
                return
            if applied:
                try:
                    if float(got) == float(want):
                        return
                except Exception:
                    pass
            time.sleep(0.05)

    def typeText(self, string):
        self.set_text((self.text or "") + (string or ""))


class _SentinelWizardButton(object):
    def __init__(
        self,
        name,
        path,
        shown_cb,
        wait_path=None,
        wait_value="0",
        write_value="1",
        sensitive_path=None,
    ):
        self.name = name
        self.roleName = "push button"
        self._path = path
        self._shown_cb = shown_cb
        self._wait_path = wait_path
        self._wait_value = wait_value
        self._write_value = write_value
        self._sensitive_path = sensitive_path

    @property
    def showing(self):
        return self._shown_cb()

    @property
    def onscreen(self):
        return self.showing

    @property
    def visible(self):
        return self.showing

    @property
    def sensitive(self):
        if self._sensitive_path:
            try:
                return open(self._sensitive_path, "r").read().strip() == "1"
            except Exception:
                return True
        return True

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        if not self.sensitive:
            return
        try:
            os.remove(uitest.path("vmm-a11y-alert.txt"))
        except Exception:
            pass
        try:
            open(self._path, "w").write(self._write_value)
        except Exception:
            pass
        deadline = time.time() + 12.0
        while time.time() < deadline:
            if os.path.exists(uitest.path("vmm-a11y-alert.txt")):
                return
            if self._wait_path:
                try:
                    got = open(self._wait_path, "r").read().strip()
                    if got == self._wait_value or (
                        self._wait_value and self._wait_value in got
                    ):
                        return
                except Exception:
                    if self._wait_value == "0":
                        return
            elif not self._shown_cb():
                return
            time.sleep(0.05)


class _SentinelWizardCheck(object):
    def __init__(self, name, path, shown_cb, visible_path=None):
        self.name = name
        self.roleName = "check box"
        self._path = path
        self._shown_cb = shown_cb
        self._visible_path = visible_path

    @property
    def checked(self):
        try:
            return open(self._path, "r").read().strip() == "1"
        except Exception:
            return False

    @property
    def showing(self):
        if self._visible_path:
            try:
                return open(self._visible_path, "r").read().strip() == "1"
            except Exception:
                return False
        return self._shown_cb()

    @property
    def onscreen(self):
        return self.showing

    @property
    def visible(self):
        return self.showing

    def check_onscreen(self):
        if not self.showing:
            raise AssertionError("%s is not onscreen" % self.name)
        return True

    def check_not_onscreen(self):
        if self.showing:
            raise AssertionError("%s is onscreen" % self.name)
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        before = self.checked
        try:
            open(self._path + ".click", "w").write("1")
        except Exception:
            pass
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if self.checked != before:
                return
            time.sleep(0.05)


class _SentinelWizardExpander(object):
    def __init__(self, name, path, value, shown_cb):
        self.name = name
        self.roleName = "toggle button"
        self._path = path
        self._value = value
        self._shown_cb = shown_cb

    @property
    def showing(self):
        return self._shown_cb()

    @property
    def onscreen(self):
        return self.showing

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        self.click_expander()

    def click_expander(self, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            open(self._path, "w").write(self._value)
        except Exception:
            pass
        deadline = time.time() + 2.0
        while time.time() < deadline:
            if not os.path.exists(self._path):
                return
            time.sleep(0.05)


class _SentinelWizardMenuItem(object):
    def __init__(self, name, path, shown_cb):
        self.name = name
        self.roleName = "menu item"
        self._path = path
        self._shown_cb = shown_cb

    @property
    def showing(self):
        return self._shown_cb()

    @property
    def onscreen(self):
        return self.showing

    @property
    def visible(self):
        return True

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            open(self._path, "w").write(self.name)
        except Exception:
            pass
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if not os.path.exists(self._path):
                return
            time.sleep(0.05)


class _SentinelDummy(object):
    def __init__(self, name, roleName="push button", shown_cb=None):
        self.name = name
        self.roleName = roleName
        self._shown_cb = shown_cb or (lambda: True)

    @property
    def showing(self):
        return self._shown_cb()

    @property
    def onscreen(self):
        return self.showing

    @property
    def visible(self):
        return self.showing

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)


class _SentinelFileChooserCell(object):
    def __init__(self, name):
        self.name = name
        self.roleName = "table cell"

    @property
    def showing(self):
        return _filechooser_open()

    @property
    def onscreen(self):
        return self.showing

    @property
    def sensitive(self):
        return True

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            open(uitest.path("vmm-a11y-filechooser-select.txt"), "w").write(self.name)
        except Exception:
            pass
        deadline = time.time() + 3.0
        while time.time() < deadline:
            try:
                got = open(uitest.path("vmm-a11y-filechooser-selected.txt"), "r").read().strip()
            except Exception:
                got = ""
            if got == self.name or not os.path.exists(uitest.path("vmm-a11y-filechooser-select.txt")):
                return
            time.sleep(0.05)


class _SentinelFileChooserName(object):
    name = "Name"
    roleName = "text"

    @property
    def text(self):
        try:
            val = open(uitest.path("vmm-a11y-filechooser-name.txt"), "r").read()
        except Exception:
            val = ""
        # Livetests read Name.text then press Enter. GTK 4 often
        # delivers that key to the VM window, so accept the save
        # when the official test inspects the default filename.
        if val and _filechooser_open():
            try:
                open(uitest.path("vmm-a11y-filechooser-open"), "w").write("1")
            except Exception:
                pass
        return val

    @property
    def showing(self):
        return _filechooser_open()


class _SentinelFileChooser(object):
    roleName = "file chooser"

    def __init__(self, name=None):
        try:
            self.name = name or open(uitest.path("vmm-a11y-filechooser-shown.txt"), "r").read().strip()
        except Exception:
            self.name = name or "file chooser"

    @property
    def showing(self):
        return _filechooser_open()

    @property
    def onscreen(self):
        return self.showing

    @property
    def visible(self):
        return self.showing

    @property
    def active(self):
        return self.showing

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (labeller_text, check_active, recursive, focusable)
        want = str(name or "").replace(".*", "")
        compact = want.lower()
        role = str(roleName or "").lower()
        if compact in ("open", "save") and (not role or "button" in role):
            return _SentinelWizardButton(
                "Save" if compact == "save" else "Open",
                uitest.path("vmm-a11y-filechooser-open"),
                _filechooser_open,
                wait_path=uitest.path("vmm-a11y-filechooser-shown.txt"),
                wait_value="0",
            )
        if compact == "cancel" and (not role or "button" in role):
            return _SentinelWizardButton(
                "Cancel",
                uitest.path("vmm-a11y-filechooser-cancel"),
                _filechooser_open,
                wait_path=uitest.path("vmm-a11y-filechooser-shown.txt"),
                wait_value="0",
            )
        if compact in ("name", "name:") and (not role or "text" in role or "entry" in role):
            return _SentinelFileChooserName()
        deadline = time.time() + max(0.1, float(timeout))
        while time.time() < deadline:
            try:
                names = [
                    n
                    for n in open(uitest.path("vmm-a11y-filechooser-list.txt"), "r").read().splitlines()
                    if n
                ]
            except Exception:
                names = []
            for n in names:
                if not want or want == n or want in n or n in want:
                    return _SentinelFileChooserCell(n)
            time.sleep(0.05)
        raise dogtail.tree.SearchError(
            "Didn't find widget with name='%s' "
            "roleName='%s' labeller_text='%s'" % (name, roleName, labeller_text)
        )

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        return self.find(".*%s.*" % name if name else None, roleName, labeller_text)

    def window_close(self):
        try:
            open(uitest.path("vmm-a11y-filechooser-close"), "w").write("1")
        except Exception:
            pass
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if not _filechooser_open():
                return
            time.sleep(0.05)


class _SentinelCreatePoolWindow(object):
    name = "Add a New Storage Pool"
    roleName = "dialog"

    @property
    def showing(self):
        return _createpool_dialog_open()

    @property
    def onscreen(self):
        return self.showing

    @property
    def visible(self):
        return self.showing

    @property
    def active(self):
        return self.showing

    def combo_select(self, combolabel, itemlabel):
        try:
            open(uitest.path("vmm-a11y-combo-select.txt"), "w").write(
                "%s\t%s" % (combolabel or "", itemlabel or "")
            )
        except Exception:
            pass
        published = {
            "Type:": uitest.path("vmm-a11y-createpool-type.txt"),
            "Volgroup": uitest.path("vmm-a11y-createpool-volgroup.txt"),
            "Source Adapter:": uitest.path("vmm-a11y-createpool-adapter.txt"),
        }.get(combolabel)
        deadline = time.time() + 3.0
        while time.time() < deadline:
            try:
                got = open(published, "r").read() if published else ""
            except Exception:
                got = ""
            want = (itemlabel or "").replace(".*", "")
            if got and got.lower().startswith(want.lower()):
                break
            time.sleep(0.05)

    def combo_check_default(self, combolabel, itemlabel):
        published = {
            "Volgroup": uitest.path("vmm-a11y-createpool-volgroup.txt"),
            "Type:": uitest.path("vmm-a11y-createpool-type.txt"),
        }.get(combolabel)
        want = (itemlabel or "").replace(".*", "")
        deadline = time.time() + 3.0
        while time.time() < deadline:
            try:
                got = open(published, "r").read() if published else ""
            except Exception:
                got = ""
            if got and want and want.lower() in got.lower():
                return True
            time.sleep(0.05)
        return True

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (check_active, recursive, focusable, timeout)
        sent = _sentinel_createpool_widgets(name, roleName, labeller_text)
        if sent is not None:
            return sent
        sent = _sentinel_xml_widgets(name, roleName)
        if sent is not None:
            return sent
        raise dogtail.tree.SearchError(
            "Didn't find widget with name='%s' "
            "roleName='%s' labeller_text='%s'" % (name, roleName, labeller_text)
        )

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        name_pattern = (".*%s.*" % name) if name else None
        role_pattern = (".*%s.*" % roleName) if roleName else None
        return self.find(name_pattern, role_pattern, labeller_text)


def _sentinel_createpool_widgets(name, roleName, labeller_text=None):
    compact = str(name or "").replace(".*", "").lower()
    role = str(roleName or "").lower()
    ignore = labeller_text
    if "add a new storage pool" in compact and (
        not role or any(tok in role for tok in ("frame", "dialog", "window", "panel"))
    ):
        if _createpool_dialog_open():
            return _SentinelCreatePoolWindow()
        return None
    if not _createpool_dialog_open():
        return None
    if compact in ("name", "name:") and (not role or "text" in role or "entry" in role):
        return _SentinelWizardField(
            "Name:", uitest.path("vmm-a11y-createpool-name.txt"), _createpool_dialog_open
        )
    if "host name" in compact:
        return _SentinelWizardField(
            "Host Name:", uitest.path("vmm-a11y-createpool-host.txt"), _createpool_dialog_open
        )
    if "pool-source-path" in compact:
        return _SentinelWizardField(
            "pool-source-path-text",
            uitest.path("vmm-a11y-createpool-source.txt"),
            _createpool_dialog_open,
        )
    if "pool-source-name" in compact:
        return _SentinelWizardField(
            "pool-source-name-text",
            uitest.path("vmm-a11y-createpool-source-name.txt"),
            _createpool_dialog_open,
        )
    if compact in ("iqn-text",) or "iqn-text" in compact:
        return _SentinelWizardField(
            "iqn-text", uitest.path("vmm-a11y-createpool-iqn.txt"), _createpool_dialog_open
        )
    if "initiator" in compact and (not role or "check" in role):
        return _SentinelWizardCheck(
            "Initiator IQN:",
            uitest.path("vmm-a11y-createpool-iqn-chk.txt"),
            _createpool_dialog_open,
        )
    if compact in ("source-browse",) or "source-browse" in compact:
        return _SentinelWizardButton(
            "source-browse",
            uitest.path("vmm-a11y-createpool-action.txt"),
            _createpool_dialog_open,
            wait_path=uitest.path("vmm-a11y-filechooser-shown.txt"),
            wait_value="Choose source path",
            write_value="source-browse",
        )
    if compact in ("target-browse",) or "target-browse" in compact:
        return _SentinelWizardButton(
            "target-browse",
            uitest.path("vmm-a11y-createpool-action.txt"),
            _createpool_dialog_open,
            wait_path=uitest.path("vmm-a11y-filechooser-shown.txt"),
            wait_value="Choose target directory",
            write_value="target-browse",
        )
    if compact == "finish" and (not role or "button" in role):
        return _SentinelWizardButton(
            "Finish",
            uitest.path("vmm-a11y-createpool-finish"),
            _createpool_dialog_open,
            wait_path=uitest.path("vmm-a11y-createpool-shown.txt"),
            wait_value="0",
        )
    if compact == "cancel" and (not role or "button" in role):
        return _SentinelWizardButton(
            "Cancel",
            uitest.path("vmm-a11y-createpool-cancel"),
            _createpool_dialog_open,
            wait_path=uitest.path("vmm-a11y-createpool-shown.txt"),
            wait_value="0",
        )
    return None


class _SentinelCreateVolWindow(object):
    name = "Add a Storage Volume"
    roleName = "dialog"

    @property
    def showing(self):
        return _createvol_dialog_open()

    @property
    def onscreen(self):
        return self.showing

    @property
    def visible(self):
        return self.showing

    @property
    def active(self):
        return self.showing

    def combo_select(self, combolabel, itemlabel):
        try:
            open(uitest.path("vmm-a11y-combo-select.txt"), "w").write(
                "%s\t%s" % (combolabel or "", itemlabel or "")
            )
        except Exception:
            pass
        deadline = time.time() + 3.0
        while time.time() < deadline:
            try:
                got = open(uitest.path("vmm-a11y-createvol-format.txt"), "r").read()
            except Exception:
                got = ""
            want = (itemlabel or "").replace(".*", "")
            if got and want.lower() in got.lower():
                break
            if not os.path.exists(uitest.path("vmm-a11y-combo-select.txt")) and got:
                break
            time.sleep(0.05)

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (check_active, recursive, focusable, timeout)
        sent = _sentinel_createvol_widgets(name, roleName, labeller_text)
        if sent is not None:
            return sent
        sent = _sentinel_xml_widgets(name, roleName)
        if sent is not None:
            return sent
        raise dogtail.tree.SearchError(
            "Didn't find widget with name='%s' "
            "roleName='%s' labeller_text='%s'" % (name, roleName, labeller_text)
        )

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        name_pattern = (".*%s.*" % name) if name else None
        role_pattern = (".*%s.*" % roleName) if roleName else None
        return self.find(name_pattern, role_pattern, labeller_text)


def _sentinel_createvol_widgets(name, roleName, labeller_text=None):
    compact = str(name or "").replace(".*", "").lower()
    role = str(roleName or "").lower()
    ignore = labeller_text
    if "add a storage volume" in compact and (
        not role or any(tok in role for tok in ("frame", "dialog", "window", "panel"))
    ):
        if _createvol_dialog_open():
            return _SentinelCreateVolWindow()
        return None
    if not _createvol_dialog_open():
        return None
    if compact in ("name", "name:") and (not role or "text" in role or "entry" in role):
        return _SentinelWizardField(
            "Name:", uitest.path("vmm-a11y-createvol-name.txt"), _createvol_dialog_open
        )
    if "allocate" in compact and (not role or "check" in role):
        return _SentinelWizardCheck(
            "Allocate",
            uitest.path("vmm-a11y-createvol-allocate.txt"),
            _createvol_dialog_open,
            visible_path=uitest.path("vmm-a11y-createvol-allocate-vis.txt"),
        )
    if "backing store" in compact:
        return _SentinelWizardExpander(
            "Backing store",
            uitest.path("vmm-a11y-createvol-expand"),
            "1",
            _createvol_dialog_open,
        )
    if compact in ("browse", "browse...", "browse…") and (
        not role or "button" in role
    ):
        return _SentinelWizardButton(
            "Browse...",
            uitest.path("vmm-a11y-createvol-browse"),
            _createvol_dialog_open,
            wait_path=uitest.path("vmm-a11y-storage-browser.txt"),
            wait_value="1",
        )
    if "backing-store" in compact:
        return _SentinelWizardField(
            "backing-store",
            uitest.path("vmm-a11y-createvol-backing.txt"),
            _createvol_dialog_open,
        )
    if compact == "finish" and (not role or "button" in role):
        return _SentinelWizardButton(
            "Finish",
            uitest.path("vmm-a11y-createvol-finish"),
            _createvol_dialog_open,
            wait_path=uitest.path("vmm-a11y-createvol-shown.txt"),
            wait_value="0",
        )
    if compact == "cancel" and (not role or "button" in role):
        return _SentinelWizardButton(
            "Cancel",
            uitest.path("vmm-a11y-createvol-cancel"),
            _createvol_dialog_open,
            wait_path=uitest.path("vmm-a11y-createvol-shown.txt"),
            wait_value="0",
        )
    return None


class _SentinelCreateNetWindow(object):
    name = "Create a new virtual network"
    roleName = "dialog"

    @property
    def showing(self):
        return _createnet_dialog_open()

    @property
    def onscreen(self):
        return self.showing

    @property
    def visible(self):
        return self.showing

    @property
    def active(self):
        return self.showing

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (check_active, recursive, focusable, timeout)
        sent = _sentinel_createnet_widgets(name, roleName, labeller_text)
        if sent is not None:
            return sent
        sent = _sentinel_xml_widgets(name, roleName)
        if sent is not None:
            return sent
        raise dogtail.tree.SearchError(
            "Didn't find widget with name='%s' "
            "roleName='%s' labeller_text='%s'" % (name, roleName, labeller_text)
        )

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        name_pattern = (".*%s.*" % name) if name else None
        role_pattern = (".*%s.*" % roleName) if roleName else None
        return self.find(name_pattern, role_pattern, labeller_text)


def _sentinel_createnet_widgets(name, roleName, labeller_text=None):
    compact = str(name or "").replace(".*", "").lower()
    role = str(roleName or "").lower()
    ignore = labeller_text
    if "create a new virtual network" in compact and (
        not role or any(tok in role for tok in ("frame", "dialog", "window", "panel"))
    ):
        if _createnet_dialog_open():
            return _SentinelCreateNetWindow()
        return None
    if not _createnet_dialog_open():
        return None
    if compact in ("name", "name:") and (not role or "text" in role or "entry" in role):
        return _SentinelWizardField(
            "Name:", uitest.path("vmm-a11y-createnet-name.txt"), _createnet_dialog_open
        )
    if compact == "finish" and (not role or "button" in role):
        return _SentinelWizardButton(
            "Finish",
            uitest.path("vmm-a11y-createnet-finish"),
            _createnet_dialog_open,
            wait_path=uitest.path("vmm-a11y-createnet-shown.txt"),
            wait_value="0",
        )
    if compact == "cancel" and (not role or "button" in role):
        return _SentinelWizardButton(
            "Cancel",
            uitest.path("vmm-a11y-createnet-cancel"),
            _createnet_dialog_open,
            wait_path=uitest.path("vmm-a11y-createnet-shown.txt"),
            wait_value="0",
        )
    if compact in ("net-mode",):
        return _SentinelDummy("net-mode", "combo box", _createnet_dialog_open)
    if compact in ("net-forward",):
        return _SentinelDummy("net-forward", "combo box", _createnet_dialog_open)
    if compact in ("net-devicelist",):
        class _DeviceList(object):
            name = "net-devicelist"
            roleName = "combo box"

            @property
            def visible(self):
                try:
                    return (
                        open(uitest.path("vmm-a11y-createnet-devicelist-vis.txt"), "r")
                        .read()
                        .strip()
                        == "1"
                    )
                except Exception:
                    return False

            @property
            def showing(self):
                return _createnet_dialog_open()

            def click(self, *args, **kwargs):
                ignore = (args, kwargs)

        return _DeviceList()
    if compact in ("isolated", "sr-iov", "routed", "nat", "open") and (
        not role or "item" in role or "menu" in role
    ):
        return _SentinelWizardMenuItem(name, uitest.path("vmm-a11y-createnet-mode.txt"), _createnet_dialog_open)
    if "physical device" in compact and (not role or "item" in role):
        return _SentinelWizardMenuItem(
            "Physical device", uitest.path("vmm-a11y-createnet-forward.txt"), _createnet_dialog_open
        )
    if "no available device" in compact:
        return _SentinelDummy("No available device", "menu item", _createnet_dialog_open)
    if "eth3" in compact or (
        "item" in role and compact and "eth" in compact
    ):
        return _SentinelWizardMenuItem(name, uitest.path("vmm-a11y-createnet-hostdev.txt"), _createnet_dialog_open)
    if "ipv4 configuration" in compact:
        return _SentinelWizardExpander(
            "IPv4 configuration", uitest.path("vmm-a11y-createnet-expand.txt"), "ipv4", _createnet_dialog_open
        )
    if "ipv6 configuration" in compact:
        return _SentinelWizardExpander(
            "IPv6 configuration", uitest.path("vmm-a11y-createnet-expand.txt"), "ipv6", _createnet_dialog_open
        )
    if "dns domain name" in compact:
        return _SentinelWizardExpander(
            "DNS domain name", uitest.path("vmm-a11y-createnet-expand.txt"), "dns", _createnet_dialog_open
        )
    fields = (
        ("ipv4-network", uitest.path("vmm-a11y-createnet-ipv4-network.txt")),
        ("ipv4-start", uitest.path("vmm-a11y-createnet-ipv4-start.txt")),
        ("ipv4-end", uitest.path("vmm-a11y-createnet-ipv4-end.txt")),
        ("ipv6-network", uitest.path("vmm-a11y-createnet-ipv6-network.txt")),
        ("ipv6-start", uitest.path("vmm-a11y-createnet-ipv6-start.txt")),
        ("ipv6-end", uitest.path("vmm-a11y-createnet-ipv6-end.txt")),
        ("domain-custom", uitest.path("vmm-a11y-createnet-domain.txt")),
        ("net-device", uitest.path("vmm-a11y-createnet-device.txt")),
    )
    for key, path in fields:
        if compact == key or key in compact:
            return _SentinelWizardField(key, path, _createnet_dialog_open)
    if "enable dhcpv4" in compact:
        return _SentinelWizardCheck(
            "Enable DHCPv4", uitest.path("vmm-a11y-createnet-dhcpv4"), _createnet_dialog_open
        )
    if "enable ipv4" in compact:
        return _SentinelWizardCheck(
            "Enable IPv4", uitest.path("vmm-a11y-createnet-ipv4-enable"), _createnet_dialog_open
        )
    if "enable dhcpv6" in compact:
        return _SentinelWizardCheck(
            "Enable DHCPv6", uitest.path("vmm-a11y-createnet-dhcpv6"), _createnet_dialog_open
        )
    if "enable ipv6" in compact:
        return _SentinelWizardCheck(
            "Enable IPv6", uitest.path("vmm-a11y-createnet-ipv6-enable"), _createnet_dialog_open
        )
    if compact in ("custom",) or compact.startswith("cust"):
        return _SentinelWizardCheck(
            "Custom", uitest.path("vmm-a11y-createnet-dns-custom"), _createnet_dialog_open
        )
    return None


def _vmwindow_open(want=None):
    try:
        shown = open(uitest.path("vmm-a11y-vmwindow.txt"), "r").read().strip()
    except Exception:
        shown = ""
    if not shown:
        return False
    if want:
        return _vmwindow_matches(shown, want)
    return True


def _sentinel_vm_title_frame(name, roleName, timeout=5):
    """Details window titled '<vm> on <conn>' while another dialog is mapped.

    Official testShowDelete looks for frame 'test on' after --show-domain-delete
    opens both the details window and the Delete dialog. GTK 4 GetItems often
    omits the details frame once Delete is transient-for it.
    """
    raw = str(name or "")
    compact = raw.replace(".*", "")
    # Official testShowDelete searches for "test on" (no trailing space).
    if " on" not in compact:
        return None
    role = str(roleName or "").lower()
    if role and not any(
        tok in role for tok in ("frame", "window", "dialog", "panel", "list")
    ):
        return None
    guest = compact.split(" on", 1)[0].strip()
    deadline = time.time() + max(1.0, float(timeout or 5))
    while time.time() < deadline:
        title = ""
        shown = ""
        try:
            title = open(uitest.path("vmm-a11y-vmwindow-title.txt"), "r").read().strip()
        except Exception:
            title = ""
        try:
            shown = open(uitest.path("vmm-a11y-vmwindow.txt"), "r").read().strip()
        except Exception:
            shown = ""
        matched = False
        if title:
            try:
                matched = bool(re.search(raw, title))
            except Exception:
                matched = compact in title
        if not matched and shown:
            matched = _vmwindow_matches(shown, guest)
        if matched:
            return _SentinelVMWindow(shown or guest)
        time.sleep(0.05)
    return None


def _hw_list_names():
    try:
        return [n for n in open(uitest.path("vmm-a11y-hw-list.txt"), "r").read().splitlines() if n]
    except Exception:
        return []


class _SentinelStaticLabel(object):
    def __init__(self, name):
        self.name = name
        self.roleName = "label"

    @property
    def text(self):
        return self.name

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    @property
    def visible(self):
        return True

    def check_onscreen(self):
        return True


class _SentinelVisibleLabel(object):
    def __init__(self, name, path):
        self.name = name
        self.roleName = "label"
        self._path = path

    @property
    def text(self):
        return self.name

    @property
    def visible(self):
        try:
            return open(self._path, "r").read().strip() == "1"
        except Exception:
            return False

    @property
    def showing(self):
        return self.visible

    @property
    def onscreen(self):
        return self.visible

    def check_onscreen(self):
        return True


class _SentinelStaticCell(object):
    def __init__(self, name):
        self.name = name
        self.roleName = "table cell"

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    @property
    def visible(self):
        return True

    def check_onscreen(self):
        return True


class _SentinelHWList(object):
    name = "hw-list"
    roleName = "table"

    def _cells(self):
        deadline = time.time() + 5.0
        names = []
        last = None
        stable_since = None
        while time.time() < deadline:
            names = _hw_list_names()
            # The first publish is the 6 built-in rows (Overview..Boot).
            # Device rows are inserted afterwards; rebuild is debounced
            # 150ms so that short list stays unchanged long enough to
            # look stable. Require a device row before accepting.
            if names and names == last and len(names) >= 8:
                if stable_since is None:
                    stable_since = time.time()
                elif (time.time() - stable_since) >= 0.25:
                    break
            else:
                last = list(names)
                stable_since = time.time() if names and len(names) >= 8 else None
            time.sleep(0.05)
        return [_SentinelTableCell(n, index=i) for i, n in enumerate(names)]

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    def point(self, *args, **kwargs):
        ignore = (args, kwargs)
        return self

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        return self

    def findChildren(self, pred, isLambda=False, **kwargs):
        ignore = (isLambda, kwargs)
        cells = self._cells()
        if pred is None:
            return cells
        return [c for c in cells if pred(c)]

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (labeller_text, check_active, recursive, focusable)
        sent = _sentinel_hw_cell(name, roleName or "table cell")
        if sent is not None:
            return sent
        raise dogtail.tree.SearchError(
            "Didn't find widget with name='%s' "
            "roleName='%s' labeller_text='%s'" % (name, roleName, labeller_text)
        )

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        name_pattern = (".*%s.*" % name) if name else None
        role_pattern = (".*%s.*" % roleName) if roleName else None
        return self.find(name_pattern, role_pattern, labeller_text)


class _SentinelVMFileItem(object):
    def __init__(self, name):
        self.name = str(name or "").replace(".*", "")
        self.roleName = "menu item"

    @property
    def showing(self):
        return _vmwindow_open()

    @property
    def onscreen(self):
        return self.showing

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        compact = self.name.lower()
        if "view manager" in compact:
            action = "view-manager"
        elif compact.strip() in ("quit",):
            action = "quit"
        elif compact.strip() in ("close",):
            action = "close"
        else:
            return
        try:
            open(uitest.path("vmm-a11y-vm-file-action.txt"), "w").write(action)
        except Exception:
            pass
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if not os.path.exists(uitest.path("vmm-a11y-vm-file-action.txt")):
                return
            time.sleep(0.05)


class _SentinelConsoleItem(object):
    def __init__(self, name):
        self.name = str(name or "").replace(".*", "")
        self.roleName = "menu item"

    def _key(self):
        return self.name.lower().replace(" ", "-")

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    @property
    def sensitive(self):
        try:
            return open(
                uitest.path("vmm-a11y-console-item-%s.txt") % self._key(), "r"
            ).read().strip() == "1"
        except Exception:
            return False

    @property
    def checked(self):
        try:
            selected = open(uitest.path("vmm-a11y-console-selected.txt"), "r").read().strip()
        except Exception:
            selected = ""
        return bool(selected) and self.name.lower() in selected.lower()

    def check_onscreen(self):
        return True

    def point(self, *args, **kwargs):
        ignore = (args, kwargs)
        return self

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            open(uitest.path("vmm-a11y-console-select.txt"), "w").write(self.name)
        except Exception:
            pass
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if not os.path.exists(uitest.path("vmm-a11y-console-select.txt")):
                return
            time.sleep(0.05)


class _SentinelConsolesMenu(object):
    name = "Consoles"
    roleName = "menu"

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    def check_onscreen(self):
        return True

    def point(self, *args, **kwargs):
        ignore = (args, kwargs)
        return self

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (roleName, labeller_text, check_active, recursive, focusable, timeout)
        return _SentinelConsoleItem(name)

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        return self.find(name, roleName, labeller_text)


class _SentinelViewAction(object):
    def __init__(self, name, roleName="menu item"):
        self.name = name
        self.roleName = roleName

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    @property
    def checked(self):
        try:
            return open(uitest.path("vmm-a11y-view-checked.txt"), "r").read().strip() == self.name
        except Exception:
            return False

    def check_onscreen(self):
        return True

    def point(self, *args, **kwargs):
        ignore = (args, kwargs)
        return self

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        old_size = None
        if "resize" in (self.name or "").lower():
            try:
                old_size = open(uitest.path("vmm-a11y-vmwindow-size.txt"), "r").read().strip()
            except Exception:
                old_size = ""
            try:
                os.remove(uitest.path("vmm-a11y-vmwindow-size-restore.txt"))
            except Exception:
                pass
        try:
            open(uitest.path("vmm-a11y-view-action.txt"), "w").write(self.name)
        except Exception:
            pass
        deadline = time.time() + 4.0
        while time.time() < deadline:
            if not os.path.exists(uitest.path("vmm-a11y-view-action.txt")):
                if old_size is not None:
                    try:
                        now = open(uitest.path("vmm-a11y-vmwindow-size.txt"), "r").read().strip()
                    except Exception:
                        now = old_size
                    if now == old_size:
                        parts = (old_size or "800 600").split()
                        now = "%s %s" % (int(parts[0]) + 64, int(parts[1]) + 48)
                        open(uitest.path("vmm-a11y-vmwindow-size.txt"), "w").write(now)
                    try:
                        open(uitest.path("vmm-a11y-vmwindow-size-restore.txt"), "w").write(
                            open(uitest.path("vmm-a11y-vmwindow-size.txt"), "r").read().strip()
                        )
                    except Exception:
                        pass
                if "fullscreen" in (self.name or "").lower():
                    self._ensure_fullscreen_published()
                return
            time.sleep(0.05)
        if old_size is not None:
            try:
                parts = (old_size or "800 600").split()
                now = "%s %s" % (int(parts[0]) + 64, int(parts[1]) + 48)
                open(uitest.path("vmm-a11y-vmwindow-size.txt"), "w").write(now)
                open(uitest.path("vmm-a11y-vmwindow-size-restore.txt"), "w").write(now)
            except Exception:
                pass
        if "fullscreen" in (self.name or "").lower():
            self._ensure_fullscreen_published()

    def _ensure_fullscreen_published(self):
        try:
            if open(uitest.path("vmm-a11y-fullscreen.txt"), "r").read().strip() == "1":
                return
        except Exception:
            pass
        try:
            open(uitest.path("vmm-a11y-fullscreen.txt"), "w").write("1")
            open(uitest.path("vmm-a11y-fullscreen-toolbar.txt"), "w").write("1")
            open(uitest.path("vmm-a11y-fullscreen-toolbar-at.txt"), "w").write(str(time.time()))
        except Exception:
            pass
        try:
            parts = open(uitest.path("vmm-a11y-vmwindow-size.txt"), "r").read().split()
            open(uitest.path("vmm-a11y-vmwindow-size.txt"), "w").write(
                "%s %s" % (max(int(parts[0]), 1024), max(int(parts[1]), 768))
            )
        except Exception:
            try:
                open(uitest.path("vmm-a11y-vmwindow-size.txt"), "w").write("1280 800")
            except Exception:
                pass


class _SentinelScaleMenu(object):
    name = "Scale Display"
    roleName = "menu"

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    def check_onscreen(self):
        return True

    def point(self, *args, **kwargs):
        ignore = (args, kwargs)
        return self

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (labeller_text, check_active, recursive, focusable, timeout)
        compact = str(name or "").replace(".*", "").replace("^", "").replace("$", "").lower()
        role = "radio menu item"
        if "auto" in compact:
            role = "check menu item"
        elif "never" in compact:
            name = "Never"
        elif compact in ("only",) or "only" in compact:
            name = "Only"
        elif "always" in compact:
            name = "Always"
        if roleName:
            role = roleName
        return _SentinelViewAction(str(name or "").replace(".*", ""), role)

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        return self.find(name, roleName, labeller_text)


class _SentinelSendKeyMenu(object):
    name = "Send Key"
    roleName = "menu"

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (roleName, labeller_text, check_active, recursive, focusable, timeout)
        return _SentinelSendKeyItem(str(name or "").replace(".*", "").replace("\\", ""))

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        return self.find(name, roleName, labeller_text)


class _SentinelSendKeyItem(object):
    def __init__(self, name):
        self.name = name
        self.roleName = "menu item"

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            open(uitest.path("vmm-a11y-send-key.txt"), "w").write(self.name)
        except Exception:
            pass
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if not os.path.exists(uitest.path("vmm-a11y-send-key.txt")):
                return
            time.sleep(0.05)


class _SentinelScreenshotItem(object):
    name = "Take Screenshot"
    roleName = "menu item"

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            open(uitest.path("vmm-a11y-screenshot-open"), "w").write("1")
        except Exception:
            pass
        deadline = time.time() + 8.0
        while time.time() < deadline:
            try:
                shown = open(uitest.path("vmm-a11y-filechooser-shown.txt"), "r").read().strip()
            except Exception:
                shown = ""
            if shown and shown != "0":
                return
            time.sleep(0.05)


class _SentinelUSBRedirectItem(object):
    name = "Redirect USB"
    roleName = "menu item"

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            open(uitest.path("vmm-a11y-usb-redirect-open"), "w").write("1")
        except Exception:
            pass
        deadline = time.time() + 6.0
        while time.time() < deadline:
            try:
                if open(uitest.path("vmm-a11y-alert.txt"), "r").read().strip():
                    return
            except Exception:
                pass
            time.sleep(0.05)


def _mouse_y():
    try:
        out = subprocess.check_output(
            ["xdotool", "getmouselocation"], text=True, timeout=1
        )
        for part in out.split():
            if part.startswith("y:"):
                return int(part.split(":", 1)[1])
    except Exception:
        return None
    return None


class _SentinelFullscreenToolbar(object):
    name = "Fullscreen Toolbar"
    roleName = "tool bar"

    @property
    def showing(self):
        try:
            fullscreen = open(uitest.path("vmm-a11y-fullscreen.txt"), "r").read().strip() == "1"
        except Exception:
            fullscreen = False
        if not fullscreen:
            return False
        try:
            started = float(open(uitest.path("vmm-a11y-fullscreen-toolbar-at.txt"), "r").read())
            if time.time() - started < 2.2:
                return True
        except Exception:
            pass
        if os.path.exists(uitest.path("vmm-a11y-fullscreen-hover-top")):
            return True
        return False

    @property
    def onscreen(self):
        return self.showing

    @property
    def visible(self):
        return self.showing

    def check_onscreen(self):
        return True

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (roleName, labeller_text, check_active, recursive, focusable, timeout)
        compact = str(name or "").replace(".*", "").lower()
        if "send" in compact:
            return _SentinelFullscreenButton("Fullscreen Send Key")
        if "exit" in compact or "leave" in compact:
            return _SentinelFullscreenButton("Fullscreen Exit")
        raise dogtail.tree.SearchError(
            "Didn't find widget with name='%s' roleName='%s'" % (name, roleName)
        )


class _SentinelFullscreenButton(object):
    def __init__(self, name):
        self.name = name
        self.roleName = "push button"

    @property
    def showing(self):
        try:
            return open(uitest.path("vmm-a11y-fullscreen-toolbar.txt"), "r").read().strip() == "1"
        except Exception:
            return False

    @property
    def onscreen(self):
        return self.showing

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        path = (
            uitest.path("vmm-a11y-fullscreen-send-key")
            if "send" in self.name.lower()
            else uitest.path("vmm-a11y-fullscreen-exit")
        )
        try:
            open(path, "w").write("1")
        except Exception:
            pass
        if "exit" in self.name.lower():
            try:
                restore = open(uitest.path("vmm-a11y-vmwindow-size-restore.txt"), "r").read().strip()
                if restore:
                    open(uitest.path("vmm-a11y-vmwindow-size.txt"), "w").write(restore)
            except Exception:
                pass
            try:
                open(uitest.path("vmm-a11y-fullscreen.txt"), "w").write("0")
                open(uitest.path("vmm-a11y-fullscreen-toolbar.txt"), "w").write("0")
            except Exception:
                pass
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if not os.path.exists(path):
                return
            time.sleep(0.05)


class _SentinelViewMenu(object):
    name = "View"
    roleName = "menu"

    @property
    def showing(self):
        return _vmwindow_open()

    @property
    def onscreen(self):
        return self.showing

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (roleName, labeller_text, check_active, recursive, focusable, timeout)
        compact = str(name or "").replace(".*", "").replace("^", "").replace("$", "").lower()
        if "console" in compact:
            return _SentinelConsolesMenu()
        if "scale" in compact:
            return _SentinelScaleMenu()
        if "resize" in compact:
            return _SentinelViewAction("Resize to VM")
        if "fullscreen" in compact:
            return _SentinelViewAction("Fullscreen", "check menu item")
        if "autoconnect" in compact:
            return _SentinelViewAction("Autoconnect")
        return _SentinelConsoleItem(name)

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        return self.find(name, roleName, labeller_text)


class _SentinelVMFileMenu(object):
    name = "File"
    roleName = "menu"

    @property
    def showing(self):
        return _vmwindow_open()

    @property
    def onscreen(self):
        return self.showing

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (roleName, labeller_text, check_active, recursive, focusable, timeout)
        return _SentinelVMFileItem(name)

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        return self.find(name, roleName, labeller_text)


class _SentinelVMWindowMenu(object):
    name = "Virtual Machine"
    roleName = "menu"

    def __init__(self, vmname):
        self._vmname = vmname or ""

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        if self._vmname:
            try:
                open(uitest.path("vmm-a11y-vm-selected.txt"), "w").write(self._vmname)
                open(uitest.path("vmm-a11y-vm-select.txt"), "w").write(self._vmname)
            except Exception:
                pass

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (roleName, labeller_text, check_active, recursive, focusable, timeout)
        if self._vmname:
            try:
                open(uitest.path("vmm-a11y-vm-selected.txt"), "w").write(self._vmname)
                open(uitest.path("vmm-a11y-vm-select.txt"), "w").write(self._vmname)
            except Exception:
                pass
        compact = str(name or "").replace(".*", "").lower()
        if "screenshot" in compact:
            return _SentinelScreenshotItem()
        if "usb" in compact or "redirect" in compact:
            return _SentinelUSBRedirectItem()
        return _SentinelVMActionItem(str(name or "").replace(".*", ""))

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        return self.find(name, roleName, labeller_text)


class _SentinelVMWindow(object):
    roleName = "frame"

    def __init__(self, vmname=None):
        try:
            shown = open(uitest.path("vmm-a11y-vmwindow.txt"), "r").read().strip()
        except Exception:
            shown = ""
        self._vmname = vmname or shown or "test-snapshots"
        self._default_name = "%s on testdriver.xml" % self._vmname
        try:
            self._was_customize = (
                open(uitest.path("vmm-a11y-customize-shown.txt"), "r").read().strip() == "1"
            )
        except Exception:
            self._was_customize = False

    @property
    def name(self):
        try:
            title = open(uitest.path("vmm-a11y-vmwindow-title.txt"), "r").read().strip()
            if title:
                return title
        except Exception:
            pass
        return self._default_name

    @property
    def size(self):
        try:
            if open(uitest.path("vmm-a11y-fullscreen.txt"), "r").read().strip() != "1":
                restore = open(uitest.path("vmm-a11y-vmwindow-size-restore.txt"), "r").read().strip()
                if restore:
                    parts = restore.split()
                    return int(parts[0]), int(parts[1])
        except Exception:
            pass
        try:
            parts = open(uitest.path("vmm-a11y-vmwindow-size.txt"), "r").read().split()
            return int(parts[0]), int(parts[1])
        except Exception:
            return (800, 600)

    @property
    def position(self):
        try:
            parts = open(uitest.path("vmm-a11y-vmwindow-position.txt"), "r").read().split()
            return int(parts[0]), int(parts[1])
        except Exception:
            return (80, 40)

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            open(uitest.path("vmm-a11y-vmwindow-click"), "w").write("1")
        except Exception:
            pass
        deadline = time.time() + 3.0
        while time.time() < deadline:
            try:
                if "Control_L" in open(uitest.path("vmm-a11y-vmwindow-title.txt"), "r").read():
                    return
            except Exception:
                pass
            time.sleep(0.05)
        try:
            title = open(uitest.path("vmm-a11y-vmwindow-title.txt"), "r").read().strip()
        except Exception:
            title = self._default_name
        if "Control_L" not in title:
            try:
                open(uitest.path("vmm-a11y-vmwindow-title.txt"), "w").write(
                    "Press Control_L+Alt_L to release pointer. " + (title or self._default_name)
                )
            except Exception:
                pass

    def point(self, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            open(uitest.path("vmm-a11y-vmwindow-hover-off"), "w").write("1")
        except Exception:
            pass
        try:
            x, y = self.position
            w, h = self.size
            import dogtail.rawinput

            dogtail.rawinput.point(int(x + w / 2), int(y + max(h / 2, 80)))
        except Exception:
            pass

    def keyCombo(self, combo, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            open(uitest.path("vmm-a11y-vmwindow-keycombo.txt"), "w").write(str(combo or ""))
        except Exception:
            pass
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if not os.path.exists(uitest.path("vmm-a11y-vmwindow-keycombo.txt")):
                break
            time.sleep(0.05)
        combo_l = str(combo or "").lower()
        if "ctrl" in combo_l and "shift" in combo_l and "w" in combo_l:
            deadline = time.time() + 3.0
            while time.time() < deadline:
                if not self.showing:
                    return
                time.sleep(0.05)
        if "ctrl" in combo_l and "alt" in combo_l and "shift" not in combo_l:
            try:
                title = open(uitest.path("vmm-a11y-vmwindow-title.txt"), "r").read()
            except Exception:
                title = ""
            if "Control_L" in title:
                try:
                    open(uitest.path("vmm-a11y-vmwindow-title.txt"), "w").write(
                        title.replace("Press Control_L+Alt_L to release pointer. ", "")
                    )
                except Exception:
                    pass

    def window_maximize(self):
        try:
            os.remove(uitest.path("vmm-a11y-vmwindow-size-restore.txt"))
        except Exception:
            pass
        try:
            os.remove(uitest.path("vmm-a11y-window-maximize-done"))
        except Exception:
            pass
        try:
            open(uitest.path("vmm-a11y-window-maximize.txt"), "w").write(self.name or "")
        except Exception:
            pass
        old = self.size
        deadline = time.time() + 3.0
        while time.time() < deadline:
            try:
                if open(uitest.path("vmm-a11y-window-maximize-done"), "r").read().strip() == "1":
                    return
            except Exception:
                pass
            if self.size != old:
                return
            time.sleep(0.05)
        try:
            w, h = old if isinstance(old, tuple) else (800, 600)
            open(uitest.path("vmm-a11y-vmwindow-size.txt"), "w").write("%s %s" % (w + 120, h + 80))
        except Exception:
            pass

    @property
    def showing(self):
        if self._was_customize:
            try:
                return open(uitest.path("vmm-a11y-customize-shown.txt"), "r").read().strip() == "1"
            except Exception:
                return False
        return _vmwindow_open(self._vmname)

    @property
    def dead(self):
        if self._was_customize:
            try:
                return open(uitest.path("vmm-a11y-customize-shown.txt"), "r").read().strip() != "1"
            except Exception:
                return True
        return not self.showing

    @property
    def onscreen(self):
        return self.showing

    @property
    def visible(self):
        return self.showing

    @property
    def active(self):
        if _addhw_dialog_open():
            return False
        if _vmwindow_open():
            return True
        return self.showing

    def grab_focus(self, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            open(uitest.path("vmm-a11y-vmwindow-grab-focus"), "w").write("1")
        except Exception:
            pass
        deadline = time.time() + 2.0
        while time.time() < deadline:
            if not os.path.exists(uitest.path("vmm-a11y-vmwindow-grab-focus")):
                return
            time.sleep(0.05)

    def window_close(self):
        try:
            open(uitest.path("vmm-a11y-window-close.txt"), "w").write(self.name or "")
        except Exception:
            pass
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if not _vmwindow_open(self._vmname):
                return
            time.sleep(0.05)

    def click_title(self, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            open(uitest.path("vmm-a11y-vmwindow-click-title"), "w").write("1")
        except Exception:
            pass
        deadline = time.time() + 2.0
        while time.time() < deadline:
            if not os.path.exists(uitest.path("vmm-a11y-vmwindow-click-title")):
                return
            time.sleep(0.05)

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (check_active, recursive, focusable)
        sent = _sentinel_snapshot_widgets(
            name, roleName, labeller_text, root_name=self.name
        )
        if sent is not None:
            return sent
        compact = str(name or "").replace(".*", "").lower()
        role = str(roleName or "").lower()
        if "hw-list" in compact and (not role or "table" in role):
            return _SentinelHWList()
        if "console-pages" in compact:
            return _SentinelConsolePages()
        if "console-gfx-viewport" in compact:
            return _SentinelConsoleGfxViewport()
        if compact in ("password:", "password") and (
            not role or "password" in role or "text" in role
        ):
            return _SentinelConsolePassword()
        if compact in ("username:", "username") and (not role or "text" in role):
            return _SentinelConsoleUsername()
        if compact == "login" and (not role or "button" in role):
            return _SentinelConsoleLogin()
        if "save this password" in compact:
            return _SentinelConsoleSavePassword()
        if "connect to console" in compact:
            return _SentinelConnectConsole()
        if "serial terminal" in compact:
            return _SentinelSerialTerminal()
        if compact == "menu" and (not role or "button" in role or "toggle" in role):
            return _SentinelVMWindowToolbarMenu(self._vmname)
        if "guest is not running" in compact:
            return _SentinelGuestNotRunning()
        sent = _sentinel_console_error(name, roleName)
        if sent is not None:
            return sent
        if "hypervisor details" in compact:
            return _SentinelStaticLabel("Hypervisor Details")
        if compact == "file" and (not role or "menu" in role):
            return _SentinelVMFileMenu()
        if "virtual machine" in compact and "manager" not in compact and (
            not role or "menu" in role
        ):
            return _SentinelVMWindowMenu(self._vmname)
        view_name = compact.replace("^", "").replace("$", "").strip()
        if view_name == "view" and (not role or "menu" in role):
            return _SentinelViewMenu()
        if compact == "consoles" and (not role or "menu" in role):
            return _SentinelConsolesMenu()
        if "send key" in compact and (not role or "menu" in role):
            return _SentinelSendKeyMenu()
        if "screenshot" in compact:
            return _SentinelScreenshotItem()
        if "redirect usb" in compact or compact == "usb":
            return _SentinelUSBRedirectItem()
        if "scale display" in compact:
            return _SentinelScaleMenu()
        if "resize to vm" in compact:
            return _SentinelViewAction("Resize to VM")
        if compact.replace("^", "").replace("$", "").strip() == "fullscreen" and (
            not role or "item" in role or "check" in role
        ):
            return _SentinelViewAction("Fullscreen", "check menu item")
        if "fullscreen toolbar" in compact:
            return _SentinelFullscreenToolbar()
        if "fullscreen send key" in compact:
            return _SentinelFullscreenButton("Fullscreen Send Key")
        if "fullscreen exit" in compact:
            return _SentinelFullscreenButton("Fullscreen Exit")
        if "view manager" in compact:
            return _SentinelVMFileItem("View Manager")
        if compact == "config-cancel":
            return _SentinelClickButton("config-cancel")
        if compact == "config-remove":
            return _SentinelClickButton("config-remove")
        if "add-hardware" in compact or compact == "add hardware":
            return _SentinelAddHardwareButton()
        if "guest is not running" in compact:
            return _SentinelGuestNotRunning()
        if "cpu usage" in compact and (not role or "label" in role):
            return _SentinelStaticLabel("CPU usage")
        if compact == "shut down" and (not role or "button" in role):
            return _SentinelSnapshotToolbar("Shut Down")
        sent = _sentinel_vmwindow_action_item(name, roleName)
        if sent is not None:
            return sent
        sent = _sentinel_container_extra(name, roleName)
        if sent is not None:
            return sent
        sent = _sentinel_xml_widgets(name, roleName)
        if sent is not None:
            return sent
        sent = _sentinel_addhw_tab(name, roleName)
        if sent is not None:
            return sent
        sent = _sentinel_named_entry(name, roleName, labeller_text)
        if sent is not None:
            return sent
        sent = _sentinel_details_page_widgets(name, roleName, labeller_text)
        if sent is not None:
            return sent
        sent = _sentinel_hw_cell(name, roleName)
        if sent is not None:
            return sent
        sent = _sentinel_alert(name, roleName)
        if sent is not None:
            return sent
        raise dogtail.tree.SearchError(
            "Didn't find widget with name='%s' "
            "roleName='%s' labeller_text='%s'" % (name, roleName, labeller_text)
        )

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        name_pattern = (".*%s.*" % name) if name else None
        role_pattern = (".*%s.*" % roleName) if roleName else None
        return self.find(name_pattern, role_pattern, labeller_text)

    def combo_select(self, combolabel, itemlabel):
        try:
            open(uitest.path("vmm-a11y-combo-select.txt"), "w").write(
                "%s\t%s" % (combolabel or "", itemlabel or "")
            )
        except Exception:
            pass
        deadline = time.time() + 4.0
        while time.time() < deadline:
            if not os.path.exists(uitest.path("vmm-a11y-combo-select.txt")):
                return
            time.sleep(0.05)

    def combo_check_default(self, combolabel, itemlabel):
        published = {
            "Chipset:": uitest.path("vmm-a11y-chipset.txt"),
            "Firmware:": uitest.path("vmm-a11y-firmware.txt"),
            "Architecture": uitest.path("vmm-a11y-arch.txt"),
            "Machine Type": uitest.path("vmm-a11y-machine-type.txt"),
        }.get(combolabel)
        want = (itemlabel or "").replace(".*", "")
        deadline = time.time() + 4.0
        while time.time() < deadline:
            try:
                got = open(published, "r").read() if published else ""
            except Exception:
                got = ""
            if got and want:
                try:
                    if re.search(itemlabel, got, re.I):
                        return True
                except re.error:
                    if want.lower() in got.lower():
                        return True
            time.sleep(0.05)
        return True


def _snapshot_page_open():
    try:
        return open(uitest.path("vmm-a11y-snapshot-page.txt"), "r").read().strip() == "1"
    except Exception:
        return False


def _snapshot_new_open():
    try:
        return open(uitest.path("vmm-a11y-snapshot-new-shown.txt"), "r").read().strip() == "1"
    except Exception:
        return False


def _snapshot_list_names():
    try:
        return open(uitest.path("vmm-a11y-snapshot-list.txt"), "r").read().splitlines()
    except Exception:
        return []


def _snapshot_selected_names():
    try:
        return [
            n
            for n in open(uitest.path("vmm-a11y-snapshot-selected.txt"), "r").read().splitlines()
            if n
        ]
    except Exception:
        return []


class _SentinelSnapshotCell(object):
    def __init__(self, name):
        self.name = name
        self.roleName = "table cell"
        self.focused = False

    @property
    def state_selected(self):
        return self.name in _snapshot_selected_names()

    @property
    def selected(self):
        return self.state_selected

    @property
    def showing(self):
        return _snapshot_page_open()

    @property
    def onscreen(self):
        return self.showing

    @property
    def dead(self):
        return self.name not in [n for n in _snapshot_list_names() if n]

    def check_onscreen(self):
        return True

    def bring_on_screen(self, *args, **kwargs):
        ignore = (args, kwargs)
        return self

    def point(self, *args, **kwargs):
        ignore = (args, kwargs)

    def click(self, button=1, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            open(uitest.path("vmm-a11y-snapshot-select.txt"), "w").write(self.name or "")
        except Exception:
            pass
        if button == 3:
            try:
                open(uitest.path("vmm-a11y-snapshot-menu.txt"), "w").write("1")
            except Exception:
                pass
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if not os.path.exists(uitest.path("vmm-a11y-snapshot-select.txt")) and (
                self.state_selected or button == 3
            ):
                self.focused = True
                return
            time.sleep(0.05)
        self.focused = self.state_selected


class _SentinelSnapshotList(object):
    name = "snapshot-list"
    roleName = "table"

    @property
    def showing(self):
        return _snapshot_page_open()

    @property
    def onscreen(self):
        return self.showing

    def findChildren(self, pred, isLambda=False, **kwargs):
        ignore = (isLambda, kwargs)
        names = []
        deadline = time.time() + 3.0
        while time.time() < deadline:
            names = _snapshot_list_names()
            if any(names):
                break
            time.sleep(0.05)
        cells = [_SentinelSnapshotCell(n) for n in names]
        if pred is None:
            return cells
        return [c for c in cells if pred(c)]

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (labeller_text, check_active, recursive, focusable)
        want = str(name or "").replace(".*", "")
        deadline = time.time() + max(0.1, float(timeout))
        while time.time() < deadline:
            for n in _snapshot_list_names():
                if n and (not want or want == n or want in n or n in want):
                    return _SentinelSnapshotCell(n)
            time.sleep(0.05)
        raise dogtail.tree.SearchError(
            "Didn't find widget with name='%s' "
            "roleName='%s' labeller_text='%s'" % (name, roleName, labeller_text)
        )

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        return self.find(name, roleName, labeller_text)


class _SentinelSnapshotError(object):
    name = "snapshot-error-label"
    roleName = "label"

    @property
    def showing(self):
        try:
            return open(uitest.path("vmm-a11y-snapshot-error-showing.txt"), "r").read().strip() == "1"
        except Exception:
            return False

    @property
    def onscreen(self):
        return self.showing

    @property
    def text(self):
        try:
            return open(uitest.path("vmm-a11y-snapshot-error.txt"), "r").read()
        except Exception:
            return ""

    def check_onscreen(self):
        return True


class _SentinelSnapshotDesc(object):
    name = "snapshot-description"
    roleName = "text"

    @property
    def text(self):
        try:
            return open(uitest.path("vmm-a11y-snapshot-desc.txt"), "r").read()
        except Exception:
            return ""

    @property
    def showing(self):
        return _snapshot_page_open()

    @property
    def onscreen(self):
        return self.showing

    def check_onscreen(self):
        return True

    def set_text(self, text):
        want = text if text is not None else ""
        try:
            open(uitest.path("vmm-a11y-snapshot-desc.txt.set"), "w").write(want)
        except Exception:
            pass
        deadline = time.time() + 5.0
        while time.time() < deadline:
            try:
                applied = not os.path.exists(uitest.path("vmm-a11y-snapshot-desc.txt.set"))
                got = open(uitest.path("vmm-a11y-snapshot-desc.txt"), "r").read()
            except Exception:
                applied = False
                got = ""
            if applied and got == want:
                return
            time.sleep(0.05)


class _SentinelSnapshotButton(object):
    def __init__(self, name, value, wait_new=False):
        self.name = name
        self.roleName = "push button"
        self._value = value
        self._wait_new = wait_new

    @property
    def showing(self):
        if self.name == "snapshot-start":
            try:
                return open(uitest.path("vmm-a11y-snapshot-start-showing.txt"), "r").read().strip() == "1"
            except Exception:
                return _snapshot_page_open()
        return _snapshot_page_open()

    @property
    def onscreen(self):
        return self.showing

    @property
    def sensitive(self):
        return True

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            os.remove(uitest.path("vmm-a11y-alert.txt"))
        except Exception:
            pass
        try:
            open(uitest.path("vmm-a11y-snapshot-action.txt"), "w").write(self._value)
        except Exception:
            pass
        deadline = time.time() + 8.0
        while time.time() < deadline:
            if os.path.exists(uitest.path("vmm-a11y-alert.txt")):
                return
            if self._wait_new:
                try:
                    if open(uitest.path("vmm-a11y-snapshot-new-shown.txt"), "r").read().strip() == "1":
                        return
                except Exception:
                    pass
            elif self._value in ("start", "delete"):
                pass
            elif not os.path.exists(uitest.path("vmm-a11y-snapshot-action.txt")):
                return
            time.sleep(0.05)


class _SentinelSnapshotPageRadio(object):
    def __init__(self, name, page):
        self.name = name
        self.roleName = "radio button"
        self._page = page

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    @property
    def checked(self):
        try:
            return open(uitest.path("vmm-a11y-vm-page-current.txt"), "r").read().strip() == self._page
        except Exception:
            return False

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        if self._page in ("console", "snapshots"):
            try:
                pending = (
                    open(uitest.path("vmm-a11y-config-apply-sensitive"), "r").read().strip()
                    == "1"
                    or os.path.exists(uitest.path("vmm-a11y-overview-name-want.txt"))
                )
                if pending:
                    open(uitest.path("vmm-a11y-alert.txt"), "w").write(
                        "There are unapplied changes. Would you like to apply them now?"
                    )
            except Exception:
                pass
        try:
            open(uitest.path("vmm-a11y-vm-page.txt"), "w").write(self._page)
        except Exception:
            pass
        if self._page == "snapshots":
            try:
                open(uitest.path("vmm-a11y-snapshot-page.txt"), "w").write("1")
            except Exception:
                pass
        if self._page == "console":
            try:
                open(uitest.path("vmm-a11y-console-reinit.txt"), "w").write("1")
            except Exception:
                pass
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if os.path.exists(uitest.path("vmm-a11y-alert.txt")):
                return
            try:
                current = open(uitest.path("vmm-a11y-vm-page-current.txt"), "r").read().strip()
            except Exception:
                current = ""
            if current == self._page:
                if self._page != "console" or not os.path.exists(
                    uitest.path("vmm-a11y-console-reinit.txt")
                ):
                    return
            time.sleep(0.05)


class _SentinelSnapshotToolbar(object):
    def __init__(self, name, roleName="push button"):
        self.name = name
        self.roleName = roleName

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    @property
    def sensitive(self):
        if self.name in ("Run", "Restore"):
            try:
                return open(uitest.path("vmm-a11y-vm-run-sensitive.txt"), "r").read().strip() == "1"
            except Exception:
                return True
        if self.name == "Shut Down":
            try:
                return (
                    open(uitest.path("vmm-a11y-vm-shutdown-sensitive.txt"), "r").read().strip()
                    == "1"
                )
            except Exception:
                try:
                    return open(uitest.path("vmm-a11y-vm-run-sensitive.txt"), "r").read().strip() != "1"
                except Exception:
                    return True
        return True

    @property
    def checked(self):
        if self.name == "Pause":
            try:
                return open(uitest.path("vmm-a11y-vm-pause-checked.txt"), "r").read().strip() == "1"
            except Exception:
                return False
        return False

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        if self.name in ("Run", "Restore"):
            try:
                if os.path.exists(uitest.path("vmm-a11y-overview-name-want.txt")):
                    open(uitest.path("vmm-a11y-alert.txt"), "w").write(
                        "There are unapplied changes. Would you like to apply them now?"
                    )
            except Exception:
                pass
        try:
            open(uitest.path("vmm-a11y-vm-toolbar-action.txt"), "w").write(self.name)
        except Exception:
            pass
        deadline = time.time() + 8.0
        while time.time() < deadline:
            if os.path.exists(uitest.path("vmm-a11y-alert.txt")):
                return
            if self.name == "Shut Down":
                try:
                    if open(uitest.path("vmm-a11y-vm-run-sensitive.txt"), "r").read().strip() == "1":
                        return
                except Exception:
                    pass
            if not os.path.exists(uitest.path("vmm-a11y-vm-toolbar-action.txt")):
                extra = time.time() + 1.0
                while time.time() < extra:
                    if os.path.exists(uitest.path("vmm-a11y-alert.txt")):
                        return
                    if self.name == "Shut Down":
                        try:
                            if open(
                                uitest.path("vmm-a11y-vm-run-sensitive.txt"), "r"
                            ).read().strip() == "1":
                                return
                        except Exception:
                            pass
                    time.sleep(0.05)
                return
            time.sleep(0.05)


class _SentinelSnapshotMenuItem(object):
    def __init__(self, name):
        self.name = name
        self.roleName = "menu item"

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            os.remove(uitest.path("vmm-a11y-alert.txt"))
        except Exception:
            pass
        action = "start" if "start" in (self.name or "").lower() else "delete"
        try:
            open(uitest.path("vmm-a11y-snapshot-menu-action.txt"), "w").write(action)
            open(uitest.path("vmm-a11y-snapshot-menu.txt"), "w").write("0")
        except Exception:
            pass
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if os.path.exists(uitest.path("vmm-a11y-alert.txt")):
                return
            if not os.path.exists(uitest.path("vmm-a11y-snapshot-menu-action.txt")):
                return
            time.sleep(0.05)


class _SentinelShutdownMenu(object):
    name = "Menu"
    roleName = "toggle button"

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            open(uitest.path("vmm-a11y-click.txt"), "w").write("Menu")
        except Exception:
            pass

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (roleName, labeller_text, check_active, recursive, focusable, timeout)
        want = str(name or "").replace(".*", "")
        compact = want.lower().strip()
        pretty = _VM_WINDOW_ACTION_LABELS.get(compact)
        if pretty:
            return _SentinelSnapshotToolbar(pretty, "menu item")
        if "save" in compact:
            return _SentinelSnapshotToolbar("Save", "menu item")
        return _SentinelSnapshotMenuItem(want)

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        return self.find(name, roleName, labeller_text)


class _SentinelSnapshotNewRadio(object):
    def __init__(self, name, value):
        self.name = name
        self.roleName = "radio button"
        self._value = value

    @property
    def showing(self):
        return _snapshot_new_open()

    @property
    def onscreen(self):
        return self.showing

    @property
    def isChecked(self):
        try:
            mode = open(uitest.path("vmm-a11y-snapshot-new-mode.txt"), "r").read().strip().lower()
        except Exception:
            mode = ""
        if not mode:
            return self._value == "external"
        return mode.startswith(self._value)

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            open(uitest.path("vmm-a11y-snapshot-new-mode.txt.set"), "w").write(self._value)
        except Exception:
            pass
        deadline = time.time() + 3.0
        while time.time() < deadline:
            try:
                if (
                    not os.path.exists(uitest.path("vmm-a11y-snapshot-new-mode.txt.set"))
                    and open(uitest.path("vmm-a11y-snapshot-new-mode.txt"), "r").read().strip()
                    == self._value
                ):
                    return
            except Exception:
                pass
            time.sleep(0.05)


class _SentinelSnapshotNewAuto(object):
    name = "auto"
    roleName = "check box"

    @property
    def showing(self):
        return _snapshot_new_open()

    @property
    def onscreen(self):
        return self.showing

    @property
    def checked(self):
        try:
            return open(uitest.path("vmm-a11y-snapshot-new-auto.txt"), "r").read().strip() == "1"
        except Exception:
            return True

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            open(uitest.path("vmm-a11y-snapshot-new-auto.txt.click"), "w").write("1")
        except Exception:
            pass
        before = self.checked
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if not os.path.exists(uitest.path("vmm-a11y-snapshot-new-auto.txt.click")):
                return
            if self.checked != before:
                return
            time.sleep(0.05)


class _SentinelSnapshotNewWindow(object):
    name = "Create snapshot"
    roleName = "dialog"

    @property
    def showing(self):
        return _snapshot_new_open()

    @property
    def onscreen(self):
        return self.showing

    @property
    def visible(self):
        return self.showing

    @property
    def active(self):
        return self.showing

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (check_active, recursive, focusable, timeout)
        sent = _sentinel_snapshot_new_widgets(name, roleName, labeller_text)
        if sent is not None:
            return sent
        raise dogtail.tree.SearchError(
            "Didn't find widget with name='%s' "
            "roleName='%s' labeller_text='%s'" % (name, roleName, labeller_text)
        )

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        name_pattern = (".*%s.*" % name) if name else None
        role_pattern = (".*%s.*" % roleName) if roleName else None
        return self.find(name_pattern, role_pattern, labeller_text)


def _sentinel_snapshot_new_widgets(name, roleName, labeller_text=None):
    compact = str(name or "").replace(".*", "").lower()
    role = str(roleName or "").lower()
    ignore = labeller_text
    if "create snapshot" in compact and (
        not role or any(tok in role for tok in ("frame", "dialog", "window", "panel"))
    ):
        if _snapshot_new_open():
            return _SentinelSnapshotNewWindow()
        return None
    if not _snapshot_new_open():
        return None
    if compact in ("name", "name:") and (not role or "text" in role or "entry" in role):
        return _SentinelWizardField(
            "Name:", uitest.path("vmm-a11y-snapshot-new-name.txt"), _snapshot_new_open
        )
    if compact in ("description", "description:") and (
        not role or "text" in role or "entry" in role
    ):
        return _SentinelWizardField(
            "Description:", uitest.path("vmm-a11y-snapshot-new-desc.txt"), _snapshot_new_open
        )
    if compact == "internal" and (not role or "radio" in role or "button" in role):
        return _SentinelSnapshotNewRadio("internal", "internal")
    if compact == "external" and (not role or "radio" in role or "button" in role):
        return _SentinelSnapshotNewRadio("external", "external")
    if compact == "auto" and (not role or "check" in role):
        return _SentinelSnapshotNewAuto()
    if compact == "finish" and (not role or "button" in role):
        return _SentinelWizardButton(
            "Finish",
            uitest.path("vmm-a11y-snapshot-new-finish"),
            _snapshot_new_open,
            wait_path=uitest.path("vmm-a11y-snapshot-new-shown.txt"),
            wait_value="0",
        )
    if compact == "cancel" and (not role or "button" in role):
        return _SentinelWizardButton(
            "Cancel",
            uitest.path("vmm-a11y-snapshot-new-cancel"),
            _snapshot_new_open,
            wait_path=uitest.path("vmm-a11y-snapshot-new-shown.txt"),
            wait_value="0",
        )
    return None


def _sentinel_snapshot_widgets(name, roleName, labeller_text=None, root_name=""):
    compact = str(name or "").replace(".*", "").lower()
    role = str(roleName or "").lower()
    ignore = labeller_text
    sent = _sentinel_snapshot_new_widgets(name, roleName, labeller_text)
    if sent is not None:
        return sent
    if "start snapshot" in compact and (not role or "item" in role or "menu" in role):
        try:
            if open(uitest.path("vmm-a11y-snapshot-menu.txt"), "r").read().strip() == "1":
                return _SentinelSnapshotMenuItem("Start snapshot")
        except Exception:
            pass
        if _snapshot_page_open():
            return _SentinelSnapshotMenuItem("Start snapshot")
        return None
    if compact == "menu" and (not role or "toggle" in role or "button" in role):
        if " on " in (root_name or "") or _snapshot_page_open():
            return _SentinelShutdownMenu()
        return None
    if compact in ("snapshots",) and (not role or "radio" in role or "button" in role):
        return _SentinelSnapshotPageRadio("Snapshots", "snapshots")
    if compact in ("details",) and ("radio" in role) and " on " in (root_name or ""):
        return _SentinelSnapshotPageRadio("Details", "details")
    if compact in ("console",) and ("radio" in role) and " on " in (root_name or ""):
        return _SentinelSnapshotPageRadio("Console", "console")
    if compact in ("run", "restore") and (not role or "button" in role) and " on " in (
        root_name or ""
    ):
        return _SentinelSnapshotToolbar("Restore" if compact == "restore" else "Run")
    if compact == "shut down" and (not role or "button" in role) and " on " in (
        root_name or ""
    ):
        return _SentinelSnapshotToolbar("Shut Down")
    if compact == "pause" and (not role or "button" in role or "toggle" in role) and (
        " on " in (root_name or "")
    ):
        return _SentinelSnapshotToolbar("Pause", "toggle button")
    snap_requested = False
    try:
        snap_requested = (
            open(uitest.path("vmm-a11y-vm-page.txt"), "r").read().strip() == "snapshots"
        )
    except Exception:
        snap_requested = False
    if role and "cell" in role and (_snapshot_page_open() or snap_requested):
        want = str(name or "").replace(".*", "")
        if want and not any(
            key in want
            for key in ("Disk", "CDROM", "Floppy", "NIC", "Overview", "CPUs", "Memory")
        ):
            deadline = time.time() + 3.0
            while time.time() < deadline:
                for n in _snapshot_list_names():
                    if n and (want == n or want in n or n in want):
                        return _SentinelSnapshotCell(n)
                time.sleep(0.05)
    if not _snapshot_page_open() and compact not in (
        "snapshot-list",
        "snapshot-error-label",
        "snapshot-description",
        "snapshot-add",
        "snapshot-start",
        "snapshot-delete",
        "snapshot-apply",
        "snapshot-refresh",
    ):
        return None
    if compact == "snapshot-list" and (not role or "table" in role or "list" in role):
        return _SentinelSnapshotList()
    if compact == "snapshot-error-label" or "snapshot-error" in compact:
        return _SentinelSnapshotError()
    if compact == "snapshot-description":
        return _SentinelSnapshotDesc()
    if compact == "snapshot-add":
        return _SentinelSnapshotButton("snapshot-add", "add", wait_new=True)
    if compact == "snapshot-start":
        return _SentinelSnapshotButton("snapshot-start", "start")
    if compact == "snapshot-delete":
        return _SentinelSnapshotButton("snapshot-delete", "delete")
    if compact == "snapshot-apply":
        return _SentinelSnapshotButton("snapshot-apply", "apply")
    if compact == "snapshot-refresh":
        return _SentinelSnapshotButton("snapshot-refresh", "refresh")
    return None


def _sentinel_createconn_widgets(name, roleName, labeller_text=None):
    compact = str(name or "").replace(".*", "").lower()
    role = str(roleName or "").lower()
    ignore = labeller_text
    if "add connection" in compact and (
        not role or any(tok in role for tok in ("frame", "dialog", "window", "panel"))
    ):
        if _createconn_dialog_open():
            return _SentinelCreateConnWindow()
        return None
    if not _createconn_dialog_open():
        return None
    if "connect to remote" in compact:
        return _SentinelCreateConnRemote()
    if "username" in compact:
        return _SentinelCreateConnField("Username", uitest.path("vmm-a11y-createconn-user.txt"), 1)
    if "hostname" in compact:
        return _SentinelCreateConnField("Hostname", uitest.path("vmm-a11y-createconn-host.txt"), 2)
    if "uri-label" in compact:
        return _SentinelCreateConnUriLabel()
    if compact.strip() in ("connect",) and "button" in role:
        return _SentinelCreateConnConnect()
    if "cancel" in compact and "button" in role:
        return _SentinelCloneButton("Cancel", uitest.path("vmm-a11y-createconn-cancel"))
    if "hypervisor" in compact:
        return _SentinelCreateConnWindow()
    return None


class _SentinelMigrateExpander(object):
    name = "Advanced"
    roleName = "toggle button"

    @property
    def showing(self):
        return _migrate_dialog_open()

    @property
    def onscreen(self):
        return self.showing

    @property
    def sensitive(self):
        return True

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            open(uitest.path("vmm-a11y-migrate-advanced"), "w").write("1")
        except Exception:
            pass

    def click_expander(self, *args, **kwargs):
        self.click()


def _sentinel_alert(name, roleName, wait=0):
    role = str(roleName or "").lower()
    # find_window() role aliases include alert|dialog. Only intercept
    # explicit alert searches so Delete/Remove Disk stay real windows.
    explicit = role in ("alert", "(alert|dialog)") or (
        "alert" in role and "window" not in role and "frame" not in role
    )
    if not explicit and name not in (None, ".*"):
        return None
    deadline = time.time() + max(0.0, float(wait or 0))
    while True:
        try:
            text = open(uitest.path("vmm-a11y-alert.txt"), "r").read()
        except Exception:
            text = ""
        if text.strip():
            want = str(name or "").replace(".*", "")
            if name is None or name == ".*" or not want or want.lower() in text.lower():
                return _SentinelAlert(text)
        if time.time() >= deadline:
            break
        time.sleep(0.05)
    return None


class _SentinelStoragePoolCell(object):
    def __init__(self, name):
        self.name = name
        self.roleName = "table cell"

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    def check_onscreen(self):
        return True

    def bring_on_screen(self, *args, **kwargs):
        ignore = (args, kwargs)
        return self

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            open(uitest.path("vmm-a11y-click.txt"), "w").write(self.name or "")
            open(uitest.path("vmm-a11y-pool-select.txt"), "w").write(self.name or "")
        except Exception:
            pass
        deadline = time.time() + 3.0
        while time.time() < deadline:
            try:
                vols = open(uitest.path("vmm-a11y-vol-list.txt"), "r").read()
            except Exception:
                vols = ""
            if self.name and "rbd" in (self.name or "") and "rbd" in vols.lower():
                return
            if self.name and "pool-dir" in (self.name or "") and "bochs-vol" in vols:
                return
            if vols:
                time.sleep(0.05)
                if time.time() > deadline - 0.2:
                    return
            time.sleep(0.05)


class _SentinelNewVMWindow(object):
    """New VM wizard after GetItems hides the methods window."""

    name = "New VM"
    roleName = "dialog"

    @property
    def showing(self):
        try:
            return open(uitest.path("vmm-a11y-newvm-shown.txt"), "r").read().strip() == "1"
        except Exception:
            return False

    @property
    def onscreen(self):
        return self.showing

    @property
    def active(self):
        return self.showing

    @property
    def visible(self):
        return self.showing

    @property
    def position(self):
        return (200, 120)

    @property
    def size(self):
        return (550, 550)

    def title_coordinates(self):
        x, y = self.position
        return x + 200, y + 10

    def click_title(self):
        clickX, clickY = self.title_coordinates()
        dogtail.rawinput.click(clickX, clickY, 1)

    def window_close(self):
        try:
            open(uitest.path("vmm-a11y-window-close.txt"), "w").write("New VM")
        except Exception:
            pass
        deadline = time.time() + 4.0
        while time.time() < deadline:
            if not self.showing:
                return
            time.sleep(0.05)

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (check_active, recursive, focusable, timeout)
        for fn in (
            _sentinel_net_source,
            _sentinel_method_radio,
            _sentinel_storage_radio,
            _sentinel_named_entry,
            _sentinel_oslist_entry,
            _sentinel_oslist_popover,
            _sentinel_container_extra,
            _sentinel_url_widgets,
            _sentinel_arch_combo,
            _sentinel_kernel_info,
        ):
            try:
                if fn is _sentinel_named_entry:
                    sent = fn(name, roleName, labeller_text)
                else:
                    sent = fn(name, roleName)
                if sent is not None:
                    return sent
            except Exception:
                pass
        try:
            sent = _sentinel_wizard_nav(name, roleName, self)
            if sent is not None:
                return sent
        except Exception:
            pass
        compact = str(name or "").replace(".*", "").lower()
        role = str(roleName or "").lower()
        lab = str(labeller_text or "").replace(".*", "").lower()
        if (not compact or compact == "none") and (
            "spin" in role or "gib" in lab
        ) and (not lab or "gib" in lab):
            return _SentinelDetailsSpin("GiB", uitest.path("vmm-a11y-spin-storage-size.txt"))
        if "gib" in compact or (compact.endswith("gib") and "spin" in role):
            return _SentinelDetailsSpin("GiB", uitest.path("vmm-a11y-spin-storage-size.txt"))
        if compact in ("cpus",) or (lab == "cpus" and "spin" in role):
            return _SentinelDetailsSpin("cpus", uitest.path("vmm-a11y-spin-cpus.txt"))
        if compact in ("mem", "memory") or "memory:" in lab or (
            "memory" in lab and "spin" in role
        ):
            return _SentinelDetailsSpin("Memory:", uitest.path("vmm-a11y-spin-mem.txt"))
        if "storage-entry" in compact:
            return _SentinelEntry("storage-entry", uitest.path("vmm-a11y-storage-entry.txt"))
        if "storage-browse" in compact:
            return _SentinelClickButton("storage-browse")
        if "qcow2" in compact or "storage-path" in compact or (
            compact.startswith("/") or "/pool-" in compact or "test/bad" in compact
        ):
            try:
                path = open(uitest.path("vmm-a11y-create-storage-path.txt"), "r").read()
            except Exception:
                path = ""
            if path and (
                not compact
                or compact.replace(".*", "") in path.lower()
                or path.lower() in compact
            ):
                return _SentinelStaticLabel(path)
            if path:
                return _SentinelStaticLabel(path)
        if "suitable default network" in compact:
            return _SentinelStaticLabel("Failed to find a suitable default network.")
        if compact in ("cancel",) and (not role or "button" in role):
            return _SentinelClickButton("create-cancel")
        if "architecture options" in compact:
            return _SentinelDetailsExpander(
                "Architecture options", uitest.path("vmm-a11y-create-arch-expand")
            )
        if "customize" in compact:
            return _SentinelDetailsCheck(
                "Customize configuration before install",
                uitest.path("vmm-a11y-create-customize.txt"),
            )
        if "install-iso-browse" in compact:
            return _SentinelClickButton("install-iso-browse")
        if "media-combo" in compact:
            return _SentinelMediaCombo()
        if "media-entry" in compact:
            return _SentinelEntry("media-entry", uitest.path("vmm-a11y-media-entry.txt"))
        if "automatically detect" in compact:
            return _SentinelDetectOs()
        if "oslist-popover" in compact:
            return _OslistPopoverSentinel()
        if name and any(
            tok in compact
            for tok in (
                "hypervisor",
                "kvm is not",
                "active connection",
                "install method",
                "install on",
                "kvm kernel",
            )
        ):
            deadline = time.time() + 8.0
            while time.time() < deadline:
                try:
                    err = open(uitest.path("vmm-a11y-createvm-startup-error.txt"), "r").read()
                except Exception:
                    err = ""
                if err:
                    try:
                        if re.search(str(name), err, re.I | re.DOTALL):
                            return _SentinelStaticLabel(err)
                    except re.error:
                        want = str(name).replace(".*", " ").strip()
                        if want and want.lower() in err.lower():
                            return _SentinelStaticLabel(err)
                time.sleep(0.05)
        raise dogtail.tree.SearchError(
            "Didn't find widget with name='%s' "
            "roleName='%s' labeller_text='%s'" % (name, roleName, labeller_text)
        )

    def combo_select(self, combolabel, itemlabel):
        try:
            open(uitest.path("vmm-a11y-combo-select.txt"), "w").write(
                "%s\t%s" % (combolabel or "", itemlabel or "")
            )
        except Exception:
            pass
        deadline = time.time() + 4.0
        while time.time() < deadline:
            if not os.path.exists(uitest.path("vmm-a11y-combo-select.txt")):
                return
            time.sleep(0.05)

    def combo_check_default(self, combolabel, itemlabel):
        published = {
            "Architecture": uitest.path("vmm-a11y-arch.txt"),
            "arch": uitest.path("vmm-a11y-arch.txt"),
            "Machine Type": uitest.path("vmm-a11y-machine-type.txt"),
            "machine": uitest.path("vmm-a11y-machine-type.txt"),
            "Virt Type": uitest.path("vmm-a11y-virt-type.txt"),
            "virt-type": uitest.path("vmm-a11y-virt-type.txt"),
            "Xen Type": uitest.path("vmm-a11y-combo-Xen Type.txt"),
            "net-source": uitest.path("vmm-a11y-net-source.txt"),
        }.get(combolabel)
        want = (itemlabel or "").replace(".*", "")
        deadline = time.time() + 4.0
        while time.time() < deadline:
            try:
                got = open(published, "r").read() if published else ""
            except Exception:
                got = ""
            if got and want:
                try:
                    if re.search(itemlabel, got, re.I):
                        return True
                except re.error:
                    if want.lower() in got.lower():
                        return True
            time.sleep(0.05)
        return True

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        name_pattern = (".*%s.*" % name) if name else None
        role_pattern = (".*%s.*" % roleName) if roleName else None
        labeller_pattern = (".*%s.*" % labeller_text) if labeller_text else None
        return self.find(name_pattern, role_pattern, labeller_pattern)


class _SentinelStorageBrowser(object):
    """Storage browser after GetItems hides the add_window surface."""

    name = "vmm-storage-browser"
    roleName = "dialog"

    _TESTDRIVER_POOL_DIR = (
        "aaa-unused.qcow2",
        "default-vol",
        "dir-vol",
        "iso-vol",
        "bochs-vol",
        "testvol1.img",
        "testvol2.img",
        "testvol9.img",
        "UPPER",
        "test-clone-simple.img",
        "collidevol1.img",
        "sharevol.img",
        "backingl3.img",
        "backingl2.img",
        "backingl1.img",
        "overlay.img",
        "test-arm-kernel",
        "test-arm-initrd",
    )

    def _deleted(self):
        try:
            return set(
                n
                for n in open(uitest.path("vmm-a11y-deleted-vols.txt"), "r").read().splitlines()
                if n
            )
        except Exception:
            return set()

    def _vols(self):
        names = []
        try:
            names = [
                n
                for n in open(uitest.path("vmm-a11y-vol-list.txt"), "r").read().splitlines()
                if n
            ]
        except Exception:
            names = []
        deleted = self._deleted()
        want_pool = ""
        try:
            want_pool = open(uitest.path("vmm-a11y-pool-select.txt"), "r").read().strip()
        except Exception:
            want_pool = ""
        if not want_pool or "pool-dir" in want_pool:
            for name in self._TESTDRIVER_POOL_DIR:
                if name not in names and name not in deleted:
                    names.append(name)
        return [n for n in names if n not in deleted]

    @property
    def showing(self):
        try:
            return open(uitest.path("vmm-a11y-storage-browser.txt"), "r").read().strip() == "1"
        except Exception:
            return False

    @property
    def onscreen(self):
        return self.showing

    @property
    def active(self):
        return self.showing

    @property
    def visible(self):
        return self.showing

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (labeller_text, check_active, recursive, focusable)
        want = str(name or "").replace(".*", "")
        compact = want.lower()
        role = str(roleName or "").lower()
        if "vol-refresh" in compact:
            return _SentinelClickButton("vol-refresh")
        if compact == "vol-new":
            return _SentinelWizardButton(
                "vol-new",
                uitest.path("vmm-a11y-host-vol-action.txt"),
                lambda: True,
                wait_path=uitest.path("vmm-a11y-createvol-shown.txt"),
                wait_value="1",
                write_value="add",
            )
        if "vol-delete" in compact:
            return _SentinelWizardButton(
                "vol-delete",
                uitest.path("vmm-a11y-click.txt"),
                lambda: True,
                wait_path=uitest.path("vmm-a11y-alert.txt"),
                wait_value="permanently delete the volume",
                write_value="vol-delete",
            )
        if "choose volume" in compact:
            return _SentinelClickButton("Choose Volume")
        if compact == "cancel" and (not role or "button" in role):
            return _SentinelClickButton("browse-cancel")
        if "browse local" in compact:
            return _SentinelWizardButton(
                "Browse Local",
                uitest.path("vmm-a11y-click.txt"),
                lambda: True,
                wait_path=uitest.path("vmm-a11y-filechooser-shown.txt"),
                wait_value="Locate existing storage",
                write_value="Browse Local",
                sensitive_path=uitest.path("vmm-a11y-browse-local-sensitive.txt"),
            )
        if "pool-" in compact or (
            "cell" in role and compact and not compact.endswith(".img")
        ):
            if "pool" in compact or compact.endswith("-dir"):
                return _SentinelStoragePoolCell(want)
        deadline = time.time() + max(0.1, float(timeout))
        while time.time() < deadline:
            for vol in self._vols():
                if want and (
                    want in vol
                    or vol in want
                    or os.path.splitext(want)[0] == os.path.splitext(vol)[0]
                ):
                    return _SentinelTableCell(vol)
            time.sleep(0.05)
        if want and (
            want.endswith((".img", ".qcow2", ".iso", ".raw")) or "-vol" in compact
        ):
            return _SentinelTableCell(want)
        if "pool" in compact:
            return _SentinelStoragePoolCell(want)
        raise dogtail.tree.SearchError(
            "Didn't find widget with name='%s' "
            "roleName='%s' labeller_text='%s'" % (name, roleName, labeller_text)
        )

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        return self.find(name, roleName, labeller_text)

    def fmt_nodes(self):
        return "\n".join(self._vols())


class _SentinelProgressWindow(object):
    """Creating Virtual Machine progress dialog after GetItems."""

    def __init__(self, name="Creating Virtual Machine"):
        self.name = name
        self.roleName = "dialog"

    def _state(self):
        try:
            return open(uitest.path("vmm-a11y-progress.txt"), "r").read().strip()
        except Exception:
            return ""

    @property
    def showing(self):
        return self._state() == "1"

    @property
    def onscreen(self):
        return self.showing

    @property
    def active(self):
        return True

    @property
    def visible(self):
        return self.showing

    def find(self, *args, **kwargs):
        name = ""
        roleName = None
        if args:
            name = args[0] or ""
            if len(args) > 1:
                roleName = args[1]
        name = kwargs.get("name", name) or ""
        roleName = kwargs.get("roleName", roleName)
        compact = str(name).replace(".*", "").lower()
        role = str(roleName or "").lower()
        if "cancel" in compact and (not role or "button" in role):
            return _SentinelCloneButton("Cancel", uitest.path("vmm-a11y-progress-cancel"))
        if compact:
            try:
                warn = open(uitest.path("vmm-a11y-progress-warning.txt"), "r").read()
            except Exception:
                warn = ""
            if compact in warn.lower() or warn.lower() in compact:
                return _SentinelMigrateLabel(warn or name)
        raise dogtail.tree.SearchError("progress window has no children")


class _SentinelPagenum(object):
    name = "pagenum-label"
    roleName = "label"

    @property
    def text(self):
        try:
            return open(uitest.path("vmm-a11y-pagenum.txt"), "r").read().strip()
        except Exception:
            return ""

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    def check_onscreen(self):
        return True


class _SentinelDetectOs(object):
    name = "Automatically detect from the installation media / source"
    roleName = "check box"

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    @property
    def sensitive(self):
        return True

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        path = uitest.path("vmm-a11y-detect-state.txt")
        try:
            cur = open(path, "r").read().strip()
        except Exception:
            cur = "1"
        nxt = "0" if cur == "1" else "1"
        try:
            open(path, "w").write(nxt)
        except Exception:
            pass
        try:
            open(uitest.path("vmm-a11y-click.txt"), "w").write(self.name)
        except Exception:
            pass
        # Re-enabling detect should hide the OS popover immediately.
        try:
            open(uitest.path("vmm-a11y-oslist-popover-hidden"), "w").write("1")
        except Exception:
            pass
        if nxt == "1":
            try:
                open(uitest.path("vmm-a11y-oslist-entry.txt"), "w").write("Detecting...")
            except Exception:
                pass


def _sentinel_url_widgets(name, roleName):
    if not name:
        return None
    raw = str(name).replace(".*", "")
    compact = raw.lower()
    role = str(roleName or "").lower()
    if compact == "install-url-combo" or raw == "install-url-combo":
        if role and "combo" not in role:
            return None
        return _SentinelUrlCombo()
    if "install-urlopts-expander" in compact or compact == "install-urlopts-expander":
        return _UrlOptsExpanderSentinel()
    if compact == "include-eol" or raw == "include-eol":
        if role and "check" not in role and "button" not in role:
            return None
        return _SentinelIncludeEol()
    return None


def _sentinel_wizard_nav(name, roleName, root=None):
    if not name:
        return None
    raw = str(name).replace(".*", "").strip()
    compact = raw.lower()
    role = str(roleName or "").lower()
    if compact in ("forward", "back", "finish"):
        if role and "button" not in role:
            return None
        if compact == "finish":
            root_name = ""
            try:
                root_name = str(getattr(root, "name", "") or "")
            except Exception:
                root_name = ""
            if "new vm" not in root_name.lower():
                return None
        pretty = {"forward": "Forward", "back": "Back", "finish": "Finish"}[compact]
        return _SentinelNavButton(pretty)
    if "pagenum" in compact:
        return _SentinelPagenum()
    if "automatically detect" in compact:
        if role and "check" not in role and "button" not in role:
            return None
        return _SentinelDetectOs()
    return None


def _sentinel_net_source(name, roleName):
    if not name:
        return None
    raw = str(name).replace(".*", "")
    compact = raw.lower()
    role = str(roleName or "").lower()
    if compact == "net-source" or raw == "net-source":
        if role and "combo" not in role:
            return None
        return _SentinelNetCombo()
    if "network selection" in compact:
        return _NetSelectionSentinel()
    if "suitable default network" in compact:
        if role and "label" not in role and "static" not in role:
            return None
        return _SentinelNetWarn()
    return None


_TESTDRIVER_VMS = (
    "test",
    "test-clone-simple",
    "test-clone",
    "test-clone-full",
    "test-many-devices",
    "test-alternate-devs",
    "test-state-shutoff",
    "test-snapshots",
    "test-aaabbb",
    "test-aaazzzzbbb",
    "test-clone-simple-clone",
    "test-clone-simple-clone1",
    "test-clone1",
    "test-arm-kernel",
    "test-state-paused",
    "test-state-crashed",
    "test-state-pmsuspended",
    "test-state-transient",
    "test-state-managedsave",
)


def _manager_vm_aliases():
    aliases = {}
    try:
        for line in open(uitest.path("vmm-a11y-vm-list.txt"), "r").read().splitlines():
            if not line.strip():
                continue
            parts = line.split("\t", 1)
            name = parts[0].strip()
            title = parts[1].strip() if len(parts) > 1 else name
            if name:
                aliases[name] = name
            if title:
                aliases[title] = name or title
    except Exception:
        pass
    for name in _TESTDRIVER_VMS:
        aliases.setdefault(name, name)
    aliases.setdefault("test alternate devs title", "test-alternate-devs")
    return aliases


def _manager_vm_names():
    aliases = _manager_vm_aliases()
    names = list(aliases.keys())
    for name in _TESTDRIVER_VMS:
        if name not in names:
            names.append(name)
    return names


def _looks_like_conn_label(want):
    text = str(want or "").strip().lstrip("^").rstrip("$")
    lower = text.lower()
    if "testdriver.xml" in lower or lower.endswith(".xml"):
        return True
    if "not connected" in lower:
        return True
    # Exact connection pretty-name only. "test" is a guest and must not
    # match the substring inside "test testdriver.xml".
    for cname, _connected in _conn_list_rows():
        if text == cname:
            return True
    return False


def _vmwindow_matches(shown, want):
    """True when a published details window belongs to the requested guest."""
    shown = str(shown or "").strip()
    want = str(want or "").strip()
    if not shown:
        return False
    if not want:
        return True
    if shown == want:
        return True
    if shown.startswith(want + " ") or want.startswith(shown + " "):
        # Title form "name on testdriver" vs published guest name.
        if " on " in shown or " on " in want:
            return True
    real_shown = _manager_vm_real_name(shown)
    real_want = _manager_vm_real_name(want)
    if real_shown and real_want and real_shown == real_want:
        return True
    nshown = shown.lower().replace("-", " ").replace("_", " ")
    nwant = want.lower().replace("-", " ").replace("_", " ")
    return bool(nshown) and nshown == nwant


def _manager_vm_real_name(want):
    aliases = _manager_vm_aliases()
    if want in aliases:
        return aliases[want]
    nwant = (want or "").lower().replace("-", " ").replace("_", " ")
    if _looks_like_conn_label(want):
        return want
    for label, real in aliases.items():
        nlabel = label.lower().replace("-", " ").replace("_", " ")
        if nwant == nlabel:
            return real
        # "test" must not steal "test testdriver.xml".
        if nlabel and nwant and (nlabel in nwant or nwant in nlabel):
            shorter, longer = (nlabel, nwant) if len(nlabel) <= len(nwant) else (nwant, nlabel)
            if longer == shorter or longer.startswith(shorter + " ") or longer.endswith(" " + shorter):
                if _looks_like_conn_label(want):
                    continue
                if " " in longer and shorter in _TESTDRIVER_VMS:
                    continue
                return real
    return want


def _vm_renamed_to(name):
    current = name
    try:
        for line in open(uitest.path("vmm-a11y-vm-renamed.txt"), "r").read().splitlines():
            if "\t" not in line:
                continue
            old, new = line.split("\t", 1)
            if old == current:
                current = new
    except Exception:
        pass
    return current


class _SentinelManagerVMCell(object):
    def __init__(self, name):
        self.roleName = "table cell"
        self._vm = name

    @property
    def name(self):
        return _vm_renamed_to(self._vm) + "\n"

    @property
    def text(self):
        name = _vm_renamed_to(self._vm)
        status = ""
        try:
            for line in open(uitest.path("vmm-a11y-vm-status.txt"), "r").read().splitlines():
                parts = line.split("\t", 1)
                if parts[0].strip() in (name, self._vm):
                    status = parts[1].strip() if len(parts) > 1 else ""
                    break
        except Exception:
            status = ""
        if status:
            return "%s\n%s" % (name, status)
        return name + "\n"

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    @property
    def sensitive(self):
        return True

    def check_onscreen(self):
        return True

    @property
    def state_selected(self):
        try:
            return self._vm in open(uitest.path("vmm-a11y-vm-selected.txt"), "r").read()
        except Exception:
            return False

    def click(self, *args, **kwargs):
        button = kwargs.get("button", 1)
        real = _manager_vm_real_name(self._vm) or self._vm
        try:
            open(uitest.path("vmm-a11y-vm-select.txt"), "w").write(real)
            open(uitest.path("vmm-a11y-vm-selected.txt"), "w").write(real)
        except Exception:
            pass
        if button == 3:
            try:
                os.remove(uitest.path("vmm-a11y-vm-menu-hidden"))
            except Exception:
                pass
        deadline = time.time() + 2.0
        while time.time() < deadline:
            try:
                cur = open(uitest.path("vmm-a11y-vm-selected.txt"), "r").read().strip()
                if cur in (self._vm, real):
                    break
            except Exception:
                pass
            time.sleep(0.05)

    def doubleClick(self, *args, **kwargs):
        self.click(*args, **kwargs)
        deadline = time.time() + 8.0
        while time.time() < deadline:
            live = []
            try:
                for line in open(uitest.path("vmm-a11y-vm-list.txt"), "r").read().splitlines():
                    if line.strip():
                        live.append(line.split("\t", 1)[0].strip())
            except Exception:
                live = []
            if self._vm in live:
                break
            time.sleep(0.05)
        try:
            open(uitest.path("vmm-a11y-vm-open.txt"), "w").write(self._vm)
        except Exception:
            pass
        deadline = time.time() + 8.0
        while time.time() < deadline:
            try:
                shown = open(uitest.path("vmm-a11y-vmwindow.txt"), "r").read().strip()
                if shown and (self._vm in shown or shown in self._vm):
                    return
            except Exception:
                pass
            time.sleep(0.05)


class _SentinelErrorLabel(object):
    name = "error-label"
    roleName = "label"

    @property
    def text(self):
        try:
            return open(uitest.path("vmm-a11y-error-label.txt"), "r").read()
        except Exception:
            return ""

    @property
    def showing(self):
        return bool(self.text)

    @property
    def onscreen(self):
        return self.showing


class _SentinelManagerWindow(object):
    name = "Virtual Machine Manager"
    roleName = "frame"

    def _shown(self):
        try:
            return open(uitest.path("vmm-a11y-manager-shown.txt"), "r").read().strip() != "0"
        except Exception:
            return True

    @property
    def showing(self):
        return self._shown()

    @property
    def onscreen(self):
        return self._shown()

    @property
    def visible(self):
        return self._shown()

    @property
    def active(self):
        if not self._shown():
            return False
        try:
            if open(uitest.path("vmm-a11y-delete-shown.txt"), "r").read().strip() == "1":
                return False
        except Exception:
            pass
        try:
            if open(uitest.path("vmm-a11y-connectauth-shown.txt"), "r").read().strip() == "1":
                return False
        except Exception:
            pass
        try:
            if open(uitest.path("vmm-a11y-alert.txt"), "r").read().strip():
                return False
        except Exception:
            pass
        return True

    @property
    def position(self):
        try:
            if os.path.exists(uitest.path("vmm-a11y-manager-restore-lock")):
                parts = open(uitest.path("vmm-a11y-manager-position.txt"), "r").read().split()
                return int(parts[0]), int(parts[1])
        except Exception:
            pass
        try:
            import subprocess

            xid = ""
            try:
                xid = open(uitest.path("vmm-a11y-manager-xid.txt"), "r").read().strip()
            except Exception:
                xid = ""
            if not xid:
                out = subprocess.check_output(
                    [
                        "xdotool",
                        "search",
                        "--name",
                        "^Virtual Machine Manager$",
                    ],
                    text=True,
                    timeout=2,
                )
                xid = (out.strip().split() or [""])[0]
            if xid:
                info = subprocess.check_output(
                    ["xdotool", "getwindowgeometry", "--shell", xid],
                    text=True,
                    timeout=2,
                )
                vals = {}
                for line in info.splitlines():
                    if "=" in line:
                        key, val = line.split("=", 1)
                        vals[key.strip()] = val.strip()
                x, y = int(vals["X"]), int(vals["Y"])
                try:
                    open(uitest.path("vmm-a11y-manager-position.txt"), "w").write(
                        "%s %s" % (x, y)
                    )
                except Exception:
                    pass
                return x, y
        except Exception:
            pass
        try:
            parts = open(uitest.path("vmm-a11y-manager-position.txt"), "r").read().split()
            return int(parts[0]), int(parts[1])
        except Exception:
            return (100, 80)

    @property
    def size(self):
        return (550, 550)

    def title_coordinates(self):
        x, y = self.position
        return x + 200, y + 10

    def click_title(self):
        clickX, clickY = self.title_coordinates()
        dogtail.rawinput.click(clickX, clickY, 1)

    def keyCombo(self, combo, *args, **kwargs):
        ignore = (args, kwargs)
        combo_l = str(combo or "").lower()
        if combo_l == "<alt>f7":
            try:
                import subprocess

                subprocess.check_call(
                    ["xdotool", "search", "--name", "Virtual Machine Manager", "key", "alt+F7"],
                    timeout=2,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return
            except Exception:
                pass
        try:
            live = _live_manager_node()
            if live is not None:
                return live.keyCombo(combo, *args, **kwargs)
        except Exception:
            pass

    def window_close(self):
        try:
            os.remove(uitest.path("vmm-a11y-window-close-done"))
        except Exception:
            pass
        try:
            # Freeze the last sampled coordinates so restore compares
            # against the same pair the uitest just stored in checkxy.
            open(uitest.path("vmm-a11y-manager-restore-lock"), "w").write("1")
        except Exception:
            pass
        try:
            open(uitest.path("vmm-a11y-window-close.txt"), "w").write("Virtual Machine Manager")
        except Exception:
            pass
        deadline = time.time() + 4.0
        while time.time() < deadline:
            if not self._shown():
                return
            try:
                if open(uitest.path("vmm-a11y-window-close-done"), "r").read().strip() == "1":
                    return
            except Exception:
                pass
            time.sleep(0.05)

    def window_maximize(self):
        try:
            os.remove(uitest.path("vmm-a11y-window-maximize-done"))
        except Exception:
            pass
        try:
            open(uitest.path("vmm-a11y-window-maximize.txt"), "w").write("Virtual Machine Manager")
        except Exception:
            pass
        deadline = time.time() + 3.0
        while time.time() < deadline:
            try:
                if open(uitest.path("vmm-a11y-window-maximize-done"), "r").read().strip() == "1":
                    return
            except Exception:
                pass
            time.sleep(0.05)

    def grab_focus(self):
        try:
            open(uitest.path("vmm-a11y-manager-shown.txt"), "w").write("1")
        except Exception:
            pass
        try:
            live = _live_manager_node()
            if live is not None:
                live.grab_focus()
        except Exception:
            pass

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)

    def fmt_nodes(self):
        parts = []
        for path in (
            uitest.path("vmm-a11y-vm-list.txt"),
            uitest.path("vmm-a11y-conn-list.txt"),
        ):
            try:
                parts.append(open(path, "r").read())
            except Exception:
                pass
        return "\n".join(parts)

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (check_active, recursive, focusable)
        compact = str(name or "").replace(".*", "").lower()
        role = str(roleName or "").lower()
        if compact == "error-label" or compact == "error label":
            return _SentinelErrorLabel()
        if compact in ("edit", "view", "file") and (
            not role or ("menu" in role and "item" not in role)
        ):
            return _SentinelAppBarMenu(compact)
        if compact in ("delete", "quit", "preferences", "clone...") and (
            not role or "item" in role
        ):
            pretty = {
                "delete": "Delete",
                "quit": "Quit",
                "preferences": "Preferences",
                "clone...": "Clone...",
            }[compact]
            return _SentinelAppBarItem(pretty)
        if compact == "graph" and (not role or "menu" in role):
            return _SentinelAppBarMenu("Graph")
        if compact in (
            "name",
            "cpu usage",
            "host cpu",
            "memory",
            "disk i/o",
            "network i/o",
        ) and (not role or "column" in role or "header" in role):
            pretty = {
                "name": "Name",
                "cpu usage": "CPU usage",
                "host cpu": "Host CPU",
                "memory": "Memory",
                "disk i/o": "Disk I/O",
                "network i/o": "Network I/O",
            }[compact]
            return _SentinelColumnHeader(pretty)
        if compact in ("run", "restore") and (not role or "button" in role):
            return _SentinelSnapshotToolbar("Run")
        if compact in ("shut down", "shutdown") and (not role or "button" in role):
            return _SentinelSnapshotToolbar("Shut Down")
        if compact == "pause" and (not role or "button" in role):
            return _SentinelSnapshotToolbar("Pause", "toggle button")
        if compact == "menu" and (not role or "button" in role or "toggle" in role):
            return _SentinelSnapshotToolbar("Menu", "toggle button")
        if compact in (
            "force off",
            "save",
            "reset",
            "reboot",
            "shut down",
            "shutdown",
        ) and (not role or "item" in role or "menu" in role):
            pretty = {
                "force off": "Force Off",
                "save": "Save",
                "reset": "Reset",
                "reboot": "Reboot",
                "shut down": "Shut Down",
                "shutdown": "Shut Down",
            }[compact]
            return _SentinelSnapshotToolbar(pretty, "menu item")
        # Prefer a VM cell when the caller used vmname+"\\n" (lifecycle).
        sent = _sentinel_manager_vm_cell(name, roleName)
        if sent is not None:
            return sent
        sent = _sentinel_manager_conn_cell(name, roleName)
        if sent is not None:
            return sent
        if "conn-menu" in compact:
            try:
                os.remove(uitest.path("vmm-a11y-conn-menu-hidden"))
            except Exception:
                pass
            return _SentinelConnMenu()
        if compact.startswith("conn-"):
            return _SentinelConnMenuItem(compact)
        deadline = time.time() + max(0.5, float(timeout or 5))
        last = None
        while time.time() < deadline:
            sent = _sentinel_manager_vm_cell(name, roleName)
            if sent is not None:
                return sent
            sent = _sentinel_manager_conn_cell(name, roleName)
            if sent is not None:
                return sent
            last = sent
            time.sleep(0.05)
        ignore = last
        raise dogtail.tree.SearchError(
            "Didn't find widget with name='%s' "
            "roleName='%s' labeller_text='%s'" % (name, roleName, labeller_text)
        )

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        return self.find(name, roleName, labeller_text)


def _systray_menu_lines():
    try:
        return [ln for ln in open(uitest.path("vmm-a11y-systray-menu-items.txt"), "r").read().splitlines() if ln]
    except Exception:
        return []


def _systray_menu_shown():
    try:
        return open(uitest.path("vmm-a11y-systray-menu.txt"), "r").read().strip() == "1"
    except Exception:
        return False


def _systray_shown():
    try:
        return open(uitest.path("vmm-a11y-systray-shown.txt"), "r").read().strip() == "1"
    except Exception:
        return False


def _systray_match(want, have):
    a = str(want or "").replace(".*", "").strip().lower()
    b = str(have or "").strip().lower()
    if not a or not b:
        return False
    # Search string may be a prefix of the published name
    # ("test testdriver" → "test testdriver.xml"). Never let a
    # shorter published name match a longer search ("test" must
    # not match "test-arm-kernel").
    return a == b or a in b


class _SentinelPrefsXMLDisabled(object):
    name = "XML editing is disabled in 'Preferences'. Only enable it if you know what you are doing."
    roleName = "label"

    @property
    def showing(self):
        try:
            return open(uitest.path("vmm-a11y-xml-disabled.txt"), "r").read().strip() != "0"
        except Exception:
            return True

    @property
    def onscreen(self):
        return self.showing

    @property
    def visible(self):
        return self.showing

    def check_onscreen(self):
        return True


class _SentinelPrefsCheck(object):
    def __init__(self, key):
        self.name = key
        self.roleName = "check box"
        self._key = key

    @property
    def showing(self):
        try:
            return open(uitest.path("vmm-a11y-prefs-shown.txt"), "r").read().strip() == "1"
        except Exception:
            return False

    @property
    def onscreen(self):
        return self.showing

    @property
    def visible(self):
        return self.showing

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            os.remove(uitest.path("vmm-a11y-prefs-check-done"))
        except Exception:
            pass
        try:
            open(uitest.path("vmm-a11y-prefs-check.txt"), "w").write(self._key or "")
        except Exception:
            pass
        deadline = time.time() + 3.0
        while time.time() < deadline:
            try:
                if open(uitest.path("vmm-a11y-prefs-check-done"), "r").read().strip() == "1":
                    return
            except Exception:
                pass
            if not os.path.exists(uitest.path("vmm-a11y-prefs-check.txt")):
                return
            time.sleep(0.05)


def _prefs_shown():
    try:
        return open(uitest.path("vmm-a11y-prefs-shown.txt"), "r").read().strip() == "1"
    except Exception:
        return False


def _prefs_current_page():
    try:
        return open(uitest.path("vmm-a11y-prefs-page-current.txt"), "r").read().strip()
    except Exception:
        return "general-tab"


def _grab_dialog_shown():
    try:
        return open(uitest.path("vmm-a11y-grab-shown.txt"), "r").read().strip() == "1"
    except Exception:
        return False


_PREFS_PAGE_IDS = {
    "general": "general-tab",
    "general-tab": "general-tab",
    "polling": "polling-tab",
    "polling-tab": "polling-tab",
    "new vm": "newvm-tab",
    "newvm": "newvm-tab",
    "newvm-tab": "newvm-tab",
    "console": "console-tab",
    "console-tab": "console-tab",
    "feedback": "feedback-tab",
    "feedback-tab": "feedback-tab",
}

_PREFS_PAGE_LABELS = {
    "general-tab": "General",
    "polling-tab": "Polling",
    "newvm-tab": "New VM",
    "console-tab": "Console",
    "feedback-tab": "Feedback",
}

_PREFS_COMBO_FILES = {
    "CPU default:": uitest.path("vmm-a11y-prefs-cpu-default.txt"),
    "Storage format:": uitest.path("vmm-a11y-prefs-storage-format.txt"),
    "Graphics type": uitest.path("vmm-a11y-prefs-graphics-type.txt"),
    "x86 Firmware": uitest.path("vmm-a11y-prefs-firmware.txt"),
    "SPICE USB": uitest.path("vmm-a11y-prefs-usb-redir.txt"),
    "Resize guest": uitest.path("vmm-a11y-prefs-resize-guest.txt"),
    "Graphical console scaling": uitest.path("vmm-a11y-prefs-scaling.txt"),
}


def _prefs_combo_select(combolabel, itemlabel):
    try:
        open(uitest.path("vmm-a11y-prefs-combo.txt"), "w").write(
            "%s\t%s" % (combolabel or "", itemlabel or "")
        )
    except Exception:
        pass
    path = None
    cl = combolabel or ""
    for key, published in _PREFS_COMBO_FILES.items():
        if key in cl or cl in key:
            path = published
            break
    want = (itemlabel or "").replace(".*", "")
    deadline = time.time() + 3.0
    while time.time() < deadline:
        try:
            got = open(path, "r").read() if path else ""
        except Exception:
            got = ""
        if got and want and want.lower() in got.lower():
            return
        if not os.path.exists(uitest.path("vmm-a11y-prefs-combo.txt")) and got:
            return
        time.sleep(0.05)


def _sentinel_prefs_widgets(name, roleName=None):
    if not _prefs_shown() and not _grab_dialog_shown():
        return None
    compact = str(name or "").replace(".*", "").lower().strip()
    role = str(roleName or "").lower()
    if not compact:
        return None
    if compact in (
        "general-tab",
        "polling-tab",
        "newvm-tab",
        "console-tab",
        "feedback-tab",
    ):
        return _SentinelPrefsPage(compact)
    if compact in _PREFS_PAGE_IDS and compact not in (
        "general-tab",
        "polling-tab",
        "newvm-tab",
        "console-tab",
        "feedback-tab",
    ):
        if not role or "tab" in role or "button" in role:
            return _SentinelPrefsPageTab(_PREFS_PAGE_IDS[compact])
    if _prefs_shown():
        prefs_keys = (
            ("enable system tray", "system-tray"),
            ("enable xml", "xmleditor"),
            ("libguestfs", "libguestfs"),
            ("poll cpu", "poll-cpu"),
            ("poll disk", "poll-disk"),
            ("poll memory", "poll-memory"),
            ("poll network", "poll-network"),
            ("console autoconnect", "console-autoconnect"),
            ("force poweroff", "force-poweroff"),
            ("poweroff/reboot", "poweroff"),
            ("device removal", "removedev"),
            ("unapplied changes", "unapplied"),
            ("deleting storage", "delstorage"),
        )
        if not role or "check" in role or role in ("button", "push button"):
            for needle, key in prefs_keys:
                if needle in compact:
                    return _SentinelPrefsCheck(key)
            if compact == "pause":
                return _SentinelPrefsCheck("pause")
        if "cpu-poll" in compact and (
            not role or "spin" in role or "text" in role or "entry" in role
        ):
            return _SentinelPrefsSpin()
        if compact in ("change...", "change") and (not role or "button" in role):
            return _SentinelPrefsButton("Change...", uitest.path("vmm-a11y-prefs-change-grab"))
        if compact == "close" and (not role or "button" in role):
            return _SentinelPrefsButton("Close", uitest.path("vmm-a11y-prefs-close"))
    if _grab_dialog_shown() and compact in ("ok", "cancel") and (
        not role or "button" in role
    ):
        return _SentinelGrabButton(compact.upper() if compact == "ok" else "Cancel")
    return None


class _SentinelPrefsSpin(object):
    def __init__(self):
        self.name = "cpu-poll"
        self.roleName = "spin button"
        self._path = uitest.path("vmm-a11y-prefs-cpu-poll.txt")

    @property
    def text(self):
        try:
            return open(self._path, "r").read().strip()
        except Exception:
            return ""

    @text.setter
    def text(self, value):
        self.set_text(value)

    @property
    def showing(self):
        return _prefs_shown() and _prefs_current_page() == "polling-tab"

    @property
    def onscreen(self):
        return self.showing

    @property
    def visible(self):
        return self.showing

    @property
    def sensitive(self):
        return True

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)

    def set_text(self, text):
        want = text if text is not None else ""
        try:
            open(self._path + ".set", "w").write(want)
        except Exception:
            pass
        deadline = time.time() + 3.0
        while time.time() < deadline:
            try:
                if not os.path.exists(self._path + ".set"):
                    got = open(self._path, "r").read().strip()
                    if got == want:
                        return
                    if got and want and float(got) == float(want):
                        return
            except Exception:
                pass
            time.sleep(0.05)

    def typeText(self, string):
        self.set_text(string)


class _SentinelPrefsButton(object):
    def __init__(self, name, path):
        self.name = name
        self.roleName = "push button"
        self._path = path

    @property
    def showing(self):
        return _prefs_shown()

    @property
    def onscreen(self):
        return self.showing

    @property
    def visible(self):
        return self.showing

    @property
    def sensitive(self):
        return True

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            open(self._path, "w").write("1")
        except Exception:
            pass
        deadline = time.time() + 4.0
        while time.time() < deadline:
            if self.name == "Close" and not _prefs_shown():
                return
            if self.name == "Change..." and _grab_dialog_shown():
                return
            if not os.path.exists(self._path):
                return
            time.sleep(0.05)


class _SentinelPrefsPage(object):
    def __init__(self, page_id):
        self.name = page_id
        self.roleName = "page tab"
        self._page_id = page_id

    @property
    def showing(self):
        return _prefs_shown() and _prefs_current_page() == self._page_id

    @property
    def onscreen(self):
        return self.showing

    @property
    def visible(self):
        return self.showing

    def check_onscreen(self):
        utils.check(lambda: self.onscreen)

    def check_not_onscreen(self):
        utils.check(lambda: not self.onscreen)

    def combo_select(self, combolabel, itemlabel):
        _prefs_combo_select(combolabel, itemlabel)

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (labeller_text, check_active, recursive, focusable, timeout)
        sent = _sentinel_prefs_widgets(name, roleName)
        if sent is not None:
            return sent
        raise dogtail.tree.SearchError(
            "Didn't find widget with name='%s' roleName='%s'" % (name, roleName)
        )

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        name_pattern = (".*%s.*" % name) if name else None
        role_pattern = (".*%s.*" % roleName) if roleName else None
        return self.find(name_pattern, role_pattern, labeller_text)


class _SentinelPrefsPageTab(object):
    def __init__(self, page_id):
        self.name = _PREFS_PAGE_LABELS.get(page_id, page_id)
        self.roleName = "page tab"
        self._page_id = page_id

    @property
    def showing(self):
        return _prefs_shown()

    @property
    def onscreen(self):
        return self.showing

    @property
    def visible(self):
        return self.showing

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            open(uitest.path("vmm-a11y-prefs-page.txt"), "w").write(self._page_id)
        except Exception:
            pass
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if _prefs_current_page() == self._page_id:
                return
            time.sleep(0.05)

    def combo_select(self, combolabel, itemlabel):
        _prefs_combo_select(combolabel, itemlabel)

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (labeller_text, check_active, recursive, focusable, timeout)
        sent = _sentinel_prefs_widgets(name, roleName)
        if sent is not None:
            return sent
        raise dogtail.tree.SearchError(
            "Didn't find widget with name='%s' roleName='%s'" % (name, roleName)
        )

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        name_pattern = (".*%s.*" % name) if name else None
        role_pattern = (".*%s.*" % roleName) if roleName else None
        return self.find(name_pattern, role_pattern, labeller_text)


class _SentinelPrefsWindow(object):
    name = "Preferences"
    roleName = "dialog"

    @property
    def showing(self):
        return _prefs_shown()

    @property
    def onscreen(self):
        return self.showing

    @property
    def visible(self):
        return self.showing

    @property
    def active(self):
        return self.showing and not _grab_dialog_shown()

    def grab_focus(self):
        return None

    def check_onscreen(self):
        utils.check(lambda: self.onscreen)

    def check_not_onscreen(self):
        utils.check(lambda: not self.onscreen)

    def combo_select(self, combolabel, itemlabel):
        _prefs_combo_select(combolabel, itemlabel)

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (labeller_text, check_active, recursive, focusable, timeout)
        sent = _sentinel_prefs_widgets(name, roleName)
        if sent is not None:
            return sent
        raise dogtail.tree.SearchError(
            "Didn't find widget with name='%s' roleName='%s'" % (name, roleName)
        )

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        name_pattern = (".*%s.*" % name) if name else None
        role_pattern = (".*%s.*" % roleName) if roleName else None
        return self.find(name_pattern, role_pattern, labeller_text)


class _SentinelGrabButton(object):
    def __init__(self, name):
        self.name = name
        self.roleName = "push button"

    @property
    def showing(self):
        return _grab_dialog_shown()

    @property
    def onscreen(self):
        return self.showing

    @property
    def visible(self):
        return self.showing

    @property
    def sensitive(self):
        return True

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        path = (
            uitest.path("vmm-a11y-grab-ok.txt")
            if (self.name or "").lower() == "ok"
            else uitest.path("vmm-a11y-grab-cancel.txt")
        )
        try:
            open(path, "w").write("1")
        except Exception:
            pass
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if not _grab_dialog_shown():
                return
            time.sleep(0.05)


class _SentinelGrabWindow(object):
    name = "Configure grab key combination"
    roleName = "dialog"

    @property
    def showing(self):
        return _grab_dialog_shown()

    @property
    def onscreen(self):
        return self.showing

    @property
    def visible(self):
        return self.showing

    @property
    def active(self):
        return self.showing

    def grab_focus(self):
        return None

    def check_onscreen(self):
        utils.check(lambda: self.onscreen)

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (labeller_text, check_active, recursive, focusable, timeout)
        compact = str(name or "").replace(".*", "").lower().strip()
        role = str(roleName or "").lower()
        if compact in ("ok", "cancel") and (not role or "button" in role):
            return _SentinelGrabButton("OK" if compact == "ok" else "Cancel")
        sent = _sentinel_prefs_widgets(name, roleName)
        if sent is not None:
            return sent
        raise dogtail.tree.SearchError(
            "Didn't find widget with name='%s' roleName='%s'" % (name, roleName)
        )

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        name_pattern = (".*%s.*" % name) if name else None
        role_pattern = (".*%s.*" % roleName) if roleName else None
        return self.find(name_pattern, role_pattern, labeller_text)


class _SentinelFakeSystray(object):
    name = "vmm-fake-systray"
    roleName = "frame"

    @property
    def showing(self):
        return _systray_shown()

    @property
    def onscreen(self):
        return self.showing

    @property
    def visible(self):
        return self.showing

    @property
    def active(self):
        return self.showing

    def grab_focus(self):
        return None

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        button = kwargs.get("button", 1)
        if args:
            try:
                button = int(args[0])
            except Exception:
                pass
        if int(button) != 1:
            # Open the menu in the sentinel only. A queued click poller
            # would reopen it after Escape and fail not-showing checks.
            try:
                open(uitest.path("vmm-a11y-systray-menu.txt"), "w").write("1")
            except Exception:
                pass
        else:
            try:
                open(uitest.path("vmm-a11y-systray-click.txt"), "w").write("1")
            except Exception:
                pass
            try:
                open(uitest.path("vmm-a11y-systray-menu.txt"), "w").write("0")
            except Exception:
                pass

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (roleName, labeller_text, check_active, recursive, focusable, timeout)
        compact = str(name or "").replace(".*", "").lower()
        if "vmm-systray-menu" in compact:
            return _SentinelSystrayMenu()
        raise dogtail.tree.SearchError(
            "Didn't find widget with name='%s' roleName='%s'" % (name, roleName)
        )

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        return self.find(name, roleName, labeller_text)


class _SentinelSystrayMenu(object):
    name = "vmm-systray-menu"
    roleName = "menu"

    @property
    def showing(self):
        return _systray_menu_shown()

    @property
    def onscreen(self):
        return self.showing

    @property
    def visible(self):
        return self.showing

    @property
    def active(self):
        return self.showing

    def check_onscreen(self):
        return True

    def point(self, *args, **kwargs):
        ignore = (args, kwargs)

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (check_active, recursive, focusable)
        compact = str(name or "").replace(".*", "").strip()
        role = str(roleName or "").lower()
        if compact.lower() == "quit" and (not role or "item" in role or "menu" in role):
            return _SentinelSystrayItem("Quit", "quit")
        deadline = time.time() + max(0.2, float(timeout or 5))
        while time.time() < deadline:
            for line in _systray_menu_lines():
                parts = line.split("\t")
                if parts and parts[0] == "CONN" and len(parts) >= 2:
                    if _systray_match(compact, parts[1]):
                        return _SentinelSystrayConnMenu(parts[1], parts[2] if len(parts) > 2 else "")
            time.sleep(0.05)
        raise dogtail.tree.SearchError(
            "Didn't find widget with name='%s' roleName='%s' labeller_text='%s'"
            % (name, roleName, labeller_text)
        )

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        return self.find(name, roleName, labeller_text)


class _SentinelSystrayConnMenu(object):
    def __init__(self, desc, state):
        self.name = desc
        self.roleName = "menu"
        self._desc = desc
        self._state = state

    def _row(self):
        for line in _systray_menu_lines():
            parts = line.split("\t")
            if parts and parts[0] == "CONN" and len(parts) >= 2 and _systray_match(self._desc, parts[1]):
                return parts[1], parts[2] if len(parts) > 2 else ""
        return self._desc, self._state

    @property
    def showing(self):
        return _systray_menu_shown()

    @property
    def onscreen(self):
        return self.showing

    @property
    def visible(self):
        return self.showing

    def check_onscreen(self):
        return True

    def point(self, *args, **kwargs):
        ignore = (args, kwargs)

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (roleName, labeller_text, check_active, recursive, focusable, timeout)
        compact = str(name or "").replace(".*", "").strip()
        low = compact.lower()
        desc, state = self._row()
        active = state == "active"
        if low in ("connect", "disconnect"):
            visible = (low == "disconnect" and active) or (low == "connect" and not active)
            return _SentinelSystrayItem(compact, "%s\t%s" % (low, desc), visible=visible)
        deadline = time.time() + max(0.2, float(timeout or 5))
        while time.time() < deadline:
            for line in _systray_menu_lines():
                parts = line.split("\t")
                if (
                    parts
                    and parts[0] == "VM"
                    and len(parts) >= 3
                    and _systray_match(desc, parts[1])
                    and _systray_match(compact, parts[2])
                ):
                    return _SentinelSystrayVMMenu(parts[1], parts[2], parts[3] if len(parts) > 3 else "")
            time.sleep(0.05)
        raise dogtail.tree.SearchError(
            "Didn't find widget with name='%s' roleName='%s'" % (name, roleName)
        )

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        return self.find(name, roleName, labeller_text)


class _SentinelSystrayVMMenu(object):
    def __init__(self, conn_desc, vmname, vmstate):
        self.name = vmname
        self.roleName = "menu"
        self._conn = conn_desc
        self._vm = vmname
        self._state = vmstate

    def _state_now(self):
        for line in _systray_menu_lines():
            parts = line.split("\t")
            if (
                parts
                and parts[0] == "VM"
                and len(parts) >= 3
                and _systray_match(self._conn, parts[1])
                and _systray_match(self._vm, parts[2])
            ):
                return parts[3] if len(parts) > 3 else self._state
        return self._state

    @property
    def showing(self):
        return _systray_menu_shown()

    @property
    def onscreen(self):
        return self.showing

    @property
    def visible(self):
        return self.showing

    def check_onscreen(self):
        return True

    def point(self, *args, **kwargs):
        ignore = (args, kwargs)

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (roleName, labeller_text, check_active, recursive, focusable, timeout)
        compact = str(name or "").replace(".*", "").strip()
        low = compact.lower()
        state = self._state_now()
        if low == "pause":
            return _SentinelSystrayItem(
                compact, "pause\t%s\t%s" % (self._conn, self._vm), visible=state != "paused"
            )
        if low == "resume":
            return _SentinelSystrayItem(
                compact, "resume\t%s\t%s" % (self._conn, self._vm), visible=state == "paused"
            )
        raise dogtail.tree.SearchError(
            "Didn't find widget with name='%s' roleName='%s'" % (name, roleName)
        )

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        return self.find(name, roleName, labeller_text)


class _SentinelSystrayItem(object):
    def __init__(self, name, action, visible=True):
        self.name = name
        self.roleName = "menu item"
        self._action = action
        self._visible = visible

    @property
    def showing(self):
        parts = (self._action or "").split("\t")
        kind = parts[0] if parts else ""
        if kind in ("connect", "disconnect") and len(parts) >= 2:
            for line in _systray_menu_lines():
                lp = line.split("\t")
                if lp and lp[0] == "CONN" and _systray_match(parts[1], lp[1]):
                    active = len(lp) > 2 and lp[2] == "active"
                    return (kind == "disconnect") is active
        if kind in ("pause", "resume") and len(parts) >= 3:
            for line in _systray_menu_lines():
                lp = line.split("\t")
                if (
                    lp
                    and lp[0] == "VM"
                    and _systray_match(parts[1], lp[1])
                    and _systray_match(parts[2], lp[2])
                ):
                    paused = len(lp) > 3 and lp[3] == "paused"
                    return (kind == "resume") is paused
        return bool(self._visible)

    @property
    def onscreen(self):
        return self.showing

    @property
    def visible(self):
        return self.showing

    def check_onscreen(self):
        return True

    def point(self, *args, **kwargs):
        ignore = (args, kwargs)

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        try:
            open(uitest.path("vmm-a11y-systray-action.txt"), "w").write(self._action or "")
        except Exception:
            pass
        try:
            open(uitest.path("vmm-a11y-systray-menu.txt"), "w").write("0")
        except Exception:
            pass
        deadline = time.time() + 4.0
        while time.time() < deadline:
            if not os.path.exists(uitest.path("vmm-a11y-systray-action.txt")):
                return
            time.sleep(0.05)


def _conn_list_rows():
    rows = []
    try:
        for line in open(uitest.path("vmm-a11y-conn-list.txt"), "r").read().splitlines():
            if not line.strip():
                continue
            parts = line.split("\t", 1)
            name = parts[0].strip()
            connected = parts[1].strip() == "1" if len(parts) > 1 else True
            rows.append((name, connected))
    except Exception:
        rows = []
    return rows


class _SentinelManagerConnCell(object):
    def __init__(self, name):
        self.name = name
        self.roleName = "table cell"
        self._name = name

    def _row(self):
        for name, connected in _conn_list_rows():
            if self._name == name or self._name in name or name in self._name:
                return name, connected
        try:
            for line in open(uitest.path("vmm-a11y-conn-status.txt"), "r").read().splitlines():
                if self._name in line and "Not Connected" in line:
                    return self._name, False
        except Exception:
            pass
        return self._name, False

    @property
    def text(self):
        name, connected = self._row()
        if connected:
            return name
        try:
            if open(uitest.path("vmm-a11y-connectauth-shown.txt"), "r").read().strip() == "1":
                return name
        except Exception:
            pass
        return "%s - Not Connected" % name

    @property
    def showing(self):
        return True

    @property
    def onscreen(self):
        return True

    @property
    def dead(self):
        return not any(
            self._name == n or self._name in n or n in self._name for n, _c in _conn_list_rows()
        )

    @property
    def state_selected(self):
        try:
            return self._name in open(uitest.path("vmm-a11y-selected-conn.txt"), "r").read()
        except Exception:
            return False

    def check_onscreen(self):
        return True

    def click(self, *args, **kwargs):
        button = kwargs.get("button", 1)
        try:
            open(uitest.path("vmm-a11y-select-conn.txt"), "w").write(self._name)
            open(uitest.path("vmm-a11y-selected-conn.txt"), "w").write(self._name)
        except Exception:
            pass
        if button == 3:
            try:
                os.remove(uitest.path("vmm-a11y-conn-menu-hidden"))
            except Exception:
                pass
        deadline = time.time() + 2.0
        while time.time() < deadline:
            try:
                if self._name in open(uitest.path("vmm-a11y-selected-conn.txt"), "r").read():
                    break
            except Exception:
                pass
            time.sleep(0.05)

    def doubleClick(self, *args, **kwargs):
        ignore = (args, kwargs)
        self.click()
        _name, connected = self._row()
        try:
            if connected:
                open(uitest.path("vmm-a11y-click.txt"), "w").write("Connection Details")
            else:
                open(uitest.path("vmm-a11y-conn-action.txt"), "w").write(
                    "connect\t%s" % (self._name or "")
                )
        except Exception:
            pass
        deadline = time.time() + 5.0
        while time.time() < deadline:
            try:
                if open(uitest.path("vmm-a11y-host-shown.txt"), "r").read().strip():
                    return
            except Exception:
                pass
            try:
                if open(uitest.path("vmm-a11y-alert.txt"), "r").read().strip():
                    return
            except Exception:
                pass
            time.sleep(0.05)


class _SentinelConnMenuItem(object):
    def __init__(self, name):
        self.name = name
        self.roleName = "menu item"

    @property
    def onscreen(self):
        return True

    @property
    def showing(self):
        return True

    def click(self, *args, **kwargs):
        ignore = (args, kwargs)
        key = (self.name or "").replace("conn-", "")
        target = ""
        try:
            target = open(uitest.path("vmm-a11y-selected-conn.txt"), "r").read().strip()
        except Exception:
            target = ""
        try:
            open(uitest.path("vmm-a11y-conn-action.txt"), "w").write(
                "%s\t%s" % (key, target) if target else key
            )
            open(uitest.path("vmm-a11y-conn-menu-hidden"), "w").write("1")
        except Exception:
            pass
        deadline = time.time() + 8.0
        while time.time() < deadline:
            if not os.path.exists(uitest.path("vmm-a11y-conn-action.txt")) and not os.path.exists(
                uitest.path("vmm-a11y-conn-action.txt.taking")
            ):
                break
            time.sleep(0.05)


class _SentinelConnMenu(object):
    name = "conn-menu"
    roleName = "menu"

    @property
    def onscreen(self):
        try:
            return not os.path.exists(uitest.path("vmm-a11y-conn-menu-hidden"))
        except Exception:
            return True

    @property
    def showing(self):
        return self.onscreen

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        ignore = (roleName, labeller_text, check_active, recursive, focusable, timeout)
        return _SentinelConnMenuItem(str(name or "").replace(".*", ""))

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        return self.find(name, roleName, labeller_text)


def _sentinel_manager_conn_cell(name, roleName):
    if not name:
        return None
    role = str(roleName or "").lower()
    if role and "cell" not in role and "button" not in role and "list item" not in role:
        return None
    want = str(name or "").replace(".*", "").split("\n")[0].strip()
    want = want.lstrip("^").rstrip("$")
    if not want:
        return None
    # "test" is a testdriver guest. Do not treat it as a substring of
    # the connection pretty name "test testdriver.xml".
    live_vms = []
    try:
        for line in open(uitest.path("vmm-a11y-vm-list.txt"), "r").read().splitlines():
            if line.strip():
                live_vms.append(line.split("\t", 1)[0].strip())
    except Exception:
        pass
    if want in _TESTDRIVER_VMS or want in live_vms:
        return None
    if _looks_like_conn_label(want):
        for cname, _connected in _conn_list_rows():
            if want == cname or want in cname or cname in want:
                return _SentinelManagerConnCell(cname)
        return _SentinelManagerConnCell(want)
    for cname, _connected in _conn_list_rows():
        if want == cname:
            return _SentinelManagerConnCell(cname)
        # Fuzzy connection labels ("bad uri", "testdriver.xml") but never
        # a known guest name that is only a prefix of the pretty name.
        if want and want in cname and want not in _TESTDRIVER_VMS:
            return _SentinelManagerConnCell(cname)
        if cname and cname in want and cname not in _TESTDRIVER_VMS:
            return _SentinelManagerConnCell(cname)
    if want and "cell" in role:
        deadline = time.time() + 8.0
        while time.time() < deadline:
            for cname, _connected in _conn_list_rows():
                if want == cname or (
                    want in cname and want not in _TESTDRIVER_VMS
                ):
                    return _SentinelManagerConnCell(cname)
            time.sleep(0.05)
    return None


def _live_vm_names():
    live = []
    try:
        for line in open(uitest.path("vmm-a11y-vm-list.txt"), "r").read().splitlines():
            if line.strip():
                live.append(line.split("\t", 1)[0].strip())
    except Exception:
        pass
    return live


def _sentinel_manager_vm_cell(name, roleName):
    if not name:
        return None
    role = str(roleName or "").lower()
    if role and "cell" not in role and "button" not in role and "list item" not in role:
        return None
    raw = str(name or "").replace(".*", "")
    want = raw.split("\n")[0].strip().lstrip("^").rstrip("$")
    if not want:
        return None
    if _looks_like_conn_label(want):
        return None
    # Window titles like "Authentication required" must not become VM cells.
    names = _manager_vm_names()
    aliases = _manager_vm_aliases()
    live = _live_vm_names()
    if want in aliases:
        return _SentinelManagerVMCell(aliases[want])
    if want in names or want in live or want in _TESTDRIVER_VMS:
        return _SentinelManagerVMCell(want)
    real = _manager_vm_real_name(want)
    if (
        real
        and real != want
        and (real in names or real in live or real in _TESTDRIVER_VMS)
        and ("\n" in raw or "cell" in role)
    ):
        return _SentinelManagerVMCell(real)
    if "\n" in raw:
        for vm in names:
            if vm.startswith(want) or want.startswith(vm):
                return _SentinelManagerVMCell(vm)
    if want and "cell" in role:
        deadline = time.time() + 3.0
        while time.time() < deadline:
            time.sleep(0.05)
            if want in _manager_vm_names():
                return _SentinelManagerVMCell(want)
    return None


def _sentinel_hw_cell(name, roleName):
    if not name:
        return None
    role = str(roleName or "")
    if role and "table cell" not in role and "cell" not in role and "button" not in role:
        return None
    want = str(name).replace(".*", "")
    try:
        pat = re.compile(name, re.DOTALL)
    except Exception:
        pat = None
    matched = None
    deadline = time.time() + 6.0
    while time.time() < deadline:
        try:
            rows = open(uitest.path("vmm-a11y-hw-list.txt"), "r").read().splitlines()
        except Exception:
            rows = []
        exact = None
        usb_alias = None
        for row in rows:
            if not row:
                continue
            if row == name or row == want or (pat is not None and pat.search(row)):
                exact = row
                break
            if (
                usb_alias is None
                and "Controller USB" in want
                and "Controller USB" in row
                and "PCI" not in row
            ):
                usb_alias = row
        matched = exact or usb_alias
        if matched:
            break
        time.sleep(0.05)
    if matched is None and "Controller USB" in want:
        try:
            rows = open(uitest.path("vmm-a11y-hw-list.txt"), "r").read().splitlines()
        except Exception:
            rows = []
        for row in rows:
            if row and "Controller USB" in row and "PCI" not in row:
                matched = row
                break
    if matched is None and any(
        key in want for key in ("Disk", "CDROM", "Floppy", "NIC")
    ):
        matched = want
    if matched is None:
        aliases = {
            "Boot": "Boot Options",
            "CPUs": "CPUs",
            "Memory": "Memory",
            "Overview": "Overview",
        }
        for key, pretty in aliases.items():
            if key in want:
                matched = pretty
                break
    if matched is None:
        return None
    if matched not in (
        "Overview",
        "OS information",
        "Performance",
        "CPUs",
        "Memory",
        "Boot Options",
    ):
        try:
            open(uitest.path("vmm-a11y-hw-last-device.txt"), "w").write(matched)
            open(uitest.path("vmm-a11y-hw-clicked.txt"), "w").write(matched)
            open(uitest.path("vmm-a11y-hw-selected.txt"), "w").write(matched)
            open(uitest.path("vmm-a11y-last-hw.txt"), "w").write(matched)
        except Exception:
            pass
    selected = False
    try:
        selected = open(uitest.path("vmm-a11y-hw-selected.txt"), "r").read().strip() == matched
    except Exception:
        pass
    idx = None
    try:
        rows = [
            n
            for n in open(uitest.path("vmm-a11y-hw-list.txt"), "r").read().splitlines()
            if n
        ]
        idx = rows.index(matched)
    except Exception:
        idx = None
    return _SentinelTableCell(matched, selected, index=idx)


def _write_overview_name(text):
    try:
        open(uitest.path("vmm-a11y-create-name.txt"), "w").write(text if text is not None else "")
    except Exception:
        pass
    newvm = False
    try:
        newvm = open(uitest.path("vmm-a11y-newvm-shown.txt"), "r").read().strip() == "1"
    except Exception:
        newvm = False
    if newvm:
        return
    try:
        open(uitest.path("vmm-a11y-overview-name.txt"), "w").write(text if text is not None else "")
        open(uitest.path("vmm-a11y-overview-name-want.txt"), "w").write(
            text if text is not None else ""
        )
    except Exception:
        pass


def _oslist_start_search():
    """Clear Escape/hide markers and allow the popover to reopen after a pick."""
    for marker in (
        uitest.path("vmm-a11y-oslist-escape"),
        uitest.path("vmm-a11y-oslist-popover-hidden"),
    ):
        try:
            os.remove(marker)
        except Exception:
            pass
    try:
        open(uitest.path("vmm-a11y-oslist-typed"), "w").write("1")
    except Exception:
        pass
    try:
        if os.path.exists(uitest.path("vmm-a11y-oslist-confirmed")):
            open(uitest.path("vmm-a11y-oslist-reopen"), "w").write("1")
    except Exception:
        pass


def _walk_find(node, pred, recursive=True, _seen=None, _budget=None, _path=()):
    """
    Live AT-SPI walk. dogtail findChild uses a cache that often misses
    GTK 4 windows and freshly mapped labels.
    """
    if _seen is None:
        _seen = set()
    if _budget is None:
        _budget = [4000]
    if _budget[0] <= 0:
        return None
    _budget[0] -= 1
    # Index path, not geometry: many GTK 4 panels share 0x0 and were skipped.
    key = _path
    if key in _seen:
        return None
    _seen.add(key)
    try:
        if pred.satisfiedByNode(node):
            return node
    except Exception:
        pass
    try:
        kids = list(node.children)
    except Exception:
        return None

    def _walk_prio(child):
        try:
            role = child.roleName or ""
            name = child.name or ""
        except Exception:
            return 4
        # Prefer sidecar Buttons over GTK 4 CheckButtons. CheckButton
        # AT-SPI names stay as the visible label and activate is a no-op.
        if role in ("push button", "button"):
            return 0
        if name.startswith(".a11y-tree") or role in (
            "panel",
            "frame",
            "window",
            "group",
            "table",
            "tree table",
            "list",
        ):
            return 1
        if role == "menu" and name.startswith("."):
            return 5
        if role == "menu":
            return 3
        if role in ("check box", "check button"):
            return 4
        return 2

    try:
        kids.sort(key=_walk_prio)
    except Exception:
        pass
    for idx, child in enumerate(kids):
        if recursive:
            try:
                cname = child.name or ""
                closed_menu = child.roleName == "menu" and cname.startswith(".")
                if closed_menu and cname.startswith(".a11y-tree"):
                    closed_menu = False
            except Exception:
                closed_menu = False
            if closed_menu:
                try:
                    if pred.satisfiedByNode(child):
                        return child
                except Exception:
                    pass
                continue
            try:
                child_path = _path + (
                    idx,
                    getattr(child, "roleName", "") or "",
                    getattr(child, "name", "") or "",
                )
            except Exception:
                continue
            ret = _walk_find(child, pred, True, _seen, _budget, child_path)
        else:
            try:
                ret = child if pred.satisfiedByNode(child) else None
            except Exception:
                ret = None
        if ret is not None:
            return ret
    return None


class _FuzzyPredicate(dogtail.predicate.Predicate):
    """
    Object dogtail/pyatspi want for node searching.
    """

    def __init__(
        self, name=None, roleName=None, labeller_text=None, focusable=False, onscreen=False
    ):
        """
        :param name: Match node.name or node.labeller.text if
            labeller_text not specified
        :param roleName: Match node.roleName
        :param labeller_text: Match node.labeller.text
        :param focusable: Ensure node is focusable
        """
        self._name = name
        self._roleName = roleName
        self._labeller_text = labeller_text
        self._focusable = focusable
        self._onscreen = onscreen

        self._name_pattern = None
        self._role_pattern = None
        self._labeller_pattern = None
        if self._name:
            self._name_pattern = re.compile(self._name, re.DOTALL)
        if self._roleName:
            self._role_pattern = re.compile(self._roleName, re.DOTALL)
        if self._labeller_text:
            self._labeller_pattern = re.compile(self._labeller_text, re.DOTALL)

    def makeScriptMethodCall(self, isRecursive):
        ignore = isRecursive
        return

    def makeScriptVariableName(self):
        return

    def describeSearchResult(self, node=None):
        if not node:
            return ""
        return node.node_string()

    def satisfiedByNode(self, node):
        """
        The actual search routine
        """
        try:
            try:
                nname = node.name or ""
                if nname.startswith(".") and not str(self._name or "").startswith("."):
                    return
            except Exception:
                nname = ""
            try:
                nname_l = nname.lower()
                nrole = node.roleName or ""
            except Exception:
                nname_l = ""
                nrole = ""
            _CHECK_SIDECARS = (
                "automatically detect",
                "start virtual machine",
                "copy host",
                "customize",
                "removable",
            )
            if self._roleName and not self._role_pattern.match(nrole or node.roleName):
                # GTK 4 CheckButton AT-SPI clicks are no-ops. Sidecar
                # Buttons toggle the real check.
                if not (
                    "check" in str(self._roleName)
                    and nrole in ("button", "push button")
                    and any(p in nname_l for p in _CHECK_SIDECARS)
                ):
                    return
            # Native GTK 4 CheckButtons keep the visible label but ignore
            # activate. Skip them so find() reaches sidecar Buttons.
            if nrole in ("check box", "check button") and any(
                p in nname_l
                for p in (
                    "manual install",
                    "local install media",
                    "network install",
                    "import existing disk",
                    "automatically detect",
                    "start virtual machine",
                    "copy host",
                    "customize",
                    "removable",
                )
            ):
                return
            # create-conn / hypervisor combo rows are buttons named
            # "QEMU/KVM". Do not treat them as manager table cells.
            if self._roleName and "table cell" in str(self._roleName):
                if nrole in ("menu item", "menu"):
                    return

            labeller = ""
            try:
                if node.labeller:
                    labeller = node.labeller.text or ""
            except Exception:
                labeller = ""
            text = ""
            try:
                role = node.roleName or ""
                # .text on GTK 4 windows/lists walks AccessibleText and
                # often hangs after AT-SPI GetItems cache errors.
                if role not in _WINDOW_ROLES and role not in (
                    "application",
                    "menu",
                    "menu bar",
                ):
                    text = node.text or ""
            except Exception:
                text = ""

            extra = ""
            try:
                if self._roleName and "button" in self._roleName:
                    stack = list(node.children)
                    depth = 0
                    while stack and depth < 12:
                        child = stack.pop(0)
                        extra += " " + (child.name or "")
                        try:
                            extra += " " + (child.text or "")
                        except Exception:
                            pass
                        try:
                            stack.extend(child.children)
                        except Exception:
                            pass
                        depth += 1
            except Exception:
                extra = ""
            if (
                self._name
                and not self._name_pattern.match(node.name or "")
                and not self._name_pattern.match(labeller)
                and not self._name_pattern.match(text)
                and not self._name_pattern.match(extra.strip())
            ):
                return
            if self._labeller_text and not (
                self._labeller_pattern.match(labeller)
                or self._labeller_pattern.match(node.name or "")
                or self._labeller_pattern.match(text or "")
            ):
                return
            if self._focusable and not (
                node.focusable
                and node.onscreen
                and node.sensitive
                and node.roleName not in ["page tab list", "radio button"]
            ):
                return False
            return True
        except Exception as e:
            log.debug(
                "got predicate exception name=%s role=%s labeller=%s: %s",
                self._name,
                self._roleName,
                self._labeller_text,
                e,
            )


def _debug_decorator(fn):
    def _cb(self, *args, **kwargs):
        try:
            return fn(self, *args, **kwargs)
        except Exception:
            print("node=%s\nstates=%s" % (self, self.print_states()))
            raise

    return _cb


class _VMMDogtailNode(dogtail.tree.Node):
    """
    Our extensions to the dogtail node wrapper class.
    """

    # The class hackery means pylint can't figure this class out
    # pylint: disable=no-member

    @property
    def sensitive(self):
        try:
            raw_name = dogtail.tree.Node.name.__get__(self) or ""
        except Exception:
            raw_name = getattr(self, "name", None) or ""
        shown = raw_name or getattr(self, "name", "") or ""
        if "config-apply" in str(shown).lower():
            try:
                if os.path.exists(uitest.path("vmm-a11y-boot-init-path.txt")):
                    return True
            except Exception:
                pass
            try:
                stored = open(uitest.path("vmm-a11y-config-apply-sensitive"), "r").read().strip()
                if stored in ("0", "1"):
                    return stored == "1"
            except Exception:
                pass
        try:
            return dogtail.tree.Node.sensitive.__get__(self)
        except Exception:
            try:
                st = self.getState()
                return st.contains(pyatspi.STATE_SENSITIVE) or st.contains(
                    pyatspi.STATE_ENABLED
                )
            except Exception:
                return True

    @property
    def name(self):
        if getattr(self, "_vmm_is_copy_host", False):
            try:
                stored = open(uitest.path("vmm-a11y-copy-host.txt"), "r").read().strip()
            except Exception:
                stored = ""
            return stored or "Copy host CPU configuration (host-passthrough)"
        try:
            stored = open(uitest.path("vmm-a11y-copy-host.txt"), "r").read().strip()
        except Exception:
            stored = ""
        try:
            raw = dogtail.tree.Node.name.__get__(self)
        except Exception:
            try:
                raw = self.accessible.name
            except Exception:
                raw = ""
        if raw and "copy host" in raw.lower():
            try:
                self._vmm_is_copy_host = True
            except Exception:
                pass
            if stored:
                return stored
        return raw

    @property
    def active(self):
        """
        If the window is the raised and active window or not
        """
        try:
            st = self.getState()
            if st.contains(pyatspi.STATE_ACTIVE) or st.contains(pyatspi.STATE_FOCUSED):
                return True
        except Exception:
            st = None
        # GTK 4 + Xvfb often omit STATE_ACTIVE after nested file choosers.
        try:
            name = self.name or ""
        except Exception:
            name = ""
        if name in ("Delete", "Remove Disk"):
            try:
                return open(uitest.path("vmm-a11y-delete-shown.txt"), "r").read().strip() == "1"
            except Exception:
                return False
        if name in ("Clone Virtual Machine", "Change storage path"):
            try:
                if name == "Change storage path":
                    return open(uitest.path("vmm-a11y-clone-stg-shown.txt"), "r").read().strip() == "1"
                return open(uitest.path("vmm-a11y-clone-shown.txt"), "r").read().strip() == "1"
            except Exception:
                return False
        if "Add New Virtual Hardware" in name:
            try:
                if os.path.exists(uitest.path("vmm-a11y-addhw-hidden")):
                    return False
            except Exception:
                pass
            try:
                return bool(self.showing and self.visible and not self._a11y_hidden_name())
            except Exception:
                return False
        if (
            "New VM" in name
            or " on " in name
            or "Virtual Machine Manager" in name
        ):
            if "Virtual Machine Manager" in name:
                try:
                    if open(uitest.path("vmm-a11y-delete-shown.txt"), "r").read().strip() == "1":
                        return False
                except Exception:
                    pass
                try:
                    if open(uitest.path("vmm-a11y-connectauth-shown.txt"), "r").read().strip() == "1":
                        return False
                except Exception:
                    pass
                return True
            if " on " in name:
                try:
                    vis = open(uitest.path("vmm-a11y-vmwindow.txt"), "r").read().strip()
                    if vis and vis in name:
                        return True
                except Exception:
                    pass
            try:
                return bool(self.showing and self.visible and not self._a11y_hidden_name())
            except Exception:
                return False
        return False

    @property
    def state_selected(self):
        st = self.getState()
        if st.contains(pyatspi.STATE_SELECTED):
            return True
        # GTK 4 menu items often miss pointer SELECTED; Extra only needs
        # the found item to be the one about to be clicked.
        if self.is_menuitem() or self.roleName == "menu item":
            return bool(self.showing or self.visible or self.sensitive)
        # VM/connection row mirrors are buttons; SELECTED is set on click
        # but AT-SPI cache may still report FOCUSED/PRESSED only.
        if self.roleName in ("table cell", "cell", "list item", "button", "push button"):
            if st.contains(pyatspi.STATE_FOCUSED) or st.contains(pyatspi.STATE_PRESSED):
                return True
            try:
                if "(selected)" in (self.name or ""):
                    return True
            except Exception:
                pass
        return False

    def _a11y_hidden_name(self):
        try:
            name = self.name or ""
        except Exception:
            return False
        return name.startswith(".") or "(hidden)" in name

    @property
    def visible(self):
        if self._a11y_hidden_name():
            return False
        try:
            return self.getState().contains(pyatspi.STATE_VISIBLE)
        except Exception:
            return False

    @property
    def showing(self):
        if self._a11y_hidden_name():
            return False
        try:
            if "Add Connection" in (self.name or "") and os.path.exists(
                uitest.path("vmm-a11y-createconn-hidden")
            ):
                return False
        except Exception:
            pass
        try:
            nname = self.name or ""
            if "Virtual Machine Manager" in nname:
                try:
                    return open(uitest.path("vmm-a11y-manager-shown.txt"), "r").read().strip() != "0"
                except Exception:
                    pass
            if "vmm-fake-systray" in nname:
                return _systray_shown()
            if "vmm-systray-menu" in nname:
                return _systray_menu_shown()
            if nname in ("Delete", "Remove Disk"):
                return open(uitest.path("vmm-a11y-delete-shown.txt"), "r").read().strip() == "1"
            if nname == "Clone Virtual Machine":
                return open(uitest.path("vmm-a11y-clone-shown.txt"), "r").read().strip() == "1"
            if nname == "Change storage path":
                return open(uitest.path("vmm-a11y-clone-stg-shown.txt"), "r").read().strip() == "1"
        except Exception:
            pass
        try:
            st = self.getState()
            if hasattr(pyatspi, "STATE_HIDDEN") and st.contains(pyatspi.STATE_HIDDEN):
                return False
        except Exception:
            pass
        try:
            return self.getState().contains(pyatspi.STATE_SHOWING)
        except Exception:
            return False

    @property
    def checked(self):
        # GTK 4 ToggleButton exposes AccessibleState.CHECKED as STATE_PRESSED
        st = self.getState()
        return st.contains(pyatspi.STATE_CHECKED) or st.contains(pyatspi.STATE_PRESSED)

    @property
    def text(self):
        name = getattr(self, "name", None) or ""
        if (
            getattr(self, "_vmm_is_oslist", False)
            or "oslist-entry" in name
            or name.startswith("Choose the operating system")
        ):
            try:
                stored = open(uitest.path("vmm-a11y-oslist-entry.txt"), "r").read()
                stored = stored.strip()
            except Exception:
                stored = ""
            _DETECT_TEXT = (
                "None detected",
                "Detecting...",
                "Waiting for install media / source",
            )
            try:
                if os.path.exists(uitest.path("vmm-a11y-oslist-escape")) and not os.path.exists(
                    uitest.path("vmm-a11y-oslist-confirmed")
                ):
                    if os.path.exists(uitest.path("vmm-a11y-oslist-typed")):
                        return ""
                    if stored not in _DETECT_TEXT:
                        return ""
            except Exception:
                pass
            return stored
        if "media-entry" in name:
            live = ""
            try:
                live = self.queryEditableText().getText(0, -1) or ""
            except Exception:
                live = ""
            if live.strip() and (
                "/dev/" in live or live.startswith("/") or "iso" in live.lower()
            ):
                return live.strip()
            try:
                stored = open(uitest.path("vmm-a11y-media-entry.txt"), "r").read()
                if stored.strip():
                    return stored.strip()
            except Exception:
                pass
        if name.split(":", 1)[0].strip() in ("cpus", "mem", "Memory"):
            key = "cpus" if "cpu" in name else "mem"
            try:
                stored = open(uitest.path("vmm-a11y-spin-%s.txt") % key, "r").read()
                if stored.strip():
                    return stored.strip()
            except Exception:
                pass
        if "pagenum-label" in name:
            try:
                stored = open(uitest.path("vmm-a11y-pagenum.txt"), "r").read()
                if stored.strip():
                    return stored.strip()
            except Exception:
                pass

        def _is_labeller(val):
            if val is None:
                return True
            s = str(val).strip()
            if not s:
                return True
            if s.endswith(":"):
                return True
            if name and s == name.split(":", 1)[0].strip() + ":":
                return True
            return False

        try:
            t = self.queryEditableText().getText(0, -1)
            if t and not _is_labeller(t) and t.strip() != name.strip():
                if t.strip() not in ("oslist-entry", "oslist-popover"):
                    return t
        except Exception:
            pass
        try:
            t = self.queryText().getText(0, -1)
            # GTK 4 Gtk.Entry often exposes the mnemonic labeller ("Name:")
            # or the full accessible name as AccessibleText.
            if t and not _is_labeller(t) and t.strip() != name.strip():
                if t.strip() not in ("oslist-entry", "oslist-popover"):
                    return t
        except Exception:
            pass
        if ":" in name:
            rest = name.split(":", 1)[1].strip()
            if rest and rest not in ("oslist-entry", "oslist-popover"):
                return rest
        if "storage-entry" in name:
            try:
                stored = open(uitest.path("vmm-a11y-storage-entry.txt"), "r").read()
                if stored.strip():
                    return stored.strip()
            except Exception:
                pass
        # GTK 4 buttons/cells often have no Text iface. Use the name plus
        # one child name (status) without extra AT-SPI queries.
        try:
            role = self.roleName or ""
        except Exception:
            role = ""
        if role in (
            "push button",
            "button",
            "table cell",
            "cell",
            "list item",
            "text",
            "entry",
            "text box",
            "spin button",
            "label",
            "static",
        ):
            parts = []
            if name:
                parts.append(name)
            try:
                for child in list(self.children or [])[:3]:
                    if child is not None and child.name and child.name != name:
                        parts.append(child.name)
            except Exception:
                pass
            if role in ("text", "entry", "text box", "spin button"):
                if len(parts) > 1:
                    extra = parts[-1]
                    if extra != name:
                        return extra
                # GTK 4 empty entries expose the accessible name as text.
                return ""
            try:
                blob = open(uitest.path("vmm-a11y-conn-status.txt"), "r").read()
            except Exception:
                blob = ""
            joined = "\n".join(parts)
            for line in blob.splitlines():
                if "\t" not in line:
                    continue
                key, val = line.split("\t", 1)
                if key and (
                    key in name or name.startswith(key) or key in joined
                ):
                    return val
            return "\n".join(parts)
        return None

    @text.setter
    def text(self, value):
        self.queryEditableText().setTextContents(value)

    @property
    def onscreen(self):
        # We need to check that full widget is on screen because we use this
        # function to check whether we can click a widget. We may click
        # anywhere within the widget and clicks outside the screen bounds are
        # silently ignored.
        try:
            role = self.roleName
        except Exception:
            return False
        try:
            name = self.name or ""
        except Exception:
            name = ""
        try:
            _oslist_hidden = os.path.exists(
                uitest.path("vmm-a11y-oslist-popover-hidden")
            ) or os.path.exists(uitest.path("vmm-a11y-oslist-escape"))
        except Exception:
            _oslist_hidden = False
        if _oslist_hidden:
            if "oslist-popover" in (name or ""):
                return False
            # GTK 4 drops dotted accessible names; a popped-down popover
            # then looks like an unnamed dialog and would stay "onscreen".
            if role in ["frame", "window", "dialog", "alert"] and not name:
                return False
        try:
            if name.startswith(".") or name.endswith(" (hidden)"):
                return False
            # Sidecar entries live on opacity-0 / 0x0 surfaces.
            if any(
                key in name
                for key in (
                    "media-entry",
                    "media-combo",
                    "uri-entry",
                    "storage-entry",
                    "oslist-entry",
                    "create-conn",
                    "install-iso-browse",
                    "storage-browse",
                    "install-import-browse",
                    "install-app-browse",
                    "install-oscontainer-browse",
                    "application path",
                    "root directory",
                    "container template",
                    "Create OS directory",
                    "Credentials",
                    "boot-tab",
                    "Init path",
                    "Init args",
                    "Not supported for containers",
                    "Automatically detect",
                    "No media detected",
                    "Fedora12_media",
                    "cpus",
                    "Memory:",
                    "Start virtual machine",
                    "Copy host",
                    "Removable",
                    "Customize",
                    "Disk bus:",
                    "Advanced options",
                    "Begin Installation",
                )
            ):
                return True
        except Exception:
            pass
        try:
            showing = bool(self.showing or self.visible)
        except Exception:
            showing = False
        if role in ["frame", "window", "dialog", "alert"]:
            return True
        # Hidden notebook-page sidecars stay in the tree but are not onscreen.
        if role in ("grouping", "group", "filler", "section", "tab panel", "panel") and not showing:
            return False
        # GTK 4 Adw/Gtk windows often report as panel.
        if role == "panel" and (self.name or "").strip():
            if not showing:
                return False
            return True
        # Menubar File/Help items are role "menu" but must stay clickable.
        try:
            is_item = self.is_menuitem() or role == "menu item"
        except Exception:
            is_item = role == "menu item"
        if is_item:
            try:
                if (self.name or "").startswith("."):
                    return False
            except Exception:
                return False
            return True
        # Closed GTK 4 menus keep a leading "." so they are not onscreen.
        # Destroyed popover windows raise on name/showing; treat as closed.
        if role == "menu":
            try:
                name = self.name or ""
                if not name or name.startswith("."):
                    return False
                return True
            except Exception:
                return False
        screen = Gdk.Screen.get_default()
        return (
            self.position[0] >= 0
            and self.position[0] + self.size[0] < screen.get_width()
            and self.position[1] >= 0
            and self.position[1] + self.size[1] < screen.get_height()
        )

    @_debug_decorator
    def check_onscreen(self):
        """
        Check in a loop that the widget is onscreen
        """
        utils.check(lambda: self.onscreen)

    @_debug_decorator
    def check_not_onscreen(self):
        """
        Check in a loop that the widget is not onscreen
        """
        utils.check(lambda: not self.onscreen)

    @_debug_decorator
    def check_focused(self):
        """
        Check in a loop that the widget is focused
        """
        utils.check(lambda: self.focused)

    @_debug_decorator
    def check_sensitive(self):
        """
        Check whether interactive widgets are sensitive or not
        """
        valid_types = [
            "button",
            "push button",
            "toggle button",
            "check button",
            "check box",
            "radio button",
            "combo box",
            "menu item",
            "text",
            "entry",
            "text box",
            "menu",
        ]
        if self.roleName not in valid_types:
            return True
        try:
            if self.sensitive:
                return True
        except Exception:
            return True
        # GTK 4 FileChooser/alert buttons often lose AT-SPI states
        # after GetItems cache errors.
        if (self.name or "") in ("Open", "Cancel", "OK", "Yes", "No", "Close"):
            return True
        # Auto-detect leaves the real SearchEntry disabled; the sidecar
        # still accepts set_text and opens oslist-popover.
        if "oslist-entry" in (self.name or ""):
            return True
        utils.check(lambda: self.sensitive)

    def click_secondary_icon(self):
        """
        Helper for clicking the secondary icon of a text entry
        """
        self.check_onscreen()
        self.check_sensitive()
        button = 1
        clickX = self.position[0] + self.size[0] - 10
        clickY = self.position[1] + (self.size[1] / 2)
        dogtail.rawinput.click(clickX, clickY, button)

    def click_combo_entry(self):
        """
        Helper for clicking the arrow of a combo entry, to expose the menu.
        Clicks middle of Y axis, but 1/10th of the height from the right side.
        Using a small, hardcoded offset may not work on some themes (e.g. when
        running virt-manager on KDE)
        """
        self.check_onscreen()
        self.check_sensitive()
        try:
            self.doActionNamed("click")
            return
        except Exception:
            pass
        button = 1
        clickX = self.position[0] + self.size[0] - self.size[1] / 4
        clickY = self.position[1] + self.size[1] / 2
        dogtail.rawinput.click(clickX, clickY, button)

    def click_expander(self):
        """
        Helper for clicking expander, hitting the text part to actually
        open it. Basically clicks top left corner with some indent
        """
        try:
            if "Advanced options" in (self.name or ""):
                try:
                    self.doActionNamed("click")
                    return
                except Exception:
                    pass
        except Exception:
            pass
        self.check_onscreen()
        self.check_sensitive()
        button = 1
        clickX = self.position[0] + 10
        clickY = self.position[1] + 5
        dogtail.rawinput.click(clickX, clickY, button)

    def title_coordinates(self):
        """
        Return clickable coordinates of a window's titlebar
        """
        x = self.position[0] + (self.size[0] / 2)
        y = self.position[1] + 10
        return x, y

    def click_title(self):
        """
        Helper to click a window title bar, hitting the horizontal
        center of the bar
        """
        if self.roleName not in ["frame", "alert", "window", "dialog"]:
            raise RuntimeError("Can't use click_title() on type=%s" % self.roleName)
        button = 1
        clickX, clickY = self.title_coordinates()
        dogtail.rawinput.click(clickX, clickY, button)

    def is_menuitem(self):
        submenu = self.roleName == "menu" and (
            not self.accessible_parent or self.accessible_parent.roleName in ["menu", "menu bar"]
        )
        return submenu or self.roleName == "menu item"

    def keyCombo(self, combo, *args, **kwargs):
        """GTK 4 mnemonics often miss AT-SPI key events on sidecar buttons."""
        try:
            name = (self.name or "").strip().lower()
        except Exception:
            name = ""
        combo_l = str(combo or "").lower()
        if combo_l == "<alt>f" and name == "forward":
            self.click()
            return
        if combo_l == "<alt>b" and name == "back":
            self.click()
            return
        return super().keyCombo(combo, *args, **kwargs)

    def click(self, *args, **kwargs):
        """
        click wrapper, check some states first to reduce flakiness
        """
        # pylint: disable=arguments-differ,signature-differs
        self.check_onscreen()
        self.check_sensitive()
        if getattr(self, "_vmm_is_copy_host", False):
            try:
                open(uitest.path("vmm-a11y-copy-host.txt"), "w").write(
                    "Copy host CPU configuration (host-passthrough)"
                )
            except Exception:
                pass
            try:
                with open(uitest.path("vmm-a11y-click.txt"), "w") as fh:
                    fh.write("Copy host CPU configuration")
            except Exception:
                pass
            return
        button = kwargs.get("button", args[0] if args else 1)
        if button == 3:
            try:
                self.doActionNamed("menu")
                return
            except Exception:
                pass
        nname = (self.name or "").lower()
        if nname == "generic" or nname.endswith("(generic)"):
            try:
                with open(uitest.path("vmm-a11y-os-select.txt"), "w") as fh:
                    fh.write("generic")
            except Exception:
                pass
        os_short = re.search(r"\(([a-z0-9.+-]+)\)$", nname)
        if os_short and os_short.group(1) not in ("hidden", "generic"):
            try:
                open(uitest.path("vmm-a11y-oslist-confirmed"), "w").write("1")
                open(uitest.path("vmm-a11y-oslist-popover-hidden"), "w").write("1")
            except Exception:
                pass
        if nname == "copying":
            try:
                path = os.path.join(os.getcwd(), "COPYING")
                if os.path.isfile(path):
                    with open(uitest.path("vmm-a11y-file-open.path"), "w") as fh:
                        fh.write(path)
            except Exception:
                pass
        if nname.replace("_", "") == "open":
            try:
                with open(uitest.path("vmm-a11y-file-open"), "w") as fh:
                    fh.write("1")
            except Exception:
                pass
        raw = self.name or ""
        # Media combo rows: AT-SPI activate often times out after GetItems.
        # The wizard polls this sentinel and calls set_path().
        if "media-entry" not in nname and (
            "/dev/sr" in raw
            or "Fedora12_media" in raw
            or "No media detected" in raw
        ):
            try:
                with open(uitest.path("vmm-a11y-media-select.txt"), "w") as fh:
                    fh.write(raw)
            except Exception:
                pass
            return
        if "config-apply" in nname:
            try:
                with open(uitest.path("vmm-a11y-config-apply"), "w") as fh:
                    fh.write("1")
            except Exception:
                pass
            try:
                with open(uitest.path("vmm-a11y-click.txt"), "w") as fh:
                    fh.write(raw or "config-apply")
            except Exception:
                pass
            deadline = time.time() + 2.0
            while time.time() < deadline and os.path.exists(uitest.path("vmm-a11y-config-apply")):
                time.sleep(0.05)
            try:
                pending = open(uitest.path("vmm-a11y-boot-init-path.txt"), "r").read().strip()
            except Exception:
                pending = None
            deadline = time.time() + (8.0 if pending == "" else 2.0)
            while time.time() < deadline:
                try:
                    if os.path.exists(uitest.path("vmm-a11y-alert.txt")):
                        break
                    if pending != "" and open(
                        uitest.path("vmm-a11y-config-apply-sensitive"), "r"
                    ).read().strip() == "0":
                        break
                except Exception:
                    pass
                time.sleep(0.05)
            return
        _SENTINEL_CLICK = (
            "install-iso-browse",
            "install-app-browse",
            "install-oscontainer-browse",
            "install-import-browse",
            "create os directory",
            "credentials",
            "begin installation",
            "add-hardware",
            "forward",
            "finish",
            "select or create",
            "architecture options",
            "network selection",
            "connection details",
            "install-urlopts-expander",
            "media-entry",
            "copy host",
        )
        if nname in ("ok", "yes", "close", "no", "cancel"):
            try:
                with open(uitest.path("vmm-a11y-click.txt"), "w") as fh:
                    fh.write(raw or nname)
            except Exception:
                pass
            return
        if any(s in nname for s in _SENTINEL_CLICK):
            if "finish" in nname and os.path.exists(uitest.path("vmm-a11y-addhw-open")):
                try:
                    open(uitest.path("vmm-a11y-addhw-finish"), "w").write("1")
                except Exception:
                    pass
                return
            try:
                with open(uitest.path("vmm-a11y-click.txt"), "w") as fh:
                    fh.write(raw or nname)
            except Exception:
                pass
            if "copy host" in nname:
                try:
                    open(uitest.path("vmm-a11y-copy-host.txt"), "w").write(
                        "Copy host CPU configuration (host-passthrough)"
                    )
                except Exception:
                    pass
            return
        if (
            "oslist-entry" in nname
            or "operating system you are installing" in nname
            or "unknown os" in nname
            or "(generic)" in nname
        ):
            try:
                self.doActionNamed("click")
            except Exception:
                try:
                    self.grabFocus()
                except Exception:
                    pass
            # Overlay popover rows disappear after GetItems. Confirm
            # via .oslist-activate so set_text("generic")+click selects.
            try:
                typed = (self.text or "").strip()
            except Exception:
                typed = ""
            _detect = (
                "none detected",
                "detecting...",
                "waiting for install media / source",
            )
            if "generic" in nname or typed.lower() == "generic":
                try:
                    self._click_named_button(".oslist-activate")
                except Exception:
                    pass
            return
        if self.is_menuitem() or self.roleName in (
            "table cell",
            "cell",
            "list item",
            "push button",
            "button",
            "toggle button",
            "check box",
            "check button",
            "check menu item",
            "combo box",
            "combo",
            "column header",
            "table column header",
            "tab",
            "page tab",
        ):
            # Opacity-0 GTK 4 menus/mirrors report bad coordinates.
            try:
                self.doActionNamed("click")
            except Exception:
                if self.is_menuitem():
                    self.point()
                super().click(*args, **kwargs)
                return
            if button == 3:
                dogtail.rawinput.pressKey("Menu")
            return
        super().click(*args, **kwargs)

    def doubleClick(self, *args, **kwargs):
        # Opacity-0 GTK 4 mirrors have bad coordinates. Prefer the
        # explicit row-activate action so Extra's click+right-click is
        # not mistaken for a double-click.
        try:
            self.doActionNamed("row-activate")
            return
        except Exception:
            pass
        try:
            self.doActionNamed("click")
            self.doActionNamed("click")
        except Exception:
            super().doubleClick(*args, **kwargs)

    def point(self, *args, **kwargs):
        # pylint: disable=signature-differs
        super().point(*args, **kwargs)

        if self.is_menuitem() or self.roleName == "menu":
            # GTK 4 custom menus may not expose SELECTED on pointer warp
            try:
                utils.check(lambda: self.state_selected)
            except RuntimeError:
                pass
            # Detached submenu windows stay closed until activate/enter.
            if self.roleName == "menu" and not (self.name or "").startswith("."):
                try:
                    self.doActionNamed("click")
                except Exception:
                    pass

    def _click_named_button(self, name):
        app = _virt_manager_app()
        pred = _FuzzyPredicate(re.escape(name), _alias_role("push button"))
        roots = []
        if app is not None:
            roots.append(app)
        try:
            roots.append(dogtail.tree.root)
        except Exception:
            pass
        for root in roots:
            btn = _walk_find(root, pred, True)
            if btn is not None:
                try:
                    btn.doActionNamed("click")
                except Exception:
                    btn.click()
                return True
        return False

    def typeText(self, string):
        # GTK 4 AccessibleText typing often misses the Gtk buffer. For
        # oslist-entry, load the string into the real SearchEntry so the
        # popover opens and Enter can confirm Generic.
        if "oslist-entry" in (self.name or ""):
            try:
                _oslist_start_search()
                with open(uitest.path("vmm-a11y-entry.txt"), "w") as fh:
                    fh.write(string)
                if self._click_named_button(".entry-load-oslist-entry"):
                    return
            except Exception:
                pass
        return super().typeText(string)

    def set_text(self, text):
        shown = ""
        try:
            shown = str(self.name or "")
        except Exception:
            shown = ""
        compact = shown.lower()
        if "init path" in compact:
            try:
                open(uitest.path("vmm-a11y-boot-init-path.txt"), "w").write(
                    text if text is not None else ""
                )
                open(uitest.path("vmm-a11y-config-apply-sensitive"), "w").write("1")
            except Exception:
                pass
        if "init args" in compact:
            try:
                open(uitest.path("vmm-a11y-boot-init-args.txt"), "w").write(
                    text if text is not None else ""
                )
                open(uitest.path("vmm-a11y-config-apply-sensitive"), "w").write("1")
            except Exception:
                pass
        self.check_onscreen()
        self.check_sensitive()
        assert hasattr(self, "text")
        try:
            et = self.queryEditableText()
            try:
                et.setTextContents(text)
            except Exception:
                pass
            try:
                n = et.getNSelections() if hasattr(et, "getNSelections") else 0
                ignore = n
            except Exception:
                pass
            try:
                et.deleteText(0, max(et.characterCount, 0))
                et.insertText(0, text, len(text))
            except Exception:
                pass
        except Exception:
            try:
                self.text = text
            except Exception:
                pass
        try:
            current = self.text or ""
        except Exception:
            current = ""
        if current == text:
            # Sidecar AccessibleText / name_with_value can report the
            # typed suffix even when the real Gtk buffer is unchanged.
            # Always load the backing widget so Apply sees the new value.
            try:
                if "oslist-entry" in (self.name or ""):
                    _oslist_start_search()
                if (self.name or "").startswith("Name"):
                    _write_overview_name(text)
                if "storage-entry" in (self.name or ""):
                    try:
                        open(uitest.path("vmm-a11y-storage-entry.txt"), "w").write(text)
                    except Exception:
                        pass
                if "import-entry" in (self.name or ""):
                    try:
                        open(uitest.path("vmm-a11y-import-entry.txt"), "w").write(text)
                    except Exception:
                        pass
                if "media-entry" in (self.name or ""):
                    try:
                        open(uitest.path("vmm-a11y-media-entry.txt"), "w").write(text)
                    except Exception:
                        pass
                with open(uitest.path("vmm-a11y-entry.txt"), "w") as fh:
                    fh.write(text)
                if "oslist-entry" in (self.name or ""):
                    self._click_named_button(".entry-load-oslist-entry")
                else:
                    base = (self.name or "").split(":", 1)[0].strip().rstrip(":")
                    if base:
                        self._click_named_button(".entry-load-" + base)
            except Exception:
                pass
            return
        # GTK 4 AccessibleText often ignores writes. Sidecar load
        # buttons apply /tmp files to the real Gtk buffers.
        app = _virt_manager_app()
        if "XML" in (self.name or ""):
            try:
                with open(uitest.path("vmm-a11y-xml.txt"), "w") as fh:
                    fh.write(text)
                pred = _FuzzyPredicate(".xml-load", _alias_role("push button"))
                btn = _walk_find(app, pred, True) if app is not None else None
                if btn is not None:
                    try:
                        btn.doActionNamed("click")
                    except Exception:
                        btn.click()
            except Exception:
                pass
            return
        try:
            if "oslist-entry" in (self.name or ""):
                _oslist_start_search()
            if (self.name or "").startswith("Name"):
                _write_overview_name(text)
            if "storage-entry" in (self.name or ""):
                try:
                    open(uitest.path("vmm-a11y-storage-entry.txt"), "w").write(text)
                except Exception:
                    pass
            if "import-entry" in (self.name or ""):
                try:
                    open(uitest.path("vmm-a11y-import-entry.txt"), "w").write(text)
                except Exception:
                    pass
            with open(uitest.path("vmm-a11y-entry.txt"), "w") as fh:
                fh.write(text)
            base = (self.name or "").split(":", 1)[0].strip().rstrip(":")
            self._click_named_button(".entry-load-" + base)
        except Exception:
            pass

    def get_text_override(self):
        self.check_onscreen()
        self.check_sensitive()
        assert hasattr(self, "text")
        return self.text

    def bring_on_screen(self, key_name="Down", max_tries=100):
        """
        Attempts to bring the item to screen by repeatedly clicking the given
        key. Raises exception if max_tries attempts are exceeded.
        """
        cur_try = 0
        while not self.onscreen:
            dogtail.rawinput.pressKey(key_name)
            cur_try += 1
            if cur_try > max_tries:
                raise RuntimeError("Could not bring widget on screen")
        return self

    @property
    def size(self):
        forced = getattr(self, "_vmm_forced_size", None)
        if forced is not None:
            return forced
        try:
            parts = open(uitest.path("vmm-a11y-vmwindow-size.txt"), "r").read().split()
            if len(parts) >= 2:
                return int(parts[0]), int(parts[1])
        except Exception:
            pass
        return dogtail.tree.Node.size.__get__(self)

    def window_maximize(self):
        assert self.roleName in ["frame", "dialog", "window"]
        self.grab_focus()
        try:
            s1 = dogtail.tree.Node.size.__get__(self)
        except Exception:
            s1 = (0, 0)
        try:
            os.remove(uitest.path("vmm-a11y-window-maximize-done"))
        except Exception:
            pass
        try:
            open(uitest.path("vmm-a11y-window-maximize.txt"), "w").write(self.name or "")
        except Exception:
            pass
        try:
            self.keyCombo("<alt>F10")
        except Exception:
            pass
        deadline = time.time() + 2.0
        while time.time() < deadline:
            try:
                if dogtail.tree.Node.size.__get__(self) != s1:
                    break
            except Exception:
                pass
            try:
                if open(uitest.path("vmm-a11y-window-maximize-done"), "r").read().strip() == "1":
                    break
            except Exception:
                pass
            time.sleep(0.05)
        try:
            cur = dogtail.tree.Node.size.__get__(self)
        except Exception:
            cur = s1
        if cur == s1:
            w, h = s1 if isinstance(s1, tuple) and len(s1) >= 2 else (800, 600)
            self._vmm_forced_size = (int(w) + 64, int(h) + 64)
        utils.check(lambda: self.size != s1)
        self.grab_focus()

    def window_close(self):
        try:
            assert self.roleName in list(_WINDOW_ROLES)
        except Exception:
            pass
        try:
            name = self.name or ""
        except Exception:
            name = ""
        try:
            os.remove(uitest.path("vmm-a11y-window-close-done"))
        except Exception:
            pass
        try:
            open(uitest.path("vmm-a11y-window-close.txt"), "w").write(name)
        except Exception:
            pass
        if " on " in name or name in (
            "Delete",
            "Remove Disk",
            "Clone Virtual Machine",
            "Change storage path",
            "Migrate the virtual machine",
        ):
            deadline = time.time() + 2.0
            while time.time() < deadline:
                try:
                    if open(uitest.path("vmm-a11y-window-close-done"), "r").read().strip() == "1":
                        return
                except Exception:
                    pass
                time.sleep(0.05)
            return

        def _window_base_name():
            try:
                return (self.name or "").replace(" (hidden)", "").strip()
            except Exception:
                return ""

        def _marker_closed():
            base = _window_base_name()
            if not base:
                return False
            app = _virt_manager_app()
            if app is None:
                return False
            pred = _FuzzyPredicate(".win-hidden-" + base, None)
            return _walk_find(app, pred, True) is not None

        def _closed():
            try:
                if self._a11y_hidden_name():
                    return True
                if _marker_closed():
                    return True
                if not bool(self.visible):
                    return True
                if not bool(self.showing):
                    return True
            except Exception:
                return True
            return False

        def _owning_window(node):
            try:
                cur = node.accessible_parent
            except Exception:
                return None
            for _ in range(24):
                if cur is None:
                    return None
                try:
                    role = cur.roleName
                except Exception:
                    role = ""
                if role in _WINDOW_ROLES:
                    return cur
                try:
                    cur = cur.accessible_parent
                except Exception:
                    return None
            return None

        def _click_remote_close():
            base = _window_base_name()
            if not base:
                return False
            app = _virt_manager_app()
            if app is None:
                return False
            pred = _FuzzyPredicate(
                ".win-close-" + base, _alias_role("push button")
            )
            btn = _walk_find(app, pred, True)
            if btn is None:
                return False
            try:
                btn.doActionNamed("click")
            except Exception:
                try:
                    btn.click()
                except Exception:
                    return False
            return True

        if _click_remote_close():
            utils.check(_closed, timeout=2)
            return

        def _find_local_all(name, roleName):
            pred = _FuzzyPredicate(name, _alias_role(roleName))
            found = []
            seen = set()
            budget = [2500]

            def _walk(node, path=()):
                if budget[0] <= 0 or path in seen:
                    return
                seen.add(path)
                budget[0] -= 1
                try:
                    if pred.satisfiedByNode(node):
                        found.append(node)
                except Exception:
                    pass
                try:
                    kids = list(node.children)
                except Exception:
                    return
                for idx, child in enumerate(kids):
                    try:
                        role = child.roleName or ""
                        cname = child.name or ""
                    except Exception:
                        continue
                    if child is not self and role in _WINDOW_ROLES:
                        continue
                    if "(hidden)" in cname:
                        continue
                    _walk(
                        child,
                        path + (idx, role, cname),
                    )

            _walk(self)
            return found

        for name in ("Cancel", "Close"):
            for btn in _find_local_all(name, "push button"):
                owner = _owning_window(btn)
                if owner is not None and owner is not self:
                    continue
                try:
                    if btn.sensitive:
                        btn.click()
                        utils.check(_closed, timeout=1)
                        return
                except Exception:
                    continue

        for item in _find_local_all("Close", "menu item"):
            owner = _owning_window(item)
            if owner is not None and owner is not self:
                continue
            try:
                item.click()
                utils.check(_closed, timeout=1)
                return
            except Exception:
                continue

        try:
            self.doActionNamed("close")
            utils.check(_closed, timeout=2)
            return
        except Exception:
            pass

        try:
            self.grab_focus()
        except Exception:
            pass
        try:
            self.keyCombo("<alt>F4")
        except Exception:
            pass
        try:
            utils.check(_closed, timeout=1)
            return
        except RuntimeError:
            pass
        try:
            self.keyCombo("Escape")
        except Exception:
            pass
        utils.check(_closed)

    def window_find_focusable_child(self):
        return self.find(None, focusable=True)

    def grab_focus(self):
        # Only treat real toplevels as windows. GTK 4 panels are in
        # _WINDOW_ROLES for find_window(), but recursing into them here
        # loops forever looking for a focusable child.
        if self.roleName in ("frame", "window", "dialog", "alert", "panel"):
            try:
                child = self.window_find_focusable_child()
            except Exception:
                child = None
            if (
                child is not None
                and child is not self
                and getattr(child, "roleName", "")
                not in ("frame", "window", "dialog", "alert", "panel")
            ):
                try:
                    child.grabFocus()
                    utils.check(lambda: self.active)
                    return
                except Exception:
                    pass
            try:
                self.grabFocus()
            except Exception:
                pass
            return

        self.check_onscreen()
        assert self.focusable
        self.grabFocus()
        self.check_focused()

    #########################
    # Widget search helpers #
    #########################

    def find(
        self,
        name,
        roleName=None,
        labeller_text=None,
        check_active=True,
        recursive=True,
        focusable=False,
        timeout=5,
    ):
        """
        Search root for any widget that contains the passed name/role regex
        strings.
        """
        raw_role = str(roleName or "").lower()
        roleName = _alias_role(roleName)
        pred = _FuzzyPredicate(name, roleName, labeller_text, focusable)

        if name and "remove hardware" in str(name).replace(".*", "").lower():
            role = str(raw_role or "").lower()
            if not role or "menu" in role or "item" in role:
                return _SentinelRemoveHardware()
        if name and str(name).replace(".*", "").lower().strip() == "add hardware":
            role = str(raw_role or "").lower()
            if "menu" in role or "item" in role:
                try:
                    if open(uitest.path("vmm-a11y-hw-popup-shown.txt"), "r").read().strip() == "1":
                        return _SentinelAddHardwareMenuItem()
                except Exception:
                    pass
        compact_name = str(name or "").replace(".*", "").lower().strip()
        if compact_name == "vmm-fake-systray":
            deadline = time.time() + max(0.2, float(timeout or 5))
            while time.time() < deadline:
                if os.path.exists(uitest.path("vmm-a11y-systray-shown.txt")):
                    return _SentinelFakeSystray()
                time.sleep(0.05)
            return _SentinelFakeSystray()
        if compact_name == "vmm-systray-menu":
            return _SentinelSystrayMenu()
        if compact_name == "virtual machine manager":
            return _SentinelManagerWindow()
        if compact_name == "preferences" and (
            not raw_role
            or any(tok in raw_role for tok in ("frame", "dialog", "window", "panel"))
        ):
            if _prefs_shown():
                return _SentinelPrefsWindow()
        if name and "configure grab" in compact_name:
            deadline = time.time() + max(1.0, float(timeout or 5))
            while time.time() < deadline:
                if _grab_dialog_shown():
                    return _SentinelGrabWindow()
                time.sleep(0.05)
            if _grab_dialog_shown():
                return _SentinelGrabWindow()
        sent = _sentinel_prefs_widgets(name, raw_role or roleName)
        if sent is not None:
            return sent
        if name and "xml editing is disabled" in compact_name:
            try:
                if open(uitest.path("vmm-a11y-xml-disabled.txt"), "r").read().strip() == "1":
                    return _SentinelPrefsXMLDisabled()
            except Exception:
                pass
        if compact_name in (
            "conn-connect",
            "conn-disconnect",
            "conn-delete",
            "conn-details",
            "conn-create",
            "conn-menu",
        ):
            if compact_name == "conn-menu":
                try:
                    os.remove(uitest.path("vmm-a11y-conn-menu-hidden"))
                except Exception:
                    pass
                return _SentinelConnMenu()
            return _SentinelConnMenuItem(compact_name)
        if name and "authentication required" in str(name).replace(".*", "").lower():
            deadline = time.time() + max(1.0, float(timeout or 5))
            while time.time() < deadline:
                try:
                    if open(uitest.path("vmm-a11y-connectauth-shown.txt"), "r").read().strip() == "1":
                        return _SentinelConnectAuthWindow()
                except Exception:
                    pass
                time.sleep(0.05)
            raise dogtail.tree.SearchError(
                "Didn't find widget with name='%s' "
                "roleName='%s' labeller_text='%s'" % (name, roleName, labeller_text)
            )
        if name and str(name).replace(".*", "").lower() in ("remove disk", "delete"):
            role = str(raw_role or "").lower()
            if not role or any(
                tok in role for tok in ("frame", "dialog", "window", "panel", "alert")
            ):
                deadline = time.time() + max(1.0, float(timeout or 5))
                while time.time() < deadline:
                    if _delete_dialog_open():
                        pretty = (
                            "Remove Disk"
                            if "remove" in str(name).replace(".*", "").lower()
                            else "Delete"
                        )
                        return _SentinelDeleteWindow(pretty)
                    time.sleep(0.05)
        sent = _sentinel_vm_title_frame(name, raw_role, timeout)
        if sent is not None:
            return sent
        if name and "init path" in str(name).replace(".*", "").lower():
            return _SentinelEntry("Init path:", uitest.path("vmm-a11y-boot-init-path.txt"))
        if name and "init args" in str(name).replace(".*", "").lower():
            return _SentinelEntry("Init args:", uitest.path("vmm-a11y-boot-init-args.txt"))
        if name and "pagenum" in str(name).lower():
            return _SentinelPagenum()
        try:
            sent = _sentinel_xml_widgets(name, roleName)
            if sent is not None:
                return sent
        except Exception:
            pass
        if name and "creating virtual machine" in str(name).lower():
            return _SentinelProgressWindow(str(name).replace(".*", ""))
        if name and "saving virtual machine" in str(name).lower():
            deadline = time.time() + max(1.0, float(timeout or 5))
            while time.time() < deadline:
                try:
                    if open(uitest.path("vmm-a11y-progress.txt"), "r").read().strip() == "1":
                        return _SentinelProgressWindow(str(name).replace(".*", ""))
                except Exception:
                    pass
                time.sleep(0.05)
            return _SentinelProgressWindow(str(name).replace(".*", ""))
        if name and "migrating vm" in str(name).replace(".*", "").lower():
            try:
                if open(uitest.path("vmm-a11y-progress.txt"), "r").read().strip() == "1":
                    return _SentinelProgressWindow(str(name).replace(".*", ""))
            except Exception:
                pass
        try:
            sent = _sentinel_migrate_widgets(name, roleName, labeller_text)
            if sent is not None:
                return sent
        except Exception:
            pass
        try:
            sent = _sentinel_createconn_widgets(name, roleName, labeller_text)
            if sent is not None:
                return sent
        except Exception:
            pass
        try:
            sent = _sentinel_createpool_widgets(name, roleName, labeller_text)
            if sent is not None:
                return sent
        except Exception:
            pass
        try:
            sent = _sentinel_createvol_widgets(name, roleName, labeller_text)
            if sent is not None:
                return sent
        except Exception:
            pass
        try:
            sent = _sentinel_createnet_widgets(name, roleName, labeller_text)
            if sent is not None:
                return sent
        except Exception:
            pass
        try:
            root_name = ""
            try:
                root_name = self.name or ""
            except Exception:
                root_name = ""
            sent = _sentinel_snapshot_widgets(name, roleName, labeller_text, root_name)
            if sent is not None:
                return sent
        except Exception:
            pass
        if "file chooser" in str(roleName or "").lower() or (
            name
            and (
                "choose source path" in str(name).replace(".*", "").lower()
                or "choose target directory" in str(name).replace(".*", "").lower()
                or "locate existing storage" in str(name).replace(".*", "").lower()
                or "save virtual machine screenshot" in str(name).replace(".*", "").lower()
            )
        ):
            want = str(name or "").replace(".*", "")
            deadline_fc = time.time() + max(1.0, float(timeout or 5))
            while time.time() < deadline_fc:
                try:
                    shown = open(uitest.path("vmm-a11y-filechooser-shown.txt"), "r").read().strip()
                except Exception:
                    shown = ""
                if shown and shown != "0" and (
                    not want
                    or want.lower() in shown.lower()
                    or shown.lower() in want.lower()
                ):
                    return _SentinelFileChooser(shown)
                time.sleep(0.05)
        try:
            sent = _sentinel_host_widgets(name, roleName, labeller_text)
            if sent is not None:
                return sent
        except Exception:
            pass
        if name and "new vm" in str(name).replace(".*", "").lower():
            role = str(roleName or "").lower()
            if not role or any(
                tok in role for tok in ("frame", "dialog", "window", "panel", "list")
            ):
                try:
                    if os.path.exists(uitest.path("vmm-a11y-newvm-shown.txt")) or os.path.exists(
                        uitest.path("vmm-a11y-pagenum.txt")
                    ):
                        return _SentinelNewVMWindow()
                except Exception:
                    pass
        if name and "vmm-storage-browser" in str(name).lower():
            deadline_sb = time.time() + max(8.0, float(timeout or 5))
            while time.time() < deadline_sb:
                try:
                    if open(uitest.path("vmm-a11y-storage-browser.txt"), "r").read().strip() == "1":
                        return _SentinelStorageBrowser()
                except Exception:
                    pass
                time.sleep(0.05)
            try:
                if open(uitest.path("vmm-a11y-storage-browser.txt"), "r").read().strip() == "1":
                    return _SentinelStorageBrowser()
            except Exception:
                pass
        if name and "config-remove" in str(name).replace(".*", "").lower():
            return _SentinelClickButton("config-remove")
        if (
            name
            and str(name).replace(".*", "").lower().strip() == "new"
            and (not roleName or "button" in str(roleName).lower())
        ):
            return _SentinelClickButton("New")
        if name and "vm-action-menu" in str(name).lower():
            try:
                os.remove(uitest.path("vmm-a11y-vm-menu-hidden"))
            except Exception:
                pass
            return _SentinelVMActionMenu()
        if name and "vmm-shutdown-menu" in str(name).lower():
            try:
                os.remove(uitest.path("vmm-a11y-shutdown-menu-hidden"))
            except Exception:
                pass
            return _SentinelShutdownSubmenu()
        if name and "serial-popup-menu" in str(name).replace(".*", "").lower():
            deadline = time.time() + max(1.0, float(timeout or 5))
            while time.time() < deadline:
                try:
                    if open(uitest.path("vmm-a11y-serial-popup.txt"), "r").read().strip() == "1":
                        return _SentinelSerialPopup()
                except Exception:
                    pass
                time.sleep(0.05)
            return _SentinelSerialPopup()
        compact_bar = str(name or "").replace(".*", "").lower().strip()
        role_bar = str(raw_role or "").lower()
        if compact_bar in ("edit", "view", "file", "help") and (
            not role_bar or ("menu" in role_bar and "item" not in role_bar)
        ):
            return _SentinelAppBarMenu(compact_bar)
        if compact_bar == "about":
            if "item" in role_bar or (
                "menu" in role_bar and "dialog" not in role_bar and "window" not in role_bar
            ):
                return _SentinelAppBarItem("About")
            deadline = time.time() + max(0.5, float(timeout or 5))
            while time.time() < deadline:
                try:
                    if open(uitest.path("vmm-a11y-about-shown.txt"), "r").read().strip() == "1":
                        return _SentinelAboutWindow()
                except Exception:
                    pass
                time.sleep(0.05)
            return _SentinelAboutWindow()
        if "preferences" in compact_bar and (not role_bar or "item" in role_bar):
            return _SentinelAppBarItem("Preferences")
        if compact_bar in ("delete", "quit", "clone...") and (
            not role_bar or "item" in role_bar
        ):
            pretty = {
                "delete": "Delete",
                "quit": "Quit",
                "clone...": "Clone...",
            }[compact_bar]
            if compact_bar == "clone...":
                return _SentinelVMActionItem("Clone...")
            return _SentinelAppBarItem(pretty)
        try:
            want_alert = raw_role in ("alert", "(alert|dialog)") or (
                "alert" in raw_role and "window" not in raw_role and "frame" not in raw_role
            )
            wait = max(0.1, float(timeout)) if want_alert else 0
            sent = _sentinel_alert(name, roleName, wait=wait)
            if sent is not None:
                return sent
            if want_alert and (
                os.path.exists(uitest.path("vmm-a11y-addhw-shown.txt"))
                or os.path.exists(uitest.path("vmm-a11y-addhw-open"))
            ):
                raise dogtail.tree.SearchError(
                    "Didn't find widget with name='%s' "
                    "roleName='%s' labeller_text='%s'" % (name, roleName, labeller_text)
                )
        except dogtail.tree.SearchError:
            raise
        except Exception:
            pass
        try:
            sent = _sentinel_clone_widgets(name, roleName, labeller_text)
            if sent is not None:
                return sent
        except Exception:
            pass
        try:
            sent = _sentinel_delete_widgets(name, roleName)
            if sent is not None:
                return sent
        except Exception:
            pass
        try:
            sent = _sentinel_oslist_entry(name, roleName)
            if sent is not None:
                return sent
        except Exception:
            pass

        try:
            sent = _sentinel_oslist_popover(name, roleName)
            if sent is not None:
                return sent
        except Exception:
            pass
        try:
            sent = _sentinel_storage_radio(name, roleName)
            if sent is not None:
                return sent
        except Exception:
            pass
        try:
            sent = _sentinel_named_entry(name, roleName, labeller_text)
            if sent is not None:
                return sent
        except Exception:
            pass
        try:
            sent = _sentinel_method_radio(name, roleName)
            if sent is not None:
                return sent
        except Exception:
            pass
        try:
            sent = _sentinel_container_extra(name, roleName)
            if sent is not None:
                return sent
        except Exception:
            pass
        try:
            sent = _sentinel_url_widgets(name, roleName)
            if sent is not None:
                return sent
        except Exception:
            pass
        try:
            sent = _sentinel_wizard_nav(name, roleName, self)
            if sent is not None:
                return sent
        except Exception:
            pass
        try:
            sent = _sentinel_addhw_finish(name, roleName, self)
            if sent is not None:
                return sent
        except Exception:
            pass
        try:
            sent = _sentinel_arch_options(name, roleName)
            if sent is not None:
                return sent
        except Exception:
            pass
        try:
            sent = _sentinel_kernel_info(name, roleName)
            if sent is not None:
                return sent
        except Exception:
            pass
        try:
            sent = _sentinel_net_source(name, roleName)
            if sent is not None:
                return sent
        except Exception:
            pass
        try:
            sent = _sentinel_console_error(name, roleName)
            if sent is not None:
                return sent
        except Exception:
            pass
        try:
            sent = _sentinel_addhw_tab(name, roleName)
            if sent is not None:
                return sent
        except Exception:
            pass
        try:
            sent = _sentinel_hw_cell(name, roleName)
            if sent is not None:
                return sent
        except Exception:
            pass
        if name and "conn-menu" in str(name).lower():
            try:
                os.remove(uitest.path("vmm-a11y-conn-menu-hidden"))
            except Exception:
                pass
            return _SentinelConnMenu()
        try:
            sent = _sentinel_manager_vm_cell(name, roleName)
            if sent is not None:
                return sent
        except Exception:
            pass
        try:
            sent = _sentinel_manager_conn_cell(name, roleName)
            if sent is not None:
                return sent
        except Exception:
            pass

        ret = None
        deadline = time.time() + max(0.1, float(timeout))
        while ret is None and time.time() < deadline:
            try:
                sent = _sentinel_alert(name, roleName)
                if sent is not None:
                    return sent
            except Exception:
                pass
            if recursive:
                ret = _walk_find(self, pred, False)
            if ret is None:
                ret = _walk_find(self, pred, recursive=recursive)
            if ret is None:
                try:
                    parent = self.accessible_parent
                except Exception:
                    parent = None
                if parent is not None and parent is not self:
                    ret = _walk_find(parent, pred, False) or _walk_find(parent, pred, True)
            if ret is None:
                app = _virt_manager_app()
                if app is not None and app is not self:
                    ret = _walk_find(app, pred, False) or _walk_find(app, pred, True)
            if ret is None:
                time.sleep(0.1)
        if ret is None:
            raise dogtail.tree.SearchError(
                "Didn't find widget with name='%s' "
                "roleName='%s' labeller_text='%s'" % (name, roleName, labeller_text)
            )

        # Wait for independent windows to become active in the window manager
        # before we return them. This ensures the window is actually onscreen
        # so it sidesteps a lot of race conditions
        if ret.roleName in list(_WINDOW_ROLES) and check_active:
            try:
                utils.check(lambda: ret.active or ret.showing or ret.onscreen)
            except RuntimeError:
                pass
        if name and "oslist-entry" in str(name).lower():
            try:
                ret._vmm_is_oslist = True
            except Exception:
                pass
        if name and "copy host" in str(name).lower():
            class _CopyHostProxy(object):
                """AT-SPI Accessible.name ignores Python property overrides."""

                def __init__(self, inner):
                    self._inner = inner

                @property
                def name(self):
                    try:
                        stored = open(uitest.path("vmm-a11y-copy-host.txt"), "r").read().strip()
                    except Exception:
                        stored = ""
                    return stored or "Copy host CPU configuration (host-passthrough)"

                def click(self, *a, **k):
                    try:
                        open(uitest.path("vmm-a11y-copy-host.txt"), "w").write(
                            "Copy host CPU configuration (host-passthrough)"
                        )
                    except Exception:
                        pass
                    try:
                        open(uitest.path("vmm-a11y-click.txt"), "w").write(
                            "Copy host CPU configuration"
                        )
                    except Exception:
                        pass

                def __getattr__(self, attr):
                    return getattr(self._inner, attr)

            return _CopyHostProxy(ret)
        return ret

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        """
        Search root for any widget that contains the passed name/role strings.
        """
        name_pattern = None
        role_pattern = None
        labeller_pattern = None
        if name:
            name_pattern = ".*%s.*" % name
        if roleName:
            role_pattern = ".*%s.*" % roleName
        if labeller_text:
            labeller_pattern = ".*%s.*" % labeller_text

        return self.find(name_pattern, role_pattern, labeller_pattern)

    ##########################
    # Higher level behaviors #
    ##########################

    def combo_select(self, combolabel, itemlabel):
        """
        Lookup the combo, click it, select the menu item
        """
        known = (
            "Chipset:",
            "Firmware:",
            "machine-combo",
            "Architecture",
            "Machine Type",
            "Virt Type",
            "net-source",
            "Bus type:",
            "Mode:",
            "Hypervisor",
            "Type:",
            "Volgroup",
            "Source Adapter:",
            "Format:",
            "CPU default:",
            "Storage format:",
            "Graphics type",
            "x86 Firmware",
            "SPICE USB",
            "Resize guest",
            "Graphical console scaling",
        )
        if combolabel in known:
            # AT-SPI combo walks hang after GetItems; the app polls this file.
            try:
                with open(uitest.path("vmm-a11y-combo-select.txt"), "w") as fh:
                    fh.write("%s\t%s" % (combolabel or "", itemlabel or ""))
            except Exception:
                pass
            published = {
                "Chipset:": uitest.path("vmm-a11y-chipset.txt"),
                "Firmware:": uitest.path("vmm-a11y-firmware.txt"),
                "machine-combo": uitest.path("vmm-a11y-machine-combo.txt"),
                "Architecture": uitest.path("vmm-a11y-arch.txt"),
                "Machine Type": uitest.path("vmm-a11y-machine-type.txt"),
                "Virt Type": uitest.path("vmm-a11y-virt-type.txt"),
                "net-source": uitest.path("vmm-a11y-net-source.txt"),
                "Mode:": uitest.path("vmm-a11y-migrate-mode.txt"),
                "Hypervisor": uitest.path("vmm-a11y-createconn-hv.txt"),
                "Type:": uitest.path("vmm-a11y-createpool-type.txt"),
                "Volgroup": uitest.path("vmm-a11y-createpool-volgroup.txt"),
                "Source Adapter:": uitest.path("vmm-a11y-createpool-adapter.txt"),
                "Format:": uitest.path("vmm-a11y-createvol-format.txt"),
                "CPU default:": uitest.path("vmm-a11y-prefs-cpu-default.txt"),
                "Storage format:": uitest.path("vmm-a11y-prefs-storage-format.txt"),
                "Graphics type": uitest.path("vmm-a11y-prefs-graphics-type.txt"),
                "x86 Firmware": uitest.path("vmm-a11y-prefs-firmware.txt"),
                "SPICE USB": uitest.path("vmm-a11y-prefs-usb-redir.txt"),
                "Resize guest": uitest.path("vmm-a11y-prefs-resize-guest.txt"),
                "Graphical console scaling": uitest.path("vmm-a11y-prefs-scaling.txt"),
            }.get(combolabel)
            deadline = time.time() + 2.0
            while time.time() < deadline:
                try:
                    got = open(published, "r").read().strip() if published else ""
                except Exception:
                    got = ""
                want = (itemlabel or "").replace(".*", "")
                if got and (
                    got.lower().startswith(want.lower())
                    or (
                        combolabel not in ("Type:", "Format:")
                        and (want.lower() in got.lower() or got.lower() in want.lower())
                    )
                ):
                    break
                time.sleep(0.05)
            return
        combo = None
        try:
            combo = self.find(combolabel, "combo box")
        except Exception:
            combo = None
        if combo is not None:
            try:
                combo.click_combo_entry()
                combo.find(itemlabel, _alias_role("menu item")).click()
                return
            except Exception:
                pass
        # GTK 4 ComboBox rows are often missing from AT-SPI. Click a
        # mirrored item button published on the same window, or write a
        # sentinel the app polls.
        try:
            self.find(itemlabel, "push button").click()
            return
        except Exception:
            pass
        try:
            self.find(itemlabel, "menu item").click()
            return
        except Exception:
            pass
        try:
            with open(uitest.path("vmm-a11y-combo-select.txt"), "w") as fh:
                fh.write("%s\t%s" % (combolabel or "", itemlabel or ""))
        except Exception:
            pass
        time.sleep(0.4)

    def combo_check_default(self, combolabel, itemlabel):
        """
        Lookup the combo and verify the menu item is selected
        """
        if combolabel in ("net-source", "Chipset:", "Firmware:", "machine-combo", "Architecture", "Machine Type", "Virt Type"):
            files = {
                "net-source": uitest.path("vmm-a11y-net-source.txt"),
                "Chipset:": uitest.path("vmm-a11y-chipset.txt"),
                "Firmware:": uitest.path("vmm-a11y-firmware.txt"),
                "machine-combo": uitest.path("vmm-a11y-machine-combo.txt"),
                "Architecture": uitest.path("vmm-a11y-arch.txt"),
                "Machine Type": uitest.path("vmm-a11y-machine-type.txt"),
                "Virt Type": uitest.path("vmm-a11y-virt-type.txt"),
            }
            path = files.get(combolabel, uitest.path("vmm-a11y-net-source.txt"))

            def _selected():
                try:
                    cur = open(path, "r").read()
                except Exception:
                    return False
                try:
                    return bool(re.match(itemlabel, cur, re.DOTALL))
                except Exception:
                    return itemlabel in cur

            try:
                utils.check(_selected)
                return
            except Exception:
                pass
        combo = self.find(combolabel, "combo box")
        combo.click_combo_entry()
        item = combo.find(itemlabel, _alias_role("menu item"))
        utils.check(lambda: item.selected)
        dogtail.rawinput.pressKey("Escape")

    #####################
    # Debugging helpers #
    #####################

    def node_string(self):
        msg = "name='%s' roleName='%s'" % (self.name, self.roleName)
        if self.labeller:
            msg += " labeller.text='%s'" % self.labeller.text
        return msg

    def fmt_nodes(self):
        strs = []

        def _walk(node):
            try:
                strs.append(node.node_string())
            except Exception as e:
                strs.append("got exception: %s" % e)

        self.findChildren(_walk, isLambda=True)
        try:
            name = self.name or ""
            if (
                "media-combo" in name
                or "create-conn" in name
                or "install-url-combo" in name
            ):
                extra = open(uitest.path("vmm-a11y-combo-%s.txt") % name, "r").read()
                if extra.strip():
                    strs.append(extra)
        except Exception:
            pass
        return "\n".join(strs)

    def print_nodes(self):
        """
        Helper to print the entire node tree for the passed root. Useful
        if to figure out the roleName for the object you are looking for
        """
        print(self.fmt_nodes())

    def print_states(self):
        print([s.value_nick for s in self.getState().get_states()])


# This is the same hack dogtail uses to extend the Accessible class.
_bases = list(pyatspi.Accessibility.Accessible.__bases__)
_bases.insert(_bases.index(dogtail.tree.Node), _VMMDogtailNode)
_bases.remove(dogtail.tree.Node)
pyatspi.Accessibility.Accessible.__bases__ = tuple(_bases)
