#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Python 2.6 / Tk client; TLS delegated to a separately verified curl bundle."""
from __future__ import unicode_literals
import json
import os
import subprocess
import threading
import time
import base64
import binascii
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


def origin(url):
    parts = urlsplit(url)
    if (parts.scheme != 'https' or not parts.hostname or parts.username or
            parts.password or parts.query or parts.fragment or parts.path not in ('', '/')):
        raise ValueError('Configura solo un origen HTTPS, sin ruta ni credenciales.')
    return url.rstrip('/')


def config():
    with open(os.path.join(ROOT, 'config.json'), 'rb') as handle:
        value = json.loads(handle.read().decode('utf-8'))
    url = value.get('server', '')
    origin(url)
    if value.get('supabase_url'):
        origin(value['supabase_url'])
        public_key(value.get('supabase_public_key', ''))
    return value


def public_key(key):
    # Public anon/publishable keys only. Never distribute a service-role key.
    if key.startswith('sb_publishable_') and all(33 <= ord(c) <= 126 for c in key):
        return key
    try:
        segment = key.split('.')[1]
        claim = json.loads(base64.urlsafe_b64decode((segment + '=' * (-len(segment) % 4)).encode('ascii')).decode('utf-8'))
        if isinstance(claim, dict) and claim.get('role') == 'anon' and all(33 <= ord(c) <= 126 for c in key):
            return key
    except (ValueError, IndexError, TypeError, UnicodeError, binascii.Error):
        pass
    raise ValueError('Configura una clave PUBLICA anon o publishable de Supabase.')


def curl_quote(value):
    # curl config escaping, not shell escaping. No shell is ever launched.
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('\r', '\\r').replace('\n', '\\n') + '"'


def http_json(url, headers, payload):
    parts = urlsplit(url)
    origin(parts.scheme + '://' + parts.netloc)
    if parts.fragment or any(ord(c) < 33 or ord(c) > 126 for c in url):
        raise ValueError('URL HTTPS invalida.')
    binary = os.path.join(ROOT, 'transport', 'curl')
    ca = os.path.join(ROOT, 'transport', 'cacert.pem')
    if not os.path.isfile(binary) or not os.path.isfile(ca):
        raise ValueError('Falta el transporte TLS verificado. Consulta INSTALACION.md.')
    for header in headers:
        if any(ord(c) < 32 or ord(c) > 126 for c in header):
            raise ValueError('Cabecera invalida.')
    options = '\n'.join([
        'url = ' + curl_quote(url),
        'header = "Content-Type: application/json"',
        'data-binary = ' + curl_quote(json.dumps(payload, ensure_ascii=True)),
    ] + ['header = ' + curl_quote(header) for header in headers])
    # -q must be first: do not load ~/.curlrc. No redirects, proxies or insecure mode.
    env = os.environ.copy()
    for key in list(env):
        if key.lower().endswith('_proxy') or key in ('CURL_CA_BUNDLE', 'SSL_CERT_FILE', 'SSL_CERT_DIR', 'SSLKEYLOGFILE'):
            del env[key]
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
    try:
        body, code = output.rsplit(b'\n', 1)
        if len(body) > 65536:
            raise ValueError()
        result = json.loads(body.decode('utf-8'))
        if not isinstance(result, dict):
            raise ValueError()
    except (ValueError, UnicodeError):
        raise ValueError('Respuesta inesperada del servidor.')
    return code, result


