# This work is licensed under the GNU GPLv2 or later.
# See the COPYING file in the top-level directory.

"""
Import stub so testdriver inspection UI tests can run without python3-guestfs.

virt-manager only instantiates guestfs.GuestFS for real libvirt connections.
The testdriver path uses _make_fake_data() and never constructs this class.
"""


class GuestFS(object):
    def __init__(self, *args, **kwargs):
        raise RuntimeError("guestfs stub: real appliance is not available")
