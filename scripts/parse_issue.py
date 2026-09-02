import json
import os
from pathlib import Path

from cutai.validation import extract_url

event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
body = event.get("issue", {}).get("body", "")
url = extract_url(body)
output = Path(os.environ["GITHUB_OUTPUT"])
with output.open("a", encoding="utf-8") as stream:
    stream.write(f"url={url}\n")

