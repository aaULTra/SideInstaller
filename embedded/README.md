# LiveContainer + SideInstaller (experimental embedded flavour)

This is a **personal evaluation build**, not an official LiveContainer or
SideInstaller release. SideInstaller by FrizzleM:
<https://github.com/FrizzleM/SideInstaller>. LiveContainer:
<https://github.com/LiveContainer/LiveContainer>. Their licenses and copyright
notices remain in force. Do not publish or commercially redistribute the
combined binary without resolving the upstream licensing requirements.

## Architecture

The build pins LiveContainer to `4dbe0f9a626de801184a42c0be8d2cb105058e3d`.
It adapts the existing embedded-SideStore loader, converts the **locally built**
SideInstaller executable with upstream's `dylibify` tool, and embeds it as
`Frameworks/SideInstallerApp.framework`. It does not include a SideStore app.

Upstream's internal `builtinSideStore` selector, `isSideStore` flag,
`SideStoreSupport` target and some filenames are intentionally retained to
minimize changes to the loader. They denote the **built-in sideloader slot** in
this build. They do not select SideStore's signing or installation code.

The original LiveProcess/XPC transport starts SideInstaller for the host's
native Refresh All Apps action. `SIEmbeddedBridge` invokes the existing
`AppRefreshService` and Engine. Progress, result dialogs and cancellation cross
that transport. Missing listeners, launch timeouts and process interruption
produce errors rather than unresumed continuations. No private App Intent
runner is used for execution. Background 2FA fails with an instruction to sign
in in the visible embedded UI, rather than waiting for an invisible sheet.

`EmbeddedRefreshLease` uses an OS-held file lock in the shared SideInstaller
home, so the UI and LiveProcess cannot install/refresh concurrently. A dead
process automatically releases the lock.

## Isolated identity and data

- Host: `com.aault.livecontainer.sideinstaller` (your sideloader may append its team).
- URL schemes: `livecontainer-sideinstaller`, `sideinstaller-embedded`.
- Embedded home: host `Documents/SideInstaller`.
- Guest IPA library: host `Documents/SideInstaller/Documents`.
- Separate app groups and certificate Keychain service.

This does **not** replace stock LiveContainer or move its existing guest data.
Do not change the bundle ID to overwrite your real installation as a shortcut
to migration. Back up guest data before any future migration experiment.

The custom URL scheme is registered as a **primary** LiveContainer in the
host's mode detection. It must not select LC2: that mode hides primary-host
certificate/signing settings. An older experimental build misclassified this
scheme; update the same app (same signing team/bundle ID) to correct it.

If JIT-less diagnostics report incorrect keychain groups after sideloading,
copy the installed entitlement plist from **LiveContainer Entitlements** and
**LiveProcess Entitlements** before changing signing settings. The unsigned
IPA's templates do not prove which groups the installer actually retained.
Do not revoke certificates or change identities to try to fix that check.

## First device test

1. Download `LiveContainer-SideInstaller-experimental` from the successful
   **Build LiveContainer + SideInstaller** run. Sign its unsigned IPA using your
   own account. **Keep every app extension**, especially LiveProcess.
2. Confirm it installs alongside your current LiveContainer. Open its built-in
   SideInstaller using the sideloader icon at the top left of the Apps page.
3. Accept SideInstaller's terms, add your Apple account, and import/generate the
   device pairing file. Connect LocalDevVPN as with standalone SideInstaller.
4. Start with a disposable test app. Keep its original IPA in the embedded
   library, install it, refresh it, and verify its data and expiry afterwards.
5. Tap **Share signing certificate**, return with **Back to LiveContainer**, then
   open LC Settings → **Import Certificate from SideInstaller**. The signed-in
   team must match the host's team. The hand-off contains an encrypted PKCS#12
   identity and its password, not the Apple account password or session token.
6. In Shortcuts use this combined app's **Refresh All Apps** action. Test with
   the combined app closed, missing VPN, cancellation, and a failing test app.

## Certificate mismatch / host repair

**Share signing certificate** now reads the selected team from the actual
signing session and compares it with the installed host's profile. Missing
host metadata, unavailable teams and different teams produce distinct errors
with the public team IDs. It also compares the exported leaf certificate's
DER bytes with the host profile's `DeveloperCertificates` and shows a SHA-256
fingerprint. Same team alone does not mean the certificate is authorised.

If it reports a certificate mismatch, import **this exact updated build's
original unsigned combined IPA** with Custom .ipa, then tap **Repair host
signing**. A confirmation explains that the running app may close. Repair
uses the existing `prepareRefresh` / `refreshInstalledApp` signing and tunnel
installation pipeline; it does not revoke/create a certificate itself.

Repair refuses a different source revision, bundle ID, team, missing embedded
app/LiveProcess, a new profile that does not authorise the signing certificate,
or signatures lacking development/app-group/128-keychain-group entitlements.
The signer requests the custom host's isolated keychain groups only for that
marked bundle. Other apps retain their existing signing behaviour.

After replacement, reopen the host, import the shared certificate, and test
JIT-less mode. Do not uninstall: a successful same-ID update is intended to
preserve data. Back up important guest data before testing this experimental
repair. Host replacement, extension behaviour and data preservation still
need hardware verification. If SideStore later refreshes the host with a
different identity it can reintroduce the mismatch.

Certificate diagnostics now decode the revocation URL using its ASN.1 byte
length and preserve the locally decoded team/expiry on network failures.
Revocation failures stay **Unknown**, never Valid. JIT-less signature errors
include the failing kernel-validation stage, errno and available message.

## Deliberate limits / tests still required

- A successful build and metadata/layout verification are **not a device test**.
  Native guest launch, JIT-less signing, imported app launch, IPC, signing,
  foreground continuation and app-data preservation require real hardware.
- Existing standalone SideInstaller credentials, pairing files, private keys,
  and existing LiveContainer guests are **not automatically migrated**.
- A certificate belonging to another installation can trigger Apple's existing
  certificate-conflict error. No certificate is automatically revoked. Resolve
  identity access deliberately; revocation can break existing apps.
- The certificate export never creates a new certificate: first sign an app
  using the matching team/private key. Refreshing the host with that identity
  may be needed before its profile permits JIT-less guest signatures.
- For a self-refresh test, manually put the original **combined IPA** in the
  embedded library. Replacing the host can terminate its running extension;
  an interrupted response means **unknown**, not proof of install failure.
  Reopen the app and verify the installed profile before retrying.
- The 27-second foreground request does not grant unlimited background time.
  Apple 2FA may require opening the embedded UI and retrying afterwards.
- No unattended scheduling, persistent health-monitoring system, or automatic
  retries are added by this prototype.

## Build

The dedicated workflow builds the native SideInstaller target with
`SIDEINSTALLER_EMBEDDED` and `embedded/Runtime.h`, builds the adapted LC host,
then runs `embedded/package.py`. The normal standalone target has no embedded
dependency and remains buildable without that flag/header.

The unsigned IPA carries ad-hoc entitlement templates with upstream's dummy
team placeholder, not a real signing identity. The installer must replace
that placeholder with your team and preserve the requested app/keychain
groups and extensions. The packager verifies these signing requests after
embedding; it does not grant capabilities your profile does not allow.

Run packaging regression checks with:

```sh
python3 -m unittest discover -s embedded -p 'test_*.py' -v
```

`verification.json` is included beside the unsigned IPA and build logs. It
checks isolated bundle identities, extension placement, dylib conversion, no
embedded SideStore app, and the host's discoverable progress-reporting refresh
intent with foreground continuation.
