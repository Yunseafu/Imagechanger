"""iOS 27 Texture/Grain data set: a small plist plus twelve empty 2026 matte images.

Photos only offers grain when the file carries all of it; the plist alone makes the whole
palette disappear.
"""
from __future__ import annotations

import plistlib
import zlib

URI_TEXTURE = "tag:apple.com,2026:photo:metadata:texture_styles"
MATTE_NAMES = ("semanticnosematte", "semanticskinmattev2", "semanticnonfaceskinmatte", "semanticlipsmatte",
               "semanticteethmattev2", "semanticpersonmatte", "semanticglassesmattev2",
               "semanticeyebrowsmatte", "semantictattoomatte", "semantichandsmatte", "semanticearsmatte",
               "semanticfaceskinmatte")
MATTE_URIS = tuple(f"tag:apple.com,2026:photo:aux:{n}" for n in MATTE_NAMES)
MATTE_SIZE = (768, 576)
XMP = (b'<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="XMP Core 6.0.0">\n'
       b' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
       b'  <rdf:Description rdf:about=""\n'
       b'    xmlns:fsincMattes="http://ns.apple.com/fsinc/1.0/">\n'
       b'   <fsincMattes:FSINCMatteVersion>0</fsincMattes:FSINCMatteVersion>\n'
       b'  </rdf:Description>\n'
       b' </rdf:RDF>\n'
       b'</x:xmpmeta>\n')


def grain_seed(primary_bytes: bytes) -> int:
    """Per-photo seed (0..255) so every photo gets its own grain pattern."""
    return zlib.crc32(primary_bytes) % 256


def texture_plist(seed: int) -> bytes:
    root = {"Preset": "Standard", "CaptureType": "LF", "CaptureMode": "Still", "PortType": "PortTypeBack",
            "HardwareModel": "iPhone19,2", "TextureStylePeopleDataVersion": 3, "FilmGrainSeed": seed}
    return plistlib.dumps(root, fmt=plistlib.FMT_BINARY, sort_keys=False)
