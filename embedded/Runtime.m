#import "Runtime.h"
#import <objc/message.h>

BOOL SIEmbeddedIsLiveProcess(void) {
    SEL selector = NSSelectorFromString(@"isLiveProcess");
    return [NSUserDefaults respondsToSelector:selector]
        && ((BOOL (*)(id, SEL))objc_msgSend)(NSUserDefaults.class, selector);
}

NSString *SIEmbeddedHostTeam(void) {
    Class cls = NSClassFromString(@"LCSharedUtils");
    SEL selector = NSSelectorFromString(@"teamIdentifier");
    if (![cls respondsToSelector:selector]) return nil;
    return ((NSString *(*)(id, SEL))objc_msgSend)(cls, selector);
}

NSBundle *SIEmbeddedHostBundle(void) {
    SEL selector = NSSelectorFromString(@"lcMainBundle");
    if (![NSUserDefaults respondsToSelector:selector]) return nil;
    NSBundle *bundle = ((NSBundle *(*)(id, SEL))objc_msgSend)(NSUserDefaults.class, selector);
    if (SIEmbeddedIsLiveProcess()) {
        bundle = [NSBundle bundleWithURL:bundle.bundleURL.URLByDeletingLastPathComponent.URLByDeletingLastPathComponent];
    }
    return bundle;
}

NSString *SIEmbeddedEntitlementsAtPath(NSString *path) {
    Class cls = NSClassFromString(@"LCSharedUtils");
    SEL selector = NSSelectorFromString(@"embeddedEntitlementsAtPath:");
    if (![cls respondsToSelector:selector]) return nil;
    return ((NSString *(*)(id, SEL, id))objc_msgSend)(cls, selector, path);
}

void SIEmbeddedReturnToHost(void) {
    Class cls = NSClassFromString(@"LCSharedUtils");
    SEL selector = NSSelectorFromString(@"launchToGuestAppWithClassicMode:");
    if ([cls respondsToSelector:selector]) {
        ((BOOL (*)(id, SEL, NSUInteger))objc_msgSend)(cls, selector, 0);
    }
}
