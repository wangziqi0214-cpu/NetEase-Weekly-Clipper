import requests

_SESSION = requests.Session()


def GetCurrentSession():
    return _SESSION
