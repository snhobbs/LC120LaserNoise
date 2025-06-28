'''
Use the Siglent scope and the LC120
Use pyvisa to control the scope.
'''
import logging
from pathlib import Path
import click
import numpy as np
from .measurement import get_laser, get_scope, run_measurement, Sds1000x_eOscilloscope, Lc120Laser

log_ = logging.getLogger("lc120_noise")

class MockDevice():
    def __init__(self, responses: dict):
        self.responses = responses
    def write(self, *args, **kwargs):
        pass

    def read(self, *args, **kwargs):
        return ""

    def query(self, *args, **kwargs):
        key = args[0].split(" ")[0]
        if key in self.responses:
            return self.responses[key]()
        return self.read(*args, **kwargs)

    def query_binary_values(self, *args, **kwargs):
        return self.query(*args, **kwargs)

laser_responses = {}
scope_reponses = {"*OPC?": lambda: 1,
                  "SAMPLE_RATE?": lambda: "SARA 5.00E+05Sa/s",
                  "C1:WAVEFORM?": lambda: np.array(np.random.rand(1000), dtype=str),
                  "C2:WAVEFORM?": lambda: np.array(np.random.rand(1000), dtype=str),
                  "INR?": lambda: 1}

@click.group()
def cli():
    pass

@cli.command()
@click.option('--scope', 'scope_resource', help="Scope USB resource")
@click.option('--laser', "laser_resource", help="Laser serial port")
@click.option('--toml', required=True, help="toml file for setting up the data run")
@click.option('--debug', default=logging.INFO, type=int, help="Logging level")
@click.option('--mock', is_flag=True, help="Use mock interfaces for debug")
def main(scope_resource, laser_resource, toml, debug, mock):
    logging.basicConfig(level=debug)
    if mock:
        laser = MockDevice(laser_responses)
        scope = MockDevice(scope_reponses)
    else:
        if laser_resource or scope_resource is None:
            raise click.ClickException("Set the resource values")
        laser = get_laser(laser_resource)
        scope = get_scope(scope_resource)
    devices = {"scope": Sds1000x_eOscilloscope(scope), "laser": Lc120Laser(laser)}
    run_measurement(Path(toml), devices=devices)

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    cli()
