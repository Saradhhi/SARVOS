import os
import tempfile


def test_subscriber_is_called_on_voice_event():
    os.environ["SARVOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
    os.environ["SARVOS_DISABLE_VOICE_PIPELINE"] = "1"

    import importlib
    import api.server as server_module
    importlib.reload(server_module)

    received = []
    server_module.subscribe_to_voice_events(received.append)

    server_module._emit_voice_event({"type": "wake_detected"})

    assert received == [{"type": "wake_detected"}]


def test_multiple_subscribers_all_receive_the_event():
    os.environ["SARVOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
    os.environ["SARVOS_DISABLE_VOICE_PIPELINE"] = "1"

    import importlib
    import api.server as server_module
    importlib.reload(server_module)

    received_a, received_b = [], []
    server_module.subscribe_to_voice_events(received_a.append)
    server_module.subscribe_to_voice_events(received_b.append)

    server_module._emit_voice_event({"type": "response", "text": "hi"})

    assert received_a == [{"type": "response", "text": "hi"}]
    assert received_b == [{"type": "response", "text": "hi"}]


def test_subscriber_exception_does_not_crash_emit_or_block_other_subscribers():
    """A misbehaving subscriber (e.g. desktop.py's window-control callback
    hitting a pywebview error) must not prevent other subscribers -- or
    the WebSocket broadcast queue -- from still receiving the event."""
    os.environ["SARVOS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
    os.environ["SARVOS_DISABLE_VOICE_PIPELINE"] = "1"

    import importlib
    import api.server as server_module
    importlib.reload(server_module)

    def bad_subscriber(event):
        raise RuntimeError("simulated pywebview failure")

    received_good = []
    server_module.subscribe_to_voice_events(bad_subscriber)
    server_module.subscribe_to_voice_events(received_good.append)

    server_module._emit_voice_event({"type": "listening"})  # must not raise

    assert received_good == [{"type": "listening"}]
