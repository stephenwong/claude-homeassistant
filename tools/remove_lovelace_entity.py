#!/usr/bin/env python3
import json
import sys
from pathlib import Path


def remove_entity_from_lovelace(data: dict, target_entity: str) -> tuple[dict, int]:
    removed_count = 0
    views = data.get("data", {}).get("config", {}).get("views", [])
    for view in views:
        for card in view.get("cards", []):
            if "entities" in card and isinstance(card["entities"], list):
                new_entities = []
                for e in card["entities"]:
                    eid = e if isinstance(e, str) else e.get("entity")
                    if eid == target_entity:
                        removed_count += 1
                    else:
                        new_entities.append(e)
                card["entities"] = new_entities
    return data, removed_count


def main() -> int:
    if len(sys.argv) < 3:
        print("Usage: remove_lovelace_entity.py <target_file> <entity_id>")
        return 1

    file_path = Path(sys.argv[1])
    target_entity = sys.argv[2]

    if not file_path.is_file():
        print(f"Error: {file_path} does not exist")
        return 1

    try:
        with open(file_path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"Error reading JSON from {file_path}: {e}")
        return 1

    data, count = remove_entity_from_lovelace(data, target_entity)
    print(f"Removed {count} occurrence(s) of '{target_entity}' from {file_path}")

    tmp_path = file_path.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    tmp_path.replace(file_path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
