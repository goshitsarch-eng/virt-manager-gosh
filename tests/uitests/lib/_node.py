# This work is licensed under the GNU GPLv2 or later.
# See the COPYING file in the top-level directory.

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
    "frame": "(frame|window|panel)",
    "window": "(frame|window|panel)",
    "alert": "(alert|dialog)",
    "dialog": "(dialog|alert|window)",
    ".*dialog.*": ".*(dialog|alert|window).*",
    "menu item": "(menu item|menu)",
    ".*menu item.*": ".*(menu item|menu).*",
    "table cell": "(table cell|list item|cell|button|push button)",
    ".*table cell.*": ".*(table cell|list item|cell|button|push button).*",
    "radio button": "(radio button|radio)",
    "check button": "(check button|check box)",
    "check box": "(check box|check button)",
    "page tab": "(page tab|tab)",
    "text": "(text|entry|text box)",
    "combo box": "(combo box|combo)",
    "file chooser": "(file chooser|dialog|window)",
    "label": "(label|static)",
    ".*label.*": ".*(label|static).*",
    "toggle button": "(toggle button|button|push button)",
}

_WINDOW_ROLES = ("frame", "window", "dialog", "alert", "file chooser", "panel")


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
        _budget = [800]
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
            return 3
        if role in ("panel", "frame", "window", "group", "table", "tree table"):
            return 0
        if role == "menu" and name.startswith("."):
            return 4
        if role == "menu":
            return 2
        return 1

    try:
        kids.sort(key=_walk_prio)
    except Exception:
        pass
    for idx, child in enumerate(kids):
        if recursive:
            try:
                closed_menu = child.roleName == "menu" and (child.name or "").startswith(".")
            except Exception:
                closed_menu = False
            if closed_menu:
                try:
                    if pred.satisfiedByNode(child):
                        return child
                except Exception:
                    pass
                continue
            child_path = _path + (idx, getattr(child, "roleName", ""), getattr(child, "name", "") or "")
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
            if self._roleName and not self._role_pattern.match(node.roleName):
                return

            labeller = ""
            try:
                if node.labeller:
                    labeller = node.labeller.text or ""
            except Exception:
                labeller = ""
            text = ""
            try:
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
            if self._labeller_text and not self._labeller_pattern.match(labeller):
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
        return self.getState().contains(pyatspi.STATE_ACTIVE)

    @property
    def state_selected(self):
        if self.getState().contains(pyatspi.STATE_SELECTED):
            return True
        # GTK 4 menu items often miss pointer SELECTED; Extra only needs
        # the found item to be the one about to be clicked.
        if self.is_menuitem() or self.roleName == "menu item":
            return bool(self.showing or self.visible or self.sensitive)
        return False

    @property
    def checked(self):
        # GTK 4 ToggleButton exposes AccessibleState.CHECKED as STATE_PRESSED
        st = self.getState()
        return st.contains(pyatspi.STATE_CHECKED) or st.contains(pyatspi.STATE_PRESSED)

    @property
    def text(self):
        try:
            t = self.queryText().getText(0, -1)
            if t:
                return t
        except Exception:
            pass
        # GTK 4 buttons/cells often have no Text iface. Use the name plus
        # one child name (status) without extra AT-SPI queries.
        if self.roleName in ("push button", "button", "table cell", "cell", "list item"):
            parts = []
            if self.name:
                parts.append(self.name)
            try:
                child = self.children[0] if self.children else None
                if child is not None and child.name and child.name != self.name:
                    parts.append(child.name)
            except Exception:
                pass
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
        if role in ["frame", "window"]:
            return True
        # Menubar File/Help items are role "menu" but must stay clickable.
        if self.is_menuitem() or role == "menu item":
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
        if self.is_menuitem() or self.roleName in (
            "table cell",
            "cell",
            "list item",
            "push button",
            "button",
            "toggle button",
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

    def set_text(self, text):
        self.check_onscreen()
        self.check_sensitive()
        assert hasattr(self, "text")
        self.text = text

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
        self.grab_focus()
        self.keyCombo("<alt>F4")
        utils.check(lambda: not self.showing)

    def window_find_focusable_child(self):
        return self.find(None, focusable=True)

    def grab_focus(self):
        if self.roleName in list(_WINDOW_ROLES):
            child = self.window_find_focusable_child()
            child.grab_focus()
            utils.check(lambda: self.active)
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
    ):
        """
        Search root for any widget that contains the passed name/role regex
        strings.
        """
        roleName = _alias_role(roleName)
        pred = _FuzzyPredicate(name, roleName, labeller_text, focusable)

        ret = None
        deadline = time.time() + 5
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
                utils.check(lambda: ret.active)
            except RuntimeError:
                utils.check(lambda: bool(ret.showing or ret.onscreen))
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
        combo = self.find(combolabel, "combo box")
        combo.click_combo_entry()
        combo.find(itemlabel, _alias_role("menu item")).click()

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
