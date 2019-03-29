import bz2
import logging
import os
from urllib.request import urlretrieve, URLError

import numpy as np
from astropy.io import fits, ascii
from scipy.integrate import trapz
from scipy.interpolate import InterpolatedUnivariateSpline
from tqdm import tqdm

import Starfish.constants as C
from Starfish import config
from Starfish.spectrum import create_log_lam_grid
from .base_interfaces import RawGridInterface
from .utils import vacuum_to_air, idl_float

log = logging.getLogger(__name__)


def download_PHOENIX_models(parameters, base=config.grid["raw_path"]):
    """
    Download the PHOENIX grid models from the Goettingen servers. This will skip over any ill-defined files or any
    files that already exist on disk in the given folder.

    :param parameters: The parameters to download. Should be a list of parameters where parameters can either be
        [Teff, logg, Z] or [Teff, logg, Z, Alpha]. All values should be floats or integers and not string.
    :type parameters: iterable of iterables of length 3 or length 4
    :param base: The base directory to save the files in. Default is specified by the ``grid.raw_path`` in the
        ``config.yaml`` file
    :type base: str or path-like

    .. warning:: This will create any directories if they do not exist

    .. warning:: Please use this responsibly to avoid over-saturating the connection to the Gottinghen servers.

    Example usage

    .. code-block:: python

        from itertools import product
        from Starfish.grid_tools import download_PHOENIX_models

        T = [5000, 5100, 5200]
        logg = [4.0, 4.5, 5.0]
        Z = [0]
        Alpha = [0, -0.2]
        params = product(T, logg, Z, Alpha)
        download_PHOENIX_models(params, base='models')

    """
    wave_url = 'http://phoenix.astro.physik.uni-goettingen.de/data/HiResFITS/WAVE_PHOENIX-ACES-AGSS-COND-2011.fits'
    wave_file = os.path.join(base, 'WAVE_PHOENIX-ACES-AGSS-COND-2011.fits')
    flux_file_formatter = 'http://phoenix.astro.physik.uni-goettingen.de/data/HiResFITS/PHOENIX-ACES-AGSS-COND-2011' \
                          '/Z{2:s}{3:s}/lte{0:05.0f}-{1:03.2f}{2:s}{3:s}.PHOENIX-ACES-AGSS-COND-2011-HiRes.fits'
    output_formatter = 'Z{2:s}{3:s}/lte{0:05.0f}-{1:03.2f}{2:s}{3:s}.PHOENIX-ACES-AGSS-COND-2011-HiRes.fits'

    os.makedirs(base, exist_ok=True)
    # Download step
    log.info('Starting Download of PHOENIX ACES models to {}'.format(base))
    if not os.path.exists(wave_file):
        urlretrieve(wave_url, wave_file)
    pbar = tqdm(parameters)
    for p in pbar:
        tmp_p = [p[0], p[1]]
        # Create the Z string. Have to do this because PHOENIX models use - sign for 0.0
        Zstr = '-0.0' if p[2] == 0 else '{:+.1f}'.format(p[2])
        tmp_p.append(Zstr)
        # Create the Alpha string, which is nothing if alpha is 0 or unspecified
        if len(p) == 4:
            Astr = '' if p[3] == 0 else '.Alpha={:+.2f}'.format(p[3])
        else:
            Astr = ''
        tmp_p.append(Astr)
        url = flux_file_formatter.format(*tmp_p)
        pbar.set_description(url.split('/')[-1])
        output_file = os.path.join(base, output_formatter.format(*tmp_p))
        if not os.path.exists(output_file):
            os.makedirs(os.path.dirname(output_file), exist_ok=True)
            try:
                urlretrieve(url, output_file)
            except URLError:
                logging.warning('Parameters {} not found. Double check they are on PHOENIX grid'.format(p))


