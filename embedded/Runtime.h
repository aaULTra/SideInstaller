#import <Foundation/Foundation.h>

NS_ASSUME_NONNULL_BEGIN
BOOL SIEmbeddedIsLiveProcess(void);
NSString * _Nullable SIEmbeddedHostTeam(void);
NSBundle * _Nullable SIEmbeddedHostBundle(void);
NSString * _Nullable SIEmbeddedEntitlementsAtPath(NSString *path);
void SIEmbeddedReturnToHost(void);
NS_ASSUME_NONNULL_END
