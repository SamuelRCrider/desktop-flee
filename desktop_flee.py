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
FLEE_SPEED = 18
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
            "fleeing": False, "frame": 0, "angle": 0.0,
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
def draw_limb(x1, y1, x2, y2, hand=True):
    path = NSBezierPath.bezierPath()
    path.moveToPoint_((x1, y1))
    path.lineToPoint_((x2, y2))
    path.setLineCapStyle_(1)
    NSColor.whiteColor().set()
    path.setLineWidth_(LIMB_W + 3)
    path.stroke()
    DARK.set()
    path.setLineWidth_(LIMB_W)
    path.stroke()
    r = 3.5 if hand else 4.5
    oval = NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect(x2 - r, y2 - r, r * 2, r * 2))
    NSColor.whiteColor().set()
    oval.fill()
    DARK.set()
    oval.setLineWidth_(1.5)
    oval.stroke()


def draw_eyes(cx, cy, angle):
    perp = angle + math.pi / 2
    for side in (1, -1):
        ex = cx + math.cos(angle) * 10 + math.cos(perp) * 8 * side
        ey = cy + math.sin(angle) * 10 + math.sin(perp) * 8 * side
        r = 6
        oval = NSBezierPath.bezierPathWithOvalInRect_(
            NSMakeRect(ex - r, ey - r, r * 2, r * 2))
        NSColor.whiteColor().set()
        oval.fill()
        DARK.set()
        oval.setLineWidth_(1.2)
        oval.stroke()
        pr = 3
        px = ex + math.cos(angle) * 2.5
        py = ey + math.sin(angle) * 2.5
        pupil = NSBezierPath.bezierPathWithOvalInRect_(
            NSMakeRect(px - pr, py - pr, pr * 2, pr * 2))
        DARK.set()
        pupil.fill()


def draw_sweat(cx, cy, angle, frame):
    if frame % 4 != 0:
        return
    blue = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.3, 0.6, 1.0, 0.7)
    perp = angle + math.pi / 2
    for i in range(2):
        off = (i - 0.5) * 18
        dx = -math.cos(angle) * 38 + math.cos(perp) * off
        dy = -math.sin(angle) * 38 + math.sin(perp) * off
        sx, sy = cx + dx, cy + dy
        drop = NSBezierPath.bezierPath()
        drop.moveToPoint_((sx, sy - 4))
        drop.curveToPoint_controlPoint1_controlPoint2_(
            (sx, sy + 4), (sx - 3, sy), (sx + 3, sy))
        blue.set()
        drop.fill()


def draw_icon_limbs(ic):
    cx = ic["x"] + ICON_HALF
    cy = ic["y"] + ICON_HALF
    a = ic["angle"]
    f = ic["frame"]
    swing = math.sin(f * math.pi / 2) * 0.7
    perp = a + math.pi / 2

    for side in (1, -1):
        sx = cx + math.cos(perp) * ICON_HALF * 0.78 * side
        sy = cy + math.sin(perp) * ICON_HALF * 0.78 * side
        arm_a = a + swing * side
        draw_limb(sx, sy,
                  sx + math.cos(arm_a) * ARM_LEN,
                  sy + math.sin(arm_a) * ARM_LEN, hand=True)

    back = a + math.pi
    for side in (1, -1):
        hx = cx + math.cos(back) * ICON_HALF * 0.45 + math.cos(perp) * ICON_HALF * 0.32 * side
        hy = cy + math.sin(back) * ICON_HALF * 0.45 + math.sin(perp) * ICON_HALF * 0.32 * side
        leg_a = back + swing * (-side) * 0.9
        draw_limb(hx, hy,
                  hx + math.cos(leg_a) * LEG_LEN,
                  hy + math.sin(leg_a) * LEG_LEN, hand=False)

    draw_eyes(cx, cy, a)
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

        global DARK
        DARK = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.15, 0.15, 0.15, 1)

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
                ic["fleeing"] = True
                ic["frame"] = (ic["frame"] + 1) % 8
                nx, ny = dx / dist, dy / dist
                ic["angle"] = math.atan2(ny, nx)
                new_x = ic["x"] + nx * FLEE_SPEED
                new_y = ic["y"] + ny * FLEE_SPEED
                ic["x"] = max(10, min(new_x, self._sw - ICON_SIZE - 10))
                ic["y"] = max(MENU_BAR_H, min(new_y, self._sh - ICON_SIZE - DOCK_H))
            else:
                ic["fleeing"] = False
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
