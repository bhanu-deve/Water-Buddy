r"""Voice check: does the character really talk through your speakers?

Run:   .\.venv\Scripts\python.exe tools\voice_check.py [--volume 70]

THIS ONE MAKES SOUND ON PURPOSE (about 20 seconds). It says one sentence silently (volume 0) and then one
sentence with each Windows voice installed on this computer, at the volume you choose (default 70 %).

While it speaks it also reads the loudness that Windows measures for THIS PROGRAM'S OWN audio session (every
program has its own "peak meter" in Windows), so the result does not depend on your ears alone, and music or a
video playing at the same time cannot confuse it:
  * the silent sentence   -> this program's meter should stay at (almost) zero
  * the audible sentences -> it should jump, proving the sound really goes out to your audio device
The loudness of ALL sound on your speakers is shown too, for information. The script never moves the mouse and
never clicks.

    --meter-test   only check that the meters can be read (silent)
"""
import argparse
import os
import sys
import time
from ctypes import HRESULT, POINTER, c_float, c_int, c_void_p, cast
from ctypes.wintypes import BOOL, DWORD, LPCWSTR, LPWSTR
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import comtypes  # noqa: E402  (installed together with pyttsx3)
from comtypes import COMMETHOD, GUID, IUnknown  # noqa: E402
from PySide6.QtCore import QCoreApplication  # noqa: E402

from water_assistant.speech_service import SpeechService  # noqa: E402


# --- the bit of Windows "Core Audio" needed to read peak meters (declared exactly as in Windows' audiopolicy.h) ---
class IAudioMeterInformation(IUnknown):
    _iid_ = GUID("{C02216F6-8C67-4B5B-9D00-D008E73E0064}")
    _methods_ = (
        COMMETHOD([], HRESULT, "GetPeakValue", (["out"], POINTER(c_float), "pfPeak")),
        COMMETHOD([], HRESULT, "GetMeteringChannelCount", (["out"], POINTER(DWORD), "pnChannelCount")),
        COMMETHOD([], HRESULT, "GetChannelsPeakValues", (["in"], DWORD, "u32ChannelCount"), (["in"], POINTER(c_float), "afPeakValues")),
        COMMETHOD([], HRESULT, "QueryHardwareSupport", (["out"], POINTER(DWORD), "pdwHardwareSupportMask")),
    )


class IAudioSessionControl(IUnknown):
    _iid_ = GUID("{F4B1A599-7266-4319-A8CA-E70ACB11E8CD}")
    _methods_ = (
        COMMETHOD([], HRESULT, "GetState", (["out"], POINTER(DWORD), "pRetVal")),
        COMMETHOD([], HRESULT, "GetDisplayName", (["out"], POINTER(LPWSTR), "pRetVal")),
        COMMETHOD([], HRESULT, "SetDisplayName", (["in"], LPCWSTR, "Value"), (["in"], POINTER(GUID), "EventContext")),
        COMMETHOD([], HRESULT, "GetIconPath", (["out"], POINTER(LPWSTR), "pRetVal")),
        COMMETHOD([], HRESULT, "SetIconPath", (["in"], LPCWSTR, "Value"), (["in"], POINTER(GUID), "EventContext")),
        COMMETHOD([], HRESULT, "GetGroupingParam", (["out"], POINTER(GUID), "pRetVal")),
        COMMETHOD([], HRESULT, "SetGroupingParam", (["in"], POINTER(GUID), "Override"), (["in"], POINTER(GUID), "EventContext")),
        COMMETHOD([], HRESULT, "RegisterAudioSessionNotification", (["in"], c_void_p, "NewNotifications")),
        COMMETHOD([], HRESULT, "UnregisterAudioSessionNotification", (["in"], c_void_p, "NewNotifications")),
    )


class IAudioSessionControl2(IAudioSessionControl):
    _iid_ = GUID("{BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D}")
    _methods_ = (
        COMMETHOD([], HRESULT, "GetSessionIdentifier", (["out"], POINTER(LPWSTR), "pRetVal")),
        COMMETHOD([], HRESULT, "GetSessionInstanceIdentifier", (["out"], POINTER(LPWSTR), "pRetVal")),
        COMMETHOD([], HRESULT, "GetProcessId", (["out"], POINTER(DWORD), "pRetVal")),
        COMMETHOD([], HRESULT, "IsSystemSoundsSession"),
        COMMETHOD([], HRESULT, "SetDuckingPreference", (["in"], BOOL, "optOut")),
    )


class IAudioSessionEnumerator(IUnknown):
    _iid_ = GUID("{E2F5BB11-0570-40CA-ACDD-3AA01277DEE8}")
    _methods_ = (
        COMMETHOD([], HRESULT, "GetCount", (["out"], POINTER(c_int), "SessionCount")),
        COMMETHOD([], HRESULT, "GetSession", (["in"], c_int, "SessionCount"), (["out"], POINTER(POINTER(IAudioSessionControl)), "Session")),
    )


