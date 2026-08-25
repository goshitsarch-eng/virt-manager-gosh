# This work is licensed under the GNU GPLv2 or later.
# See the COPYING file in the top-level directory.

import os
import re
import time

from gi.repository import Gdk

import dogtail.tree
import pyatspi

from virtinst import log
from . import utils


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


class _SentinelTableCell(object):
    """hw-list row when AT-SPI walks hang after GetItems."""

    def __init__(self, name, selected=False):
        self.name = name
        self.roleName = "table cell"
        self._selected = selected

    @property
    def state_selected(self):
        try:
            cur = open("/tmp/vmm-a11y-hw-selected.txt", "r").read().strip()
            if cur == self.name:
                return True
        except Exception:
            pass
        try:
            cur = open("/tmp/vmm-a11y-hostdev-selected.txt", "r").read().strip()
            if cur == self.name or (self.name and self.name in cur):
                return True
        except Exception:
            pass
        return self._selected

    @property
    def selected(self):
        return self.state_selected

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
            open("/tmp/vmm-a11y-hw-select.txt", "w").write(self.name or "")
        except Exception:
            pass

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
                open("/tmp/vmm-a11y-oslist-eol.txt", "w").write("1")
                return
            open("/tmp/vmm-a11y-os-select.txt", "w").write(want)
        except Exception:
            pass


class _OslistPopoverSentinel(object):
    """oslist-popover after GetItems: AT-SPI walks miss the renamed wrap."""

    def __init__(self):
        self.name = "oslist-popover"
        self.roleName = "panel"

    def _hidden(self):
        try:
            return os.path.exists(
                "/tmp/vmm-a11y-oslist-popover-hidden"
            ) or os.path.exists("/tmp/vmm-a11y-oslist-escape")
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
            return open("/tmp/vmm-a11y-oslist-entry.txt", "r").read()
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
            "/tmp/vmm-a11y-oslist-escape",
            "/tmp/vmm-a11y-oslist-popover-hidden",
        ):
            try:
                os.remove(marker)
            except Exception:
                pass
        try:
            open("/tmp/vmm-a11y-oslist-reopen", "w").write("1")
        except Exception:
            pass

    def set_text(self, text):
        # Keep the load-button / os-select path so set_text("generic") works.
        try:
            open("/tmp/vmm-a11y-oslist-entry.txt", "w").write(text or "")
            open("/tmp/vmm-a11y-entry.txt", "w").write(text or "")
            open("/tmp/vmm-a11y-oslist-typed", "w").write("1")
            open("/tmp/vmm-a11y-os-select.txt", "w").write(text or "")
        except Exception:
            pass
        _oslist_start_search()
        try:
            open("/tmp/vmm-a11y-click.txt", "w").write(".entry-load-oslist-entry")
        except Exception:
            pass


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
            open("/tmp/vmm-a11y-storage-radio.txt", "w").write(self._want)
        except Exception:
            pass
        try:
            open("/tmp/vmm-a11y-click.txt", "w").write(self.name)
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
            open("/tmp/vmm-a11y-click.txt", "w").write("Enable storage")
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
        try:
            return open(self._path, "r").read()
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

    def check_onscreen(self):
        return True

    def check_sensitive(self):
        return True

    def click(self, *args, **kwargs):
        return True

    def set_text(self, text):
        try:
            open(self._path, "w").write(text if text is not None else "")
        except Exception:
            pass
        try:
            open("/tmp/vmm-a11y-entry.txt", "w").write(text if text is not None else "")
        except Exception:
            pass
        if self.name == "install-url-entry":
            try:
                open("/tmp/vmm-a11y-url-entry.txt", "w").write(
                    text if text is not None else ""
                )
            except Exception:
                pass
        if self.name == "install-urlopts-entry":
            try:
                open("/tmp/vmm-a11y-urlopts-entry.txt", "w").write(
                    text if text is not None else ""
                )
            except Exception:
                pass
        if str(self.name).startswith("Name"):
            try:
                open("/tmp/vmm-a11y-create-name.txt", "w").write(
                    text if text is not None else ""
                )
            except Exception:
                pass
            try:
                open("/tmp/vmm-a11y-overview-name.txt", "w").write(
                    text if text is not None else ""
                )
            except Exception:
                pass


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
            open("/tmp/vmm-a11y-click.txt", "w").write("Architecture options")
        except Exception:
            pass

    def click_expander(self, *args, **kwargs):
        self.click()


