# Desktop Flee

Your macOS desktop icons sprout arms, legs, and googly eyes, then run away from your cursor.

https://github.com/user-attachments/assets/placeholder

## How it works

1. Captures the desktop wallpaper and each icon sprite via the macOS Accessibility and Quartz APIs
2. Renders a full-screen overlay just above the real desktop icons
3. When your cursor gets close, icons panic — they grow stick-figure limbs, sweat drops, and googly eyes, then flee in the opposite direction
4. When the cursor moves away, they cautiously tiptoe back to their original positions

## Requirements

- macOS
- Python 3.10+
- Terminal.app needs **Accessibility** and **Screen Recording** permissions (System Settings > Privacy & Security)

## Setup

```bash
# Clone
git clone https://github.com/samuelgodshall/desktop-flee.git
cd desktop-flee

# Create venv and install dependencies
python3 -m venv venv
venv/bin/pip install pyobjc-framework-Cocoa pyobjc-framework-Quartz pyobjc-framework-ApplicationServices
```

## Usage

### One-off (manual)

Run it in the foreground and stop it with Ctrl+C or `pkill`:

**Start:**
```bash
venv/bin/python3 -u desktop_flee.py
```

**Stop:**
```bash
# Ctrl+C in the terminal, or from another terminal:
pkill -f desktop_flee.py
```

### Persistent (launchd)

Use the included launchd agent to run at login and auto-restart if killed.

**Install and start:**
```bash
cp com.samuel.desktop-flee.plist ~/Library/LaunchAgents/
# Edit the plist to set the correct paths to your python and desktop_flee.py
launchctl load ~/Library/LaunchAgents/com.samuel.desktop-flee.plist
```

**Stop:**
```bash
launchctl unload ~/Library/LaunchAgents/com.samuel.desktop-flee.plist
```

## Tuning

Edit the constants at the top of `desktop_flee.py`:

| Constant | Default | Description |
|---|---|---|
| `FLEE_RADIUS` | 180 | How close (px) before icons panic |
| `FLEE_SPEED` | 18 | How fast icons run away (px/frame) |
| `RETURN_SPEED` | 4 | How fast they tiptoe back home |
| `FPS` | 30 | Animation frame rate |
