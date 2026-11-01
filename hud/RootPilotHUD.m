// RootPilotHUD — SpringBoard overlay shown while RootPilot is controlling the iPhone.
//
// A glowing, slowly rotating edge around the whole screen (in the spirit of the border shown
// while Claude operates a computer), plus a ripple where the agent is about to touch.
//
// Deliberately minimal and passive:
//  - no method hooks: it only listens for the Darwin notifications the RootPilot loop posts
//    (com.rootpilot.hud.active / .update / .idle) and reads /var/mobile/Library/RootPilot/hud.json;
//  - the window never takes touches (hitTest returns nil), so it can't block the agent or you;
//  - no text is drawn, because RootPilot reads the screen with OCR and text would end up in its
//    observations. The current step is shown on the control page instead.
//
// It can also capture screen frames (for docs and debugging): only when RootPilot has written
// capture.request into its private 0700 folder, so another app posting the notification alone
// can't trigger it.

#import <QuartzCore/QuartzCore.h>
#import <UIKit/UIKit.h>
#import <Vision/Vision.h>
#include <dlfcn.h>
#include <notify.h>
#include <objc/message.h>
#include <unistd.h>

// Fixed, $HOME-independent location shared with RootPilot (agent/paths.py hud_dir): on
// rootless jailbreaks mobile's shell home is /var/jb/var/mobile, SpringBoard's is /var/mobile.
static NSString *const kHUDPath = @"/var/mobile/Library/RootPilot/hud.json";
static NSString *const kStatusPath = @"/var/mobile/Library/RootPilot/hud.status.json";
static NSString *const kCaptureRequest = @"/var/mobile/Library/RootPilot/capture.request";
static NSString *const kCaptureStatus = @"/var/mobile/Library/RootPilot/capture.status.json";
static NSString *const kCaptureDir = @"/var/mobile/Library/RootPilot/captures";
static NSString *const kOCRRequest = @"/var/mobile/Library/RootPilot/ocr.request";
static NSString *const kOCRResult = @"/var/mobile/Library/RootPilot/ocr.result.json";
static NSString *const kClipRequest = @"/var/mobile/Library/RootPilot/clipboard.request";
static NSString *const kClipResult = @"/var/mobile/Library/RootPilot/clipboard.result.json";
static NSString *const kKillFile = @"/var/mobile/Library/RootPilot/KILL";
static NSString *const kPixelsRequest = @"/var/mobile/Library/RootPilot/pixels.request";
static NSString *const kPixelsResult = @"/var/mobile/Library/RootPilot/pixels.result.json";
static NSString *const kPixelsData = @"/var/mobile/Library/RootPilot/pixels.rgb";
static NSString *const kPixelsJPEG = @"/var/mobile/Library/RootPilot/pixels.jpg";

// Self-report for `rootpilot doctor` and debugging: proves the overlay loaded and drew.
static void RPWriteStatus(NSDictionary *extra) {
    NSMutableDictionary *status = [@{
        @"loaded" : @YES,
        @"pid" : @(getpid()),
        @"ts" : @([NSDate date].timeIntervalSince1970)
    } mutableCopy];
    [status addEntriesFromDictionary:extra];
    NSData *data = [NSJSONSerialization dataWithJSONObject:status options:0 error:nil];
    [NSFileManager.defaultManager createDirectoryAtPath:kStatusPath.stringByDeletingLastPathComponent
                            withIntermediateDirectories:YES
                                             attributes:nil
                                                  error:nil];
    [data writeToFile:kStatusPath atomically:YES];
}

@interface RPPassthroughWindow : UIWindow
@end

@implementation RPPassthroughWindow
- (UIView *)hitTest:(CGPoint)point withEvent:(UIEvent *)event {
    return nil;  // every touch goes to whatever is underneath
}
- (BOOL)canBecomeKeyWindow {
    return NO;
}
@end

@interface RPHUD : NSObject
@property(nonatomic, strong) RPPassthroughWindow *window;
@property(nonatomic, strong) CALayer *edge;      // masked container for the rotating gradient
@property(nonatomic, strong) CAShapeLayer *glow;  // soft inner glow along the edge
@property(nonatomic, strong) CAShapeLayer *killEdge;  // red edge shown when the kill switch fires
@property(nonatomic, assign) BOOL active;
@property(nonatomic, assign) NSInteger frozen;  // OCR requests in flight
+ (instancetype)shared;
- (void)freeze:(BOOL)on;
@end

