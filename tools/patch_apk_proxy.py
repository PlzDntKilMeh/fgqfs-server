from __future__ import annotations

import argparse
import re
from pathlib import Path


BASE_CONFIG = """    <base-config cleartextTrafficPermitted="true">
        <trust-anchors>
            <certificates src="system" />
            <certificates src="user" />
        </trust-anchors>
    </base-config>"""


V28_CONFIG = f"""<?xml version="1.0" encoding="utf-8"?>
<network-security-config>
{BASE_CONFIG}
</network-security-config>
"""


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_if_changed(path: Path, text: str) -> bool:
    old = read_text(path)
    if old == text:
        return False
    path.write_text(text, encoding="utf-8", newline="\n")
    return True


def patch_manifest(apk_dir: Path) -> bool:
    path = apk_dir / "AndroidManifest.xml"
    text = read_text(path)
    if "android:networkSecurityConfig=" in text:
        return False

    patched, count = re.subn(
        r"(<application\b)",
        r'\1 android:networkSecurityConfig="@xml/network_security_config"',
        text,
        count=1,
    )
    if count != 1:
        raise RuntimeError("Could not find <application> in AndroidManifest.xml")
    return write_if_changed(path, patched)


def patch_normal_config(apk_dir: Path) -> bool:
    path = apk_dir / "res" / "xml" / "network_security_config.xml"
    text = read_text(path)

    if '<certificates src="user" />' in text and "<base-config" in text:
        return False

    if "<base-config" in text:
        patched, count = re.subn(
            r"\s*<base-config\b[^>]*(?:/>|>.*?</base-config>)",
            "\n" + BASE_CONFIG,
            text,
            count=1,
            flags=re.DOTALL,
        )
        if count != 1:
            raise RuntimeError("Could not replace base-config in res/xml/network_security_config.xml")
    else:
        patched = text.replace(
            "<network-security-config>",
            "<network-security-config>\n" + BASE_CONFIG,
            1,
        )
        if patched == text:
            raise RuntimeError("Could not find <network-security-config> in res/xml/network_security_config.xml")

    return write_if_changed(path, patched)


def patch_v28_config(apk_dir: Path) -> bool:
    path = apk_dir / "res" / "xml-v28" / "network_security_config.xml"
    return write_if_changed(path, V28_CONFIG)


def main() -> None:
    parser = argparse.ArgumentParser(description="Patch a decompiled FG:QfS APK folder for proxy certificate trust.")
    parser.add_argument("apk_dir", help="Path to the decompiled APK folder")
    args = parser.parse_args()

    apk_dir = Path(args.apk_dir).resolve()
    required = [
        apk_dir / "AndroidManifest.xml",
        apk_dir / "res" / "xml" / "network_security_config.xml",
        apk_dir / "res" / "xml-v28" / "network_security_config.xml",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise SystemExit("Missing required APK files:\n  " + "\n  ".join(missing))

    changes = {
        "AndroidManifest.xml": patch_manifest(apk_dir),
        r"res\xml\network_security_config.xml": patch_normal_config(apk_dir),
        r"res\xml-v28\network_security_config.xml": patch_v28_config(apk_dir),
    }

    print(f"Patched APK folder: {apk_dir}")
    for name, changed in changes.items():
        print(f"  {'updated' if changed else 'already ok'}  {name}")


if __name__ == "__main__":
    main()