class PHOENIXGridInterface(RawGridInterface):
    """
    An Interface to the PHOENIX/Husser synthetic library.

    Note that the wavelengths in the spectra are in Angstrom and the flux are in :math:`F_\lambda` as
    :math:`erg/s/cm^2/cm`
    """

    def __init__(self, air=True, wl_range=(3000, 54000), base=config.grid["raw_path"]):
        super().__init__(name="PHOENIX",
                         param_names=["temp", "logg", "Z", "alpha"],
                         points=[
                             np.array([2300, 2400, 2500, 2600, 2700, 2800, 2900, 3000, 3100, 3200,
                                       3300, 3400, 3500, 3600, 3700, 3800, 3900, 4000, 4100, 4200, 4300, 4400,
                                       4500, 4600, 4700, 4800, 4900, 5000, 5100, 5200, 5300, 5400, 5500, 5600,
                                       5700, 5800, 5900, 6000, 6100, 6200, 6300, 6400, 6500, 6600, 6700, 6800,
                                       6900, 7000, 7200, 7400, 7600, 7800, 8000, 8200, 8400, 8600, 8800, 9000,
                                       9200, 9400, 9600, 9800, 10000, 10200, 10400, 10600, 10800, 11000, 11200,
                                       11400, 11600, 11800, 12000]),
                             np.arange(0.0, 6.1, 0.5),
                             np.arange(-2., 1.1, 0.5),
                             np.array([-0.2, 0.0, 0.2, 0.4, 0.6, 0.8])],
                         air=air, wl_range=wl_range, base=base)  # wl_range used to be (2999, 13001)

        self.par_dicts = [None,
                          None,
                          {-2 : "-2.0", -1.5: "-1.5", -1: '-1.0', -0.5: '-0.5',
                           0.0: '-0.0', 0.5: '+0.5', 1: '+1.0'},
                          {-0.4: ".Alpha=-0.40", -0.2: ".Alpha=-0.20",
                           0.0 : "", 0.2: ".Alpha=+0.20", 0.4: ".Alpha=+0.40",
                           0.6 : ".Alpha=+0.60", 0.8: ".Alpha=+0.80"}]

        # if air is true, convert the normally vacuum file to air wls.
        try:
            wl_filename = os.path.join(self.base, "WAVE_PHOENIX-ACES-AGSS-COND-2011.fits")
            w_full = fits.getdata(wl_filename)
        except:
            raise ValueError("Wavelength file improperly specified.")

        if self.air:
            self.wl_full = vacuum_to_air(w_full)
        else:
            self.wl_full = w_full

        self.ind = (self.wl_full >= self.wl_range[0]) & (self.wl_full <= self.wl_range[1])
        self.wl = self.wl_full[self.ind]
        self.rname = os.path.join(self.base, "Z{2:}{3:}/lte{0:0>5.0f}-{1:.2f}{2:}{3:}" \
                                             ".PHOENIX-ACES-AGSS-COND-2011-HiRes.fits")

    def load_flux(self, parameters, header=False, norm=True):
        self.check_params(parameters)  # Check to make sure that the keys are
        # allowed and that the values are in the grid

        # Create a list of the parameters to be fed to the format string
        # optionally replacing arguments using the dictionaries, if the formatting
        # of a certain parameter is tricky
        str_parameters = []
        for param, par_dict in zip(parameters, self.par_dicts):
            if par_dict is None:
                str_parameters.append(param)
            else:
                str_parameters.append(par_dict[param])

        fname = self.rname.format(*str_parameters)

        # Still need to check that file is in the grid, otherwise raise a C.GridError
        # Read all metadata in from the FITS header, and append to spectrum
        try:
            flux_file = fits.open(fname)
            f = flux_file[0].data
            hdr = dict(flux_file[0].header)
            flux_file.close()
        except:
            raise ValueError("{} is not on disk.".format(fname))

        # If we want to normalize the spectra, we must do it now since later we won't have the full EM range
        if norm:
            f *= 1e-8  # convert from erg/cm^2/s/cm to erg/cm^2/s/A
            F_bol = trapz(f, self.wl_full)
            f = f * (C.F_sun / F_bol)  # bolometric luminosity is always 1 L_sun

        # Add temp, logg, Z, alpha, norm to the metadata
        hdr["norm"] = norm
        hdr["air"] = self.air

        if header:
            return (f[self.ind], hdr)
        else:
            return f[self.ind]


