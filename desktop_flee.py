#!/usr/bin/env python3
"""
Desktop icons sprout arms and legs and flee from the mouse cursor.

Captures the wallpaper and icon sprites, then renders a full-screen overlay
just above Finder's desktop icons with our own movable versions.
"""

import math
import sys
import os

from AppKit import (
    NSApplication, NSWindow, NSView, NSColor, NSBezierPath, NSTimer,
    NSScreen, NSBackingStoreBuffered, NSWindowStyleMaskBorderless,
    NSObject, NSMakeRect, NSURL, NSWorkspace, NSImage,
    NSBitmapImageRep, NSCompositingOperationSourceOver,
)
from ApplicationServices import (
    AXUIElementCreateApplication,
    AXUIElementCopyAttributeValue,
    AXValueGetValue,
    kAXValueCGPointType,
)
from Quartz import (
    CGEventCreate, CGEventGetLocation,
    CGWindowListCopyWindowInfo, kCGWindowListOptionAll, kCGNullWindowID,
    CGWindowListCreateImage, kCGWindowListOptionIncludingWindow,
    kCGWindowImageBoundsIgnoreFraming, CGRectMake,
    CGImageGetWidth, CGImageGetHeight, CGImageCreateWithImageInRect,
    kCGDesktopIconWindowLevel,
)
import objc

# ── Tuning ──────────────────────────────────────────────────────────────
FLEE_RADIUS = 180
FLEE_SPEED = 8
RETURN_SPEED = 4
ICON_SIZE = 64
ICON_HALF = ICON_SIZE // 2
FPS = 30
ARM_LEN = 18
LEG_LEN = 22
LIMB_W = 3.5
MENU_BAR_H = 38
DOCK_H = 80
DARK = None

# Crop region around each icon (points, relative to icon AX position)
CROP_PAD_L = 25
CROP_PAD_T = 5
CROP_W = 114
CROP_H = 105


# ── Mouse ───────────────────────────────────────────────────────────────
def get_mouse():
    e = CGEventCreate(None)
    p = CGEventGetLocation(e)
    return p.x, p.y


# ── AX icon discovery ──────────────────────────────────────────────────
def get_finder_pid():
    for a in NSWorkspace.sharedWorkspace().runningApplications():
        if a.bundleIdentifier() == "com.apple.finder":
            return a.processIdentifier()
    return None


def get_desktop_icons():
    pid = get_finder_pid()
    if not pid:
        print("Finder not running!", flush=True)
        sys.exit(1)

    app = AXUIElementCreateApplication(pid)
    err, windows = AXUIElementCopyAttributeValue(app, "AXWindows", None)
    if err or not windows:
        print("Cannot access Finder desktop (Accessibility permission needed).", flush=True)
        sys.exit(1)

    err, groups = AXUIElementCopyAttributeValue(windows[0], "AXChildren", None)
    if err or not groups:
        return []
    err, ax_icons = AXUIElementCopyAttributeValue(groups[0], "AXChildren", None)
    if err or not ax_icons:
        return []

    icons = []
    for ax in ax_icons:
        err, title = AXUIElementCopyAttributeValue(ax, "AXTitle", None)
        err, pos_val = AXUIElementCopyAttributeValue(ax, "AXPosition", None)
        if not pos_val:
            continue
        ok, pt = AXValueGetValue(pos_val, kAXValueCGPointType, None)
        if not ok:
            continue
        icons.append({
            "name": str(title) if title else "?",
            "x": float(pt.x), "y": float(pt.y),
            "ox": float(pt.x), "oy": float(pt.y),
            "fleeing": False, "was_fleeing": False,
            "frame": 0, "angle": 0.0,
            "pop_t": 0.0,  # 0→1 over pop-in frames
            "sprite": None,
        })
    return icons


# ── Window capture helpers ──────────────────────────────────────────────
def find_window_id(owner, name_contains=None, layer_below=0, match_width=None):
    """Find a window by owner name and optional filters."""
    windows = CGWindowListCopyWindowInfo(kCGWindowListOptionAll, kCGNullWindowID)
    for w in windows:
        if w.get("kCGWindowOwnerName") != owner:
            continue
        if layer_below is not None and w.get("kCGWindowLayer", 0) >= layer_below:
            continue
        if name_contains and name_contains not in w.get("kCGWindowName", ""):
            continue
        if match_width and w["kCGWindowBounds"]["Width"] != match_width:
            continue
        return w["kCGWindowNumber"]
    return None


