# Copyright (C) 2026 virt-manager GTK4/Adwaita port
#
# This work is licensed under the GNU GPLv2 or later.
# See the COPYING file in the top-level directory.

"""
GTK 4 + libadwaita compatibility helpers for virt-manager.

Registers GTK3 widget types that were removed in GTK4 so existing .ui
files and Python still instantiate equivalent GTK4 widgets, and provides
event/dialog/file-chooser helpers that preserve the original feature set.
"""

import os

from gi.repository import Gdk
from gi.repository import Gio
from gi.repository import GLib
from gi.repository import GObject
from gi.repository import Gtk

try:
    from gi.repository import Adw
except ImportError:  # pragma: no cover
    Adw = None


def set_accessible_name(widget, name):
    if not widget or name is None:
        return
    widget.update_property([Gtk.AccessibleProperty.LABEL], [str(name)])
    widget.set_name(str(name))


def _mnemonic_label(text):
    if not text:
        return ""
    return str(text).replace("_", "", 1)


def attach_menubar_submenus(widget):
    if widget is None or type(widget).__name__ not in ("MenuBar", "GtkMenuBar"):
        return
    for child in get_children(widget):
        if hasattr(widget, "_attach_submenu"):
            widget._attach_submenu(child)


def sync_builder_accessible(widget):
    """
    GTK 4 often exposes tooltip text as the AT-SPI name for icon buttons.
    Prefer the widget label so dogtail lookups match the GTK 3 names.
    """
    if widget is None or not isinstance(widget, Gtk.Widget):
        return
    label = None
    if hasattr(widget, "get_label"):
        try:
            label = widget.get_label()
        except TypeError:
            label = None
    name = _mnemonic_label(label)
    if name:
        set_accessible_name(widget, name)


def get_accessible_name(widget):
    return widget.get_name()


class _Accessible:
    def __init__(self, widget):
        self._widget = widget

    def set_name(self, name):
        set_accessible_name(self._widget, name)

    def get_name(self):
        return self._widget.get_name()


def get_children(widget):
    children = []
    child = widget.get_first_child() if hasattr(widget, "get_first_child") else None
    while child:
        children.append(child)
        child = child.get_next_sibling()
    return children


def container_add(parent, child):
    if child is None:
        return
    if child.get_parent() is parent:
        return
    if child.get_parent() is not None:
        child.unparent()
    # Prefer set_child for GTK4 bin widgets (ScrolledWindow, Viewport, ...)
    # even when they also expose a leftover append() from Gtk.Widget.
    if type(parent).__name__ in (
        "ScrolledWindow",
        "Viewport",
        "Revealer",
        "Overlay",
        "Frame",
        "Expander",
        "Window",
        "ApplicationWindow",
        "Popover",
        "AspectFrame",
        "Dialog",
    ) and hasattr(parent, "set_child"):
        parent.set_child(child)
        return
    if hasattr(parent, "append") and not isinstance(parent, Gtk.Grid):
        try:
            parent.append(child)
            return
        except TypeError:
            pass
    if hasattr(parent, "set_child"):
        parent.set_child(child)
        return
    if isinstance(parent, Gtk.Grid):
        parent.attach(child, 0, 0, 1, 1)
        return
    raise TypeError("Cannot add child to %s" % type(parent))


def container_remove(parent, child):
    if hasattr(parent, "remove"):
        parent.remove(child)
    elif hasattr(parent, "set_child"):
        parent.set_child(None)


def show_all(widget):
    if isinstance(widget, Gtk.Popover):
        return
    widget.set_visible(True)
    # ComboBox popovers crash if realized outside a toplevel
    if isinstance(widget, Gtk.ComboBox):
        return
    for child in get_children(widget):
        if isinstance(child, Gtk.Popover):
            continue
        show_all(child)


class _FakeEvent:
    def __init__(self, button=0, keyval=0, hardware_keycode=0, state=0, x=0, y=0, type=None):
        self.button = button
        self.keyval = keyval
        self.hardware_keycode = hardware_keycode
        self.state = state
        self.x = x
        self.y = y
        self.type = type


def _widget_get_accessible(self):
    return _Accessible(self)


def _widget_show_all(self):
    show_all(self)


def _widget_get_children(self):
    return get_children(self)


def _widget_add(self, child):
    container_add(self, child)


def _widget_modify_bg(self, _state=None, color=None):
    r = g = b = 0
    if color is not None:
        r = getattr(color, "red", 0) or 0
        g = getattr(color, "green", 0) or 0
        b = getattr(color, "blue", 0) or 0
        if r > 1 or g > 1 or b > 1:
            r, g, b = r / 65535.0, g / 65535.0, b / 65535.0
    css = ".vmm-modify-bg { background-color: rgb(%d,%d,%d); }" % (
        int(r * 255),
        int(g * 255),
        int(b * 255),
    )
    self.add_css_class("vmm-modify-bg")
    provider = Gtk.CssProvider()
    provider.load_from_data(css.encode("utf-8"))
    self.get_style_context().add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def _widget_get_window(self):
    native = self.get_native() if hasattr(self, "get_native") else None
    if native is not None and hasattr(native, "get_surface"):
        surface = native.get_surface()
        if surface is not None:
            return surface
    return self


def _widget_get_pointer(self):
    if hasattr(self, "_last_xy"):
        return self._last_xy
    return (0, 0)


def _get_current_event():
    return _FakeEvent()


class _EntryIconPosition:
    PRIMARY = 0
    SECONDARY = 1


