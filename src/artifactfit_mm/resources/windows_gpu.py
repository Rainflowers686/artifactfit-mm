from __future__ import annotations

import ctypes
import os
import re
from contextlib import suppress
from ctypes import wintypes
from typing import Any

PDH_FMT_LARGE = 0x00000400
PDH_MORE_DATA = 0x800007D2
ERROR_SUCCESS = 0


class _PdhValueUnion(ctypes.Union):
    _fields_ = [
        ("longValue", wintypes.LONG),
        ("doubleValue", ctypes.c_double),
        ("largeValue", ctypes.c_longlong),
        ("AnsiStringValue", ctypes.c_char_p),
        ("WideStringValue", wintypes.LPWSTR),
    ]


class _PdhFmtCounterValue(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = [("CStatus", wintypes.DWORD), ("value", _PdhValueUnion)]


class _PdhFmtCounterValueItem(ctypes.Structure):
    _fields_ = [("szName", wintypes.LPWSTR), ("FmtValue", _PdhFmtCounterValue)]


class WindowsGpuProcessMemorySampler:
    """Read Windows WDDM per-process dedicated GPU memory through PDH."""

    def __init__(self) -> None:
        self.available = False
        self.reason = "PDH_NOT_WINDOWS"
        self._pdh: Any = None
        self._query = ctypes.c_void_p()
        self._counter = ctypes.c_void_p()
        if os.name != "nt":
            return
        try:
            self._pdh = ctypes.WinDLL("pdh.dll")
            self._pdh.PdhOpenQueryW.argtypes = [
                wintypes.LPCWSTR,
                ctypes.c_size_t,
                ctypes.POINTER(ctypes.c_void_p),
            ]
            self._pdh.PdhOpenQueryW.restype = wintypes.LONG
            self._pdh.PdhAddEnglishCounterW.argtypes = [
                ctypes.c_void_p,
                wintypes.LPCWSTR,
                ctypes.c_size_t,
                ctypes.POINTER(ctypes.c_void_p),
            ]
            self._pdh.PdhAddEnglishCounterW.restype = wintypes.LONG
            self._pdh.PdhCollectQueryData.argtypes = [ctypes.c_void_p]
            self._pdh.PdhCollectQueryData.restype = wintypes.LONG
            self._pdh.PdhGetFormattedCounterArrayW.argtypes = [
                ctypes.c_void_p,
                wintypes.DWORD,
                ctypes.POINTER(wintypes.DWORD),
                ctypes.POINTER(wintypes.DWORD),
                ctypes.c_void_p,
            ]
            self._pdh.PdhGetFormattedCounterArrayW.restype = wintypes.LONG
            self._pdh.PdhCloseQuery.argtypes = [ctypes.c_void_p]
            self._pdh.PdhCloseQuery.restype = wintypes.LONG
            status = self._pdh.PdhOpenQueryW(None, 0, ctypes.byref(self._query))
            if status != ERROR_SUCCESS:
                self.reason = f"PDH_OPEN_QUERY_FAILED:{status & 0xFFFFFFFF:#x}"
                return
            status = self._pdh.PdhAddEnglishCounterW(
                self._query,
                r"\GPU Process Memory(*)\Dedicated Usage",
                0,
                ctypes.byref(self._counter),
            )
            if status != ERROR_SUCCESS:
                self.reason = f"PDH_ADD_COUNTER_FAILED:{status & 0xFFFFFFFF:#x}"
                self.close()
                return
            self.available = True
            self.reason = "WINDOWS_PDH_GPU_PROCESS_DEDICATED_USAGE"
        except Exception as exc:
            self.reason = f"PDH_INIT_FAILED:{type(exc).__name__}"
            self.close()

    def sample(self, pids: set[int]) -> int | None:
        if not self.available or self._pdh is None:
            return None
        try:
            status = self._pdh.PdhCollectQueryData(self._query)
            if status != ERROR_SUCCESS:
                self.reason = f"PDH_COLLECT_FAILED:{status & 0xFFFFFFFF:#x}"
                return None
            buffer_size = wintypes.DWORD(0)
            item_count = wintypes.DWORD(0)
            status = self._pdh.PdhGetFormattedCounterArrayW(
                self._counter,
                PDH_FMT_LARGE,
                ctypes.byref(buffer_size),
                ctypes.byref(item_count),
                None,
            )
            if (status & 0xFFFFFFFF) != PDH_MORE_DATA:
                if status == ERROR_SUCCESS and item_count.value == 0:
                    return 0
                self.reason = f"PDH_SIZE_QUERY_FAILED:{status & 0xFFFFFFFF:#x}"
                return None
            buffer = ctypes.create_string_buffer(buffer_size.value)
            status = self._pdh.PdhGetFormattedCounterArrayW(
                self._counter,
                PDH_FMT_LARGE,
                ctypes.byref(buffer_size),
                ctypes.byref(item_count),
                ctypes.cast(buffer, ctypes.c_void_p),
            )
            if status != ERROR_SUCCESS:
                self.reason = f"PDH_ARRAY_QUERY_FAILED:{status & 0xFFFFFFFF:#x}"
                return None
            items = ctypes.cast(
                buffer,
                ctypes.POINTER(_PdhFmtCounterValueItem * item_count.value),
            ).contents
            total = 0
            for item in items:
                match = re.search(r"(?:^|_)pid_(\d+)(?:_|$)", item.szName or "", re.IGNORECASE)
                if not match or int(match.group(1)) not in pids:
                    continue
                if item.FmtValue.CStatus == ERROR_SUCCESS:
                    total += max(0, int(item.FmtValue.largeValue))
            return total
        except Exception as exc:
            self.reason = f"PDH_SAMPLE_FAILED:{type(exc).__name__}"
            return None

    def close(self) -> None:
        if self._pdh is not None and self._query.value:
            with suppress(Exception):
                self._pdh.PdhCloseQuery(self._query)
        self._query = ctypes.c_void_p()
