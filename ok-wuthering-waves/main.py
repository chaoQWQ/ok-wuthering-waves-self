if __name__ == '__main__':
    from multiprocessing import freeze_support
    freeze_support()
    from config import config
    from ok import OK

    config = config
    ok = OK(config)
    ok.start()
