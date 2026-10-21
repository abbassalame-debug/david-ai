# ableton_bridge.py — AbletonOSC integration for David
# Requires: pip install python-osc
# Requires: AbletonOSC Max for Live device installed and active in Ableton
# AbletonOSC: https://github.com/ideoforms/AbletonOSC
# Default ports: David sends to 11000, receives on 11001

import os
import sys
import site
for _sp in site.getsitepackages():
    if _sp not in sys.path:
        sys.path.insert(0, _sp)

import time
import threading
from pythonosc import udp_client, dispatcher, osc_server

ABLETON_HOST      = "127.0.0.1"
ABLETON_SEND_PORT = 11000   # AbletonOSC listens here
ABLETON_RECV_PORT = int(os.environ.get("DAVID_OSC_RECV_PORT", 11001))   # configurable per process

_client = None
_responses = {}
_server_thread = None


def _get_client():
    global _client
    if _client is None:
        _client = udp_client.SimpleUDPClient(ABLETON_HOST, ABLETON_SEND_PORT)
    return _client


def _handle_response(address, *args):
    _responses[address] = args


def _start_listener():
    global _server_thread
    if _server_thread and _server_thread.is_alive():
        return
    d = dispatcher.Dispatcher()
    d.set_default_handler(_handle_response)
    server = osc_server.ThreadingOSCUDPServer(("127.0.0.1", ABLETON_RECV_PORT), d)
    _server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    _server_thread.start()


def _send(address, *args):
    _get_client().send_message(address, list(args) if args else [])


def _send_and_wait(address, *args, timeout=2.0):
    _start_listener()
    _responses.pop(address, None)
    _send(address, *args)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if address in _responses:
            return _responses[address]
        time.sleep(0.05)
    return None


# ── Transport ─────────────────────────────────────────────────────────────────

def play():
    _send("/live/song/start_playing")
    return "Ableton: playing"


def stop():
    _send("/live/song/stop_playing")
    return "Ableton: stopped"


def get_bpm():
    result = _send_and_wait("/live/song/get/tempo")
    return result[0] if result else None


def set_bpm(bpm: float):
    _send("/live/song/set/tempo", float(bpm))
    return f"Ableton: BPM set to {bpm}"


# ── Tracks ────────────────────────────────────────────────────────────────────

def create_midi_track(index: int = -1):
    """Create a MIDI track. index=-1 appends at end."""
    _send("/live/song/create_midi_track", index)
    return f"Ableton: MIDI track created at index {index}"


def create_audio_track(index: int = -1):
    _send("/live/song/create_audio_track", index)
    return f"Ableton: audio track created at index {index}"


def arm_track(track_index: int):
    _send("/live/track/set/arm", track_index, 1)
    return f"Ableton: track {track_index} armed"


def set_track_name(track_index: int, name: str):
    _send("/live/track/set/name", track_index, name)
    return f"Ableton: track {track_index} renamed to '{name}'"


def set_track_volume(track_index: int, volume: float):
    """volume: 0.0–1.0 (0.85 ≈ 0dB)"""
    _send("/live/track/set/volume", track_index, float(volume))
    return f"Ableton: track {track_index} volume set to {volume}"


# ── Clips ─────────────────────────────────────────────────────────────────────

def create_clip(track_index: int, slot_index: int, length: float = 4.0):
    """Create an empty MIDI clip. length in bars."""
    _send("/live/clip_slot/create_clip", track_index, slot_index, float(length))
    return f"Ableton: clip created at track {track_index}, slot {slot_index}, {length} bars"


def add_notes(track_index: int, slot_index: int, notes: list):
    """
    Add MIDI notes to a clip.
    notes: list of (pitch, start_time, duration, velocity, mute)
    Example: [(60, 0.0, 0.5, 100, 0), (64, 0.5, 0.5, 90, 0)]
    """
    flat = []
    for note in notes:
        flat.extend(note)
    _send("/live/clip/add/notes", track_index, slot_index, *flat)
    return f"Ableton: {len(notes)} notes added to track {track_index}, slot {slot_index}"


def fire_clip(track_index: int, slot_index: int):
    _send("/live/clip_slot/fire", track_index, slot_index)
    return f"Ableton: clip fired at track {track_index}, slot {slot_index}"


# ── Session state ─────────────────────────────────────────────────────────────

def get_track_count():
    result = _send_and_wait("/live/song/get/num_tracks")
    return result[0] if result else None


def is_playing():
    result = _send_and_wait("/live/song/get/is_playing")
    return bool(result[0]) if result else False


def get_session_info():
    bpm   = get_bpm()
    playing = is_playing()
    tracks  = get_track_count()
    return {
        "bpm":     bpm,
        "playing": playing,
        "tracks":  tracks,
    }


# ── Quick actions for Beat Architect ─────────────────────────────────────────

def quick_beat(bpm: float = 120.0, bars: int = 4):
    """
    Create a basic 4/4 kick pattern on a new MIDI track at given BPM.
    Kick on beats 1 and 3 (MIDI note 36 = C1).
    """
    set_bpm(bpm)
    create_midi_track(0)
    set_track_name(0, "Kick")
    create_clip(0, 0, float(bars))

    # Kick on every beat 1 and 3 across all bars
    notes = []
    for bar in range(bars):
        notes.append((36, bar * 4.0,       0.25, 100, 0))  # beat 1
        notes.append((36, bar * 4.0 + 2.0, 0.25, 90,  0))  # beat 3

    add_notes(0, 0, notes)
    fire_clip(0, 0)
    return f"Beat started: {bpm} BPM, {bars} bars, kick on 1 and 3"


if __name__ == "__main__":
    print("Testing AbletonOSC bridge...")
    print("Make sure Ableton is open with the AbletonOSC device active.")
    info = get_session_info()
    print(f"Session: {info}")