class PHOENIXGridInterfaceNoAlpha(PHOENIXGridInterface):
    """
    An Interface to the PHOENIX/Husser synthetic library without any Alpha concentration doping.
    """

    def __init__(self, air=True, wl_range=(3000, 54000),
                 base=config.grid["raw_path"]):
        # Initialize according to the regular PHOENIX values
        super().__init__(air=air, wl_range=wl_range, base=base)

        # Now override parameters to exclude alpha
        self.param_names = ["temp", "logg", "Z"]
        self.points = [
            np.array([2300, 2400, 2500, 2600, 2700, 2800, 2900, 3000, 3100, 3200,
                      3300, 3400, 3500, 3600, 3700, 3800, 3900, 4000, 4100, 4200, 4300, 4400,
                      4500, 4600, 4700, 4800, 4900, 5000, 5100, 5200, 5300, 5400, 5500, 5600,
                      5700, 5800, 5900, 6000, 6100, 6200, 6300, 6400, 6500, 6600, 6700, 6800,
                      6900, 7000, 7200, 7400, 7600, 7800, 8000, 8200, 8400, 8600, 8800, 9000,
                      9200, 9400, 9600, 9800, 10000, 10200, 10400, 10600, 10800, 11000, 11200,
                      11400, 11600, 11800, 12000]),
            np.arange(0.0, 6.1, 0.5),
            np.arange(-2., 1.1, 0.5)]

        self.par_dicts = [None,
                          None,
                          {-2 : "-2.0", -1.5: "-1.5", -1: '-1.0', -0.5: '-0.5',
                           0.0: '-0.0', 0.5: '+0.5', 1: '+1.0'}]

        self.rname = os.path.join(self.base, "Z{2:}/lte{0:0>5.0f}-{1:.2f}{2:}" \
                                             ".PHOENIX-ACES-AGSS-COND-2011-HiRes.fits")


class PHOENIXGridInterfaceNoAlphaNoFE(PHOENIXGridInterface):
    """
    An Interface to the PHOENIX/Husser synthetic library that disregards [Fe/H] as well as alpha.
    """

    def __init__(self, air=True, wl_range=(3000, 54000),
                 base=config.grid['raw_path']):
        # Initialize according to the regular PHOENIX values
        super().__init__(air=air, wl_range=wl_range, base=base)

        # Now override parameters to exclude alpha
        self.param_names = ["temp", "logg"]
        self.points = [
            np.array([2300, 2400, 2500, 2600, 2700, 2800, 2900, 3000, 3100, 3200,
                      3300, 3400, 3500, 3600, 3700, 3800, 3900, 4000, 4100, 4200, 4300, 4400,
                      4500, 4600, 4700, 4800, 4900, 5000, 5100, 5200, 5300, 5400, 5500, 5600,
                      5700, 5800, 5900, 6000, 6100, 6200, 6300, 6400, 6500, 6600, 6700, 6800,
                      6900, 7000, 7200, 7400, 7600, 7800, 8000, 8200, 8400, 8600, 8800, 9000,
                      9200, 9400, 9600, 9800, 10000, 10200, 10400, 10600, 10800, 11000, 11200,
                      11400, 11600, 11800, 12000]),
            np.arange(0.0, 6.1, 0.5)]

        self.par_dicts = [None, None, None]

        self.rname = os.path.join(self.base, "Z-0.0/lte{0:0>5.0f}-{1:.2f}-0.0"
                                             ".PHOENIX-ACES-AGSS-COND-2011-HiRes.fits")


