"""Isolated HTTPS auth integration check using a synthetic Invidious upstream."""

from pathlib import Path
import subprocess
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]


def run(*args, **kwargs):
    result = subprocess.run(args, capture_output=True, text=True, **kwargs)
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout)
    return result.stdout


CLIENT = r'''
import http.client, json, ssl, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sessions = set()
checks = []
class Invidious(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def handle_request(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        cookie = self.headers.get("Cookie", "")
        status, headers = 200, {}
        if self.path == "/api/v1/auth/preferences":
            checks.append((self.command, body, self.headers.get("Authorization")))
            status = 200 if cookie in sessions else 403
            if cookie == "SID=unavailable":
                status = 503
        elif self.path == "/login" and self.command == "POST":
            assert body == b"email=test&password=synthetic", body
            sessions.add("SID=valid")
            headers["Set-Cookie"] = "SID=valid; Secure; HttpOnly; Path=/"
            status, headers["Location"] = 302, "/"
        elif self.path == "/signout":
            sessions.discard(cookie)
        result = json.dumps([self.command, self.path, cookie,
                             self.headers.get("Authorization"), self.headers.get("Range")]).encode()
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(result)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(result)

    do_GET = do_POST = do_HEAD = handle_request

server = ThreadingHTTPServer(("127.0.0.1", 3000), Invidious)
threading.Thread(target=server.serve_forever, daemon=True).start()
context = ssl._create_unverified_context()  # Disposable synthetic certificate.
def request(path="/", method="GET", headers=None, body=None):
    time.sleep(.06)  # Stay within the production rate limit.
    c = http.client.HTTPSConnection("127.0.0.1", 443, context=context, timeout=5)
    c.request(method, path, body=body, headers=headers or {})
    r = c.getresponse()
    result = r.status, dict(r.getheaders()), r.read().decode()
    c.close()
    return result

def check(expected, path="/", **kwargs):
    result = request(path, **kwargs)
    assert result[0] == expected, (path, result)
    assert "WWW-Authenticate" not in result[1], result
    if expected == 302:
        assert result[1]["Location"].endswith("/login"), result
    return result

for _ in range(50):
    try:
        request("/login")
        break
    except OSError:
        time.sleep(.1)
else:
    raise AssertionError("nginx failed to start")

protected = ("/", "/search?q=test", "/watch?v=test", "/embed/test", "/videoplayback?id=test",
             "/api/v1/search?q=test", "/api/v1/videos/test", "/api/manifest/dash/id/test",
             "/api/manifest/hls_playlist/id/test", "/companion/latest_version?id=test", "/latest_version?id=test",
             "/vi/test/default.jpg", "/feed/popular", "/hh3d-session", "/session", "/verify",
             "/css/../search?q=test", "/%73earch?q=test", "/login/search?q=test")
for cookie in ("", "SID=forged", "SID=expired", "__Host-hh3d_gate=old"):
    for path in protected:
        check(302, path, headers={"Cookie": cookie})
for authorization in ("Basic dGVzdDpwYXNz", "Bearer synthetic"):
    check(302, "/search", headers={"Authorization": authorization, "X-Forwarded-For": "192.168.1.14"})
print("PASS anonymous, forged and legacy credentials cannot access pages, APIs or streams")

for path in ("/login", "/login?referer=%2Fsearch", "/css/default.css", "/js/_helpers.js",
             "/fonts/ionicons.woff", "/favicon-32x32.png", "/site.webmanifest"):
    before = len(checks)
    check(200, path)
    assert len(checks) == before, path
check(404, "/_invidious_session")
status, headers, _ = request("/login", "POST", body="email=test&password=synthetic")
assert status == 302 and "SID=valid" in headers["Set-Cookie"], (status, headers)
print("PASS login page, assets, form body and SID cookie work without nginx credentials")

for path in protected:
    check(200, path, headers={"Cookie": "SID=valid"})
for method in ("HEAD", "POST"):
    check(200, "/watch?v=test", method=method, headers={"Cookie": "SID=valid"}, body="payload")
_, _, body = check(200, "/videoplayback?id=test", headers={"Cookie": "SID=valid", "Range": "bytes=0-1", "Authorization": "Basic ignored"})
assert json.loads(body)[2:] == ["SID=valid", None, "bytes=0-1"], body
assert checks and all(item == ("GET", b"", None) for item in checks), checks
print("PASS valid sessions access protected routes; probes use GET without body or Authorization")
check(500, "/search", headers={"Cookie": "SID=unavailable"})
check(200, "/signout", headers={"Cookie": "SID=valid"})
check(302, "/watch?v=test", headers={"Cookie": "SID=valid"})
print("PASS auth failure and revoked sessions fail closed")
server.shutdown()
'''


def main():
    name = "minetube-auth-test-" + uuid.uuid4().hex[:8]
    with tempfile.TemporaryDirectory(prefix="minetube-auth-", dir="/tmp") as directory:
        path = Path(directory)
        run("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
            "-subj", "/CN=minetube.ddns.net", "-keyout", str(path / "key.pem"), "-out", str(path / "cert.pem"))
        config = (ROOT / "nginx.conf").read_text()
        config = config.replace("/etc/letsencrypt/live/minetube.ddns.net/fullchain.pem", "/test/cert.pem")
        config = config.replace("/etc/letsencrypt/live/minetube.ddns.net/privkey.pem", "/test/key.pem")
        (path / "default.conf").write_text(config)
        try:
            run("docker", "run", "--rm", "-d", "--network", "none", "--name", name,
                "--add-host", "invidious:127.0.0.1",
                "-v", f"{path}:/test:ro", "-v", f"{path}/default.conf:/etc/nginx/conf.d/default.conf:ro", "nginx:stable-alpine")
            run("docker", "exec", name, "nginx", "-t")
            print(run("docker", "run", "--rm", "-i", "--network", "container:" + name,
                "--name", name + "-client", "python:3.11-slim", "python", "-B", "-", input=CLIENT), end="")
        finally:
            subprocess.run(["docker", "rm", "-f", name + "-client", name], capture_output=True)


if __name__ == "__main__":
    main()
