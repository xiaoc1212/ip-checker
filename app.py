"""Native Windows desktop IP checker. All network work stays off the UI thread."""
import ctypes
import datetime
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
import webbrowser

from network import (ROOT, result, settings, save_settings, run_checks, gate,
                     controller, classify_connections, save_report)

BG = '#f3f5fa'
INK = '#192334'
MUTED = '#66748a'
COLORS = {'pass': '#167452', 'fail': '#bd3535', 'unknown': '#9b6c16', 'info': '#53657f', 'running': '#365cd8'}
LABELS = {'pass': '已确认', 'fail': '异常', 'unknown': '未确认', 'info': '参考', 'running': '检测中'}
TITLES = {'claude': 'Claude 固定出口', 'system': '系统接管出口', 'general': '普通网站出口',
          'guard': '正在生效的固定规则', 'client': 'Claude 客户端', 'geo': '出口归属信息'}


class IPDesk:
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue()
        self.report = None
        self.busy = False
        self.monitoring = False
        self.history = {}
        self.launch_after = None
        self.cards = {}
        self.root.title('工作台 · IP 检测')
        self.root.geometry('1100x900')
        self.root.minsize(920, 780)
        self.root.configure(bg=BG)
        self.root.protocol('WM_DELETE_WINDOW', self.root.destroy)
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('Treeview', font=('Microsoft YaHei UI', 9), rowheight=29, background='white', fieldbackground='white')
        style.configure('Treeview.Heading', font=('Microsoft YaHei UI', 9), background='#eaf0f8')
        head = tk.Frame(root, bg='#1a2742', padx=24, pady=20)
        head.pack(fill='x')
        tk.Label(head, text='IP 检测', fg='white', bg='#1a2742', font=('Microsoft YaHei UI', 22, 'bold')).pack(anchor='w')
        tk.Label(head, text='固定出口核验 · Claude 客户端连接观察', fg='#c1cee5', bg='#1a2742', font=('Microsoft YaHei UI', 10)).pack(anchor='w', pady=(5, 0))
        toolbar = tk.Frame(root, bg=BG, padx=22, pady=14)
        toolbar.pack(fill='x')
        tk.Label(toolbar, text='目标固定 IP', bg=BG, fg=INK, font=('Microsoft YaHei UI', 10)).pack(side='left')
        self.expected = tk.StringVar(value=settings()['expected_ip'])
        tk.Entry(toolbar, textvariable=self.expected, width=18, font=('Segoe UI', 11), relief='solid', bd=1).pack(side='left', padx=10, ipady=5)
        self.check_button = self.button(toolbar, '一键检测', self.check, primary=True)
        self.check_button.pack(side='left', padx=4)
        self.launch_button = self.button(toolbar, '检测并启动 Claude', lambda: self.check('native'))
        self.launch_button.pack(side='left', padx=4)
        self.button(toolbar, 'Claude 网页', lambda: self.check('web')).pack(side='left', padx=4)
        self.button(toolbar, '打开 Clash', lambda: os.startfile(r'D:\Clash Verge\clash-verge.exe')).pack(side='right')
        self.status = tk.Label(root, text='等待检测。安装原生客户端后，本工具会自动识别。', bg=BG, fg=MUTED,
                               font=('Microsoft YaHei UI', 10), anchor='w', padx=24)
        self.status.pack(fill='x')
        scroll_area = tk.Frame(root, bg=BG)
        scroll_area.pack(fill='both', expand=True)
        canvas = tk.Canvas(scroll_area, bg=BG, highlightthickness=0, height=450)
        scrollbar = ttk.Scrollbar(scroll_area, orient='vertical', command=canvas.yview)
        scrollbar.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)
        canvas.configure(yscrollcommand=scrollbar.set)
        body = tk.Frame(canvas, bg=BG)
        body_window = canvas.create_window((0, 0), window=body, anchor='nw')
        body.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda e: canvas.itemconfigure(body_window, width=e.width))
        root.bind('<MouseWheel>', lambda e: canvas.yview_scroll(-int(e.delta / 120), 'units'))
        grid = tk.Frame(body, bg=BG, padx=18, pady=10)
        grid.pack(fill='x')
        grid.columnconfigure(0, weight=1, uniform='cards')
        grid.columnconfigure(1, weight=1, uniform='cards')
        for i, (key, title) in enumerate(TITLES.items()):
            card = tk.Frame(grid, bg='white', highlightbackground='#e0e6ef', highlightthickness=1, padx=15, pady=10)
            card.grid(row=i//2, column=i%2, sticky='nsew', padx=5, pady=5)
            top = tk.Frame(card, bg='white')
            top.pack(fill='x')
            tk.Label(top, text=title, bg='white', fg=MUTED, font=('Microsoft YaHei UI', 10)).pack(side='left')
            badge = tk.Label(top, text='未检测', bg='white', fg=MUTED, font=('Microsoft YaHei UI', 9))
            badge.pack(side='right')
            value = tk.Label(card, text='—', bg='white', fg=INK, font=('Microsoft YaHei UI', 16, 'bold'), anchor='w')
            value.pack(fill='x', pady=(6, 4))
            detail = tk.Label(card, text='点击上方「一键检测」', bg='white', fg=MUTED, font=('Microsoft YaHei UI', 9),
                              anchor='nw', justify='left', wraplength=465, height=3)
            detail.pack(fill='x')
            card.bind('<Configure>', lambda e, v=value, d=detail: (v.configure(wraplength=max(100, e.width-32)), d.configure(wraplength=max(100, e.width-32))))
            self.cards[key] = (badge, value, detail)
        lower = tk.Frame(body, bg=BG, padx=24)
        lower.pack(fill='both', expand=True)
        title_row = tk.Frame(lower, bg=BG)
        title_row.pack(fill='x', pady=(0, 6))
        tk.Label(title_row, text='客户端连接观察', bg=BG, fg=INK, font=('Microsoft YaHei UI', 11, 'bold')).pack(side='left')
        self.watch_label = tk.Label(title_row, text='每 5 秒观察一次，仅列出 Claude 程序及其目录内的连接', bg=BG, fg=MUTED, font=('Microsoft YaHei UI', 9))
        self.watch_label.pack(side='right')
        self.table = ttk.Treeview(lower, columns=('time', 'process', 'host', 'route', 'state'), show='headings', height=4)
        for name, label, width in [('time', '观察时间', 85), ('process', '程序', 120), ('host', '目标', 230), ('route', '经过的代理', 370), ('state', '结论', 100)]:
            self.table.heading(name, text=label)
            self.table.column(name, width=width, minwidth=60, stretch=True)
        self.table.tag_configure('fail', foreground=COLORS['fail'])
        self.table.pack(fill='both', expand=True)
        self.note = tk.Label(lower, text='尚未观察到客户端连接。预检成功不代表客户端连接已验证。', bg=BG, fg=MUTED,
                             font=('Microsoft YaHei UI', 9), anchor='w', justify='left', wraplength=1030)
        self.note.pack(fill='x', pady=(6, 8))
        foot = tk.Frame(root, bg='#e8edf5', padx=24, pady=10)
        foot.pack(fill='x')
        tk.Label(foot, text='检测范围：当前请求与代理运行规则。DNS / WebRTC 泄露未实测；未部署代理退出后的系统断网保护。',
                 bg='#e8edf5', fg=MUTED, font=('Microsoft YaHei UI', 9), anchor='w', justify='left', wraplength=840).pack(side='left')
        self.button(foot, '查看检测报告', self.open_report).pack(side='right')
        self.root.after(100, self.poll)
        self.root.after(300, self.check)
        self.root.after(1500, self.monitor)

    @staticmethod
    def button(parent, text, command, primary=False):
        return tk.Button(parent, text=text, command=command, font=('Microsoft YaHei UI', 9), padx=12, pady=7,
                         relief='flat', bg='#365cd8' if primary else '#e4eaf4', fg='white' if primary else INK,
                         activebackground='#cbd8f2', cursor='hand2')

    def update_card(self, key, data):
        badge, value, detail = self.cards[key]
        color = COLORS[data['state']]
        badge.configure(text=LABELS[data['state']], fg=color)
        value.configure(text=data['value'], fg=color if data['state'] in ('pass', 'fail') else INK)
        detail.configure(text=data['detail'])

    def check(self, launch=None):
        if self.busy:
            return
        expected = self.expected.get().strip()
        try:
            save_settings(expected)
        except Exception:
            messagebox.showerror('目标 IP', '请输入有效的 IPv4 地址，例如 161.77.11.188。')
            return
        self.busy = True
        self.report = None
        self.launch_after = launch
        self.check_button.configure(state='disabled')
        self.launch_button.configure(state='disabled')
        self.status.configure(text='正在检查运行配置与实际出口…', fg=COLORS['running'])
        for key in self.cards:
            self.update_card(key, result('running', '检测中…', '本次重新请求，不沿用上次通过状态。'))
        def work():
            try:
                report = run_checks(expected, lambda key, value: self.events.put(('card', (key, value))))
                save_report(report)
                self.events.put(('done', report))
            except Exception as exc:
                self.events.put(('error', str(exc)))
        threading.Thread(target=work, daemon=True).start()

    def monitor(self):
        if not self.monitoring:
            self.monitoring = True
            def work():
                try:
                    self.events.put(('connections', classify_connections(controller(connections=True))))
                except Exception as exc:
                    self.events.put(('monitor_error', str(exc)))
                finally:
                    self.events.put(('monitor_done', None))
            threading.Thread(target=work, daemon=True).start()
        self.root.after(5000, self.monitor)

    def show_connections(self, connections):
        now = time.time()
        for conn in connections:
            self.history[conn['id']] = conn
        self.history = {key: value for key, value in self.history.items() if now - value['observed'] < 300}
        self.table.delete(*self.table.get_children())
        for conn in sorted(self.history.values(), key=lambda c: c['observed'], reverse=True)[:40]:
            label = {'pass': '已走固定组', 'fail': '路径异常', 'local': '本地直连'}[conn['state']]
            self.table.insert('', 'end', values=(datetime.datetime.fromtimestamp(conn['observed']).strftime('%H:%M:%S'),
                              conn['process'], conn['host'], ' → '.join(reversed(conn['chains'])), label), tags=(conn['state'],))
        if any(c['state'] == 'fail' for c in connections):
            self.note.configure(text='发现客户端连接未经过固定出口组，请检查上述路径。', fg=COLORS['fail'])
        elif connections:
            self.note.configure(text='正在观察客户端连接；表格保留最近 5 分钟记录。链路命中不等于目标站点已回报出口 IP。', fg=COLORS['pass'])
        else:
            self.note.configure(text='当前没有观察到活跃的客户端连接；记录只代表所列时间，不能证明所有历史请求。', fg=MUTED)
        self.watch_label.configure(text='最近观察 ' + datetime.datetime.now().strftime('%H:%M:%S'))

    def finish(self, report):
        self.report = report
        self.busy = False
        self.check_button.configure(state='normal')
        self.launch_button.configure(state='normal')
        ok, text = gate(report, self.expected.get().strip())
        when = datetime.datetime.fromtimestamp(report['finished']).strftime('%H:%M:%S')
        self.status.configure(text=f'{text} · {when} · {report["system_proxy"]}', fg=COLORS['pass' if ok else 'unknown'])
        pending, self.launch_after = self.launch_after, None
        if pending:
            if not ok:
                messagebox.showwarning('暂未启动', text + '。请查看检测卡片。')
            elif pending == 'native' and not report.get('native'):
                messagebox.showinfo('尚未安装', '没有发现原生 Claude 客户端。当前固定出口规则已预留；安装后再次点击此按钮。')
            else:
                # Recheck runtime immediately before launch, without blocking the UI.
                def launch_work():
                    from network import validate_guard
                    try:
                        if validate_guard(controller())['state'] != 'pass':
                            raise RuntimeError('运行规则在检测后发生变化，已停止启动')
                        self.events.put(('launch_ready', (pending, report)))
                    except Exception as exc:
                        self.events.put(('launch_error', str(exc)))
                threading.Thread(target=launch_work, daemon=True).start()

    def launch_ready(self, pending, report):
        if not gate(report, self.expected.get().strip())[0]:
            messagebox.showwarning('暂未启动', '检测结果已变化或过期，请重新检测。')
            return
        try:
            tz_env = {**os.environ, 'TZ': 'America/New_York'}
            os.environ['TZ'] = 'America/New_York'
            if pending == 'web':
                edge_candidates = [
                    Path(r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'),
                    Path(r'C:\Program Files\Microsoft\Edge\Application\msedge.exe'),
                ]
                edge_exe = next((p for p in edge_candidates if p.is_file()), None)
                safe_dir = Path(r'D:\claude-safe-launcher')
                if edge_exe and safe_dir.is_dir():
                    subprocess.Popen([
                        str(edge_exe),
                        f'--user-data-dir={safe_dir / "browser-profile"}',
                        f'--load-extension={safe_dir}',
                        '--lang=en-US',
                        '--force-webrtc-ip-handling-policy=disable_non_proxied_udp',
                        '--no-first-run',
                        '--no-default-browser-check',
                        'https://claude.ai/new',
                    ], env=tz_env, close_fds=True)
                else:
                    webbrowser.open('https://claude.ai/new')
            else:
                app = report['native']
                if app['kind'] == 'aumid':
                    os.startfile('shell:AppsFolder\\' + app['target'])
                else:
                    subprocess.Popen([app['target']], env=tz_env, close_fds=True)
        except Exception as exc:
            messagebox.showerror('启动未完成', str(exc))

    def poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == 'card': self.update_card(*value)
                elif kind == 'done': self.finish(value)
                elif kind == 'connections': self.show_connections(value)
                elif kind == 'monitor_done': self.monitoring = False
                elif kind == 'monitor_error':
                    self.watch_label.configure(text='连接观察暂不可用')
                    self.note.configure(text=value + '；当前无法核验客户端实际连接。', fg=COLORS['unknown'])
                elif kind == 'launch_ready': self.launch_ready(*value)
                elif kind == 'launch_error': messagebox.showwarning('暂未启动', value)
                elif kind == 'error':
                    self.busy = False
                    self.launch_after = None
                    self.check_button.configure(state='normal')
                    self.launch_button.configure(state='normal')
                    self.status.configure(text='检测未完成：' + value, fg=COLORS['fail'])
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def open_report(self):
        path = ROOT / 'reports' / 'last-check.json'
        if path.exists():
            subprocess.Popen(['notepad.exe', str(path)])
        else:
            messagebox.showinfo('检测报告', '完成一次检测后生成报告。')


if __name__ == '__main__':
    import sys
    from network import python_exe
    if Path(sys.executable).name.lower() == 'pythonw.exe':
        subprocess.Popen([python_exe(), str(Path(__file__).resolve()), *sys.argv[1:]],
                         cwd=ROOT, creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        from dashboard import main
        main()
