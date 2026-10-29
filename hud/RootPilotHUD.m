// RootPilotHUD — SpringBoard overlay shown while RootPilot is controlling the iPhone.
//
// A glowing, slowly rotating edge around the whole screen (in the spirit of the border shown
// while Claude operates a computer), plus a ripple where the agent is about to touch.
//
// Deliberately minimal and passive:
//  - no method hooks: it only listens for the Darwin notifications the RootPilot loop posts
//    (com.rootpilot.hud.active / .update / .idle) and reads ~/.local/state/rootpilot/hud.json;
//  - the window never takes touches (hitTest returns nil), so it can't block the agent or you;
//  - no text is drawn, because RootPilot reads the screen with OCR and text would end up in its
//    observations. The current step is shown on the control page instead.

#import <QuartzCore/QuartzCore.h>
#import <UIKit/UIKit.h>
#include <notify.h>
#include <unistd.h>

static NSString *const kHUDPath = @"/var/mobile/.local/state/rootpilot/hud.json";
static NSString *const kStatusPath = @"/var/mobile/.local/state/rootpilot/hud.status.json";

// Self-report for `rootpilot doctor` and debugging: proves the overlay loaded and drew.
static void RPWriteStatus(NSDictionary *extra) {
    NSMutableDictionary *status = [@{
        @"loaded" : @YES,
        @"pid" : @(getpid()),
        @"ts" : @([NSDate date].timeIntervalSince1970)
    } mutableCopy];
    [status addEntriesFromDictionary:extra];
    NSData *data = [NSJSONSerialization dataWithJSONObject:status options:0 error:nil];
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
@property(nonatomic, assign) BOOL active;
+ (instancetype)shared;
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

    self.window = w;
    self.edge = edge;
    self.glow = glow;
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
