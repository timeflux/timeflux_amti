import argparse
import datetime
import logging

from timeflux_amti.driver import ForceDriver


def main():
    logging.basicConfig(level=logging.DEBUG)
    parser = argparse.ArgumentParser()
    parser.add_argument('--dll-dir',
                        required=True,
                        help='Path to directory containing AMTIUSBDevice.dll')
    parser.add_argument('--rate', default=500, type=int,
                        help='Sampling rate')
    parser.add_argument('--device', default=0, type=int,
                        help='Device index')
    parser.add_argument('--time', default=10, type=int,
                        help='Acquire for this number of seconds, then exit')
    args = parser.parse_args()

    amti = ForceDriver(rate=args.rate, dll_dir=args.dll_dir, device_index=args.device)
    tic = datetime.datetime.now()
    while True:
        amti.update()
        toc = datetime.datetime.now()
        if (toc - tic).total_seconds() > args.time:
            break
    amti.terminate()


if __name__ == '__main__':
    main()
