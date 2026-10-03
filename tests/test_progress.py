from ramon.progress import ProgressReporter, ProgressSlice


def test_progress_reporter_prints_percentage_to_stderr(capsys):
    p = ProgressReporter(total=10, enabled=True, min_interval=0)
    p.update(5, stage="half")
    p.finish(stage="done")
    captured = capsys.readouterr()
    assert "50.00%" in captured.err
    assert "100.00%" in captured.err
    assert "half" in captured.err
    assert "done" in captured.err
    assert captured.out == ""


def test_progress_slice_maps_child_range(capsys):
    p = ProgressReporter(total=100, enabled=True, min_interval=0)
    child = ProgressSlice(p, 20, 60, 4, "child")
    child.update(2)
    child.finish()
    captured = capsys.readouterr()
    assert "40.00%" in captured.err
    assert "60.00%" in captured.err


def test_disabled_progress_is_silent(capsys):
    p = ProgressReporter(total=5, enabled=False, min_interval=0)
    p.update(3, stage="hidden")
    p.finish()
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == ""