def _box_pack_start(self, child, expand=True, fill=True, padding=0):
    ignore = fill
    if child.get_parent() is not None:
        child.unparent()
    if expand:
        if self.get_orientation() == Gtk.Orientation.VERTICAL:
            child.set_vexpand(True)
        else:
            child.set_hexpand(True)
    if padding:
        child.set_margin_start(child.get_margin_start() + padding)
        child.set_margin_end(child.get_margin_end() + padding)
    self.append(child)


def _box_pack_end(self, child, expand=True, fill=True, padding=0):
    ignore = fill
    if child.get_parent() is not None:
        child.unparent()
    if expand:
        if self.get_orientation() == Gtk.Orientation.VERTICAL:
            child.set_vexpand(True)
        else:
            child.set_hexpand(True)
    if padding:
        child.set_margin_start(child.get_margin_start() + padding)
        child.set_margin_end(child.get_margin_end() + padding)
    self.append(child)


def _run_modal(window, response_signal="response"):
    result = [Gtk.ResponseType.CLOSE]
    loop = GLib.MainLoop()

    def on_response(_src, response=None):
        if response is not None:
            result[0] = response
        if loop.is_running():
            loop.quit()

    def on_close(_src, *_args):
        if loop.is_running():
            loop.quit()
        return False

    hid = None
    if GObject.signal_lookup(response_signal, window):
        hid = window.connect(response_signal, on_response)
    close_hid = None
    if GObject.signal_lookup("close-request", window):
        close_hid = window.connect("close-request", on_close)
    window.present()
    loop.run()
    if hid is not None:
        window.disconnect(hid)
    if close_hid is not None:
        window.disconnect(close_hid)
    return result[0]


def run_dialog(dialog):
    return _run_modal(dialog)


def choose_alert(parent, heading, body="", responses=None, extra_child=None, default=None):
    """
    Synchronous Adw.AlertDialog. responses is [(id, label, appearance), ...]
    Returns the response id string.
    """
    if Adw is None:  # pragma: no cover
        raise RuntimeError("libadwaita is required")

    dialog = Adw.AlertDialog(heading=heading, body=body or "")
    dialog.set_accessible_role(Gtk.AccessibleRole.ALERT)
    if extra_child is not None:
        dialog.set_extra_child(extra_child)

    responses = responses or [("close", "Close", None)]
    for resp_id, label, appearance in responses:
        dialog.add_response(resp_id, label)
        if appearance is not None:
            dialog.set_response_appearance(resp_id, appearance)
    if default:
        dialog.set_default_response(default)

    result = [responses[-1][0]]
    loop = GLib.MainLoop()

    def _done(dlg, async_result):
        try:
            result[0] = dlg.choose_finish(async_result)
        except Exception:  # pragma: no cover
            result[0] = "close"
        loop.quit()

    dialog.choose(parent, None, _done)
    loop.run()
    return result[0]


def browse_local(
    parent,
    dialog_name,
    start_folder=None,
    _type=None,
    dialog_type=None,
    choose_label=None,
    default_name=None,
    confirm_overwrite=False,
):
    if dialog_type is None:
        dialog_type = Gtk.FileChooserAction.OPEN

    dialog = Gtk.FileDialog()
    dialog.set_title(dialog_name)
    if choose_label:
        dialog.set_accept_label(choose_label)
    if default_name:
        dialog.set_initial_name(default_name)

    if _type is not None:
        pattern = _type
        name = None
        if isinstance(_type, tuple):
            pattern = _type[0]
            name = _type[1]
        filt = Gtk.FileFilter()
        filt.add_pattern("*." + pattern)
        if name:
            filt.set_name(name)
        dialog.set_default_filter(filt)

    if start_folder and os.access(start_folder, os.R_OK):
        dialog.set_initial_folder(GioFile_for_path(start_folder))

    result = [None]
    loop = GLib.MainLoop()

    def _finish(dlg, async_result, opener):
        try:
            gfile = opener(dlg, async_result)
            result[0] = gfile.get_path() if gfile else None
        except Exception:
            result[0] = None
        loop.quit()

    if dialog_type == Gtk.FileChooserAction.SAVE:
        dialog.set_initial_name(default_name or "")
        dialog.save(parent, None, lambda d, r: _finish(d, r, Gtk.FileDialog.save_finish))
    elif dialog_type == Gtk.FileChooserAction.SELECT_FOLDER:
        dialog.select_folder(
            parent, None, lambda d, r: _finish(d, r, Gtk.FileDialog.select_folder_finish)
        )
    else:
        dialog.open(parent, None, lambda d, r: _finish(d, r, Gtk.FileDialog.open_finish))

    ignore = confirm_overwrite
    loop.run()
    return result[0]


def GioFile_for_path(path):
    return Gio.File.new_for_path(path)


############################################
# Custom GTypes for removed GTK3 widgets   #
############################################


