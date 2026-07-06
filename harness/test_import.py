
import sys
try:
    import harness
    print(f"Success! harness imported from {harness.__file__}")
    print("Module attributes:", [x for x in dir(harness) if not x.startswith("_")])
except Exception as e:
    print(f"FAIL: {e}")
    import traceback
    traceback.print_exc()
