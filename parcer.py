"""
    This script was made with AI voodoo magic.
    There's no way in hell I'm digging through hundreds files to see how TFG handles alloying restrictions.

    It is not guaranteed to work with all modpack that include TFC, and may break in the future.
    Use at your own risk. It could destroy alloys.json (stores all alloy recipes), so make a backup before running it.
    If you find a bug, please report it. I will fix it with AI.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ALLOY_TYPE = "tfc:alloy"


class ScanCancelled(Exception):
    """Raised when the user stops an in-progress scan."""


def _check_scan_cancelled(stop_event) -> None:
    if stop_event is not None and stop_event.is_set():
        raise ScanCancelled()


def _report_progress(progress, stage: str, current: int = 0, total: int = 0) -> None:
    if progress is not None:
        progress(stage, current, total)


@dataclass
class Recipe:
    recipe_id: str
    result: str
    components: dict[str, dict[str, float]]
    source: str
    source_type: str  # "json" or "kubejs"


def read_lang_file(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}

    if not isinstance(data, dict):
        return {}

    return {
        str(key): value
        for key, value in data.items()
        if isinstance(value, str)
    }


def translation_candidates(identifier: str) -> list[str]:
    """Return common Minecraft translation keys for namespace:path IDs."""
    if ":" not in identifier:
        return []

    namespace, path = identifier.split(":", 1)
    return [
        f"metal.{namespace}.{path}",
        f"material.{namespace}.{path}",
        f"fluid.{namespace}.metal.{path}",
        f"fluid.{namespace}.{path}",
        f"item.{namespace}.{path}",
        f"block.{namespace}.{path}",
        f"{namespace}.{path}",
        f"fluid.{namespace}.molten_{path}",
        f"item.{namespace}.metal.{path}",
    ]


def scan_language_files(instance: Path, scan_libraries: bool = False, stop_event=None) -> dict[str, str]:
    """Collect English names from every filesystem and archive lang folder."""
    translations: dict[str, str] = {}

    for lang_dir in instance.rglob("lang"):
        _check_scan_cancelled(stop_event)
        if not lang_dir.is_dir():
            continue
        for path in lang_dir.iterdir():
            if path.is_file() and path.stem.lower() == "en_us":
                translations.update(read_lang_file(path))

    archive_roots = [instance / "mods"]
    if scan_libraries:
        archive_roots.append(instance / "libraries")

    for root in archive_roots:
        if not root.is_dir():
            continue
        archives = root.glob("*.jar") if root == instance / "mods" else root.rglob("*.jar")
        for archive in archives:
            _check_scan_cancelled(stop_event)
            try:
                with zipfile.ZipFile(archive, "r") as jar:
                    for filename in jar.namelist():
                        normalized = filename.replace("\\", "/")
                        if not re.search(r"(?:^|/)lang/en_us\.json$", normalized, re.IGNORECASE):
                            continue
                        try:
                            data = json.loads(jar.read(filename).decode("utf-8-sig"))
                        except (UnicodeDecodeError, json.JSONDecodeError):
                            continue
                        if isinstance(data, dict):
                            translations.update({
                                str(key): value
                                for key, value in data.items()
                                if isinstance(value, str)
                            })
            except (zipfile.BadZipFile, OSError):
                continue

    return translations


def get_english_name(identifier: str, translations: dict[str, str]) -> str:
    for key in translation_candidates(identifier):
        value = translations.get(key)
        if value:
            return value
    return identifier


def number_to_percent(value: float) -> float:
    """0.7 -> 70, 0.075 -> 7.5"""
    return round(value * 100, 6)


def clean_id(value: str) -> str:
    return value.strip()


def parse_json_recipe(data: Any, source: str) -> Recipe | None:
    """Parse a TFC alloy JSON recipe.

    Supports common TFC 1.20.x forms:
      contents[].metal
      contents[].fluid

    Result can be a string or an object containing an id/item/fluid field.
    """
    if not isinstance(data, dict):
        return None

    if data.get("type") != ALLOY_TYPE:
        return None

    raw_result = data.get("result")
    if isinstance(raw_result, str):
        result = raw_result
    elif isinstance(raw_result, dict):
        result = (
            raw_result.get("fluid")
            or raw_result.get("item")
            or raw_result.get("id")
            or ""
        )
    else:
        result = ""

    if not result:
        return None

    contents = data.get("contents", [])
    if not isinstance(contents, list):
        return None

    components: dict[str, dict[str, float]] = {}

    for part in contents:
        if not isinstance(part, dict):
            continue

        # TFC versions use either "metal" or "fluid" depending on format.
        component = (
            part.get("metal")
            or part.get("fluid")
            or part.get("ingredient")
        )

        if isinstance(component, dict):
            component = (
                component.get("fluid")
                or component.get("item")
                or component.get("id")
            )

        if not isinstance(component, str):
            continue

        min_value = part.get("min")
        max_value = part.get("max")

        if not isinstance(min_value, (int, float)):
            continue
        if not isinstance(max_value, (int, float)):
            continue

        # JSON TFC alloy values are fractions, e.g. 0.70 -> 70%.
        components[clean_id(component)] = {
            "min": number_to_percent(float(min_value)),
            "max": number_to_percent(float(max_value)),
        }

    if not components:
        return None

    # JSON recipe IDs are normally derived from the file path.
    # The caller supplies the actual ID.
    return Recipe(
        recipe_id="",
        result=clean_id(result),
        components=components,
        source=source,
        source_type="json",
    )


def recipe_id_from_json_path(path_inside_archive: str) -> str | None:
    """data/<namespace>/recipes/<path>.json -> namespace:<path>"""
    p = path_inside_archive.replace("\\", "/")

    m = re.search(r"(?:^|/)data/([^/]+)/recipes/(.+)\.json$", p)
    if not m:
        return None

    namespace = m.group(1)
    recipe_path = m.group(2)
    return f"{namespace}:{recipe_path}"


def scan_jar(jar_path: Path) -> tuple[list[Recipe], int]:
    recipes: list[Recipe] = []
    json_count = 0

    try:
        with zipfile.ZipFile(jar_path, "r") as jar:
            for filename in jar.namelist():
                if not filename.lower().endswith(".json"):
                    continue

                json_count += 1

                recipe_id = recipe_id_from_json_path(filename)
                if recipe_id is None:
                    continue

                try:
                    raw = jar.read(filename)
                    data = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue

                source = f"{jar_path.name}!/{filename}"
                recipe = parse_json_recipe(data, source)

                if recipe is not None:
                    recipe.recipe_id = recipe_id
                    recipes.append(recipe)

    except (zipfile.BadZipFile, OSError) as exc:
        print(f"[WARN] Failed to read {jar_path}: {exc}")

    return recipes, json_count


# KubeJS pattern used by TerraFirmaGreg-modern:
#
# event.recipes.tfc.alloy('tfg:potin', [
#     TFC.alloyPart('tfc:copper', 0.63, 0.69),
#     ...
# ]).id('tfg:alloy/potin')
#
# We deliberately support both single and double quotes.
ALLOY_CALL_RE = re.compile(
    r"""
    event\s*\.\s*recipes\s*\.\s*tfc\s*\.\s*alloy
    \s*\(\s*
    (?P<quote>['"])
    (?P<result>[^'"]+)
    (?P=quote)
    \s*,\s*
    \[
        (?P<body>.*?)
    \]
    \s*\)
    (?P<id_part>
        \s*\.\s*id
        \s*\(\s*
        (?P<id_quote>['"])
        (?P<recipe_id>[^'"]+)
        (?P=id_quote)
        \s*\)
    )?
    """,
    re.IGNORECASE | re.DOTALL | re.VERBOSE,
)

ALLOY_PART_RE = re.compile(
    r"""
    TFC\s*\.\s*alloyPart
    \s*\(\s*
    (?P<quote>['"])
    (?P<component>[^'"]+)
    (?P=quote)
    \s*,\s*
    (?P<min>[+-]?(?:\d+(?:\.\d*)?|\.\d+))
    \s*,\s*
    (?P<max>[+-]?(?:\d+(?:\.\d*)?|\.\d+))
    \s*\)
    """,
    re.IGNORECASE | re.VERBOSE,
)


def strip_js_comments(text: str) -> str:
    """Remove // and /* */ comments while keeping strings intact."""
    out: list[str] = []
    i = 0
    n = len(text)
    quote: str | None = None

    while i < n:
        c = text[i]

        if quote:
            out.append(c)

            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue

            if c == quote:
                quote = None

            i += 1
            continue

        if c in ("'", '"', "`"):
            quote = c
            out.append(c)
            i += 1
            continue

        if c == "/" and i + 1 < n and text[i + 1] == "/":
            i += 2
            while i < n and text[i] not in "\r\n":
                i += 1
            out.append("\n")
            continue

        if c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            out.append("\n")
            continue

        out.append(c)
        i += 1

    return "".join(out)


def parse_kubejs_file(path: Path) -> list[Recipe]:
    recipes: list[Recipe] = []

    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        print(f"[WARN] Failed to read {path}: {exc}")
        return recipes

    # Comments can otherwise contain text that looks like a recipe.
    text = strip_js_comments(text)

    for match in ALLOY_CALL_RE.finditer(text):
        result = clean_id(match.group("result"))
        body = match.group("body")
        recipe_id = match.group("recipe_id")

        components: dict[str, dict[str, float]] = {}

        for part in ALLOY_PART_RE.finditer(body):
            component = clean_id(part.group("component"))

            try:
                min_value = float(part.group("min"))
                max_value = float(part.group("max"))
            except ValueError:
                continue

            components[component] = {
                "min": number_to_percent(min_value),
                "max": number_to_percent(max_value),
            }

        if not components:
            continue

        # For TFG's recipes.metals.js every alloy has .id(...).
        # If one doesn't, make an informative synthetic ID.
        if not recipe_id:
            recipe_id = f"__kubejs__:{path.stem}/{result}"

        recipes.append(
            Recipe(
                recipe_id=clean_id(recipe_id),
                result=result,
                components=components,
                source=str(path),
                source_type="kubejs",
            )
        )

    return recipes


def scan_kubejs(kubejs_dir: Path, stop_event=None) -> tuple[list[Recipe], int]:
    recipes: list[Recipe] = []
    js_count = 0

    if not kubejs_dir.is_dir():
        return recipes, js_count

    for path in kubejs_dir.rglob("*.js"):
        _check_scan_cancelled(stop_event)
        js_count += 1
        recipes.extend(parse_kubejs_file(path))

    return recipes, js_count


def scan_removed_recipe_ids(kubejs_dir: Path, stop_event=None) -> set[str]:
    """Find exact recipe IDs removed by KubeJS event.remove calls."""
    removed: set[str] = set()
    remove_patterns = (
        re.compile(
            r"event\s*\.\s*remove\s*\(\s*\{[^}]*?id\s*:\s*['\"]([^'\"]+)['\"]",
            re.IGNORECASE | re.DOTALL,
        ),
        re.compile(
            r"event\s*\.\s*remove\s*\(\s*['\"]([^'\"]+)['\"]",
            re.IGNORECASE,
        ),
    )

    if not kubejs_dir.is_dir():
        return removed

    def collect_from_text(text: str) -> None:
        text = strip_js_comments(text)
        for pattern in remove_patterns:
            removed.update(pattern.findall(text))

    for path in kubejs_dir.rglob("*.js"):
        _check_scan_cancelled(stop_event)
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        collect_from_text(text)

    for archive in kubejs_dir.rglob("*.zip"):
        _check_scan_cancelled(stop_event)
        try:
            with zipfile.ZipFile(archive, "r") as jar:
                for filename in jar.namelist():
                    if not filename.lower().endswith(".js"):
                        continue
                    try:
                        text = jar.read(filename).decode("utf-8")
                    except UnicodeDecodeError:
                        continue
                    collect_from_text(text)
        except (zipfile.BadZipFile, OSError):
            continue

    return removed


def scan_datapack_path(path: Path, stop_event=None) -> tuple[list[Recipe], int]:
    """Scan a directory or zip datapack for TFC alloy JSON recipes."""
    recipes: list[Recipe] = []
    json_count = 0

    if path.is_dir():
        for file in path.rglob("*.json"):
            _check_scan_cancelled(stop_event)
            json_count += 1

            try:
                data = json.loads(file.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue

            # Build a Minecraft resource ID from the datapack path.
            rel = file.relative_to(path).as_posix()
            recipe_id = recipe_id_from_json_path(rel)

            if recipe_id is None:
                continue

            recipe = parse_json_recipe(data, str(file))
            if recipe:
                recipe.recipe_id = recipe_id
                recipes.append(recipe)

    elif path.is_file() and path.suffix.lower() in {".zip", ".jar"}:
        # Datapacks are commonly .zip.
        jar_recipes, json_count = scan_jar(path)
        recipes.extend(jar_recipes)

    return recipes, json_count


def scan_instance_datapacks(instance: Path, stop_event=None) -> tuple[list[Recipe], int]:
    recipes: list[Recipe] = []
    json_count = 0

    candidates: list[Path] = []

    direct = instance / "datapacks"
    if direct.exists():
        candidates.append(direct)

    saves = instance / "saves"
    if saves.is_dir():
        for world in saves.iterdir():
            dp = world / "datapacks"
            if dp.exists():
                candidates.append(dp)

    for root in candidates:
        _check_scan_cancelled(stop_event)
        if root.is_dir():
            for item in root.iterdir():
                _check_scan_cancelled(stop_event)
                r, count = scan_datapack_path(item, stop_event)
                recipes.extend(r)
                json_count += count

    return recipes, json_count


def collect(
    instance: Path,
    scan_libraries: bool = False,
    progress=None,
    stop_event=None,
) -> tuple[list[Recipe], dict]:
    all_recipes: list[Recipe] = []

    stats = {
        "jars_scanned": 0,
        "jar_json_scanned": 0,
        "kubejs_files_scanned": 0,
        "datapack_json_scanned": 0,
        "json_alloys_found": 0,
        "kubejs_alloys_found": 0,
        "duplicate_ids": 0,
        "english_names_found": 0,
        "removed_recipe_count": 0,
    }

    # 1. Mods
    mods_dir = instance / "mods"
    if mods_dir.is_dir():
        jars = sorted(mods_dir.glob("*.jar"))
        for index, jar in enumerate(jars, start=1):
            _check_scan_cancelled(stop_event)
            _report_progress(progress, "Scanning mods", index, len(jars))
            stats["jars_scanned"] += 1
            recipes, count = scan_jar(jar)
            stats["jar_json_scanned"] += count
            all_recipes.extend(recipes)

    # Optional libraries scan. Usually unnecessary for TFG and can be huge.
    if scan_libraries:
        libraries_dir = instance / "libraries"
        if libraries_dir.is_dir():
            jars = sorted(libraries_dir.rglob("*.jar"))
            for index, jar in enumerate(jars, start=1):
                _check_scan_cancelled(stop_event)
                _report_progress(progress, "Scanning libraries", index, len(jars))
                stats["jars_scanned"] += 1
                recipes, count = scan_jar(jar)
                stats["jar_json_scanned"] += count
                all_recipes.extend(recipes)

    # 2. KubeJS
    _report_progress(progress, "Scanning KubeJS", 0, 0)
    _check_scan_cancelled(stop_event)
    kubejs_recipes, js_count = scan_kubejs(instance / "kubejs", stop_event)
    stats["kubejs_files_scanned"] = js_count
    stats["kubejs_alloys_found"] = len(kubejs_recipes)
    all_recipes.extend(kubejs_recipes)

    # 3. External/world datapacks
    _report_progress(progress, "Scanning datapacks", 0, 0)
    _check_scan_cancelled(stop_event)
    datapack_recipes, count = scan_instance_datapacks(instance, stop_event)
    stats["datapack_json_scanned"] = count
    all_recipes.extend(datapack_recipes)

    _report_progress(progress, "Finalizing recipes", 0, 0)
    _check_scan_cancelled(stop_event)
    removed_recipe_ids = scan_removed_recipe_ids(instance / "kubejs", stop_event)
    before_filter_count = len(all_recipes)
    all_recipes = [
        recipe
        for recipe in all_recipes
        if recipe.recipe_id not in removed_recipe_ids
    ]
    stats["removed_recipe_count"] = before_filter_count - len(all_recipes)

    stats["json_alloys_found"] = sum(
        r.source_type == "json" for r in all_recipes
    )

    return all_recipes, stats


def merge_recipes(recipes: list[Recipe]) -> tuple[dict[str, Recipe], list[dict]]:
    merged: dict[str, Recipe] = {}
    conflicts: list[dict] = []

    priority = {
        "json": 1,
        "kubejs": 3,
    }

    for recipe in recipes:
        old = merged.get(recipe.recipe_id)

        if old is None:
            merged[recipe.recipe_id] = recipe
            continue

        old_priority = priority.get(old.source_type, 0)
        new_priority = priority.get(recipe.source_type, 0)

        winner = recipe if new_priority >= old_priority else old
        loser = old if winner is recipe else recipe

        conflicts.append({
            "recipe_id": recipe.recipe_id,
            "winner": {
                "source": winner.source,
                "type": winner.source_type,
            },
            "ignored": {
                "source": loser.source,
                "type": loser.source_type,
            },
        })

        merged[recipe.recipe_id] = winner

    return merged, conflicts


def build_output(
    merged: dict[str, Recipe],
    conflicts: list[dict],
    stats: dict,
    translations: dict[str, str] | None = None,
) -> dict:
    translations = translations or {}
    alloys = {}

    for recipe_id in sorted(merged):
        recipe = merged[recipe_id]

        alloys[recipe_id] = {
            "result": recipe.result,
            "name": get_english_name(recipe.result, translations),
            "components": {
                component_id: {
                    **component,
                    "name": get_english_name(component_id, translations),
                }
                for component_id, component in recipe.components.items()
            },
            "source": recipe.source,
            "source_type": recipe.source_type,
        }

    return {
        "alloys": alloys,
        "meta": {
            "alloy_count": len(alloys),
            "conflict_count": len(conflicts),
            "stats": stats,
            "conflicts": conflicts,
        },
    }


def relativize_source(source: str, instance: Path) -> str:
    """Store filesystem-backed sources relative to the scanned instance."""
    archive_parts = source.split("!/", 1)
    filesystem_source = Path(archive_parts[0])
    if not filesystem_source.is_absolute():
        return source
    try:
        relative_source = filesystem_source.resolve().relative_to(instance.resolve())
    except ValueError:
        return source
    result = relative_source.as_posix()
    if len(archive_parts) == 2:
        result += "!/" + archive_parts[1]
    return result


def relativize_recipe_sources(
    merged: dict[str, Recipe],
    conflicts: list[dict],
    instance: Path,
) -> None:
    for recipe in merged.values():
        recipe.source = relativize_source(recipe.source, instance)
    for conflict in conflicts:
        for role in ("winner", "ignored"):
            conflict[role]["source"] = relativize_source(
                conflict[role]["source"], instance
            )


def update_alloys(
    instance: Path,
    output_path: Path,
    scan_libraries: bool = False,
    progress=None,
    stop_event=None,
) -> dict:
    """Scan recipes and refresh alloys.json while preserving custom recipes."""
    if not instance.is_dir():
        raise ValueError(f"Minecraft instance was not found: {instance}")
    with output_path.open(encoding="utf-8") as file:
        current_data = json.load(file)
    custom_alloys = {
        identifier: alloy
        for identifier, alloy in current_data.get("alloys", {}).items()
        if alloy.get("source_type") == "custom"
    }

    recipes, stats = collect(
        instance,
        scan_libraries=scan_libraries,
        progress=progress,
        stop_event=stop_event,
    )
    merged, conflicts = merge_recipes(recipes)
    if not merged:
        raise ValueError("No alloy recipes were found in the Minecraft instance")
    relativize_recipe_sources(merged, conflicts, instance)
    _check_scan_cancelled(stop_event)
    translations = scan_language_files(
        instance,
        scan_libraries=scan_libraries,
        stop_event=stop_event,
    )
    known_ids = {
        identifier
        for recipe in merged.values()
        for identifier in [recipe.result, *recipe.components]
    }
    stats["english_names_found"] = sum(
        get_english_name(identifier, translations) != identifier
        for identifier in known_ids
    )
    output = build_output(merged, conflicts, stats, translations)
    output["alloys"].update(custom_alloys)
    output["last_scan_location"] = str(instance.resolve())
    _check_scan_cancelled(stop_event)

    with output_path.open("w", encoding="utf-8") as file:
        json.dump(output, file, ensure_ascii=False, indent=2)
        file.write("\n")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract TFC alloy recipes."
    )
    parser.add_argument(
        "instance",
        nargs="?",
        default=".",
        help="Path to the version instance root.",
    )
    parser.add_argument(
        "--scan-libraries",
        action="store_true",
        help="Also scan libraries/**/*.jar. Usually not required.",
    )

    args = parser.parse_args()

    if args.instance:
        instance = Path(args.instance).resolve()
    else:
        instance = Path(".").resolve()
    if not instance.exists():
        print(f"[ERROR] Path does not exist: {instance}")
        return 1
    script_dir = Path(__file__).parent.resolve()
    output_path = script_dir / "alloys.json"

    print("=" * 60)
    print("TFC Alloy Extractor")
    print("=" * 60)
    print(f"Instance: {instance}")
    print()

    recipes, stats = collect(
        instance,
        scan_libraries=args.scan_libraries,
    )

    merged, conflicts = merge_recipes(recipes)
    relativize_recipe_sources(merged, conflicts, instance)

    translations = scan_language_files(
        instance,
        scan_libraries=args.scan_libraries,
    )
    known_ids = {
        identifier
        for recipe in merged.values()
        for identifier in [recipe.result, *recipe.components]
    }
    stats["english_names_found"] = sum(
        get_english_name(identifier, translations) != identifier
        for identifier in known_ids
    )

    output = build_output(merged, conflicts, stats, translations)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"JAR checked:             {stats['jars_scanned']}")
    print(f"JSON inside JAR checked: {stats['jar_json_scanned']}")
    print(f"JS files checked:       {stats['kubejs_files_scanned']}")
    print(f"JSON in datapack checked: {stats['datapack_json_scanned']}")
    print()
    print(f"Alloys from JSON:            {stats['json_alloys_found']}")
    print(f"Alloys from KubeJS:          {stats['kubejs_alloys_found']}")
    print(f"English names found:         {stats['english_names_found']}")
    print(f"Recipes removed by KubeJS:   {stats['removed_recipe_count']}")
    print(f"Total alloys:          {len(merged)}")
    print(f"Conflicts/overrides: {len(conflicts)}")
    print()
    print(f"[OK] Created: {output_path}")

    if conflicts:
        print()
        print("Overrides:")
        for conflict in conflicts:
            print(f"  {conflict['recipe_id']}")
            print(f"    -> used: {conflict['winner']['source']}")
            print(f"    -> ignored: {conflict['ignored']['source']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())