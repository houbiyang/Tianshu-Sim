"""Send a real native menu accelerator to an isolated X11 QA desktop.

Example: python tools/qa/desktop_key.py --display :96 --key comma
The q key sends Ctrl+Q; verify shutdown separately because menu defaults vary.
Never targets the user's normal display.
"""
import argparse
import ctypes as c
import re
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--display', required=True)
parser.add_argument('--key', choices=('comma', 'q', 'n', 'Escape'), required=True)
parser.add_argument('--window', help='QA dialog window id from xwininfo, e.g. 0x400002')
args = parser.parse_args()
if args.display in (':0', ':1'):
    parser.error('Use a separate QA Xvfb display')
tree = subprocess.check_output(['xwininfo', '-display', args.display, '-root', '-tree'], text=True)
window = re.search(r'(0x[0-9a-f]+) "GenTactix-DSH ·', tree)
if not window: raise RuntimeError('QA desktop window not found')
x = c.CDLL('libX11.so.6'); xt = c.CDLL('libXtst.so.6')
x.XOpenDisplay.argtypes = [c.c_char_p]; x.XOpenDisplay.restype = c.c_void_p
x.XSetInputFocus.argtypes = [c.c_void_p, c.c_ulong, c.c_int, c.c_ulong]
x.XStringToKeysym.argtypes = [c.c_char_p]; x.XStringToKeysym.restype = c.c_ulong
x.XKeysymToKeycode.argtypes = [c.c_void_p, c.c_ulong]; x.XKeysymToKeycode.restype = c.c_uint
x.XSync.argtypes = [c.c_void_p, c.c_int]; x.XCloseDisplay.argtypes = [c.c_void_p]
xt.XTestFakeKeyEvent.argtypes = [c.c_void_p, c.c_uint, c.c_int, c.c_ulong]
display = x.XOpenDisplay(args.display.encode())
if not display: raise RuntimeError('Cannot open QA display')
try:
    x.XSetInputFocus(display, int(args.window or window[1], 16), 1, 0)
    control = x.XKeysymToKeycode(display, x.XStringToKeysym(b'Control_L'))
    key = x.XKeysymToKeycode(display, x.XStringToKeysym(args.key.encode()))
    events = ((key, 1), (key, 0)) if args.key == 'Escape' else ((control, 1), (key, 1), (key, 0), (control, 0))
    for code, pressed in events:
        xt.XTestFakeKeyEvent(display, code, pressed, 0)
    x.XSync(display, 0)
finally: x.XCloseDisplay(display)
