"""Format one YAML file without replacing it until serialization succeeds."""

import sys
from pathlib import Path

from ruamel.yaml import YAML

from tools.common import _atomic_replace


def main() -> int:
    path = Path(sys.argv[1])
    yaml = YAML()
    yaml.preserve_quotes = True
    data = yaml.load(path.read_text(encoding="utf-8"))
    if data is None:
        return 0

    def write_temp(temp_path: Path) -> None:
        with temp_path.open("w", encoding="utf-8") as temp:
            yaml.dump(data, temp)

    _atomic_replace(path, write_temp, cleanup_missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
