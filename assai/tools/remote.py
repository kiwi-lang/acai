import torch
import traceback
from multiprocessing import Pipe, Process, set_start_method


def _background_process(remote, parent_remote, init, *args, **kwargs):
    parent_remote.close()

    try:
        obj = init(*args, **kwargs)
    except:
        traceback.print_exc()
        remote.send(("error", None))
        return -1

    while True:
        cmd, data = remote.recv()

        match cmd:
            case "__call__":

                remote.send(("start", None))

                try:
                    result = obj(*data["args"], **data["kwargs"])
                    
                    for item in result:
                        remote.send(("item", item))

                    remote.send(("end", None))
            
                except:
                    traceback.print_exc()
                    remote.send(("error", None))

            case "close":
                return 0


class RemoteModel:
    """Some frameworks are unhappy about being inside flask
    So we spawn a process for them to use them.

    This is solely to experiment with them, not deploy
    """
    def __init__(self, init, model_name):
        self.remote, self.work_remote = Pipe()

        set_start_method("spawn", force=True)
        self.worker = Process(
            target=_background_process,
            args=(self.work_remote, self.remote, init, model_name)
        )
        self.worker.start()
        self.work_remote.close()

    def __call__(self, *args, **kwargs):
        self.remote.send(("__call__", {"args": args, "kwargs": kwargs}))

        while True:
            cmd, data = self.remote.recv()

            match cmd:
                case "start":
                    pass

                case "item":
                    yield data

                case "end":
                    return

                case "error":
                    return

    def __del__(self):
        self.remote.send(("close", None))
        self.remote.close()
        self.worker.join()
        self.worker.close()
