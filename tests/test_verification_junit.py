from pathlib import Path

import pytest

from ai_dev_platform.infrastructure.verification import (
    VerificationError,
    parse_junit_test_cases,
)


def test_junit_xml_is_parsed_into_executed_test_cases(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_example.py").write_text("", encoding="utf-8")
    report = tmp_path / "junit.xml"
    report.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" tests="4">
    <testcase classname="tests.test_example" name="test_pass"
              file="tests/test_example.py" time="0.1" />
    <testcase classname="tests.test_example" name="test_skip"
              file="tests/test_example.py"><skipped /></testcase>
    <testcase classname="tests.test_example" name="test_fail"
              file="tests/test_example.py"><failure /></testcase>
    <testcase classname="tests.test_example" name="test_error"
              file="tests/test_example.py"><error /></testcase>
  </testsuite>
</testsuites>
""",
        encoding="utf-8",
    )

    cases = parse_junit_test_cases(report, tmp_path, "VERIFY-1")

    assert {item.node_id: item.status for item in cases} == {
        "tests/test_example.py::test_error": "ERROR",
        "tests/test_example.py::test_fail": "FAIL",
        "tests/test_example.py::test_pass": "PASS",
        "tests/test_example.py::test_skip": "SKIP",
    }
    assert all(item.evidence_reference.startswith("junit:VERIFY-1:") for item in cases)


def test_duplicate_junit_test_case_uses_worst_status_and_combined_duration(
    tmp_path: Path,
) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_example.py").write_text("", encoding="utf-8")
    report = tmp_path / "duplicates.xml"
    report.write_text(
        """<testsuites><testsuite>
<testcase classname="tests.test_example" name="test_duplicate"
          file="tests/test_example.py" time="0.2" />
<testcase classname="tests.test_example" name="test_duplicate"
          file="tests/test_example.py" time="0.3"><failure /></testcase>
</testsuite></testsuites>""",
        encoding="utf-8",
    )

    cases = parse_junit_test_cases(report, tmp_path, "VERIFY-2")

    assert len(cases) == 1
    assert cases[0].status == "FAIL"
    assert cases[0].duration_seconds == 0.5


@pytest.mark.parametrize("test_file", ["../outside.py", "tests/missing.py"])
def test_junit_test_file_must_resolve_inside_the_repository(tmp_path: Path, test_file: str) -> None:
    report = tmp_path / "unsafe.xml"
    report.write_text(
        f"""<testsuite><testcase name="test_unsafe" file="{test_file}" /></testsuite>""",
        encoding="utf-8",
    )

    with pytest.raises(VerificationError, match="JUnit test file"):
        parse_junit_test_cases(report, tmp_path, "VERIFY-3")