static UIColor *RPColor(CGFloat r, CGFloat g, CGFloat b) {
    return [UIColor colorWithRed:r / 255.0 green:g / 255.0 blue:b / 255.0 alpha:1];
}

@implementation RPHUD

+ (instancetype)shared {
    static RPHUD *hud;
    static dispatch_once_t once;
    dispatch_once(&once, ^{ hud = [RPHUD new]; });
    return hud;
}

- (UIWindowScene *)mainScene {
    for (UIScene *scene in UIApplication.sharedApplication.connectedScenes) {
        if ([scene isKindOfClass:UIWindowScene.class] &&
            ((UIWindowScene *)scene).screen == UIScreen.mainScreen) {
            return (UIWindowScene *)scene;
        }
    }
    return nil;
}

- (CGFloat)cornerRadius {
    UIScreen *screen = UIScreen.mainScreen;
    if ([screen respondsToSelector:NSSelectorFromString(@"_displayCornerRadius")]) {
        NSNumber *r = [screen valueForKey:@"_displayCornerRadius"];
        if (r.doubleValue > 0) return r.doubleValue;
    }
    return 0;
}

- (void)build {
    if (self.window) return;
    CGRect bounds = UIScreen.mainScreen.bounds;
    UIWindowScene *scene = [self mainScene];
    RPPassthroughWindow *w = scene ? [[RPPassthroughWindow alloc] initWithWindowScene:scene]
                                   : [[RPPassthroughWindow alloc] initWithFrame:bounds];
    w.frame = bounds;
    w.windowLevel = UIWindowLevelStatusBar + 1000;  // above apps, alerts and the status bar
    w.userInteractionEnabled = NO;
    w.backgroundColor = UIColor.clearColor;
    w.opaque = NO;
    w.alpha = 0;
    w.hidden = YES;

    CGFloat radius = [self cornerRadius];
    CGFloat width = 7;
    UIBezierPath *path =
        [UIBezierPath bezierPathWithRoundedRect:CGRectInset(bounds, width / 2, width / 2)
                                   cornerRadius:MAX(radius - width / 2, 0)];
    UIColor *clay = RPColor(217, 119, 87), *amber = RPColor(242, 166, 90),
            *coral = RPColor(232, 115, 90), *sand = RPColor(250, 214, 165);

    // Soft glow that bleeds inward from the edge.
    CAShapeLayer *glow = [CAShapeLayer layer];
    glow.path = path.CGPath;
    glow.fillColor = UIColor.clearColor.CGColor;
    glow.strokeColor = clay.CGColor;
    glow.lineWidth = 3;
    glow.shadowColor = clay.CGColor;
    glow.shadowRadius = 16;
    glow.shadowOpacity = 0.9;
    glow.shadowOffset = CGSizeZero;
    // Without a shadowPath the render server traces the stroke every frame offscreen; that
    // made each OCR read ~0.8 s slower on an iPhone X while the glow was up (1047 -> 1838 ms).
    glow.shadowPath = CGPathCreateCopyByStrokingPath(path.CGPath, NULL, 3, kCGLineCapRound,
                                                     kCGLineJoinRound, 0);
    [w.layer addSublayer:glow];

    // Bright edge: a conic gradient spinning under a ring-shaped mask.
    CALayer *edge = [CALayer layer];
    edge.frame = bounds;
    CAShapeLayer *mask = [CAShapeLayer layer];
    mask.path = path.CGPath;
    mask.fillColor = UIColor.clearColor.CGColor;
    mask.strokeColor = UIColor.blackColor.CGColor;
    mask.lineWidth = width;
    edge.mask = mask;

    CGFloat side = hypot(bounds.size.width, bounds.size.height);
    CAGradientLayer *spin = [CAGradientLayer layer];
    spin.type = kCAGradientLayerConic;
    spin.frame = CGRectMake(CGRectGetMidX(bounds) - side / 2, CGRectGetMidY(bounds) - side / 2,
                            side, side);
    spin.startPoint = CGPointMake(0.5, 0.5);
    spin.endPoint = CGPointMake(0.5, 0);
    spin.colors = @[
        (id)clay.CGColor, (id)amber.CGColor, (id)sand.CGColor, (id)coral.CGColor,
        (id)clay.CGColor
    ];
    [edge addSublayer:spin];
    [w.layer addSublayer:edge];

    CABasicAnimation *rotate = [CABasicAnimation animationWithKeyPath:@"transform.rotation.z"];
    rotate.fromValue = @0;
    rotate.toValue = @(2 * M_PI);
    rotate.duration = 6;
    rotate.repeatCount = HUGE_VALF;
    rotate.removedOnCompletion = NO;
    [spin addAnimation:rotate forKey:@"spin"];

    CABasicAnimation *breathe = [CABasicAnimation animationWithKeyPath:@"shadowOpacity"];
    breathe.fromValue = @0.45;
    breathe.toValue = @0.95;
    breathe.duration = 1.4;
    breathe.autoreverses = YES;
    breathe.repeatCount = HUGE_VALF;
    breathe.removedOnCompletion = NO;
    [glow addAnimation:breathe forKey:@"breathe"];

    CAShapeLayer *killEdge = [CAShapeLayer layer];
    killEdge.path = path.CGPath;
    killEdge.fillColor = UIColor.clearColor.CGColor;
    killEdge.strokeColor = RPColor(235, 64, 52).CGColor;
    killEdge.lineWidth = width + 4;
    killEdge.shadowColor = killEdge.strokeColor;
    killEdge.shadowRadius = 18;
    killEdge.shadowOpacity = 1;
    killEdge.shadowOffset = CGSizeZero;
    killEdge.shadowPath = CGPathCreateCopyByStrokingPath(path.CGPath, NULL, width + 4,
                                                         kCGLineCapRound, kCGLineJoinRound, 0);
    killEdge.opacity = 0;
    [w.layer addSublayer:killEdge];

    self.window = w;
    self.edge = edge;
    self.glow = glow;
    self.killEdge = killEdge;
}

