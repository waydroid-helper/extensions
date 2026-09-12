#!/bin/bash

# HPE 14 libhoudini time-bomb fix, based on:
# https://github.com/waydroid-helper/waydroid-helper/issues/127
# The expiration check ends in a six-byte JAE at file offset 0xe6085.

PatchHexAtOffset() {
    local file="$1" offset="$2" replacement="$3" offset_dec
    offset_dec=$((offset))
    local length=$(( ${#replacement} / 2 )) current
    [[ -f "$file" ]] || { echo "Patch target not found: $file" >&2; return 1; }
    [[ "$replacement" =~ ^[0-9a-fA-F]+$ ]] && (( length > 0 && ${#replacement} % 2 == 0 )) || { echo "Invalid replacement hex" >&2; return 1; }
    current=$(od -An -tx1 -j "$offset_dec" -N "$length" "$file" | tr -d ' \n')
    [[ ${#current} -eq ${#replacement} ]] || { echo "Patch offset outside file: $file @ 0x$(printf '%x' "$offset")" >&2; return 1; }
    if [[ "${current,,}" == "${replacement,,}" ]]; then
        echo "Already patched $file at 0x$(printf '%x' "$offset")"
        return 0
    fi
    printf '%s' "$replacement" | xxd -r -p | dd of="$file" bs=1 seek="$offset_dec" conv=notrunc status=none || return 1
    echo "Patched $file at 0x$(printf '%x' "$offset") with $replacement"
}
