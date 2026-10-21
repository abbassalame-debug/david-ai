"""THROWAWAY (CI gate proof, never merged): one deliberately failing check. The Windows job must go red on this PR."""
import sys
print("  FAIL  deliberately failing check: the CI gate must turn this pull request red")
print("\n0/1 checks passed")
sys.exit(1)