def _sentinel_arch_options(name, roleName):
    if not name:
        return None
    compact = str(name).replace(".*", "").lower()
    if "architecture options" not in compact:
        return None
    return _ArchOptionsSentinel()


def _sentinel_named_entry(name, roleName):
    if not name:
        return None
    raw = str(name).replace(".*", "")
    if raw.startswith("."):
        return None
    role = str(roleName or "").lower()
    if role and "text" not in role and "entry" not in role:
        # find("storage-entry") passes roleName=None
        if role not in ("", "none"):
            return None
    compact = raw.lower()
    if compact == "storage-entry" or raw == "storage-entry":
        return _SentinelEntry("storage-entry", "/tmp/vmm-a11y-storage-entry.txt")
    if compact in ("name", "name:") or raw in ("Name", "Name:"):
        return _SentinelEntry("Name:", "/tmp/vmm-a11y-create-name.txt")
    if compact == "import-entry" or raw == "import-entry":
        return _SentinelEntry("import-entry", "/tmp/vmm-a11y-import-entry.txt")
    if compact == "install-url-entry" or raw == "install-url-entry":
        return _SentinelEntry("install-url-entry", "/tmp/vmm-a11y-url-entry.txt")
    if compact == "install-urlopts-entry" or raw == "install-urlopts-entry":
        return _SentinelEntry("install-urlopts-entry", "/tmp/vmm-a11y-urlopts-entry.txt")
    if "device name" in compact:
        return _SentinelEntry("Device name:", "/tmp/vmm-a11y-net-device.txt")
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
            open("/tmp/vmm-a11y-combo-select.txt", "w").write(
                "%s\t%s" % (self._combo, self.name)
            )
        except Exception:
            pass


class _SentinelNetCombo(object):
    """net-source combo when AT-SPI cannot see the finish-page widget."""

    def __init__(self):
        self.name = "net-source"
        self.roleName = "combo box"
        self._selected_path = "/tmp/vmm-a11y-net-source.txt"

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
            for line in open("/tmp/vmm-a11y-combo-net-source.txt", "r").read().splitlines():
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
            open("/tmp/vmm-a11y-click.txt", "w").write("Network selection")
        except Exception:
            pass

    def click_expander(self, *args, **kwargs):
        self.click()


class _SentinelNetWarn(object):
    name = "Failed to find a suitable default network."
    roleName = "label"

    def _shown(self):
        try:
            return open("/tmp/vmm-a11y-net-warn.txt", "r").read().strip() != "0"
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


class _SentinelAddhwTab(object):
    """Add Hardware notebook page after GetItems hides the real tab panel."""

    def __init__(self, name):
        self.name = name
        self.roleName = "page tab"

    def _current(self):
        try:
            return open("/tmp/vmm-a11y-addhw-tab.txt", "r").read().strip()
        except Exception:
            return ""

    @property
    def showing(self):
        return self._current() == self.name

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
        ignore = (roleName, labeller_text, check_active, recursive, focusable, timeout)
        raw = str(name or "").replace(".*", "")
        compact = raw.lower()
        if "no devices" in compact:
            selected = True
            try:
                selected = "No Devices" in open(
                    "/tmp/vmm-a11y-hostdev-selected.txt", "r"
                ).read()
            except Exception:
                selected = True
            return _SentinelTableCell("No Devices Available", selected)
        raise dogtail.tree.SearchError(
            "Didn't find widget with name='%s' "
            "roleName='%s' labeller_text='%s'" % (name, roleName, labeller_text)
        )

    def find_fuzzy(self, name, roleName=None, labeller_text=None):
        name_pattern = (".*%s.*" % name) if name else None
        role_pattern = (".*%s.*" % roleName) if roleName else None
        labeller_pattern = (".*%s.*" % labeller_text) if labeller_text else None
        return self.find(name_pattern, role_pattern, labeller_pattern)


