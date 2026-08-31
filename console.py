# -*- coding: utf-8 -*-
"""帝王花 · 桌面挂件控制台。

她沿着屏幕的某一条边直线爬过去，整个身子爬出屏幕后，随机换一个角落、
随机换一个方向重新钻进来。不拐弯。

控制窗口可以实时调数量（最多 10 只）、大小和速度。
Ctrl+Alt+P 暂停 / 继续，Ctrl+Alt+Q 退出，关掉控制窗口也是退出。
"""
import argparse, ctypes, os, queue, random, threading, tkinter as tk
from tkinter import font as tkfont

from PIL import Image, ImageTk

from pet import HERE, KEY_HEX, HK_QUIT, hotkey_thread, load_frames, to_keyed_photo

NAME = '帝王花 一几一几'

# 配色全部取自 assets/sprite.png 里她本人的像素，不是凭感觉调的
HAIR = '#C5AEE1'          # 主调发色
HAIR_HI = '#D8C3F2'       # 发丝高光
PINK = '#D8B8E8'          # 粉紫，裙摆和发梢那一层
DEEP = '#8F77B0'          # 发影
INK = '#4A3866'           # 正文
MUTED = '#A091BB'         # 次要文字
BG = '#F7F1FA'            # 窗口底：把发色提亮到接近白
TROUGH = '#EADCF3'        # 滑轨底槽
STAGE = '#EDE2F5'         # 预览条底色
LACE = '#FFFFFF'          # 蕾丝白

COUNT_MIN, COUNT_MAX, COUNT_DEF = 1, 10, 1
SIZE_MIN, SIZE_MAX, SIZE_DEF = 80, 600, 200
SPEED_MIN, SPEED_MAX, SPEED_DEF = 0.3, 6.0, 1.0
WAIT_MIN, WAIT_MAX = 1.5, 5.0            # 爬出屏幕后随机歇多久再从新角落出现
STAGE_W = 300                            # 预览条宽度

ROTATE = {90: Image.ROTATE_270, 180: Image.ROTATE_180, 270: Image.ROTATE_90}


def rgb(hexstr):
    return tuple(int(hexstr[i:i + 2], 16) for i in (1, 3, 5))


def paint_titlebar(root, caption, text, border):
    """Win11 的 DWM 接口，把系统标题栏也染成她的颜色。失败就维持默认样式。"""
    try:
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        for attr, col in ((35, caption), (36, text), (34, border)):
            r, g, b = rgb(col)
            v = ctypes.c_int(b << 16 | g << 8 | r)     # COLORREF 是 0x00BBGGRR
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), 4)
    except Exception:
        pass


def ui_scale(root):
    """Tk 会自动按 DPI 放大字号，但写死的像素尺寸不会，这里补上这个倍率。

    必须在建 Tk 之前设好 DPI 感知（main 里做了），否则这里读到的是缩放后的
    逻辑 DPI 恒等于 96，整个窗口在高分屏上就会偏小。
    """
    try:
        return max(1.0, min(3.0, root.winfo_fpixels('1i') / 96.0))
    except Exception:
        return 1.0


def pick_font(root):
    fams = set(tkfont.families(root))
    for f in ('Microsoft YaHei UI', 'Microsoft YaHei', 'Segoe UI'):
        if f in fams:
            return f
    return 'TkDefaultFont'


# ---------------------------------------------------------------- 路线

