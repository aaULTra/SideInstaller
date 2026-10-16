import importlib.util
import json
import plistlib
import struct
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("embedded_package", Path(__file__).with_name("package.py"))
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = Path(self.temp.name) / "LiveContainer.app"
        self.app.mkdir()
        (self.app / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": package.HOST_ID}))
        guest = self.app / "Frameworks/SideInstallerApp.framework"
        guest.mkdir(parents=True)
        (guest / "SideInstaller").write_bytes(struct.pack("<IIII", 0xfeedfacf, 0x100000c, 0, 6))
        process = self.app / "PlugIns/LiveProcess.appex"
        process.mkdir(parents=True)
        (process / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": package.HOST_ID + ".LiveProcess"}))
        metadata = self.app / "Metadata.appintents"
        metadata.mkdir()
        self.actions = {"actions": {"RefreshAllAppsIntent": {
            "isDiscoverable": True, "openAppWhenRun": False,
            "fullyQualifiedTypeName": "SideStoreSupport.RefreshAllAppsIntent",
            "systemProtocols": ["com.apple.link.systemProtocol.ForegroundContinuable",
                                "com.apple.link.systemProtocol.ProgressReporting"],
        }}, "autoShortcuts": [{"actionIdentifier": "RefreshAllAppsIntent"}]}
        self.write_actions()

    def write_actions(self):
        (self.app / "Metadata.appintents/extract.actionsdata").write_text(json.dumps(self.actions))

    def test_valid_layout(self):
        self.assertTrue(package.verify(self.app)["nativeRefreshIntent"])

    def test_stock_identity_is_rejected(self):
        (self.app / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": "com.kdt.livecontainer"}))
        with self.assertRaises(AssertionError):
            package.verify(self.app)

    def test_side_store_app_must_not_be_shipped(self):
        (self.app / "Frameworks/SideStoreApp.framework").mkdir()
        with self.assertRaises(AssertionError):
            package.verify(self.app)

    def test_guest_executable_must_be_converted(self):
        path = self.app / "Frameworks/SideInstallerApp.framework/SideInstaller"
        path.write_bytes(struct.pack("<IIII", 0xfeedfacf, 0x100000c, 0, 2))
        with self.assertRaises(AssertionError):
            package.verify(self.app)

    def test_native_action_must_remain_discoverable(self):
        self.actions["actions"]["RefreshAllAppsIntent"]["isDiscoverable"] = False
        self.write_actions()
        with self.assertRaises(AssertionError):
            package.verify(self.app)

    def test_nested_extensions_are_rejected(self):
        (self.app / "Frameworks/SideInstallerApp.framework/PlugIns").mkdir()
        with self.assertRaises(AssertionError):
            package.verify(self.app)

    def test_wrong_extension_identity_is_rejected(self):
        (self.app / "PlugIns/LiveProcess.appex/Info.plist").write_bytes(
            plistlib.dumps({"CFBundleIdentifier": "com.kdt.livecontainer.LiveProcess"}))
        with self.assertRaises(AssertionError):
            package.verify(self.app)

    def test_entitlement_templates_resolve_to_isolated_groups(self):
        path = Path(self.temp.name) / "template.plist"
        path.write_bytes(plistlib.dumps({
            "application-identifier": "$(DEVELOPMENT_TEAM).$(PRODUCT_BUNDLE_IDENTIFIER)",
            "com.apple.security.application-groups": ["$(APP_GROUP_SIDESTORE)", "$(APP_GROUP_ALTSTORE)"],
            "keychain-access-groups": ["$(AppIdentifierPrefix)" + package.HOST_ID + ".shared"],
        }))
        result = package.entitlement_template(path, package.HOST_ID)
        self.assertEqual(result["com.apple.security.application-groups"][0], "group." + package.HOST_ID)
        self.assertNotIn("$(", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