def request(settings, token, messages):
    if not token or any(ord(c) < 33 or ord(c) > 126 for c in token):
        raise ValueError('Pega un token de sesion valido, sin espacios ni saltos de linea.')
    code, result = http_json(origin(settings['server']) + '/api/mini-ai/chat',
                             ['Authorization: Bearer ' + token], {'messages': messages})
    if code != b'200':
        errors = {b'401': 'La sesion ha caducado. Pega un token nuevo.',
                  b'403': 'Tu usuario no tiene acceso a Mini AI.',
                  b'429': 'Limite de uso alcanzado. Intentalo mas tarde.',
                  b'503': 'Mini AI necesita configuracion en el servidor.',
                  b'502': 'El proveedor no pudo responder. Intentalo mas tarde.'}
        raise ValueError(errors.get(code, 'El servidor rechazo la consulta.'))
    reply = result.get('reply')
    if not isinstance(reply, type('')) or not reply.strip() or len(reply) > 2000:
        raise ValueError('Respuesta inesperada del servidor.')
    return reply


class Session(object):
    """User credentials and rotating refresh tokens exist only in process memory."""
    def __init__(self, settings):
        self.settings = settings
        self.clear()

    def clear(self):
        self.access = self.refresh = ''
        self.until = 0

    def exchange(self, grant, payload):
        base = origin(self.settings.get('supabase_url', ''))
        key = public_key(self.settings.get('supabase_public_key', ''))
        code, data = http_json(base + '/auth/v1/token?grant_type=' + grant,
                               ['apikey: ' + key], payload)
        if code != b'200':
            self.clear()
            raise ValueError('No se pudo iniciar o renovar la sesion. Revisa tu acceso a Nexxus.')
        access, refresh = data.get('access_token'), data.get('refresh_token')
        ttl = data.get('expires_in')
        if (not isinstance(access, type('')) or not access or len(access) > 8192 or
                not isinstance(refresh, type('')) or not refresh or len(refresh) > 8192 or
                not isinstance(ttl, (int, float)) or not 0 < ttl <= 604800 or
                any(ord(c) < 33 or ord(c) > 126 for c in access + refresh)):
            self.clear()
            raise ValueError('Respuesta de sesion invalida.')
        self.access, self.refresh = access, refresh
        self.until = time.time() + ttl
        return access

    def login(self, email, password):
        self.clear()
        if not email.strip() or not password:
            raise ValueError('Escribe correo y contrasena.')
        return self.exchange('password', {'email': email.strip(), 'password': password})

    def token(self):
        if not self.access:
            raise ValueError('Inicia sesion antes de consultar.')
        if time.time() + 60 >= self.until:
            return self.exchange('refresh_token', {'refresh_token': self.refresh})
        return self.access


