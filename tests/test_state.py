from papercast.core.state import (
    Stage,
    StateRecord,
    can_advance,
    is_terminal,
    next_stage,
)


def test_linear_flow_advances_stage_by_stage():
    rec = StateRecord(paper_id="abc")
    chain = [
        Stage.PARSED,
        Stage.FIGURES_SPLIT,
        Stage.READ_DONE,
        Stage.SLIDES_DONE,
        Stage.SCRIPT_DONE,
        Stage.AWAITING_REVIEW,
        Stage.APPROVED,
        Stage.TTS_SUBMITTED,
        Stage.TTS_DONE,
        Stage.COMPOSED,
        Stage.PUBLISHED,
    ]
    for stage in chain:
        rec.advance(stage)
    assert rec.stage is Stage.PUBLISHED
    assert is_terminal(rec.stage)
    assert next_stage(rec.stage) is None


def test_skipping_a_stage_is_rejected():
    rec = StateRecord(paper_id="abc")
    try:
        rec.advance(Stage.READ_DONE)  # skipping parsed + figures_split
    except ValueError:
        return
    raise AssertionError("expected ValueError on illegal transition")


def test_failed_records_error_and_can_recover():
    rec = StateRecord(paper_id="abc")
    rec.advance(Stage.PARSED)
    rec.fail("boom")
    assert rec.stage is Stage.FAILED
    assert rec.errors == ["boom"]
    # Recovery: from failed we can re-enter the linear flow at any point.
    assert can_advance(Stage.FAILED, Stage.READ_DONE)


def test_next_stage_terminal():
    assert next_stage(Stage.PUBLISHED) is None
    assert next_stage(Stage.FAILED) is None
