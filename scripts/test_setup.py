#!/usr/bin/env python3
"""Test that setup is working correctly."""

import importlib
import sys


def _check_imports() -> tuple[bool, str]:
    try:
        required_modules = ["edgar", "pandas", "sqlalchemy", "fastapi", "streamlit"]
        for module_name in required_modules:
            if importlib.util.find_spec(module_name) is None:
                raise ImportError(f"missing module: {module_name}")
        return True, ""
    except ImportError as e:
        return False, str(e)


def _check_edgar() -> tuple[bool, str]:
    try:
        from edgar import set_identity

        set_identity("Test test@test.com")
        return True, ""
    except Exception as e:
        return False, str(e)


def test_imports() -> None:
    """Test that all required packages are installed."""
    ok, error = _check_imports()
    assert ok, f"Import failed: {error}"


def test_edgar() -> None:
    """Test edgartools works."""
    ok, error = _check_edgar()
    assert ok, f"edgartools test failed: {error}"


def main():
    """Run all tests."""
    print("Testing setup...\n")

    imports_ok, imports_error = _check_imports()
    if imports_ok:
        print("✅ All packages imported successfully")
    else:
        print(f"❌ Import failed: {imports_error}")

    edgar_ok, edgar_error = _check_edgar()
    if edgar_ok:
        print("✅ edgartools configured")
    else:
        print(f"❌ edgartools test failed: {edgar_error}")

    tests = [
        imports_ok,
        edgar_ok,
    ]

    if all(tests):
        print("\n✅ All tests passed! Setup is complete.")
        return 0
    else:
        print("\n❌ Some tests failed. Check errors above.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