// Pause the glow's animations while Vision reads the screen: the GPU is shared, and a spinning
// masked gradient redrawn 60 times a second slowed every read. Nested: OCR may overlap.
- (void)freeze:(BOOL)on {
    CALayer *layer = self.window.layer;
    if (!layer) return;
    if (on) {
        if (self.frozen++ > 0) return;
        CFTimeInterval now = [layer convertTime:CACurrentMediaTime() fromLayer:nil];
        layer.speed = 0;
        layer.timeOffset = now;
    } else {
        if (self.frozen == 0 || --self.frozen > 0) return;
        CFTimeInterval paused = layer.timeOffset;
        layer.speed = 1;
        layer.timeOffset = 0;
        layer.beginTime = 0;
        layer.beginTime = [layer convertTime:CACurrentMediaTime() fromLayer:nil] - paused;
    }
}

- (void)show {
    [self build];
    if (!self.window) return;
    self.active = YES;
    self.window.hidden = NO;
    [UIView animateWithDuration:0.35 animations:^{ self.window.alpha = 1; }];
    RPWriteStatus(@{
        @"visible" : @YES,
        @"scene" : @(self.window.windowScene != nil),
        @"level" : @(self.window.windowLevel)
    });
}

- (void)hide {
    self.active = NO;
    if (!self.window) return;
    [UIView animateWithDuration:0.45
        animations:^{ self.window.alpha = 0; }
        completion:^(BOOL finished) {
            if (!self.active) self.window.hidden = YES;
        }];
    RPWriteStatus(@{@"visible" : @NO});
}

- (void)flashKill {
    // Red edge pulses three times, then the overlay fades out: "RootPilot stopped".
    [self build];
    if (!self.window) return;
    self.window.hidden = NO;
    self.window.alpha = 1;
    CABasicAnimation *pulse = [CABasicAnimation animationWithKeyPath:@"opacity"];
    pulse.fromValue = @1;
    pulse.toValue = @0.15;
    pulse.duration = 0.22;
    pulse.autoreverses = YES;
    pulse.repeatCount = 3;
    self.killEdge.opacity = 1;
    [self.killEdge addAnimation:pulse forKey:@"pulse"];
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(1.5 * NSEC_PER_SEC)),
                   dispatch_get_main_queue(), ^{
                     self.killEdge.opacity = 0;
                     [self hide];
                   });
}

