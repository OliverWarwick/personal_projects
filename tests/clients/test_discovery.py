from personal_project.clients.better_com.discovery import discover_from_har, summarize_har_endpoints
from pathlib import Path
import json


def make_sample_har(tmp_path: Path) -> Path:
    # minimal HAR structure with one XHR POST entry
    har = {
        "log": {
            "entries": [
                {
                    "request": {
                        "method": "POST",
                        "url": "https://better.com/api/login",
                        "headers": [{"name": "content-type", "value": "application/json"}],
                        "postData": {"text": '{"username":"me","password":"pw"}'}
                    },
                    "response": {
                        "status": 200,
                        "headers": [{"name": "content-type", "value": "application/json"}],
                        "content": {"mimeType": "application/json", "text": '{"token":"abc123"}'}
                    },
                    "_resourceType": "xhr"
                }
            ]
        }
    }
    p = tmp_path / "sample.har"
    p.write_text(json.dumps(har), encoding="utf-8")
    return p


def test_discover_from_har(tmp_path: Path):
    p = make_sample_har(tmp_path)
    res = discover_from_har(p)
    assert "entries" in res
    assert len(res["entries"]) == 1
    e = res["entries"][0]
    assert e["method"] == "POST"
    assert "api/login" in e["url"]


def test_summarize_har_endpoints(tmp_path: Path):
    p = make_sample_har(tmp_path)
    out = summarize_har_endpoints(p)
    assert isinstance(out, list)
    assert out[0]["method"] == "POST"
    assert "login" in out[0]["url"]

