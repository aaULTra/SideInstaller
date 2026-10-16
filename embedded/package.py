#!/usr/bin/env python3
"""Package a native SideInstaller build into an adapted LiveContainer host."""
import argparse
import json
import os
import plistlib
import shutil
import struct
import subprocess
from pathlib import Path

HOST_ID = "com.aault.livecontainer.sideinstaller"


def entitlement_template(path, bundle_id):
    # This is upstream's ad-hoc placeholder, not a signing account. Resigning
    # must replace it with the user's actual development team and profile.
    text = path.read_text()
    for key, value in {
        "$(DEVELOPMENT_TEAM)": "AAAAA11111", "$(AppIdentifierPrefix)": "AAAAA11111.",
        "$(PRODUCT_BUNDLE_IDENTIFIER)": bundle_id,
        "$(APP_GROUP_SIDESTORE)": "group." + HOST_ID,
        "$(APP_GROUP_ALTSTORE)": "group." + HOST_ID + ".shared",
    }.items():
        text = text.replace(key, value)
    data = plistlib.loads(text.encode())
    data["application-identifier"] = "AAAAA11111." + bundle_id
    data["com.apple.developer.team-identifier"] = "AAAAA11111"
    assert "$(" not in json.dumps(data), "Unresolved signing template macro"
    assert "com.kdt.livecontainer.shared" not in json.dumps(data)
    return data


def update_plist(path, change):
    with path.open("rb") as stream:
        data = plistlib.load(stream)
    change(data)
    with path.open("wb") as stream:
        plistlib.dump(data, stream, sort_keys=False)


def package(host, guest, output, dylibify, upstream):
    output.mkdir(parents=True, exist_ok=True)
    payload = output / "Payload"
    if payload.exists():
        raise ValueError("Refusing to overwrite a previous Payload; use a fresh output directory.")
    payload.mkdir()
    app = payload / "LiveContainer.app"
    shutil.copytree(host, app)
    framework = app / "Frameworks/SideInstallerApp.framework"
    shutil.copytree(guest, framework)
    binary = framework / "SideInstaller"
    converted = framework / "SideInstaller.dylib"
    subprocess.run([str(dylibify), str(binary), str(converted)], check=True)
    converted.replace(binary)
    # Same conversion used by upstream's embedded SideStore flavour.
    # A normal sideloader must sign the finished host/frameworks/extensions.
    subprocess.run(["ldid", "-S", str(binary)], check=True)
    if struct.unpack_from("<I", binary.read_bytes(), 12)[0] != 6:
        raise ValueError("Embedded SideInstaller was not converted to MH_DYLIB.")
    shutil.copyfile(upstream / ".github/sidelc/LCAppInfo.plist", framework / "LCAppInfo.plist")

    support = app / "Frameworks/SideStoreSupport.framework"
    metadata = support / "Metadata.appintents"
    if not metadata.is_dir():
        raise ValueError("Host refresh bridge's App Intents metadata was not generated.")
    if (app / "Metadata.appintents").exists():
        shutil.rmtree(app / "Metadata.appintents")
    shutil.copytree(metadata, app / "Metadata.appintents")

    # App extensions must be immediate children of the actual host, not nested
    # under a guest framework. Retain the pairing Live Activity extension.
    plugins = framework / "PlugIns"
    if plugins.exists():
        (app / "PlugIns").mkdir(exist_ok=True)
        for extension in plugins.iterdir():
            if extension.suffix != ".appex":
                raise ValueError(f"Unexpected embedded plugin: {extension.name}")
            destination = app / "PlugIns" / extension.name
            shutil.move(str(extension), destination)
            update_plist(destination / "Info.plist", lambda data: data.update(
                CFBundleIdentifier=HOST_ID + ".PairingActivity"))
        plugins.rmdir()

    def merge_guest_permissions(data):
        guest_info = plistlib.loads((guest / "Info.plist").read_bytes())
        for key in ("NSLocalNetworkUsageDescription", "NSLocationWhenInUseUsageDescription",
                    "NSLocationAlwaysAndWhenInUseUsageDescription", "NSSupportsLiveActivities"):
            if key in guest_info:
                data[key] = guest_info[key]
        for key in ("LSApplicationQueriesSchemes", "NSBonjourServices", "UIBackgroundModes"):
            data[key] = list(dict.fromkeys(data.get(key, []) + guest_info.get(key, [])))
        data["SIEmbeddedGuestVersion"] = guest_info["CFBundleShortVersionString"]
        data["SIEmbeddedSourceRevision"] = os.environ.get("SI_SOURCE_REVISION", "local-evaluation")
    update_plist(app / "Info.plist", merge_guest_permissions)

    for signature in list(app.rglob("_CodeSignature")):
        shutil.rmtree(signature)
    for provision in app.rglob("embedded.mobileprovision"):
        provision.unlink()

    report = verify(app)
    (output / "verification.json").write_text(json.dumps(report, indent=2) + "\n")
    # Source notices travel with the experimental build.
    notices = app / "EmbeddedSourceNotices"
    notices.mkdir()
    shutil.copyfile(upstream / "LICENSE", notices / "LiveContainer-LICENSE")
    shutil.copyfile(Path(__file__).resolve().parent.parent / "LICENSE.md", notices / "SideInstaller-LICENSE.md")
    # Preserve LC's signing requests in ad-hoc signatures. A build with signing
    # disabled alone would lose app/keychain groups that IPA resigners read.
    templates = [(app, upstream / "entitlements.xml")]
    for extension in (app / "PlugIns").glob("*.appex"):
        source = upstream / extension.stem / (extension.stem + ".entitlements")
        if source.exists():
            templates.append((extension, source))
    for bundle, source in templates:
        data = plistlib.loads((bundle / "Info.plist").read_bytes())
        template = entitlement_template(source, data["CFBundleIdentifier"])
        location = notices / (bundle.stem + "-entitlements.plist")
        location.write_bytes(plistlib.dumps(template))
        executable = bundle / data["CFBundleExecutable"]
        subprocess.run(["ldid", "-S" + str(location), str(executable)], check=True)
        recovered = plistlib.loads(subprocess.check_output(["ldid", "-e", str(executable)]))
        assert recovered == template, "Signing entitlement template was not preserved"
    report["isolatedSigningEntitlementsVerified"] = True
    (output / "verification.json").write_text(json.dumps(report, indent=2) + "\n")
    subprocess.run(["ditto", "-c", "-k", "--keepParent", "Payload", "LiveContainer-SideInstaller-unsigned.ipa"],
                   cwd=output, check=True)


