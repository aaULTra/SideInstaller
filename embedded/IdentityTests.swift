import Foundation

enum EngineError: Error { case message(String) }
func SIEmbeddedHostBundle() -> Bundle? { nil }
func SIEmbeddedHostTeam() -> String? { nil }

@main
struct IdentityTests {
    static func main() throws {
        // Same team is not enough: only exact DER membership authorises the identity.
        let installed = Data([1, 2, 3]), other = Data([1, 2, 4])
        let profile: [String: Any] = ["DeveloperCertificates": [installed],
                                     "TeamIdentifier": ["ABCDEFGHIJ"]]
        precondition(EmbeddedSigningIdentity.permits(installed, profile: profile))
        precondition(!EmbeddedSigningIdentity.permits(other, profile: profile))
        precondition(!EmbeddedSigningIdentity.permits(installed, profile: [:]))
        let xml = try PropertyListSerialization.data(fromPropertyList: profile, format: .xml, options: 0)
        var cmsLike = Data([0x30, 0x82, 0x01, 0x00])
        cmsLike.append(xml); cmsLike.append(Data([0, 1]))
        let decoded = try EmbeddedSigningIdentity.decodeProfile(cmsLike)
        precondition(EmbeddedSigningIdentity.permits(installed, profile: decoded))
        do {
            _ = try EmbeddedSigningIdentity.decodeProfile(Data([0, 1]))
            fatalError("Malformed profile accepted")
        } catch {}
        do {
            _ = try EmbeddedSigningIdentity.certificate(p12: Data([0, 1]), password: "invalid")
            fatalError("Invalid PKCS#12 accepted")
        } catch {}
        precondition(EmbeddedSigningIdentity.fingerprint(Data("abc".utf8)) ==
                     "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
        print("Certificate binding, malformed input and fingerprint checks passed")
    }
}
