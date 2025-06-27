'''
Use the Siglent scope and the LC120
Use pyvisa to control the scope.
'''
import logging
from pathlib import Path
import click
from .measurement import get_laser, get_scope, run_measurement

log_ = logging.getLogger("lc120_noise")

class MockDevice():
    def write(self, *args, **kwargs):
        pass

    def read(self, *args, **kwargs):
        return ""

    def query(self, *args, **kwargs):
        if args[0] == ":WAV:DATA?":
            return ['0.1']*1000
        if args[0] == ":WAV:XINC?":
            return 1
        return self.read(*args, **kwargs)


@click.group()
def cli():
    """Siglent Oscilloscope CLI using PyVISA"""
    pass

@cli.command()
@click.option('--scope', 'scope_resource', help="Scope USB resource")
@click.option('--laser', "laser_resource", help="Laser serial port")
@click.option('--toml', required=True, help="toml file for setting up the data run")
@click.option('--debug', default=logging.INFO, type=int, help="Logging level")
def main(scope_resource, laser_resource, toml, debug):
    logging.basicConfig(level=debug)
    #laser = get_laser(laser_resource)
    #scope = get_scope(scope_resource)
    laser = MockDevice()
    scope = MockDevice()
    devices = {"scope": scope, "laser": laser}
    run_measurement(Path(toml), devices=devices)

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    cli()