def capture_window(wid, sw, sh):
    rect = CGRectMake(0, 0, sw, sh)
    return CGWindowListCreateImage(
        rect, kCGWindowListOptionIncludingWindow, wid,
        kCGWindowImageBoundsIgnoreFraming)


def cg_to_nsimage(cg_img, size_pts=None):
    rep = NSBitmapImageRep.alloc().initWithCGImage_(cg_img)
    w = CGImageGetWidth(cg_img)
    h = CGImageGetHeight(cg_img)
    img = NSImage.alloc().initWithSize_((w, h))
    img.addRepresentation_(rep)
    if size_pts:
        img.setSize_(size_pts)
    return img


def crop_sprite(cg_img, icon, scale):
    full_w = CGImageGetWidth(cg_img)
    full_h = CGImageGetHeight(cg_img)

    px = int((icon["ox"] - CROP_PAD_L) * scale)
    py = int((icon["oy"] - CROP_PAD_T) * scale)
    pw = int(CROP_W * scale)
    ph = int(CROP_H * scale)
    px, py = max(0, px), max(0, py)
    if px + pw > full_w: pw = full_w - px
    if py + ph > full_h: ph = full_h - py

    cropped = CGImageCreateWithImageInRect(cg_img, CGRectMake(px, py, pw, ph))
    if not cropped:
        return None

    rep = NSBitmapImageRep.alloc().initWithCGImage_(cropped)
    img = NSImage.alloc().initWithSize_((CROP_W, CROP_H))
    img.addRepresentation_(rep)
    return img


# ── Drawing helpers ─────────────────────────────────────────────────────
SKIN = None    # peach skin tone, set at init
SHOE = None    # dark red shoe color
WHITE = None

UPPER_ARM = 10
FOREARM = 9
THIGH = 13
SHIN = 11


def _stroke_seg(x1, y1, x2, y2, width):
    """Draw a single limb segment with outline."""
    path = NSBezierPath.bezierPath()
    path.moveToPoint_((x1, y1))
    path.lineToPoint_((x2, y2))
    path.setLineCapStyle_(1)  # round
    DARK.set()
    path.setLineWidth_(width + 1.5)
    path.stroke()
    SKIN.set()
    path.setLineWidth_(width)
    path.stroke()


def _joint_dot(x, y, r):
    oval = NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(x - r, y - r, r * 2, r * 2))
    SKIN.set()
    oval.fill()


def draw_arm(sx, sy, angle, phase, scale, side):
    """Sprinter arm pump. phase=-1..1, side=+1/-1."""
    ua = UPPER_ARM * scale
    fa = FOREARM * scale
    if scale < 0.1:
        return

    # Shoulder is on the side of the body (perpendicular to flee).
    # Upper arm swings forward/back: forward = flee dir, back = opposite.
    # At rest the arm points "down" (away from flee direction).
    swing_angle = phase * 0.7
    upper_a = angle + math.pi * 0.5 * side + swing_angle
    elbow_x = sx + math.cos(upper_a) * ua
    elbow_y = sy + math.sin(upper_a) * ua

    # Forearm: always folds roughly back toward the body at ~90 deg.
    # The fold direction flips with side so both arms bend inward.
    forearm_a = upper_a - side * (1.3 + phase * 0.2)
    hand_x = elbow_x + math.cos(forearm_a) * fa
    hand_y = elbow_y + math.sin(forearm_a) * fa

    _stroke_seg(sx, sy, elbow_x, elbow_y, 2.5)
    _stroke_seg(elbow_x, elbow_y, hand_x, hand_y, 2.0)
    _joint_dot(elbow_x, elbow_y, 1.8 * scale)

    # Small fist
    r = 2.5 * scale
    oval = NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect(hand_x - r, hand_y - r, r * 2, r * 2))
    SKIN.set()
    oval.fill()
    DARK.set()
    oval.setLineWidth_(0.8)
    oval.stroke()


