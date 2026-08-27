# Copyright (C) 2010, 2013 Red Hat, Inc.
# Copyright (C) 2010 Cole Robinson <crobinso@redhat.com>
#
# This work is licensed under the GNU GPLv2 or later.
# See the COPYING file in the top-level directory.

import os
import sys
import threading
import traceback
import types

from gi.repository import Gdk
from gi.repository import GLib
from gi.repository import GObject
from gi.repository import Gtk

from virtinst import log
from virtinst import xmlutil

class _VmmBuilderScope(GObject.GObject, Gtk.BuilderScope):
    """
    GTK4 Builder looks up signal handlers at parse time. Defer the
    actual callback lookup until connect_signals() is invoked.
    """

    def __init__(self):
        GObject.GObject.__init__(self)
        self.handlers = {}

    def do_create_closure(self, builder, func_name, flags, obj):
        ignore = builder
        ignore = flags
        ignore = obj

        def _cb(*args):
            cb = self.handlers.get(func_name)
            if cb is None:
                return False
            return cb(*args)

        return _cb


class _VmmBuilder:
    def __init__(self):
        self._scope = _VmmBuilderScope()
        self._builder = Gtk.Builder()
        self._builder.set_scope(self._scope)

    def set_translation_domain(self, domain):
        self._builder.set_translation_domain(domain)

    def add_from_file(self, uifile):
        ret = self._builder.add_from_file(uifile)
        from .lib import gtkcompat

        gtkcompat.apply_gtk3_border_widths(self._builder, uifile)
        gtkcompat.apply_gtk3_builder_chrome(self._builder, uifile)
        for obj in self._builder.get_objects():
            gtkcompat.sync_builder_accessible(obj)
        return ret

    def get_object(self, name):
        return self._builder.get_object(name)

    def connect_signals(self, mapping):
        self._scope.handlers.update(mapping)


