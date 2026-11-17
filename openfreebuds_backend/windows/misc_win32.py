import subprocess
import sys
import winreg
from pathlib import Path

# Portable mode if file name ends with `_portable.exe`
# or location folder contains `is_portable` flag-file.
IS_PORTABLE = sys.executable.endswith("_portable.exe") \
              or (Path(sys.executable).parent / "is_portable").is_file()

# Feature flags
AUTOSTART_AVAILABLE = not IS_PORTABLE
AUTOUPDATE_AVAILABLE = not IS_PORTABLE          # TODO: mmk_updater add portable replace support
GLOBAL_HOTKEYS_AVAILABLE = True

no_console = subprocess.STARTUPINFO()
no_console.dwFlags |= subprocess.STARTF_USESHOWWINDOW


def get_app_storage_path():
    # In portable mode, store all app-related data near executable
    if IS_PORTABLE:
        return Path(sys.executable).parent / "data"

    return Path.home() / "AppData" / "Roaming" / "openfreebuds"


def open_file(path):
    subprocess.Popen(["notepad.exe", path])


def is_run_at_boot():
    with winreg.OpenKey(
            key=winreg.HKEY_CURRENT_USER,
            sub_key=r'Software\Microsoft\Windows\CurrentVersion\Run',
            reserved=0,
            access=winreg.KEY_ALL_ACCESS,
    ) as key:
        idx = 0
        while idx < 1_000:     # Max 1.000 values
            try:
                key_name, _, _ = winreg.EnumValue(key, idx)
                if key_name == "openfreebuds":
                    return True
                idx += 1
            except OSError:
                break
    return False


async def set_run_at_boot(val):
    with winreg.OpenKey(
            key=winreg.HKEY_CURRENT_USER,
            sub_key=r'Software\Microsoft\Windows\CurrentVersion\Run',
            reserved=0,
            access=winreg.KEY_ALL_ACCESS,
    ) as key:
        try:
            if val:
                winreg.SetValueEx(key, "openfreebuds", 0, winreg.REG_SZ, sys.executable)
            else:
                winreg.DeleteValue(key, "openfreebuds")
        except OSError:
            return


def get_active_bluetooth_audio_codec(mac_address=None):
    clean_mac = mac_address.replace(":", "").replace("-", "").upper() if mac_address else None
    render_key = r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render"
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, render_key)
    except Exception:
        return None

    num_subkeys = winreg.QueryInfoKey(root)[0]
    for i in range(num_subkeys):
        guid = winreg.EnumKey(root, i)
        try:
            prop_key = winreg.OpenKey(root, guid + r"\Properties")
        except Exception:
            continue

        endpoint_id = ""
        driver_inf = ""
        device_name = ""
        fmt_bytes = None

        j = 0
        while True:
            try:
                name, val, _ = winreg.EnumValue(prop_key, j)
                if name == "{b3f8fa53-0004-438e-9003-51a46e139bfc},2":
                    endpoint_id = str(val)
                elif name == "{b3f8fa53-0004-438e-9003-51a46e139bfc},6":
                    device_name = str(val)
                elif name == "{83da6326-97a6-4088-9453-a1923f573b29},3":
                    driver_inf = str(val)
                elif name == "{f19f064d-082c-4e27-bc73-6882a1bb8e4c},0":
                    fmt_bytes = val
                j += 1
            except OSError:
                break

        if not fmt_bytes or "BTHENUM" not in endpoint_id:
            continue

        if clean_mac and clean_mac not in endpoint_id.upper():
            continue

        import struct
        offset = 8 if len(fmt_bytes) > 10 and fmt_bytes[8:10] in (b"\xfe\xff", b"\x01\x00") else 0
        if len(fmt_bytes) >= offset + 16:
            wFormatTag, nChannels, nSamplesPerSec = struct.unpack_from("<HHI", fmt_bytes, offset)
            wBitsPerSample = struct.unpack_from("<H", fmt_bytes, offset + 14)[0] if len(fmt_bytes) >= offset + 16 else 16
        else:
            nSamplesPerSec = 48000
            wBitsPerSample = 16

        driver_lower = driver_inf.lower()
        if nSamplesPerSec >= 88200 or "alt_a2dp" in driver_lower or "a2dpdriver" in driver_lower:
            codec = "LDAC"
            theme = "ldac"
        elif nSamplesPerSec == 48000 or "a2dp" in driver_lower:
            codec = "AAC"
            theme = "aac"
        else:
            codec = "SBC"
            theme = "sbc"

        rate_str = f"{nSamplesPerSec / 1000:.1f}".rstrip("0").rstrip(".") + " kHz"
        return {
            "codec": codec,
            "rate": nSamplesPerSec,
            "bits": wBitsPerSample,
            "label": f"{codec} • {rate_str}",
            "theme": theme,
            "tooltip": f"Codec Bluetooth A2DP: {codec}\nFrequenza di campionamento: {nSamplesPerSec:,} Hz\nProfondit\u00e0: {wBitsPerSample}-bit\nDispositivo: {device_name}"
        }

    return None


