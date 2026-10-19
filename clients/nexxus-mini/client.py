#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Python 2.6 / Tk client; TLS delegated to a separately verified curl bundle."""
from __future__ import unicode_literals
import json
import os
import subprocess
import threading
import time
import math
import calendar
try:
    import Tkinter as tk
    import tkMessageBox as messagebox
    import Queue as queue
    from urlparse import urlsplit
except ImportError:
    import tkinter as tk
    from tkinter import messagebox
    import queue
    from urllib.parse import urlsplit

ROOT = os.path.dirname(os.path.abspath(__file__))


def config():
    with open(os.path.join(ROOT, 'config.json'), 'rb') as handle:
        value = json.loads(handle.read().decode('utf-8'))
    url = value.get('server', '')
    parts = urlsplit(url)
    if (parts.scheme != 'https' or not parts.hostname or parts.username or
            parts.password or parts.query or parts.fragment or parts.path not in ('', '/')):
        raise ValueError('Configura solo el origen HTTPS de Railway, sin ruta ni credenciales.')
    return value


def curl_quote(value):
    # curl config escaping, not shell escaping. No shell is ever launched.
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('\r', '\\r').replace('\n', '\\n') + '"'


def transport(settings, path, data=None, token=None):
    if token is not None and (not token or any(ord(c) < 33 or ord(c) > 126 for c in token)):
        raise ValueError('Pega un token de sesion valido, sin espacios ni saltos de linea.')
    binary = os.path.join(ROOT, 'transport', 'curl')
    ca = os.path.join(ROOT, 'transport', 'cacert.pem')
    if not os.path.isfile(binary) or not os.path.isfile(ca):
        raise ValueError('Falta el transporte TLS verificado. Consulta INSTALACION.md.')
    payload = json.dumps(data, ensure_ascii=True)
    options = '\n'.join([
        'url = ' + curl_quote(settings['server'].rstrip('/') + path),
        ('header = ' + curl_quote('Authorization: Bearer ' + token)) if token else '',
        'header = "Content-Type: application/json"',
        'header = "User-Agent: Nexxus-Domus-Mini (github.com/jorgeabalo/nexxus-core.)"',
        ('data-binary = ' + curl_quote(payload)) if data is not None else '',
    ])
    # -q must be first: do not load ~/.curlrc. No redirects, proxies or insecure mode.
    env = os.environ.copy()
    for key in list(env):
        if key.lower().endswith('_proxy') or key in ('CURL_CA_BUNDLE', 'SSL_CERT_FILE', 'SSL_CERT_DIR', 'SSLKEYLOGFILE'):
            del env[key]
    env['OPENSSL_CONF'] = os.devnull
    env['OPENSSL_MODULES'] = os.path.join(ROOT, 'transport', 'modules')
    proc = subprocess.Popen([binary, '-q', '--config', '-', '--silent',
                             '--proto', '=https', '--tlsv1.2', '--cacert', ca,
                             '--connect-timeout', '10', '--max-time', '40',
                             '--max-filesize', '262144', '--noproxy', '*',
                             '--write-out', '\n%{http_code}'],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=env)
    output, unused = proc.communicate(options.encode('utf-8'))
    if proc.returncode:
        raise ValueError('No se pudo conectar con TLS seguro. Comprueba fecha, red y transporte.')
    body, code = output.rsplit(b'\n', 1)
    if code != b'200':
        errors = {b'401': 'Acceso no valido. Revisa tus datos y pulsa Conectar.',
                  b'403': 'Tu usuario no tiene acceso a Mini AI.',
                  b'429': 'Limite de uso alcanzado. Intentalo mas tarde.',
                  b'503': 'Mini AI necesita configuracion en el servidor.',
                  b'502': 'El proveedor no pudo responder. Intentalo mas tarde.'}
        raise ValueError(errors.get(code, 'El servidor rechazo la consulta.'))
    result = json.loads(body.decode('utf-8'))
    return result


