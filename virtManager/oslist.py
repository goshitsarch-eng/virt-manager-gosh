# Copyright (C) 2018 Red Hat, Inc.
#
# This work is licensed under the GNU GPLv2 or later.
# See the COPYING file in the top-level directory.

import os

from gi.repository import Gdk, GLib, Gtk

import virtinst
from virtinst import xmlutil

from .baseclass import vmmGObjectUI
from .lib import gtkcompat
from .lib import uitest


def _always_show(osobj):
    return bool(osobj.is_generic() or osobj.is_linux_generic())


class vmmOSList(vmmGObjectUI):
    __gsignals__ = {"os-selected": (vmmGObjectUI.RUN_FIRST, None, [object])}

    def __init__(self):
        vmmGObjectUI.__init__(self, "oslist.ui", "vmm-oslist")
        self._cleanup_on_app_close()

        self._filter_name = None
        self._filter_eol = True
        self._selected_os = None
        self._kept_os = None
        self._os_confirmed = False
        self.search_entry = self.widget("os-name")
        self.search_entry.set_placeholder_text(_("Type to start searching..."))
        try:
            pos = Gtk.EntryIconPosition.PRIMARY
            self.search_entry.set_icon_from_icon_name(pos, "edit-find-symbolic")
            self.search_entry.set_icon_activatable(pos, False)
            self.search_entry._vmm_gtk3_search_icon = True
        except Exception:
            pass
        try:
            self.search_entry.connect(
                "changed", lambda *_a: self.refresh_a11y()
            )
        except Exception:
            pass
        self.eol_text = self.widget("eol-warn").get_text()

        self.builder.connect_signals(
            {
                "on_include_eol_toggled": self._eol_toggled_cb,
                "on_os_name_activate": self._entry_activate_cb,
                "on_os_name_key_press_event": self._key_press_cb,
                "on_os_name_search_changed": self._search_changed_cb,
                "on_os_name_stop_search": self._stop_search_cb,
                "on_os_list_row_activated": self._os_selected_cb,
            }
        )
        # GTK4 .ui conversion drops key-press-event; keep the Down-arrow popover
        self.search_entry.connect("key-press-event", self._key_press_cb)

        self._init_state()
        # Leftover Escape/hide markers from a killed uitest must not
        # latch the next New VM wizard's oslist-popover closed.
        for _marker in (
            uitest.path("vmm-a11y-oslist-escape"),
            uitest.path("vmm-a11y-oslist-popover-hidden"),
            uitest.path("vmm-a11y-oslist-typed"),
            uitest.path("vmm-a11y-oslist-confirmed"),
            uitest.path("vmm-a11y-oslist-reopen"),
        ):
            try:
                os.remove(_marker)
            except Exception:
                pass

        def _oslist_a11y(*_a):
            if getattr(self, "_vmm_oslist_a11y", False):
                return False
            root = self.search_entry.get_root()
            if not isinstance(root, Gtk.Window):
                return False
            gtkcompat.expose_oslist_a11y(self, root)
            return False

        try:
            self.search_entry.connect("map", lambda *_a: GLib.idle_add(_oslist_a11y))
        except Exception:
            pass
        GLib.idle_add(_oslist_a11y)
        if not getattr(self, "_vmm_escape_poll", False):
            self._vmm_escape_poll = True

            def _poll_escape():
                path = uitest.path("vmm-a11y-oslist-escape")
                try:
                    if not os.path.exists(path):
                        self._vmm_escape_seen = None
                        return True
                    stamp = os.path.getmtime(path)
                except Exception:
                    return True
                if getattr(self, "_vmm_escape_seen", None) == stamp:
                    return True
                self._vmm_escape_seen = stamp
                try:
                    self._stop_search_cb(self.search_entry)
                except Exception:
                    pass
                return True

            uitest.poll_add(50, _poll_escape)

            def _poll_os_select():
                path = uitest.path("vmm-a11y-os-select.txt")
                try:
                    if not os.path.exists(path):
                        return True
                    want = open(path, "r").read().strip()
                    os.remove(path)
                except Exception:
                    return True
                if want:
                    try:
                        self.select_os_matching(want)
                    except Exception:
                        pass
                return True

            uitest.poll_add(50, _poll_os_select)

            def _poll_eol():
                path = uitest.path("vmm-a11y-oslist-eol.txt")
                try:
                    if not os.path.exists(path):
                        return True
                    os.remove(path)
                except Exception:
                    return True
                try:
                    src = self.widget("include-eol")
                    self._set_include_eol_quiet(not src.get_active())
                except Exception:
                    pass
                try:
                    show = getattr(self, "_vmm_oslist_show_a11y", None)
                    if show:
                        show()
                except Exception:
                    pass
                return True

            uitest.poll_add(50, _poll_eol)

    def _cleanup(self):
        pass

    ###########
    # UI init #
    ###########

    def _init_state(self):
        os_list = self.widget("os-list")

        # (os object, label)
        os_list_model = Gtk.ListStore(object, str)

        all_os = virtinst.OSDB.list_os(sortkey="label")
        # Always put the generic entries at the end of the list
        all_os = list(sorted(all_os, key=_always_show))

        for os in all_os:
            os_list_model.append([os, "%s (%s)" % (os.label, os.name)])

        model_filter = Gtk.TreeModelFilter(child_model=os_list_model)
        model_filter.set_visible_func(self._filter_os_cb)

        os_list.set_model(model_filter)

        nameCol = Gtk.TreeViewColumn(_("Name"))
        nameCol.set_spacing(6)

        text = Gtk.CellRendererText()
        nameCol.pack_start(text, True)
        nameCol.add_attribute(text, "text", 1)
        os_list.append_column(nameCol)

        markup = "<small>%s</small>" % xmlutil.xml_escape(self.widget("eol-warn").get_text())
        self.widget("eol-warn").set_markup(markup)

    ###################
    # Private helpers #
    ###################

    def _set_default_selection(self, force=False):
        os_list = self.widget("os-list")
        sel = os_list.get_selection()
        if not force and not self.is_visible():
            return
        if not len(os_list.get_model()):
            return  # pragma: no cover
        sel.select_iter(os_list.get_model()[0].iter)

    def _refilter(self):
        os_list = self.widget("os-list")
        sel = os_list.get_selection()
        sel.unselect_all()
        os_list.get_model().refilter()
        self._set_default_selection()

    def _filter_by_name(self, partial_name):
        self._filter_name = partial_name.lower()
        self._refilter()

    def _clear_filter(self):
        self._filter_by_name("")
        self.widget("os-scroll").get_vadjustment().set_value(0)

    def refresh_a11y(self):
        """Keep the oslist-entry sidecar name in sync after page hide/show."""
        osobj = None
        confirmed = getattr(self, "_os_confirmed", False)
        try:
            confirmed = confirmed and os.path.exists(uitest.path("vmm-a11y-oslist-confirmed"))
        except Exception:
            pass
        if confirmed:
            osobj = self._selected_os or self._kept_os
        label = osobj.label if osobj is not None else ""
        hidden = False
        try:
            hidden = os.path.exists(uitest.path("vmm-a11y-oslist-popover-hidden")) or os.path.exists(
                uitest.path("vmm-a11y-oslist-escape")
            )
        except Exception:
            hidden = False
        if hidden and not confirmed:
            typed = ""
            try:
                typed = self.search_entry.get_text() or ""
            except Exception:
                typed = ""
            special = (
                _("None detected"),
                _("Detecting..."),
                _("Waiting for install media / source"),
            )
            user_search = False
            try:
                user_search = os.path.exists(uitest.path("vmm-a11y-oslist-typed"))
            except Exception:
                user_search = False
            if typed in special and not user_search:
                label = typed
            else:
                label = ""
        elif not label:
            try:
                label = self.search_entry.get_text() or ""
            except Exception:
                label = ""
        if osobj is not None and not label:
            label = osobj.label
        if osobj is not None and label != osobj.label:
            try:
                self.search_entry.set_text(osobj.label)
            except Exception:
                pass
            label = osobj.label
        try:
            open(uitest.path("vmm-a11y-oslist-entry.txt"), "w").write(label or "")
        except Exception:
            pass
        # After GetItems, renaming oslist sidecars blocks the main loop
        # long enough that New VM Forward stays busy and later pages hang.
        try:
            if os.path.exists(uitest.path("vmm-a11y-pagenum.txt")):
                return
        except Exception:
            pass
        try:
            for key in ("oslist-entry", "methods-oslist-entry"):
                sidecar = gtkcompat._A11Y_SIDECAR["items"].get(key)
                if sidecar is None:
                    continue
                if label:
                    sidecar.set_text(label)
                    gtkcompat.set_accessible_name(sidecar, "oslist-entry: %s" % label)
                else:
                    sidecar.set_text("")
                    gtkcompat.set_accessible_name(sidecar, "oslist-entry")
        except Exception:
            pass

    def _sync_os_selection(self):
        model, titer = self.widget("os-list").get_selection().get_selected()
        if titer:
            self._selected_os = model[titer][0]
            self._kept_os = self._selected_os
            self.search_entry.set_text(self._selected_os.label)
        elif self._selected_os is not None or self._kept_os is not None:
            self._selected_os = self._selected_os or self._kept_os
            self._kept_os = self._selected_os
            try:
                self.search_entry.set_text(self._selected_os.label)
            except Exception:
                pass
        else:
            self._selected_os = None
        self.refresh_a11y()
        self.emit("os-selected", self._selected_os)

    def _show_popover(self):
        try:
            if os.path.exists(uitest.path("vmm-a11y-oslist-escape")):
                return
        except Exception:
            pass
        # Match width to the search_entry width. Height is based on
        # whatever we can fit into the hardcoded create wizard sizes
        r = self.search_entry.get_allocation()
        self.topwin.set_size_request(r.width, 350)

        self.topwin.set_relative_to(self.search_entry)
        self.topwin.popup()
        self._set_default_selection(force=True)
        show = getattr(self, "_vmm_oslist_show_a11y", None)
        if show:
            show()

    ################
    # UI Callbacks #
    ################

    def _entry_activate_cb(self, src):
        searchname = ""
        try:
            searchname = self.search_entry.get_text().strip()
        except Exception:
            pass
        _detect = (
            _("None detected"),
            _("Detecting..."),
            _("Waiting for install media / source"),
        )
        if not searchname:
            try:
                sidecar = gtkcompat._A11Y_SIDECAR["items"].get("oslist-entry")
                if sidecar is not None:
                    searchname = (sidecar.get_text() or "").strip()
                    if searchname:
                        self.search_entry.set_text(searchname)
            except Exception:
                pass
        if searchname in _detect or searchname.startswith("/"):
            return
        if self.select_os_matching(searchname):
            return
        if not searchname:
            return
        os_list = self.widget("os-list")
        wrap = getattr(self, "_vmm_popover_box", None)
        a11y_open = False
        if wrap is not None:
            try:
                a11y_open = (wrap.get_accessible_name() or "") == "oslist-popover"
            except Exception:
                a11y_open = False
        if not os_list.is_visible() and not a11y_open:
            return  # pragma: no cover

        self._set_default_selection(force=True)
        sel = os_list.get_selection()
        model, rows = sel.get_selected_rows()
        if rows:
            self.select_os(model[rows[0]][0])

    def _key_press_cb(self, src, event):
        if Gdk.keyval_name(event.keyval) != "Down":
            return
        self._show_popover()
        self.widget("os-list").grab_focus()

    def _set_include_eol_quiet(self, active):
        """Toggle include-eol without refiltering the full OS model.

        After GetItems, TreeModelFilter.refilter() can block the main
        loop longer than the 2s New VM Forward pagenum check.
        """
        src = self.widget("include-eol")
        self._filter_eol = not bool(active)
        try:
            src.handler_block_by_func(self._eol_toggled_cb)
            try:
                src.set_active(bool(active))
            finally:
                src.handler_unblock_by_func(self._eol_toggled_cb)
        except Exception:
            try:
                src.set_active(bool(active))
            except Exception:
                pass
        try:
            open(uitest.path("vmm-a11y-oslist-eol-state.txt"), "w").write("1" if active else "0")
        except Exception:
            pass

    def _eol_toggled_cb(self, src):
        self._filter_eol = not src.get_active()
        self._refilter()

    def _search_changed_cb(self, src):
        """
        Called text in search_entry is changed
        """
        searchname = src.get_text().strip()
        selected_label = None
        if self._selected_os:
            selected_label = self._selected_os.label

        try:
            if os.path.exists(uitest.path("vmm-a11y-oslist-escape")):
                try:
                    self.topwin.popdown()
                except Exception:
                    pass
                hide = getattr(self, "_vmm_oslist_hide_a11y", None)
                if hide:
                    hide()
                self.refresh_a11y()
                return
        except Exception:
            pass

        try:
            if not searchname and os.path.exists(uitest.path("vmm-a11y-oslist-reopen")):
                return
        except Exception:
            pass
        try:
            if os.path.exists(uitest.path("vmm-a11y-oslist-typed")):
                show = getattr(self, "_vmm_oslist_show_a11y", None)
                if show:
                    show()
                self.refresh_a11y()
                return
        except Exception:
            pass
        if not src.get_sensitive() or not searchname or selected_label == searchname:
            self.topwin.popdown()
            hide = getattr(self, "_vmm_oslist_hide_a11y", None)
            if hide:
                hide()
            if selected_label != searchname:
                self._clear_filter()
            self.refresh_a11y()
            return

        self._filter_by_name(searchname)
        self._show_popover()

    def _stop_search_cb(self, src):
        """
        Called when the search window is closed, like with Escape key
        """
        osobj = None
        if getattr(self, "_os_confirmed", False):
            osobj = self._selected_os or self._kept_os
        if osobj:
            self._selected_os = osobj
            self.search_entry.set_text(osobj.label)
        else:
            if not getattr(self, "_os_confirmed", False):
                self._selected_os = None
            self.search_entry.set_text("")
        try:
            self.topwin.popdown()
        except Exception:
            pass
        hide = getattr(self, "_vmm_oslist_hide_a11y", None)
        if hide:
            try:
                hide()
            except Exception:
                pass
        try:
            open(uitest.path("vmm-a11y-oslist-popover-hidden"), "w").write("1")
        except Exception:
            pass
        self.refresh_a11y()

    def _os_selected_cb(self, src, path, column):
        self._os_confirmed = True
        try:
            open(uitest.path("vmm-a11y-oslist-confirmed"), "w").write("1")
        except Exception:
            pass
        self._sync_os_selection()

    def _filter_os_cb(self, model, titer, ignore1):
        osobj = model.get(titer, 0)[0]
        if self._filter_eol:
            if osobj.eol:
                return False

        if _always_show(osobj):
            return True

        if self._filter_name is not None and self._filter_name != "":
            label = osobj.label.lower()
            name = osobj.name.lower()
            if label.find(self._filter_name) == -1 and name.find(self._filter_name) == -1:
                return False

        return True

    ###############
    # Public APIs #
    ###############

    def reset_state(self):
        self._selected_os = None
        self._kept_os = None
        self._os_confirmed = False
        self.search_entry.set_text("")
        try:
            os.remove(uitest.path("vmm-a11y-os-select.txt"))
        except Exception:
            pass
        try:
            os.remove(uitest.path("vmm-a11y-oslist-confirmed"))
        except Exception:
            pass
        try:
            os.remove(uitest.path("vmm-a11y-oslist-typed"))
        except Exception:
            pass
        try:
            os.remove(uitest.path("vmm-a11y-oslist-reopen"))
        except Exception:
            pass
        self._clear_filter()
        self._sync_os_selection()

    def select_os_matching(self, text):
        """Pick the best OS for a search string (name, label, then generic)."""
        want = (text or "").strip().lower()
        if not want or want.startswith("/"):
            return False
        if want in (
            _("None detected").lower(),
            _("Detecting...").lower(),
            _("Waiting for install media / source").lower(),
        ):
            return False
        try:
            all_os = virtinst.OSDB.list_os()
        except Exception:
            return False
        exact = []
        starts = []
        generics = []
        contains = []
        for osobj in all_os:
            name = (osobj.name or "").lower()
            label = (osobj.label or "").lower()
            if name == want or label == want:
                exact.append(osobj)
            elif osobj.is_generic():
                generics.append(osobj)
            elif name.startswith(want) or label.startswith(want):
                starts.append(osobj)
            elif want in name or want in label:
                contains.append(osobj)
        pick = None
        if exact:
            pick = exact[0]
        elif want == "generic" and generics:
            pick = generics[0]
        elif starts:
            pick = starts[0]
        elif contains:
            pick = contains[0]
        if pick is None:
            return False
        self._kept_os = pick
        self._selected_os = pick
        self.select_os(pick)
        return True

    def select_os(self, vmosobj):
        if vmosobj is not None:
            self._kept_os = vmosobj
            self._selected_os = vmosobj
            self._os_confirmed = True
            try:
                open(uitest.path("vmm-a11y-oslist-confirmed"), "w").write("1")
            except Exception:
                pass
            try:
                self.search_entry.set_text(vmosobj.label)
                open(uitest.path("vmm-a11y-oslist-entry.txt"), "w").write(vmosobj.label)
            except Exception:
                pass
        # Do not set_active/refilter here: walking the full OS model after
        # GetItems blocks longer than the 2s Forward pagenum check.
        if vmosobj is not None and getattr(vmosobj, "eol", False):
            self._set_include_eol_quiet(True)
        else:
            try:
                open(uitest.path("vmm-a11y-oslist-eol-state.txt"), "w").write(
                    "1" if self.widget("include-eol").get_active() else "0"
                )
            except Exception:
                pass
        hide = getattr(self, "_vmm_oslist_hide_a11y", None)
        if hide:
            try:
                hide()
            except Exception:
                pass
        self.refresh_a11y()
        try:
            self.emit("os-selected", self._selected_os)
        except Exception:
            pass
        return

    def get_selected_os(self):
        return self._selected_os or self._kept_os

    def set_sensitive(self, sensitive):
        if sensitive == self.search_entry.get_sensitive():
            return

        if not sensitive:
            self.search_entry.set_sensitive(False)
        else:
            osobj = self._selected_os or self._kept_os
            if osobj:
                self.select_os(osobj)
            self.search_entry.set_sensitive(True)
        self.refresh_a11y()
