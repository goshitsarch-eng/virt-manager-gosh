# Copyright (C) 2009, 2013, 2014 Red Hat, Inc.
# Copyright (C) 2009 Cole Robinson <crobinso@redhat.com>
#
# This work is licensed under the GNU GPLv2 or later.
# See the COPYING file in the top-level directory.

from gi.repository import GObject
from gi.repository import Gtk

from virtinst import xmlutil


#####################
# UI getter helpers #
#####################


def spin_get_helper(widget):
    """
    Safely get spin button contents, converting to int if possible
    """
    adj = widget.get_adjustment()
    txt = widget.get_text()

    try:
        return int(txt)
    except Exception:
        return adj.get_value()


def get_list_selected_row(widget, check_visible=False):
    """
    Helper to simplify getting the selected row in a list/tree/combo
    """
    if check_visible and not widget.get_visible():
        return None

    if hasattr(widget, "get_selection"):
        selection = widget.get_selection()
        model, treeiter = selection.get_selected()
        if treeiter is None:
            return None

        row = model[treeiter]
    else:
        idx = widget.get_active()
        if idx == -1:
            return None

        row = widget.get_model()[idx]

    return row


def get_list_selection(widget, column=0, check_visible=False, check_entry=True):
    """
    Helper to simplify getting the selected row and value in a list/tree/combo.
    If nothing is selected, and the widget is a combo box with a text entry,
    return the value of that.

    :param check_entry: If True, attempt to check the widget's text entry
        using the logic described above.
    """
    row = get_list_selected_row(widget, check_visible=check_visible)
    if row is not None:
        return row[column]

    if check_entry and hasattr(widget, "get_has_entry"):
        if widget.get_has_entry():
            return widget.get_child().get_text().strip()

    return None


#####################
# UI setter helpers #
#####################


def set_list_selection_by_number(widget, rownum):
    """
    Helper to set list selection from the passed row number
    """
    selection = widget.get_selection()
    model = widget.get_model()
    try:
        rownum = int(rownum)
    except Exception:
        rownum = 0

    # GTK 4 set_cursor/select_path need a TreePath. A bare "3" string
    # often leaves the previous row selected (Floppy 2 after a CDROM click).
    _iter = None
    if model is not None:
        try:
            _iter = model.iter_nth_child(None, rownum)
        except Exception:
            _iter = None
    if _iter is not None:
        try:
            selection.unselect_all()
        except Exception:
            pass
        try:
            path = model.get_path(_iter)
            widget.set_cursor(path)
        except Exception:
            try:
                widget.set_cursor(Gtk.TreePath.new_from_indices([rownum]))
            except Exception:
                pass
        try:
            selection.select_iter(_iter)
        except Exception:
            try:
                selection.select_path(model.get_path(_iter))
            except Exception:
                pass
        return

    path = str(rownum)
    try:
        selection.unselect_all()
    except Exception:
        pass
    try:
        widget.set_cursor(path)
    except Exception:
        pass
    try:
        selection.select_path(path)
    except Exception:
        pass


def set_list_selection(widget, value, column=0):
    """
    Set a list or tree selection given the passed key, expected to
    be stored at the specified column.

    If the passed value is not found, and the widget is a combo box with
    a text entry, set the text entry to the passed value.
    """
    model = widget.get_model()
    _iter = None
    want = value
    want_l = want.lower() if isinstance(want, str) else None
    for row in model:
        cell = row[column]
        if cell == value:
            _iter = row.iter
            break
        if want_l is not None and isinstance(cell, str) and cell.lower() == want_l:
            _iter = row.iter
            break
        try:
            pretty = row[1]
        except Exception:
            pretty = None
        if (
            want_l is not None
            and column != 1
            and isinstance(pretty, str)
            and pretty.lower() == want_l
        ):
            _iter = row.iter
            break

    if not _iter:
        if hasattr(widget, "get_has_entry") and widget.get_has_entry():
            widget.get_child().set_text(value or "")
        else:
            _iter = model.get_iter_first()

    if hasattr(widget, "get_selection"):
        selection = widget.get_selection()
        cb = selection.select_iter
    else:
        selection = widget
        cb = selection.set_active_iter
    if _iter:
        cb(_iter)
    selection.emit("changed")


##################
# Misc functions #
##################


def child_get_property(parent, child, propname):
    """
    GTK4 Grid stores attach coordinates on the layout child.
    """
    if isinstance(parent, Gtk.Grid):
        layout = parent.get_layout_manager()
        child_layout = layout.get_layout_child(child)
        if propname == "top-attach":
            return child_layout.get_row()
        if propname == "left-attach":
            return child_layout.get_column()
        if propname == "width":
            return child_layout.get_column_span()
        if propname == "height":
            return child_layout.get_row_span()

    value = GObject.Value()
    value.init(GObject.TYPE_INT)
    parent.child_get_property(child, propname, value)
    return value.get_int()


def set_grid_row_visible(child, visible):
    """
    For the passed widget, find its parent GtkGrid, and hide/show all
    elements that are in the same row as it. Simplifies having to name
    every element in a row when we want to dynamically hide things
    based on UI interaction
    """
    parent = child.get_parent()
    if not isinstance(parent, Gtk.Grid):
        raise xmlutil.DevError("parent must be grid, not %s" % type(parent))

    row = child_get_property(parent, child, "top-attach")
    from . import gtkcompat

    for c in gtkcompat.get_children(parent):
        if child_get_property(parent, c, "top-attach") == row:
            c.set_visible(visible)


def init_combo_text_column(combo, col):
    """
    Set the text column of the passed combo to 'col'. Does the
    right thing whether it's a plain combo or a comboboxentry. Saves
    some typing.

    :returns: If we added a cell renderer, returns it. Otherwise return None
    """
    if combo.get_has_entry():
        combo.set_entry_text_column(col)
    else:
        text = Gtk.CellRendererText()
        combo.pack_start(text, True)
        combo.add_attribute(text, "text", col)
        return text
    return None


def pretty_mem(val):
    val = int(val)
    if val > (10 * 1024 * 1024):
        return "%2.2f GiB" % (val / (1024.0 * 1024.0))
    else:
        return "%2.0f MiB" % (val / 1024.0)


def build_simple_combo(combo, values, default_value=None, sort=True):
    """
    Helper to build a combo with model schema [xml value, label]
    """
    model = Gtk.ListStore(object, str)
    combo.set_model(model)
    init_combo_text_column(combo, 1)
    if sort:
        model.set_sort_column_id(1, Gtk.SortType.ASCENDING)

    for xmlval, label in values:
        model.append([xmlval, label])
    if default_value:
        set_list_selection(combo, default_value)
    elif len(model):
        combo.set_active(0)
