"""Builder for the ``styles`` metadata item (a binary plist) and its supporting constants."""
from __future__ import annotations

import plistlib
import struct

URI_STYLES = "tag:apple.com,2023:photo:metadata:styles"
URI_LINEAR_THUMB = "tag:apple.com,2023:photo:aux:linearthumbnail"
URI_STYLE_DELTA = "tag:apple.com,2023:photo:aux:styledeltamap"

SCHEMA = 16                  # iOS 27 / iPhone 18 generation (14 and 15 are older)
REGIONS = 2 * 18 * 24        # spatial cells of the colour-transform field
TERMS = 10                   # 1, R, G, B, R2, G2, B2, RG, RB, GB
LIGHT_MAP = 32               # light maps are 32x32 half floats
PIXEL_FORMAT_L00H = 0x4C303068   # 'L00h'

# Flat light-map values that keep the renderer spatially neutral.
FLAT_TONE_MAPPED = 0.3115234375
FLAT_LINEAR = 0.200927734375
LINEAR_OVER_TONE = 0.166     # LinearImage statistics are the ToneMapped ones x 0.166
GAIN = 14.565662384033203   # HDR gain; key "h" is always Gain / 4

# Scene statistics used when the photo is not analysed (values of a typical daylight scene).
DEFAULT_TONE_STATS = {"blackPoint": 0.0, "p02": 0.004, "p10": 0.004, "p25": 0.024,
                      "p50": 0.160, "p75": 0.312, "p98": 0.788, "whitePoint": 0.932,
                      "highKey": 0.70}
STAT_FLAVOURS = ("ToneMappedImage", "ToneMappedImageSkinBased", "ToneMappedImagePersonSegmentBased",
                 "ToneMappedImageRedChannelSkinBased", "ToneMappedImageGreenChannelSkinBased",
                 "ToneMappedImageBlueChannelSkinBased", "LinearImage", "LinearImageSkinBased",
                 "LinearImagePersonSegmentBased", "LinearGTCImage")


def _half(values) -> bytes:
    return struct.pack("<%de" % len(values), *values)


def identity_field() -> bytes:
    """Per-region colour polynomial that returns its input unchanged."""
    one_cell = [0.0] * 3                                  # constant term
    one_cell += [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]   # R, G, B terms -> identity
    one_cell += [0.0] * (3 * (TERMS - 4))                 # quadratic terms
    return _half(one_cell * REGIONS)


def identity_tone_curve() -> bytes:
    """4-byte header + 256 uint16 points of a straight line."""
    return bytes([1, 1, 0, 0]) + b"".join(struct.pack("<H", round(i * 65535 / 255)) for i in range(256))


def flat_map(value: float) -> bytes:
    return _half([value] * (LIGHT_MAP * LIGHT_MAP))


def stats_block(tone: dict, high_key_linear: float = 0.964) -> dict:
    linear = {k: v * LINEAR_OVER_TONE for k, v in tone.items() if k != "highKey"}
    linear["highKey"] = high_key_linear
    return {"ToneMappedImage": dict(tone), "LinearImage": linear}


def luma_percentiles(luma) -> dict:
    """Statistics block from linear-light luma samples (0..1)."""
    v = sorted(luma)
    if not v:
        return dict(DEFAULT_TONE_STATS)

    def pct(q):
        return float(v[min(len(v) - 1, int(q * (len(v) - 1) + 0.5))])
    return {"blackPoint": 0.0, "p02": pct(.02), "p10": pct(.10), "p25": pct(.25),
            "p50": pct(.50), "p75": pct(.75), "p98": pct(.98), "whitePoint": float(v[-1]),
            "highKey": DEFAULT_TONE_STATS["highKey"]}


def _empty_flavour(high_key=1.0):
    return {"blackPoint": 0.0, "p02": 0.0, "p10": 0.0, "p25": 0.0, "p50": 0.0, "p75": 0.0,
            "p98": 0.0, "whitePoint": 0.0, "highKey": high_key}


def build_styles(tone_stats: dict | None = None, person_masks: bool = False) -> bytes:
    tone_stats = tone_stats or DEFAULT_TONE_STATS
    six = {name: _empty_flavour() for name in STAT_FLAVOURS}
    six["LinearGTCImage"] = _empty_flavour(0.0)
    six.update(stats_block(tone_stats))
    plist = {
        "0": SCHEMA,
        "1": identity_field(),
        "2": True,
        "3": identity_tone_curve(),
        "4": 5.384615421295166,
        "5": 0,
        "6": six,
        "7": {"PeopleRatio": 0.0, "SkinRatio": 0.0,
              "PersonMasksValidHint": 1.0 if person_masks else -1.0},
        "c": flat_map(FLAT_TONE_MAPPED),
        "d": flat_map(FLAT_LINEAR),
        "e": LIGHT_MAP,
        "f": LIGHT_MAP,
        "g": PIXEL_FORMAT_L00H,
        "h": GAIN / 4,
        "i": {"OriginalRangeMin": -0.0019588470458984375,
              "OriginalRangeMax": 0.08447265625, "Gain": GAIN},
        "j": 1.0,
        "k": False,
        "l": False,
    }
    return plistlib.dumps(plist, fmt=plistlib.FMT_BINARY, sort_keys=False)
