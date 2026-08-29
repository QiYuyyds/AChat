"""Unit tests for eval_harness suite YAML loading and validation (task 2.2)."""

import pytest
from eval_harness.core.suite import SuiteLoadError, load_suite
from eval_harness.core.types import EvalSuite

VALID_SUITE_YAML = """
name: demo-suite
description: A valid suite
version: 1.2.0
tasks:
  - id: task-1
    description: Say hello
    prompt: "Hello"
    graders:
      - type: code
        name: code_based
        config:
          checks:
            - type: contains
              value: "hello"
              target: transcript
"""


def _write(tmp_path, content: str):
    suite_file = tmp_path / "suite.yaml"
    suite_file.write_text(content, encoding="utf-8")
    return suite_file


def test_valid_yaml_loads(tmp_path):
    suite = load_suite(_write(tmp_path, VALID_SUITE_YAML))
    assert isinstance(suite, EvalSuite)
    assert suite.name == "demo-suite"
    assert suite.version == "1.2.0"
    assert len(suite.tasks) == 1
    assert suite.tasks[0].graders[0].name == "code_based"


def test_from_yaml_delegates_to_loader(tmp_path):
    suite = EvalSuite.from_yaml(str(_write(tmp_path, VALID_SUITE_YAML)))
    assert suite.name == "demo-suite"


def test_missing_file_raises_with_path(tmp_path):
    with pytest.raises(SuiteLoadError, match="not found"):
        load_suite(tmp_path / "nope.yaml")


def test_directory_as_suite_file_raises(tmp_path):
    with pytest.raises(SuiteLoadError, match="Cannot read"):
        load_suite(tmp_path)


def test_invalid_yaml_syntax(tmp_path):
    suite_file = _write(tmp_path, "name: [unclosed\n  bad indent: :")
    with pytest.raises(SuiteLoadError, match="Invalid YAML"):
        load_suite(suite_file)


def test_non_mapping_yaml(tmp_path):
    suite_file = _write(tmp_path, "- just\n- a\n- list\n")
    with pytest.raises(SuiteLoadError, match="YAML mapping"):
        load_suite(suite_file)


def test_duplicate_task_ids(tmp_path):
    yaml_text = """
name: dup
tasks:
  - id: same-id
    prompt: a
    graders:
      - type: code
        name: code_based
  - id: same-id
    prompt: b
    graders:
      - type: code
        name: code_based
"""
    with pytest.raises(SuiteLoadError, match="Duplicate task IDs"):
        load_suite(_write(tmp_path, yaml_text))


@pytest.mark.parametrize("version", ["1.0", "v1.0.0", "1.0.0-beta", "a.b.c", ""])
def test_invalid_version_format(tmp_path, version):
    yaml_text = f"""
name: bad-version
version: "{version}"
tasks:
  - id: t1
    prompt: a
    graders:
      - type: code
        name: code_based
"""
    with pytest.raises(SuiteLoadError, match="version"):
        load_suite(_write(tmp_path, yaml_text))


def test_empty_graders_rejected(tmp_path):
    yaml_text = """
name: no-graders
tasks:
  - id: t1
    prompt: a
    graders: []
"""
    with pytest.raises(SuiteLoadError):
        load_suite(_write(tmp_path, yaml_text))


def test_missing_graders_rejected(tmp_path):
    yaml_text = """
name: no-graders
tasks:
  - id: t1
    prompt: a
"""
    with pytest.raises(SuiteLoadError):
        load_suite(_write(tmp_path, yaml_text))


@pytest.mark.parametrize(
    "bad_name", ["", "1starts_with_digit", "has space", "dot.name", "中文"]
)
def test_invalid_grader_name(tmp_path, bad_name):
    yaml_text = f"""
name: bad-grader-name
tasks:
  - id: t1
    prompt: a
    graders:
      - type: code
        name: "{bad_name}"
"""
    with pytest.raises(SuiteLoadError, match="name"):
        load_suite(_write(tmp_path, yaml_text))


def test_empty_suite_name_rejected(tmp_path):
    yaml_text = """
name: "   "
tasks:
  - id: t1
    prompt: a
    graders:
      - type: code
        name: code_based
"""
    with pytest.raises(SuiteLoadError, match="name"):
        load_suite(_write(tmp_path, yaml_text))


def test_suite_name_over_128_chars_rejected(tmp_path):
    yaml_text = """
name: {name}
tasks:
  - id: t1
    prompt: a
    graders:
      - type: code
        name: code_based
""".format(name="x" * 129)
    with pytest.raises(SuiteLoadError, match="128"):
        load_suite(_write(tmp_path, yaml_text))


def test_no_tasks_rejected(tmp_path):
    yaml_text = """
name: empty
tasks: []
"""
    with pytest.raises(SuiteLoadError):
        load_suite(_write(tmp_path, yaml_text))


def test_negative_weight_rejected(tmp_path):
    yaml_text = """
name: bad-weight
tasks:
  - id: t1
    prompt: a
    graders:
      - type: code
        name: code_based
        weight: -1.0
"""
    with pytest.raises(SuiteLoadError, match="weight"):
        load_suite(_write(tmp_path, yaml_text))


@pytest.mark.parametrize("sample_count", [0, 11])
def test_sample_count_out_of_range_rejected(tmp_path, sample_count):
    yaml_text = f"""
name: bad-sample-count
tasks:
  - id: t1
    prompt: a
    graders:
      - type: model
        name: model_based
        sample_count: {sample_count}
"""
    with pytest.raises(SuiteLoadError, match="sample_count"):
        load_suite(_write(tmp_path, yaml_text))