class IAudioSessionManager2(IUnknown):
    _iid_ = GUID("{77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F}")
    _methods_ = (
        COMMETHOD([], HRESULT, "GetAudioSessionControl", (["in"], POINTER(GUID), "AudioSessionGuid"), (["in"], DWORD, "StreamFlags"),
                  (["out"], POINTER(POINTER(IAudioSessionControl)), "SessionControl")),
        COMMETHOD([], HRESULT, "GetSimpleAudioVolume", (["in"], POINTER(GUID), "AudioSessionGuid"), (["in"], DWORD, "StreamFlags"),
                  (["out"], POINTER(POINTER(IUnknown)), "AudioVolume")),
        COMMETHOD([], HRESULT, "GetSessionEnumerator", (["out"], POINTER(POINTER(IAudioSessionEnumerator)), "SessionEnum")),
        COMMETHOD([], HRESULT, "RegisterSessionNotification", (["in"], c_void_p, "SessionNotification")),
        COMMETHOD([], HRESULT, "UnregisterSessionNotification", (["in"], c_void_p, "SessionNotification")),
        COMMETHOD([], HRESULT, "RegisterDuckNotification", (["in"], LPCWSTR, "sessionID"), (["in"], c_void_p, "duckNotification")),
        COMMETHOD([], HRESULT, "UnregisterDuckNotification", (["in"], c_void_p, "duckNotification")),
    )


class IMMDevice(IUnknown):
    _iid_ = GUID("{D666063F-1587-4E43-81F1-B948E807363F}")
    _methods_ = (
        COMMETHOD([], HRESULT, "Activate", (["in"], POINTER(GUID), "iid"), (["in"], DWORD, "dwClsCtx"),
                  (["in"], POINTER(DWORD), "pActivationParams"), (["out"], POINTER(POINTER(IUnknown)), "ppInterface")),
        COMMETHOD([], HRESULT, "OpenPropertyStore", (["in"], DWORD, "stgmAccess"), (["out"], POINTER(POINTER(IUnknown)), "ppProperties")),
        COMMETHOD([], HRESULT, "GetId", (["out"], POINTER(LPWSTR), "ppstrId")),
        COMMETHOD([], HRESULT, "GetState", (["out"], POINTER(DWORD), "pdwState")),
    )


class IMMDeviceEnumerator(IUnknown):
    _iid_ = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
    _methods_ = (
        COMMETHOD([], HRESULT, "EnumAudioEndpoints", (["in"], DWORD, "dataFlow"), (["in"], DWORD, "dwStateMask"),
                  (["out"], POINTER(POINTER(IUnknown)), "ppDevices")),
        COMMETHOD([], HRESULT, "GetDefaultAudioEndpoint", (["in"], DWORD, "dataFlow"), (["in"], DWORD, "role"),
                  (["out"], POINTER(POINTER(IMMDevice)), "ppEndpoint")),
    )


class Meters:
    """Peak meters of the default audio output: `everything()` = all sound on it, `ours()` = this program's own sound."""

    def __init__(self) -> None:
        enumerator = comtypes.CoCreateInstance(
            GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}"), IMMDeviceEnumerator, comtypes.CLSCTX_INPROC_SERVER
        )
        device = enumerator.GetDefaultAudioEndpoint(0, 1)  # eRender (playback), eMultimedia
        self._endpoint = cast(device.Activate(IAudioMeterInformation._iid_, comtypes.CLSCTX_ALL, None), POINTER(IAudioMeterInformation))
        self._sessions = cast(device.Activate(IAudioSessionManager2._iid_, comtypes.CLSCTX_ALL, None), POINTER(IAudioSessionManager2))
        self._own_meter = None

    def everything(self) -> float:
        return self._endpoint.GetPeakValue()

    def ours(self) -> float:
        """Peak of this process' audio session (0.0 until it has made any sound)."""
        if self._own_meter is None:
            sessions = self._sessions.GetSessionEnumerator()
            for index in range(sessions.GetCount()):
                control = sessions.GetSession(index)
                if control.QueryInterface(IAudioSessionControl2).GetProcessId() == os.getpid():
                    self._own_meter = control.QueryInterface(IAudioMeterInformation)
                    break
        return self._own_meter.GetPeakValue() if self._own_meter is not None else 0.0


# --------------------------------------------------------------------------------------------------------------
app = QCoreApplication(sys.argv)


def pump(seconds, meters=None):
    """Run Qt's events for a while; returns (loudest of all sound, loudest of this program's sound) seen meanwhile."""
    everything = ours = 0.0
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        if meters is not None:
            everything = max(everything, meters.everything())
            ours = max(ours, meters.ours())
        time.sleep(0.02)
    return everything, ours


