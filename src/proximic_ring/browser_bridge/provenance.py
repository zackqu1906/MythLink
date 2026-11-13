"""Optional local hostname evidence, independent of OCR text and model input."""
from .client import post
from .protocol import BUNDLES, clean_snapshot, identity


def snapshot(bundle):
    if bundle.casefold() not in {b.casefold() for b in BUNDLES.values()}:
        return None
    try:
        # Optional export evidence must not inherit a long action timeout.
        result = clean_snapshot(post("/request", dict(bundle=bundle, operation="snapshot", target=None, action=None), timeout=.25))
        return result if result["focused"] and result["authorized"] and result["website"] else None
    except (OSError, ValueError, TypeError):
        return None


def same_page(before, after):
    return bool(before and after and before["website"] == after["website"]
                and identity(before)[:4] == identity(after)[:4])
