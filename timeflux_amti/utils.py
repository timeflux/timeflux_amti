import sys


class path_context:

    def __init__(self, path):
        self.path = path

    def __enter__(self):
        if self.path:
            sys.path.insert(0, self.path)

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if self.path:
                sys.path.remove(self.path)
        except ValueError:
            pass
