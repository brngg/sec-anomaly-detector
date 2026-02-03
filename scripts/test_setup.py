#!/usr/bin/env python3
"""Test that setup is working correctly."""

import sys

def test_imports():
    """Test that all required packages are installed."""
    try:
        import edgar
        import pandas
        import sqlalchemy
        import fastapi
        import streamlit
        print("✅ All packages imported successfully")
        return True
    except ImportError as e:
        print(f"❌ Import failed: {e}")
        return False

def test_edgar():
    """Test edgartools works."""
    try:
        from edgar import set_identity
        set_identity("Test test@test.com")
        print("✅ edgartools configured")
        return True
    except Exception as e:
        print(f"❌ edgartools test failed: {e}")
        return False

def main():
    """Run all tests."""
    print("Testing setup...\n")
    
    tests = [
        test_imports(),
        test_edgar(),
    ]
    
    if all(tests):
        print("\n✅ All tests passed! Setup is complete.")
        return 0
    else:
        print("\n❌ Some tests failed. Check errors above.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
