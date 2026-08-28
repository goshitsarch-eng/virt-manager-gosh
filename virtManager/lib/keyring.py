# Copyright (C) 2006, 2013 Red Hat, Inc.
# Copyright (C) 2006 Daniel P. Berrange <berrange@redhat.com>
#
# This work is licensed under the GNU GPLv2 or later.
# See the COPYING file in the top-level directory.

import json
import os

from gi.repository import Gio
from gi.repository import GLib

from virtinst import log

from ..baseclass import vmmGObject


class _vmmSecret:
    def __init__(self, name, secret=None, attributes=None):
        self.name = name
        self.secret = secret
        self.attributes = attributes

    def get_secret(self):
        return self.secret

    def get_name(self):
        return self.name


class vmmKeyring(vmmGObject):
    """
    freedesktop Secret API abstraction
    """

    @classmethod
    def get_instance(cls):
        if not cls._instance:
            cls._instance = vmmKeyring()
        return cls._instance

    def __init__(self):
        vmmGObject.__init__(self)

        self._collection = None

        try:
            self._dbus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            self._service = Gio.DBusProxy.new_sync(
                self._dbus,
                0,
                None,
                "org.freedesktop.secrets",
                "/org/freedesktop/secrets",
                "org.freedesktop.Secret.Service",
                None,
            )

            self._session = self._service.OpenSession("(sv)", "plain", GLib.Variant("s", ""))[1]

            self._collection = Gio.DBusProxy.new_sync(
                self._dbus,
                0,
                None,
                "org.freedesktop.secrets",
                "/org/freedesktop/secrets/aliases/default",
                "org.freedesktop.Secret.Collection",
                None,
            )

            log.debug("Using keyring session %s", self._session)
        except Exception:  # pragma: no cover
            log.exception("Error determining keyring")

    def _cleanup(self):
        pass  # pragma: no cover

    def _find_secret_item_path(self, uuid, hvuri):
        attributes = {
            "uuid": uuid,
            "hvuri": hvuri,
        }
        unlocked, locked = self._service.SearchItems("(a{ss})", attributes)
        if not unlocked:
            if locked:
                log.warning("Item found, but it's locked")  # pragma: no cover
            return None
        return unlocked[0]

    def _do_prompt_if_needed(self, path):
        if path == "/":
            return
        iface = Gio.DBusProxy.new_sync(  # pragma: no cover
            self._dbus,
            0,
            None,
            "org.freedesktop.secrets",
            path,
            "org.freedesktop.Secret.Prompt",
            None,
        )
        iface.Prompt("(s)", "")  # pragma: no cover

    def _add_secret(self, secret):
        try:
            props = {
                "org.freedesktop.Secret.Item.Label": GLib.Variant("s", secret.get_name()),
                "org.freedesktop.Secret.Item.Attributes": GLib.Variant("a{ss}", secret.attributes),
            }
            params = (
                self._session,
                [],
                [ord(v) for v in secret.get_secret()],
                "text/plain; charset=utf8",
            )
            replace = True

            dummy, prompt = self._collection.CreateItem("(a{sv}(oayays)b)", props, params, replace)
            self._do_prompt_if_needed(prompt)
        except Exception:  # pragma: no cover
            log.exception("Failed to add keyring secret")

    def _del_secret(self, uuid, hvuri):
        try:
            path = self._find_secret_item_path(uuid, hvuri)
            if path is None:
                return None

            iface = Gio.DBusProxy.new_sync(
                self._dbus,
                0,
                None,
                "org.freedesktop.secrets",
                path,
                "org.freedesktop.Secret.Item",
                None,
            )
            prompt = iface.Delete()
            self._do_prompt_if_needed(prompt)
        except Exception:  # pragma: no cover
            log.exception("Failed to delete keyring secret")

    def _get_secret(self, uuid, hvuri):
        ret = None
        try:
            path = self._find_secret_item_path(uuid, hvuri)
            if path is None:
                return None

            iface = Gio.DBusProxy.new_sync(
                self._dbus,
                0,
                None,
                "org.freedesktop.secrets",
                path,
                "org.freedesktop.Secret.Item",
                None,
            )

            secretbytes = iface.GetSecret("(o)", self._session)[2]
            label = iface.get_cached_property("Label").unpack().strip("'")
            dbusattrs = iface.get_cached_property("Attributes").unpack()

            secret = "".join([chr(c) for c in secretbytes])

            attrs = {}
            for key, val in dbusattrs.items():
                if key not in ["hvuri", "uuid"]:
                    continue
                attrs["%s" % key] = "%s" % val

            ret = _vmmSecret(label, secret, attrs)
        except Exception:  # pragma: no cover
            log.exception("Failed to get keyring secret uuid=%r hvuri=%r", uuid, hvuri)

        return ret

    ##############
    # Public API #
    ##############

    def is_available(self):
        # File fallback keeps "Save this password" usable without Secret Service.
        return True

    def _file_store_path(self):
        return os.path.join(GLib.get_user_config_dir(), "virt-manager", "console-keyring.json")

    def _file_key(self, vm):
        return "%s|%s" % (vm.get_uuid(), vm.conn.get_uri())

    def _file_load(self):
        path = self._file_store_path()
        if not os.path.exists(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except Exception:
            log.debug("Error loading file keyring", exc_info=True)
            return {}

    def _file_save(self, data):
        """Write the fallback store, readable only by its owner.

        This holds console passwords in the clear, so it must never be
        created with the default umask (0644 on most systems).
        """
        path = self._file_store_path()
        try:
            os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
            tmp = path + ".tmp"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(data, fh)
            except Exception:
                os.close(fd)
                raise
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except Exception:
            log.debug("Error saving file keyring", exc_info=True)

    def _get_secret_name(self, vm):
        return "vm-console-" + vm.get_uuid()

    def get_console_password(self, vm):
        if self._collection is not None:
            try:
                secret = self._get_secret(vm.get_uuid(), vm.conn.get_uri())
                if secret is not None:
                    return (secret.get_secret(), vm.get_console_username() or "")
            except Exception:
                log.debug("Error fetching libsecret password", exc_info=True)

        rec = self._file_load().get(self._file_key(vm))
        if isinstance(rec, dict):
            return (rec.get("password") or "", rec.get("username") or vm.get_console_username() or "")
        if isinstance(rec, str):
            return (rec, vm.get_console_username() or "")
        return ("", "")

    def set_console_password(self, vm, password, username=""):
        vm.set_console_username(username)
        if self._collection is not None:
            secret = _vmmSecret(
                self._get_secret_name(vm),
                password,
                {"uuid": vm.get_uuid(), "hvuri": vm.conn.get_uri()},
            )
            self._add_secret(secret)
            # The Secret Service has it. Writing a cleartext copy as well
            # would defeat the point of storing it there, so drop any
            # entry left over from a session that had no keyring.
            data = self._file_load()
            if data.pop(self._file_key(vm), None) is not None:
                self._file_save(data)
            return

        # No Secret Service: the file is the only place "Save this
        # password" can put it. Keep the feature, owner-readable only.
        data = self._file_load()
        data[self._file_key(vm)] = {"password": password, "username": username}
        self._file_save(data)

    def del_console_password(self, vm):
        if self._collection is not None:
            self._del_secret(vm.get_uuid(), vm.conn.get_uri())
        vm.del_console_username()
        data = self._file_load()
        data.pop(self._file_key(vm), None)
        self._file_save(data)
