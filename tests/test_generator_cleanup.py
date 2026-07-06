"""
The fix in WakeWordDetector.listen() depends on a specific Python behavior:
does explicitly calling generator.close() force a `with` block INSIDE that
generator to run its __exit__ (releasing the resource) immediately, versus
just breaking a for-loop over the generator (which does NOT close it
immediately -- it stays alive until garbage collected, which is exactly
the gap that caused the original bug: two microphone streams open at once).

This is a pure Python language question, fully testable with a fake
resource -- no audio hardware involved.
"""

from __future__ import annotations


class FakeResource:
    """Stands in for sd.InputStream -- tracks whether __exit__ ran."""

    def __init__(self):
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True


def _fake_frame_stream(resource: FakeResource):
    with resource:
        while True:
            yield "frame"


def test_breaking_for_loop_does_not_close_resource_immediately():
    """This is the BUG this fix addresses: merely breaking out of a
    for-loop over a generator does NOT run the generator's internal
    `with` block's __exit__ right away."""
    resource = FakeResource()
    gen = _fake_frame_stream(resource)
    for _ in gen:
        break
    assert resource.closed is False, (
        "if this fails, Python's behavior changed and the original bug "
        "this fix addresses may not exist -- re-investigate before "
        "removing the explicit .close() call in WakeWordDetector.listen()"
    )


def test_explicit_generator_close_does_close_resource_immediately():
    """This is the FIX: explicitly calling generator.close() DOES force
    the `with` block to run __exit__ right away -- this is what
    WakeWordDetector.listen() now does before calling on_wake(), instead
    of leaving the wake-word's microphone stream open during the whole
    conversation."""
    resource = FakeResource()
    gen = _fake_frame_stream(resource)
    for _ in gen:
        break
    gen.close()
    assert resource.closed is True


def test_try_finally_close_pattern_matches_production_code():
    """Mirrors the exact try/finally structure used in
    WakeWordDetector.listen(): iterate, break on some condition, close in
    finally -- confirms the resource is released even when breaking out
    from inside the loop body (not just after it)."""
    resource = FakeResource()
    gen = _fake_frame_stream(resource)
    detected = False
    try:
        for i, _frame in enumerate(gen):
            if i == 2:
                detected = True
                break
    finally:
        gen.close()

    assert detected is True
    assert resource.closed is True
