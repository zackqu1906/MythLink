# Shared by bash and zsh build entry points. Public identity only; no secrets.
MYTHLINK_LOCAL_CONFIG="${PROJECT_ROOT:-${TASK_ROOT:-.}}/.env.macos-signing"
if [[ -f "$MYTHLINK_LOCAL_CONFIG" ]]; then
    source "$MYTHLINK_LOCAL_CONFIG"
fi
MYTHLINK_SIGNING_TEAM="5H42RU3M38"
MYTHLINK_DEFAULT_IDENTITY="Developer ID Application: MythLink (Beijing) Technology Co., Ltd. (5H42RU3M38)"
APPLE_SIGNING_IDENTITY="${APPLE_SIGNING_IDENTITY:-${PROXIMIC_SIGN_IDENTITY:-}}"
if [[ "${PROXIMIC_ALLOW_ADHOC_SIGNING:-0}" == "1" ]]; then
    APPLE_SIGNING_IDENTITY="${APPLE_SIGNING_IDENTITY:--}"
else
    APPLE_SIGNING_IDENTITY="${APPLE_SIGNING_IDENTITY:-$MYTHLINK_DEFAULT_IDENTITY}"
    if [[ "$APPLE_SIGNING_IDENTITY" == "-" ]]; then
        echo "Ad-hoc signing requires PROXIMIC_ALLOW_ADHOC_SIGNING=1 (development only)." >&2
        exit 1
    fi
fi
export APPLE_SIGNING_IDENTITY MYTHLINK_SIGNING_TEAM
export PROXIMIC_SIGN_IDENTITY="$APPLE_SIGNING_IDENTITY"
MYTHLINK_CODESIGN_FLAGS=(--force --sign "$APPLE_SIGNING_IDENTITY")
MYTHLINK_IDENTITY_CHECK=(security find-identity -v -p codesigning)
if [[ -n "${APPLE_SIGNING_KEYCHAIN:-}" ]]; then
    MYTHLINK_CODESIGN_FLAGS+=(--keychain "$APPLE_SIGNING_KEYCHAIN")
    MYTHLINK_IDENTITY_CHECK+=("$APPLE_SIGNING_KEYCHAIN")
    export APPLE_SIGNING_KEYCHAIN
fi
if [[ "$APPLE_SIGNING_IDENTITY" != "-" ]]; then
    MYTHLINK_CODESIGN_FLAGS+=(--options runtime --timestamp)
    if ! "${MYTHLINK_IDENTITY_CHECK[@]}" | grep -F -- "$APPLE_SIGNING_IDENTITY" >/dev/null; then
        echo "Developer ID identity unavailable: $APPLE_SIGNING_IDENTITY" >&2
        echo "Import the certificate/private key and Apple Developer ID intermediate certificate first." >&2
        exit 1
    fi
fi