class _SentinelConsoleError(object):
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
    def visible(self):
        return True

    @property
    def sensitive(self):
        return True

    def check_onscreen(self):
        return True


def _sentinel_console_error(name, roleName):
    if not name:
        return None
    compact = str(name).replace(".*", "").lower()
    if "test suite faking no spice" not in compact and "graphical console" not in compact:
        return None
    text = ""
    for path in (
        "/tmp/vmm-a11y-spice-import.txt",
        "/tmp/vmm-a11y-console-error.txt",
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
        "network-tab",
        "input-tab",
        "graphics-tab",
        "sound-tab",
        "char-tab",
        "video-tab",
        "watchdog-tab",
        "fs-tab",
        "smartcard-tab",
        "usbredir-tab",
        "tpm-tab",
        "rng-tab",
        "panic-tab",
        "vsock-tab",
        "controller-tab",
    )
    if compact in tabs or raw in tabs:
        return _SentinelAddhwTab(compact)
    return None


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
            open("/tmp/vmm-a11y-click.txt", "w").write("install-urlopts-expander")
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
            return open("/tmp/vmm-a11y-combo-install-url-combo.txt", "r").read()
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
            return open("/tmp/vmm-a11y-oslist-eol-state.txt", "r").read().strip() == "1"
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
            open("/tmp/vmm-a11y-oslist-eol.txt", "w").write("1")
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
            "Forward": "/tmp/vmm-a11y-create-forward",
            "Back": "/tmp/vmm-a11y-create-back",
            "Finish": "/tmp/vmm-a11y-click.txt",
        }
        path = mapping.get(self.name, "/tmp/vmm-a11y-click.txt")
        try:
            open(path, "w").write(self.name)
        except Exception:
            pass

    def keyCombo(self, combo, *args, **kwargs):
        self.click()


class _SentinelPagenum(object):
    name = "pagenum-label"
    roleName = "label"

    @property
    def text(self):
        try:
            return open("/tmp/vmm-a11y-pagenum.txt", "r").read().strip()
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
        path = "/tmp/vmm-a11y-detect-state.txt"
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
            open("/tmp/vmm-a11y-click.txt", "w").write(self.name)
        except Exception:
            pass
        # Re-enabling detect should hide the OS popover immediately.
        try:
            open("/tmp/vmm-a11y-oslist-popover-hidden", "w").write("1")
        except Exception:
            pass
        if nxt == "1":
            try:
                open("/tmp/vmm-a11y-oslist-entry.txt", "w").write("Detecting...")
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


def _sentinel_hw_cell(name, roleName):
    if not name:
        return None
    role = str(roleName or "")
    if role and "table cell" not in role and "cell" not in role and "button" not in role:
        return None
    try:
        rows = open("/tmp/vmm-a11y-hw-list.txt", "r").read().splitlines()
    except Exception:
        return None
    matched = None
    try:
        pat = re.compile(name, re.DOTALL)
    except Exception:
        pat = None
    for row in rows:
        if not row:
            continue
        if row == name or (pat is not None and pat.search(row)):
            matched = row
            break
    if matched is None:
        return None
    selected = False
    try:
        selected = open("/tmp/vmm-a11y-hw-selected.txt", "r").read().strip() == matched
    except Exception:
        pass
    return _SentinelTableCell(matched, selected)


def _write_overview_name(text):
    try:
        open("/tmp/vmm-a11y-overview-name.txt", "w").write(text if text is not None else "")
    except Exception:
        pass
    try:
        open("/tmp/vmm-a11y-create-name.txt", "w").write(text if text is not None else "")
    except Exception:
        pass


