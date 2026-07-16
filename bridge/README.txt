Companion Bridge (Windows)
==========================

1. Unzip this folder anywhere (Desktop is fine).
2. Double-click run_bridge.bat
3. In the app:
   - Railway URL  = your Railway app URL
   - API Secret   = same as Railway API_SECRET (if set)
   - Companion Host / OSC Port = machine running Bitfocus Companion
4. Click START (or TEST OSC first).

Requirements
- Python 3 installed (https://www.python.org/downloads/)
- No pip packages needed (stdlib only)
- Companion 3.5.5+ OSC listener enabled (port 12321 by default)

OSC format used:
  /location/{page}/{row}/{column}/press

Feedback (button color / text / pressed):
- Bridge also connects to Companion Satellite TCP port 16622
- Companion 4.3+: automatic per-button subscriptions
- Companion 4.2.x: creates "CB Feedback P#" surfaces — in Companion → Surfaces,
  set each of those to the matching page number
