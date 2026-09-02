import json
import os
from pathlib import Path

from cutai.validation import extract_url

event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
body = event.get("issue", {}).get("body", "")

# Uma Issue [LIVE] pode omitir o link quando o workflow possui uma live padrão.
# Nesse caso deixamos a saída vazia para a etapa Resolver parâmetros aplicar DEFAULT_LIVE_URL.
try:
    url = extract_url(body)
except ValueError:
    url = ""

output = Path(os.environ["GITHUB_OUTPUT"])
with output.open("a", encoding="utf-8") as stream:
    stream.write(f"url={url}\n")
