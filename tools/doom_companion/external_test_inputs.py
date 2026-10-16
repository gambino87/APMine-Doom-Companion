"""Keep independently sourced optional test payloads outside distributions."""
# APMine companion change, 2026-10-15: require external JSON_checker fixtures.

from pathlib import Path, PurePosixPath


INPUT_ROOT = "APCpp/jsoncpp/test/jsonchecker"
LIBRARY_TEST_DATA_ROOT = "APCpp/jsoncpp/test/data"
NOTICE_NAMES = {"readme", "readme.txt", "readme.md", "license", "license.txt", "license.md",
                "copying", "copying.txt", "notice", "notice.txt", "notice.md"}


def is_external_test_input(name: str) -> bool:
    return name.casefold().startswith(INPUT_ROOT.casefold() + "/") and PurePosixPath(name).name.casefold() not in NOTICE_NAMES


def is_library_test_path(name: str) -> bool:
    return name.casefold() == LIBRARY_TEST_DATA_ROOT.casefold() or name.casefold().startswith(LIBRARY_TEST_DATA_ROOT.casefold() + "/")


def is_library_test_data(name: str) -> bool:
    return is_library_test_path(name) and PurePosixPath(name).suffix.casefold() in {".json", ".expected"}


def policy() -> dict:
    return {"schema_version": 1, "path": INPUT_ROOT, "status": "external-optional-test-inputs-only",
            "origin_url": "https://www.json.org/JSON_checker/",
            "original_archive_url": "https://www.json.org/JSON_checker/test.zip",
            "compatible_fixture_grant_established": False,
            "preserved_library_test_data": LIBRARY_TEST_DATA_ROOT,
            "scope": "Independently attributed JSON_checker test payloads; preserve notices/references and the JsonCpp test harness",
            "build_profile": "JSONCPP_WITH_TESTS=OFF and JSONCPP_WITH_POST_BUILD_UNITTEST=OFF",
            "historical_recipient_delivery_complete": False}


def validate(root: Path) -> None:
    directories = [root]
    for component in INPUT_ROOT.split("/"):
        matching = []
        for directory in directories:
            if not directory.is_dir():
                continue
            for child in directory.iterdir():
                if child.name.casefold() != component.casefold():
                    continue
                if child.is_symlink() or getattr(child, "is_junction", lambda: False)():
                    raise ValueError("External test input directories cannot follow links")
                matching.append(child)
        directories = matching
    for directory in directories:
        for path in directory.rglob("*"):
            if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
                raise ValueError("External test input directories cannot follow links")
            if path.is_file() and is_external_test_input(path.relative_to(root).as_posix()):
                raise ValueError("External JSON_checker test payloads must not be distributed")
