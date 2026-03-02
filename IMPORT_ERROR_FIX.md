# Import Error Fix Guide

## Error Message

```
ImportError: cannot import name 'load_company_profile' from 'services.ui_data_loader'
```

## Root Cause

The import is failing because either:
1. The Python environment is not activated
2. Required dependencies are missing
3. Python is caching old module versions
4. Streamlit needs to be restarted

## Quick Fix (Try These in Order)

### 1. Activate Virtual Environment

```bash
cd "/Users/rohit/LH2_Automation copy 5"
source .venv/bin/activate
```

### 2. Clear Python Cache

```bash
# Remove all Python cache files
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null
find . -type f -name "*.pyc" -delete

# Clear Streamlit cache
rm -rf ~/.streamlit/cache
```

### 3. Verify Imports Work

```bash
python test_imports.py
```

You should see:
```
✓ load_company_profile imported successfully
✓ load_strategic_brief imported successfully
✓ load_action_history imported successfully
✓ force_refresh_research imported successfully
✅ All imports successful!
```

### 4. Restart Streamlit

```bash
# Stop current Streamlit process (Ctrl+C)

# Start fresh
streamlit run apps.py
```

## Verification

The functions ARE defined in `services/ui_data_loader.py`:

- Line 165: `def load_company_profile(domain)`
- Line 122: `def load_strategic_brief(session_id)`
- Line 223: `def load_action_history(company_id, limit=100)`
- Line 293: `def force_refresh_research(session_id)`

You can verify with:

```bash
grep -n "^def load_company_profile\|^def load_strategic_brief\|^def load_action_history\|^def force_refresh_research" services/ui_data_loader.py
```

## If Still Failing

### Check Python Path

```python
import sys
print(sys.path)
```

Should include: `/Users/rohit/LH2_Automation copy 5`

### Manual Import Test

```python
# In Python REPL or script
import sys
sys.path.insert(0, '/Users/rohit/LH2_Automation copy 5')

from services.ui_data_loader import load_company_profile
print(load_company_profile)  # Should print function object
```

### Check File Permissions

```bash
ls -la services/ui_data_loader.py
```

Should be readable (r-- permission)

### Install Missing Dependencies

If you see `ModuleNotFoundError` for other modules:

```bash
pip install -r requirements.txt
```

Common missing modules:
- `passlib` - Password hashing
- `sqlalchemy` - Database ORM
- `streamlit` - Web framework

## Nuclear Option: Force Reinstall

```bash
# Deactivate and remove venv
deactivate
rm -rf .venv

# Create fresh venv
python3 -m venv .venv
source .venv/bin/activate

# Reinstall everything
pip install --upgrade pip
pip install -r requirements.txt

# Test imports
python test_imports.py

# Run app
streamlit run apps.py
```

## Expected Behavior After Fix

When you start Streamlit:

1. ✅ No import errors
2. ✅ Intelligence tab loads
3. ✅ "Run deep research" button visible
4. ✅ Research executes and displays

## Common Issues

### Issue: Old Streamlit Process Running

**Symptom**: Changes not reflected, old errors persist

**Fix**:
```bash
# Find and kill Streamlit processes
ps aux | grep streamlit
kill -9 <PID>

# Or restart your terminal
```

### Issue: Wrong Python Version

**Symptom**: Syntax errors, module not found

**Fix**:
```bash
python --version  # Should be 3.8+

# Use specific version if needed
python3.10 -m venv .venv
```

### Issue: Import Circular Dependency

**Symptom**: `ImportError` during module initialization

**Fix**: Already fixed by simplifying `services/__init__.py`

## Files Created/Modified

✅ `services/__init__.py` - Package initialization (simplified)
✅ `test_imports.py` - Import verification script
✅ `services/ui_data_loader.py` - Contains all required functions

## Contact if Nothing Works

If none of these solutions work, provide:

1. Output of `python test_imports.py`
2. Output of `python --version`
3. Output of `pip list | grep streamlit`
4. Full error traceback from Streamlit

The functions are definitely there - this is just an environment/cache issue!
