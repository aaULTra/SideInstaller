#if SIDEINSTALLER_EMBEDDED
import Foundation
import Security
import SwiftUI
import Darwin

/// UI and LiveProcess have different Engine singletons. An OS-held lock also
/// excludes the other process and is automatically released if either dies.
final class EmbeddedRefreshLease {
    private var descriptor: Int32 = -1

    init() throws {
        let directory = URL(fileURLWithPath: NSHomeDirectory()).appendingPathComponent("Library/Caches")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        descriptor = open(directory.appendingPathComponent("SideInstaller-operation.lock").path,
                          O_CREAT | O_RDWR, S_IRUSR | S_IWUSR)
        guard descriptor >= 0 else { throw EngineError.message("Unable to reserve the embedded signing operation.") }
        guard flock(descriptor, LOCK_EX | LOCK_NB) == 0 else {
            let failedDescriptor = descriptor
            descriptor = -1
            close(failedDescriptor)
            throw EngineError.message("SideInstaller is already installing or refreshing in another process.")
        }
    }

    deinit {
        guard descriptor >= 0 else { return }
        flock(descriptor, LOCK_UN)
        close(descriptor)
    }
}

/// ObjC entry point called by LiveContainer's existing LiveProcess transport.
/// The transport does not sign or install anything: this delegates to the same
/// service used by SideInstaller's apps page and standalone App Intent.
@MainActor
@objc(SIEmbeddedBridge)
final class SIEmbeddedBridge: NSObject {
    private static var refreshTask: Task<Void, Never>?

    @objc(refreshAllWithProgress:completion:)
    static func refreshAll(progress: @escaping (Double) -> Void,
                           completion: @escaping (String?, String?) -> Void) {
        guard refreshTask == nil else {
            completion(nil, "Another embedded refresh is already running.")
            return
        }
        refreshTask = Task {
            defer { refreshTask = nil }
            guard UserDefaults.standard.bool(forKey: "hasAcceptedTOS"),
                  UserDefaults.standard.bool(forKey: "hasCompletedAccountSetup") else {
                completion(nil, "Open the built-in SideInstaller and complete its initial setup first.")
                return
            }
            var needsAuthentication = false
            let authenticationMonitor = Task { @MainActor in
                guard SIEmbeddedIsLiveProcess() else { return }
                while !Task.isCancelled {
                    if Engine.shared.twoFactor != nil {
                        needsAuthentication = true
                        Engine.shared.cancelTwoFactor()
                        return
                    }
                    do { try await Task.sleep(for: .milliseconds(200)) } catch { return }
                }
            }
            defer { authenticationMonitor.cancel() }
            do {
                let result = try await AppRefreshService.shared.refreshAll { jobs, _ in
                    let completed = jobs.filter {
                        switch $0.state {
                        case .done, .failed: return true
                        default: return false
                        }
                    }.count
                    progress(Double(completed) / Double(max(1, jobs.count)))
                }
                progress(1)
                completion(result.dialog, nil)
            } catch {
                completion(nil, needsAuthentication
                    ? "Apple requires two-factor authentication. Open the built-in SideInstaller, sign in there, then retry the Shortcut."
                    : AppRefreshService.text(for: error))
            }
        }
    }

    @objc(cancelRefresh)
    static func cancelRefresh() { refreshTask?.cancel() }

    /// Keychain hand-off stays within this combined app's access groups. Apple
    /// account passwords and developer-session tokens are never handed to LC.
    static func storeCertificate(config: String, signingTeam: String?) throws -> String {
        guard let json = try JSONSerialization.jsonObject(with: Data(config.utf8)) as? [String: Any],
              let encoded = json["certificateData"] as? String,
              let certificate = Data(base64Encoded: encoded),
              let password = json["certificatePassword"] as? String else {
            throw EngineError.message("SideInstaller could not export its signing certificate.")
        }
        let report = try EmbeddedSigningIdentity.report(p12: certificate, password: password, team: signingTeam)
        for (account, data) in [("signingCertificate", certificate),
                                ("signingCertificatePassword", Data(password.utf8))] {
            let query: [String: Any] = [
                kSecClass as String: kSecClassGenericPassword,
                kSecAttrService as String: "com.aault.livecontainer.sideinstaller",
                kSecAttrAccount as String: account,
            ]
            let status = SecItemUpdate(query as CFDictionary,
                                      [kSecValueData as String: data] as CFDictionary)
            if status == errSecItemNotFound {
                var add = query
                add[kSecValueData as String] = data
                add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlock
                let added = SecItemAdd(add as CFDictionary, nil)
                guard added == errSecSuccess else {
                    throw EngineError.message("Certificate Keychain write failed (\(added)).")
                }
            } else if status != errSecSuccess {
                throw EngineError.message("Certificate Keychain update failed (\(status)).")
            }
        }
        return "Certificate shared. Return to LiveContainer → Settings → Import Certificate from SideInstaller.\n\n" + report
    }
}

struct EmbeddedLiveContainerBar: View {
    @EnvironmentObject private var engine: Engine
    @State private var working = false
    @State private var message: String?
    @State private var confirmRepair = false

    var body: some View {
        HStack {
            Button("Back to LiveContainer", systemImage: "arrow.uturn.backward") {
                SIEmbeddedReturnToHost()
            }
            .disabled(engine.isRunning || AppRefreshService.shared.isRefreshing || working)
            Spacer()
            Button(working ? "Sharing…" : "Share signing certificate") {
                working = true
                Task { @MainActor in
                    defer { working = false }
                    do {
                        message = try await engine.shareCertificateWithLiveContainer()
                    } catch { message = AppRefreshService.text(for: error) }
                }
            }
            .disabled(working || engine.isRunning || AppRefreshService.shared.isRefreshing)
            Button("Repair host signing") { confirmRepair = true }
                .disabled(working || engine.isRunning || AppRefreshService.shared.isRefreshing)
        }
        .font(.footnote)
        .padding()
        .background(.regularMaterial)
        .confirmationDialog("Re-sign this LiveContainer with SideInstaller's identity?", isPresented: $confirmRepair,
                            titleVisibility: .visible) {
            Button("Repair host signing") {
                working = true
                Task { @MainActor in
                    defer { working = false }
                    do {
                        try await engine.repairEmbeddedHostSigning()
                        message = "Host replacement reported completion. Close and reopen LiveContainer, import the shared certificate and test JIT-less mode."
                    } catch { message = AppRefreshService.text(for: error) }
                }
            }
        } message: {
            Text("Uses the exact original IPA from your library and the same installed bundle ID/team. Replacing this running app may close it before confirmation. Reopen and verify before retrying. Back up important guest data first.")
        }
        .alert("LiveContainer + SideInstaller", isPresented: Binding(
            get: { message != nil }, set: { if !$0 { message = nil } })) {
                Button("OK") { message = nil }
            } message: { Text(message ?? "") }
    }
}
#endif