class MenuItem(Gtk.Button):
    __gtype_name__ = "GtkMenuItem"

    use_underline = GObject.Property(type=bool, default=True)
    label = GObject.Property(type=str, default="")

    def __init__(self, label=None, **kwargs):
        kwargs.setdefault("has_frame", False)
        super().__init__(**kwargs)
        self.set_halign(Gtk.Align.FILL)
        self.set_hexpand(True)
        self.add_css_class("flat")
        self.set_accessible_role(Gtk.AccessibleRole.MENU_ITEM)
        self._label_widget = Gtk.Label(xalign=0, use_underline=True)
        self._submenu = None
        self._submenu_btn = None
        if label or self.label:
            self.set_label(label or self.label)
        self.set_child(self._label_widget)
        self.connect("clicked", self._on_clicked)
        self.connect("notify::label", self._on_label_prop)
        self.vmm_widget_name = None
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", self._on_pointer_enter)
        motion.connect("leave", self._on_pointer_leave)
        self.add_controller(motion)

    def _set_selected(self, selected):
        self.update_state([Gtk.AccessibleState.SELECTED], [bool(selected)])

    def _on_pointer_enter(self, *_args):
        self._set_selected(True)

    def _on_pointer_leave(self, *_args):
        self._set_selected(False)

    def _sync_accessible_label(self):
        text = ""
        if self._label_widget is not None:
            text = self._label_widget.get_text() or ""
        if not text:
            text = (self.label or "").replace("_", "", 1)
        if text:
            set_accessible_name(self, text)
        if not self._submenu:
            self.set_accessible_role(Gtk.AccessibleRole.MENU_ITEM)

    def _on_label_prop(self, *_args):
        if self.label:
            self._label_widget.set_text_with_mnemonic(self.label)
            self._sync_accessible_label()

    def _on_clicked(self, *_args):
        self._set_selected(True)
        if self._submenu:
            self._submenu.popup_at_widget(self)
        else:
            self.emit("activate")

    @classmethod
    def new_with_mnemonic(cls, label):
        return cls(label=label)

    @classmethod
    def new_with_label(cls, label):
        item = cls(label=label)
        item._label_widget.set_use_underline(False)
        return item

    def set_label(self, text):
        self.label = text or ""
        self._label_widget.set_text_with_mnemonic(text or "")
        self._sync_accessible_label()

    def do_add_child(self, builder, child, type_name):
        ignore = builder
        if type_name == "submenu" or isinstance(child, Menu):
            self.set_submenu(child)
            return
        Gtk.Button.set_child(self, child)

    def get_label(self):
        return self._label_widget.get_text()

    def get_child(self):
        return self._label_widget

    def set_use_underline(self, val):
        self.use_underline = bool(val)
        self._label_widget.set_use_underline(bool(val))

    def set_submenu(self, menu):
        self._submenu = menu
        if menu is not None:
            self.set_accessible_role(Gtk.AccessibleRole.MENU)
        else:
            self.set_accessible_role(Gtk.AccessibleRole.MENU_ITEM)
            # Do not parent the menu onto the item: GTK 4 would concatenate
            # every submenu label into this item's accessible name.
            if menu.get_parent() is self:
                menu.unparent()
            parent = self.get_parent()
            if isinstance(parent, MenuBar):
                parent._attach_submenu(self)
        self._sync_accessible_label()

    def get_submenu(self):
        return self._submenu

    def set_child(self, child):
        if isinstance(child, Menu):
            self.set_submenu(child)
            return
        Gtk.Button.set_child(self, child)

    def set_sensitive(self, val):
        Gtk.Button.set_sensitive(self, val)


class CheckMenuItem(Gtk.CheckButton):
    __gtype_name__ = "GtkCheckMenuItem"

    use_underline = GObject.Property(type=bool, default=True)
    draw_as_radio = GObject.Property(type=bool, default=False)

    def __init__(self, label=None, **kwargs):
        super().__init__(**kwargs)
        self.set_accessible_role(Gtk.AccessibleRole.MENU_ITEM)
        self.vmm_widget_name = None
        if label:
            self.set_label(label)
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_a: self.update_state([Gtk.AccessibleState.SELECTED], [True]))
        motion.connect("leave", lambda *_a: self.update_state([Gtk.AccessibleState.SELECTED], [False]))
        self.add_controller(motion)

    @classmethod
    def new_with_mnemonic(cls, label):
        return cls(label=label)

    def get_child(self):
        return self


class RadioMenuItem(CheckMenuItem):
    __gtype_name__ = "GtkRadioMenuItem"

    def __init__(self, label=None, **kwargs):
        super().__init__(label=label, **kwargs)
        self.set_accessible_role(Gtk.AccessibleRole.RADIO)

    def join_group(self, other):
        self.set_group(other)


class ImageMenuItem(MenuItem):
    __gtype_name__ = "GtkImageMenuItem"

    def __init__(self, label=None, **kwargs):
        super().__init__(label=label, **kwargs)
        self._image = Gtk.Image()
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.append(self._image)
        box.append(self._label_widget)
        self.set_child(box)

    @classmethod
    def new_with_label(cls, label):
        return cls(label=label)

    @classmethod
    def new_with_mnemonic(cls, label):
        return cls(label=label)

    @classmethod
    def new_from_stock(cls, stock, _accel=None):
        label, icon = _stock_to_label_icon(stock)
        item = cls(label=label)
        if icon:
            item._image.set_from_icon_name(icon)
        return item

    def get_child(self):
        return self._label_widget


class SeparatorMenuItem(Gtk.Separator):
    __gtype_name__ = "GtkSeparatorMenuItem"

    def __init__(self, **kwargs):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, **kwargs)
        self.vmm_widget_name = None

    def get_submenu(self):
        return None

    def set_submenu(self, _menu):
        return None


