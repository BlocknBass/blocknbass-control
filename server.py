from ola.ClientWrapper import ClientWrapper
from contextlib import contextmanager
import socket
from socket import timeout
from google.protobuf.internal.encoder import _VarintBytes
from google.protobuf.internal.decoder import _DecodeVarint32
import select
from core import message_pb2
from light import light_pb2
from build import build_pb2
import json
import os

lights = []

class Fixture:
    def __init__(self, id, x, y, z):
        self.id = id
        self.x = x
        self.y = y
        self.z = z

    def to_message(self):
        light_message = light_pb2.FixtureMessage()
        light_message.id = self.id
        light_message.x = self.x
        light_message.y = self.y
        light_message.z = self.z
        return light_message

def encode_fixture(z):
    obj_dict = {
        "__class__": z.__class__.__name__,
        "__module__": z.__module__
    }
    obj_dict.update(z.__dict__)
    return obj_dict

def decode_fixture(dct):
    if "__class__" in dct:
        class_name = dct.pop("__class__")
        module_name = dct.pop("__module__")
        module = __import__(module_name)
        class_ = getattr(module, class_name)
        obj = class_(**dct)
    else:
        obj = dct
    return obj


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
    epoll.register(fd, select.EPOLLOUT)
    sockets[fd] = socket
    clients.append(fd)
    data_in[fd] = b''

    light_message = light_pb2.LightsUpdateMessage()
    light_message.type = light_pb2.SET_LIGHTS
    for fixture in lights:
        light_message.lights.extend([fixture.to_message()])

    data_out[fd] = make_message(light_message, "light_update")
    print("Accepted new client {:02d}".format(fd))

def on_data_in(socket, data_in, epoll):
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

def make_message(user_message, key):
    message = message_pb2.Message()
    message.key = key
    message.message.Pack(user_message)
    message_data = message.SerializeToString()
    message_length = message.ByteSize()
    data = _VarintBytes(message_length) + message_data
    return data

def ola_data_cb(data, clients, data_out, epoll):
    global lights
    for fixture in lights:
        id = fixture.id
        if 7 + id*11 > 511:
            break
        light_message = light_pb2.LightMessage()
        light_message.id = id
        light_message.pan = data[0 + id*11]
        light_message.tilt = data[1 + id*11]
        light_message.red = data[4 + id*11]
        light_message.green = data[5 + id*11]
        light_message.blue = data[6 + id*11]
        light_message.white = data[7 + id*11]
        msg_data = make_message(light_message, "light")
        for fd in clients:
            data_out[fd] += msg_data
            epoll.modify(fd, select.EPOLLOUT)

def handle_build_light(fd, clients, data_out, epoll, build_message):
    global lights
    next_id = 0
    taken = True
    while taken:
        taken = False
        for fixture in lights:
            if fixture.id == next_id:
                taken = True
                next_id += 1
                break

    light_data = build_message.lights[0]
    fixture = Fixture(next_id, light_data.x, light_data.y, light_data.z)
    lights.append(fixture)
    light_message = light_pb2.LightsUpdateMessage()
    light_message.type = light_pb2.ADD_LIGHT
    light_message_data = fixture.to_message()
    light_message.lights.extend([light_message_data])
    data = make_message(light_message, "light_update")
    for fd in clients:
        data_out[fd] = data
        epoll.modify(fd, select.EPOLLOUT)

def handle_list_lights(fd, clients, data_out, epoll, build_message):
    global lights
    ret_message = build_pb2.BuildMessage()
    ret_message.type = build_pb2.LIST_LIGHTS
    for fixture in lights:
        ret_message.lights.extend([fixture.to_message()])

    data = make_message(ret_message, "build")
    data_out[fd] = data
    epoll.modify(fd, select.EPOLLOUT)

def handle_remove_light(fd, clients, data_out, epoll, build_message):
    global lights
    light_message = light_pb2.LightsUpdateMessage()
    light_message.type = light_pb2.REMOVE_LIGHT
    fixture = Fixture(build_message.lights[0].id, 0, 0, 0)
    light_message.lights.extend([fixture.to_message()])
    data = make_message(light_message, "light_update")
    for fd in clients:
        data_out[fd] = data
        epoll.modify(fd, select.EPOLLOUT)

    lights = list(filter(lambda x: x.id != build_message.lights[0].id, lights))

def handle_build_packet(fd, clients, data_out, epoll, message):
    build_message = build_pb2.BuildMessage()
    message.message.Unpack(build_message)
    if build_message.type == build_pb2.BUILD_LIGHT:
        handle_build_light(fd, clients, data_out, epoll, build_message)
    elif build_message.type == build_pb2.LIST_LIGHTS:
        handle_list_lights(fd, clients, data_out, epoll, build_message)
    elif build_message.type == build_pb2.REMOVE_LIGHT:
        handle_remove_light(fd, clients, data_out, epoll, build_message)
    else:
        print("{}: got unexpected build light packet!".format(build_message.type))

def handle_clients(fd, clients, data_in, data_out, epoll):
    from google.protobuf.message import DecodeError
    if len(data_in[fd]) == 0:
        return

    msg_len, offset = _DecodeVarint32(data_in[fd], 0);
    if len(data_in[fd]) < offset + msg_len:
        return

    msg_buf = data_in[fd][offset:offset + msg_len]
    message = message_pb2.Message()
    try:
        message.ParseFromString(msg_buf)
        data_in[fd] = data_in[fd][offset + msg_len:]
    except DecodeError:
        return
    data_in[fd] = data_in[fd][offset + msg_len:]

    key = message.key
    if key == "build":
        handle_build_packet(fd, clients, data_out, epoll, message)
    else:
        print("Unexpected packet received: {}!".format(key))

def main():
    with socketcontext(socket.AF_INET, socket.SOCK_STREAM) as server, epollcontext(server.fileno(), select.EPOLLIN) as epoll \
        , open("lights.json", "a+") as json_file:
        global lights
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("0.0.0.0", 6969))
        server.listen(5)
        server.setblocking(0)
        server.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        print("Listening on 0.0.0.0:6969")

        if os.stat("lights.json").st_size != 0:
            json_file.seek(0)
            json_data = json_file.read()
            lights = json.loads(json_data, object_hook=decode_fixture)

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
                    if on_data_in(sockets[fd], data_in, epoll) < 0:
                        print("Disconnecting client {:02d}!".format(fd))
                        epoll.unregister(fd)
                        sockets[fd].close()
                        clients.remove(fd)
                        del sockets[fd], data_in[fd], data_out[fd]
                elif event & select.EPOLLOUT:
                    on_data_out(fd, sockets, data_out, epoll)

            for fd in clients:
                handle_clients(fd, clients, data_in, data_out, epoll)

def cleanup():
    with open("lights.json", "w") as json_file:
        json_output = json.dumps(lights, default=encode_fixture)
        json_file.seek(0)
        json_file.write(json_output)
        json_file.truncate()

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        cleanup()
        print("Shutting down...")

