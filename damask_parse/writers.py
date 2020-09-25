"""`damask_parse.writers.py`"""

import copy
from pathlib import Path
from collections import OrderedDict

import numpy as np
from ruamel.yaml import YAML

from damask_parse.utils import (
    zeropad,
    format_1D_masked_array,
    align_orientations,
    get_volume_element_materials,
)

__all__ = [
    'write_geom',
    'write_material',
    'write_numerics_config',
    'write_load_case',
]


def write_geom(volume_element, geom_path):
    """Write the geometry file for a spectral DAMASK simulation.

    Parameters
    ----------
    volume_element : dict
        Dict that represents the specification of a volume element, with keys:
            grid_size : list of length three
            voxel_grain_idx : nested list or 3D ndarray of int
                A mapping that determines the grain index for each voxel.
            voxel_homogenization_idx : nested list or 3D ndarray of int, optional
                A mapping that determines the homogenization scheme (via an integer index)
                for each voxel. Currently, only one homogenization index is supported. If
                not specified, the default of 0 is used.
            size : list of length three, optional
                Volume element size. By default set to unit size: [1, 1, 1].
            origin : list of length three, optional
                Volume element origin. By default: [0, 0, 0].
    geom_path : str or Path
        The path to the file that will be generated.

    Returns
    -------
    geom_path : Path
        The path to the generated file.

    Notes
    -----
    The microstructure and texture parts are not included in the header of the generated
    file.

    """

    voxel_grain_idx = volume_element['voxel_grain_idx']
    if isinstance(voxel_grain_idx, list):
        voxel_grain_idx = np.array(voxel_grain_idx)

    grid = voxel_grain_idx.shape
    grain_idx_2d = np.concatenate(voxel_grain_idx.swapaxes(0, 2))
    ve_size = volume_element.get('size', [1, 1, 1])
    ve_origin = volume_element.get('origin', [0.0, 0.0, 0.0])
    num_micros = np.max(grain_idx_2d) + 1  # `grain_idx_2d` is zero-indexed

    # For now, only a single homogenization is supported:
    homog_idx_uniq = np.unique(volume_element.get('voxel_homogenization_idx', 0))
    if homog_idx_uniq.size > 1:
        msg = (f'Only one homogenization is currently supported, but the volume element '
               f'has these multiple unique homogenization indices '
               f'(zero-indexed): {homog_idx_uniq}.')
        raise NotImplementedError(msg)
    else:
        homog_idx = homog_idx_uniq[0] + 1  # needs to be one-indexed

    header_lns = [
        f'grid a {grid[0]} b {grid[1]} c {grid[2]}',
        f'size x {ve_size[0]} y {ve_size[1]} z {ve_size[2]}',
        f'origin x {ve_origin[0]} y {ve_origin[1]} z {ve_origin[2]}',
        f'microstructures {num_micros}',
        f'homogenization {homog_idx}',
    ]
    num_header_lns = len(header_lns)
    header = f'{num_header_lns} header\n' + '\n'.join(header_lns) + '\n'

    arr_str = ''
    for row in grain_idx_2d:
        for col in row:
            arr_str += '{:<5d}'.format(col + 1)  # needs to be one-indexed
        arr_str += '\n'

    geom_path = Path(geom_path)
    with geom_path.open('w') as handle:
        handle.write(header + arr_str)

    return geom_path