class Menu(Gtk.Box):
    __gtype_name__ = "GtkMenu"

    def __init__(self, **kwargs):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0, **kwargs)
        self.set_accessible_role(Gtk.AccessibleRole.MENU)
        self.add_css_class("vmm-submenu")
        self._items = []
        self._popover = None
        self._parent_widget = None

    def add(self, item):
        self.insert(item, -1)

    def append(self, item):
        self.add(item)

    def insert(self, item, position):
        if item.get_parent() is not None:
            item.unparent()
        if position < 0 or position >= len(self._items):
            Gtk.Box.append(self, item)
            self._items.append(item)
        else:
            sibling = self._items[position]
            self.insert_child_after(item, sibling.get_prev_sibling())
            self._items.insert(position, item)
        item.set_visible(True)

    def remove(self, item):
        if item in self._items:
            self._items.remove(item)
        Gtk.Box.remove(self, item)

    def get_children(self):
        kids = get_children(self)
        if kids:
            return kids
        return list(self._items)

    def show_all(self):
        show_all(self)
        for item in self._items:
            show_all(item)

    def _ensure_popover(self, parent):
        # Use a transient undecorated window so AT-SPI can see menu items.
        # Gtk.Popover often exposes only empty panels to dogtail.
        if self._popover is None:
            self._popover = Gtk.Window()
            self._popover.set_decorated(False)
            self._popover.set_resizable(False)
            self._popover.set_transient_for(parent.get_root() if parent else None)
            self._popover.set_accessible_role(Gtk.AccessibleRole.MENU)
            self._popover.add_css_class("menu")
        if self.get_parent() is not None and self.get_parent() != self._popover:
            self.unparent()
        if self._popover.get_child() is not self:
            self._popover.set_child(self)
        self._parent_widget = parent
        self.remove_css_class("vmm-submenu")
        show_all(self)
        for item in self._items:
            show_all(item)
            if hasattr(item, "_sync_accessible_label"):
                item._sync_accessible_label()

    def popup(self, *_args, **_kwargs):
        parent = self._parent_widget
        if parent is None:
            return
        self._ensure_popover(parent)
        try:
            self._popover.present()
        except Exception:
            pass

    def popup_at_pointer(self, event=None):
        ignore = event
        self.popup()

    def popup_at_widget(self, widget):
        self._ensure_popover(widget)
        self.popup()

    def popup_at_rect(self, _window, rect, _g1=None, _g2=None, _event=None):
        self._ensure_popover(self._parent_widget)
        if self._popover:
            self._popover.set_pointing_to(rect)
        self.popup()

    def get_accessible(self):
        return _Accessible(self)


class MenuBar(Gtk.Box):
    __gtype_name__ = "GtkMenuBar"

    def __init__(self, **kwargs):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, **kwargs)
        self.add_css_class("menubar")
        self.set_accessible_role(Gtk.AccessibleRole.MENU_BAR)
        self._items = []

    def add(self, item):
        self.append(item)
        self._items.append(item)
        self._attach_submenu(item)

    def _attach_submenu(self, item):
        # Keep submenu widgets in the menubar tree so AT-SPI/dogtail can
        # find "About" / "Add Connection..." without relying on a popup.
        submenu = item.get_submenu() if hasattr(item, "get_submenu") else None
        if submenu is None:
            return
        if submenu.get_parent() is not None and submenu.get_parent() is not self:
            submenu.unparent()
        if submenu.get_parent() is None:
            self.append(submenu)
        submenu.set_visible(True)
        show_all(submenu)

    def get_children(self):
        return get_children(self)

    def do_add(self, child):
        # Builder child packing
        self.append(child)
        self._items.append(child)
        self._attach_submenu(child)


class Toolbar(Gtk.Box):
    __gtype_name__ = "GtkToolbar"

    show_arrow = GObject.Property(type=bool, default=False)
    toolbar_style = GObject.Property(type=int, default=0)
    icon_size = GObject.Property(type=int, default=0)

    def __init__(self, **kwargs):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, **kwargs)
        self.add_css_class("toolbar")

    def set_style(self, *_args):
        pass

    def set_show_arrow(self, *_args):
        pass

    def add(self, child):
        self.append(child)

    def get_children(self):
        return get_children(self)


class ToolButton(Gtk.Button):
    __gtype_name__ = "GtkToolButton"

    is_important = GObject.Property(type=bool, default=False)
    use_underline = GObject.Property(type=bool, default=True)
    icon_name = GObject.Property(type=str, default="")
    label = GObject.Property(type=str, default="")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)


class ToggleToolButton(Gtk.ToggleButton):
    __gtype_name__ = "GtkToggleToolButton"

    is_important = GObject.Property(type=bool, default=False)
    use_underline = GObject.Property(type=bool, default=True)
    icon_name = GObject.Property(type=str, default="")
    label = GObject.Property(type=str, default="")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)


class RadioToolButton(Gtk.ToggleButton):
    __gtype_name__ = "GtkRadioToolButton"

    is_important = GObject.Property(type=bool, default=False)
    use_underline = GObject.Property(type=bool, default=True)
    icon_name = GObject.Property(type=str, default="")
    label = GObject.Property(type=str, default="")
    group = GObject.Property(type=str, default="")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_accessible_role(Gtk.AccessibleRole.RADIO)


