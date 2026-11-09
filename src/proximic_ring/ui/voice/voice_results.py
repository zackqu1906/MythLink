"""Shared validation for an explicitly empty full-body edit result."""

def _is_explicit_emptying_edit_response(
    response: object,
    expected_original: str,
) -> bool:
    """Return whether either validated edit contract clears all text."""

    if not isinstance(response, dict) or response.get("modified_text") != "":
        return False
    # The full-text race contract intentionally has no original_text field.
    if set(response) == {"modified_text"}:
        return True
    return (
        isinstance(response.get("original_text"), str)
        and response["original_text"] == str(expected_original or "")
        and bool(response["original_text"])
    )