class KuruczGridInterface(RawGridInterface):
    """
    Kurucz grid interface.

    Spectra are stored in ``f_nu`` in a filename like
    ``t03500g00m25ap00k2v070z1i00.fits``, ``ap00`` means zero alpha enhancement,
    and ``k2`` is the microturbulence, while ``z1`` is the macroturbulence.
    These particular values are roughly the ones appropriate for the Sun.
    """

    def __init__(self, air=True, wl_range=(5000, 5400), base=config.grid["raw_path"]):
        super().__init__(name="Kurucz",
                         param_names=["temp", "logg", "Z"],
                         points=[np.arange(3500, 9751, 250),
                                 np.arange(0.0, 5.1, 0.5),
                                 np.array([-2.5, -2.0, -1.5, -1.0, -0.5, 0.0, 0.5])],
                         air=air, wl_range=wl_range, base=base)

        self.par_dicts = [None, None,
                          {-2.5: "m25", -2.0: "m20", -1.5: "m15", -1.0: "m10", -0.5: "m05", 0.0: "p00", 0.5: "p05"}]

        # Convert to f_lam and average to 1, or leave in f_nu?
        self.rname = os.path.join(base, "t{0:0>5.0f}/g{1:0>2.0f}/t{0:0>5.0f}g{1:0>2.0f}{2}ap00k2v000z1i00.fits")
        self.wl_full = np.load(os.path.join(base, "kurucz_raw_wl.npy"))
        self.ind = (self.wl_full >= self.wl_range[0]) & (self.wl_full <= self.wl_range[1])
        self.wl = self.wl_full[self.ind]

    def load_flux(self, parameters, header=False, norm=True):
        self.check_params(parameters)

        str_parameters = []
        for param, par_dict in zip(parameters, self.par_dicts):
            if par_dict is None:
                str_parameters.append(param)
            else:
                str_parameters.append(par_dict[param])

        # Multiply logg by 10
        str_parameters[1] *= 10

        fname = self.rname.format(*str_parameters)

        # Still need to check that file is in the grid, otherwise raise a C.GridError
        # Read all metadata in from the FITS header, and append to spectrum
        try:
            flux_file = fits.open(fname)
            f = flux_file[0].data
            hdr = dict(flux_file[0].header)
            flux_file.close()
        except:
            raise ValueError("{} is not on disk.".format(fname))

        # We cannot normalize the spectra, since we don't have a full wl range, so instead we set the average
        # flux to be 1

        # Also, we should convert from f_nu to f_lam
        if norm:
            f *= C.c_ang / self.wl ** 2  # Convert from f_nu to f_lambda
            f /= np.average(f)  # divide by the mean flux, so avg(f) = 1

        # Add temp, logg, Z, norm to the metadata
        hdr["norm"] = norm
        hdr["air"] = self.air

        if header:
            return (f[self.ind], hdr)
        else:
            return f[self.ind]

    @staticmethod
    def get_wl_kurucz(filename):
        """The Kurucz grid is log-linear spaced."""
        flux_file = fits.open(filename)
        hdr = flux_file[0].header
        num = len(flux_file[0].data)
        p = np.arange(num)
        w1 = hdr['CRVAL1']
        dw = hdr['CDELT1']
        wl = 10 ** (w1 + dw * p)
        return wl