class MenuToolButton(Gtk.Box):
    __gtype_name__ = "GtkMenuToolButton"

    is_important = GObject.Property(type=bool, default=False)
    use_underline = GObject.Property(type=bool, default=True)
    icon_name = GObject.Property(type=str, default="")
    label = GObject.Property(type=str, default="")
    has_tooltip = GObject.Property(type=bool, default=False)

    def __init__(self, **kwargs):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, **kwargs)
        self._button = Gtk.Button()
        self._menu_button = Gtk.MenuButton()
        self._button.set_hexpand(True)
        self.append(self._button)
        self.append(self._menu_button)
        self._menu = None
        self.connect("notify::label", self._sync_label)
        self.connect("notify::icon-name", self._sync_icon)
        self._button.connect("clicked", lambda *_a: self.emit("clicked"))

    def _sync_label(self, *_args):
        self._button.set_label(self.label)

    def _sync_icon(self, *_args):
        if self.icon_name:
            self._button.set_icon_name(self.icon_name)

    def set_icon_name(self, name):
        self.icon_name = name or ""
        self._button.set_icon_name(name)

    def set_label(self, label):
        self.label = label or ""
        self._button.set_label(label)

    def set_menu(self, menu):
        self._menu = menu
        if menu is None:
            self._menu_button.set_popover(None)
            return
        if isinstance(menu, Gtk.Popover):
            self._menu_button.set_popover(menu)
            return
        popover = Gtk.Popover()
        popover.set_has_arrow(False)
        if menu.get_parent() is not None:
            menu.unparent()
        popover.set_child(menu)
        self._menu_button.set_popover(popover)

    def get_menu(self):
        return self._menu

    def set_sensitive(self, val):
        Gtk.Box.set_sensitive(self, val)
        self._button.set_sensitive(val)
        self._menu_button.set_sensitive(val)


class SeparatorToolItem(Gtk.Separator):
    __gtype_name__ = "GtkSeparatorToolItem"

    homogeneous = GObject.Property(type=bool, default=False)

    def __init__(self, **kwargs):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, **kwargs)


class EventBox(Gtk.Box):
    __gtype_name__ = "GtkEventBox"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._last_xy = (0, 0)
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", self._on_motion)
        motion.connect("motion", self._on_motion)
        motion.connect("leave", self._on_leave)
        self.add_controller(motion)
        self._entered = False

    def _on_motion(self, _c, x=0, y=0):
        self._last_xy = (x, y)
        self._entered = True

    def _on_leave(self, *_args):
        self._entered = False

    def add(self, child):
        self.append(child)

    def get_pointer(self):
        if not self._entered:
            return (-1, -1)
        return self._last_xy


class ButtonBox(Gtk.Box):
    __gtype_name__ = "GtkButtonBox"

    layout_style = GObject.Property(type=int, default=0)

    def __init__(self, **kwargs):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, **kwargs)
        self.set_halign(Gtk.Align.END)


class VBox(Gtk.Box):
    __gtype_name__ = "GtkVBox"

    def __init__(self, homogeneous=False, spacing=0, **kwargs):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=spacing or 0, **kwargs)
        self.set_homogeneous(bool(homogeneous))


class HBox(Gtk.Box):
    __gtype_name__ = "GtkHBox"

    def __init__(self, homogeneous=False, spacing=0, **kwargs):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=spacing or 0, **kwargs)
        self.set_homogeneous(bool(homogeneous))


class Alignment(Gtk.Box):
    __gtype_name__ = "GtkAlignment"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)


def _stock_to_label_icon(stock):
    mapping = {
        "gtk-ok": (_("_OK"), "emblem-ok-symbolic"),
        "gtk-cancel": (_("_Cancel"), "window-close-symbolic"),
        "gtk-add": (_("_Add"), "list-add-symbolic"),
        "gtk-quit": (_("_Quit"), "application-exit-symbolic"),
        "gtk-connect": (_("_Connect"), "network-transmit-receive-symbolic"),
        "gtk-disconnect": (_("_Disconnect"), "network-offline-symbolic"),
        "gtk-close": (_("_Close"), "window-close-symbolic"),
        "gtk-yes": (_("_Yes"), None),
        "gtk-no": (_("_No"), None),
    }
    if stock in mapping:
        return mapping[stock]
    return (str(stock), None)


def _bin_remove(self, child=None):
    current = self.get_child() if hasattr(self, "get_child") else None
    if child is None or current is child or child is current:
        if hasattr(self, "set_child"):
            self.set_child(None)
            return
    if child is not None and child.get_parent() is self:
        child.unparent()


def _patch_bin_add(cls):
    if cls is None:
        return
    cls.add = _widget_add
    orig_remove = getattr(cls, "remove", None)

    def remove(self, child=None):
        if orig_remove is not None and child is not None:
            try:
                return orig_remove(self, child)
            except TypeError:
                pass
        return _bin_remove(self, child)

    cls.remove = remove