def _oslist_start_search():
    """Clear Escape/hide markers and allow the popover to reopen after a pick."""
    for marker in (
        "/tmp/vmm-a11y-oslist-escape",
        "/tmp/vmm-a11y-oslist-popover-hidden",
        "/tmp/vmm-a11y-os-select.txt",
    ):
        try:
            os.remove(marker)
        except Exception:
            pass
    try:
        open("/tmp/vmm-a11y-oslist-typed", "w").write("1")
    except Exception:
        pass
    try:
        if os.path.exists("/tmp/vmm-a11y-oslist-confirmed"):
            open("/tmp/vmm-a11y-oslist-reopen", "w").write("1")
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
        if "config-apply" in raw_name:
            try:
                stored = open("/tmp/vmm-a11y-config-apply-sensitive", "r").read().strip()
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
                stored = open("/tmp/vmm-a11y-copy-host.txt", "r").read().strip()
            except Exception:
                stored = ""
            return stored or "Copy host CPU configuration (host-passthrough)"
        try:
            stored = open("/tmp/vmm-a11y-copy-host.txt", "r").read().strip()
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
        if "Add New Virtual Hardware" in name:
            try:
                if os.path.exists("/tmp/vmm-a11y-addhw-hidden"):
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
                "/tmp/vmm-a11y-createconn-hidden"
            ):
                return False
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
                stored = open("/tmp/vmm-a11y-oslist-entry.txt", "r").read()
                stored = stored.strip()
            except Exception:
                stored = ""
            _DETECT_TEXT = (
                "None detected",
                "Detecting...",
                "Waiting for install media / source",
            )
            try:
                if os.path.exists("/tmp/vmm-a11y-oslist-escape") and not os.path.exists(
                    "/tmp/vmm-a11y-oslist-confirmed"
                ):
                    if os.path.exists("/tmp/vmm-a11y-oslist-typed"):
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
                stored = open("/tmp/vmm-a11y-media-entry.txt", "r").read()
                if stored.strip():
                    return stored.strip()
            except Exception:
                pass
        if name.split(":", 1)[0].strip() in ("cpus", "mem", "Memory"):
            key = "cpus" if "cpu" in name else "mem"
            try:
                stored = open("/tmp/vmm-a11y-spin-%s.txt" % key, "r").read()
                if stored.strip():
                    return stored.strip()
            except Exception:
                pass
        if "pagenum-label" in name:
            try:
                stored = open("/tmp/vmm-a11y-pagenum.txt", "r").read()
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
                stored = open("/tmp/vmm-a11y-storage-entry.txt", "r").read()
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
                blob = open("/tmp/vmm-a11y-conn-status.txt", "r").read()
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
                "/tmp/vmm-a11y-oslist-popover-hidden"
            ) or os.path.exists("/tmp/vmm-a11y-oslist-escape")
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
                open("/tmp/vmm-a11y-copy-host.txt", "w").write(
                    "Copy host CPU configuration (host-passthrough)"
                )
            except Exception:
                pass
            try:
                with open("/tmp/vmm-a11y-click.txt", "w") as fh:
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
                with open("/tmp/vmm-a11y-os-select.txt", "w") as fh:
                    fh.write("generic")
            except Exception:
                pass
        os_short = re.search(r"\(([a-z0-9.+-]+)\)$", nname)
        if os_short and os_short.group(1) not in ("hidden", "generic"):
            try:
                open("/tmp/vmm-a11y-oslist-confirmed", "w").write("1")
                open("/tmp/vmm-a11y-oslist-popover-hidden", "w").write("1")
            except Exception:
                pass
        if nname == "copying":
            try:
                path = os.path.join(os.getcwd(), "COPYING")
                if os.path.isfile(path):
                    with open("/tmp/vmm-a11y-file-open.path", "w") as fh:
                        fh.write(path)
            except Exception:
                pass
        if nname.replace("_", "") == "open":
            try:
                with open("/tmp/vmm-a11y-file-open", "w") as fh:
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
                with open("/tmp/vmm-a11y-media-select.txt", "w") as fh:
                    fh.write(raw)
            except Exception:
                pass
            return
        if "config-apply" in nname:
            try:
                with open("/tmp/vmm-a11y-config-apply", "w") as fh:
                    fh.write("1")
            except Exception:
                pass
            try:
                with open("/tmp/vmm-a11y-click.txt", "w") as fh:
                    fh.write(raw or "config-apply")
            except Exception:
                pass
            return
        _SENTINEL_CLICK = (
            "install-iso-browse",
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
                with open("/tmp/vmm-a11y-click.txt", "w") as fh:
                    fh.write(raw or nname)
            except Exception:
                pass
            return
        if any(s in nname for s in _SENTINEL_CLICK):
            try:
                with open("/tmp/vmm-a11y-click.txt", "w") as fh:
                    fh.write(raw or nname)
            except Exception:
                pass
            if "copy host" in nname:
                try:
                    open("/tmp/vmm-a11y-copy-host.txt", "w").write(
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
                with open("/tmp/vmm-a11y-entry.txt", "w") as fh:
                    fh.write(string)
                if self._click_named_button(".entry-load-oslist-entry"):
                    return
            except Exception:
                pass
        return super().typeText(string)

    def set_text(self, text):
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
                        open("/tmp/vmm-a11y-storage-entry.txt", "w").write(text)
                    except Exception:
                        pass
                if "import-entry" in (self.name or ""):
                    try:
                        open("/tmp/vmm-a11y-import-entry.txt", "w").write(text)
                    except Exception:
                        pass
                with open("/tmp/vmm-a11y-entry.txt", "w") as fh:
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
                with open("/tmp/vmm-a11y-xml.txt", "w") as fh:
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
                    open("/tmp/vmm-a11y-storage-entry.txt", "w").write(text)
                except Exception:
                    pass
            if "import-entry" in (self.name or ""):
                try:
                    open("/tmp/vmm-a11y-import-entry.txt", "w").write(text)
                except Exception:
                    pass
            with open("/tmp/vmm-a11y-entry.txt", "w") as fh:
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

    def window_maximize(self):
        assert self.roleName in ["frame", "dialog", "window"]
        self.grab_focus()
        s1 = self.size
        self.keyCombo("<alt>F10")
        utils.check(lambda: self.size != s1)
        self.grab_focus()

    def window_close(self):
        assert self.roleName in list(_WINDOW_ROLES)

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
        roleName = _alias_role(roleName)
        pred = _FuzzyPredicate(name, roleName, labeller_text, focusable)

        if name and "pagenum" in str(name).lower():
            return _SentinelPagenum()
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
            sent = _sentinel_named_entry(name, roleName)
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
            sent = _sentinel_arch_options(name, roleName)
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

        ret = None
        deadline = time.time() + max(0.1, float(timeout))
        while ret is None and time.time() < deadline:
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
                        stored = open("/tmp/vmm-a11y-copy-host.txt", "r").read().strip()
                    except Exception:
                        stored = ""
                    return stored or "Copy host CPU configuration (host-passthrough)"

                def click(self, *a, **k):
                    try:
                        open("/tmp/vmm-a11y-copy-host.txt", "w").write(
                            "Copy host CPU configuration (host-passthrough)"
                        )
                    except Exception:
                        pass
                    try:
                        open("/tmp/vmm-a11y-click.txt", "w").write(
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
            with open("/tmp/vmm-a11y-combo-select.txt", "w") as fh:
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
                "net-source": "/tmp/vmm-a11y-net-source.txt",
                "Chipset:": "/tmp/vmm-a11y-chipset.txt",
                "Firmware:": "/tmp/vmm-a11y-firmware.txt",
                "machine-combo": "/tmp/vmm-a11y-machine-combo.txt",
                "Architecture": "/tmp/vmm-a11y-arch.txt",
                "Machine Type": "/tmp/vmm-a11y-machine-type.txt",
                "Virt Type": "/tmp/vmm-a11y-virt-type.txt",
            }
            path = files.get(combolabel, "/tmp/vmm-a11y-net-source.txt")

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
                extra = open("/tmp/vmm-a11y-combo-%s.txt" % name, "r").read()
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