def request(settings, token, messages):
    result = transport(settings, '/api/mini-ai/chat', {'messages': messages}, token)
    reply = result.get('reply')
    if not isinstance(reply, type('')) or not reply.strip() or len(reply) > 2000:
        raise ValueError('Respuesta inesperada del servidor.')
    return reply


def login(settings, email, password):
    result = transport(settings, '/api/mini-ai/session', {'email': email, 'password': password})
    token = result.get('access_token')
    if not isinstance(token, type('')) or not 1 <= len(token) <= 8192:
        raise ValueError('Respuesta de acceso inesperada.')
    return token


def katy_weather():
    settings = {'server': 'https://api.weather.gov'}
    point = transport(settings, '/points/29.7858,-95.8245')
    url = point['properties']['forecastHourly']
    parts = urlsplit(url)
    if parts.scheme != 'https' or parts.netloc != 'api.weather.gov' or not parts.path.startswith('/gridpoints/') or parts.query or parts.fragment:
        raise ValueError('Unexpected weather endpoint')
    forecast = transport(settings, parts.path)
    periods = forecast['properties']['periods']
    # Choose the current hour; never silently display an expired forecast.
    now = time.time()
    for period in periods:
        def timestamp(value):
            base = calendar.timegm(time.strptime(value[:19], '%Y-%m-%dT%H:%M:%S'))
            offset = (int(value[20:22])*60 + int(value[23:25]))*60
            return base - offset if value[19] == '+' else base + offset
        if timestamp(period['startTime']) <= now < timestamp(period['endTime']):
            temp = float(period['temperature'])
            if period['temperatureUnit'] == 'F':
                celsius, fahrenheit = (temp-32)*5/9.0, temp
            elif period['temperatureUnit'] == 'C':
                celsius, fahrenheit = temp, temp*9/5.0+32
            else:
                raise ValueError('Unknown temperature unit')
            return 'Katy, Texas: %.0f C / %.0f F\nPrevision de esta hora (NWS)\n%s' % (celsius, fahrenheit, period['shortForecast'])
    raise ValueError('No current forecast')


