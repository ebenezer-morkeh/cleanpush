from cleanpush.agent import describe_call


def test_write_file_hides_contents():
    text = describe_call("write_file", {"path": "a.py", "content": "x" * 50})
    assert text == "a.py (50 chars)"


def test_other_calls():
    assert describe_call("run_formatters", {"paths": ["a.py", "b.py"]}) == "a.py, b.py"
    assert describe_call("read_file", {"path": "README.md"}) == "README.md"
    assert describe_call("search", {"pattern": "foo"}) == "foo"
    assert describe_call("changed_files", {}) == ""
