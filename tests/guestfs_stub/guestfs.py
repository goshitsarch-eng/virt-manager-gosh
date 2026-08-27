# This work is licensed under the GNU GPLv2 or later.
# See the COPYING file in the top-level directory.

"""
Import stub so testdriver inspection UI tests can run without python3-guestfs.

Testdriver still uses virtManager.lib.inspection._make_fake_data() for
vmmInspection.vm_refresh. This class exists so _perform_inspection() can
be exercised against the same fake application/OS shape without launching
a real libguestfs appliance.
"""

import time


class GuestFS(object):
    def __init__(self, close_on_exit=False, python_return_dict=True, **kwargs):
        ignore = (close_on_exit, python_return_dict, kwargs)
        self._dom = None
        self._launched = False
        self._mounted = False
        self._name = ""

    def add_libvirt_dom(self, backend, readonly=1):
        ignore = readonly
        self._dom = backend
        try:
            self._name = backend.name()
        except Exception:
            self._name = ""
        return 1

    def launch(self):
        self._launched = True

    def inspect_os(self):
        if self._name == "test":
            return []
        return ["/dev/sda2"]

    def inspect_get_type(self, root):
        ignore = root
        return "linux"

    def inspect_get_distro(self, root):
        ignore = root
        return "fedora"

    def inspect_get_major_version(self, root):
        ignore = root
        return 40

    def inspect_get_minor_version(self, root):
        ignore = root
        return 1

    def inspect_get_hostname(self, root):
        ignore = root
        return "test_hostname"

    def inspect_get_product_name(self, root):
        ignore = root
        return "test_product_name"

    def inspect_get_product_variant(self, root):
        ignore = root
        return "test_product_variant"

    def inspect_get_package_format(self, root):
        ignore = root
        return "rpm"

    def inspect_get_mountpoints(self, root):
        ignore = root
        return {"/": "/dev/sda2", "/boot": "/dev/sda1"}

    def mount_ro(self, dev, mp):
        ignore = (dev, mp)
        self._mounted = True

    def inspect_get_icon(self, root, favicon=0, highquality=1):
        ignore = (root, favicon, highquality)
        return b""

    def inspect_list_applications2(self, root):
        ignore = root
        stamp = str(time.time())
        return [
            {
                "app2_name": "test_app1",
                "app2_display_name": "test_app1_display_name",
                "app2_epoch": 1,
                "app2_version": "2",
                "app2_release": "3",
                "app2_summary": "test_app1_summary-" + stamp,
                "app2_description": "",
            },
            {
                "app2_name": "test_app2_name",
                "app2_display_name": "",
                "app2_epoch": 1,
                "app2_version": "2",
                "app2_release": "3",
                "app2_summary": "",
                "app2_description": "test_app2_description-" + stamp + "\n",
            },
        ]