def _patch_widget_methods():
    Gtk.Widget.get_accessible = _widget_get_accessible
    Gtk.Widget.show_all = _widget_show_all
    Gtk.Widget.get_children = _widget_get_children
    Gtk.Widget.modify_bg = _widget_modify_bg
    Gtk.Widget.get_window = _widget_get_window
    Gtk.Widget.get_pointer = _widget_get_pointer

    orig_add = getattr(Gtk.Box, "add", None)
    ignore = orig_add
    Gtk.Box.add = _widget_add
    Gtk.Box.pack_start = _box_pack_start
    Gtk.Box.pack_end = _box_pack_end
    Gtk.Box.get_children = _widget_get_children
    orig_box_append = Gtk.Box.append

    def box_append(self, child):
        if child is not None and child.get_parent() is not None:
            child.unparent()
        return orig_box_append(self, child)

    Gtk.Box.append = box_append

    for clsname in (
        "ScrolledWindow",
        "Viewport",
        "Revealer",
        "Overlay",
        "Frame",
        "Expander",
        "Window",
        "ApplicationWindow",
        "Popover",
        "AspectFrame",
        "Dialog",
        "MessageDialog",
    ):
        _patch_bin_add(getattr(Gtk, clsname, None))

    if not hasattr(Gtk.Window, "get_position"):

        def get_position(self):
            return (0, 0)

        Gtk.Window.get_position = get_position

    if not hasattr(Gtk.Window, "move"):

        def move(self, *_args):
            return None

        Gtk.Window.move = move

    if not hasattr(Gtk.Window, "get_size"):

        def get_size(self):
            return (self.get_width(), self.get_height())

        Gtk.Window.get_size = get_size

    def set_border_width(self, width):
        self.set_margin_top(width)
        self.set_margin_bottom(width)
        self.set_margin_start(width)
        self.set_margin_end(width)

    Gtk.Widget.set_border_width = set_border_width

    if not hasattr(Gtk.Label, "set_line_wrap"):

        def set_line_wrap(self, wrap):
            self.set_wrap(bool(wrap))

        Gtk.Label.set_line_wrap = set_line_wrap

    if not hasattr(Gtk.Label, "set_line_wrap_mode"):

        def set_line_wrap_mode(self, mode):
            self.set_wrap_mode(mode)

        Gtk.Label.set_line_wrap_mode = set_line_wrap_mode

    _orig_label_new = Gtk.Label.__new__
    _orig_label_init = Gtk.Label.__init__

    def label_init(self, text=None, **kwargs):
        if text is not None and "label" not in kwargs:
            kwargs["label"] = text
        return _orig_label_init(self, **kwargs)

    Gtk.Label.__init__ = label_init

    def grab_default(self):
        root = self.get_root() if hasattr(self, "get_root") else None
        if root is not None and hasattr(root, "set_default_widget"):
            root.set_default_widget(self)
        if hasattr(self, "set_receives_default"):
            self.set_receives_default(True)

    Gtk.Widget.grab_default = grab_default

    if not hasattr(Gtk.Widget, "destroy"):

        def destroy(self):
            parent = self.get_parent()
            if parent is not None:
                self.unparent()
            self.run_dispose()

        Gtk.Widget.destroy = destroy

    if not hasattr(Gtk.Widget, "get_allocation"):

        class _Alloc:
            def __init__(self, widget):
                self.x = 0
                self.y = 0
                self.width = widget.get_width()
                self.height = widget.get_height()

        def get_allocation(self):
            return _Alloc(self)

        Gtk.Widget.get_allocation = get_allocation

    def resize(self, width, height):
        self.set_default_size(max(1, int(width)), max(1, int(height)))

    Gtk.Window.resize = resize

    def set_type_hint(self, *_args):
        return None

    Gtk.Window.set_type_hint = set_type_hint

    def add_accel_group(self, *_args):
        return None

    def remove_accel_group(self, *_args):
        return None

    Gtk.Window.add_accel_group = add_accel_group
    Gtk.Window.remove_accel_group = remove_accel_group

    def window_remove(self, child):
        if hasattr(self, "get_child") and self.get_child() is child:
            self.set_child(None)
            return
        if child is not None and child.get_parent() is self:
            child.unparent()

    Gtk.Window.remove = window_remove

    def set_relative_to(self, widget):
        parent = self.get_parent()
        if parent is not None and parent is not widget:
            self.unparent()
        if self.get_parent() is None and widget is not None:
            self.set_parent(widget)

    Gtk.Popover.set_relative_to = set_relative_to

    def _entry_set_icon_from_icon_name(self, _pos, _name):
        return None

    def _entry_set_icon_activatable(self, _pos, _val):
        return None

    Gtk.Entry.set_icon_from_icon_name = _entry_set_icon_from_icon_name
    Gtk.Entry.set_icon_activatable = _entry_set_icon_activatable

    orig_set_from_icon_name = Gtk.Image.set_from_icon_name

    def set_from_icon_name(self, name, _size=None):
        return orig_set_from_icon_name(self, name)

    Gtk.Image.set_from_icon_name = set_from_icon_name

    orig_new_from_icon_name = Gtk.Image.new_from_icon_name

    def new_from_icon_name(name, _size=None):
        return orig_new_from_icon_name(name)

    Gtk.Image.new_from_icon_name = staticmethod(new_from_icon_name)

    def new_from_stock(stock):
        label, icon = _stock_to_label_icon(stock)
        btn = Gtk.Button(label=label, use_underline=True)
        if icon:
            btn.set_icon_name(icon)
        return btn

    Gtk.Button.new_from_stock = staticmethod(new_from_stock)

    orig_dialog_run = getattr(Gtk.Dialog, "run", None)
    ignore = orig_dialog_run
    Gtk.Dialog.run = run_dialog

    if hasattr(Gtk, "NativeDialog"):
        Gtk.NativeDialog.run = run_dialog

    if not hasattr(Gtk.Dialog, "add_button"):

        def add_button(self, label, response):
            btn = Gtk.Button(label=label, use_underline=True)
            self.add_action_widget(btn, response)
            btn.set_visible(True)
            return btn

        Gtk.Dialog.add_button = add_button

    def add_buttons(self, *args):
        for idx in range(0, len(args), 2):
            self.add_button(args[idx], args[idx + 1])

    Gtk.Dialog.add_buttons = add_buttons

    def format_secondary_text(self, text):
        self.set_property("secondary-text", text or "")

    Gtk.MessageDialog.format_secondary_text = format_secondary_text

    # FileChooser path helpers
    if hasattr(Gtk, "FileChooser"):

        def get_filename(self):
            gfile = self.get_file()
            return gfile.get_path() if gfile else None

        def set_current_folder(self, path):
            if path:
                self.set_current_folder(GioFile_for_path(path) if isinstance(path, str) else path)

        Gtk.FileChooser.get_filename = get_filename

    orig_connect = Gtk.Widget.connect

    def connect(self, signal, callback, *args):
        if signal == "delete-event":
            return orig_connect(self, "close-request", lambda w: callback(w, None, *args) or False)
        if signal == "size-allocate":
            last = [None]

            def _tick(w, _clock):
                alloc = (w.get_width(), w.get_height())
                if alloc != last[0] and alloc[0] > 0 and alloc[1] > 0:
                    last[0] = alloc
                    callback(w, w.get_allocation() if hasattr(w, "get_allocation") else None, *args)
                return True

            return self.add_tick_callback(_tick)
        if signal == "configure-event":

            def _on_notify(w, *_a):
                callback(w, None, *args)

            return orig_connect(self, "notify::default-width", _on_notify)
        if signal == "button-press-event":
            gesture = Gtk.GestureClick()
            gesture.set_button(0)

            def _pressed(gest, _n, x, y):
                button = gest.get_current_button()
                ev = _FakeEvent(button=button, x=x, y=y)
                callback(self, ev, *args)

            gesture.connect("pressed", _pressed)
            self.add_controller(gesture)
            return id(gesture)
        if signal in ("key-press-event", "key-release-event"):
            controller = Gtk.EventControllerKey()
            sig = "key-pressed" if signal == "key-press-event" else "key-released"

            def _key(_c, keyval, keycode, state):
                ev = _FakeEvent(keyval=keyval, hardware_keycode=keycode, state=state)
                callback(self, ev, *args)
                return False

            controller.connect(sig, _key)
            self.add_controller(controller)
            return id(controller)
        if signal == "icon-press":

            def _icon(*_a):
                callback(self, Gtk.EntryIconPosition.SECONDARY, _FakeEvent(), *args)

            return orig_connect(self, "activate", _icon)
        if signal in ("enter-notify-event", "leave-notify-event"):
            controller = Gtk.EventControllerMotion()
            evname = "enter" if signal == "enter-notify-event" else "leave"

            def _motion(*_a):
                callback(self, _FakeEvent(), *args)

            controller.connect(evname, _motion)
            self.add_controller(controller)
            return id(controller)
        return orig_connect(self, signal, callback, *args)

    Gtk.Widget.connect = connect


