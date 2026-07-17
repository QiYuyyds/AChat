def test_verbose_progress_is_combined_into_monotonic_whole_run_percent() -> None:
    from app.code_intelligence.progress import CodeGraphProgressTracker

    tracker = CodeGraphProgressTracker()

    assert tracker.feed("[0.1s] Phase: parsing") is None
    parsing = tracker.feed("[0.2s]   34/100 (34%) - src/app.ts")
    assert parsing is not None
    assert parsing.phase == "parsing"
    assert parsing.phase_percent == 34
    assert parsing.overall_percent == 34

    assert tracker.feed("[0.3s]   20/100 (20%) - src/old.ts") is None
    assert tracker.feed("[0.4s] Phase: storing") is None
    storing = tracker.feed("[0.5s]   10/100 (10%)")
    assert storing is not None
    assert storing.overall_percent == 52

    assert tracker.feed("unrelated output") is None


def test_active_progress_never_reports_completion() -> None:
    from app.code_intelligence.progress import CodeGraphProgressTracker

    tracker = CodeGraphProgressTracker()
    tracker.feed("[0.1s] Phase: resolving")
    progress = tracker.feed("[0.2s]   100/100 (100%)")

    assert progress is not None
    assert progress.overall_percent == 99
