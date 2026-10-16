#if SIDEINSTALLER_EMBEDDED
import Foundation
import Security
import CryptoKit

/// Inspects public certificate/profile data only. Never exports a private key.
enum EmbeddedSigningIdentity {
    static let baseID = "com.aault.livecontainer.sideinstaller"

    static func profile(at bundle: Bundle) throws -> [String: Any] {
        let data = try Data(contentsOf: bundle.bundleURL.appendingPathComponent("embedded.mobileprovision"))
        return try decodeProfile(data)
    }

    static func decodeProfile(_ data: Data) throws -> [String: Any] {
        guard let start = data.range(of: Data("<?xml".utf8)),
              let end = data.range(of: Data("</plist>".utf8), in: start.lowerBound..<data.endIndex),
              let dict = try PropertyListSerialization.propertyList(
                from: data[start.lowerBound..<end.upperBound], format: nil) as? [String: Any] else {
            throw EngineError.message("The host provisioning profile could not be decoded. No signing identity was changed.")
        }
        return dict
    }

    static func host() throws -> (bundle: Bundle, profile: [String: Any], team: String, id: String) {
        guard let bundle = SIEmbeddedHostBundle(), let id = bundle.bundleIdentifier else {
            throw EngineError.message("Unable to locate the installed LiveContainer host.")
        }
        let profile = try self.profile(at: bundle)
        let entitlements = profile["Entitlements"] as? [String: Any] ?? [:]
        guard let team = entitlements["com.apple.developer.team-identifier"] as? String,
              entitlements["application-identifier"] as? String == team + "." + id,
              id == baseID + "." + team else {
            throw EngineError.message("The installed host's bundle ID/team/profile do not match this experimental build. Repair was stopped to avoid installing a second copy.")
        }
        if let runtimeTeam = SIEmbeddedHostTeam(), runtimeTeam != team {
            throw EngineError.message("Host profile team \(team) differs from process team \(runtimeTeam). Repair was stopped.")
        }
        return (bundle, profile, team, id)
    }

    static func certificate(p12: Data, password: String) throws -> Data {
        var imported: CFArray?
        let status = SecPKCS12Import(p12 as CFData,
            [kSecImportExportPassphrase as String: password] as CFDictionary, &imported)
        guard status == errSecSuccess,
              let items = imported as? [[String: Any]], let item = items.first,
              let identity = item[kSecImportItemIdentity as String] else {
            throw EngineError.message("Unable to read the shared PKCS#12 identity (Security status \(status)).")
        }
        var cert: SecCertificate?
        let certStatus = SecIdentityCopyCertificate(identity as! SecIdentity, &cert)
        guard certStatus == errSecSuccess, let cert else {
            throw EngineError.message("The shared identity has no readable signing certificate (\(certStatus)).")
        }
        return SecCertificateCopyData(cert) as Data
    }

    static func permits(_ certificate: Data, profile: [String: Any]) -> Bool {
        (profile["DeveloperCertificates"] as? [Data] ?? []).contains(certificate)
    }

    static func fingerprint(_ certificate: Data) -> String {
        SHA256.hash(data: certificate).map { String(format: "%02x", $0) }.joined()
    }

    static func report(p12: Data, password: String, team: String?) throws -> String {
        let host = try host()
        guard let team else {
            throw EngineError.message("SideInstaller's selected team is unavailable. Host team: \(host.team). Sign in again; no certificate was replaced.")
        }
        guard team == host.team else {
            throw EngineError.message("Team mismatch: SideInstaller \(team); installed LiveContainer \(host.team). Use the account/team that signed this host. No certificate was replaced.")
        }
        let certificate = try certificate(p12: p12, password: password)
        let permitted = permits(certificate, profile: host.profile)
        let expired = (host.profile["ExpirationDate"] as? Date).map { $0 <= Date() } ?? true
        let result = permitted
            ? "The shared certificate is authorised by the installed host profile."
            : "CERTIFICATE MISMATCH: the installed host profile does not authorise SideInstaller's certificate. Sharing/importing alone cannot fix JIT-less mode. Use Repair host signing with this build's original IPA."
        return "\(result)\nHost team: \(host.team)\nCertificate SHA-256: \(fingerprint(certificate))\nHost profile: \(expired ? "expired or unreadable expiry" : "within its expiry date"). Revocation status is a separate online check."
    }
}
#endif
