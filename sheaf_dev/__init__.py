"""Dev-only tools for Sheaf. NOT installed in production builds.

This package is excluded from the production Docker image entirely.
It provides dangerous or destructive jobs (e.g. database wipes for dev
instances) that must never be available in production.

Install with: pip install -e ".[dev,devtools]"
"""