class BTSettlGridInterface(RawGridInterface):
    """
    BTSettl grid interface. Unlike the PHOENIX and Kurucz grids, the
    individual files of the BTSettl grid do not always have the same wavelength
    sampling. Therefore, each call of :meth:`load_flux` will interpolate the
    flux onto a LogLambda spaced grid that ranges between `wl_range` and has a
    velocity spacing of 0.08 km/s or better.

    If you have a choice, it's probably easier to use the Husser PHOENIX grid.
    """

    def __init__(self, air=True, wl_range=(2999, 13000), base="libraries/raw/BTSettl/"):
        super().__init__(name="BTSettl",
                         points={"temp" : np.arange(3000, 7001, 100),
                                 "logg" : np.arange(2.5, 5.6, 0.5),
                                 "Z"    : np.arange(-0.5, 0.6, 0.5),
                                 "alpha": np.array([0.0])},
                         air=air, wl_range=wl_range, base=base)

        # Normalize to 1 solar luminosity?
        self.rname = os.path.join(self.base, "CIFIST2011/M{Z:}/lte{temp:0>3.0f}-{logg:.1f}{Z:}.BT-Settl.spec.7.bz2")
        # self.Z_dict = {-2:"-2.0", -1.5:"-1.5", -1:'-1.0', -0.5:'-0.5', 0.0: '-0.0', 0.5: '+0.5', 1: '+1.0'}
        self.Z_dict = {-0.5: '-0.5a+0.2', 0.0: '-0.0a+0.0', 0.5: '+0.5a0.0'}

        wl_dict = create_log_lam_grid(wl_start=self.wl_range[0], wl_end=self.wl_range[1], min_vc=0.08 / C.c_kms)
        self.wl = wl_dict['wl']

    def load_flux(self, parameters, norm=True):
        """
        Because of the crazy format of the BTSettl, we need to sort the wl to make sure
        everything is unique, and we're not screwing ourselves with the spline.

        :param parameters: stellar parameters
        :type parameters: dict
        :param norm: If True, will normalize the spectrum to solar luminosity. Default is True
        :type norm: bool
        """
        # Check to make sure that the keys are allowed and that the values are in the grid
        self.check_params(parameters)

        str_parameters = parameters.copy()

        # Rewrite Z
        Z = parameters["Z"]
        str_parameters["Z"] = self.Z_dict[Z]

        # Multiply temp by 0.01
        str_parameters["temp"] = 0.01 * parameters['temp']

        fname = self.rname.format(**str_parameters)
        file = bz2.BZ2File(fname, 'r')

        lines = file.readlines()
        strlines = [line.decode('utf-8') for line in lines]
        file.close()

        data = ascii.read(strlines, col_starts=(0, 13), col_ends=(12, 25), Reader=ascii.FixedWidthNoHeader)
        wl = data['col1']
        fl_str = data['col2']

        fl = idl_float(fl_str)  # convert because of "D" exponent, unreadable in Python
        fl = 10 ** (fl - 8.)  # now in ergs/cm^2/s/A

        # "Clean" the wl and flux points. Remove duplicates, sort in increasing wl
        wl, ind = np.unique(wl, return_index=True)
        fl = fl[ind]

        if norm:
            F_bol = trapz(fl, wl)
            fl = fl * (C.F_sun / F_bol)
            # the bolometric luminosity is always 1 L_sun

        # truncate the spectrum to the wl range of interest
        # at this step, make the range a little more so that the next stage of
        # spline interpolation is properly in bounds
        ind = (wl >= (self.wl_range[0] - 50.)) & (wl <= (self.wl_range[1] + 50.))
        wl = wl[ind]
        fl = fl[ind]

        if self.air:
            # Shift the wl that correspond to the raw spectrum
            wl = vacuum_to_air(wl)

        # Now interpolate wl, fl onto self.wl
        interp = InterpolatedUnivariateSpline(wl, fl, k=5)
        fl_interp = interp(self.wl)

        return fl_interp