def draw_leg(hx, hy, angle, phase, scale):
    """Running leg with high knee lift. phase=-1..1, scale=pop amount."""
    th = THIGH * scale
    sh = SHIN * scale
    if scale < 0.1:
        return

    back = angle + math.pi

    # Thigh swings between forward (flee dir) and back.
    # phase +1 = knee up in front, phase -1 = leg extended behind (push off)
    thigh_a = back - phase * 0.6
    knee_x = hx + math.cos(thigh_a) * th
    knee_y = hy + math.sin(thigh_a) * th

    # Shin: when knee is up (phase>0), shin folds under (tight bend).
    # When pushing off (phase<0), shin extends almost straight.
    if phase > 0:
        knee_bend = 1.8  # tight fold
    else:
        knee_bend = 0.4  # nearly straight push-off
    shin_a = thigh_a + knee_bend
    foot_x = knee_x + math.cos(shin_a) * sh
    foot_y = knee_y + math.sin(shin_a) * sh

    _stroke_seg(hx, hy, knee_x, knee_y, 3.0)
    _stroke_seg(knee_x, knee_y, foot_x, foot_y, 2.5)
    _joint_dot(knee_x, knee_y, 2.2 * scale)

    # Shoe — oriented along the shin direction
    shoe_len = 8 * scale
    shoe_h = 5 * scale
    # Shoe points along the shin
    shoe_cx = foot_x + math.cos(shin_a) * shoe_len * 0.3
    shoe_cy = foot_y + math.sin(shin_a) * shoe_len * 0.3
    shoe = NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect(shoe_cx - shoe_len / 2, shoe_cy - shoe_h / 2, shoe_len, shoe_h))
    SHOE.set()
    shoe.fill()
    DARK.set()
    shoe.setLineWidth_(0.8)
    shoe.stroke()


def draw_eyes(cx, cy, angle, frame):
    """Small panicked eyes at the leading edge of the icon."""
    perp = angle + math.pi / 2
    for side in (1, -1):
        # Position: at the front edge of the icon, close together
        ex = cx + math.cos(angle) * (ICON_HALF + 2) + math.cos(perp) * 5 * side
        ey = cy + math.sin(angle) * (ICON_HALF + 2) + math.sin(perp) * 5 * side

        # White of eye
        r = 4
        oval = NSBezierPath.bezierPathWithOvalInRect_(
            NSMakeRect(ex - r, ey - r, r * 2, r * 2))
        WHITE.set()
        oval.fill()
        DARK.set()
        oval.setLineWidth_(1.0)
        oval.stroke()

        # Pupil — looking in flee direction
        pr = 1.5
        px = ex + math.cos(angle) * 1.5
        py = ey + math.sin(angle) * 1.5
        pupil = NSBezierPath.bezierPathWithOvalInRect_(
            NSMakeRect(px - pr, py - pr, pr * 2, pr * 2))
        DARK.set()
        pupil.fill()

    # Tiny open mouth
    mx = cx + math.cos(angle) * (ICON_HALF + 1)
    my = cy + math.sin(angle) * (ICON_HALF + 1)
    mouth_r = 2
    mouth = NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect(mx - mouth_r, my - mouth_r, mouth_r * 2, mouth_r * 2))
    DARK.set()
    mouth.fill()


def draw_sweat(cx, cy, angle, frame):
    if frame % 3 != 0:
        return
    blue = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.3, 0.65, 1.0, 0.8)
    perp = angle + math.pi / 2
    # Sweat drops flying off behind the icon
    for i in range(3):
        t = (frame * 0.3 + i * 2.1) % 6.0
        spread = (i - 1) * 15
        dist = 30 + t * 4
        dx = -math.cos(angle) * dist + math.cos(perp) * spread
        dy = -math.sin(angle) * dist + math.sin(perp) * spread
        sx, sy = cx + dx, cy + dy
        size = max(1, 4 - t * 0.5)
        drop = NSBezierPath.bezierPath()
        drop.moveToPoint_((sx, sy - size))
        drop.curveToPoint_controlPoint1_controlPoint2_(
            (sx, sy + size), (sx - size * 0.7, sy), (sx + size * 0.7, sy))
        blue.set()
        drop.fill()


def draw_speed_lines(cx, cy, angle, frame):
    """Motion lines trailing behind the icon."""
    if frame % 2 != 0:
        return
    perp = angle + math.pi / 2
    gray = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.5, 0.5, 0.5, 0.4)
    for i in range(3):
        spread = (i - 1) * 12
        start_d = 35 + i * 5
        length = 12 + i * 3
        sx = cx - math.cos(angle) * start_d + math.cos(perp) * spread
        sy = cy - math.sin(angle) * start_d + math.sin(perp) * spread
        ex = sx - math.cos(angle) * length
        ey = sy - math.sin(angle) * length
        line = NSBezierPath.bezierPath()
        line.moveToPoint_((sx, sy))
        line.lineToPoint_((ex, ey))
        line.setLineCapStyle_(1)
        gray.set()
        line.setLineWidth_(1.5)
        line.stroke()