class Client(object):
    def __init__(self, root, settings):
        self.root, self.settings = root, settings
        self.messages, self.events = [], queue.Queue()
        self.busy = False
        self.metrics_window = None
        self.metrics_timer = None
        self.metric_events = queue.Queue()
        self.metric_jobs = {}
        self.last_domus = 0
        self.weather_events = queue.Queue()
        self.weather_busy = False
        self.last_weather = 0
        with open(os.path.join(ROOT, 'nebula.json'), 'rb') as handle:
            self.nebula_pixels = json.loads(handle.read().decode('utf-8'))['pixels']
        bg, panel, ink, muted = '#030409', '#161422', '#eeeafc', '#b6a1ff'
        root.title('AITA - Nexxus Mini AI')
        root.geometry('720x680')
        root.minsize(660, 580)
        root.configure(background=bg)
        root.option_add('*Font', ('Helvetica', 14))
        header = tk.Frame(root, bg=bg)
        header.pack(fill='x', padx=22, pady=14)
        # Existing AITA typographic mark and Domus palette; no invented emblem.
        tk.Label(header, text='AITA', font=('Helvetica', 28, 'bold'), bg=bg,
                 fg=muted).pack(side='left')
        tk.Label(header, text='Nexxus Domus', font=('Helvetica', 17), bg=bg,
                 fg=ink).pack(side='left', padx=16)
        self.session_token = ''
        self.login_frame = tk.Frame(root, bg=panel, padx=18, pady=14)
        tk.Label(self.login_frame, text='1. Conecta con tu cuenta de Nexxus',
                 bg=panel, fg=ink).pack(anchor='w', pady=(0, 10))
        tk.Label(self.login_frame, text='Correo', bg=panel, fg=ink).pack(anchor='w')
        self.email = tk.Entry(self.login_frame, bg='#080a12', fg=ink,
                              insertbackground=ink, relief='flat')
        self.email.pack(fill='x', ipady=7, pady=(4, 10))
        self.email.insert(0, settings.get('email', ''))
        tk.Label(self.login_frame, text='Contrasena de Nexxus Manager', bg=panel,
                 fg=ink).pack(anchor='w')
        self.password = tk.Entry(self.login_frame, show='*', bg='#080a12', fg=ink,
                                 insertbackground=ink, relief='flat')
        self.password.pack(fill='x', ipady=7, pady=4)
        self.password.bind('<Return>', lambda event: self.connect())
        self.connect_button = tk.Button(self.login_frame, text='Conectar',
                                        command=self.connect, fg='#20152f',
                                        highlightbackground=panel, width=18)
        self.connect_button.pack(pady=(10, 0))
        self.chat_frame = tk.Frame(root, bg=bg)
        self.chat_frame.pack(fill='both', expand=True, padx=22, pady=8)
        self.connection_row = tk.Frame(self.chat_frame, bg=bg)
        self.connection_row.pack(fill='x')
        self.connection_label = tk.Label(self.connection_row, text='Nexxus Domus - Acceso directo',
                                         bg=bg, fg=ink)
        self.connection_label.pack(side='left')
        self.account_button = tk.Button(self.connection_row, text='Volver a conectar',
                                        command=self.show_login, fg='#20152f',
                                        highlightbackground=bg)
        self.log = tk.Text(self.chat_frame, wrap='word', height=10, state='disabled',
                           bg=panel, fg=ink, relief='flat', padx=14, pady=12)
        self.log.pack(fill='both', expand=True, pady=10)
        self.entry = tk.Entry(self.chat_frame, bg='#080a12', fg=ink,
                              insertbackground=ink, state='disabled', relief='flat')
        self.entry.pack(fill='x', ipady=10)
        self.entry.bind('<Return>', self.send)
        row = tk.Frame(self.chat_frame, bg=bg)
        row.pack(fill='x', pady=10)
        self.send_button = tk.Button(row, text='Enviar', command=self.send,
                                     state='disabled', fg='#20152f', highlightbackground=bg,
                                     width=12)
        self.send_button.pack(side='left', padx=(0, 8))
        tk.Button(row, text='Ver metricas', command=self.show_metrics,
                  fg='#20152f', highlightbackground=bg).pack(side='left', padx=4)
        tk.Button(row, text='Nexxus', command=self.show_companion,
                  fg='#20152f', highlightbackground=bg).pack(side='left', padx=4)
        self.companion = None
        self.companion_timer = None
        self.companion_frame = 0
        tk.Button(row, text='Borrar charla', command=self.clear, fg='#20152f',
                  highlightbackground=bg).pack(side='right')
        self.status = tk.Label(root, text='Sin usuario ni contrasena. Chat y datos privados pendientes de vinculacion segura.',
                               bg=bg, fg=muted, wraplength=650)
        self.status.pack(padx=18, pady=5)
        tk.Label(root, text='Las metricas del mini se quedan en este equipo. Solo el chat se envia a la IA.',
                 font=('Helvetica', 11), bg=bg, fg='#a9a5ba').pack(pady=(0, 12))
        root.after(100, self.poll)
        root.after(200, self.show_companion)
        self.append('Bienvenido a Nexxus Domus. Abre Ver metricas para ver el mini.\nEl chat y los datos privados requieren una futura vinculacion segura sin contrasena.')

    def show_companion(self):
        if self.companion is not None:
            self.companion.lift()
            return
        window = tk.Toplevel(self.root)
        self.companion = window
        window.title('Nexxus - Tu espacio')
        window.geometry('360x590')
        window.configure(bg='#030409')
        self.companion_float = tk.IntVar(value=1)
        self.companion_motion = tk.IntVar(value=1)
        def floating():
            window.attributes('-topmost', bool(self.companion_float.get()))
        floating()
        tk.Checkbutton(window, text='Mantener flotante', variable=self.companion_float,
                       command=floating, bg='#030409', fg='#b6a1ff').pack()
        tk.Checkbutton(window, text='Animacion suave', variable=self.companion_motion,
                       bg='#030409', fg='#b6a1ff').pack()
        self.nebula_canvas = tk.Canvas(window, width=330, height=190,
                                       bg='#030409', highlightthickness=0)
        self.nebula_canvas.pack()
        self.companion_clock = tk.Label(window, bg='#030409', fg='#eeeafc',
                                        font=('Helvetica', 21))
        self.companion_clock.pack(pady=5)
        tk.Label(window, text='Calendario del mes', bg='#030409', fg='#b6a1ff').pack()
        self.companion_calendar = tk.Label(window, bg='#030409', fg='#eeeafc',
                                           font=('Courier', 13), justify='left')
        self.companion_calendar.pack(pady=6)
        self.weather_label = tk.Label(window, text='Katy, Texas: consultando el tiempo...',
                 bg='#030409', fg='#a9a5ba', font=('Helvetica', 11), wraplength=330)
        self.weather_label.pack(pady=8)
        self.refresh_weather()
        window.protocol('WM_DELETE_WINDOW', self.close_companion)
        self.animate_companion()

    def refresh_weather(self):
        if self.weather_busy:
            return
        self.weather_busy = True
        self.last_weather = time.time()
        def run():
            try:
                result = katy_weather()
            except Exception:
                result = 'Katy, Texas: tiempo no disponible.\nSe reintentara en 15 minutos.'
            self.weather_events.put(result)
            self.weather_busy = False
        thread = threading.Thread(target=run)
        thread.setDaemon(True)
        thread.start()

    def close_companion(self):
        if self.companion_timer is not None:
            self.root.after_cancel(self.companion_timer)
            self.companion_timer = None
        self.companion.destroy()
        self.companion = None

    def animate_companion(self):
        if self.companion is None:
            return
        self.companion_clock.configure(text=time.strftime('%d/%m/%Y   %H:%M'))
        now = time.localtime()
        self.companion_calendar.configure(text=calendar.month(now.tm_year, now.tm_mon))
        canvas = self.nebula_canvas
        canvas.delete('all')
        phase = self.companion_frame / 12.0 if self.companion_motion.get() else 0
        # Original Domus pixel coordinates/colors, alpha blended onto the Tk background.
        scale = .70 * (1 + .025 * math.sin(phase))
        for px, py, width, height, color, opacity in self.nebula_pixels:
            x, y = 165 + (px-220)*scale, 89 + (py-170)*scale + math.sin(phase)*5
            bg = (3,4,9)
            rgb = [int(color[i:i+2],16) for i in (1,3,5)]
            fill = '#%02x%02x%02x' % tuple(int(rgb[i]*opacity+bg[i]*(1-opacity)) for i in range(3))
            canvas.create_rectangle(x,y,x+width*scale,y+height*scale,fill=fill,outline='')
        canvas.create_text(165, 170, text='N E X X U S', fill='#e0d2fb', font=('Helvetica', 13))
        try:
            while True:
                self.weather_label.configure(text=self.weather_events.get_nowait())
        except queue.Empty:
            pass
        if time.time() - self.last_weather >= 900:
            self.refresh_weather()
        self.companion_frame += 1
        self.companion_timer = self.root.after(250 if self.companion_motion.get() else 1000,
                                               self.animate_companion)

    def show_login(self):
        self.status.configure(text='Esta primera version abre sin cuenta. Vinculacion privada pendiente.')

    def connect(self):
        if self.busy:
            return
        email, password = self.email.get().strip(), self.password.get()
        self.password.delete(0, 'end')
        self.session_token = ''
        if self.metrics_window is not None:
            self.domus_metrics.configure(text='Datos privados de Domus: vinculacion segura pendiente.')
        self.entry.configure(state='disabled')
        self.send_button.configure(state='disabled')
        self.clear()
        if not email or not password:
            self.status.configure(text='Introduce tu correo y contrasena de Nexxus.')
            return
        self.connection_label.configure(text='Conectando...')
        self.status.configure(text='Conectando de forma segura...')
        self.work(lambda: login(self.settings, email, password), 'session')

    def append(self, text):
        self.log.configure(state='normal')
        self.log.insert('end', text + '\n\n')
        self.log.see('end')
        self.log.configure(state='disabled')

    def clear(self):
        if self.busy:
            return
        self.messages = []
        self.log.configure(state='normal')
        self.log.delete('1.0', 'end')
        self.log.configure(state='disabled')

    def work(self, job, kind):
        self.busy = True
        self.connect_button.configure(state='disabled')
        self.send_button.configure(state='disabled')
        def run():
            try:
                self.events.put((kind, job(), None, session))
            except Exception as exc:
                self.events.put((kind, None, str(exc)))
        thread = threading.Thread(target=run)
        thread.setDaemon(True)
        thread.start()

    def send(self, event=None):
        if self.busy:
            return
        text = self.entry.get().strip()
        if not text or len(text) > 2000:
            self.status.configure(text='Escribe entre 1 y 2000 caracteres.')
            return
        token = self.session_token
        if not token:
            self.status.configure(text='Pulsa Conectar a Nexxus antes de enviar.')
            return
        # Four complete exchanges plus this question. Never share history with another user.
        if getattr(self, 'last_token', token) != token:
            self.clear()
        self.last_token = token
        self.pending = self.messages[-8:] + [{'role': 'user', 'content': text}]
        self.status.configure(text='Consultando a Nexxus...')
        self.work(lambda: request(self.settings, token, self.pending), 'chat')

    def show_metrics(self):
        if self.metrics_window is not None and self.metrics_window.winfo_exists():
            self.metrics_window.lift()
            return
        window = tk.Toplevel(self.root)
        self.metrics_window = window
        window.title('AITA - Mi mini y Nexxus Domus')
        window.geometry('660x520')
        window.configure(bg='#030409')
        tk.Label(window, text='Mi Mac mini', font=('Helvetica', 22, 'bold'),
                 bg='#030409', fg='#b6a1ff').pack(anchor='w', padx=22, pady=(18, 8))
        self.local_metrics = tk.Label(window, text='Leyendo el mini...', justify='left',
                                      bg='#030409', fg='#eeeafc', font=('Helvetica', 15))
        self.local_metrics.pack(anchor='w', padx=22, pady=8)
        tk.Label(window, text='Nexxus Domus', font=('Helvetica', 22, 'bold'),
                 bg='#030409', fg='#b6a1ff').pack(anchor='w', padx=22, pady=(16, 8))
        self.domus_metrics = tk.Label(window, text='Datos privados de Domus: vinculacion segura pendiente.',
                                      justify='left', wraplength=610,
                                      bg='#030409', fg='#eeeafc', font=('Helvetica', 14))
        self.domus_metrics.pack(anchor='w', padx=22, pady=8)
        tk.Button(window, text='Actualizar Domus', command=self.refresh_domus).pack(pady=8)
        tk.Label(window, text='Mini: cada 5 s. Domus: cada minuto. Dispositivos: estado registrado, no telemetria en vivo.',
                 wraplength=610, bg='#030409', fg='#a9a5ba', font=('Helvetica', 11)).pack(padx=22, pady=12)
        window.protocol('WM_DELETE_WINDOW', self.close_metrics)
        self.last_domus = 0
        self.weather_events = queue.Queue()
        self.weather_busy = False
        self.last_weather = 0
        with open(os.path.join(ROOT, 'nebula.json'), 'rb') as handle:
            self.nebula_pixels = json.loads(handle.read().decode('utf-8'))['pixels']
        self.refresh_metrics()

    def close_metrics(self):
        if self.metrics_timer is not None:
            self.root.after_cancel(self.metrics_timer)
            self.metrics_timer = None
        self.metrics_window.destroy()
        self.metrics_window = None

    def metric_job(self, kind, job):
        if self.metric_jobs.get(kind):
            return
        self.metric_jobs[kind] = True
        session = self.session_token
        def run():
            try:
                self.metric_events.put((kind, job(), None, session))
            except Exception:
                self.metric_events.put((kind, None, 'No disponible. Comprueba tu conexion o vuelve a conectar.', session))
        thread = threading.Thread(target=run)
        thread.setDaemon(True)
        thread.start()

    def refresh_metrics(self):
        if self.metrics_window is None:
            return
        def local():
            proc = subprocess.Popen([os.path.join(ROOT, 'system-metrics')],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            data, unused = proc.communicate()
            if proc.returncode:
                raise ValueError('metrics unavailable')
            return json.loads(data.decode('utf-8'))
        self.metric_job('local', local)
        if time.time() - self.last_domus >= 60:
            self.refresh_domus()
        self.metrics_timer = self.root.after(5000, self.refresh_metrics)

    def refresh_domus(self):
        if self.metrics_window is None:
            return
        if not self.session_token:
            self.domus_metrics.configure(text='Datos privados de Domus: vinculacion segura pendiente.')
            return
        token = self.session_token
        self.last_domus = time.time()
        self.metric_job('domus', lambda: transport(self.settings, '/api/mini-ai/metrics', token=token))

    def poll_metrics(self):
        try:
            while True:
                kind, data, error, session = self.metric_events.get_nowait()
                self.metric_jobs[kind] = False
                if self.metrics_window is None or (kind == 'domus' and session != self.session_token):
                    continue
                label = self.local_metrics if kind == 'local' else self.domus_metrics
                if error:
                    label.configure(text=error)
                elif kind == 'local':
                    gb = float(1024 ** 3)
                    label.configure(text=('CPU: %.1f %%\nRAM ocupada: %.2f / %.2f GiB (incluye cache)\n'
                        'Disco del sistema libre: %.1f / %.1f GiB\nTiempo encendido: %.1f horas') %
                        (data['cpu_percent'], data['ram_occupied']/gb, data['ram_total']/gb,
                         data['disk_free']/gb, data['disk_total']/gb, data['uptime_seconds']/3600.0))
                else:
                    counts = data.get('domus', {})
                    def number(name):
                        value = counts.get(name)
                        return str(value) if value is not None else 'No disponible'
                    text = ('Hogares: %s   Habitaciones: %s\nDispositivos registrados: %s\n'
                            'Marcados conectados: %s   Pendientes: %s   Atencion: %s\nAsistentes registrados: %s') % tuple(
                        number(k) for k in ('homes','rooms','devices','marked_connected','pending','attention','assistants'))
                    quota = data.get('nexxus')
                    if quota is not None:
                        text += '\nIntentos de chat hoy (UTC): %s / %s. Restantes: %s' % (
                            quota['attempts_today'],quota['daily_limit'],quota['remaining'])
                    text += '\nActualizado: ' + time.strftime('%H:%M:%S')
                    label.configure(text=text)
        except queue.Empty:
            pass

    def poll(self):
        self.poll_metrics()
        try:
            kind, value, error = self.events.get_nowait()
        except queue.Empty:
            self.root.after(100, self.poll)
            return
        self.busy = False
        self.connect_button.configure(state='normal')
        self.send_button.configure(state=('normal' if self.session_token else 'disabled'))
        if error:
            if kind == 'session':
                self.connection_label.configure(text='Sin conexion')
            self.status.configure(text=error)
        elif kind == 'session':
            self.session_token = value
            self.login_frame.pack_forget()
            self.account_button.pack(side='right')
            self.connection_label.configure(text='Conectado a Nexxus')
            self.entry.configure(state='normal')
            self.send_button.configure(state='normal')
            self.entry.focus_set()
            self.status.configure(text='Escribe tu pregunta y pulsa Enviar o Intro.')
            if self.metrics_window is not None:
                self.refresh_domus()
        elif kind == 'chat':
            self.messages = self.pending + [{'role': 'assistant', 'content': value}]
            self.append('Tu: ' + self.pending[-1]['content'])
            self.append('Nexxus: ' + value)
            self.entry.delete(0, 'end')
            self.status.configure(text='Respuesta recibida.')
        self.root.after(100, self.poll)


if __name__ == '__main__':
    root = tk.Tk()
    try:
        settings = config()
    except Exception as exc:
        messagebox.showerror('Configuracion', str(exc))
        root.destroy()
    else:
        Client(root, settings)
        root.mainloop()