class CIFISTGridInterface(RawGridInterface):
    """
    CIFIST grid interface, grid available here: https://phoenix.ens-lyon.fr/Grids/BT-Settl/CIFIST2011_2015/FITS/.
    Unlike the PHOENIX and Kurucz grids, the
    individual files of the BTSettl grid do not always have the same wavelength
    sampling. Therefore, each call of :meth:`load_flux` will interpolate the
    flux onto a LogLambda spaced grid that ranges between `wl_range` and has a
    velocity spacing of 0.08 km/s or better.

    If you have a choice, it's probably easier to use the Husser PHOENIX grid.
    """

    def __init__(self, air=True, wl_range=(3000, 13000), base=config.grid["raw_path"]):
        super().__init__(name="CIFIST",
                         points=[np.concatenate((np.arange(1200, 2351, 50), np.arange(2400, 7001, 100)), axis=0),
                                 np.arange(2.5, 5.6, 0.5)],
                         param_names=["temp", "logg"],
                         air=air, wl_range=wl_range, base=base)

        self.par_dicts = [None, None]
        self.rname = os.path.join(self.base, "lte{0:0>5.1f}-{1:.1f}-0.0a+0.0.BT-Settl.spec.fits.gz")

        wl_dict = create_log_lam_grid(dv=0.08, wl_start=self.wl_range[0], wl_end=self.wl_range[1])
        self.wl = wl_dict['wl']

    def load_flux(self, parameters, header=False, norm=True):
        self.check_params(parameters)

        str_parameters = []
        for param, par_dict in zip(parameters, self.par_dicts):
            if par_dict is None:
                str_parameters.append(param)
            else:
                str_parameters.append(par_dict[param])

        # Multiply temp by 0.01
        str_parameters[0] = 0.01 * parameters[0]

        fname = self.rname.format(*str_parameters)

        # Still need to check that file is in the grid, otherwise raise a C.GridError
        # Read all metadata in from the FITS header, and append to spectrum
        try:
            flux_file = fits.open(fname)
            data = flux_file[1].data
            hdr = dict(flux_file[1].header)

            wl = data["Wavelength"] * 1e4  # [Convert to angstroms]
            fl = data["Flux"]

            flux_file.close()
        except OSError:
            raise C.GridError("{} is not on disk.".format(fname))

        # "Clean" the wl and flux points. Remove duplicates, sort in increasing wl
        wl, ind = np.unique(wl, return_index=True)
        fl = fl[ind]

        if norm:
            F_bol = trapz(fl, wl)
            fl = fl * (C.F_sun / F_bol)
            # the bolometric luminosity is always 1 L_sun

        # truncate the spectrum to the wl range of interest
        # at this step, make the range a little more so that the next stage of
        # spline interpolation is properly in bounds
        ind = (wl >= (self.wl_range[0] - 50.)) & (wl <= (self.wl_range[1] + 50.))
        wl = wl[ind]
        fl = fl[ind]

        if self.air:
            # Shift the wl that correspond to the raw spectrum
            wl = vacuum_to_air(wl)

        # Now interpolate wl, fl onto self.wl
        interp = InterpolatedUnivariateSpline(wl, fl, k=5)
        fl_interp = interp(self.wl)

        # Add temp, logg, Z, norm to the metadata

        hdr["norm"] = norm
        hdr["air"] = self.air

        if header:
            return (fl_interp, hdr)
        else:
            return fl_interp


def load_BTSettl(temp, logg, Z, norm=False, trunc=False, air=False):
    rname = "BT-Settl/CIFIST2011/M{Z:}/lte{temp:0>3.0f}-{logg:.1f}{Z:}.BT-Settl.spec.7.bz2".format(temp=0.01 * temp,
                                                                                                   logg=logg, Z=Z)
    file = bz2.BZ2File(rname, 'r')

    lines = file.readlines()
    strlines = [line.decode('utf-8') for line in lines]
    file.close()

    data = ascii.read(strlines, col_starts=[0, 13], col_ends=[12, 25], Reader=ascii.FixedWidthNoHeader)
    wl = data['col1']
    fl_str = data['col2']

    fl = idl_float(fl_str)  # convert because of "D" exponent, unreadable in Python
    fl = 10 ** (fl - 8.)  # now in ergs/cm^2/s/A

    if norm:
        F_bol = trapz(fl, wl)
        fl = fl * (C.F_sun / F_bol)
        # this also means that the bolometric luminosity is always 1 L_sun

    if trunc:
        # truncate to only the wl of interest
        ind = (wl > 3000) & (wl < 13000)
        wl = wl[ind]
        fl = fl[ind]

    if air:
        wl = vacuum_to_air(wl)

    return [wl, fl]