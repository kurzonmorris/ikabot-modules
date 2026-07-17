import multiprocessing
import sys

if __name__ == '__main__':
    if sys.platform.startswith('win'):
        multiprocessing.freeze_support()
    from ikabot.command_line import main
    main()
