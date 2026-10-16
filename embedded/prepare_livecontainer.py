#!/usr/bin/env python3
"""Apply a small, checked adaptation to a pinned LiveContainer checkout.

Upstream's builtinSideStore / isSideStore routing identifiers and support target
names remain implementation details. Paths, executable, UI and refresh engine
are SideInstaller-specific. Never run this against a user's installed app.
"""
import argparse
import plistlib
import shutil
import subprocess
from pathlib import Path
from certificate_validation import patch as patch_certificate_validation

LIVE_CONTAINER_COMMIT = "4dbe0f9a626de801184a42c0be8d2cb105058e3d"
HOST_ID = "com.aault.livecontainer.sideinstaller"
HOST_SCHEME = "livecontainer-sideinstaller"
CERT_SERVICE = HOST_ID


def replace(path, old, new, count=None):
    text = path.read_text()
    found = text.count(old)
    if not found or (count is not None and found != count):
        raise ValueError(f"Upstream patch anchor changed: {path}: {old[:70]!r} ({found} matches)")
    path.write_text(text.replace(old, new))


def edit_plist(path, transform):
    with path.open("rb") as stream:
        data = plistlib.load(stream)
    transform(data)
    with path.open("wb") as stream:
        plistlib.dump(data, stream, sort_keys=False)


