#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Script to convert ASCII files of radiosonde type RS41

Original version by: Johannes Kiliani/Lukas Frank
"""

# insert some subroutines if possible
import time
import shutil
import datetime
import calendar
import os.path
import sys
import glob
import subprocess as sp
import configparser
from configparser import ExtendedInterpolation
import argparse
import logging
import numpy as np
from pathlib import Path
import netCDF4
from netCDF4 import Dataset, default_fillvals, num2date, date2num

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from config import cfg_creator as configupdater
from _helpers import *

try:
    import eurec4a_snd
    __version__ = eurec4a_snd.__version__
except ModuleNotFoundError:
    print('Not found')
    __version__ = 'see git_version'

# ====================================================
# General MPI-BCO settings:
# ====================================================


def load_configuration(configuration_file=None):
    """
    Loads the configuration file PATH.ini.
    1. If provided load configuration_file
    2. Attempt to load from home directory
    3. Attempt to load from relative path inside BCO-git structure

    Args:
        configuration_file: optional: complete path to the configuration file.

    Returns:
        instance of ConfigParser class with extended interpolation.
    """
    dir_path = os.path.dirname(os.path.realpath(__file__))
    ini_path = "/".join(dir_path.split("/")[:-1]) + "/eurec4a_snd/config/meta_information.ini"
    if not isinstance(configuration_file, str):
        possible_file_in_userdir = Path("~/meta_information.ini").expanduser()
        if os.path.isfile(possible_file_in_userdir):
            configuration_file = possible_file_in_userdir
        elif os.path.isfile(ini_path):
            configuration_file = ini_path
        if configuration_file is None or not os.path.isfile(configuration_file):
            raise FileNotFoundError(
                "No Configuration File 'meta_information.ini' found. Please create one"
                " in your home directory "
                "or provide the path via the argument parsing -c.")
        else:
            logging.info("Using configuration file: %s" % configuration_file)

    conf = configparser.ConfigParser(interpolation=ExtendedInterpolation())
    conf.read(configuration_file)
    return conf


def get_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('-c', '--configfile', metavar="meta_information.ini", help='Provide a meta_information.ini configuration file. \n'
                                                                       'If not provided it will be searched for at:\n'
                                                                       '1. ~/meta_information.ini\n'
                                                                       '2. ../../../meta_information.ini', required=False, default=None)
    parser.add_argument("-i", "--inputfile", metavar="INPUT_FILE",
                        help="Single sonde file (bufr) or file format\n"
                             "including wildcards", default=None, required=False)

    parser.add_argument("-p", "--inputpath", metavar='/some/example/path/',
                        help="Path to the folder containing sonde bufr files",
                        default=None,
                        required=False)

    parser.add_argument("-o", "--outputfolder", metavar="/some/example/path/",
                        help="Output folder for converted files (netCDF)",
                        default=None,
                        required=False)

    parser.add_argument('-v', '--verbose', metavar="DEBUG",
                        help='Set the level of verbosity [DEBUG, INFO,'
                        ' WARNING, ERROR]',
                        required=False, default="INFO")

    parser.add_argument('-d', '--date', metavar="YYYYMMDD", help='Provide the desired date to be processed. '
                                                                 'Fomat: YYYYMMDD', required=False, default=None)
    parsed_args = vars(parser.parse_args())

    if (parsed_args["date"] is None) and (parsed_args["inputfile"] is None):
        parser.error(
            "either --date or --inputfile needs to be set. (--date not yet implemented)")

    return parsed_args


def setup_logging(verbose):
    assert verbose in ["DEBUG", "INFO", "WARNING", "ERROR"]
    logging.basicConfig(
        level=logging.getLevelName(verbose),
        format="%(levelname)s - %(name)s - %(funcName)s - %(message)s",
        handlers=[
            logging.FileHandler("{}.log".format(__file__)),
            logging.StreamHandler()
        ])


def main():
    # Set up global configuration of BCO-MPI-GIT:
    args = get_args()
    setup_logging(args['verbose'])

    try:
        config = load_configuration(args["configfile"])
    except FileNotFoundError:
        logging.info('No configuration file could be found and will now'
                     ' be created with your help')
        configupdater.update_config(os.path.abspath(os.path.dirname(__file__)) +
            '/config/meta_information_template.ini',
            Path('~/meta_information.ini').expanduser())
        sys.exit("Config file has been created at {0}. Please restart script with the option -c {0}".format(Path('~/meta_information.ini').expanduser()))
        if args["outputfolder"] is None and (args["inputpath"] is None and args["inputpath"] is None):
            sys.exit("No config file found! Outputfolder and Inputpath"
                     " or Inputfile need to be provided!")
        else:
            logging.warning("The file meta_information.ini could not be found and"
                            " no metainformation will be added to the output! "
                            " This is not recommended!")
            pass
    else:
        if args["inputpath"] is None:
            args["inputpath"] = config["FILES"]["INPUT_DAT"]
        if args["outputfolder"] is None:
            args["outputfolder"] = config["FILES"]["OUTPUT_DAT2NC"]

    try:
        git_module_version = sp.check_output(
            ["git", "describe", "--always"]).strip()
    except:
        logging.info('No git-version could be found. Please consider'
                     'pulling the git repository.')
        git_module_version = "--"

    time_in = time.time()
    date_unit = "seconds since 1970-01-01 00:00:00 UTC"

    # Creating file list according to given arguments
    if args['inputfile'] is None:
        filelist = glob.glob(args['inputpath'] + '*.bfr')
    else:
        filelist = glob.glob(args['inputfile'])
    filelist = sorted(filelist)

    logging.info('Files to process {}'.format(filelist))
    for ifile, bufr_file in enumerate(filelist):
        logging.info('Reading file number {}'.format(ifile))

        json_file = convert_bufr_to_json(bufr_file)
        json_flat, keys = read_json(json_file)
        shutil.rmtree(os.path.dirname(json_file))

        sounding = convert_json_to_arrays(json_flat, keys)
        sounding = replace_missing_data(sounding)
        sounding = convert_list_to_array(sounding)

        sounding.latitude = calculate_coordinates(
            sounding.station_lat,
            sounding.displacement_lat)
        sounding.longitude = calculate_coordinates(
            sounding.station_lon,
            sounding.displacement_lon)

        sounding = bufr_specific_handling(sounding)

        serial = sounding.meta_data['sonde_serial_number']
        sondetype = sounding.meta_data['radiosondeType']

        direction = get_sounding_direction(sounding.meta_data['bufr_msg'])
        if direction == 1:
            # Upward
            direction_str = 'Ascent'
        elif direction == -1:
            # Downward
            direction_str = 'Descent'

        sounding = expected_unit_check(sounding)

        # after all needed header information is read, the reduced data field
        # is masked for NaN values and an output file produced afterward:

        sounding.time = np.ma.masked_invalid(sounding.time)
#         vvert_m = np.ma.masked_invalid(data[:, 9])
        sounding.gpm = np.ma.masked_invalid(sounding.gpm)
        sounding.pressure = np.ma.masked_invalid(sounding.pressure)
        sounding.temperature = np.ma.masked_invalid(sounding.temperature)
#         rh_m = np.ma.masked_invalid(data[:, 5])
#         # mixing ratio is not including in input file
#         # but remains for compatibility with airport
#         # sounding files
#         mix_m = np.ma.masked_invalid(data[:, 5])
        sounding.dewpoint = np.ma.masked_invalid(sounding.dewpoint)
        sounding.windspeed = np.ma.masked_invalid(sounding.windspeed)
        sounding.winddirection = np.ma.masked_invalid(sounding.winddirection)
        sounding.latitude = np.ma.masked_invalid(sounding.latitude)
        sounding.longitude = np.ma.masked_invalid(sounding.longitude)

#         rh_m = np.ma.masked_outside(rh_m, 0., 100.)
#         pres_m = np.ma.masked_less(pres_m, 5.)
#         vhori_m = np.ma.masked_greater(vhori_m, 100.)
#         mix_m = np.ma.masked_greater(mix_m, -1., )

        # Calculate additional variables
        relative_humidity = 100*(np.exp((17.625*sounding.dewpoint)/(243.04+sounding.dewpoint))/np.exp((17.625*sounding.temperature)/(243.04+sounding.temperature)))
        vapor_pressure = (relative_humidity/100.) * (611.2 * np.exp((17.62*(sounding.temperature))/(243.12 + sounding.temperature)))
        wv_mix_ratio = 1000.*((0.622*vapor_pressure)/(100.*sounding.pressure - vapor_pressure))

        relative_humidity = np.ma.masked_invalid(relative_humidity)
        wv_mix_ratio = np.ma.masked_invalid(wv_mix_ratio)
        # Find temporal resolution
        # using most common time difference
        _, indices = np.unique(np.diff(sounding.time), return_inverse=True)
        time_resolution = np.abs(np.diff(sounding.time)[np.argmax(np.bincount(indices))])

        # Create outputfile with time information from file
        sounding_date = sounding.sounding_start_time
        YYYYMM = sounding.sounding_start_time.strftime('%Y%m')
        YYYYMMDDHHMM = sounding.sounding_start_time.strftime('%Y%m%d%H%M')

        outpath = args['outputfolder'] + YYYYMM + '/'
        if not os.path.exists(outpath):
            success = sp.call(["mkdir", "-p", outpath])

        outfile = outpath + \
            "{platform}_Sounding{direction}_{location}_{date}.nc".\
            format(platform=config['PLATFORM']['platform_name_short'],
                   location=config['PLATFORM']['platform_location'].
                                replace(' ', '').
                                replace(',', '').
                                replace(';', ''),
                   direction='{}Profile'.format(direction_str),
                   date=sounding_date.strftime('%Y%m%d_%H%M'))

        # Creation of output NetCDF file
        fo = Dataset(outfile, 'w', format='NETCDF4')

        # assign NetCDF file attributes from meta data
        fo.title = 'Sounding data containing temperature, pressure, humidity,'\
                   ' latitude, longitude, wind direction, wind speed, and time'
        # Platform information
        fo.platform_name = '{long} ({short})'.format(
            long=config['PLATFORM']['platform_name_long'],
            short=config['PLATFORM']['platform_name_short'])
        fo.location = config['PLATFORM']['platform_location']
        fo.surface_altitude = config['PLATFORM']['platform_altitude']

        # Instrument metadata
        fo.instrument = config['INSTRUMENT']['instrument_description']
        fo.number_of_Probe = serial
        fo.radiosonde_type = sondetype

        # Information about launch
        fo.date_YYYYMMDD = sounding_date.strftime('%Y%m%d')
        fo.time_of_launch_HHmmss = sounding_date.strftime('%H%M%S')
        fo.launch_unixtime = date2num(sounding.sounding_start_time, date_unit)
        fo.latitude_of_launch_location = '{0:5.2f} deg N'.\
            format(sounding.station_lat)
        fo.longitude_of_launch_location = '{0:6.2f} deg E'.\
            format(sounding.station_lon)

        # Information about output
        fo.resolution = "{:g} sec".format(time_resolution)
        fo.source = bufr_file
        fo.git_version = git_module_version
        fo.created_with = '{file} with its last modifications on {time}'.\
            format(time=time.ctime(os.path.getmtime(os.path.realpath(__file__))),
                   file=os.path.basename(__file__))
        fo.created_on = str(time.ctime(time.time()))
        fo.contact_person = '{name} ({mail})'.format(
            name=config['OUTPUT']['contact_person_name'],
            mail=config['OUTPUT']['contact_person_email'])
        fo.institution = config['OUTPUT']['institution']
        fo.converted_by = '{name} ({mail})'.format(
            name=config['OUTPUT']['executive_person_name'],
            mail=config['OUTPUT']['executive_person_email'])
        fo.python_version = "{} (with numpy:{}, netCDF4:{}, eurec4a_snd:{})".\
            format(sys.version, np.__version__, netCDF4.__version__,
                   __version__)
        fo.Conventions = 'CF-1.7'
        fo.featureType = "trajectory"

        # Define Dimension (record length) from ASCII record counter
        fo.createDimension('levels', len(sounding.pressure))
        prof_dim = fo.createDimension('trajectory', None)
        str_dim = fo.createDimension('str_dim', 1000)
        fillval = default_fillvals['f4']

        # Creation of NetCDF Variables, including description and unit
        nc_prof = fo.createVariable(
            'trajectory', 'S1', ('trajectory', 'str_dim'),
            fill_value='',
            zlib=True)
        nc_prof.cf_role = "trajectory_id"
        nc_prof.long_name = 'trajectory identifier'
        nc_prof.description = 'unique string describing the trajectories origin'

        nc_launchtime = fo.createVariable('launch_time', 'f8', ('trajectory'),
            zlib=True)
        nc_launchtime.long_name = "time at which the sonde has been launched"
        nc_launchtime.units = 'seconds since 1970-01-01 00:00:00 UTC'
        nc_launchtime.calendar = 'gregorian'
        nc_launchtime.standard_name = 'time'

        nc_tindex = fo.createVariable(
            'flight_time', 'f4', ('trajectory', 'levels'),
            fill_value=fillval,
            zlib=True)
        nc_tindex.long_name = 'time passed since launch'
        nc_tindex.standard_name = 'time'
        nc_tindex.units = 'seconds since {launch}'.format(
            launch=sounding_date.strftime('%Y-%m-%d %H:%M:%S UTC'))
        nc_tindex.axis = 'T'
        nc_tindex.calendar = "gregorian"
        nc_vvert = fo.createVariable(
            'ascentRate', 'f4', ('trajectory', 'levels'),
            fill_value=fillval,
            zlib=True)
        nc_vvert.long_name = 'ascent/descent rate of balloon or other measuring device'
        nc_vvert.description = 'ascent rate is positive/ descent rate is negative'
        nc_vvert.units = 'm/s'
        nc_vvert.coordinates = "flight_time longitude latitude pressure"
        nc_alti = fo.createVariable(
            'altitude', 'f4', ('trajectory', 'levels'),
            fill_value=fillval,
            zlib=True)
        nc_alti.standard_name = 'altitude'
        nc_alti.units = 'm'
        nc_alti.coordinates = "flight_time longitude latitude pressure"
        nc_pres = fo.createVariable(
            'pressure', 'f4', ('trajectory', 'levels'),
            fill_value=fillval,
            zlib=True)
        nc_pres.standard_name = 'air_pressure'
        nc_pres.units = 'hPa'
        nc_pres.axis = 'Z'
        nc_pres.positive = 'down'
        nc_temp = fo.createVariable(
            'temperature', 'f4', ('trajectory', 'levels'),
            fill_value=fillval,
            zlib=True)
        nc_temp.standard_name = 'air_temperature'
        nc_temp.units = 'degrees_Celsius'
        nc_temp.coordinates = "flight_time longitude latitude pressure"
        nc_rh = fo.createVariable(
            'humidity', 'f4', ('trajectory', 'levels'),
            fill_value=fillval,
            zlib=True)
        nc_rh.standard_name = 'relative_humidity'
        nc_rh.units = '%'
        nc_rh.coordinates = "flight_time longitude latitude pressure"
        nc_dewp = fo.createVariable(
            'dewPoint', 'f4', ('trajectory', 'levels'),
            fill_value=fillval,
            zlib=True)
        nc_dewp.standard_name = 'dew_point_temperature'
        nc_dewp.units = 'degrees_Celsius'
        nc_dewp.coordinates = "flight_time longitude latitude pressure"
        nc_mix = fo.createVariable(
            'mixingRatio', 'f4', ('trajectory', 'levels'),
            fill_value=fillval,
            zlib=True)
        nc_mix.long_name = 'water vapor mixing ratio'
        nc_mix.standard_name = 'humidity_mixing_ratio'
        nc_mix.units = 'g/kg'
        nc_mix.coordinates = "flight_time longitude latitude pressure"
        nc_vhori = fo.createVariable(
            'windSpeed', 'f4', ('trajectory', 'levels'),
            fill_value=fillval,
            zlib=True)
        nc_vhori.standard_name = 'wind_speed'
        nc_vhori.units = 'm/s'
        nc_vhori.coordinates = "flight_time longitude latitude pressure"
        nc_vdir = fo.createVariable(
            'windDirection', 'f4', ('trajectory', 'levels'),
            fill_value=fillval,
            zlib=True)
        nc_vdir.standard_name = 'wind_from_direction'
        nc_vdir.units = 'degrees'
        nc_vdir.coordinates = "flight_time longitude latitude pressure"
        nc_lat = fo.createVariable(
            'latitude', 'f4', ('trajectory', 'levels'),
            fill_value=fillval,
            zlib=True)
        nc_lat.long_name = 'latitude'
        nc_lat.standard_name = 'latitude'
        nc_lat.units = 'degrees_north'
        nc_lat.axis = 'Y'
        nc_long = fo.createVariable(
            'longitude', 'f4', ('trajectory', 'levels'),
            fill_value=fillval,
            zlib=True)
        nc_long.long_name = 'longitude'
        nc_long.standard_name = 'longitude'
        nc_long.units = 'degrees_east'
        nc_long.axis = 'X'

        trajectory_name = '{platform}__{lat:5.2f}_{lon:5.2f}__{launchtime}'.\
                          format(platform=config['PLATFORM']['platform_name_short'],
                                 lat=sounding.station_lat,
                                 lon=sounding.station_lon,
                                 launchtime=str(YYYYMMDDHHMM))
        trajectory_name_parts = []
        for char in trajectory_name:
            trajectory_name_parts.extend(char)

        nc_prof[0, 0:len(trajectory_name_parts)] = trajectory_name_parts
        nc_launchtime[0] = date2num(sounding.sounding_start_time, date_unit)

        nc_tindex[0, :] = sounding.time
#         nc_vvert[0, :] = vvert_m[:]
        nc_alti[0, :] = sounding.gpm
        nc_pres[0, :] = sounding.pressure
        nc_temp[0, :] = sounding.temperature
        nc_rh[0, :] = relative_humidity
        nc_dewp[0, :] = sounding.dewpoint
        nc_mix[0, :] = wv_mix_ratio
        nc_vhori[0, :] = sounding.windspeed
        nc_vdir[0, :] = sounding.winddirection
        nc_lat[0, :] = sounding.latitude
        nc_long[0, :] = sounding.longitude

        fo.close()
        logging.info('DONE: {input} converted to {output}'.format(
            input=filelist[ifile],
            output=outfile))

    time_out = time.time()
    logging.debug('System time: {}s'.format(time_out - time_in))


if __name__ == "__main__":
    main()