- (void)rippleAt:(CGPoint)point {
    if (!self.window || !self.active) return;
    UIColor *clay = RPColor(217, 119, 87);
    CAShapeLayer *dot = [CAShapeLayer layer];
    dot.path = [UIBezierPath bezierPathWithArcCenter:CGPointZero
                                              radius:22
                                          startAngle:0
                                            endAngle:2 * M_PI
                                           clockwise:YES]
                   .CGPath;
    dot.position = point;
    dot.fillColor = [clay colorWithAlphaComponent:0.30].CGColor;
    dot.strokeColor = clay.CGColor;
    dot.lineWidth = 3;
    [self.window.layer addSublayer:dot];

    CABasicAnimation *grow = [CABasicAnimation animationWithKeyPath:@"transform.scale"];
    grow.fromValue = @0.35;
    grow.toValue = @1.6;
    CABasicAnimation *fade = [CABasicAnimation animationWithKeyPath:@"opacity"];
    fade.fromValue = @1;
    fade.toValue = @0;
    CAAnimationGroup *group = [CAAnimationGroup animation];
    group.animations = @[ grow, fade ];
    group.duration = 0.7;
    group.timingFunction = [CAMediaTimingFunction functionWithName:kCAMediaTimingFunctionEaseOut];
    dot.opacity = 0;
    [CATransaction begin];
    [CATransaction setCompletionBlock:^{ [dot removeFromSuperlayer]; }];
    [dot addAnimation:group forKey:@"ripple"];
    [CATransaction commit];
}

- (NSDictionary *)readState {
    NSData *data = [NSData dataWithContentsOfFile:kHUDPath];
    if (!data) return nil;
    id obj = [NSJSONSerialization JSONObjectWithData:data options:0 error:nil];
    return [obj isKindOfClass:NSDictionary.class] ? obj : nil;
}

- (void)syncFromFile {
    NSDictionary *state = [self readState];
    if ([state[@"active"] boolValue]) [self show];
}

- (void)update {
    NSDictionary *state = [self readState];
    NSArray *point = state[@"point"];
    if ([point isKindOfClass:NSArray.class] && point.count == 2) {
        [self rippleAt:CGPointMake([point[0] doubleValue], [point[1] doubleValue])];
    }
}

@end

// ---- screen capture -------------------------------------------------------------------

// Private UIKit; returns a +1 image of the whole display (all windows, including ours).
typedef void *(*RPScreenFn)(void);

static RPScreenFn RPScreenFunction(void) {
    static RPScreenFn fn;
    static dispatch_once_t once;
    dispatch_once(&once, ^{ fn = (RPScreenFn)dlsym(RTLD_DEFAULT, "_UICreateScreenUIImage"); });
    return fn;
}

@interface RPCapture : NSObject
@property(nonatomic, strong) NSTimer *timer;
@property(nonatomic, strong) dispatch_queue_t queue;
@property(nonatomic, copy) NSString *dir;
@property(nonatomic, assign) NSInteger total, taken, written;
@property(nonatomic, assign) CGFloat width;
+ (instancetype)shared;
@end

@implementation RPCapture

+ (instancetype)shared {
    static RPCapture *capture;
    static dispatch_once_t once;
    dispatch_once(&once, ^{
      capture = [RPCapture new];
      capture.queue = dispatch_queue_create("com.rootpilot.hud.capture", DISPATCH_QUEUE_SERIAL);
    });
    return capture;
}

- (void)writeStatus:(BOOL)finished error:(NSString *)error {
    NSDictionary *s = @{
        @"dir" : self.dir ?: @"",
        @"total" : @(self.total),
        @"written" : @(self.written),
        @"finished" : @(finished),
        @"error" : error ?: @""
    };
    [[NSJSONSerialization dataWithJSONObject:s options:0 error:nil] writeToFile:kCaptureStatus
                                                                     atomically:YES];
}

- (void)startFromRequest {
    NSFileManager *fm = NSFileManager.defaultManager;
    NSData *data = [NSData dataWithContentsOfFile:kCaptureRequest];
    [fm removeItemAtPath:kCaptureRequest error:nil];  // one request, one capture
    if (!data || self.timer) return;
    NSDictionary *req = [NSJSONSerialization JSONObjectWithData:data options:0 error:nil];
    if (![req isKindOfClass:NSDictionary.class]) return;
    if (!RPScreenFunction()) {
        [self writeStatus:YES error:@"_UICreateScreenUIImage not available"];
        return;
    }
    NSString *name = [req[@"name"] description] ?: @"capture";
    NSCharacterSet *bad =
        [[NSCharacterSet characterSetWithCharactersInString:
                             @"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"]
            invertedSet];
    name = [[name componentsSeparatedByCharactersInSet:bad] componentsJoinedByString:@""];
    if (name.length == 0) name = @"capture";
    self.total = MAX(1, MIN(600, [req[@"frames"] integerValue]));
    double interval = MAX(0.1, MIN(5.0, [req[@"interval"] doubleValue] ?: 0.25));
    self.width = MAX(160, MIN(1242, [req[@"width"] doubleValue] ?: 540));
    self.taken = self.written = 0;
    self.dir = [kCaptureDir stringByAppendingPathComponent:name];
    [fm removeItemAtPath:self.dir error:nil];
    [fm createDirectoryAtPath:self.dir withIntermediateDirectories:YES attributes:nil error:nil];
    [self writeStatus:NO error:nil];
    [self tick];
    self.timer = [NSTimer scheduledTimerWithTimeInterval:interval
                                                  target:self
                                                selector:@selector(tick)
                                                userInfo:nil
                                                 repeats:YES];
}