def prepare(root):
    embedded = Path(__file__).resolve().parent
    revision = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    if revision != LIVE_CONTAINER_COMMIT:
        raise ValueError(f"Expected LiveContainer {LIVE_CONTAINER_COMMIT}, got {revision}")
    if (root / ".sideinstaller-adapted").exists():
        raise ValueError("Use a fresh upstream checkout, not an already adapted one.")

    bootstrap = root / "LiveContainer/LCBootstrap.m"
    replace(bootstrap, "Frameworks/SideStoreApp.framework", "Frameworks/SideInstallerApp.framework", 4)
    replace(bootstrap, '[docPath stringByAppendingPathComponent:@"SideStore"]',
            '[docPath stringByAppendingPathComponent:@"SideInstaller"]', 1)
    replace(bootstrap, "Import Certificate from SideStore", "Import Certificate from SideInstaller")
    replace(root / "LiveContainerSwiftUI/Models/BuiltInSideStoreAppInfo.swift",
            "Frameworks/SideStoreApp.framework", "Frameworks/SideInstallerApp.framework", 1)
    replace(root / "LiveContainerSwiftUI/Models/BuiltInSideStoreAppInfo.swift",
            "SideStore/Library/Caches", "SideInstaller/Library/Caches", 1)

    header = root / "SideStoreSupport/XPCServer.h"
    replace(header, "- (void)finish:(NSString*)error;",
            "- (void)finish:(NSString*)error;\n"
            "- (void)finishWithSummary:(NSString * _Nullable)summary error:(NSString * _Nullable)error;", 1)
    replace(header, "@protocol RefreshClient\n", "@protocol RefreshClient\n- (void)cancelRefresh;\n", 1)
    replace(header, "- (void) relaunchLC;", "- (void) relaunchLC;\n"
            "- (void)startLocalRefresh:(id<RefreshServer>)server;", 1)
    replace(root / "SideStoreSupport/XPCServer.m", "@property NSObject<RefreshServer>* reporter;",
            "@property (weak) NSObject<RefreshServer>* reporter;", 1)
    shutil.copyfile(embedded / "HostClient.m", root / "SideStoreSupport/XPCClient.m")
    shutil.copyfile(embedded / "HostRefresh.swift", root / "SideStoreSupport/SideStore.swift")
    (root / "SideStoreSupport/SideStoreClient.swift").write_text(
        "// SideInstaller refresh execution is in the embedded app's SIEmbeddedBridge.\nimport Foundation\n")
    process = root / "LiveProcess/main.m"
    replace(process, '#import <mach-o/dyld.h>', '#import <mach-o/dyld.h>\n#import <objc/message.h>', 1)
    replace(process, 'connection.interruptionHandler = ^{\n            NSLog(@"interrupted!!!");\n        };',
            'void (^cancelRefresh)(void) = ^{\n'
            '            dispatch_async(dispatch_get_main_queue(), ^{\n'
            '                Class bridge = NSClassFromString(@"SIEmbeddedBridge");\n'
            '                SEL cancel = NSSelectorFromString(@"cancelRefresh");\n'
            '                if ([bridge respondsToSelector:cancel]) ((void (*)(id, SEL))objc_msgSend)(bridge, cancel);\n'
            '            });\n'
            '        };\n'
            '        connection.interruptionHandler = cancelRefresh;\n'
            '        connection.invalidationHandler = cancelRefresh;', 1)

    utils = root / "LiveContainerSwiftUI/Utilities/LCUtils.m"
    replace(root / "LiveContainer/LCSharedUtils.h", '@interface LCSharedUtils : NSObject',
            '@interface LCSharedUtils : NSObject\n+ (NSString *)embeddedEntitlementsAtPath:(NSString *)path;', 1)
    replace(root / "LiveContainer/LCSharedUtils.m", '#import "LCSharedUtils.h"',
            '#import "LCSharedUtils.h"\n#import "LCMachOUtils.h"', 1)
    replace(root / "LiveContainer/LCSharedUtils.m", '@implementation LCSharedUtils',
            '@implementation LCSharedUtils\n+ (NSString *)embeddedEntitlementsAtPath:(NSString *)path {\n'
            '    return getExecutableEntitlementXML(path);\n}\n', 1)
    replace(utils, "// use uttype to accurately detect store",
            "// This flavour has a built-in SideInstaller. The enum is an upstream UI detail.\n"
            "        if (NSUserDefaults.sideStoreExist) { ans = SideStore; return; }\n"
            "        // use uttype to accurately detect store", 1)
    replace(root / "LiveContainerSwiftUI/Utilities/LCUtilsExtensions.swift",
            'case .SideStore:\n            return "SideStore"',
            'case .SideStore:\n            return "SideInstaller"', 1)

    settings = root / "LiveContainerSwiftUI/Views/Settings/LCSettingsView.swift"
    replace(settings, 'kSecAttrService as String: "com.kdt.livecontainer"',
            f'kSecAttrService as String: "{CERT_SERVICE}"', 2)
    replace(settings, '"lc.settings.importCertFromBuiltinSideStore".loc',
            '"Import certificate from SideInstaller"', 1)
    replace(settings, '"lc.settings.importCertFromBuiltinSideStoreDesc".loc',
            '"Share the certificate in the built-in SideInstaller first. This imports its signing certificate, not your Apple ID password."', 1)
    replace(settings, '"lc.settings.importCertFromBuiltinSideStore.certNotFounndErr".loc',
            '"No matching certificate was shared. Open SideInstaller and tap Share signing certificate after signing an app with the same team as this host."', 1)
    replace(settings, 'if store == .AltStore || store == .SideStore {',
            'if UserDefaults.sideStoreExist() || store == .AltStore || store == .SideStore {', 1)
    replace(settings, '            }\n        }\n        \n        let storeScheme : String',
            '            }\n            return // cancelling the built-in import must not open another store\n        }\n        \n        let storeScheme : String', 1)
    # This developer-only upstream button would remove SideStore integration,
    # not our embedded app. Do not offer it in this flavour.
    replace(settings, 'Button {\n                            Task { await nukeSideStore() }\n                        } label: {\n                            Text("Nuke SideStore")\n                        }',
            '// Built-in SideInstaller is managed by the combined build.', 1)

    app_list = root / "LiveContainerSwiftUI/Views/AppList/LCAppListView.swift"
    replace(app_list, 'if url.scheme == "sidestore" && UserDefaults.sideStoreExist()',
            'if url.scheme == "sideinstaller-embedded" && UserDefaults.sideStoreExist()', 1)
    replace(app_list, '.frame(width: UIFont.preferredFont(forTextStyle: .body).lineHeight, height: UIFont.preferredFont(forTextStyle: .body).lineHeight)',
            '.frame(width: UIFont.preferredFont(forTextStyle: .body).lineHeight, height: UIFont.preferredFont(forTextStyle: .body).lineHeight)\n'
            '                                .accessibilityLabel("Open SideInstaller")', 1)

    config = root / "xcconfigs/Global.xcconfig"
    replace(config, "LIVECONTAINER_BUNDLE_IDENTIFIER = com.kdt.livecontainer$(DEVELOPMENT_TEAM_SUFFIX)",
            f"LIVECONTAINER_BUNDLE_IDENTIFIER = {HOST_ID}$(DEVELOPMENT_TEAM_SUFFIX)", 1)
    replace(config, "APP_GROUP_SIDESTORE = group.com.SideStore.SideStore$(DEVELOPMENT_TEAM_SUFFIX)",
            f"APP_GROUP_SIDESTORE = group.{HOST_ID}$(DEVELOPMENT_TEAM_SUFFIX)", 1)
    replace(config, "APP_GROUP_ALTSTORE = group.com.rileytestut.AltStore$(DEVELOPMENT_TEAM_SUFFIX)",
            f"APP_GROUP_ALTSTORE = group.{HOST_ID}.shared$(DEVELOPMENT_TEAM_SUFFIX)", 1)
    for path in (root / "LiveContainer/LCSharedUtils.m",):
        replace(path, '@"livecontainer", @"livecontainer2", @"livecontainer3"',
                f'@"{HOST_SCHEME}"', 1)
        replace(path, 'group.com.SideStore.SideStore.', f'group.{HOST_ID}.', 1)
        replace(path, 'group.com.rileytestut.AltStore.', f'group.{HOST_ID}.shared.', 1)
    replace(root / "LiveContainerSwiftUI/Utilities/Shared.swift",
            '"group.com.SideStore.SideStore"', f'"group.{HOST_ID}"', 1)
    # A custom scheme is still a primary host. Upstream otherwise classifies
    # every non-stock scheme as LC2 and disables certificate/signing settings.
    replace(root / "LiveContainerSwiftUI/Utilities/Shared.swift",
            'if LCUtils.appUrlScheme()?.lowercased() != "livecontainer" {',
            f'if LCUtils.appUrlScheme()?.lowercased() != "{HOST_SCHEME}" {{', 1)
    diagnostics = root / "LiveContainerSwiftUI/Views/Settings/LCJITLessDiagnoseView.swift"
    replace(diagnostics, 'fetchedAppGroupId == "group.com.SideStore.SideStore." + teamId',
            f'fetchedAppGroupId == "group.{HOST_ID}." + teamId', 1)
    # Keychain groups must also be isolated from a stock LC signed by the same
    # team. Adapt both the declared groups and the guest-keychain hook.
    for relative in ("entitlements.xml", "LiveProcess/LiveProcess.entitlements",
                     "LiveContainer/Tweaks/SecItem.m",
                     "LiveContainerSwiftUI/Views/Settings/LCJITLessDiagnoseView.swift"):
        replace(root / relative, "com.kdt.livecontainer.shared", HOST_ID + ".shared")
    for relative in ("LiveContainerSwiftUI/Models/LCAppInfo.m",
                     "LiveContainerSwiftUI/Views/AppList/LCAppBanner/LCAppBannerViewController.swift"):
        replace(root / relative, "livecontainer://livecontainer-launch", HOST_SCHEME + "://livecontainer-launch", 2)
    management = root / "LiveContainerSwiftUI/Views/Settings/LCMultiLCManagementView.swift"
    replace(management,
            '    LaunchPriorityLC(scheme: "livecontainer", displayName: "LiveContainer"),\n'
            '    LaunchPriorityLC(scheme: "livecontainer2", displayName: "LiveContainer2"),\n'
            '    LaunchPriorityLC(scheme: "livecontainer3", displayName: "LiveContainer3")',
            f'    LaunchPriorityLC(scheme: "{HOST_SCHEME}", displayName: "LiveContainer + SI")', 1)
    replace(management, 'if scheme == "livecontainer"', f'if scheme == "{HOST_SCHEME}"', 1)

    def host_plist(data):
        data["CFBundleDisplayName"] = "LiveContainer + SI"
        data["CFBundleURLTypes"][0]["CFBundleURLSchemes"] = [HOST_SCHEME]
        data["CFBundleURLTypes"].append({"CFBundleURLName": HOST_ID + ".sideinstaller",
                                       "CFBundleURLSchemes": ["sideinstaller-embedded"]})
        data["LSApplicationQueriesSchemes"] = list(dict.fromkeys(
            data.get("LSApplicationQueriesSchemes", []) + ["localdevvpn", HOST_SCHEME]))
        data["NSSupportsLiveActivities"] = True
        data["SIEmbeddedBuild"] = True
    edit_plist(root / "LiveContainer/Info.plist", host_plist)
    patch_certificate_validation(root, replace)
    (root / ".sideinstaller-adapted").write_text(LIVE_CONTAINER_COMMIT + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkout", type=Path)
    args = parser.parse_args()
    prepare(args.checkout.resolve())
