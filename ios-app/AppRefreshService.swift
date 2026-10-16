import Foundation

/// Shared by the apps page and Shortcuts. Pending jobs are apps left untouched
/// when cancellation is requested between installs.
struct RefreshAllResult: Sendable {
    let jobs: [RefreshJob]
    let cancelled: Bool

    var total: Int { jobs.count }
    var refreshed: Int { jobs.filter { $0.state == .done }.count }
    var failed: Int {
        jobs.filter { if case .failed = $0.state { return true }; return false }.count
    }
    var skipped: Int { jobs.filter { $0.state == .pending }.count }
    var errors: [String] {
        jobs.compactMap {
            guard case .failed(let error) = $0.state else { return nil }
            return "\($0.name): \(error)"
        }
    }

    var dialog: String {
        guard total > 0 else {
            return L("No refreshable apps found. Keep the original IPAs in SideInstaller’s IPA library.")
        }
        var message = L("Refreshed %d of %d apps.", refreshed, total)
        if failed > 0 { message += " " + L("%d failed.", failed) }
        if skipped > 0 { message += " " + L("%d left unchanged.", skipped) }
        if cancelled { message += " " + L("Refresh cancelled.") }
        if !errors.isEmpty { message += "\n" + errors.joined(separator: "\n") }
        return message
    }
}

/// Owns refresh execution for every caller, without requiring a view to load.
/// Its reservation covers discovery, sign-in and every install, including
/// awaits, so a second refresh or the normal install cannot start in between.
@MainActor
final class AppRefreshService {
    static let shared = AppRefreshService()
    private(set) var isRefreshing = false
    private init() {}

    typealias Update = @MainActor ([RefreshJob], String) -> Void

    struct Inventory {
        let entries: [SideloadedAppStatus]
        let unmatched: [ProvisioningProfile]
        let jobs: [RefreshJob]
    }

    static func inventory(using engine: Engine) async throws -> Inventory {
        let inventory = try await engine.sideloadedAppInventory()
        try Task.checkCancellation()
        let apps = inventory.apps.compactMap(SideloadedApp.init(plist:))
        let profiles = inventory.profiles.compactMap(ProvisioningProfile.init(data:))
        let matched = ProfileMatcher.match(apps: apps, profiles: profiles)
        let jobs = await refreshJobs(for: matched.entries)
        try Task.checkCancellation()
        engine.log("Sideloaded apps: \(apps.count) sideloaded, \(profiles.count) profile(s) decoded.")
        engine.log("Sideloaded apps: \(jobs.count) of \(matched.entries.count) can be refreshed from an IPA on disk.")
        return Inventory(entries: matched.entries, unmatched: matched.unmatched, jobs: jobs)
    }

    func withExclusiveOperation<T>(_ operation: @MainActor () async throws -> T) async throws -> T {
        let engine = Engine.shared
        guard !isRefreshing, !engine.isRunning else {
            throw EngineError.message(L("An install or refresh is already running. Wait for it to finish, then refresh."))
        }
        isRefreshing = true
        defer { isRefreshing = false }
        #if SIDEINSTALLER_EMBEDDED
        let lease = try EmbeddedRefreshLease()
        defer { withExtendedLifetime(lease) {} }
        #endif
        return try await operation()
    }

    func refreshAll(update: Update = { _, _ in }) async throws -> RefreshAllResult {
        try await withExclusiveOperation { try await performRefreshAll(update: update) }
    }

    private func performRefreshAll(update: Update) async throws -> RefreshAllResult {
        let engine = Engine.shared
        try Task.checkCancellation()
        update([], L("Getting ready"))

        // Always discover afresh, even when this launch has never opened AppsView.
        var jobs = try await Self.inventory(using: engine).jobs
        update(jobs, L("Getting ready"))
        guard !jobs.isEmpty else { return RefreshAllResult(jobs: [], cancelled: false) }
        engine.log("=== Refresh all: \(jobs.count) app(s) ===")
        try Task.checkCancellation()
        try await engine.prepareRefresh()

        for index in jobs.indices {
            // The engine's signing/install operations finish at a safe point.
            if Task.isCancelled { break }
            let job = jobs[index]
            if let team = engine.signingTeamID, let installed = job.teamID, installed != team {
                jobs[index].state = .failed(
                    L("Signed by team %@, not the one you're signed in as — refreshing it here would install a second copy.", installed))
                update(jobs, L("Getting ready"))
                continue
            }
            jobs[index].state = .working
            update(jobs, L("Refreshing %@", job.name))
            do {
                try await engine.refreshInstalledApp(named: job.name, ipaPath: job.ipa.path)
                jobs[index].state = .done
            } catch {
                let message = Self.text(for: error)
                jobs[index].state = .failed(message)
                engine.log("⛔️ Refresh \(job.name): \(message)")
            }
            update(jobs, L("Getting ready"))
        }
        let result = RefreshAllResult(jobs: jobs, cancelled: Task.isCancelled)
        engine.log("Refresh all finished: \(result.refreshed)/\(result.total) refreshed.")
        return result
    }

    static func text(for error: Error) -> String {
        (error as? LocalizedError)?.errorDescription ?? String(describing: error)
    }

    private struct InstalledRef: Sendable {
        let bundleID: String
        let name: String
        let teamID: String?
    }

    private static func refreshJobs(for entries: [SideloadedAppStatus]) async -> [RefreshJob] {
        let installed = entries.map {
            InstalledRef(bundleID: $0.app.bundleID, name: $0.app.name,
                         teamID: $0.current?.teamIdentifier)
        }
        return await Task.detached(priority: .userInitiated) {
            pair(installed, with: IPALibrary.installable())
        }.value
    }

    private nonisolated static func pair(
        _ installed: [InstalledRef],
        with library: [(entry: IPALibrary.Entry, info: IPALibrary.AppInfo)]
    ) -> [RefreshJob] {
        installed.compactMap { app in
            guard let match = library.first(where: {
                ProfileMatcher.installed(app.bundleID, isBuiltFrom: $0.info.bundleID,
                                         teamID: app.teamID)
            }) else { return nil }
            return RefreshJob(bundleID: app.bundleID, name: app.name,
                              ipa: match.entry.url, teamID: app.teamID)
        }
    }
}
