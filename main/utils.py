import re

# Replace all version-like numeric chunks with a placeholder.
# Examples replaced:
#   141.0.0.0      -> <num>
#   10_15_7        -> <num>
#   18_6_2         -> <num>
#   605.1.15       -> <num>
_NUM_PATTERN = re.compile(r"\d+(?:[._]\d+)*")


def normalize_ua(ua: str) -> str:
    ua = ua.strip()
    # Collapse whitespace so minor spacing differences don't break matches
    ua = re.sub(r"\s+", " ", ua)
    # Normalize case (optional but usually fine)
    ua = ua.lower()
    # Remove/abstract numeric version blobs
    ua = _NUM_PATTERN.sub("<num>", ua)
    return ua


# Build the normalized whitelist once
NORMALIZED_WHITELIST = {
    "mozilla/<num> (linux; android <num>; k) applewebkit/<num> (khtml, like gecko) chrome/<num> mobile safari/<num> edga/<num>",
    "mozilla/<num> (macintosh; intel mac os x <num>) applewebkit/<num> (khtml, like gecko) obsidian/<num> chrome/<num> electron/<num> safari/<num>",
    "mozilla/<num> (windows nt <num>; win<num>; x<num>) applewebkit/<num> (khtml, like gecko) obsidian/<num> chrome/<num> electron/<num> safari/<num>",
    "mozilla/<num> (windows nt <num>; win<num>; x<num>; rv:<num>) gecko/<num> firefox/<num>",
    "mozilla/<num> (macintosh; intel mac os x <num>) applewebkit/<num> (khtml, like gecko) chrome/<num> safari/<num>",
    "mozilla/<num> (iphone; cpu iphone os <num> like mac os x) applewebkit/<num> (khtml, like gecko) version/<num> mobile/<num>e<num> safari/<num>",
    "mozilla/<num> (x<num>; linux x<num>; rv:<num>) gecko/<num> firefox/<num>",
    "mozilla/<num> (windows nt <num>; win<num>; x<num>) applewebkit/<num>",
    "mozilla/<num> (x<num>; linux x<num>) applewebkit/<num> (khtml, like gecko) chrome/<num> safari/<num>",
    "mozilla/<num> (iphone; cpu iphone os <num> like mac os x) applewebkit/<num> (khtml, like gecko) mobile/<num>e<num>",
    "mozilla/<num> (macintosh; intel mac os x <num>) applewebkit/<num> (khtml, like gecko) chrome/<num> safari/<num> opr/<num>",
    "mozilla/<num> (windows nt <num>; win<num>; x<num>) applewebkit/<num> (khtml, like gecko) chrome/<num> yabrowser/<num> safari/<num>",
    "mozilla/<num> (windows nt <num>; win<num>; x<num>) applewebkit/<num> (khtml, like gecko) chrome/<num> safari/<num> edg/<num>",
    "mozilla/<num> (macintosh; intel mac os x <num>; rv:<num>) gecko/<num> firefox/<num>",
    "mozilla/<num> (windows nt <num>; win<num>; x<num>) applewebkit/<num> (khtml, like gecko) chrome/<num> safari/<num>",
    "mozilla/<num> (windows nt <num>; win<num>; x<num>) applewebkit/<num> (khtml, like gecko) chrome/<num> safari/<num> opr/<num>",
    "mozilla/<num> (linux; android <num>; k) applewebkit/<num> (khtml, like gecko) chrome/<num> mobile safari/<num>",
    "mozilla/<num> (macintosh; intel mac os x <num>) applewebkit/<num> (khtml, like gecko)",
    "mozilla/<num> (linux; android <num>; k) applewebkit/<num> (khtml, like gecko) samsungbrowser/<num> chrome/<num> mobile safari/<num>",
    "mozilla/<num> (macintosh; intel mac os x <num>) applewebkit/<num> (khtml, like gecko) version/<num> safari/<num>",
}


def is_whitelisted(ua: str) -> bool:
    """Return True if UA matches one of the whitelisted patterns, ignoring versions."""
    return normalize_ua(ua) in NORMALIZED_WHITELIST