- (void)tick {
    if (self.taken >= self.total) {
        [self.timer invalidate];
        self.timer = nil;
        return;
    }
    // Take ownership of the +1 image so ARC frees it (a leak here would be ~10 MB per frame).
    UIImage *shot = (__bridge_transfer UIImage *)RPScreenFunction()();
    NSInteger index = ++self.taken;
    if (!shot) return;
    CGFloat width = self.width;
    NSString *path = [self.dir stringByAppendingPathComponent:
                                   [NSString stringWithFormat:@"%04ld.jpg", (long)index]];
    dispatch_async(self.queue, ^{
      CGSize src = CGSizeMake(shot.size.width * shot.scale, shot.size.height * shot.scale);
      CGFloat factor = MIN(1.0, width / src.width);
      CGSize size = CGSizeMake(round(src.width * factor), round(src.height * factor));
      UIGraphicsImageRendererFormat *format = [UIGraphicsImageRendererFormat preferredFormat];
      format.scale = 1;
      UIGraphicsImageRenderer *renderer = [[UIGraphicsImageRenderer alloc] initWithSize:size
                                                                                 format:format];
      UIImage *small = [renderer imageWithActions:^(UIGraphicsImageRendererContext *ctx) {
        [shot drawInRect:CGRectMake(0, 0, size.width, size.height)];
      }];
      [UIImageJPEGRepresentation(small, 0.85) writeToFile:path atomically:YES];
      dispatch_async(dispatch_get_main_queue(), ^{
        self.written += 1;
        [self writeStatus:(self.written >= self.total) error:nil];
      });
    });
}

@end

// ---- text recognition --------------------------------------------------------------------
//
// Replaces ZXTouch's OCR, which leaks 15-30 MB of SpringBoard memory per read (measured on
// iOS 15 and 16) until jetsam kills SpringBoard. Here every read runs inside an autorelease
// pool and holds the screen image only for the duration of the request.

@interface RPOCR : NSObject
@property(nonatomic, strong) dispatch_queue_t queue;
@property(nonatomic, assign) BOOL busy;
+ (instancetype)shared;
@end

@implementation RPOCR

+ (instancetype)shared {
    static RPOCR *ocr;
    static dispatch_once_t once;
    dispatch_once(&once, ^{
      ocr = [RPOCR new];
      ocr.queue = dispatch_queue_create("com.rootpilot.hud.ocr", DISPATCH_QUEUE_SERIAL);
    });
    return ocr;
}

- (void)finish:(NSString *)rid items:(NSArray *)items size:(CGSize)size error:(NSString *)error {
    NSDictionary *result = @{
        @"id" : rid ?: @"",
        @"items" : items ?: @[],
        @"w" : @(size.width),
        @"h" : @(size.height),
        @"error" : error ?: @""
    };
    [[NSJSONSerialization dataWithJSONObject:result options:0 error:nil] writeToFile:kOCRResult
                                                                          atomically:YES];
}

