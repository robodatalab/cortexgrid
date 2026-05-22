import base64
import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
SECRETS = [("sm-creds", "external-secrets"), ("s3-creds", "external-secrets")]


def read_secret(name: str, namespace: str) -> dict[str, str]:
    raw = subprocess.check_output(
        ["kubectl", "get", "secret", name, "-n", namespace, "-o", "json"]
    )
    return {
        k: base64.b64decode(v).decode()
        for k, v in json.loads(raw)["data"].items()
    }


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def upsert(text: str, key: str, value: str) -> str:
    line = f"{key}={shell_quote(value)}"
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    if pattern.search(text):
        return pattern.sub(line, text, count=1)
    if text and not text.endswith("\n"):
        text += "\n"
    return text + line + "\n"


def main() -> None:
    text = ENV_PATH.read_text() if ENV_PATH.exists() else ""
    for name, namespace in SECRETS:
        for key, value in read_secret(name, namespace).items():
            text = upsert(text, key, value)
    ENV_PATH.write_text(text)
    print(f"updated {ENV_PATH}")


if __name__ == "__main__":
    main()