def say_and_measure(service, meters, text, volume, timeout=25.0):
    """Speak `text` at `volume`; returns (seconds, loudest of all sound, loudest of our sound, completed?)."""
    done = {}
    handler = lambda _uid, ok: done.setdefault("ok", ok)  # noqa: E731
    service.utteranceFinished.connect(handler)
    service.set_volume(volume)
    started = time.monotonic()
    if not service.speak(text):
        return 0.0, 0.0, 0.0, False
    everything = ours = 0.0
    while "ok" not in done and time.monotonic() - started < timeout:
        e, o = pump(0.1, meters)
        everything, ours = max(everything, e), max(ours, o)
    e, o = pump(0.3, meters)  # the tail of the sound
    service.utteranceFinished.disconnect(handler)
    return time.monotonic() - started, max(everything, e), max(ours, o), bool(done.get("ok"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Does the character really talk through your speakers?")
    parser.add_argument("--volume", type=int, default=70, metavar="0-100", help="volume for the audible sentences (default 70)")
    parser.add_argument("--meter-test", action="store_true", help="only check that the peak meters can be read (silent)")
    arguments = parser.parse_args()
    volume = max(0, min(100, arguments.volume)) / 100

    try:
        meters = Meters()
        meter_problem = None
    except Exception as error:  # noqa: BLE001 - no audio device, or Windows refuses: fall back to "ears only"
        meters, meter_problem = None, f"{type(error).__name__}: {error}"

    if arguments.meter_test:
        if meters is None:
            print(f"FAIL: the peak meters could not be opened: {meter_problem}")
            return 1
        everything, ours = pump(2.0, meters)
        print(f"The peak meters work. Over 2 s right now: all sound on the speakers {everything:.3f}, "
              f"this program's own sound {ours:.3f} (0.000 = silence)")
        return 0

    service = SpeechService()
    service.start()
    waited = time.monotonic()
    while not service.voices and service.available and time.monotonic() - waited < 20:
        pump(0.05)
    if not service.voices:
        print("FAIL: no Windows speech voice could be started on this computer.")
        return 1
    pump(2.0)  # the engine warms up silently

    print("Voices installed:", ", ".join(voice.name.split(" - ")[0] for voice in service.voices))
    print("Default voice   :", next((v.name.split(" - ")[0] for v in service.voices if v.id == service.voice_id), "?"))
    if meters is None:
        print(f"(The peak meters could not be opened: {meter_problem}. Only your ears can judge this time.)")
        baseline_all = baseline_ours = 0.0
    else:
        baseline_all, baseline_ours = pump(1.0, meters)
        print(f"Before speaking: all sound on the speakers {baseline_all:.3f}, this program's own sound {baseline_ours:.3f}")
        if baseline_all > 0.01:
            print("  (other programs are playing sound right now - that is fine: only this program's meter is used for the verdict)")

    results = []

    def run(label, text, level):
        print(f"\n{label}\n   \"{text}\"")
        seconds, everything, ours, ok = say_and_measure(service, meters, text, level)
        print(f"   spoke for {seconds:.1f} s (completed: {ok}); loudest level - this program: {ours:.3f}, "
              f"all sound on the speakers: {everything:.3f}")
        results.append((ours, ok))
        return ours

    silent_ours = run("1. SILENT sentence (volume 0) - you should hear NOTHING from the voice",
                      "This sentence is not meant to be heard.", 0.0)
    audible_ours = []
    for number, voice in enumerate(service.voices, start=2):
        service.set_voice(voice.id)
        name = voice.name.split(" - ")[0].replace("Microsoft ", "").replace(" Desktop", "")
        audible_ours.append(run(f"{number}. AUDIBLE sentence ({round(volume * 100)} %) - you should hear {name}",
                                f"Hi! I am {name}. Remember to drink some water!", volume))

    service.shutdown()

    print("\n================ result ================")
    all_completed = all(ok for _ours, ok in results)
    print(f"[{'PASS' if all_completed else 'FAIL'}] every sentence was spoken to the end")
    verdict_ok = all_completed
    if meters is not None:
        silent_ok = silent_ours <= 0.005
        audible_ok = volume > 0 and all(peak > 0.01 for peak in audible_ours)
        print(f"[{'PASS' if silent_ok else 'FAIL'}] the silent sentence made no sound (this program's level {silent_ours:.3f})")
        print(f"[{'PASS' if audible_ok else 'FAIL'}] the audible sentences really went out to your audio device "
              f"(levels {', '.join(f'{peak:.3f}' for peak in audible_ours)})")
        verdict_ok = verdict_ok and silent_ok and audible_ok
    else:
        print("[----] speaker-level verdict skipped (the meters could not be opened).")
    try:
        heard = input("\nDid you hear the audible sentences and NOT the silent one? (y/n) ").strip().lower().startswith("y")
        print(f"[{'PASS' if heard else 'FAIL'}] you heard it")
        verdict_ok = verdict_ok and heard
    except (EOFError, OSError):  # not run from a keyboard (for example by a script): nobody to ask
        print("\n(Not asked: no keyboard. Please say whether you heard the audible sentences, and NOT the first one.)")
    return 0 if verdict_ok else 1


if __name__ == "__main__":
    sys.exit(main())
