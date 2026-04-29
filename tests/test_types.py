import pytest

from epub_corrector.types import ChangeRecord, ProcessingStats, ReviewState, StopProcessing


def test_stop_processing_is_exception():
    with pytest.raises(StopProcessing):
        raise StopProcessing()


def test_processing_stats_defaults():
    s = ProcessingStats()
    assert s.docs_seen == 0
    assert s.groups_seen == 0
    assert s.segments_seen == 0
    assert s.accepted_changes == 0
    assert s.rejected_changes == 0
    assert s.failed_groups == 0


def test_change_record():
    r = ChangeRecord(doc_name="ch1", original="a", proposed="b", accepted=True)
    assert r.doc_name == "ch1"
    assert r.original == "a"
    assert r.proposed == "b"
    assert r.accepted is True


def test_review_state_defaults():
    rs = ReviewState()
    assert rs.auto_accept is False


def test_review_state_mutable():
    rs = ReviewState()
    rs.auto_accept = True
    assert rs.auto_accept is True
