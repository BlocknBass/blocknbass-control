import socket
import select

sockets = {}

def main():
    epoll = select.epoll() 
    for i in range(500):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        fd = sock.fileno()
        sock.connect(("chat.bearservers.net", 6969))
        sock.setblocking(0)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        epoll.register(sock, select.EPOLLIN | select.EPOLLET)
        sockets[fd] = sock

    while True:
        events = epoll.poll(1)
        for fd, event in events:
            sockets[fd].recv(4096)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("Shutting down...")