def _install_stock_and_enums():
    Gtk.STOCK_OK = "gtk-ok"
    Gtk.STOCK_CANCEL = "gtk-cancel"
    Gtk.STOCK_ADD = "gtk-add"
    Gtk.STOCK_QUIT = "gtk-quit"
    Gtk.STOCK_CONNECT = "gtk-connect"
    Gtk.STOCK_DISCONNECT = "gtk-disconnect"
    Gtk.STOCK_CLOSE = "gtk-close"
    Gtk.STOCK_YES = "gtk-yes"
    Gtk.STOCK_NO = "gtk-no"

    class _DialogFlags:
        MODAL = 1
        DESTROY_WITH_PARENT = 2

    Gtk.DialogFlags = _DialogFlags

    # IconSize aliases used by virt-manager
    if not hasattr(Gtk.IconSize, "LARGE_TOOLBAR"):
        Gtk.IconSize.LARGE_TOOLBAR = Gtk.IconSize.LARGE
    if not hasattr(Gtk.IconSize, "DND"):
        Gtk.IconSize.DND = Gtk.IconSize.LARGE
    if not hasattr(Gtk.IconSize, "BUTTON"):
        Gtk.IconSize.BUTTON = Gtk.IconSize.NORMAL
    if not hasattr(Gtk.IconSize, "MENU"):
        Gtk.IconSize.MENU = Gtk.IconSize.NORMAL

    if not hasattr(Gtk, "ToolbarStyle"):

        class ToolbarStyle:
            BOTH_HORIZ = 0
            ICONS = 1
            TEXT = 2

        Gtk.ToolbarStyle = ToolbarStyle

    orig_icon_theme = Gtk.IconTheme.get_for_display

    def get_default():
        display = Gdk.Display.get_default()
        return orig_icon_theme(display)

    Gtk.IconTheme.get_default = staticmethod(get_default)

    if not hasattr(Gtk.IconTheme, "prepend_search_path"):
        Gtk.IconTheme.prepend_search_path = Gtk.IconTheme.add_search_path

    # Cursor helper: GTK3 was new_from_name(display, name)
    orig_cursor = Gdk.Cursor.new_from_name

    def new_from_name(*args):
        name = args[-1]
        try:
            return orig_cursor(name)
        except TypeError:
            return orig_cursor(*args)

    Gdk.Cursor.new_from_name = staticmethod(new_from_name)

    if not hasattr(Gdk, "Screen"):

        class _Screen:
            @staticmethod
            def get_default():
                return Gdk.Display.get_default()

        Gdk.Screen = _Screen

    if not hasattr(Gdk, "Color"):

        class Color:
            def __init__(self, red=0, green=0, blue=0):
                self.red = red
                self.green = green
                self.blue = blue

        Gdk.Color = Color

    if not hasattr(Gdk, "WindowTypeHint"):

        class WindowTypeHint:
            NORMAL = 0
            DIALOG = 1
            MENU = 2
            TOOLBAR = 3
            SPLASHSCREEN = 4
            UTILITY = 5
            DOCK = 6
            DESKTOP = 7

        Gdk.WindowTypeHint = WindowTypeHint

    if not hasattr(Gtk, "StateType"):

        class StateType:
            NORMAL = 0
            ACTIVE = 1
            PRELIGHT = 2
            SELECTED = 3
            INSENSITIVE = 4

        Gtk.StateType = StateType

    Gtk.EntryIconPosition = _EntryIconPosition
    Gtk.get_current_event = _get_current_event
    Gtk.accel_groups_from_object = lambda _obj: []

    if not hasattr(Gdk, "SELECTION_CLIPBOARD"):
        Gdk.SELECTION_CLIPBOARD = "CLIPBOARD"
    if not hasattr(Gdk, "SELECTION_PRIMARY"):
        Gdk.SELECTION_PRIMARY = "PRIMARY"

    if not hasattr(Gtk, "Clipboard"):

        class Clipboard:
            def __init__(self, display=None):
                self._display = display or Gdk.Display.get_default()
                self._clip = self._display.get_clipboard() if self._display else None

            @staticmethod
            def get(_selection=None):
                return Clipboard()

            @staticmethod
            def get_default(_display=None):
                return Clipboard(_display)

            def set_text(self, text, _length=-1):
                if self._clip is None:
                    return
                try:
                    self._clip.set(text or "")
                except Exception:
                    pass

            def wait_for_text(self):
                return None

        Gtk.Clipboard = Clipboard

    class VScrollbar(Gtk.Scrollbar):
        __gtype_name__ = "GtkVScrollbar"

        def __init__(self, adjustment=None, **kwargs):
            super().__init__(orientation=Gtk.Orientation.VERTICAL, **kwargs)
            if adjustment is not None:
                self.set_adjustment(adjustment)

    class HScrollbar(Gtk.Scrollbar):
        __gtype_name__ = "GtkHScrollbar"

        def __init__(self, adjustment=None, **kwargs):
            super().__init__(orientation=Gtk.Orientation.HORIZONTAL, **kwargs)
            if adjustment is not None:
                self.set_adjustment(adjustment)

    Gtk.VScrollbar = VScrollbar
    Gtk.HScrollbar = HScrollbar

    orig_settings_get = Gtk.Settings.get_property
    orig_settings_set = Gtk.Settings.set_property

    def settings_get_property(self, name):
        try:
            return orig_settings_get(self, name)
        except TypeError:
            if name == "gtk-menu-bar-accel":
                return "F10"
            if name == "gtk-enable-mnemonics":
                return True
            raise

    def settings_set_property(self, name, value):
        try:
            return orig_settings_set(self, name, value)
        except TypeError:
            if name in ("gtk-menu-bar-accel", "gtk-enable-mnemonics"):
                return None
            raise

    Gtk.Settings.get_property = settings_get_property
    Gtk.Settings.set_property = settings_set_property

    orig_accel_parse = Gtk.accelerator_parse

    def accelerator_parse(accel):
        ret = orig_accel_parse(accel)
        if isinstance(ret, tuple) and len(ret) == 3:
            return ret[1], ret[2]
        return ret

    Gtk.accelerator_parse = accelerator_parse

    def _emit_toggled(self):
        self.emit("toggled")

    Gtk.ToggleButton.toggled = _emit_toggled
    Gtk.CheckButton.toggled = _emit_toggled


