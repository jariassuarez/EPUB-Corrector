import json
import os
import tempfile

from epub_corrector.persistence import load_checkpoint, save_checkpoint, write_csv_report
from epub_corrector.types import ChangeRecord


def test_load_checkpoint_missing():
    assert load_checkpoint("/nonexistent/path.json") == {}


def test_load_checkpoint_invalid_json():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write("not json")
        path = f.name
    try:
        assert load_checkpoint(path) == {}
    finally:
        os.unlink(path)


def test_checkpoint_roundtrip():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        path = f.name
    try:
        data = {"doc1": "abc", "doc2": "def"}
        save_checkpoint(path, data)
        assert load_checkpoint(path) == data
    finally:
        os.unlink(path)


def test_checkpoint_atomic_write():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "checkpoint.json")
        save_checkpoint(path, {"doc1": "value"})
        assert os.path.isfile(path)
        # tmp file should not exist
        assert not os.path.isfile(path + ".tmp")


def test_write_csv_report():
    records = [
        ChangeRecord(doc_name="ch1", original="a", proposed="b", accepted=True),
        ChangeRecord(doc_name="ch1", original="c", proposed="d", accepted=False),
    ]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        path = f.name
    try:
        write_csv_report(records, path)
        with open(path, encoding="utf-8") as f:
            lines = f.read().strip().splitlines()
        assert lines[0] == "document,status,original,proposed"
        assert lines[1] == "ch1,accepted,a,b"
        assert lines[2] == "ch1,rejected,c,d"
    finally:
        os.unlink(path)


def test_load_checkpoint_processed_not_dict():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"processed": "not-a-dict"}, f)
        path = f.name
    try:
        assert load_checkpoint(path) == {}
    finally:
        os.unlink(path)
