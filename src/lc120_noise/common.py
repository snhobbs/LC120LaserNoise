import logging
import tomllib
from dataclasses import dataclass, asdict
import json
import time
from pathlib import Path
import numpy as np

log_ = logging.getLogger("lc120_noise")

@dataclass
class PhotorecieverConfig:
    bandwidth: float
    transimpedance: float

@dataclass
class LaserConfig:
    baudrate: int = 115200

@dataclass
class OscilloscopeConfig:
    channel: int = 1
    timescale: str = "100us"  #  s/div
    attenuation: float = 1
    scale: float = 1  #  V/DIV
    coupling: str = "D1M"
    offset: str = "0"
    averages: int = 1
    npoints: int = 14000

@dataclass
class MeasurementConfig:
    path: str
    name: str = "untitled"
    continue_on_restart: bool = False
    repetitions: int = 1
    ntemp: int = 10
    ncurrent: int = 100
    temp_sleep: float = 1
    current_sleep: float = 0.05
    temp_min: float = 0
    temp_max: float = 35
    temp_step: float = 35
    current_min: float = 1e-3
    current_max: float = 100e-3
    current_step: float = 1e-3
    measurement_sleep: float = 10e-3 # Time inbetween samples

    @property
    def run_path(self):
        return Path(self.path) / self.name

@dataclass
class Config:
    laser: LaserConfig
    oscilloscope: OscilloscopeConfig
    measurement: MeasurementConfig
    photoreceiver: PhotorecieverConfig

def load_config(path: str) -> Config:
    with open(path, 'rb') as f:
        raw = tomllib.load(f)

    print(raw.keys(), 'oscilloscope' in raw.keys())
    return Config(
        laser=LaserConfig(**raw["laser"]),
        oscilloscope=OscilloscopeConfig(**raw['oscilloscope']),
        measurement=MeasurementConfig(**raw['measurement']),
        photoreceiver=PhotorecieverConfig(**raw['photoreceiver'])
    )

@dataclass
class FileStructure:
    path: Path

    def get_config_path(self):
        return self.path/"config.toml"

    def get_config(self):
        return load_config(self.get_config_path())

    def get_all_runs(self):
        config = self.get_config()
        mconfig = config.measurement
        temps = np.arange(start=mconfig.temp_min, stop=mconfig.temp_max, step=mconfig.temp_step)
        temps = temps[temps <= mconfig.temp_max]
        currents = np.arange(start=mconfig.current_min, stop=mconfig.current_max, step=mconfig.current_step)
        currents = currents[currents <= mconfig.current_max]

        runs = []
        for current in currents:
            for temp in temps:
                runs.append(
                    RunFileStructure(
                        config=mconfig, base_path=self.path,
                        current=current, temp=temp))
        return runs

@dataclass
class RunHeader:
    path: str
    current: float
    temp: float
    timestamp: float

@dataclass
class RunFileStructure:
    base_path: Path
    config: MeasurementConfig
    current: float
    temp: float

    @property
    def path(self):
        return self.base_path / self.dir_name

    @property
    def dir_name(self):
        return f"{self.current*1e6:.0f}uA-{self.temp*1e3:.0f}mK"

    @property
    def header_path(self):
        return self.path / "header.json"

    def write_header(self):
        timestamp = float(time.time())
        header = RunHeader(current=float(self.current), temp=float(self.temp), timestamp=timestamp, path=self.path.as_posix())
        with open(self.header_path, 'w') as f:
            json.dump(asdict(header), f, indent=2)

    def get_scope_trace_path(self, n: int=0):
        '''
        Scope trace, 1 of n
        '''
        return self.path / f"scope-trace-{n}.csv"

    @property
    def laser_state_path(self):
        '''
        Laser state at the beginning of traces
        '''
        return self.path / "laser-state.json"

    def get_all_files(self):
        files = [self.laser_state_path, self.header_path]
        files.extend([self.get_scope_trace_path(n) for n in range(self.config.repetitions)])
        return files

    def is_complete(self):
        return all([f.exists() for f in self.get_all_files()])

    def write_scope_trace(self, n, trace, header):
        path = self.get_scope_trace_path(n)
        log_.debug("Writing %s", str(path))
        if path.exists():
            log_.warning("Overwriting %s", str(path))
        with open(path, 'w') as f:
            for key, value in header.items():
                f.write(f"#{key};{value}\n")
            f.write(",".join(trace))

    def write_laser_state(self, state: dict):
        path = self.laser_state_path
        if path.exists():
            log_.warning("Overwriting %s", str(path))
        with open(path, 'w') as f:
            json.dump(state, f, indent=2)

    def load_laser_state(self):
        path = self.laser_state_path
        assert path.exists()
        with open(path, 'r') as f:
            return json.load(f)

    def load_scope_trace(self, n):
        header = {}
        trace = None
        path = self.get_scope_trace_path(n)
        assert path.exists()
        with open(path, 'r') as f:
            for line in f:
                if line.strip()[0] == '#':
                    key, value = line.split(';')
                    header[key.strip()[1:]] = value.strip()
                else:
                    trace = np.array(line.split(','), dtype=float)
            return trace, header

    def load_scope_traces(self):
        traces = []
        for n in range(self.config.repetitions):
            traces.append(self.load_scope_trace(n))
        return traces
