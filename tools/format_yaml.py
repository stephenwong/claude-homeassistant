"""Format one YAML file without replacing it until serialization succeeds."""

import sys
from pathlib import Path

from ruamel.yaml import YAML

from tools.common import _atomic_replace


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print("Usage: format_yaml.py <file.yaml>", file=sys.stderr)
        return 2

    path = Path(args[0])
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as e:
        print(f"Error reading {path}: {e}", file=sys.stderr)
        return 1

    yaml = YAML()
    yaml.preserve_quotes = True
    data = yaml.load(content)
    if data is None:
        return 0

    def write_temp(temp_path: Path) -> None:
        with temp_path.open("w", encoding="utf-8") as temp:
            yaml.dump(data, temp)

    _atomic_replace(path, write_temp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
