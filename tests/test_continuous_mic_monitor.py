import time

from voice.audio_io import ContinuousMicMonitor


def test_monitor_starts_and_stops_without_raising_even_with_no_mic():
    """This sandbox has no microphone hardware -- _run() will fail
    immediately when opening the InputStream. That failure must be
    caught gracefully (not crash the calling thread), and start()/stop()
    must both return cleanly regardless."""
    monitor = ContinuousMicMonitor()
    monitor.start()
    time.sleep(0.3)  # give the background thread a moment to fail internally
    monitor.stop()  # must not hang or raise


def test_current_rms_returns_a_valid_reading_on_real_hardware():
    """This sandbox has no microphone, so current_rms() stays exactly 0.0
    here. On real hardware, this correctly picks up real ambient room
    noise instead. That reading is NOT something a test should assert a
    specific value or ceiling for -- it genuinely varies by machine, mic
    gain, and room noise at the moment the test happens to run (confirmed
    directly: 0.000176 in one real test run, 0.0159 in another, on
    different occasions -- neither is a bug, both are correct real
    ambient noise readings that simply differ). This test only confirms
    the mechanism produces a valid, sane reading -- not "quiet enough,"
    which isn't something a test running on someone else's real
    microphone can control."""
    monitor = ContinuousMicMonitor()
    monitor.start()
    time.sleep(0.3)
    try:
        rms = monitor.current_rms()
        assert isinstance(rms, float)
        assert rms >= 0.0
        assert rms < 1.0  # sanity bound: RMS of normalized audio can't exceed ~1.0
    finally:
        monitor.stop()


def test_is_loud_enough_threshold_logic_is_deterministic():
    """The actual decision logic (is RMS above a threshold) should be
    tested deterministically, independent of real ambient noise -- set
    the internal reading directly rather than depending on whatever the
    real microphone happens to pick up at test time."""
    monitor = ContinuousMicMonitor()
    with monitor._lock:
        monitor._current_rms = 0.05
    assert monitor.is_loud_enough(threshold=0.02) is True
    assert monitor.is_loud_enough(threshold=0.08) is False


def test_is_loud_enough_at_exact_threshold_boundary():
    monitor = ContinuousMicMonitor()
    with monitor._lock:
        monitor._current_rms = 0.05
    assert monitor.is_loud_enough(threshold=0.05) is False  # strictly greater-than, not >=


def test_stop_is_safe_to_call_without_start():
    monitor = ContinuousMicMonitor()
    monitor.stop()  # must not raise even though start() was never called


def test_stop_is_idempotent():
    monitor = ContinuousMicMonitor()
    monitor.start()
    time.sleep(0.2)
    monitor.stop()
    monitor.stop()  # calling stop() twice must not raise
