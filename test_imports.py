#!/usr/bin/env python3
"""
Test script to verify services.ui_data_loader imports work correctly.
Run this before starting Streamlit to verify your environment.
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

print("Testing imports from services.ui_data_loader...")
print("-" * 50)

try:
    from services.ui_data_loader import (
        load_company_profile,
        load_strategic_brief,
        load_action_history,
        force_refresh_research,
    )
    print("✓ load_company_profile imported successfully")
    print("✓ load_strategic_brief imported successfully")
    print("✓ load_action_history imported successfully")
    print("✓ force_refresh_research imported successfully")
    print("-" * 50)
    print("✅ All imports successful!")
    print("\nYou can now run: streamlit run apps.py")
    
except ImportError as e:
    print(f"❌ Import failed: {e}")
    print("\nTroubleshooting:")
    print("1. Activate your virtual environment:")
    print("   source .venv/bin/activate")
    print("\n2. Install missing dependencies:")
    print("   pip install -r requirements.txt")
    print("\n3. Check Python version:")
    print("   python --version  (should be 3.8+)")
    sys.exit(1)

except Exception as e:
    print(f"❌ Unexpected error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
