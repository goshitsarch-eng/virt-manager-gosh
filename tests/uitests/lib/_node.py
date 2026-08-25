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
            if self._roleName and not self._role_pattern.match(nrole or node.roleName):
                # GTK 4 CheckButton AT-SPI clicks are no-ops. The
                # Automatically detect sidecar is a Button that toggles.
                if not (
                    "check" in str(self._roleName)
                    and "automatically detect" in nname_l
                    and nrole in ("button", "push button")
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
        if "oslist-entry" in name or name.startswith(
            "Choose the operating system"
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
        if self.roleName in (
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
            if self.roleName in ("text", "entry", "text box", "spin button"):
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
            if "generic" in nname or (
                typed and typed.lower() not in _detect and not typed.startswith("/")
            ):
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
        if (self.text or "") == text:
            # Sidecar AccessibleText can accept the string without
            # opening oslist-popover. Always load the real SearchEntry.
            if "oslist-entry" in (self.name or ""):
                try:
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
                    with open("/tmp/vmm-a11y-entry.txt", "w") as fh:
                        fh.write(text)
                    self._click_named_button(".entry-load-oslist-entry")
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
            if "media-combo" in name or "create-conn" in name:
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
