#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Python 2.6 / Tk client; TLS delegated to a separately verified curl bundle."""
from __future__ import unicode_literals
import json
import os
import subprocess
import threading
try:
    import Tkinter as tk
    import tkMessageBox as messagebox
    import tkFileDialog as filedialog
    import Queue as queue
    from urlparse import urlsplit
except ImportError:
    import tkinter as tk
    from tkinter import messagebox, filedialog
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


def transport(settings, path, data, token=None):
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
        'data-binary = ' + curl_quote(payload),
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
                             '--max-filesize', '65536', '--noproxy', '*',
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


class Client(object):
    def __init__(self, root, settings):
        self.root, self.settings = root, settings
        self.messages, self.events = [], queue.Queue()
        self.busy = False
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
        tk.Label(header, text='Nexxus Mini AI', font=('Helvetica', 17), bg=bg,
                 fg=ink).pack(side='left', padx=16)
        self.session_token = ''
        self.login_frame = tk.Frame(root, bg=panel, padx=18, pady=14)
        self.login_frame.pack(fill='x', padx=22, pady=6)
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
        self.connection_label = tk.Label(self.connection_row, text='2. Escribe tu pregunta',
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
        self.mic_button = tk.Button(row, text='Grabar audio (10 s)', command=self.record,
                                    fg='#20152f', highlightbackground=bg)
        self.mic_button.pack(side='left', padx=4)
        tk.Button(row, text='Borrar charla', command=self.clear, fg='#20152f',
                  highlightbackground=bg).pack(side='right')
        self.status = tk.Label(root, text='Tu correo esta preparado. Escribe tu contrasena y pulsa Conectar.',
                               bg=bg, fg=muted, wraplength=650)
        self.status.pack(padx=18, pady=5)
        tk.Label(root, text='El texto se envia a la IA. El audio solo se guarda en este Mac.',
                 font=('Helvetica', 11), bg=bg, fg='#a9a5ba').pack(pady=(0, 12))
        if self.email.get():
            self.password.focus_set()
        else:
            self.email.focus_set()
        root.after(100, self.poll)

    def show_login(self):
        if self.busy:
            return
        self.login_frame.pack(fill='x', padx=22, pady=6, before=self.chat_frame)
        self.password.focus_set()

    def connect(self):
        if self.busy:
            return
        email, password = self.email.get().strip(), self.password.get()
        self.password.delete(0, 'end')
        self.session_token = ''
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
        self.mic_button.configure(state='disabled')
        def run():
            try:
                self.events.put((kind, job(), None))
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

    def record(self):
        if self.busy:
            return
        helper = os.path.join(ROOT, 'record-audio')
        if not os.path.isfile(helper):
            messagebox.showinfo('Microfono', 'Falta compilar record-audio. Consulta INSTALACION.md.')
            return
        path = filedialog.asksaveasfilename(title='Guardar grabacion local',
                                            defaultextension='.wav', filetypes=[('Audio WAV', '*.wav')])
        if not path:
            return
        # Existing files are never overwritten by the helper.
        if os.path.exists(path):
            messagebox.showerror('Microfono', 'Elige un nombre nuevo para la grabacion.')
            return
        self.status.configure(text='Grabando 10 segundos. El audio permanece en este Mac.')
        def capture():
            proc = subprocess.Popen([helper, path, '10'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            proc.communicate()
            if proc.returncode:
                raise ValueError('No se pudo grabar. Revisa el microfono en Preferencias del Sistema > Sonido.')
            return path
        self.work(capture, 'audio')

    def poll(self):
        try:
            kind, value, error = self.events.get_nowait()
        except queue.Empty:
            self.root.after(100, self.poll)
            return
        self.busy = False
        self.connect_button.configure(state='normal')
        self.send_button.configure(state=('normal' if self.session_token else 'disabled'))
        self.mic_button.configure(state='normal')
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
        elif kind == 'chat':
            self.messages = self.pending + [{'role': 'assistant', 'content': value}]
            self.append('Tu: ' + self.pending[-1]['content'])
            self.append('Nexxus: ' + value)
            self.entry.delete(0, 'end')
            self.status.configure(text='Respuesta recibida.')
        else:
            self.status.configure(text='Audio guardado localmente. Transcripcion automatica pendiente.')
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
