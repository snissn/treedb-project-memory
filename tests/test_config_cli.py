import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from treedb_project_memory.cli import app

runner = CliRunner()


def test_init_creates_expected_files_and_dirs(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init", "--workspace", "demo"])

    assert result.exit_code == 0
    assert Path(".treedb-project-memory/config.yaml").is_file()
    assert Path(".treedb-project-memory/state").is_dir()
    config = yaml.safe_load(Path(".treedb-project-memory/config.yaml").read_text())
    assert config["workspace"] == "demo"
    assert config["sources"] == {}


def test_init_refuses_overwrite_unless_force(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init", "--workspace", "first"]).exit_code == 0

    refused = runner.invoke(app, ["init", "--workspace", "second"])
    assert refused.exit_code != 0
    assert "already exists" in refused.output

    forced = runner.invoke(app, ["init", "--workspace", "second", "--force"])
    assert forced.exit_code == 0
    config = yaml.safe_load(Path(".treedb-project-memory/config.yaml").read_text())
    assert config["workspace"] == "second"


def test_add_normalizes_roots_and_stores_source_entries(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    Path("docs").mkdir()
    assert runner.invoke(app, ["init"]).exit_code == 0

    result = runner.invoke(app, ["add", "docs", "--type", "folder"])

    assert result.exit_code == 0
    config = yaml.safe_load(Path(".treedb-project-memory/config.yaml").read_text())
    source = config["sources"]["docs"]
    assert source["type"] == "folder"
    assert source["root"] == str(Path("docs").resolve())
    assert source["include"] == ["**/*.md", "**/*.txt"]


def test_add_preserves_explicit_source_ids(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    Path("notes").mkdir()
    assert runner.invoke(app, ["init"]).exit_code == 0

    result = runner.invoke(app, ["add", "notes", "--id", "research-notes"])

    assert result.exit_code == 0
    config = yaml.safe_load(Path(".treedb-project-memory/config.yaml").read_text())
    assert "research-notes" in config["sources"]


def test_duplicate_source_ids_fail_clearly(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    Path("a").mkdir()
    Path("b").mkdir()
    assert runner.invoke(app, ["init"]).exit_code == 0
    assert runner.invoke(app, ["add", "a", "--id", "src"]).exit_code == 0

    result = runner.invoke(app, ["add", "b", "--id", "src"])

    assert result.exit_code != 0
    assert "source ID 'src' already exists" in result.output


def test_doctor_reports_missing_roots(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init"]).exit_code == 0
    assert runner.invoke(app, ["add", "missing", "--id", "missing"]).exit_code == 0

    result = runner.invoke(app, ["doctor", "--format", "json"])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["errors"][0]["code"] == "missing_root"
    assert payload["errors"][0]["source_id"] == "missing"


def test_invalid_glob_fields_produce_validation_errors(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init"]).exit_code == 0
    config_path = Path(".treedb-project-memory/config.yaml")
    config = yaml.safe_load(config_path.read_text())
    config["sources"] = {
        "bad": {
            "type": "folder",
            "root": str(Path("docs").resolve()),
            "include": "*.md",
            "exclude": [],
        }
    }
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    result = runner.invoke(app, ["doctor", "--format", "json"])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["errors"][0]["code"] == "invalid_config"
    assert "sources.bad.include must be a list of strings" in payload["errors"][0]["message"]


def test_workspace_discovery_from_nested_dirs(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    Path("docs/nested").mkdir(parents=True)
    assert runner.invoke(app, ["init"]).exit_code == 0
    workspace_root = Path.cwd()

    monkeypatch.chdir("docs/nested")
    result = runner.invoke(app, ["sources", "--format", "json"])

    assert result.exit_code == 0
    assert json.loads(result.output)["workspace_root"] == str(workspace_root)


def test_json_output_parseable_for_sources_and_doctor(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    Path("docs").mkdir()
    assert runner.invoke(app, ["init"]).exit_code == 0
    assert runner.invoke(app, ["add", "docs", "--id", "docs"]).exit_code == 0

    sources_result = runner.invoke(app, ["sources", "--format", "json"])
    doctor_result = runner.invoke(app, ["doctor", "--format", "json"])

    assert sources_result.exit_code == 0
    assert doctor_result.exit_code == 0
    assert json.loads(sources_result.output)["sources"][0]["id"] == "docs"
    assert json.loads(doctor_result.output)["ok"] is True