class vmmGObject(GObject.GObject):
    # Objects can set this to false to disable leak tracking
    _leak_check = True

    # Singleton reference, if applicable (vmmSystray, vmmInspection, ...)
    _instance = None

    # windowlist mapping, if applicable (vmmVMWindow, vmmHost, ...)
    _instances = None

    # This saves a bunch of imports and typing
    RUN_FIRST = GObject.SignalFlags.RUN_FIRST

    @staticmethod
    def idle_add(func, *args, **kwargs):
        """
        Make sure idle functions are run thread safe
        """

        def cb():
            return func(*args, **kwargs)

        return GLib.idle_add(cb)

    def __init__(self):
        GObject.GObject.__init__(self)

        self.__cleaned_up = False

        self._gobject_handles = []
        self._gobject_handles_map = {}
        self._gobject_timeouts = []
        self._gsettings_handles = []

        self._signal_id_map = {}
        self._next_signal_id = 1

        self.object_key = str(self)

        # Config might not be available if we error early in startup
        if config.vmmConfig.is_initialized() and self._leak_check:
            self.config.add_object(self.object_key)

    def _get_err(self):
        from . import error

        return error.vmmErrorDialog.get_instance()

    err = property(_get_err)

    def cleanup(self):
        if self.__cleaned_up:
            return  # pragma: no cover

        # Do any cleanup required to drop reference counts so object is
        # actually reaped by python. Usually means unregistering callbacks
        try:
            # pylint: disable=protected-access
            if self.__class__._instance == self:
                # We set this to True which can help us catch instances
                # where cleanup routines try to reinit singleton classes
                self.__class__._instance = True

            _instances = self.__class__._instances or {}
            for k, v in list(_instances.items()):
                if v == self:
                    _instances.pop(k)

            self._cleanup()

            for h in self._gsettings_handles[:]:
                self.remove_gsettings_handle(h)
            for h in self._gobject_handles[:]:
                if GObject.GObject.handler_is_connected(self, h):
                    self.disconnect(h)
            for h in self._gobject_timeouts[:]:
                self.remove_gobject_timeout(h)
        except Exception:  # pragma: no cover
            log.exception("Error cleaning up %s", self)

        self.__cleaned_up = True

    def _cleanup_on_app_close(self):
        from .engine import vmmEngine

        vmmEngine.get_instance().connect("app-closing", lambda src: self.cleanup())

    def _cleanup(self):
        raise NotImplementedError("_cleanup must be implemented in subclass")

    def __del__(self):
        try:
            if config.vmmConfig.is_initialized() and self._leak_check:
                self.config.remove_object(self.object_key)
        except Exception:  # pragma: no cover
            log.exception("Error removing %s", self.object_key)

    @property
    def config(self):
        return config.vmmConfig.get_instance()

    # pylint: disable=arguments-differ
    # Newer pylint can detect, but warns that overridden arguments are wrong

    def connect(self, name, callback, *args):
        """
        GObject connect() wrapper to simplify callers, and track handles
        for easy cleanup
        """
        ret = GObject.GObject.connect(self, name, callback, *args)

        # If the passed callback is a method of a class instance,
        # keep a mapping of id(instance):[handles]. This lets us
        # implement disconnect_by_obj to simplify signal removal
        if isinstance(callback, types.MethodType):
            i = id(callback.__self__)
            if i not in self._gobject_handles_map:
                self._gobject_handles_map[i] = []
            self._gobject_handles_map[i].append(ret)

        self._gobject_handles.append(ret)
        return ret

    def disconnect(self, handle):
        """
        GObject disconnect() wrapper to simplify callers
        """
        ret = GObject.GObject.disconnect(self, handle)
        self._gobject_handles.remove(handle)
        return ret

    def disconnect_by_obj(self, instance):
        """
        disconnect() every signal attached to a method of the passed instance
        """
        i = id(instance)
        for handle in self._gobject_handles_map.get(i, []):
            if handle in self._gobject_handles:
                self.disconnect(handle)
        self._gobject_handles_map.pop(i, None)

    def timeout_add(self, timeout, func, *args):
        """
        GLib timeout_add wrapper to simplify callers, and track handles
        for easy cleanup
        """
        id_list = []

        def wrap_func(*wrapargs):
            # When the our timeout_add callback returns False, remove
            # the source ID from our cache, to avoid glib warnings like
            # this at app shutdown:
            # Warning: Source ID 60 was not found when attempting to remove it
            func_ret = func(*wrapargs)
            if not func_ret:
                self.remove_gobject_timeout(id_list[0])
            return func_ret

        ret = GLib.timeout_add(timeout, wrap_func, *args)
        self.add_gobject_timeout(ret)
        id_list.append(ret)
        return ret

    def emit(self, signal_name, *args):
        """
        GObject emit() wrapper to simplify callers
        """
        if not self._is_main_thread():  # pragma: no cover
            log.error(
                "emitting signal from non-main thread. This is a bug "
                "please report it. thread=%s self=%s signal=%s",
                self._thread_name(),
                self,
                signal_name,
            )
        return GObject.GObject.emit(self, signal_name, *args)

    def add_gsettings_handle(self, handle):
        self._gsettings_handles.append(handle)

    def remove_gsettings_handle(self, handle):
        self.config.remove_notifier(handle)
        self._gsettings_handles.remove(handle)

    def add_gobject_timeout(self, handle):
        self._gobject_timeouts.append(handle)

    def remove_gobject_timeout(self, handle):
        GLib.source_remove(handle)
        self._gobject_timeouts.remove(handle)

    def _start_thread(self, target=None, name=None, args=None, kwargs=None):
        # Helper for starting a daemonized thread
        t = threading.Thread(target=target, name=name, args=args or [], kwargs=kwargs or {})
        t.daemon = True
        t.start()

    ##############################
    # Internal debugging helpers #
    ##############################

    def _refcount(self):
        return sys.getrefcount(self)

    def _logtrace(self, msg=""):
        if msg:
            msg += " "
        log.debug(
            "%s(%s %s)\n:%s",
            msg,
            self.object_key,
            self._refcount(),
            "".join(traceback.format_stack()),
        )

    def _gc_get_referrers(self):  # pragma: no cover
        import gc
        import pprint

        pprint.pprint(gc.get_referrers(self))

    def _thread_name(self):
        return threading.current_thread().name

    def _is_main_thread(self):
        return self._thread_name() == "MainThread"

    ##############################
    # Custom signal/idle helpers #
    ##############################

    def connect_once(self, signal, func, *args):
        """
        Like standard glib connect(), but only runs the signal handler
        once, then unregisters it
        """
        id_list = []

        def wrap_func(*wrapargs):
            if id_list:
                self.disconnect(id_list[0])

            return func(*wrapargs)

        conn_id = self.connect(signal, wrap_func, *args)
        id_list.append(conn_id)

        return conn_id

    def connect_opt_out(self, signal, func, *args):
        """
        Like standard glib connect(), but allows the signal handler to
        unregister itself if it returns True
        """
        id_list = []

        def wrap_func(*wrapargs):
            ret = func(*wrapargs)
            if ret and id_list:
                self.disconnect(id_list[0])

        conn_id = self.connect(signal, wrap_func, *args)
        id_list.append(conn_id)

        return conn_id

    def idle_emit(self, signal, *args):
        """
        Safe wrapper for using 'self.emit' with GLib.idle_add
        """

        def emitwrap(_s, *_a):
            self.emit(_s, *_a)
            return False

        self.idle_add(emitwrap, signal, *args)