- (void)startFromRequest {
    NSData *data = [NSData dataWithContentsOfFile:kOCRRequest];
    [NSFileManager.defaultManager removeItemAtPath:kOCRRequest error:nil];
    if (!data) return;
    NSDictionary *req = [NSJSONSerialization JSONObjectWithData:data options:0 error:nil];
    if (![req isKindOfClass:NSDictionary.class]) return;
    NSString *rid = [req[@"id"] description] ?: @"";
    BOOL fast = [req[@"fast"] boolValue];
    // Optional region (points): only that part is read, e.g. around a field for "Paste".
    NSArray *roi = [req[@"roi"] isKindOfClass:NSArray.class] ? req[@"roi"] : nil;
    CGSize points = UIScreen.mainScreen.bounds.size;
    if (self.busy) {
        [self finish:rid items:nil size:points error:@"busy"];
        return;
    }
    if (!RPScreenFunction()) {
        [self finish:rid items:nil size:points error:@"_UICreateScreenUIImage not available"];
        return;
    }
    UIImage *shot = (__bridge_transfer UIImage *)RPScreenFunction()();
    if (!shot.CGImage) {
        [self finish:rid items:nil size:points error:@"screen capture failed"];
        return;
    }
    self.busy = YES;
    [[RPHUD shared] freeze:YES];
    dispatch_async(self.queue, ^{
      @autoreleasepool {
        NSMutableArray *items = [NSMutableArray array];
        NSError *error = nil;
        VNRecognizeTextRequest *request = [[VNRecognizeTextRequest alloc] init];
        request.recognitionLevel = fast ? VNRequestTextRecognitionLevelFast
                                        : VNRequestTextRecognitionLevelAccurate;
        request.usesLanguageCorrection = NO;  // UI text: keep exactly what is drawn
        if (roi.count == 4) {
            // Vision's region is normalised with the origin at the bottom left.
            CGFloat x = [roi[0] doubleValue] / points.width, y = [roi[1] doubleValue] / points.height;
            CGFloat w = [roi[2] doubleValue] / points.width, h = [roi[3] doubleValue] / points.height;
            CGRect r = CGRectIntersection(CGRectMake(x, 1 - y - h, w, h), CGRectMake(0, 0, 1, 1));
            if (!CGRectIsEmpty(r)) request.regionOfInterest = r;
        }
        VNImageRequestHandler *handler = [[VNImageRequestHandler alloc] initWithCGImage:shot.CGImage
                                                                                options:@{}];
        [handler performRequests:@[ request ] error:&error];
        for (VNRecognizedTextObservation *obs in request.results) {
            VNRecognizedText *best = [obs topCandidates:1].firstObject;
            if (best.string.length == 0) continue;
            // Normalised, origin bottom-left, relative to the region of interest.
            CGRect area = request.regionOfInterest, o = obs.boundingBox;
            CGRect b = CGRectMake(area.origin.x + o.origin.x * area.size.width,
                                  area.origin.y + o.origin.y * area.size.height,
                                  o.size.width * area.size.width, o.size.height * area.size.height);
            [items addObject:@{
                @"text" : best.string,
                @"x" : @(b.origin.x * points.width),
                @"y" : @((1 - b.origin.y - b.size.height) * points.height),
                @"w" : @(b.size.width * points.width),
                @"h" : @(b.size.height * points.height),
                @"c" : @(best.confidence)
            }];
        }
        [self finish:rid items:items size:points error:error.localizedDescription];
      }
      dispatch_async(dispatch_get_main_queue(), ^{
        self.busy = NO;
        [[RPHUD shared] freeze:NO];
      });
    });
}

@end

// ---- clipboard ---------------------------------------------------------------------------
//
// ZXTouch's keyboard input is unusable (it crashed SpringBoard and Safari on iOS 16 and does
// nothing on iOS 15), so RootPilot types by setting the clipboard here and tapping Paste.

static void RPClipboardHandle(void) {
    NSData *data = [NSData dataWithContentsOfFile:kClipRequest];
    [NSFileManager.defaultManager removeItemAtPath:kClipRequest error:nil];
    if (!data) return;
    NSDictionary *req = [NSJSONSerialization JSONObjectWithData:data options:0 error:nil];
    if (![req isKindOfClass:NSDictionary.class]) return;
    NSString *rid = [req[@"id"] description] ?: @"";
    NSString *op = [req[@"op"] description] ?: @"";
    NSDictionary *result;
    @try {
        if ([op isEqualToString:@"set"] && [req[@"text"] isKindOfClass:NSString.class]) {
            UIPasteboard.generalPasteboard.string = req[@"text"];
            result = @{@"id" : rid, @"ok" : @YES};
        } else if ([op isEqualToString:@"get"]) {
            result = @{@"id" : rid, @"ok" : @YES, @"text" : UIPasteboard.generalPasteboard.string ?: @""};
        } else {
            result = @{@"id" : rid, @"ok" : @NO, @"error" : @"bad request"};
        }
    } @catch (NSException *e) {
        result = @{@"id" : rid, @"ok" : @NO, @"error" : e.reason ?: @"clipboard error"};
    }
    [[NSJSONSerialization dataWithJSONObject:result options:0 error:nil] writeToFile:kClipResult
                                                                          atomically:YES];
}

// ---- pixels ------------------------------------------------------------------------------
//
// The screen as raw RGB, ``scale`` pixels per point (1 by default), for reading boards of coloured
// blocks that OCR can't see and for screenshots sent to the model. Raw bytes because the
// device's Python has no image decoder or resizer fast enough.

