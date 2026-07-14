"""Tests for mermaid_normalize — auto-repair of common LLM output patterns."""

from app.utils.mermaid_normalize import normalise_mermaid_source


def test_missing_declaration_auto_completed():
    """Source with edges but no declaration should get flowchart TD prepended."""
    result = normalise_mermaid_source("A[Start] --> B[End]")
    assert result.ok is True
    assert result.source is not None
    assert "flowchart TD" in result.source
    assert "Start" in result.source
    assert "End" in result.source


def test_missing_declaration_arrows():
    """Source with --- arrows should also be auto-completed."""
    result = normalise_mermaid_source("A[One] --- B[Two]")
    assert result.ok is True
    assert "flowchart TD" in result.source


def test_fence_with_whitespace_stripped():
    """Fence with leading/trailing whitespace should be stripped."""
    source = "  ```mermaid\nflowchart TD\nA-->B\n```  \n"
    result = normalise_mermaid_source(source)
    assert result.ok is True
    assert result.source is not None
    assert "```" not in result.source
    assert "flowchart TD" in result.source


def test_chinese_label_normalised():
    """Chinese labels should be quoted correctly."""
    result = normalise_mermaid_source('flowchart TD\nA[开始] --> B[结束]')
    assert result.ok is True
    assert result.source is not None
    assert '"开始"' in result.source
    assert '"结束"' in result.source


def test_cannot_infer_returns_error():
    """Random text without diagram syntax should return an error."""
    result = normalise_mermaid_source("some random text without diagram syntax")
    assert result.ok is False
    assert "declaration" in result.error.lower()


def test_existing_declaration_preserved():
    """Source with valid declaration should not be modified."""
    source = "sequenceDiagram\nA->>B: Hello"
    result = normalise_mermaid_source(source)
    assert result.ok is True
    assert "sequenceDiagram" in result.source
