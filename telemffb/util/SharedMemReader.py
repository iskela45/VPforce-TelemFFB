"""
Python Shared Memory Reader for Windows
Reads data from shared memory created by C++ writer process using Windows API.
Non-Windows platforms get a no-op stub (DCS telemetry uses UDP on those platforms).
"""

import sys
import time
from typing import Optional
from dataclasses import dataclass

# Shared memory configuration (must match C++ constants)
MESSAGE_BUFFER_SIZE = 65000
SHARED_DATA_VERSION = 1


@dataclass
class SharedData:
    """Python representation of the C++ SharedData structure"""
    version: int
    data_size: int
    sequence_number: int
    writer_pid: int
    timestamp_us: int
    message: str


class WaitResult:
    DATA_UPDATED = 0
    TIMEOUT = 1
    ERROR = 2


if sys.platform == 'win32':
    import ctypes
    import ctypes.wintypes

    # Windows API constants
    GENERIC_READ = 0x80000000
    FILE_MAP_READ = 0x0004
    WAIT_OBJECT_0 = 0x00000000
    WAIT_TIMEOUT = 0x00000102
    INFINITE = 0xFFFFFFFF
    SYNCHRONIZE = 0x00100000

    # Load Windows API functions
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenFileMappingA.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.BOOL, ctypes.c_char_p]
    kernel32.OpenFileMappingA.restype = ctypes.wintypes.HANDLE
    kernel32.MapViewOfFile.argtypes = [ctypes.wintypes.HANDLE, ctypes.wintypes.DWORD,
                                       ctypes.wintypes.DWORD, ctypes.wintypes.DWORD, ctypes.c_size_t]
    kernel32.MapViewOfFile.restype = ctypes.wintypes.LPVOID
    kernel32.UnmapViewOfFile.argtypes = [ctypes.wintypes.LPVOID]
    kernel32.UnmapViewOfFile.restype = ctypes.wintypes.BOOL
    kernel32.CreateEventA.argtypes = [ctypes.wintypes.LPVOID, ctypes.wintypes.BOOL, ctypes.wintypes.BOOL, ctypes.c_char_p]
    kernel32.CreateEventA.restype = ctypes.wintypes.HANDLE
    kernel32.WaitForMultipleObjects.argtypes = [ctypes.wintypes.DWORD, ctypes.POINTER(ctypes.wintypes.HANDLE),
                                                ctypes.wintypes.BOOL, ctypes.wintypes.DWORD]
    kernel32.WaitForMultipleObjects.restype = ctypes.wintypes.DWORD
    kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
    kernel32.CloseHandle.restype = ctypes.wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [ctypes.wintypes.HANDLE, ctypes.wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = ctypes.wintypes.DWORD
    kernel32.OpenEventA.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.BOOL, ctypes.c_char_p]
    kernel32.OpenEventA.restype = ctypes.wintypes.HANDLE

    class WindowsEvent:
        """Wrapper for Windows Event objects"""

        def __init__(self, name: bytes, access: int = SYNCHRONIZE, create: bool = False, manual_reset: bool = True):
            self.handle = None
            self.name = name
            self.manual_reset = manual_reset
            if self._open(access):
                return
            if create:
                self._create(access)

        def _create(self, access: int) -> bool:
            self.handle = kernel32.CreateEventA(None, self.manual_reset, False, self.name)
            return self.handle is not None

        def _open(self, access: int) -> bool:
            self.handle = kernel32.OpenEventA(access, False, self.name)
            return self.handle is not None

        def is_valid(self) -> bool:
            return self.handle is not None

        def close(self):
            if self.handle:
                kernel32.CloseHandle(self.handle)
                self.handle = None

        def wait(self, timeout: Optional[int] = None) -> bool:
            if not self.is_valid():
                return False
            if timeout is None:
                timeout = INFINITE
            result = kernel32.WaitForSingleObject(self.handle, timeout)
            if result == WAIT_OBJECT_0:
                return True
            elif result == WAIT_TIMEOUT:
                return False
            else:
                raise ctypes.WinError()

        def __del__(self):
            self.close()

    class SharedMemoryMapping:
        """Wrapper for Windows shared memory mapping"""

        def __init__(self, name: bytes, size: int, access: int = FILE_MAP_READ):
            self.name = name
            self.size = size
            self.h_map_file = None
            self.p_data = None
            self._open(access)

        def _open(self, access: int) -> bool:
            self.h_map_file = kernel32.OpenFileMappingA(access, False, self.name)
            if not self.h_map_file:
                return False
            self.p_data = kernel32.MapViewOfFile(self.h_map_file, access, 0, 0, self.size)
            return self.p_data is not None

        def is_valid(self) -> bool:
            return self.h_map_file is not None and self.p_data is not None

        def get_pointer(self):
            return self.p_data

        def close(self):
            if self.p_data:
                kernel32.UnmapViewOfFile(self.p_data)
                self.p_data = None
            if self.h_map_file:
                kernel32.CloseHandle(self.h_map_file)
                self.h_map_file = None

        def __del__(self):
            self.close()

    class MultiEventWaiter:
        """Helper for waiting on multiple Windows events"""

        def __init__(self, events: list):
            self.events = events
            self.event_handles = (ctypes.wintypes.HANDLE * len(events))(
                *[event.handle for event in events]
            )

        def wait(self, timeout_ms: int = INFINITE) -> int:
            result = kernel32.WaitForMultipleObjects(
                len(self.events), self.event_handles, False, timeout_ms
            )
            if WAIT_OBJECT_0 <= result < WAIT_OBJECT_0 + len(self.events):
                return result - WAIT_OBJECT_0
            elif result == WAIT_TIMEOUT:
                return -2
            else:
                return -1

    class SharedDataStruct(ctypes.Structure):
        _fields_ = [
            ("version", ctypes.c_uint32),
            ("data_size", ctypes.c_uint32),
            ("sequence_number", ctypes.c_uint32),
            ("writer_pid", ctypes.c_uint32),
            ("timestamp_us", ctypes.c_uint64),
            ("message", ctypes.c_char * MESSAGE_BUFFER_SIZE),
        ]

    class SharedMemoryReader:
        """Python reader for Windows shared memory created by C++ writer"""

        def __init__(self, name: bytes, size: int):
            self.name = name
            self.size = size
            self.shared_memory = None
            self.last_sequence_number = 0
            self.connected = False

            try:
                self.update_event = WindowsEvent(self.name + b"_update_event", create=True)
            except Exception:
                self.update_event = None

        def open(self) -> bool:
            if self.connected:
                return True
            try:
                self.shared_memory = SharedMemoryMapping(self.name, self.size, FILE_MAP_READ)
                if not self.shared_memory.is_valid():
                    self.shared_memory = None
                    return False
                if not self.shared_memory.get_pointer():
                    self.shared_memory = None
                    return False
                shared_struct = ctypes.cast(
                    self.shared_memory.get_pointer(),
                    ctypes.POINTER(SharedDataStruct)
                ).contents
                if shared_struct.version != SHARED_DATA_VERSION:
                    print(f"Shared data version mismatch. Expected: {SHARED_DATA_VERSION}, Found: {shared_struct.version}")
                    self.shared_memory.close()
                    self.shared_memory = None
                    return False
                self.last_sequence_number = shared_struct.sequence_number
                self.connected = True
                return True
            except Exception as e:
                print(f"Error opening shared memory: {e}")
                if self.shared_memory:
                    self.shared_memory.close()
                    self.shared_memory = None
                return False

        def read(self, timeout_ms: Optional[int] = None) -> Optional[SharedData]:
            if timeout_ms is None:
                timeout_ms = INFINITE
            try:
                result = self.update_event.wait(timeout_ms)
                if not self.connected:
                    if not self.open():
                        return None
                if result:
                    return self._read_current_data()
                else:
                    return None
            except Exception as e:
                print(f"Error during read: {e}")
                return None

        def read_current(self) -> Optional[SharedData]:
            if not self.connected:
                return None
            return self._read_current_data()

        def _read_current_data(self) -> Optional[SharedData]:
            if not self.connected or not self.shared_memory:
                return None
            try:
                pointer = self.shared_memory.get_pointer()
                if not pointer:
                    return None
                shared_struct = ctypes.cast(
                    pointer,
                    ctypes.POINTER(SharedDataStruct)
                ).contents
                message = shared_struct.message.decode('utf-8', errors='replace').rstrip('\x00')
                self.last_sequence_number = shared_struct.sequence_number
                return SharedData(
                    version=shared_struct.version,
                    data_size=shared_struct.data_size,
                    sequence_number=shared_struct.sequence_number,
                    writer_pid=shared_struct.writer_pid,
                    timestamp_us=shared_struct.timestamp_us,
                    message=message
                )
            except Exception as e:
                print(f"Error reading shared data: {e}")
                return None

        def close(self):
            if self.update_event:
                self.update_event.close()
                self.update_event = None
            if self.shared_memory:
                self.shared_memory.close()
                self.shared_memory = None
            self.connected = False

        def is_connected(self) -> bool:
            return self.connected

        def get_last_sequence(self) -> int:
            return self.last_sequence_number

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            self.close()

        def _ensure_event_initialized(self) -> bool:
            if self.update_event is not None and self.update_event.is_valid():
                return True
            try:
                if self.update_event:
                    self.update_event.close()
                self.update_event = WindowsEvent(self.name + b"_update_event", SYNCHRONIZE)
                if not self.update_event.is_valid():
                    return False
                return True
            except Exception:
                return False

else:
    # Non-Windows stub — DCS IPC thread idles harmlessly, UDP path handles telemetry
    import logging

    class SharedMemoryReader:
        """No-op stub for non-Windows platforms."""

        def __init__(self, name: bytes, size: int):
            self.name = name
            self.size = size
            self.connected = False
            logging.debug(f"SharedMemoryReader: shared memory not available on {sys.platform}, DCS IPC disabled")

        def open(self) -> bool:
            return False

        def read(self, timeout_ms: Optional[int] = None) -> Optional[SharedData]:
            # Block for the timeout duration so DcsIpcThread doesn't busy-loop
            if timeout_ms and timeout_ms > 0:
                time.sleep(min(timeout_ms / 1000.0, 1.0))
            return None

        def read_current(self) -> Optional[SharedData]:
            return None

        def close(self):
            pass

        def is_connected(self) -> bool:
            return False

        def get_last_sequence(self) -> int:
            return 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            self.close()