static void RPPixelsWrite(NSString *rid, CGSize size, NSString *file, NSString *error) {
    NSDictionary *result = @{
        @"id" : rid ?: @"",
        @"ok" : @(error.length == 0),
        @"w" : @((NSInteger)size.width),
        @"h" : @((NSInteger)size.height),
        @"file" : file ?: @"",
        @"error" : error ?: @""
    };
    [[NSJSONSerialization dataWithJSONObject:result options:0 error:nil] writeToFile:kPixelsResult
                                                                          atomically:YES];
}

static void RPPixelsHandle(void) {
    NSData *data = [NSData dataWithContentsOfFile:kPixelsRequest];
    [NSFileManager.defaultManager removeItemAtPath:kPixelsRequest error:nil];
    if (!data) return;
    NSDictionary *req = [NSJSONSerialization JSONObjectWithData:data options:0 error:nil];
    if (![req isKindOfClass:NSDictionary.class]) return;
    NSString *rid = [req[@"id"] description] ?: @"";
    CGSize points = UIScreen.mainScreen.bounds.size;
    double scale = [req[@"scale"] doubleValue];
    scale = scale > 0 ? MAX(1.0, MIN(3.0, scale)) : 1.0;
    size_t w = (size_t)round(points.width * scale), h = (size_t)round(points.height * scale);
    // "jpeg": a compressed screenshot for the model (~10x smaller than a PNG to upload).
    double quality = [req[@"format"] isEqual:@"jpeg"] ? MAX(0.3, MIN(0.95, [req[@"quality"] doubleValue] ?: 0.7)) : 0;
    if (!RPScreenFunction()) {
        RPPixelsWrite(rid, CGSizeZero, nil, @"_UICreateScreenUIImage not available");
        return;
    }
    UIImage *shot = (__bridge_transfer UIImage *)RPScreenFunction()();
    CGImageRef image = shot.CGImage;
    if (!image) {
        RPPixelsWrite(rid, CGSizeZero, nil, @"screen capture failed");
        return;
    }
    CGImageRetain(image);
    static dispatch_queue_t queue;
    static dispatch_once_t once;
    dispatch_once(&once, ^{ queue = dispatch_queue_create("com.rootpilot.hud.pixels", DISPATCH_QUEUE_SERIAL); });
    dispatch_async(queue, ^{
      @autoreleasepool {
        NSMutableData *rgba = [NSMutableData dataWithLength:w * h * 4];
        CGColorSpaceRef space = CGColorSpaceCreateDeviceRGB();
        CGContextRef ctx = CGBitmapContextCreate(rgba.mutableBytes, w, h, 8, w * 4, space,
                                                 kCGImageAlphaNoneSkipLast | kCGBitmapByteOrder32Big);
        CGColorSpaceRelease(space);
        NSString *error = nil, *file = kPixelsData;
        if (ctx && quality > 0) {
            CGContextSetInterpolationQuality(ctx, kCGInterpolationMedium);
            CGContextDrawImage(ctx, CGRectMake(0, 0, w, h), image);
            CGImageRef small = CGBitmapContextCreateImage(ctx);
            CGContextRelease(ctx);
            NSData *jpeg = small ? UIImageJPEGRepresentation([UIImage imageWithCGImage:small], quality) : nil;
            if (small) CGImageRelease(small);
            file = kPixelsJPEG;
            if (![jpeg writeToFile:kPixelsJPEG atomically:YES]) error = @"could not write the JPEG";
        } else if (ctx) {
            CGContextSetInterpolationQuality(ctx, kCGInterpolationMedium);
            CGContextDrawImage(ctx, CGRectMake(0, 0, w, h), image);
            CGContextRelease(ctx);
            // Drop the padding byte: Python then reads 3 bytes per pixel with no conversion.
            NSMutableData *rgb = [NSMutableData dataWithLength:w * h * 3];
            const uint8_t *src = rgba.bytes;
            uint8_t *dst = rgb.mutableBytes;
            for (size_t i = 0, n = w * h; i < n; i++) {
                dst[i * 3] = src[i * 4];
                dst[i * 3 + 1] = src[i * 4 + 1];
                dst[i * 3 + 2] = src[i * 4 + 2];
            }
            if (![rgb writeToFile:kPixelsData atomically:YES]) error = @"could not write pixels";
        } else {
            error = @"could not create a bitmap";
        }
        CGImageRelease(image);
        RPPixelsWrite(rid, CGSizeMake(w, h), file, error);
      }
    });
}

