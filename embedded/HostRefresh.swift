// Compiled in LiveContainer's existing SideStoreSupport target. The target's
// name is retained as an upstream build detail; no SideStore app is embedded.
import AppIntents
import Foundation
import UserNotifications

@available(iOS 17.0, *)
public struct RefreshAllAppsIntent: AppIntent, ProgressReportingIntent, ForegroundContinuableIntent {
    public static var title: LocalizedStringResource = "Refresh All Apps"
    public static var description = IntentDescription("Refreshes apps using the built-in SideInstaller.")
    public static var openAppWhenRun = false
    @MainActor private static var running = false
    public init() {}

    private enum Event: Sendable { case finished(String), deadline }

    @MainActor
    public func perform() async throws -> some IntentResult & ProvidesDialog {
        guard !Self.running else {
            throw NSError(domain: "EmbeddedSideInstaller", code: 1,
                          userInfo: [NSLocalizedDescriptionKey: "Another embedded refresh is already running."])
        }
        Self.running = true
        defer { Self.running = false }
        let handler = RefreshHandler()
        let intentProgress = progress
        intentProgress.totalUnitCount = 1000
        let deadline = ContinuousClock.now + .seconds(27)
        let summary = try await withThrowingTaskGroup(of: Event.self) { group in
            group.addTask { @MainActor in
                .finished(try await handler.refresh(progress: intentProgress))
            }
            group.addTask {
                try await Task.sleep(until: deadline, clock: .continuous)
                return .deadline
            }
            defer { group.cancelAll() }
            while let event = try await group.next() {
                switch event {
                case .finished(let message):
                    try Task.checkCancellation()
                    return message
                case .deadline:
                    try await requestToContinueInForeground("SideInstaller needs more time to finish refreshing.")
                }
            }
            throw CancellationError()
        }
        return .result(dialog: "\(summary)")
    }
}

@available(iOS 17.0, *)
public struct EmbeddedSideInstallerShortcuts: AppShortcutsProvider {
    public static var appShortcuts: [AppShortcut] {
        AppShortcut(intent: RefreshAllAppsIntent(), phrases: [
            "Refresh \(.applicationName)", "Refresh apps with \(.applicationName)"
        ], shortTitle: "Refresh All Apps", systemImageName: "arrow.triangle.2.circlepath")
    }
}

@MainActor
final class RefreshHandler: NSObject, RefreshServer {
    private var active = false
    private var ready = false
    private var progress: Progress?
    private var completion: CheckedContinuation<String, Error>?
    private var startup: CheckedContinuation<Void, Error>?
    private var listener: NSXPCListener?
    private var connection: NSXPCConnection?
    private var client: RefreshClient?
    private var ext: NSExtension?
    private var launchTask: Task<Void, Never>?
    private var timeoutTask: Task<Void, Never>?

    private func error(_ message: String) -> NSError {
        NSError(domain: "EmbeddedSideInstaller", code: 1,
                userInfo: [NSLocalizedDescriptionKey: message])
    }

    func refresh(progress: Progress) async throws -> String {
        guard !active else { throw error("Another embedded refresh is already running.") }
        active = true
        self.progress = progress
        defer {
            active = false
            self.progress = nil
            timeoutTask?.cancel()
            launchTask?.cancel()
            timeoutTask = nil
            launchTask = nil
            listener?.invalidate()
            listener = nil
            connection?.invalidate()
            connection = nil
            client = nil
            ready = false
            // The extension can be released after its final response, never
            // halfway through installing an app.
            ext = nil
        }
        return try await withTaskCancellationHandler {
            try Task.checkCancellation()
            if !UserDefaults.isSideStore() { try await startProcess() }
            try Task.checkCancellation()
            let summary = try await withCheckedThrowingContinuation { continuation in
                completion = continuation // register BEFORE sending the request
                if UserDefaults.isSideStore() {
                    SideStoreClient.shared.startLocalRefresh(self)
                } else if let client {
                    client.refreshAllApps(withIdentifier: "RefreshAllAppsIntent", mangledTypeName: "")
                } else {
                    completion = nil
                    continuation.resume(throwing: error("SideInstaller's refresh connection is unavailable."))
                }
            }
            try Task.checkCancellation()
            return summary
        } onCancel: {
            DispatchQueue.main.async { self.cancel() }
        }
    }

