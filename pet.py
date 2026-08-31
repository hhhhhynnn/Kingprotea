# -*- coding: utf-8 -*-
"""桌面挂件：紫发少女沿着屏幕四边爬行。

  左键点她      暂停 / 继续
  右键点她      弹出菜单（暂停、掉头、退出）
  Ctrl+Alt+P    全局暂停 / 继续
  Ctrl+Alt+Q    全局退出
"""
import argparse, ctypes, json, math, os, queue, sys, threading, tkinter as tk
from ctypes import wintypes

from PIL import Image, ImageTk

# 打包成 exe 之后素材被解到 _MEIPASS 里，直接用 __file__ 找不到
HERE = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
KEY_RGB = (255, 0, 255)          # 色键：被抠成透明的颜色，素材里不含品红
KEY_HEX = '#ff00ff'
ALPHA_T = 128                    # 不透明度低于此值的像素视为全透明（在半透明处截断）


# ---------------------------------------------------------------- 素材

def load_frames(target_w):
    meta = json.load(open(os.path.join(HERE, 'assets', 'meta.json')))
    sheet = Image.open(os.path.join(HERE, 'assets', 'sprite.png')).convert('RGBA')
    cw, ch, cols = meta['cell_w'], meta['cell_h'], meta['cols']
    f = target_w / cw
    dw, dh = max(1, round(cw * f)), max(1, round(ch * f))
    frames = []
    for i in range(meta['frames']):
        x, y = (i % cols) * cw, (i // cols) * ch
        frames.append(sheet.crop((x, y, x + cw, y + ch)).resize((dw, dh), Image.LANCZOS))
    return frames, meta['scroll_px_per_frame'] * f, meta['frame_ms'], dw, dh


def to_keyed_photo(img):
    """RGBA -> 透明区填成色键的 RGB PhotoImage。

    半透明边缘直接取其自身颜色而不与背景混合，避免出现品红或白色描边
    （convert('RGB') 只是丢掉 alpha，不会把颜色往黑里压）。

    这里刻意只用 PIL 不用 numpy：运行时就这一处需要逐像素操作，为它多打包
    23MB 的 numpy 不值。
    """
    mask = img.getchannel('A').point(lambda v: 255 if v >= ALPHA_T else 0)
    plate = Image.new('RGB', img.size, KEY_RGB)
    plate.paste(img.convert('RGB'), (0, 0), mask)
    return ImageTk.PhotoImage(plate)


# ---------------------------------------------------------------- 几何

class Path:
    """沿屏幕边框的一圈圆角矩形路径。s 是路径上的弧长，走完一圈等于 P。

    直角拐弯是做不到的：身体是一根 240 像素左右的硬棍子，贴着直角拐会有
    大半截甩到屏幕外。所以四个角改成半径 R 的圆弧，她沿弧线抄近路转过去，
    朝向角正好等于路径切线的转角，天然连续。

    s 递增时：从底边向左爬，再沿左边上行、顶边向右、右边下行。
    """

    LINE, ARC = 0, 1

    def __init__(self, w, h, margin, radius):
        x0, y0 = margin, margin
        x1, y1 = w - margin, h - margin
        R = self.R = min(radius, (x1 - x0) / 2, (y1 - y0) / 2)
        lw, lh = (x1 - x0) - 2 * R, (y1 - y0) - 2 * R     # 直线段长度
        arc = math.pi / 2 * R
        # (类型, 长度, 起点或圆心, 方向或起始极角, 起始朝向角)
        self.segs = (
            (self.LINE, lw,  (x1 - R, y1),      (-1, 0),  0.0),    # 底边，向左
            (self.ARC,  arc, (x0 + R, y1 - R),   90.0,    0.0),    # 左下角
            (self.LINE, lh,  (x0, y1 - R),      (0, -1),  90.0),   # 左边，向上
            (self.ARC,  arc, (x0 + R, y0 + R),  180.0,    90.0),   # 左上角
            (self.LINE, lw,  (x0 + R, y0),      (1, 0),   180.0),  # 顶边，向右
            (self.ARC,  arc, (x1 - R, y0 + R),  270.0,    180.0),  # 右上角
            (self.LINE, lh,  (x1, y0 + R),      (0, 1),   270.0),  # 右边，向下
            (self.ARC,  arc, (x1 - R, y1 - R),    0.0,    270.0),  # 右下角
        )
        self.P = sum(s[1] for s in self.segs)

    def at(self, s):
        """返回 (着地点, 顺时针朝向角)。朝向只取决于位置，与前进方向无关。"""
        s %= self.P
        for kind, length, origin, param, phi0 in self.segs:
            if s <= length:
                break
            s -= length
        if kind == self.LINE:
            return (origin[0] + param[0] * s, origin[1] + param[1] * s), phi0
        t = s / length                                     # 圆弧上的进度 0~1
        th = math.radians(param + 90.0 * t)
        return (origin[0] + self.R * math.cos(th), origin[1] + self.R * math.sin(th)), phi0 + 90.0 * t


def inward_normal(phi_deg):
    r = math.radians(phi_deg)
    return math.sin(r), -math.cos(r)  # 未旋转时朝上，即从底边指向屏幕内部


# ---------------------------------------------------------------- 全局热键

WM_HOTKEY, MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x0312, 0x0001, 0x0002, 0x4000
HK_QUIT, HK_PAUSE = 1, 2


def hotkey_thread(q):
    u = ctypes.windll.user32
    u.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    mods = MOD_CONTROL | MOD_ALT | MOD_NOREPEAT
    ok = u.RegisterHotKey(None, HK_QUIT, mods, 0x51)      # Q
    ok &= u.RegisterHotKey(None, HK_PAUSE, mods, 0x50)    # P
    if not ok:
        q.put(('warn', '全局热键注册失败（可能被其他程序占用），请用控制窗口的按钮，或点她本人'))
    msg = wintypes.MSG()
    while u.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
        if msg.message == WM_HOTKEY:
            q.put(('hotkey', msg.wParam))


# ---------------------------------------------------------------- 主体

class Pet:
    def __init__(self, args):
        self.frames, self.step, self.frame_ms, self.dw, self.dh = load_frames(args.size)
        self.mirrored = [f.transpose(Image.FLIP_LEFT_RIGHT) for f in self.frames]
        self.speed = args.speed
        self.reverse = args.reverse
        self.q = queue.Queue()

        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes('-topmost', True)
        self.root.attributes('-transparentcolor', KEY_HEX)
        self.root.configure(bg=KEY_HEX)
        self.label = tk.Label(self.root, bg=KEY_HEX, bd=0, highlightthickness=0)
        self.label.pack()

        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.path = Path(sw, sh, args.margin, args.corner or round(args.size * 0.8))
        self.s = args.start * self.path.P
        self.i = 0
        self.paused = False
        self.cache = {}
        self.current = None

        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label='暂停 / 继续', command=self.toggle)
        self.menu.add_command(label='掉头', command=self.flip)
        self.menu.add_separator()
        self.menu.add_command(label='退出', command=self.quit)
        for ev, fn in (('<Button-1>', lambda e: self.toggle()),
                       ('<Button-3>', self.popup),
                       ('<Escape>', lambda e: self.quit())):
            self.label.bind(ev, fn)
            self.root.bind(ev, fn)

        threading.Thread(target=hotkey_thread, args=(self.q,), daemon=True).start()
        self.tick()

    # -- 控制 --------------------------------------------------------
    def toggle(self):   self.paused = not self.paused
    def flip(self):     self.reverse = not self.reverse
    def popup(self, e): self.menu.tk_popup(e.x_root, e.y_root)
    def quit(self):     self.root.destroy()

    # -- 渲染 --------------------------------------------------------
    def photo(self, idx, phi):
        a = round(phi) % 360
        key = (idx, a, self.reverse)
        hit = self.cache.get(key)
        if hit is None:
            img = (self.mirrored if self.reverse else self.frames)[idx]
            if a % 90 == 0:                          # 正交方向用无损转置
                img = [img, img.transpose(Image.ROTATE_270), img.transpose(Image.ROTATE_180),
                       img.transpose(Image.ROTATE_90)][a // 90]
            elif a:
                img = img.rotate(-a, resample=Image.BICUBIC, expand=True)
            hit = to_keyed_photo(img)
            if a % 90 == 0:                          # 只常驻四个正交朝向
                self.cache[key] = hit
        return hit

    def tick(self):
        while not self.q.empty():
            kind, val = self.q.get()
            if kind == 'hotkey':
                if val == HK_QUIT:
                    self.quit()
                    return
                self.toggle()
            elif kind == 'warn':
                try:
                    print(val)
                except Exception:
                    pass

        if not self.paused:
            self.s += self.step * self.speed * (-1 if self.reverse else 1)
            self.i = (self.i + 1) % len(self.frames)

        (px, py), phi = self.path.at(self.s)
        nx, ny = inward_normal(phi)
        # 必须自己抓住引用：PhotoImage 一旦被回收，Tk 那边的图也跟着没了，
        # Label 会变成空白（圆弧上的角度不进缓存，只有这里持有它）。
        img = self.current = self.photo(self.i, phi)
        cx, cy = px + nx * self.dh / 2, py + ny * self.dh / 2
        self.label.configure(image=img)
        self.root.geometry('%dx%d+%d+%d' % (img.width(), img.height(),
                                            round(cx - img.width() / 2),
                                            round(cy - img.height() / 2)))
        self.root.after(self.frame_ms, self.tick)


def main():
    p = argparse.ArgumentParser(description='沿屏幕边框爬行的桌面挂件')
    p.add_argument('--size', type=int, default=240, help='身体长度像素，默认 240')
    p.add_argument('--speed', type=float, default=1.0, help='速度倍率，1.0 = 与动画同步')
    p.add_argument('--margin', type=int, default=0, help='离屏幕边缘的内缩像素')
    p.add_argument('--corner', type=int, default=0,
                   help='拐角圆弧半径，0 表示按身长自动取 0.8 倍')
    p.add_argument('--start', type=float, default=0.15, help='起始位置，0~1 表示绕行一周的比例')
    p.add_argument('--reverse', action='store_true', help='反方向爬')
    args = p.parse_args()

    for fn in (lambda: ctypes.windll.shcore.SetProcessDpiAwareness(2),
               lambda: ctypes.windll.user32.SetProcessDPIAware()):
        try:
            fn()
            break
        except Exception:
            pass

    Pet(args).root.mainloop()


if __name__ == '__main__':
    main()