# ---------------------------------------------------------
# Windows Core Audio (MMDeviceAPI) Mute / Volume Helpers
# ---------------------------------------------------------

import ctypes
from ctypes import POINTER, c_long, c_int, byref, c_void_p, c_wchar_p
from ctypes.wintypes import DWORD, BOOL

_ole32 = ctypes.windll.ole32


class _COM_GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8)
    ]

    @classmethod
    def from_str(cls, s: str):
        g = cls()
        _ole32.IIDFromString(c_wchar_p(s), byref(g))
        return g


_CLSID_MMDeviceEnumerator = _COM_GUID.from_str("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
_IID_IMMDeviceEnumerator = _COM_GUID.from_str("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
_IID_IAudioEndpointVolume = _COM_GUID.from_str("{5CDF2C82-841E-4546-9722-0CF74078229A}")


def _get_endpoint_volume(dataflow: int):
    """
    Retrieves IAudioEndpointVolume COM interface.
    dataflow: 0 = eRender (Speakers), 1 = eCapture (Microphone)
    """
    _ole32.CoInitialize(None)
    enumerator = c_void_p()
    hr = _ole32.CoCreateInstance(
        byref(_CLSID_MMDeviceEnumerator), None, 1, byref(_IID_IMMDeviceEnumerator), byref(enumerator)
    )
    if hr != 0 or not enumerator:
        return None, None

    enum_vtbl = ctypes.cast(ctypes.cast(enumerator, POINTER(c_void_p)).contents, POINTER(c_void_p))
    GetDefaultAudioEndpoint = ctypes.WINFUNCTYPE(c_long, c_void_p, c_int, c_int, POINTER(c_void_p))(enum_vtbl[4])
    device = c_void_p()
    hr = GetDefaultAudioEndpoint(enumerator, dataflow, 0, byref(device))
    # Release enumerator
    ctypes.WINFUNCTYPE(c_long, c_void_p)(enum_vtbl[2])(enumerator)

    if hr != 0 or not device:
        return None, None

    dev_vtbl = ctypes.cast(ctypes.cast(device, POINTER(c_void_p)).contents, POINTER(c_void_p))
    Activate = ctypes.WINFUNCTYPE(c_long, c_void_p, POINTER(_COM_GUID), DWORD, c_void_p, POINTER(c_void_p))(dev_vtbl[3])
    endpoint_vol = c_void_p()
    hr = Activate(device, byref(_IID_IAudioEndpointVolume), 1, None, byref(endpoint_vol))
    # Release device
    ctypes.WINFUNCTYPE(c_long, c_void_p)(dev_vtbl[2])(device)

    if hr != 0 or not endpoint_vol:
        return None, None

    vol_vtbl = ctypes.cast(ctypes.cast(endpoint_vol, POINTER(c_void_p)).contents, POINTER(c_void_p))
    return endpoint_vol, vol_vtbl


def get_audio_mute(dataflow: int) -> bool:
    """
    Queries current mute status for default audio device.
    dataflow: 0 = render (output/speaker), 1 = capture (microphone)
    """
    try:
        vol, vol_vtbl = _get_endpoint_volume(dataflow)
        if not vol:
            return False
        GetMute = ctypes.WINFUNCTYPE(c_long, c_void_p, POINTER(BOOL))(vol_vtbl[15])
        is_muted = BOOL()
        hr = GetMute(vol, byref(is_muted))
        ctypes.WINFUNCTYPE(c_long, c_void_p)(vol_vtbl[2])(vol)
        return bool(is_muted.value) if hr == 0 else False
    except Exception:
        return False


def set_audio_mute(dataflow: int, muted: bool) -> bool:
    """
    Sets mute status for default audio device.
    dataflow: 0 = render (output/speaker), 1 = capture (microphone)
    """
    try:
        vol, vol_vtbl = _get_endpoint_volume(dataflow)
        if not vol:
            return False
        SetMute = ctypes.WINFUNCTYPE(c_long, c_void_p, BOOL, c_void_p)(vol_vtbl[14])
        hr = SetMute(vol, BOOL(muted), None)
        ctypes.WINFUNCTYPE(c_long, c_void_p)(vol_vtbl[2])(vol)
        return hr == 0
    except Exception:
        return False


def toggle_audio_mute(dataflow: int) -> bool:
    curr = get_audio_mute(dataflow)
    new_state = not curr
    set_audio_mute(dataflow, new_state)
    return new_state


def get_microphone_mute() -> bool:
    return get_audio_mute(1)


def set_microphone_mute(muted: bool) -> bool:
    return set_audio_mute(1, muted)


def toggle_microphone_mute() -> bool:
    return toggle_audio_mute(1)


def get_speaker_mute() -> bool:
    return get_audio_mute(0)


def set_speaker_mute(muted: bool) -> bool:
    return set_audio_mute(0, muted)


def toggle_speaker_mute() -> bool:
    return toggle_audio_mute(0)


