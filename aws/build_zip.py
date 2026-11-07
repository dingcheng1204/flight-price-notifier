"""Build a deterministic Lambda zip and print its md5 + base64 (for the flight-seed bridge).

Usage:  python3 aws/build_zip.py flight-parser
Writes aws/build/<name>.zip; prints "md5 <hex>" then the base64 payload.
"""
import base64
import hashlib
import pathlib
import sys
import zipfile

root = pathlib.Path(__file__).resolve().parent
name = sys.argv[1]
src = root / "lambdas" / name / "index.py"
out = root / "build" / f"{name}.zip"
out.parent.mkdir(exist_ok=True)

info = zipfile.ZipInfo("index.py", (2026, 1, 1, 0, 0, 0))  # fixed timestamp -> stable md5
info.external_attr = 0o644 << 16
info.compress_type = zipfile.ZIP_DEFLATED
with zipfile.ZipFile(out, "w") as z:
    z.writestr(info, src.read_bytes())

data = out.read_bytes()
print("md5", hashlib.md5(data).hexdigest())
print(base64.b64encode(data).decode())
