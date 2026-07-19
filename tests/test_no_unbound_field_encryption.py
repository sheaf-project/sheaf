"""Static guard: every sheaf.crypto encrypt/decrypt call site passes an aad.

The write gate keeps writing v1 when no aad is supplied, so a future call
that forgets `aad=` would silently persist an unbound v1 ciphertext even with
FIELD_ENCRYPTION_WRITE_V2 on - exactly the regression the AAD binding exists
to prevent, and one that no runtime test would catch (it just looks like a
normal write). This walks the AST of every module under sheaf/ and fails if a
call to a name imported from sheaf.crypto (`encrypt` / `decrypt` /
`decrypt_field`) omits the `aad` keyword.

Scope notes:
- `sheaf/crypto.py` defines these functions and calls the nacl box directly;
  `sheaf/services/prism_crypto.py` is a separate Fernet scheme. Neither is a
  v2 AAD call site, so both are excluded.
- Only calls to names actually imported from sheaf.crypto in that file are
  checked (import alias respected), so an unrelated same-named function is
  not flagged.
"""

from __future__ import annotations

import ast
import pathlib

_SHEAF = pathlib.Path(__file__).resolve().parent.parent / "sheaf"
_CHECKED = {"encrypt", "decrypt", "decrypt_field"}
_EXCLUDE = {"crypto.py", "prism_crypto.py"}


def _scan():
    """Return (violations, total_checked_calls).

    violations: (relpath, lineno, funcname) for a checked call missing aad.
    total_checked_calls: how many checked calls were seen at all, so the test
    can assert it actually scanned something rather than passing vacuously.
    """
    violations = []
    total = 0
    for path in _SHEAF.rglob("*.py"):
        if path.name in _EXCLUDE:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))

        # local-name -> original crypto function, honouring `import ... as`.
        local: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "sheaf.crypto":
                for alias in node.names:
                    if alias.name in _CHECKED:
                        local[alias.asname or alias.name] = alias.name
        if not local:
            continue

        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in local
            ):
                total += 1
                if not any(kw.arg == "aad" for kw in node.keywords):
                    violations.append(
                        (path.relative_to(_SHEAF.parent), node.lineno, node.func.id)
                    )
    return violations, total


def test_all_field_encryption_calls_pass_aad():
    violations, total = _scan()
    assert not violations, (
        "field-encryption call site(s) missing aad= (would write/expect an "
        "unbound v1 ciphertext): "
        + "; ".join(f"{p}:{ln} {fn}()" for p, ln, fn in violations)
    )
    # Guard against the scanner silently matching nothing (e.g. an import-name
    # bug) and passing vacuously. There are ~150 such calls in the tree.
    assert total > 100, (
        f"only {total} sheaf.crypto call sites scanned; the guard is likely "
        f"not matching correctly"
    )