def write_load_case(load_path, load_cases):
    """

    Example load case line is: 
        fdot 1.0e-3 0 0  0 * 0  0 0 * stress * * *  * 0 *   * * 0  time 10  incs 40

    """

    all_load_case = []

    for load_case in load_cases:

        def_grad_aim = load_case.get('def_grad_aim')
        def_grad_rate = load_case.get('def_grad_rate')
        stress = load_case.get('stress')
        rot = load_case.get('rotation')
        total_time = load_case['total_time']
        num_increments = load_case['num_increments']
        freq = load_case.get('dump_frequency', 1)

        if def_grad_aim is not None and def_grad_rate is not None:
            msg = 'Specify only one of `def_grad_rate` and `def_grad_aim`.'
            raise ValueError(msg)

        stress_symbol = 'P'

        # If def_grad_aim/rate is masked array, stress masked array should also be passed,
        # such that the two arrays are component-wise exclusive.

        dg_arr = None
        dg_arr_sym = None
        if def_grad_aim is not None:
            dg_arr = def_grad_aim
            dg_arr_sym = 'F'
        elif def_grad_rate is not None:
            dg_arr = def_grad_rate
            dg_arr_sym = 'Fdot'

        load_case_ln = []

        if stress is None:

            if dg_arr is None:
                msg = 'Specify one of `def_grad_rate` or `def_grad_aim.'
                raise ValueError(msg)

            if isinstance(dg_arr, np.ma.core.MaskedArray):
                msg = ('To use mixed boundary conditions, `stress` must be passed as a '
                       'masked array.')
                raise ValueError(msg)

            dg_arr_fmt = format_1D_masked_array(dg_arr.flatten(), fmt='{:.10f}')
            load_case_ln.append(dg_arr_sym + ' ' + dg_arr_fmt)

        else:
            if isinstance(stress, np.ma.core.MaskedArray):

                if dg_arr is None:
                    msg = 'Specify one of `def_grad_rate` or `def_grad_aim.'
                    raise ValueError(msg)

                msg = ('`def_grad_rate` or `def_grad_aim` must be component-wise exclusive '
                       'with `stress` (both as masked arrays)')
                if not isinstance(dg_arr, np.ma.core.MaskedArray):
                    raise ValueError(msg)
                if np.any(dg_arr.mask == stress.mask):
                    raise ValueError(msg)

                dg_arr_fmt = format_1D_masked_array(
                    dg_arr.flatten(), fill_symbol='*', fmt='{:.10f}')
                stress_arr_fmt = format_1D_masked_array(
                    stress.flatten(), fill_symbol='*', fmt='{:.10f}')
                load_case_ln.extend([
                    dg_arr_sym + ' ' + dg_arr_fmt,
                    stress_symbol + ' ' + stress_arr_fmt,
                ])

            else:
                if dg_arr is not None:
                    msg = ('To use mixed boundary conditions, `stress` must be passed as a '
                           'masked array.')
                    raise ValueError(msg)

                stress_arr_fmt = format_1D_masked_array(stress.flatten(), fmt='{:.10f}')
                load_case_ln.append(stress_symbol + ' ' + stress_arr_fmt)

        load_case_ln.extend([
            f't {total_time}',
            f'incs {num_increments}',
            f'freq {freq}',
        ])

        if rot is not None:

            rot = np.array(rot)
            msg = 'Matrix passed as a rotation is not a rotation matrix.'
            if not np.allclose(rot.T @ rot, np.eye(3)):
                raise ValueError(msg)
            if not np.isclose(np.linalg.det(rot), 1):
                raise ValueError(msg)

            rot_fmt = format_1D_masked_array(rot.flatten(), fmt='{:.10f}')
            load_case_ln.append(f'rot {rot_fmt}')

        load_case_str = ' '.join(load_case_ln)
        all_load_case.append(load_case_str)

    all_load_case_str = '\n'.join(all_load_case)

    load_path = Path(load_path)
    with load_path.open('w') as handle:
        handle.write(all_load_case_str)

    return load_path


def write_numerics_config(dir_path, numerics):
    """Write the optional numerics.config file for a DAMASK simulation.

    Parameters
    ----------
    dir_path : str or Path
        Directory in which to generate the file(s).
    numerics : dict
        Dict of key-value pairs to write into the file.

    Returns
    -------
    numerics_path : Path
        File path to the generated numerics.config file.

    """

    dir_path = Path(dir_path).resolve()
    numerics_path = dir_path.joinpath('numerics.config')

    with numerics_path.open('w') as handle:
        for key, val in numerics.items():
            handle.write(f'{key:<30} {val}')

    return numerics_path


