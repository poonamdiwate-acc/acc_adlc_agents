"""Unit test for DE-04 API Contracts with new input sources."""

import pytest
import json
from pathlib import Path
from core.shared_folder import find_file_by_patterns


def test_find_file_by_patterns(tmp_path):
    """Test finding files by name patterns."""
    # Create test directory structure
    thread_dir = tmp_path / "thr-test"
    thread_dir.mkdir()
    
    # Create test files
    (thread_dir / "Business_Process_Agent_Interaction.html").write_text("<html><body>Test</body></html>")
    (thread_dir / "Business_Process_Agent_Network.md").write_text("# Test\nContent")
    (thread_dir / "other_file.txt").write_text("Other")
    
    # Test finding HTML file
    result = find_file_by_patterns(
        base_path=str(tmp_path),
        thread_id="thr-test",
        subfolder=".",
        file_name_patterns=["Business_Process_Agent_Interaction.html", "Business_Process_Agent_Interaction.md"],
        allowed_extensions=[".html", ".md"],
    )
    assert result is not None
    assert result.name == "Business_Process_Agent_Interaction.html"
    
    # Test finding MD file
    result = find_file_by_patterns(
        base_path=str(tmp_path),
        thread_id="thr-test",
        subfolder=".",
        file_name_patterns=["Business_Process_Agent_Network.html", "Business_Process_Agent_Network.md"],
        allowed_extensions=[".html", ".md"],
    )
    assert result is not None
    assert result.name == "Business_Process_Agent_Network.md"
    
    # Test file not found
    result = find_file_by_patterns(
        base_path=str(tmp_path),
        thread_id="thr-test",
        subfolder=".",
        file_name_patterns=["NonExistent.html"],
        allowed_extensions=[".html"],
    )
    assert result is None


def test_markdown_parser():
    """Test markdown parser extracts Mermaid diagrams."""
    from core.input_parsers.markdown_parser import MarkdownParser
    
    markdown_content = """# Test Diagram

This is a test.

```mermaid
graph TD
    A[Start] --> B[End]
```

More content.
"""
    
    parser = MarkdownParser()
    result = parser.parse(markdown_content.encode('utf-8'))
    
    assert result.source_format == "markdown"
    assert result.metadata["mermaid_diagram_count"] == 1
    assert "graph TD" in result.metadata["mermaid_diagrams"][0]
    assert "Test Diagram" in result.raw_text or "test" in result.raw_text.lower()


def test_config_structure():
    """Test DE-04 config has new input fields."""
    from core.config_loader import get_config
    
    cfg = get_config()
    de04_inputs = cfg.inputs("DE-04")
    
    # Check new fields exist
    assert "agent_interaction_diagram" in de04_inputs
    assert "agent_network_diagram" in de04_inputs
    assert "agent_architecture" in de04_inputs
    
    # Check agent_interaction_diagram is required
    assert de04_inputs["agent_interaction_diagram"]["required"] is True
    
    # Check shared_io has input_sources
    shared_io = cfg.shared_io_config("DE-04")
    assert "input_sources" in shared_io
    assert isinstance(shared_io["input_sources"], list)
    assert len(shared_io["input_sources"]) >= 3  # bs_docs, diagrams, brd_response


if __name__ == "__main__":
    # Run simple tests
    print("Running unit tests for DE-04 new input sources...")
    
    # Test 1: Config structure
    try:
        test_config_structure()
        print("✓ Config structure test passed")
    except Exception as e:
        print(f"✗ Config structure test failed: {e}")
    
    # Test 2: Markdown parser
    try:
        test_markdown_parser()
        print("✓ Markdown parser test passed")
    except Exception as e:
        import traceback
        print(f"✗ Markdown parser test failed: {e}")
        traceback.print_exc()
    
    # Test 3: File finder (needs temp dir)
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            test_find_file_by_patterns(Path(tmpdir))
            print("✓ File finder test passed")
        except Exception as e:
            print(f"✗ File finder test failed: {e}")
    
    print("\nAll unit tests completed!")