def draw_pop_burst(cx, cy, pop_t):
    """Little star/burst effect when limbs first pop out."""
    if pop_t <= 0 or pop_t >= 0.8:
        return
    n_rays = 8
    burst_r = 40 * pop_t
    alpha = max(0, 1.0 - pop_t * 1.5)
    color = NSColor.colorWithCalibratedRed_green_blue_alpha_(1, 1, 0.6, alpha)
    for i in range(n_rays):
        a = i * math.pi * 2 / n_rays
        inner = burst_r * 0.4
        outer = burst_r
        line = NSBezierPath.bezierPath()
        line.moveToPoint_((cx + math.cos(a) * inner, cy + math.sin(a) * inner))
        line.lineToPoint_((cx + math.cos(a) * outer, cy + math.sin(a) * outer))
        line.setLineCapStyle_(1)
        color.set()
        line.setLineWidth_(2.0)
        line.stroke()


def draw_icon_limbs(ic):
    cx = ic["x"] + ICON_HALF
    cy = ic["y"] + ICON_HALF
    a = ic["angle"]
    f = ic["frame"]
    pop = ic.get("pop_t", 1.0)
    perp = a + math.pi / 2
    back = a + math.pi

    # Ease-out for pop scale
    scale = 1.0 - (1.0 - pop) ** 2  # quadratic ease-out

    # Running cycle: sin wave, frame 0-15
    cycle = math.sin(f * math.pi / 4)  # -1 to 1 over 8 frames

    # Pop burst effect
    draw_pop_burst(cx, cy, pop)

    # Speed lines (only after pop)
    if pop >= 1.0:
        draw_speed_lines(cx, cy, a, f)

    # Right leg + left arm forward, then swap (opposite pairs)
    # Legs
    for side in (1, -1):
        hx = cx + math.cos(back) * ICON_HALF * 0.4 + math.cos(perp) * ICON_HALF * 0.3 * side
        hy = cy + math.sin(back) * ICON_HALF * 0.4 + math.sin(perp) * ICON_HALF * 0.3 * side
        # side=1 (right leg) uses +cycle, side=-1 (left leg) uses -cycle
        draw_leg(hx, hy, a, cycle * side, scale)

    # Arms: opposite phase from same-side leg
    for side in (1, -1):
        sx = cx + math.cos(perp) * ICON_HALF * 0.75 * side
        sy = cy + math.sin(perp) * ICON_HALF * 0.75 * side
        draw_arm(sx, sy, a, -cycle * side, scale, side)

    # Face (scale eyes too during pop)
    if scale > 0.3:
        draw_eyes(cx, cy, a, f)
    if pop >= 1.0:
        draw_sweat(cx, cy, a, f)


# ── NSView ──────────────────────────────────────────────────────────────
class OverlayView(NSView):
    def isFlipped(self):
        return True

    def drawRect_(self, rect):
        bg = getattr(self, "_bg", None)
        if bg:
            bg.drawInRect_fromRect_operation_fraction_respectFlipped_hints_(
                self.bounds(),
                NSMakeRect(0, 0, bg.size().width, bg.size().height),
                NSCompositingOperationSourceOver, 1.0, True, None)

        icons = getattr(self, "_icons", None)
        if not icons:
            return

        for ic in icons:
            sprite = ic.get("sprite")
            if sprite:
                dx = ic["x"] - CROP_PAD_L
                dy = ic["y"] - CROP_PAD_T
                sprite.drawInRect_fromRect_operation_fraction_respectFlipped_hints_(
                    NSMakeRect(dx, dy, CROP_W, CROP_H),
                    NSMakeRect(0, 0, sprite.size().width, sprite.size().height),
                    NSCompositingOperationSourceOver, 1.0, True, None)
            if ic["fleeing"]:
                draw_icon_limbs(ic)


