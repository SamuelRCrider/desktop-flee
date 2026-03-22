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

# Run
venv/bin/python3 -u desktop_flee.py &
```

## Usage

**Start:**
```bash
venv/bin/python3 -u desktop_flee.py &
```

**Stop:**
```bash
pkill -f desktop_flee.py
```

## Tuning

Edit the constants at the top of `desktop_flee.py`:

| Constant | Default | Description |
|---|---|---|
| `FLEE_RADIUS` | 180 | How close (px) before icons panic |
| `FLEE_SPEED` | 18 | How fast icons run away (px/frame) |
| `RETURN_SPEED` | 4 | How fast they tiptoe back home |
| `FPS` | 30 | Animation frame rate |
