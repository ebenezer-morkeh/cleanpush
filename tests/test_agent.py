from cleanpush.agent import describe_call


def test_write_file_hides_contents():
    text = describe_call("write_file", {"path": "a.py", "content": "x" * 50})
    assert text == "a.py (50 chars)"


def test_other_calls():
    assert describe_call("run_formatters", {"paths": ["a.py", "b.py"]}) == "a.py, b.py"
    assert describe_call("read_file", {"path": "README.md"}) == "README.md"
    assert describe_call("search", {"pattern": "foo"}) == "foo"
    assert describe_call("changed_files", {}) == ""


def test_stage_changes_only_stages_agent_files(tmp_path):
    import subprocess

    from cleanpush.agent import stage_changes

    def git(*args):
        return subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True).stdout

    git("init", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (tmp_path / "app.py").write_text("x = 1\n")
    git("add", "-A")
    git("commit", "-m", "init")

    (tmp_path / "app.py").write_text("x = 2\n")  # tracked file the agent edited
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n")  # new file the agent created
    (tmp_path / "stray.log").write_text("junk\n")  # appeared some other way

    leftovers = stage_changes(tmp_path, {"app.py", "CHANGELOG.md"})

    staged = git("diff", "--cached", "--name-only").split()
    assert sorted(staged) == ["CHANGELOG.md", "app.py"]
    assert leftovers == ["stray.log"]
