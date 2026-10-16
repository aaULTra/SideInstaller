"""Checked fixes to upstream's certificate diagnostics; never bypass trust checks."""

def patch(root, replace):
    signer = root / "ZSign/zsign.mm"
    replace(signer, '    // Extract OCSP URL from cert', '''    // Public metadata is available even when the online revocation check fails.
    NSString *organizationalUnitName = nil;
    X509_NAME *subject = X509_get_subject_name(cert);
    int index = X509_NAME_get_index_by_NID(subject, NID_organizationalUnitName, -1);
    if (index >= 0) {
        ASN1_STRING *value = X509_NAME_ENTRY_get_data(X509_NAME_get_entry(subject, index));
        organizationalUnitName = [[NSString alloc] initWithBytes:ASN1_STRING_get0_data(value)
            length:ASN1_STRING_length(value) encoding:NSUTF8StringEncoding];
    }
    struct tm expiry = {};
    NSDate *expirationDate = nil;
    if (ASN1_TIME_to_tm(X509_get0_notAfter(cert), &expiry) == 1) {
        expirationDate = [NSDate dateWithTimeIntervalSince1970:timegm(&expiry)];
    }
    // Extract OCSP URL from cert''', 1)
    old_start = '            ASN1_TIME *expirationDateAsn1 = X509_get_notAfter(cert);'
    old_end = '            formatter.locale = NSLocale.currentLocale;\n            NSDate *expirationDate = [formatter dateFromString:fullDateString];'
    source = signer.read_text()
    start, end = source.index(old_start), source.index(old_end) + len(old_end)
    replace(signer, source[start:end], '', 1)
    old = '    NSMutableURLRequest *request = [NSMutableURLRequest requestWithURL:[NSURL URLWithString:[NSString stringWithUTF8String:(const char *)uri->data]]];'
    replace(signer, old, '''    // ASN.1 strings are length-delimited, not C strings. Decode exactly its bytes.
    NSString *uriString = [[NSString alloc] initWithBytes:ASN1_STRING_get0_data(uri)
        length:ASN1_STRING_length(uri) encoding:NSASCIIStringEncoding];
    NSURL *url = uriString ? [NSURL URLWithString:uriString] : nil;
    if (!url.host.length || !([url.scheme.lowercaseString isEqualToString:@"http"] ||
                             [url.scheme.lowercaseString isEqualToString:@"https"])) {
        OPENSSL_free(der);
        OCSP_REQUEST_free(req);
        OCSP_CERTID_free(cert_id);
        sk_ACCESS_DESCRIPTION_pop_free(aia, ACCESS_DESCRIPTION_free);
        X509_free(issuer);
        BIO_free(brother1);
        completionHandler(2, expirationDate, organizationalUnitName,
                          @"Certificate revocation URL is missing or malformed. Certificate metadata remains readable; revocation status is unknown.");
        return -7;
    }
    NSMutableURLRequest *request = [NSMutableURLRequest requestWithURL:url];
    request.timeoutInterval = 20;''', 1)
    replace(signer, 'completionHandler(2, nil, nil, error.localizedDescription);',
            'OCSP_CERTID_free(cert_id);\n            completionHandler(2, expirationDate, organizationalUnitName, '
            '[NSString stringWithFormat:@"Revocation check failed (%@ %ld): %@. URL: %@", '
            'error.domain, (long)error.code, error.localizedDescription, url.absoluteString]);', 1)
    replace(signer, 'completionHandler(2, nil, nil, @"Failed to decode OCSP response.");',
            'OCSP_CERTID_free(cert_id);\n                completionHandler(2, expirationDate, organizationalUnitName, @"Failed to decode OCSP response.");', 1)
    replace(signer, '            int status, reason;\n            if (OCSP_resp_find_status',
            '            if (!basic) {\n'
            '                OCSP_RESPONSE_free(resp); OCSP_CERTID_free(cert_id);\n'
            '                completionHandler(2, expirationDate, organizationalUnitName, @"No basic OCSP response."); return;\n'
            '            }\n            int status, reason;\n            if (OCSP_resp_find_status', 1)
    replace(signer, 'completionHandler(2, nil, nil, @"Invalid response or no data");',
            'OCSP_CERTID_free(cert_id);\n            completionHandler(2, expirationDate, organizationalUnitName, '
            '[NSString stringWithFormat:@"Revocation server returned HTTP %ld or no response data", (long)httpResponse.statusCode]);', 1)

    diagnose = root / "LiveContainerSwiftUI/Views/Settings/LCJITLessDiagnoseView.swift"
    settings = root / "LiveContainerSwiftUI/Views/Settings/LCSettingsView.swift"
    replace(settings, '    func onSideStoreCertificateCallback(certificateData: Data, password: String) {',
            '    func onSideStoreCertificateCallback(certificateData: Data, password: String) {\n'
            '        guard let team = LCUtils.getCertTeamId(withKeyData: certificateData, password: password) else {\n'
            '            errorInfo = "The shared certificate/private key could not be decoded with its password. The existing certificate was kept."\n'
            '            errorShow = true; return\n'
            '        }\n'
            '        guard team == LCSharedUtils.teamIdentifier() else {\n'
            '            errorInfo = "Shared certificate team: \\(team). Host team: \\(LCSharedUtils.teamIdentifier() ?? "unavailable"). The existing certificate was kept."\n'
            '            errorShow = true; return\n'
            '        }', 1)
    replace(diagnose, '        LCUtils.validateCertificate { status, date, ou, error in\n',
            '        LCUtils.validateCertificate { status, date, ou, error in\n'
            '            if let ou { certTeamId = ou }\n'
            '            if let date {\n'
            '                let formatter = DateFormatter()\n'
            '                formatter.dateStyle = .short; formatter.timeStyle = .medium\n'
            '                certificateValidateUntil = formatter.string(from: date)\n'
            '            }\n', 1)

    macho = root / "LiveContainer/LCMachOUtils.m"
    replace(macho, 'bool checkCodeSignature(const char* path) {',
            'bool checkCodeSignature(const char* path) {\n'
            '    [NSUserDefaults.standardUserDefaults setObject:@"No readable ARM64 code signature found." forKey:@"SILastSignatureError"];', 1)
    replace(macho, '        if ( addFileSigsReault == -1 ) {',
            '        if ( addFileSigsReault == -1 ) {\n'
            '            int savedErrno = errno;\n'
            '            [NSUserDefaults.standardUserDefaults setObject:[NSString stringWithFormat:@"F_ADDFILESIGS_RETURN failed: errno %d (%s)", savedErrno, strerror(savedErrno)] forKey:@"SILastSignatureError"];', 1)
    replace(macho, '        int checkLVresult = fcntl(fd, F_CHECK_LV, &checkInfo);',
            '        int checkLVresult = fcntl(fd, F_CHECK_LV, &checkInfo);\n'
            '        if (checkLVresult != 0) {\n'
            '            int savedErrno = errno;\n'
            '            NSString *detail = [[NSString alloc] initWithBytes:messageBuffer length:strnlen(messageBuffer, sizeof(messageBuffer)) encoding:NSUTF8StringEncoding];\n'
            '            [NSUserDefaults.standardUserDefaults setObject:[NSString stringWithFormat:@"F_CHECK_LV failed: errno %d (%s). %@", savedErrno, strerror(savedErrno), detail ?: @"No library-validation message"] forKey:@"SILastSignatureError"];\n'
            '        }', 1)
    utils = root / "LiveContainerSwiftUI/Utilities/LCUtils.m"
    replace(utils, '@"lc.signer.latestCertificateInvalidErr"',
            '([[@"iOS rejected the signed test library. " stringByAppendingString:[NSUserDefaults.standardUserDefaults stringForKey:@"SILastSignatureError"] ?: @"Unknown signature error"] '
            'stringByAppendingString:@". In the built-in SideInstaller, Share signing certificate reports whether this host profile authorises that exact certificate. If it does not, use Repair host signing."])', 1)