class Speech(object):
    def __init__(self):
        self.process = None

    def stop(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
            self.process.wait()
            self.process = None

    def speak(self, text):
        self.stop()
        # No model text in shell commands or process arguments, no audio upload.
        text = text.replace('[[', '').replace(']]', '')
        self.process = subprocess.Popen(['/usr/bin/say'], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.process.stdin.write(text.encode('utf-8'))
        self.process.stdin.close()


class Client(object):
    def __init__(self, root, settings):
        self.root, self.settings = root, settings
        self.messages, self.events = [], queue.Queue()
        self.busy = False
        self.session, self.speech = Session(settings), Speech()
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.title('Nexxus Mini AI')
        root.geometry('720x560')
        tk.Label(root, text='Nexxus Mini AI', font=('Helvetica', 20)).pack(pady=8)
        tk.Label(root, text='El texto y contexto se envian a Anthropic a traves de tu servidor.').pack()
        auth = tk.Frame(root)
        auth.pack(fill='x', padx=12)
        self.login_button = tk.Button(auth, text='Iniciar sesion', command=self.login)
        self.login_button.pack(side='left')
        self.logout_button = tk.Button(auth, text='Cerrar sesion', command=self.logout)
        self.logout_button.pack(side='left', padx=4)
        self.voice = tk.IntVar(value=0)
        tk.Checkbutton(auth, text='Leer respuestas', variable=self.voice).pack(side='left')
        tk.Button(auth, text='Silenciar', command=self.speech.stop).pack(side='left')
        tk.Label(root, text='Token temporal alternativo (opcional, no se guarda):').pack(anchor='w')
        self.token = tk.Entry(root, show='*', width=90)
        self.token.pack(fill='x', padx=12)
        self.log = tk.Text(root, wrap='word', height=18, state='disabled')
        self.log.pack(fill='both', expand=True, padx=12, pady=8)
        self.entry = tk.Entry(root, width=90)
        self.entry.pack(fill='x', padx=12)
        self.entry.bind('<Return>', self.send)
        row = tk.Frame(root)
        row.pack(pady=8)
        self.send_button = tk.Button(row, text='Enviar', command=self.send)
        self.send_button.pack(side='left', padx=4)
        self.mic_button = tk.Button(row, text='Microfono: grabar 10 s', command=self.record)
        self.mic_button.pack(side='left', padx=4)
        tk.Button(row, text='Nueva conversacion', command=self.clear).pack(side='left', padx=4)
        self.status = tk.Label(root, text='El microfono graba localmente; la transcripcion esta pendiente.')
        self.status.pack(pady=4)
        root.after(100, self.poll)

    def close(self):
        if self.busy:
            self.status.configure(text='Espera a que termine la operacion antes de cerrar.')
            return
        self.speech.stop()
        self.session.clear()
        self.token.delete(0, 'end')
        self.root.destroy()

    def logout(self):
        if self.busy:
            return
        self.session.clear()
        self.token.delete(0, 'end')
        self.speech.stop()
        self.clear()
        self.status.configure(text='Sesion cerrada en este Mac.')

    def login(self):
        if self.busy:
            return
        if not self.settings.get('supabase_url'):
            messagebox.showinfo('Sesion', 'Falta configurar el proyecto Supabase. Consulta INSTALACION.md.')
            return
        dialog = tk.Toplevel(self.root)
        dialog.title('Acceso a Nexxus')
        tk.Label(dialog, text='Correo').pack()
        email = tk.Entry(dialog, width=42)
        email.pack(padx=12)
        tk.Label(dialog, text='Contrasena (solo para iniciar sesion)').pack()
        password = tk.Entry(dialog, show='*', width=42)
        password.pack(padx=12)
        def submit():
            user, secret = email.get(), password.get()
            if not user.strip() or not secret:
                return
            password.delete(0, 'end')
            dialog.destroy()
            self.logout()
            self.status.configure(text='Iniciando sesion...')
            self.work(lambda: self.session.login(user, secret), 'login')
        tk.Button(dialog, text='Entrar', command=submit).pack(pady=10)
        dialog.transient(self.root)
        dialog.grab_set()
        email.focus_set()

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
        self.send_button.configure(state='disabled')
        self.mic_button.configure(state='disabled')
        self.login_button.configure(state='disabled')
        self.logout_button.configure(state='disabled')
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
        token = self.token.get().strip()
        # Four complete exchanges plus this question. Never share history with another user.
        if getattr(self, 'last_token', token) != token:
            self.clear()
        self.last_token = token
        self.pending = self.messages[-8:] + [{'role': 'user', 'content': text}]
        self.status.configure(text='Consultando a Nexxus...')
        self.work(lambda: request(self.settings, token or self.session.token(), self.pending), 'chat')

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
        self.send_button.configure(state='normal')
        self.mic_button.configure(state='normal')
        self.login_button.configure(state='normal')
        self.logout_button.configure(state='normal')
        if error:
            self.status.configure(text=error)
        elif kind == 'chat':
            self.messages = self.pending + [{'role': 'assistant', 'content': value}]
            self.append('Tu: ' + self.pending[-1]['content'])
            self.append('Nexxus: ' + value)
            self.entry.delete(0, 'end')
            self.status.configure(text='Respuesta recibida.')
            if self.voice.get():
                try:
                    self.speech.speak(value)
                except (OSError, IOError):
                    self.status.configure(text='Respuesta recibida. No se pudo leer con la voz del Mac.')
        elif kind == 'login':
            self.status.configure(text='Sesion iniciada. Ya puedes escribir a Nexxus.')
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