def routes(sw, sh):
    """八条可能的路线：(朝向角, 是否镜像, 入场角, 前进方向, 这条边的长度)。

    朝向角是身体的顺时针旋转角，决定她趴在哪条边上；未镜像时她朝
    R(phi)·(-1,0) 爬，镜像后反向。四个角各连着两条边，正好八种走法。
    """
    return (
        (0,   False, (sw, sh), (-1, 0), sw),   # 右下角进，沿底边向左
        (0,   True,  (0,  sh), (1,  0), sw),   # 左下角进，沿底边向右
        (90,  False, (0,  sh), (0, -1), sh),   # 左下角进，沿左边向上
        (90,  True,  (0,   0), (0,  1), sh),   # 左上角进，沿左边向下
        (180, False, (0,   0), (1,  0), sw),   # 左上角进，沿顶边向右
        (180, True,  (sw,  0), (-1, 0), sw),   # 右上角进，沿顶边向左
        (270, False, (sw,  0), (0,  1), sh),   # 右上角进，沿右边向下
        (270, True,  (sw, sh), (0, -1), sh),   # 右下角进，沿右边向上
    )


def inward_normal(phi):
    return {0: (0, -1), 90: (1, 0), 180: (0, 1), 270: (-1, 0)}[phi]


class Frames:
    """按当前大小缩放好的精灵，所有挂件共用一份。"""

    def __init__(self, size):
        self.resize(size)

    def resize(self, size):
        self.frames, self.step, self.frame_ms, self.dw, self.dh = load_frames(size)
        self.cache = {}
        # 按约 60MB 封顶，够放下同时用到的几个朝向
        self.cap = max(128, int(60e6 / (self.dw * self.dh * 4)))

    def photo(self, idx, phi, mirror):
        key = (idx, phi, mirror)
        hit = self.cache.get(key)
        if hit is None:
            img = self.frames[idx]
            if mirror:                            # 按需镜像，不再整份存一套
                img = img.transpose(Image.FLIP_LEFT_RIGHT)
            if phi:
                img = img.transpose(ROTATE[phi])
            hit = to_keyed_photo(img)
            if len(self.cache) >= self.cap:       # 正在显示的那张由挂件自己持有，
                for k in list(self.cache)[:self.cap // 2]:   # 被清掉也不会变空白
                    del self.cache[k]
            self.cache[key] = hit
        return hit

    def size_at(self, phi):
        return (self.dw, self.dh) if phi % 180 == 0 else (self.dh, self.dw)


class Crawler:
    """一只挂件：一个透明置顶的小窗口，加上"爬一趟、歇一会儿"的状态。"""

    def __init__(self, app):
        self.app = app
        self.win = tk.Toplevel(app.root)
        self.win.overrideredirect(True)
        self.win.attributes('-topmost', True)
        self.win.attributes('-transparentcolor', KEY_HEX)
        self.win.configure(bg=KEY_HEX)
        self.label = tk.Label(self.win, bg=KEY_HEX, bd=0, highlightthickness=0)
        self.label.pack()
        self.label.bind('<Button-1>', lambda e: app.toggle())
        self.label.bind('<Button-3>', lambda e: app.show_console())
        self.current = None                       # 撑住 PhotoImage，别让它被回收
        self.i = random.randrange(len(app.fr.frames))
        self.route = None
        self.wait = random.uniform(0.0, WAIT_MAX)  # 错开出场时间，免得十只一起冒头
        self.win.withdraw()

    def destroy(self):
        self.win.destroy()

    def start_run(self, fr):
        pool = self.app.routes
        route = random.choice(pool)
        while route is self.route and len(pool) > 1:   # 别连着走同一条
            route = random.choice(pool)
        self.route = route
        self.t = -fr.dw / 2.0                     # 头刚好顶在入场角上，身子还在屏幕外
        self.win.deiconify()

    def tick(self, fr, speed, secs):
        if self.route is None:
            self.wait -= secs
            if self.wait > 0:
                return
            self.start_run(fr)                    # 起跑这一帧就画出来，不空一拍
        else:
            self.i = (self.i + 1) % len(fr.frames)
            self.t += fr.step * speed
            if self.t - fr.dw / 2.0 > self.route[4]:   # 尾巴也出了屏幕，歇一会儿再来
                self.route = None
                self.wait = random.uniform(WAIT_MIN, WAIT_MAX)
                self.win.withdraw()
                return

        phi, mirror, origin, dirv, length = self.route
        gx = origin[0] + dirv[0] * self.t
        gy = origin[1] + dirv[1] * self.t
        nx, ny = inward_normal(phi)
        w, h = fr.size_at(phi)
        self.current = fr.photo(self.i, phi, mirror)
        self.label.configure(image=self.current)
        self.win.geometry('%dx%d+%d+%d' % (
            w, h,
            round(gx + nx * fr.dh / 2.0 - w / 2.0),
            round(gy + ny * fr.dh / 2.0 - h / 2.0)))


# ---------------------------------------------------------------- 控件

class Slider(tk.Canvas):
    """自绘滑块。ttk 的几个主题都调不出圆头滑轨和这种配色，索性自己画。"""

    def __init__(self, master, lo, hi, value, command, width=196, height=26, radius=8):
        tk.Canvas.__init__(self, master, width=width, height=height,
                           bg=BG, bd=0, highlightthickness=0, cursor='hand2')
        self.lo, self.hi, self.command, self.R = lo, hi, command, radius
        self.x0, self.x1 = self.R + 2, width - self.R - 2
        cy = height // 2
        bar = max(3, int(radius * 0.9))
        self.trough = self.create_line(self.x0, cy, self.x1, cy,
                                       width=bar, fill=TROUGH, capstyle='round')
        self.done = self.create_line(self.x0, cy, self.x0, cy,
                                     width=bar, fill=HAIR, capstyle='round')
        self.knob = self.create_oval(0, 0, 0, 0, fill=LACE, outline=DEEP, width=2)
        self.cy = cy
        self.value = value
        self.redraw()
        for ev in ('<Button-1>', '<B1-Motion>'):
            self.bind(ev, self.on_drag)

    def redraw(self):
        f = (self.value - self.lo) / float(self.hi - self.lo)
        x = self.x0 + f * (self.x1 - self.x0)
        self.coords(self.done, self.x0, self.cy, max(x, self.x0 + 0.1), self.cy)
        self.coords(self.knob, x - self.R, self.cy - self.R, x + self.R, self.cy + self.R)

    def on_drag(self, e):
        f = (e.x - self.x0) / float(self.x1 - self.x0)
        self.value = self.lo + max(0.0, min(1.0, f)) * (self.hi - self.lo)
        self.redraw()
        self.command()

    def get(self):
        return self.value

    def set(self, v):
        self.value = max(self.lo, min(self.hi, v))
        self.redraw()


class Button(tk.Label):
    """扁平按钮，鼠标悬停变色。主按钮实心，次按钮描边。"""

    def __init__(self, master, text, command, font, fill, hover, fg, border=None):
        tk.Label.__init__(self, master, text=text, width=9, bg=fill, fg=fg, font=font,
                          bd=0, padx=6, pady=7, cursor='hand2',
                          highlightthickness=1 if border else 0,
                          highlightbackground=border or fill,
                          highlightcolor=border or fill)
        self.fill, self.hover, self.command = fill, hover, command
        self.bind('<Enter>', lambda e: self.configure(bg=self.hover))
        self.bind('<Leave>', lambda e: self.configure(bg=self.fill))
        self.bind('<Button-1>', lambda e: self.command())


# ---------------------------------------------------------------- 主窗口

class Console:
    def __init__(self, args):
        self.root = tk.Tk()
        self.root.title(NAME)
        self.root.configure(bg=BG)
        self.root.resizable(False, False)
        self.root.protocol('WM_DELETE_WINDOW', self.quit)
        self.fam = pick_font(self.root)
        self.ui = ui_scale(self.root)
        self.px = lambda v: max(1, int(round(v * self.ui)))

        self.fr = Frames(args.size)
        self.speed = args.speed
        self.paused = False
        self.pets = []
        self.q = queue.Queue()
        self.pending = None

        self.screen = (self.root.winfo_screenwidth(), self.root.winfo_screenheight())
        self.routes = routes(*self.screen)
        self.ticks = 0
        self._build_ui(args)
        self.set_count(args.count)

        paint_titlebar(self.root, HAIR, INK, HAIR)
        threading.Thread(target=hotkey_thread, args=(self.q,), daemon=True).start()
        self.tick()

    # -- 界面 --------------------------------------------------------
    def _build_ui(self, args):
        px = self.px
        body = tk.Frame(self.root, bg=BG, padx=px(18), pady=px(14))
        body.pack()

        head = tk.Frame(body, bg=BG)
        head.grid(row=0, column=0, columnspan=3, sticky='w', pady=(0, px(12)))
        self._load_art()
        if self.avatar:
            tk.Label(head, image=self.avatar, bg=BG, bd=0).pack(side='left', padx=(0, px(13)))
        txt = tk.Frame(head, bg=BG)
        txt.pack(side='left')
        tk.Label(txt, text=NAME, bg=BG, fg=DEEP,
                 font=(self.fam, 17, 'bold')).pack(anchor='w')


        self._build_stage(body)

        self.sliders = {}
        rows = (('数量', 'count', COUNT_MIN, COUNT_MAX, args.count, '只'),
                ('大小', 'size',  SIZE_MIN,  SIZE_MAX,  args.size,  'px'),
                ('速度', 'speed', SPEED_MIN, SPEED_MAX, args.speed, '×'))
        for r, (text, name, lo, hi, init, unit) in enumerate(rows, start=2):
            tk.Label(body, text=text, bg=BG, fg=INK, font=(self.fam, 10)).grid(
                row=r, column=0, sticky='w', padx=(0, px(10)), pady=px(3))
            sl = Slider(body, lo, hi, init, lambda n=name: self.on_slide(n),
                        width=px(196), height=px(26), radius=px(8))
            sl.grid(row=r, column=1, pady=px(3))
            lab = tk.Label(body, bg=BG, fg=DEEP, font=(self.fam, 10, 'bold'),
                           width=6, anchor='e')
            lab.grid(row=r, column=2, sticky='e', padx=(px(10), 0), pady=px(3))
            self.sliders[name] = (sl, lab, unit)
            self.show_value(name)

        btns = tk.Frame(body, bg=BG)
        btns.grid(row=5, column=0, columnspan=3, pady=(px(14), 0))
        bf = (self.fam, 10)
        self.pause_btn = Button(btns, '暂停', self.toggle, bf, DEEP, '#7D6699', LACE)
        self.pause_btn.pack(side='left', padx=(0, px(8)))
        Button(btns, '退出', self.quit, bf, LACE, TROUGH, DEEP, border=HAIR).pack(side='left')

        tk.Label(body, text='Ctrl+Alt+P 暂停　Ctrl+Alt+Q 退出　右键点她可以叫回本窗口',
                 bg=BG, fg=MUTED, font=(self.fam, 8)).grid(
            row=6, column=0, columnspan=3, pady=(px(12), 0))

    def _build_stage(self, body):
        """窗口里的小舞台：她在原地爬，正好是复位后的素材本来的样子。"""
        src, _, _, w, h = load_frames(min(STAGE_W * self.ui, 440))
        plate = rgb(STAGE)
        self.stage_src = []
        for f in src:
            im = Image.new('RGB', f.size, plate)
            im.paste(f, (0, 0), f)
            self.stage_src.append(im)
        self.stage_cache = [None] * len(src)
        self.stage_i = 0
        self.stage = tk.Label(body, bg=STAGE, bd=0, highlightthickness=0)
        self.stage.grid(row=1, column=0, columnspan=3, pady=(0, self.px(12)))

    def _load_art(self):
        """立绘裁出来的头像：窗口里一个圆的，标题栏和任务栏一个方的。"""
        self.avatar = self.icon = None
        art = lambda n: os.path.join(HERE, 'assets', n)
        try:
            self.avatar = ImageTk.PhotoImage(
                Image.open(art('avatar.png')).resize((self.px(56),) * 2, Image.LANCZOS))
        except Exception:
            pass
        try:
            self.icon = ImageTk.PhotoImage(
                Image.open(art('icon.png')).resize((64, 64), Image.LANCZOS))
            self.root.iconphoto(True, self.icon)
        except Exception:
            pass

    def stage_step(self):
        self.stage_i = (self.stage_i + 1) % len(self.stage_src)
        ph = self.stage_cache[self.stage_i]
        if ph is None:
            ph = self.stage_cache[self.stage_i] = ImageTk.PhotoImage(self.stage_src[self.stage_i])
        self.stage.configure(image=ph)

    def show_value(self, name):
        sl, lab, unit = self.sliders[name]
        v = sl.get()
        lab.configure(text=('%.1f%s' % (v, unit)) if name == 'speed' else '%d%s' % (round(v), unit))

    def on_slide(self, name):
        self.show_value(name)
        if name == 'speed':
            self.speed = self.sliders['speed'][0].get()
        elif name == 'count':
            self.set_count(round(self.sliders['count'][0].get()))
        else:                                     # 换大小要重新缩放 128 帧，拖动时先攒着
            if self.pending:
                self.root.after_cancel(self.pending)
            self.pending = self.root.after(250, self.apply_size)

    def apply_size(self):
        self.pending = None
        self.fr.resize(round(self.sliders['size'][0].get()))

    # -- 控制 --------------------------------------------------------
    def set_count(self, n):
        while len(self.pets) > n:
            self.pets.pop().destroy()
        while len(self.pets) < n:
            self.pets.append(Crawler(self))

    def toggle(self):
        self.paused = not self.paused
        self.pause_btn.configure(text='继续' if self.paused else '暂停')

    def show_console(self):
        self.root.deiconify()
        self.root.lift()

    def quit(self):
        self.root.destroy()

    # -- 主循环 ------------------------------------------------------
    def check_screen(self):
        """分辨率可能中途变（换显示器、插拔扩展坞、改缩放），旧路线会贴在
        不存在的边上。发现变化就重算路线，让所有挂件重新进场。"""
        now = (self.root.winfo_screenwidth(), self.root.winfo_screenheight())
        if now == self.screen:
            return
        self.screen = now
        self.routes = routes(*now)
        for p in self.pets:
            p.route = None
            p.wait = random.uniform(0.2, 1.5)
            p.win.withdraw()

    def tick(self):
        self.ticks += 1
        if self.ticks % 30 == 0:                  # 约每秒查一次，开销可忽略
            self.check_screen()

        while not self.q.empty():
            kind, val = self.q.get()
            if kind == 'hotkey':
                if val == HK_QUIT:
                    return self.quit()
                self.toggle()

        if not self.paused:
            secs = self.fr.frame_ms / 1000.0
            for pet in self.pets:
                pet.tick(self.fr, self.speed, secs)
            self.stage_step()

        self.root.after(self.fr.frame_ms, self.tick)


def main():
    p = argparse.ArgumentParser(description='%s · 桌面挂件控制台' % NAME)
    p.add_argument('--count', type=int, default=COUNT_DEF, help='初始只数 1~10')
    p.add_argument('--size', type=int, default=SIZE_DEF, help='初始身体长度像素')
    p.add_argument('--speed', type=float, default=SPEED_DEF, help='初始速度倍率')
    args = p.parse_args()
    args.count = max(COUNT_MIN, min(COUNT_MAX, args.count))
    args.size = max(SIZE_MIN, min(SIZE_MAX, args.size))
    args.speed = max(SPEED_MIN, min(SPEED_MAX, args.speed))

    for fn in (lambda: ctypes.windll.shcore.SetProcessDpiAwareness(2),
               lambda: ctypes.windll.user32.SetProcessDPIAware()):
        try:
            fn()
            break
        except Exception:
            pass

    Console(args).root.mainloop()


if __name__ == '__main__':
    main()
