import itertools


class ProxyPool:

    def __init__(self):

        # Пока список пустой.
        # Позже сюда просто добавим реальные прокси.
        self.proxies = []

        self.cycle = None

    def load(self, proxies: list[str]):

        self.proxies = proxies

        self.cycle = itertools.cycle(self.proxies)

    def get_proxy(self):

        if not self.proxies:
            return None

        return next(self.cycle)