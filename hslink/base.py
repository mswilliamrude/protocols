class HSLinkBase:
    def __init__(self, getc, putc):
        self.getc = getc
        self.putc = putc

    def _recv(self, size=1, timeout=1):
        return self.getc(size, timeout)

    def _send(self, data, timeout=1):
        return self.putc(data, timeout)