# ── Controller ──────────────────────────────────────────────────────────
class Controller(NSObject):
    def init(self):
        self = objc.super(Controller, self).init()
        if self is None:
            return None

        global DARK, SKIN, SHOE, WHITE
        DARK = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.15, 0.15, 0.15, 1)
        SKIN = NSColor.colorWithCalibratedRed_green_blue_alpha_(1.0, 0.85, 0.72, 1)
        SHOE = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.7, 0.15, 0.15, 1)
        WHITE = NSColor.whiteColor()

        screen = NSScreen.mainScreen()
        sf = screen.frame()
        self._sw = sf.size.width
        self._sh = sf.size.height
        scale = screen.backingScaleFactor()

        # 1. Get icon positions
        self._icons = get_desktop_icons()
        print(f"Found {len(self._icons)} desktop icons.", flush=True)
        if not self._icons:
            print("No icons to animate!", flush=True)
            sys.exit(0)

        # 2. Capture Dock's wallpaper window (clean background)
        wp_wid = find_window_id("Dock", name_contains="Wallpaper-",
                                layer_below=0, match_width=self._sw)
        if not wp_wid:
            print("Cannot find wallpaper window.", flush=True)
            sys.exit(1)
        cg_wallpaper = capture_window(wp_wid, self._sw, self._sh)
        self._wallpaper = cg_to_nsimage(cg_wallpaper, size_pts=(self._sw, self._sh))
        print("Captured wallpaper.", flush=True)

        # 3. Capture Finder desktop window (icons on top of wallpaper)
        finder_wid = find_window_id("Finder", layer_below=0)
        if not finder_wid:
            print("Cannot find Finder desktop window.", flush=True)
            sys.exit(1)
        cg_desktop = capture_window(finder_wid, self._sw, self._sh)
        print("Captured desktop icons.", flush=True)

        # 4. Crop each icon sprite from the desktop capture
        for ic in self._icons:
            ic["sprite"] = crop_sprite(cg_desktop, ic, scale)

        # 5. Create overlay window just above Finder's desktop icons
        self._win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0, 0), (self._sw, self._sh)),
            NSWindowStyleMaskBorderless,
            NSBackingStoreBuffered,
            False,
        )
        self._win.setLevel_(kCGDesktopIconWindowLevel + 20)
        self._win.setOpaque_(True)
        self._win.setIgnoresMouseEvents_(True)
        self._win.setHasShadow_(False)

        self._view = OverlayView.alloc().initWithFrame_(
            ((0, 0), (self._sw, self._sh)))
        self._view._icons = self._icons
        self._view._bg = self._wallpaper
        self._win.setContentView_(self._view)
        self._win.orderFrontRegardless()

        print("Running! Move your cursor near desktop icons.", flush=True)

        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1.0 / FPS, self, b"tick:", None, True)
        return self

    @objc.typedSelector(b"v@:@")
    def tick_(self, timer):
        mx, my = get_mouse()

        for ic in self._icons:
            cx = ic["x"] + ICON_HALF
            cy = ic["y"] + ICON_HALF
            dx = cx - mx
            dy = cy - my
            dist = math.hypot(dx, dy)

            if dist < FLEE_RADIUS and dist > 1:
                # Just entered radius — start pop animation
                if not ic["was_fleeing"]:
                    ic["pop_t"] = 0.0
                ic["fleeing"] = True
                ic["was_fleeing"] = True
                ic["frame"] = (ic["frame"] + 1) % 16
                nx, ny = dx / dist, dy / dist
                ic["angle"] = math.atan2(ny, nx)

                # Advance pop-in (limbs grow from 0→1 over ~8 frames)
                if ic["pop_t"] < 1.0:
                    ic["pop_t"] = min(1.0, ic["pop_t"] + 0.15)
                else:
                    # Only move once pop is done
                    new_x = ic["x"] + nx * FLEE_SPEED
                    new_y = ic["y"] + ny * FLEE_SPEED
                    ic["x"] = max(10, min(new_x, self._sw - ICON_SIZE - 10))
                    ic["y"] = max(MENU_BAR_H, min(new_y, self._sh - ICON_SIZE - DOCK_H))
            else:
                ic["fleeing"] = False
                ic["was_fleeing"] = False
                ddx = ic["ox"] - ic["x"]
                ddy = ic["oy"] - ic["y"]
                d = math.hypot(ddx, ddy)
                if d > 2:
                    ic["x"] += ddx / d * RETURN_SPEED
                    ic["y"] += ddy / d * RETURN_SPEED
                elif d > 0.5:
                    ic["x"] = ic["ox"]
                    ic["y"] = ic["oy"]

        self._view.setNeedsDisplay_(True)


# ── Main ────────────────────────────────────────────────────────────────
def main():
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(1)
    Controller.alloc().init()
    print("Press Ctrl+C to stop.", flush=True)
    from PyObjCTools import AppHelper
    AppHelper.runEventLoop(installInterrupt=True)


if __name__ == "__main__":
    main()
