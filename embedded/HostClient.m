// Reuses LiveContainer's existing LiveProcess/XPC transport, not SideStore's
// signing pipeline or private App Intent runner.
#include "XPCServer.h"
#include "../LiveContainer/LCSharedUtils.h"
#include "../LiveContainer/utils.h"
#import <objc/message.h>
#import <UIKit/UIKit.h>

static LiveProcessSideStoreHandler *handler;

@implementation SideStoreClient
+ (SideStoreClient *)shared {
    static SideStoreClient *client;
    static dispatch_once_t once;
    dispatch_once(&once, ^{ client = [SideStoreClient new]; });
    return client;
}

+ (void)load {
    if (!NSUserDefaults.isSideStore || !NSUserDefaults.isLiveProcess) return;
    handler = ((LiveProcessSideStoreHandler *(*)(id, SEL))objc_msgSend)(
        NSClassFromString(@"LiveProcessSideStoreHandler"), @selector(shared));
    installSideStoreNotificationHooks();
    handler.connection.exportedInterface = [NSXPCInterface interfaceWithProtocol:@protocol(RefreshClient)];
    handler.connection.exportedObject = self.shared;
    [NSNotificationCenter.defaultCenter addObserver:self selector:@selector(didLaunch:)
                                               name:UIApplicationDidFinishLaunchingNotification object:nil];
}

+ (void)didLaunch:(NSNotification *)notification { [handler.server finishedLaunching]; }
- (void)relaunchLC { [LCSharedUtils launchToGuestAppWithClassicMode:0]; }

- (void)startLocalRefresh:(id<RefreshServer>)server {
    dispatch_async(dispatch_get_main_queue(), ^{
        Class bridge = NSClassFromString(@"SIEmbeddedBridge");
        SEL selector = NSSelectorFromString(@"refreshAllWithProgress:completion:");
        if (![bridge respondsToSelector:selector]) {
            [server finishWithSummary:nil error:@"The embedded SideInstaller refresh bridge is missing."];
            return;
        }
        void (^progress)(double) = ^(double value) { [server updateProgress:value]; };
        void (^completion)(NSString *, NSString *) = ^(NSString *summary, NSString *error) {
            [server finishWithSummary:summary error:error];
        };
        ((void (*)(id, SEL, id, id))objc_msgSend)(bridge, selector, progress, completion);
    });
}

- (void)refreshAllAppsWithIdentifier:(NSString *)identifier mangledTypeName:(NSString *)name {
    [self startLocalRefresh:handler.server];
}

- (void)cancelRefresh {
    dispatch_async(dispatch_get_main_queue(), ^{
        Class bridge = NSClassFromString(@"SIEmbeddedBridge");
        SEL selector = NSSelectorFromString(@"cancelRefresh");
        if ([bridge respondsToSelector:selector]) ((void (*)(id, SEL))objc_msgSend)(bridge, selector);
    });
}
@end
