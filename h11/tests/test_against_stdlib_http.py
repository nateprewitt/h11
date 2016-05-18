import os.path
from contextlib import contextmanager, closing
import socket
import threading
import json

try:
    from urllib.request import urlopen
except ImportError:  # version specific: Python 2
    from urllib2 import urlopen

try:
    import socketserver
except ImportError:  # version specific: Python 2
    import SocketServer as socketserver

try:
    from http.server import SimpleHTTPRequestHandler
except ImportError:  # version specific: Python 2
    from SimpleHTTPServer import SimpleHTTPRequestHandler

import h11

@contextmanager
def socket_server(handler):
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever,
                              kwargs={"poll_interval": 0.01})
    thread.daemon = True
    try:
        thread.start()
        yield httpd
    finally:
        httpd.shutdown()

test_file_path = os.path.join(os.path.dirname(__file__), "data/test-file")
with open(test_file_path, "rb") as f:
    test_file_data = f.read()

class SingleMindedRequestHandler(SimpleHTTPRequestHandler):
    def translate_path(self, path):
        return test_file_path

def test_h11_as_client():
    with socket_server(SingleMindedRequestHandler) as httpd:
        with closing(socket.create_connection(httpd.server_address)) as s:
            c = h11.Connection(h11.CLIENT)

            s.sendall(c.send(h11.Request(
                method="GET", target="/foo", headers=[("Host", "localhost")])))
            s.sendall(c.send(h11.EndOfMessage()))

            data = bytearray()
            while True:
                event = c.next_event()
                print(event)
                if event is h11.NEED_DATA:
                    # Use a small read buffer to make things more challenging
                    # and exercise more paths :-)
                    c.receive_data(s.recv(10))
                    continue
                if type(event) is h11.Response:
                    assert event.status_code == 200
                if type(event) is h11.Data:
                    data += event.data
                if type(event) is h11.EndOfMessage:
                    break
            assert bytes(data) == test_file_data

class H11RequestHandler(socketserver.BaseRequestHandler):
    def handle(self):
        with closing(self.request) as s:
            c = h11.Connection(h11.SERVER)
            request = None
            while True:
                event = c.next_event()
                if event is h11.NEED_DATA:
                    # Use a small read buffer to make things more challenging
                    # and exercise more paths :-)
                    c.receive_data(s.recv(10))
                    continue
                if type(event) is h11.Request:
                    request = event
                if type(event) is h11.EndOfMessage:
                    break
            info = json.dumps({
                "method": request.method.decode("ascii"),
                "target": request.target.decode("ascii"),
                "headers": {
                    name.decode("ascii"): value.decode("ascii")
                    for (name, value) in request.headers
                    },
            })
            s.sendall(c.send(h11.Response(status_code=200, headers=[])))
            s.sendall(c.send(h11.Data(data=info.encode("ascii"))))
            s.sendall(c.send(h11.EndOfMessage()))

def test_h11_as_server():
    with socket_server(H11RequestHandler) as httpd:
        host, port = httpd.server_address
        url = "http://{}:{}/some-path".format(host, port)
        with closing(urlopen(url)) as f:
            assert f.getcode() == 200
            data = f.read()
    info = json.loads(data.decode("ascii"))
    print(info)
    assert info["method"] == "GET"
    assert info["target"] == "/some-path"
    assert "urllib" in info["headers"]["user-agent"]
