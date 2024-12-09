import argparse
import logging
import time
import signal
import sys

import pyipmi

from exceptions import RetryError
from super_micro_fan_controller import SuperMicroFanControl

logger = logging.getLogger(__name__)


def signal_handler(signum, frame):
    print("Caught Ctrl+C! Exiting gracefully...")
    sys.exit()


if __name__ == "__main__":
    DEFAULT_FAN_SPEED = 30

    signal.signal(signal.SIGINT, signal_handler)

    argparser = argparse.ArgumentParser(description="Control SuperMicro fan speeds")
    argparser.add_argument("ip", type=str, help="IP address of the BMC")
    argparser.add_argument("username", type=str, help="Username for the BMC")
    argparser.add_argument("password", type=str, help="Password for the BMC")
    argparser.add_argument(
        "--mode", choices=["standard", "optimal", "heavy_io", "manual"], help="Fan control mode", default="manual"
    )
    argparser.add_argument("--monitor", action="store_true", help="Monitor settings")
    argparser.add_argument("--monitor-poll-rate", type=int, help="Monitor poll rate in seconds", default=1)
    argparser.add_argument("--retry-timeout", type=int, help="Retry timeout in seconds", default=10)
    argparser.add_argument("--retry-count", type=int, help="Retry count", default=-1)
    argparser.add_argument("--show-temperatures", action="store_true", help="Show temperatures")
    group = argparser.add_mutually_exclusive_group()
    group.add_argument("--cpu", type=int, help="Fan speed for the CPU zone")
    group.add_argument("--peripheral", type=int, help="Fan speed for the peripheral zone")
    group.add_argument(
        "--speeds",
        type=int,
        nargs="*",
        help="Fan speeds for the CPU and peripheral zones",
        default=[DEFAULT_FAN_SPEED, DEFAULT_FAN_SPEED],
    )

    args = argparser.parse_args()

    logging.basicConfig(level=logging.DEBUG)

    try:
        fan_controller = SuperMicroFanControl(args.ip, args.username, args.password)
    except RetryError as e:
        logger.error(f"Failed to establish IPMI connection: {e}")

    logger.info(f"Current fan mode: {fan_controller.get_fan_mode()}")

    fan_mode = SuperMicroFanControl.FanMode.OPTIMAL
    match args.mode:
        case "standard":
            fan_mode = SuperMicroFanControl.FanMode.STANDARD
        case "optimal":
            fan_mode = SuperMicroFanControl.FanMode.OPTIMAL
        case "heavy_io":
            fan_mode = SuperMicroFanControl.FanMode.HEAVY_IO
        case "manual":
            fan_mode = SuperMicroFanControl.FanMode.FULL_SPEED

    if args.mode != "manual":
        logging.info(f"Setting fan mode to {fan_mode}")
        sys.exit()

    fan_speeds = {
        SuperMicroFanControl.Zones.CPU: DEFAULT_FAN_SPEED,
        SuperMicroFanControl.Zones.PERIPHERAL: DEFAULT_FAN_SPEED,
    }

    if args.cpu:
        logging.info(f"Setting CPU fan speed to {args.cpu}")
        fan_speeds[SuperMicroFanControl.Zones.CPU] = args.cpu
        logging.debug(f"Fan speeds: {fan_speeds}")
    elif args.peripheral:
        logging.info(f"Setting CPU fan speed to {args.peripheral}")
        fan_speeds[SuperMicroFanControl.Zones.PERIPHERAL] = args.peripheral
    elif args.speeds:
        logging.info(f"Setting fan speeds to {args.speeds}")
        fan_speeds = {
            SuperMicroFanControl.Zones.CPU: args.speeds[0],
            SuperMicroFanControl.Zones.PERIPHERAL: args.speeds[1],
        }

    fan_controller.set_fan_speed(fan_speeds)

    if args.monitor:
        while True:
            fan_mode = fan_controller.get_fan_mode()
            logging.info(f"Fan mode: {fan_mode}")

            if fan_mode != fan_controller.FanMode.FULL_SPEED:
                fan_controller.set_fan_mode(fan_controller.FanMode.FULL_SPEED)
                fan_controller.set_fan_speed(fan_speeds)

            time.sleep(args.monitor_poll_rate)

    if args.show_temperatures:
        for sensor, value in fan_controller.get_temperatures().items():
            logging.info(f"{sensor: <20}: {value}")