class vmmGObjectUI(vmmGObject):
    def __init__(self, filename, windowname, builder=None, topwin=None):
        vmmGObject.__init__(self)
        self._external_topwin = bool(topwin)
        self.__cleaned_up = False

        if filename:
            uifile = os.path.join(self.config.get_ui_dir(), filename)

            self.builder = _VmmBuilder()
            self.builder.set_translation_domain("virt-manager")
            self.builder.add_from_file(uifile)

            if not topwin:
                self.topwin = self.widget(windowname)
                self.topwin.set_visible(False)
                app = Gtk.Application.get_default()
                if app and hasattr(self.topwin, "set_application"):
                    self.topwin.set_application(app)
            else:
                self.topwin = topwin
        else:
            self.builder = builder
            self.topwin = topwin

        if self.topwin is not None:
            try:
                from .lib import gtkcompat

                gtkcompat.set_toplevel_a11y_role(self.topwin)
            except Exception:
                pass

            def _sync_title(*_a):
                try:
                    title = self.topwin.get_title()
                except Exception:
                    title = None
                if title:
                    from .lib import gtkcompat

                    hidden = False
                    try:
                        hidden = (
                            getattr(self.topwin, "_vmm_ever_shown", False)
                            and not self.topwin.get_visible()
                        )
                    except Exception:
                        hidden = False
                    gtkcompat.set_accessible_name(
                        self.topwin,
                        title + (" (hidden)" if hidden else ""),
                    )
                return False

            try:
                self.topwin.connect("notify::title", _sync_title)
            except Exception:
                pass
            _sync_title()
            if not self._external_topwin:
                self.bind_escape_key_close()
            try:
                from .lib import gtkcompat

                if filename and not self._external_topwin:
                    gtkcompat.install_window_accelerators(
                        self.builder, self.topwin, windowname
                    )
                    gtkcompat.apply_gtk3_dialog_from_name(self.topwin, windowname)
                    gtkcompat.apply_gtk3_dialog_defaults(
                        self.topwin, self.builder, windowname
                    )
                gtkcompat._apply_window_icon(self.topwin)
                gtkcompat.ensure_window_a11y_box(self.topwin)
                title = ""
                try:
                    title = self.topwin.get_title() or ""
                except Exception:
                    title = ""
                # Do not register the generic name "Close": click_alert_button
                # writes that label for error dialogs, and a fuzzy/exact
                # match here would hide the manager and exit the app.
                gtkcompat.expose_a11y_button(
                    "win-close-%s" % id(self.topwin),
                    ".win-close-%s" % (title or "window"),
                    self.close,
                    window=self.topwin,
                )
            except Exception:
                pass

        self._err = None

    def _get_err(self):
        if self._err is None:
            from . import error

            self._err = error.vmmErrorDialog(self.topwin)
        return self._err

    err = property(_get_err)

    def widget(self, name):
        ret = self.builder.get_object(name)
        if not ret:
            raise xmlutil.DevError("Did not find widget name=%s" % name)
        return ret

    def cleanup(self):
        if self.__cleaned_up:
            return  # pragma: no cover

        try:
            self.close()
            vmmGObject.cleanup(self)
            self.builder = None
            if not self._external_topwin:
                self.topwin.destroy()
            self.topwin = None
            self._err = None
        except Exception:  # pragma: no cover
            log.exception("Error cleaning up %s", self)

        self.__cleaned_up = True

    def _cleanup(self):
        raise NotImplementedError("_cleanup must be implemented in subclass")

    def close(self, ignore1=None, ignore2=None):
        if self.topwin is not None:
            self.topwin.hide()
            try:
                from .lib import gtkcompat

                gtkcompat._mark_toplevel_hidden(self.topwin, True)
            except Exception:
                pass

    def is_visible(self):
        return bool(self.topwin and self.topwin.get_visible())

    def bind_escape_key_close(self):
        if getattr(self, "_vmm_escape_bound", False) or self.topwin is None:
            return
        self._vmm_escape_bound = True

        def close_on_escape(_controller, keyval, _keycode, state):
            name = Gdk.keyval_name(keyval) or ""
            if name == "Escape":
                try:
                    from .lib import gtkcompat

                    if gtkcompat.popdown_window_menus(
                        self.topwin, getattr(self, "builder", None)
                    ):
                        return True
                except Exception:
                    pass
                self.close()
                return True
            # Xvfb has no window manager, so Alt+F4 must be handled here.
            alt = bool(state & Gdk.ModifierType.ALT_MASK)
            if name == "F4" and alt:
                self.close()
                return True
            return False

        controller = Gtk.EventControllerKey()
        try:
            controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        except Exception:
            pass
        controller.connect("key-pressed", close_on_escape)
        self.topwin.add_controller(controller)

    def destroy_topwin(self):
        if self.topwin:
            self.topwin.destroy()

    def _set_cursor(self, cursor_type):
        try:
            cursor = Gdk.Cursor.new_from_name(cursor_type)
            self.topwin.set_cursor(cursor)
        except Exception:  # pragma: no cover
            # If a cursor icon theme isn't installed this can cause errors
            # https://bugzilla.redhat.com/show_bug.cgi?id=1516588
            log.debug("Error setting cursor_type=%s", cursor_type, exc_info=True)

    def set_finish_cursor(self):
        self.topwin.set_sensitive(False)
        self._set_cursor("progress")

    def reset_finish_cursor(self, topwin=None):
        if not topwin:
            topwin = self.topwin
        topwin.set_sensitive(True)
        self._set_cursor("default")

    def _cleanup_on_conn_removed(self):
        from .connmanager import vmmConnectionManager

        connmanager = vmmConnectionManager.get_instance()

        def _cb(_src, uri):
            _conn = getattr(self, "conn", None)
            if _conn and _conn.get_uri() == uri:
                self.cleanup()
                return True

        connmanager.connect_opt_out("conn-removed", _cb)


# Imported last to avoid a cycle: config -> inspection -> baseclass
from . import config  # noqa: E402