def _install_css_helpers():
    orig_add_provider = getattr(Gtk.StyleContext, "add_provider_for_display", None)

    def add_provider_for_screen(screen, provider, priority):
        display = Gdk.Display.get_default()
        ignore = screen
        if orig_add_provider:
            orig_add_provider(display, provider, priority)

    Gtk.StyleContext.add_provider_for_screen = staticmethod(add_provider_for_screen)


def _install_menuitem_activate_signal():
    if not GObject.signal_lookup("activate", MenuItem):
        GObject.signal_new(
            "activate", MenuItem, GObject.SignalFlags.RUN_FIRST, None, []
        )
    if not GObject.signal_lookup("clicked", MenuToolButton):
        GObject.signal_new(
            "clicked", MenuToolButton, GObject.SignalFlags.RUN_FIRST, None, []
        )


def install():
    """
    Install GTK4 compatibility types and monkeypatches. Call after
    importing Gtk 4 and Adw.
    """
    _install_menuitem_activate_signal()
    _patch_widget_methods()
    _install_stock_and_enums()
    _install_css_helpers()

    Gtk.Menu = Menu
    Gtk.MenuItem = MenuItem
    Gtk.CheckMenuItem = CheckMenuItem
    Gtk.RadioMenuItem = RadioMenuItem
    Gtk.ImageMenuItem = ImageMenuItem
    Gtk.SeparatorMenuItem = SeparatorMenuItem
    Gtk.MenuBar = MenuBar
    Gtk.Toolbar = Toolbar
    Gtk.ToolButton = ToolButton
    Gtk.ToggleToolButton = ToggleToolButton
    Gtk.RadioToolButton = RadioToolButton
    Gtk.MenuToolButton = MenuToolButton
    Gtk.SeparatorToolItem = SeparatorToolItem
    Gtk.EventBox = EventBox
    Gtk.VBox = VBox
    Gtk.HBox = HBox
    Gtk.ButtonBox = ButtonBox
    Gtk.Alignment = Alignment

    # Assign module-level aliases used by Builder GTypes (already registered)
    return True
