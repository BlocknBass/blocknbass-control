from ola.ClientWrapper import ClientWrapper
from contextlib import contextmanager
import socket
from socket import timeout
import select
import message_pb2
import light_pb2

@contextmanager
def socketcontext(*args, **kwargs):
    s = socket.socket(*args, **kwargs)
    try:
        yield s
    finally:
        print("Closing server socket")
        s.close()

@contextmanager
def epollcontext(*args, **kwargs):
    e = select.epoll()
    e.register(*args, **kwargs)
    try:
        yield e
    finally:
        print("Closing epoll loop")
        e.unregister(args[0])
        e.close()

def init_connection(server, sockets, clients, data_in, data_out, epoll):
    socket, address = server.accept()
    socket.setblocking(0)

    fd = socket.fileno()
    epoll.register(fd, select.EPOLLIN)
    sockets[fd] = socket
    clients.append(fd)
    data_in[fd] = b''
    data_out[fd] = b''
    print("Accepted new client {:02d}".format(fd))

def on_data_in(socket, data_in, data_out, epoll):
    fd = socket.fileno()
    try:
        data_in[fd] += socket.recv(4096)
    except ConnectionResetError:
        print("Connection reset!")
        return -1
    except timeout:
        print("Client timed out!")
        return -1

    if data_in[fd] == b'':
        return -1

    return 0
    # TODO

def on_data_out(fd, sockets, data_out, epoll):
    no_bytes = sockets[fd].send(data_out[fd])
    data_out[fd] = data_out[fd][no_bytes:]
    epoll.modify(fd, select.EPOLLIN)

def ola_data_cb(data, clients, data_out, epoll):
    light_message = light_pb2.LightMessage()
    light_message.pan = data[0]
    light_message.tilt = data[1]
    light_message.red = data[4]
    light_message.green = data[5]
    light_message.blue = data[6]
    light_message.white = data[7]
    message = message_pb2.Message()
    message.key = "light"
    message.message.Pack(light_message)
    for fd in clients:
        data_out[fd] = message.SerializeToString()
        epoll.modify(fd, select.EPOLLOUT)

def main():
    with socketcontext(socket.AF_INET, socket.SOCK_STREAM) as server, epollcontext(server.fileno(), select.EPOLLIN) as epoll:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("0.0.0.0", 6969))
        server.listen(5)
        server.setblocking(0)
        server.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        print("Listening on 0.0.0.0:6969")

        clients = []
        sockets = {}
        data_in = {}
        data_out = {}
        server_fd = server.fileno()

        def cb_stub(data):
            ola_data_cb(data, clients, data_out, epoll)

        wrapper = ClientWrapper()
        ola_client = wrapper.Client()
        ola_client.RegisterUniverse(1, ola_client.REGISTER, cb_stub)
        ola_client_fd = ola_client.GetSocket().fileno()

        epoll.register(ola_client_fd, select.EPOLLIN)

        while True:
            events = epoll.poll(1)
            for fd, event in events:
                if fd == server_fd:
                    init_connection(server, sockets, clients, data_in, data_out, epoll)
                elif fd == ola_client_fd:
                    ola_client.SocketReady()
                elif event & select.EPOLLIN:
                    if on_data_in(sockets[fd], data_in, data_out, epoll) < 0:
                        print("Disconnecting client {:02d}!".format(fd))
                        epoll.unregister(fd)
                        sockets[fd].close()
                        clients.remove(fd)
                        del sockets[fd], data_in[fd], data_out[fd]
                elif event & select.EPOLLOUT:
                    on_data_out(fd, sockets, data_out, epoll)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("Shutting down...")

