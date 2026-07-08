"""
Standalone volume-control diagnostic for SARVOS on Windows.

Run this on your real Windows machine (NOT inside SARVOS) to capture the
exact reason volume control reports "not supported":

    python diagnose_volume.py

It tries each step of the pycaw/COM volume path separately and prints
precisely where it fails and with what error -- so we fix the real cause
rather than guessing. Nothing here changes any system state (it only
READS the current volume); it does not modify volume, mute, or anything
else.
"""

import sys
import traceback


def main():
    print(f"Python: {sys.version}")
    print(f"Platform: {sys.platform}")
    print("-" * 60)

    # Step 1: can we import comtypes at all?
    try:
        import comtypes
        print(f"[1/6] comtypes import OK (version {getattr(comtypes, '__version__', '?')})")
    except Exception as e:
        print(f"[1/6] comtypes import FAILED: {e!r}")
        print("      -> pip install comtypes")
        traceback.print_exc()
        return

    # Step 2: can we import pycaw?
    try:
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        print("[2/6] pycaw import OK")
    except Exception as e:
        print(f"[2/6] pycaw import FAILED: {e!r}")
        print("      -> pip install pycaw")
        traceback.print_exc()
        return

    # Step 3: does explicit COM initialization change anything? This is
    # the prime suspect -- pycaw needs COM initialized on THIS thread, and
    # some contexts don't do it automatically.
    com_initialized = False
    try:
        import comtypes
        comtypes.CoInitialize()
        com_initialized = True
        print("[3/6] CoInitialize() OK")
    except Exception as e:
        print(f"[3/6] CoInitialize() raised (may be harmless if already init'd): {e!r}")

    # Step 4: get the speakers device.
    try:
        devices = AudioUtilities.GetSpeakers()
        print(f"[4/6] GetSpeakers() OK: {devices!r}")
    except Exception as e:
        print(f"[4/6] GetSpeakers() FAILED: {e!r}")
        traceback.print_exc()
        _uninit(com_initialized)
        return

    # Step 5: get the volume interface. Newer pycaw (1.4.16+) exposes it
    # as a .EndpointVolume property; older pycaw needs Activate()+cast().
    try:
        endpoint = getattr(devices, "EndpointVolume", None)
        if endpoint is not None:
            volume = endpoint
            print("[5/6] Got volume interface via new .EndpointVolume property OK")
        else:
            from ctypes import cast, POINTER
            from comtypes import CLSCTX_ALL
            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            volume = cast(interface, POINTER(IAudioEndpointVolume))
            print("[5/6] Got volume interface via legacy Activate()+cast() OK")
    except Exception as e:
        print(f"[5/6] Getting volume interface FAILED: {e!r}")
        traceback.print_exc()
        _uninit(com_initialized)
        return

    # Step 6: actually read the current volume (read-only, changes nothing).
    try:
        current = volume.GetMasterVolumeLevelScalar()
        print(f"[6/6] GetMasterVolumeLevelScalar() OK: current volume is {round(current*100)}%")
        print("-" * 60)
        print("SUCCESS: the full volume path works when COM is initialized "
              "on this thread.")
        print("If SARVOS still reports 'not supported', the fix is to call "
              "comtypes.CoInitialize() before using the volume interface.")
    except Exception as e:
        print(f"[6/6] GetMasterVolumeLevelScalar() FAILED: {e!r}")
        traceback.print_exc()

    _uninit(com_initialized)


def _uninit(com_initialized):
    if com_initialized:
        try:
            import comtypes
            comtypes.CoUninitialize()
        except Exception:
            pass


if __name__ == "__main__":
    main()
