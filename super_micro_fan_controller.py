import logging
import time
from enum import Enum

import pyipmi
import pyipmi.interfaces
from pyipmi.msgs.constants import (CMDID_GET_SDR, 
    CMDID_GET_SDR_REPOSITORY_INFO, 
    CMDID_GET_SENSOR_READING, 
    CMDID_RESERVE_SDR_REPOSITORY, 
    NETFN_SENSOR_EVENT, 
    NETFN_STORAGE)
from pyipmi.sdr import (SDR_TYPE_COMPACT_SENSOR_RECORD, SDR_TYPE_FULL_SENSOR_RECORD)
from pyipmi.sensor import SENSOR_TYPE_TEMPERATURE

from exceptions import RetryError

logger = logging.getLogger()


class SuperMicroFanControl:
    NETFN_SUPER_OEM = 0x30
    BMC_STATUS = 0x70
    SUPERMICRO_FIRMWARE_INFO = 0x20

    class FanMode(Enum):
        STANDARD = 0x00
        FULL_SPEED = 0x01
        OPTIMAL = 0x02
        PUE2_OPTIMAL = 0x03
        HEAVY_IO = 0x04
        PUE3_OPTIMAL = 0x05

        def __str__(self):
            return self.name

    class Zones(Enum):
        CPU = 0x00
        PERIPHERAL = 0x01

        def __str__(self):
            return self.name

    def __init__(self, ip: str, username: str, password: str, *, retry_timeout: int = 5, retry_count: int = -1):
        self.interface = pyipmi.interfaces.create_interface("ipmitool", interface_type="lan")
        self.connection = pyipmi.create_connection(self.interface)

        self.connection.target = pyipmi.Target(0x20)
        self.connection.session.set_session_type_rmcp(ip)
        self.connection.session.set_auth_type_user(username, password)
        self.connection.session.set_priv_level("ADMINISTRATOR")

        self.connection.session.establish()

        while True:
            try:
                self.connection.session.rmcp_ping()
            except pyipmi.errors.IpmiTimeoutError as e:
                logger.error(f"Failed to establish RMCP connection: {e}")
                logger.error(f"Retrying in {retry_timeout} seconds...")

                if retry_count == 0:
                    raise RetryError

                retry_count -= 1
                time.sleep(retry_timeout)
                continue

    def send_command(self, netfn: int, data: list[int]) -> bytearray:
        return self.connection.raw_command(0, netfn, bytearray(data))

    def get_sdr_record(self, reservation_id: list[int], address: list[int], offset: int = 0x00, bytes_to_read: int = 0xFF):
        response = self.send_command(NETFN_STORAGE, [CMDID_GET_SDR, *reservation_id, *address, offset, bytes_to_read])

        next_address = response[1:3]
        *record_id, sdr_version, record_type, record_length = response[3:8]
        logger.debug(
            f"Next Address: {next_address}, Record ID: {record_id}, SDR Version: {sdr_version}, Record Type: {record_type}, Record Length: {record_length}"
        )

        data = response[8:]

        if len(data) != record_length:
            logger.debug(f"Record length {record_length} does not match the actual data length {len(data)}")
            return None

        if record_type not in [SDR_TYPE_FULL_SENSOR_RECORD, SDR_TYPE_COMPACT_SENSOR_RECORD]:
            logger.debug(f"Skipping record id {record_id}: record type {record_type}")
            return None

        return {
            "next_address": next_address,
            "record_id": record_id,
            "sdr_version": sdr_version,
            "record_type": record_type,
            "record_length": record_length,
            "data": data,
        }

    def get_sensor_reading(self, sensor_number: int):
        response = self.send_command(NETFN_SENSOR_EVENT, [CMDID_GET_SENSOR_READING, sensor_number])

        if len(response) == 1:
            logger.debug(f"No sensor data for sensor {sensor_number}")
            return None

        return response[1]

    def get_fan_mode(self):
        response = self.send_command(self.NETFN_SUPER_OEM, [0x45, 0x00])

        return self.FanMode(response[1])

    def set_fan_mode(self, mode: FanMode):
        logger.info(f"Setting fan mode to {mode}")
        if mode not in SuperMicroFanControl.FanMode:
            raise ValueError(f"Fan mode {mode} is not a valid fan mode")

        return self.send_command(self.NETFN_SUPER_OEM, [0x45, 0x01, mode.value])

    def set_fan_speed(self, fan_speeds: dict[Zones, int]):
        # First check the current fan control mode
        fan_mode = self.get_fan_mode()
        if fan_mode != self.FanMode.FULL_SPEED:
            logging.warning(f"Fan control mode is not set to manual ({fan_mode}). Setting it to manual")
            self.set_fan_mode(self.FanMode.FULL_SPEED)

        time.sleep(1)

        for zone, fan_speed in fan_speeds.items():
            logger.info(f"Setting fan speed for {zone} to {fan_speed}")
            response = self.send_command(self.NETFN_SUPER_OEM, [self.BMC_STATUS, 0x66, 0x01, zone.value, fan_speed])

        return response

    def get_temperatures(self):
        response = self.send_command(NETFN_STORAGE, [CMDID_GET_SDR_REPOSITORY_INFO])

        sdr_count = response[1]

        reservation_id = self.send_command(NETFN_STORAGE, [CMDID_RESERVE_SDR_REPOSITORY])
        logger.debug(f"Reservation ID: {reservation_id}")

        address = [0x00, 0x00]

        sensor_data = {}

        for _ in range(sdr_count):
            record = self.get_sdr_record([*reservation_id[1:]], address)

            if record is None:
                break

            data = record["data"]
            sensor_number = data[2]
            sensor_type = data[7]

            if sensor_type == SENSOR_TYPE_TEMPERATURE:
                id_string = data[43:].decode("utf-8")
                sensor_reading = self.get_sensor_reading(sensor_number)
                logger.debug(f"Sensor {id_string} ({sensor_number}) response: {sensor_reading}")

                sensor_data[id_string] = sensor_reading

            if record["next_address"] == address:
                break

            address = record["next_address"]

        return sensor_data