def write_material(homog_schemes, phases, volume_element, dir_path, name='material.yaml'):
    """Write the material.yaml file for a DAMASK simulation.

    Parameters
    ----------
    homog_schemes : dict
        Dict whose keys are homogenization scheme labels and whose values are dicts that
        specify the homogenization parameters for that scheme. This will be passed into
        the "homogenization" dict in the material file.
    phases : dict
        Dict whose keys are phase labels and whose values are the dicts that specify the
        phase parameters for that phase label. This will be passed into the "phase" dict
        in the material file.
    volume_element : dict
        Volume element data to include in the material file. Allowed keys are:
            orientations : dict
                Dict containing the following keys:
                    type : str
                        One of "euler", "quat".
                    quaternions : ndarray of shape (R, 4) of float, optional
                        Array of R row four-vectors of unit quaternions. Specify either
                        `quaternions` or `euler_angles`.
                    euler_angles : ndarray of shape (R, 3) of float, optional            
                        Array of R row three-vectors of Euler angles. Specify either
                        `quaternions` or `euler_angles`. Specified as proper Euler angles
                        in the Bunge convention. (Rotations are about Z, new X,
                        new new Z.)
            constituent_material_idx : ndarray of shape (N,) of int, optional
                Determines the material to which each constituent belongs, where N is the
                number of constituents. If `constituent_*` keys are not specified, then
                `element_material_idx` and `grid_size` must be specified. See Notes.
            constituent_material_fraction: ndarray of shape (N,) of float, optional
                The fraction that each constituent occupies within its respective
                material, where N is the number of constituents. If `constituent_*` keys
                are not specified, then `element_material_idx` and `grid_size` must be
                specified. See Notes.
            constituent_phase_label : ndarray of shape (N,) of str, optional
                Determines the phase label of each constituent, where N is the number of
                constituents.  If `constituent_*` keys are not specified, then
                `element_material_idx` and `grid_size` must be specified. See Notes.
            constituent_orientation_idx : ndarray of shape (N,) of int, optional
                Determines the orientation (as an index into `orientations`) associated
                with each constituent, where N is the number of constituents. If
                `constituent_*` keys are not specified, then `element_material_idx` and
                `grid_size` must be specified. See Notes.
            material_homog : ndarray of shape (M,) of str, optional
                Determines the homogenization scheme (from a list of available
                homogenization schemes defined elsewhere) to which each material belongs,
                where M is the number of materials. If `constituent_*` keys are not
                specified, then `element_material_idx` and `grid_size` must be specified.
                See Notes.
            element_material_idx : ndarray of shape (P,) of int, optional
                Determines the material to which each geometric model element belongs,
                where P is the number of elements. If `constituent_*` keys are not
                specified, then `element_material_idx` and `grid_size` must be specified.
                See Notes.
            grid_size : ndarray of shape (3,) of int, optional
                Geometric model grid dimensions. If `constituent_*` keys are not
                specified, then `element_material_idx` and `grid_size` must be specified.
                See Notes.
            phase_labels : ndarray of str, optional
                List of phase labels to associate with the constituents. Only applicable
                if `constituent_*` keys are not specified. The first list element is the
                phase label that will be associated with all of the geometrical elements
                for which an orientation is also specified. Additional list elements are
                phase labels for geometrical elements for which no orientations are
                specified.
            homog_label : str, optional
                The homogenization scheme label to use for all materials in the volume
                element. Only applicable if `constituent_*` keys are not specified.
    dir_path : str or Path
        Directory in which to generate the material.yaml file.
    name : str, optional
        Name of material file to write. By default, set to "material.yaml".

    Returns
    -------
    mat_path : Path
        Path of the generated material.yaml file.

    Notes
    -----
    The input `volume_element` can be either fully specified with respect to the 
    `constituent_*` keys or, if no `constituent_*` keys are specified, but the
    `element_material_idx` and `grid_size` keys are specified, we assume the model to be
    a full-field model for which each material contains precisely one constituent. In this
    case the additional keys `phase_labels` and `homog_labels` must be specified. The
    number of phase labels specified should be equal to the number or orientations
    specified plus the total number of any additional material indices in
    "element_material_idx" for which there are no orientations.

    """

    microstructures = get_volume_element_materials(
        volume_element,
        homog_schemes=homog_schemes,
        phases=phases,
    )
    mat_dat = {
        'phase': phases,
        'homogenization': homog_schemes,
        'microstructure': microstructures,
    }

    dir_path = Path(dir_path).resolve()
    mat_path = dir_path.joinpath('material.yaml')
    yaml = YAML()
    yaml.dump(mat_dat, mat_path)

    return mat_path
