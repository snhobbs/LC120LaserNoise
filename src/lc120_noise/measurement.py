import logging
import time
from typing import List
from pathlib import Path
import shutil
import os
import pyvisa
from . common import load_config, FileStructure, Config, LaserConfig, MeasurementConfig, OscilloscopeConfig, RunFileStructure

g_rm = pyvisa.ResourceManager()
log_ = logging.getLogger("lc120_noise")


class Sds1000x_eOscilloscope:
    def __init__(self, device, config=None):
        self.device = device
        self.config = config

    def setup(self, config: OscilloscopeConfig):
        self.config = config
        scope = self.device
        log_.info(scope.query("*IDN?"))

        scope.timeout = 5000  # ms
        scope.write_termination = '\n'
        scope.read_termination = '\n'
        scope.chunk_size = 1024*1024*1024

        ch = f"C{config.channel}"

        valid_timedivs = ['1NS','2NS','5NS','10NS','20NS','50NS','100NS','200NS',
        '500NS','1US','2US','5US','10US','20US','50US','100US','200US','500US','1MS',
        '2MS','5MS','10MS','20MS','50MS','100MS','200MS','500MS','1S','2S','5S','10S','20S','50S','100S']
        if str(config.timescale).upper() not in valid_timedivs:
            raise ValueError("Timediv value invalid")


        cmds = [
            f"{ch}:COUPLING {config.coupling}",
            f"TIME_DIV {config.timescale}",
            f"{ch}:ATTENUATION {config.attenuation}",
            f"WAVEFORM_SETUP SP,0,NP,{config.npoints},FP,0",
            f"BANDWIDTH_LIMIT {ch},ON",
            f"{ch}:OFFSET {config.offset}",
            f"{ch}:SKEW 0",
            f"{ch}:TRACE ON",
            f"{ch}:VOLT_DIV {config.scale}",
            f"{ch}:INVERTSET OFF",
            "BUZZER OFF",
            "TRIG_MODE AUTO",  #  There's no force trigger
            f"TRIG_SELECT EDGE,SR,{ch},HT,TI,OFF,HV,0",
        ]

        if config.averages > 1:
            cmds.append(f"ACQUIRE_WAY AVERAGE,{config.averages}")

        else:
            cmds.append("ACQUIRE_WAY SAMPLING")

        for cmd in cmds:
            scope.write(cmd)
            while int(scope.query("*OPC?")) != 1:
                continue
            log_.info(cmd)

    def read_state(self):
        config = self.config
        scope = self.device
        ch = f"C{config.channel}"
        state = {}
        fields = [
            "*IDN",
            "SAMPLE_RATE",
            "ACQUIRE_WAY",
            "MEMORY_SIZE",
            f"SAMPLE_NUM {ch}",
            "ATTENUATION",
            "BANDWIDTH_LIMIT",
            "COUPLING",
            "OFFSET",
            "SKEW",
            f"{ch}:TRACE",
            f"{ch}:VOLT_DIV",
            "TIME_DIV",
            "TRIG_DELAY",
            "TRIG_MODE"]
        for field in fields:
            state[field] = scope.query(field+'?')
        return state

    def take_trace(self):
        '''
        CH{n}:WAVEFORM? DAT2 returns
        '''
        scope = self.device
        scope.write("ARM_ACQUISITION")
        while True:
            inr = int(scope.query("INR?"))
            if inr & 0x1:  #  A new signal has been acquired
                break
            if inr & (1<<3):  # timeout
                log_.warning("Acquistion timeout")

        # Return the main data include the head, the
        # wave data and the ending flag. The length of data is
        # current memory depth.
        ch = f"C{self.config.channel}"
        trace = scope.query_binary_values(f"{ch}:WAVEFORM? DAT2", datatype='i', is_big_endian=True)

        scope_header = self.read_state()
        for field in ["SAMPLE_RATE?"]:
            scope_header[field] = scope.query(field)
        scope_header[":WAV:XINC?"] = 1/float(scope_header["SAMPLE_RATE?"].split(" ")[1].replace('Sa/s', ''))
        return trace, scope_header