    private func startProcess() async throws {
        guard let home = getenv("LC_HOME_PATH") else { throw error("LiveContainer home is unavailable.") }
        let directory = URL(fileURLWithPath: String(cString: home))
            .appendingPathComponent("Documents/SideInstaller", isDirectory: true)
        try FileManager.default.createDirectory(at: directory.appendingPathComponent("Documents"),
                                                withIntermediateDirectories: true)
        guard let bookmark = bookmarkForURL(directory) else {
            throw error("Unable to access the embedded SideInstaller's data.")
        }
        guard let listener = startAnonymousListener(self) else {
            throw error("Unable to create the embedded refresh listener.")
        }
        self.listener = listener
        guard let url = UserDefaults.lcMainBundle().builtInPlugInsURL?.appendingPathComponent("LiveProcess.appex"),
              let bundle = Bundle(url: url), let id = bundle.bundleIdentifier else {
            throw error("LiveProcess is missing. Reinstall this combined IPA with all app extensions retained.")
        }
        let process = try NSExtension(identifier: id)
        ext = process
        process.setRequestInterruptionBlock { [weak self] _ in
            DispatchQueue.main.async {
                guard let self, self.active else { return }
                self.abort(self.error("Embedded SideInstaller stopped before reporting completion. Reopen it and verify the installed profiles; an in-progress self-refresh may have replaced the host."))
            }
        }
        let item = NSExtensionItem()
        item.userInfo = ["selected": "builtinSideStore", "bookmarks": [bookmark], "endpoint": listener.endpoint]
        try await withCheckedThrowingContinuation { continuation in
            startup = continuation
            launchTask = Task { @MainActor in _ = await process.beginRequest(withInputItems: [item]) }
            timeoutTask = Task { @MainActor in
                do { try await Task.sleep(for: .seconds(20)) } catch { return }
                guard self.startup != nil else { return }
                self.abort(self.error("Embedded SideInstaller did not start within 20 seconds."))
                process._kill(9) // startup only: no signing/install has begun
            }
        }
        guard ready, client != nil else { throw error("Embedded SideInstaller did not establish its refresh connection.") }
    }

    private func cancel() {
        if startup != nil {
            abort(CancellationError())
            ext?._kill(9) // only while waiting for launch, before work is sent
        } else if UserDefaults.isSideStore() {
            SideStoreClient.shared.cancelRefresh()
        } else {
            client?.cancelRefresh()
        }
    }

    private func abort(_ error: Error) {
        startup?.resume(throwing: error)
        startup = nil
        completion?.resume(throwing: error)
        completion = nil
    }

    nonisolated func onConnection(_ connection: NSXPCConnection!) {
        connection.remoteObjectInterface = NSXPCInterface(with: RefreshClient.self)
        let proxy = connection.remoteObjectProxy as? RefreshClient
        Task { @MainActor in
            self.connection = connection
            self.client = proxy
            self.completeStartupIfReady()
        }
    }

    nonisolated func finishedLaunching() {
        Task { @MainActor in
            self.ready = true
            self.completeStartupIfReady()
        }
    }

    private func completeStartupIfReady() {
        guard ready, client != nil else { return }
        startup?.resume()
        startup = nil
        timeoutTask?.cancel()
    }

    nonisolated func updateProgress(_ value: Double) {
        Task { @MainActor in self.progress?.completedUnitCount = Int64(max(0, min(1, value)) * 1000) }
    }

    nonisolated func finish(_ error: String!) {
        finish(withSummary: nil, error: error)
    }

    nonisolated func finish(withSummary summary: String?, error message: String?) {
        Task { @MainActor in
            guard let continuation = self.completion else { return }
            self.completion = nil
            if let message { continuation.resume(throwing: self.error(message)) }
            else { continuation.resume(returning: summary ?? "Refresh completed.") }
        }
    }

    nonisolated func add(_ request: UNNotificationRequest!) {
        guard let request else { return }
        UNUserNotificationCenter.current().add(request, withCompletionHandler: nil)
    }

    nonisolated func removePendingNotificationRequests(withIdentifiers identifiers: [String]!) {
        UNUserNotificationCenter.current().removePendingNotificationRequests(withIdentifiers: identifiers ?? [])
    }
}