// ---- kill switch -------------------------------------------------------------------------
//
// Stops a running agent from the phone itself, whatever app is in front:
//  - lock the phone (side/top button), or
//  - press the volume buttons 4 times within 2.5 seconds, or
//  - post com.rootpilot.hud.kill.
// Only acts while RootPilot is running. Writes KILL, which the agent loop checks before every
// model call and every action, and flashes the edge red.

static void RPKill(NSString *source) {
    RPHUD *hud = [RPHUD shared];
    if (!hud.active) return;
    NSDictionary *info = @{@"source" : source, @"ts" : @([NSDate date].timeIntervalSince1970)};
    [[NSJSONSerialization dataWithJSONObject:info options:0 error:nil] writeToFile:kKillFile
                                                                        atomically:YES];
    notify_post("com.rootpilot.hud.killed");
    hud.active = NO;
    [hud flashKill];
}

static void RPWatchVolumeButtons(void) {
    // AVSystemController only posts volume changes to subscribers on iOS 15+.
    Class cls = NSClassFromString(@"AVSystemController");
    SEL shared = NSSelectorFromString(@"sharedAVSystemController");
    if (!cls || ![cls respondsToSelector:shared]) return;
    id controller = ((id(*)(id, SEL))objc_msgSend)(cls, shared);
    SEL setAttr = NSSelectorFromString(@"setAttribute:forKey:error:");
    NSString *name = @"AVSystemController_SystemVolumeDidChangeNotification";
    if ([controller respondsToSelector:setAttr]) {
        ((BOOL(*)(id, SEL, id, id, NSError **))objc_msgSend)(
            controller, setAttr, @[ name ], @"AVSystemController_SubscribeToNotificationsAttribute", nil);
    }
    static NSMutableArray<NSNumber *> *presses;
    presses = [NSMutableArray array];
    [NSNotificationCenter.defaultCenter
        addObserverForName:name
                    object:nil
                     queue:NSOperationQueue.mainQueue
                usingBlock:^(NSNotification *note) {
                  NSString *reason =
                      note.userInfo[@"AVSystemController_AudioVolumeChangeReasonNotificationParameter"];
                  if (reason && ![reason isEqualToString:@"ExplicitVolumeChange"]) return;
                  NSTimeInterval now = [NSDate date].timeIntervalSince1970;
                  [presses addObject:@(now)];
                  while (presses.count && now - presses.firstObject.doubleValue > 2.5) {
                      [presses removeObjectAtIndex:0];
                  }
                  if (presses.count >= 4) {
                      [presses removeAllObjects];
                      RPKill(@"volume buttons");
                  }
                }];
}

static void RPRegister(void) {
    int token;
    dispatch_queue_t main = dispatch_get_main_queue();
    notify_register_dispatch("com.rootpilot.hud.active", &token, main, ^(int t) {
      [[RPHUD shared] show];
    });
    notify_register_dispatch("com.rootpilot.hud.update", &token, main, ^(int t) {
      [[RPHUD shared] update];
    });
    notify_register_dispatch("com.rootpilot.hud.idle", &token, main, ^(int t) {
      [[RPHUD shared] hide];
    });
    notify_register_dispatch("com.rootpilot.hud.clipboard", &token, main, ^(int t) {
      RPClipboardHandle();
    });
    notify_register_dispatch("com.apple.springboard.lockcomplete", &token, main, ^(int t) {
      RPKill(@"lock button");
    });
    notify_register_dispatch("com.rootpilot.hud.kill", &token, main, ^(int t) {
      RPKill(@"notification");
    });
    RPWatchVolumeButtons();
    notify_register_dispatch("com.rootpilot.hud.ocr", &token, main, ^(int t) {
      [[RPOCR shared] startFromRequest];
    });
    notify_register_dispatch("com.rootpilot.hud.capture", &token, main, ^(int t) {
      [[RPCapture shared] startFromRequest];
    });
    notify_register_dispatch("com.rootpilot.hud.pixels", &token, main, ^(int t) {
      RPPixelsHandle();
    });
}

__attribute__((constructor)) static void RPInit(void) {
    // Wait for SpringBoard to finish launching before touching UIKit.
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(4 * NSEC_PER_SEC)),
                   dispatch_get_main_queue(), ^{
                     RPRegister();
                     RPWriteStatus(@{@"visible" : @NO});
                     [[RPHUD shared] syncFromFile];
                   });
}
