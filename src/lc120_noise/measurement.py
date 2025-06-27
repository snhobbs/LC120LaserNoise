import logging
import time
from typing import List
from pathlib import Path
import shutil
import os
import pyvisa
from . common import load_config, FileStructure, Config, LaserConfig, MeasurementConfig, OscilliscopeConfig, RunFileStructure

g_rm = pyvisa.ResourceManager()
log_ = logging.getLogger("lc120_noise")


def get_scope(resource_name):
    try:
        scope = g_rm.open_resource(resource_name)
        return scope
    except Exception as e:
        msg = f"Could not connect to {resource_name}: {e}"
        raise Exception(msg)


def setup_scope(config: OscilliscopeConfig, scope):
    log_.info(scope.query("*IDN?"))

    scope.timeout = 5000  # ms
    scope.write_termination = '\n'
    scope.read_termination = '\n'

    ch = config.channel
    cmd = f"CHAN{ch}:COUPLING {config.coupling}"
    scope.write(cmd)
    log_.info(cmd)

    timebase = config.timescale
    cmd = f":TIM:SCAL {timebase}"
    scope.write(cmd)
    log_.info(cmd)

    scale = config.scale
    cmd = f"CHAN{ch}:SCAL {scale}"
    scope.write(cmd)
    log_.info(cmd)

    scope.write(f":WAV:SOUR CHAN{ch}")         # Source channel
    scope.write(":WAV:MODE NORM")          # Normal acquisition mode
    scope.write(":WAV:FORM ASC")           # ASCII format
    if config.averages > 1:
        scope.write(":ACQuire:TYPE AVERage")
        scope.write(f":ACQuire:AVER {config.averages}")
    else:
        scope.write(":ACQuire:TYPE NORMal")
    return scope


def get_laser(resource_name="/dev/ttyUSB0"):
    try:
        dev = g_rm.open_resource(f'ASRL{resource_name}::INSTR')
        dev.baud_rate = 9600
        dev.data_bits = 8
        dev.stop_bits = pyvisa.constants.StopBits.one
        dev.parity = pyvisa.constants.Parity.none

        dev.write_termination = '\n'
        dev.read_termination = '\n'
        return dev
    except Exception as e:
        msg = f"Could not connect to {resource_name}: {e}"
        raise Exception(msg)


def setup_laser(config: LaserConfig, laser):
    laser.baud_rate = config.baudrate
    return laser

def read_laser_state(laser):
    state = dict()
    fields = ["DATE", "LEN", "LON", "ILAS_LIM", "ILAS_SP", "ILAS", "ITEC", "ITEC_ILIM", "TEMP", "TEMP_SP", "TECMODE", "VCC", "VCC_SP", "PI", "PI_STATE", "PI_RATE", "VDD", "TSCAL", "VREF", "VS"]
    for field in fields:
        state[field] = laser.query(field+'?')
    return state

def read_scope_state(scope, config: Config):
    channel = config.oscilliscope.channel
    state= dict()
    fields = ["*IDN", ":TIM:SCAL", f":CHAN{channel}:SCAL", f":CHAN{channel}:COUPling", f":CHAN{channel}:DISPlay"]
    for field in fields:
        state[field] = scope.query(field+'?')
    return state

def measurement_loop(config: Config, devices: dict, runs: List[RunFileStructure]):
    sorted_runs = sorted(runs, key=lambda x: (x.temp, x.current))
    mconfig = config.measurement

    laser = devices["laser"]
    scope = devices["scope"]
    setup_scope(scope=scope, config=config.oscilliscope)
    setup_laser(laser=laser, config=config.laser)

    last_run = None
    for run in sorted_runs:
        if last_run is None or last_run.temp != run.temp:
            cmd = f"set_temperature {run.temp}"
            laser.write(cmd)
            log_.debug("Sleeping %f", mconfig.temp_sleep)
            time.sleep(mconfig.temp_sleep)
            log_.debug(cmd)

        if last_run is None or last_run.current != run.current:
            cmd = f"set_current {run.current}"
            laser.write(cmd)
            log_.debug(cmd)
            log_.debug("Sleeping %f", mconfig.current_sleep)
            time.sleep(mconfig.current_sleep)

        if not run.path.exists():
            os.mkdir(run.path)
        run.write_header()
        scope_header = read_scope_state(scope, config)
        for i in range(mconfig.repetitions):
            trace = scope.query(":WAV:DATA?")
            # Optional: Get time axis

            for field in [":WAV:XOR?", ":WAV:XINC?"]:
                scope_header[field] = scope.query(field)
            run.write_scope_trace(trace=trace, header=scope_header, n=i)

        laser_state = read_laser_state(laser)
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
