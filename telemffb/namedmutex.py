#
# This file is part of the TelemFFB distribution (https://github.com/walmis/TelemFFB).
# Copyright (c) 2023 Valmantas Palikša.
# Copyright (c) 2023 Micah Frisby
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#

import sys


if sys.platform == 'win32':
    """Named mutex handling (for Win32).

    See README.md or https://github.com/benhoyt/namedmutex for a bit more
    documentation.

    This code is released under the new BSD 3-clause license:
    http://opensource.org/licenses/BSD-3-Clause

    """

    import ctypes
    from ctypes import wintypes

    # Create ctypes wrapper for Win32 functions we need, with correct argument/return types
    _CreateMutex = ctypes.windll.kernel32.CreateMutexW
    _CreateMutex.argtypes = [wintypes.LPCVOID, wintypes.BOOL, wintypes.LPCWSTR]
    _CreateMutex.restype = wintypes.HANDLE

    _WaitForSingleObject = ctypes.windll.kernel32.WaitForSingleObject
    _WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _WaitForSingleObject.restype = wintypes.DWORD

    _ReleaseMutex = ctypes.windll.kernel32.ReleaseMutex
    _ReleaseMutex.argtypes = [wintypes.HANDLE]
    _ReleaseMutex.restype = wintypes.BOOL

    _CloseHandle = ctypes.windll.kernel32.CloseHandle
    _CloseHandle.argtypes = [wintypes.HANDLE]
    _CloseHandle.restype = wintypes.BOOL


    class NamedMutex(object):
        """A named, system-wide mutex that can be acquired and released."""

        def __init__(self, name, acquired=False, timeout=None):
            """Create named mutex with given name, also acquiring mutex if acquired is True.
            Mutex names are case sensitive, and a filename (with backslashes in it) is not a
            valid mutex name. Raises WindowsError on error.

            """
            self.name = name
            self.acquired = acquired
            ret = _CreateMutex(None, False, name)
            if not ret:
                raise ctypes.WinError()
            self.handle = ret
            if acquired:
                self.acquire(timeout=timeout)

        def acquire(self, timeout=None):
            """Acquire ownership of the mutex, returning True if acquired. If a timeout
            is specified, it will wait a maximum of timeout seconds to acquire the mutex,
            returning True if acquired, False on timeout. Raises WindowsError on error.

            """
            if timeout is None:
                # Wait forever (INFINITE)
                timeout = 0xFFFFFFFF
            else:
                timeout = int(round(timeout * 1000))
            ret = _WaitForSingleObject(self.handle, timeout)
            if ret in (0, 0x80):
                # Note that this doesn't distinguish between normally acquired (0) and
                # acquired due to another owning process terminating without releasing (0x80)
                self.acquired = True
                return True
            elif ret == 0x102:
                # Timeout
                self.acquired = False
                return False
            else:
                # Waiting failed
                raise ctypes.WinError()

        def release(self):
            """Relase an acquired mutex. Raises WindowsError on error."""
            ret = _ReleaseMutex(self.handle)
            if not ret:
                raise ctypes.WinError()
            self.acquired = False

        def close(self):
            """Close the mutex and release the handle."""
            if self.handle is None:
                # Already closed
                return
            ret = _CloseHandle(self.handle)
            if not ret:
                raise ctypes.WinError()
            self.handle = None

        __del__ = close

        def __repr__(self):
            """Return the Python representation of this mutex."""
            return '{0}({1!r}, acquired={2})'.format(
                self.__class__.__name__, self.name, self.acquired)

        __str__ = __repr__

        # Make it a context manager so it can be used with the "with" statement
        def __enter__(self):
            self.acquire()
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            self.release()

else:
    import fcntl
    import os
    import re
    import tempfile
    import time

    class NamedMutex(object):
        """A named, process-wide lock using fcntl file locking (Linux/macOS)."""

        def __init__(self, name, acquired=False, timeout=None):
            self.name = name
            self.acquired = False
            safe_name = re.sub(r'[^\w]', '_', name)
            self._lockfile_path = os.path.join(tempfile.gettempdir(), f'vpforce_{safe_name}.lock')
            self._lockfile = open(self._lockfile_path, 'w')
            if acquired:
                self.acquire(timeout=timeout)

        def acquire(self, timeout=None):
            """Acquire the lock. Returns True if acquired, False on timeout."""
            if timeout is None:
                # Block indefinitely
                try:
                    fcntl.flock(self._lockfile.fileno(), fcntl.LOCK_EX)
                    self.acquired = True
                    return True
                except OSError:
                    return False
            elif timeout == 0:
                try:
                    fcntl.flock(self._lockfile.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self.acquired = True
                    return True
                except OSError:
                    return False
            else:
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    try:
                        fcntl.flock(self._lockfile.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        self.acquired = True
                        return True
                    except OSError:
                        time.sleep(0.05)
                return False

        def release(self):
            """Release the lock."""
            fcntl.flock(self._lockfile.fileno(), fcntl.LOCK_UN)
            self.acquired = False

        def close(self):
            """Close and clean up the lockfile."""
            if self._lockfile:
                try:
                    fcntl.flock(self._lockfile.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass
                self._lockfile.close()
                self._lockfile = None

        __del__ = close

        def __repr__(self):
            return '{0}({1!r}, acquired={2})'.format(
                self.__class__.__name__, self.name, self.acquired)

        __str__ = __repr__

        def __enter__(self):
            self.acquire()
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            self.release()


if __name__ == '__main__':
    # Just test that acquire and release work.
    with NamedMutex('test_mutex_123'):
        pass
