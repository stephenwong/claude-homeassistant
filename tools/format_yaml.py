"""Format one YAML file without replacing it until serialization succeeds."""

import os
import stat
import sys
import tempfile
from pathlib import Path

from ruamel.yaml import YAML


def main() -> int:
    path = Path(sys.argv[1])
    yaml = YAML()
    yaml.preserve_quotes = True
    data = yaml.load(path.read_text(encoding="utf-8"))
    if data is None:
        return 0

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, delete=False
        ) as temp:
            temp_path = Path(temp.name)
            yaml.dump(data, temp)
        os.chmod(temp_path, stat.S_IMODE(path.stat().st_mode))
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
