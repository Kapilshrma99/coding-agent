from app.services.ollama_service import _parse_agent_response
from app.workers.agent_worker import _ensure_minimum_actions


def test_parse_agent_response_extracts_json_from_wrapped_text():
    raw = """
Here is the result:
{
  "summary": "Created a file",
  "result": "The file was created in the workspace.",
  "actions": [
    {"type": "write_file", "path": "example.txt", "content": ""}
  ]
}
"""

    parsed = _parse_agent_response(raw)

    assert parsed["summary"] == "Created a file"
    assert parsed["result"] == "The file was created in the workspace."
    assert parsed["actions"] == [
        {"type": "write_file", "path": "example.txt", "content": ""}
    ]


def test_ensure_minimum_actions_recovers_code_block_into_write_file():
    raw = """Here is your Python script:
```python
print("hello")
```
"""

    actions = _ensure_minimum_actions(
        prompt="create a python script that prints hello",
        actions=[],
        result=raw,
        raw_response=raw,
    )

    assert actions == [
        {
            "type": "write_file",
            "path": "main.py",
            "content": 'print("hello")\n',
        }
    ]