class Lc120Laser:
    def __init__(self, device, config=None):
        self.device = device
        self.config = config

    def setup(self, config: LaserConfig):
        self.config = config
        laser = self.device
        laser.baud_rate = config.baudrate

        laser.data_bits = 8
        laser.stop_bits = pyvisa.constants.StopBits.one
        laser.parity = pyvisa.constants.Parity.none

        laser.write_termination = '\n'
        laser.read_termination = '\n'

    def read_state(self):
        laser = self.device
        state = {}
        fields = ["DATE", "LEN", "LON", "ILAS_LIM", "ILAS_SP", "ILAS", "ITEC", "ITEC_ILIM", "TEMP", "TEMP_SP", "TECMODE", "VCC", "VCC_SP", "PI", "PI_STATE", "PI_RATE", "VDD", "TSCAL", "VREF", "VS"]
        for field in fields:
            state[field] = laser.query(field+'?')
        return state

def get_scope(resource_name):
    try:
        scope = g_rm.open_resource(resource_name)
        return scope
    except Exception as e:
        msg = f"Could not connect to {resource_name}: {e}"
        raise Exception(msg)

def get_laser(resource_name="/dev/ttyUSB0"):
    try:
        dev = g_rm.open_resource(f'ASRL{resource_name}::INSTR')
        return dev
    except Exception as e:
        msg = f"Could not connect to {resource_name}: {e}"
        raise Exception(msg)

def measurement_loop(config: Config, devices: dict, runs: List[RunFileStructure]):
    sorted_runs = sorted(runs, key=lambda x: (x.temp, x.current))
    mconfig = config.measurement

    laser = devices["laser"]
    scope = devices["scope"]
    scope.setup(config=config.oscilloscope)
    laser.setup(config=config.laser)

    last_run = None
    for run in sorted_runs:
        if last_run is None or last_run.temp != run.temp:
            cmd = f"set_temperature {run.temp}"
            laser.device.write(cmd)
            log_.debug("Sleeping %f", mconfig.temp_sleep)
            time.sleep(mconfig.temp_sleep)
            log_.debug(cmd)

        if last_run is None or last_run.current != run.current:
            cmd = f"set_current {run.current}"
            laser.device.write(cmd)
            log_.debug(cmd)
            log_.debug("Sleeping %f", mconfig.current_sleep)
            time.sleep(mconfig.current_sleep)

        if not run.path.exists():
            os.mkdir(run.path)
        run.write_header()
        for i in range(mconfig.repetitions):
            trace, header = scope.take_trace()
            run.write_scope_trace(trace=trace, header=header, n=i)

        laser_state = laser.read_state()
        run.write_laser_state(laser_state)


def run_measurement(toml: Path, **kwargs):
    '''
    Allow restarting!
    '''
    config = load_config(toml)
    mconfig = config.measurement
    fstructure = FileStructure(mconfig.run_path)

    if mconfig.run_path.exists():
        if mconfig.continue_on_restart:
            # Continue from last position
            log_.info(f"Data run {mconfig.run_path} found, continuing from last position")
            if fstructure.get_config() != config:
                log_.error("Config files do not match, exiting")
        else:
            msg = f"Run path {mconfig.run_path} found and continue_on_restart is false, exiting"
            raise FileExistsError(msg)
    else:
        os.makedirs(mconfig.run_path.as_posix())
        msg = f"Making run directory {mconfig.run_path}"
        log_.info(msg)

    '''
    Copy the config file into the header of the datarun when created
    we already checked to make sure the last one is equal
    '''
    shutil.copy2(toml, fstructure.get_config_path())

    # Scan file headers for current and temp settings, remove those from the data runs.
    runs = fstructure.get_all_runs()
    filtered_runs = [run for run in runs if not run.is_complete()]
    if not len(filtered_runs):
        log_.info("All runs are complete")
        return
    else:
        if len(runs) != len(filtered_runs):
            log_.info("Partial data run, running %d/%d", len(filtered_runs), len(runs))
        measurement_loop(config, runs=filtered_runs, **kwargs)