def verify(app):
    info = plistlib.loads((app / "Info.plist").read_bytes())
    assert info["CFBundleIdentifier"] == HOST_ID, "Experimental host must not overwrite stock LiveContainer"
    guest = app / "Frameworks/SideInstallerApp.framework"
    assert guest.is_dir() and (guest / "SideInstaller").is_file()
    assert not (app / "Frameworks/SideStoreApp.framework").exists(), "Do not ship an unused SideStore app"
    assert not (guest / "PlugIns").exists(), "Nested app extensions are not installable"
    assert (app / "PlugIns/LiveProcess.appex").is_dir()
    assert struct.unpack_from("<I", (guest / "SideInstaller").read_bytes(), 12)[0] == 6
    for extension in (app / "PlugIns").glob("*.appex"):
        extension_info = plistlib.loads((extension / "Info.plist").read_bytes())
        assert extension_info["CFBundleIdentifier"].startswith(HOST_ID + "."), extension.name
    actions = json.loads((app / "Metadata.appintents/extract.actionsdata").read_bytes())
    refresh = actions["actions"]["RefreshAllAppsIntent"]
    assert refresh["isDiscoverable"]
    assert refresh["openAppWhenRun"] is False
    assert refresh["fullyQualifiedTypeName"].startswith("SideStoreSupport.")
    assert any(shortcut["actionIdentifier"] == "RefreshAllAppsIntent" for shortcut in actions["autoShortcuts"])
    protocols = refresh["systemProtocols"]
    assert "com.apple.link.systemProtocol.ForegroundContinuable" in protocols
    assert "com.apple.link.systemProtocol.ProgressReporting" in protocols
    return {"host": HOST_ID, "guest": "SideInstaller", "containsSideStoreApp": False,
            "nativeRefreshIntent": True, "extensions": sorted(x.name for x in (app / "PlugIns").glob("*.appex")),
            "runtimeDeviceTests": "required; compile/package checks do not prove device execution"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("host", "guest", "output", "dylibify", "upstream"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    package(*(getattr(args, key).resolve() for key in ("host", "guest", "output", "dylibify", "upstream")))
